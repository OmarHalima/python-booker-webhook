"""
Turbo booker — fast booking via scheduler POST + minimal Playwright submit.

Default submit path uses in-browser form submit (BotDetect requires it).
Pure HTTP POST (submitMode=http) often fails CAPTCHA_FAIL — kept for experiments.

Run:
  uvicorn app_turbo:app --host 127.0.0.1 --port 8001

Endpoint:
  POST /webhook/booker-turbo

Payload extras vs /webhook/booker:
  - submitMode: "browser" (default) | "http"
  - captchaText (optional): skip OCR if you already solved captcha
  - captureFormHtml (optional): skip scheduler POST if you already have capture HTML
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv

import app as booker

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

APPOINTMENT_ORIGIN = booker.APPOINTMENT_ORIGIN
SCHEDULER_URL = booker.SCHEDULER_URL
ARTIFACTS_DIR = booker.ARTIFACTS_DIR

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

app = FastAPI(title="BMEIA Turbo Booker", version="0.2.0")


def _turbo_submit_mode(payload: Dict[str, Any]) -> str:
    """browser = form submit in Playwright (works); http = requests POST (often CAPTCHA_FAIL)."""
    mode = str(payload.get("submitMode") or os.getenv("TURBO_SUBMIT_MODE", "browser")).strip().lower()
    if mode in ("http", "fetch", "requests"):
        return "http"
    return "browser"


def _wait_botdetect_ready(page) -> None:
    try:
        page.wait_for_selector("#Captcha_CaptchaImage", timeout=15000)
        page.wait_for_function(
            """() => {
              const img = document.querySelector('#Captcha_CaptchaImage');
              if (!img) return false;
              return getComputedStyle(img).visibility !== 'hidden' && img.offsetWidth > 0;
            }""",
            timeout=20000,
        )
    except Exception:
        pass
    page.wait_for_timeout(300)


def _capture_captcha_bytes(page, context, fresh: booker.FreshFields, session_cookie: str) -> bytes:
    try:
        return booker.capture_captcha_bytes_from_dom(page)
    except Exception:
        return booker.fetch_captcha_bytes(context, fresh.captcha_image_url, session_cookie)


def _browser_submit_capture(page, run_id: str, attempt: int) -> str:
    booker._inject_agent_command_next_for_captcha(page)
    path = booker._submit_captcha_passport_form(page, run_id, attempt)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=booker.nav_dom_content_loaded_ms())
    except Exception:
        pass
    booker._wait_load_settle(page)
    return path


def _solve_turbo_captcha(
    image_bytes: bytes,
    *,
    run_id: str,
    attempt: int,
    vcid: str,
    max_attempts: int,
) -> Tuple[str, str]:
    """OCR for early attempts; prefer Gemini on the last two retries."""
    gemini_from = max(1, max_attempts - 1)
    if attempt >= gemini_from:
        try:
            text = booker.solve_captcha_with_gemini(
                image_bytes, run_id=run_id, attempt=attempt, vcid=vcid
            )
            return text, "gemini_late"
        except Exception as exc:
            booker.dlog(
                run_id,
                "H_turbo",
                "app_turbo:_solve_turbo_captcha",
                "gemini_late_failed_fallback_ocr",
                {"attempt": attempt, "error": str(exc)[:200]},
            )
    text = booker.solve_captcha(image_bytes, run_id=run_id, attempt=attempt, vcid=vcid)
    return text, "ocr_or_gemini_fallback"


def run_turbo_browser_booking_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Scheduler POST + OCR + in-browser Next click (~4–6s). Fixes HTTP CAPTCHA_FAIL."""
    from playwright.sync_api import sync_playwright

    run_id = booker.resolve_run_id(payload)
    payload["_runId"] = run_id
    payload["runId"] = run_id

    raw_retries = payload.get("maxCaptchaRetries", payload.get("maxCaptchaAttempts", 3))
    try:
        max_attempts = max(1, min(int(raw_retries), 25))
    except (TypeError, ValueError):
        max_attempts = 3

    run_dir = ARTIFACTS_DIR / f"{run_id}-turbo"
    run_dir.mkdir(parents=True, exist_ok=True)
    timings: Dict[str, float] = {}
    t_total = time.perf_counter()
    provided_captcha = str(payload.get("captchaText") or "").strip()
    if provided_captcha:
        max_attempts = 1

    booker.dlog(
        run_id,
        "H_turbo",
        "app_turbo:run_turbo_browser_booking_flow",
        "turbo_browser_entry",
        {"maxAttempts": max_attempts},
    )

    result: Dict[str, Any] = {}
    captcha_text = ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=booker.playwright_headless())
        context = browser.new_context(
            base_url=APPOINTMENT_ORIGIN,
            viewport={"width": 1280, "height": 900},
        )
        booker.set_context_cookies(context, str(payload.get("sessionCookie") or ""))
        page = context.new_page()
        page.goto(f"{APPOINTMENT_ORIGIN}/HomeWeb/Scheduler", wait_until="domcontentloaded")

        t_nav = time.perf_counter()
        booker.navigate_to_capture_form_via_scheduler_post(
            page, context, payload, run_id, run_dir=run_dir
        )
        timings["schedulerNavMs"] = (time.perf_counter() - t_nav) * 1000
        _wait_botdetect_ready(page)
        _save_turbo_artifacts(run_dir, "01_capture_form.html", page.content())

        booker.fill_form_fields(page, payload)

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                t_nav = time.perf_counter()
                booker.navigate_to_capture_form_via_scheduler_post(
                    page, context, payload, run_id, run_dir=run_dir
                )
                timings[f"schedulerNavMs_attempt{attempt}"] = (time.perf_counter() - t_nav) * 1000
                _wait_botdetect_ready(page)
                booker.fill_form_fields(page, payload)

            fresh = booker.parse_fresh_fields(page.content())
            fresh = _fresh_fields_from_payload_override(fresh, payload)
            _verify_start_time(fresh, payload, run_id)

            captcha_text = provided_captcha if (provided_captcha and attempt == 1) else ""
            if not captcha_text:
                t0 = time.perf_counter()
                captcha_bytes = _capture_captcha_bytes(
                    page, context, fresh, str(payload.get("sessionCookie") or "")
                )
                timings[f"captchaFetchMs_attempt{attempt}"] = (time.perf_counter() - t0) * 1000
                _save_turbo_artifacts(
                    run_dir, f"attempt_{attempt:02d}_captcha.png", captcha_bytes, binary=True
                )
                t0 = time.perf_counter()
                captcha_text, captcha_source = _solve_turbo_captcha(
                    captcha_bytes,
                    run_id=run_id,
                    attempt=attempt,
                    vcid=fresh.bdc_vcid,
                    max_attempts=max_attempts,
                )
                timings[f"captchaSolveMs_attempt{attempt}"] = (time.perf_counter() - t0) * 1000
                booker.dlog(
                    run_id,
                    "H_turbo",
                    "app_turbo:run_turbo_browser_booking_flow",
                    "captcha_solved",
                    {"attempt": attempt, "source": captcha_source, "len": len(captcha_text)},
                )

            booker._fill_captcha_text_like_user(page, captcha_text)
            t0 = time.perf_counter()
            submit_path = _browser_submit_capture(page, run_id, attempt)
            submit_ms = (time.perf_counter() - t0) * 1000
            timings[f"submitMs_attempt{attempt}"] = submit_ms
            html = page.content()
            _save_turbo_artifacts(run_dir, f"attempt_{attempt:02d}_response.html", html)

            booker.dlog(
                run_id,
                "H_turbo",
                "app_turbo:run_turbo_browser_booking_flow",
                "submit_result",
                {
                    "attempt": attempt,
                    "submitPath": submit_path,
                    "submitMs": round(submit_ms, 1),
                    "url": page.url,
                },
            )

            if booker.is_success_html(html) or "Confirmation of reservation" in html:
                timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                result = {
                    "ok": True,
                    "resultType": "SUCCESS",
                    "attempt": attempt,
                    "captchaText": captcha_text,
                    "finalUrl": page.url,
                    "mode": "turbo",
                    "submitMode": "browser",
                    "runId": run_id,
                    "timingsMs": timings,
                    "artifactsDir": str(run_dir),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
                break

            submit_err = booker.classify_post_submit_html(html)
            if submit_err:
                result_type, err_msg = submit_err
                if result_type == "CAPTCHA_FAIL" and attempt < max_attempts:
                    provided_captcha = ""
                    continue
                timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                result = {
                    "ok": False,
                    "resultType": result_type,
                    "attempt": attempt,
                    "captchaText": captcha_text,
                    "message": err_msg,
                    "finalUrl": page.url,
                    "mode": "turbo",
                    "submitMode": "browser",
                    "runId": run_id,
                    "timingsMs": timings,
                    "artifactsDir": str(run_dir),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
                break

            if "BDC_VCID_Captcha" in html:
                provided_captcha = ""
                continue

            timings["totalMs"] = (time.perf_counter() - t_total) * 1000
            result = {
                "ok": False,
                "resultType": "OTHER_FAIL",
                "attempt": attempt,
                "captchaText": captcha_text,
                "finalUrl": page.url,
                "mode": "turbo",
                "submitMode": "browser",
                "runId": run_id,
                "timingsMs": timings,
                "snippet": html[:800],
                "artifactsDir": str(run_dir),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            break

        browser.close()

    if not result:
        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
        result = {
            "ok": False,
            "resultType": "CAPTCHA_FAIL",
            "attempt": max_attempts,
            "message": "Captcha retries exhausted.",
            "mode": "turbo",
            "submitMode": "browser",
            "runId": run_id,
            "timingsMs": timings,
            "artifactsDir": str(run_dir),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    booker.dlog(
        run_id,
        "H_turbo",
        "app_turbo:run_turbo_browser_booking_flow",
        "turbo_browser_done",
        {"ok": result.get("ok"), "resultType": result.get("resultType"), "totalMs": timings.get("totalMs")},
    )
    return result


def _default_headers(payload: Dict[str, Any], referer: str) -> Dict[str, str]:
    return {
        "User-Agent": str(payload.get("userAgent") or DEFAULT_UA),
        "Origin": APPOINTMENT_ORIGIN,
        "Referer": referer,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _session_from_payload(payload: Dict[str, Any]) -> requests.Session:
    sess = requests.Session()
    for name, value in booker.parse_cookie_header(str(payload.get("sessionCookie") or "")).items():
        sess.cookies.set(name, value, domain="appointment.bmeia.gv.at", path="/")
    return sess


def _merge_set_cookie(session: requests.Session, response: requests.Response) -> None:
    raw = response.headers.get("Set-Cookie")
    if not raw:
        return
    merged = booker.merge_session_cookie_from_set_cookie(
        "; ".join(f"{k}={v}" for k, v in session.cookies.get_dict().items()),
        raw,
    )
    for part in merged.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            session.cookies.set(k.strip(), v.strip(), domain="appointment.bmeia.gv.at", path="/")


def turbo_post_select_slot(session: requests.Session, payload: Dict[str, Any]) -> Tuple[str, float]:
    """POST /HomeWeb/Scheduler — same as n8n slot select."""
    referer = str(payload.get("schedulerReferer") or f"{APPOINTMENT_ORIGIN}/?fromSpecificInfo=True")
    form = {
        "Language": payload["Language"],
        "Office": payload["Office"],
        "CalendarId": str(payload["CalendarId"]),
        "PersonCount": str(payload.get("PersonCount") or "1"),
        "Monday": payload["currentMonday"],
        "Start": payload["currentSlot"],
        "Command": "Next",
    }
    t0 = time.perf_counter()
    resp = session.post(
        SCHEDULER_URL,
        data=form,
        headers={**_default_headers(payload, referer), "Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    ms = (time.perf_counter() - t0) * 1000
    _merge_set_cookie(session, resp)
    if not resp.ok:
        raise ValueError(f"Scheduler POST failed: {resp.status_code} {resp.text[:300]}")
    return resp.text, ms


def turbo_fetch_captcha(session: requests.Session, url: str, payload: Dict[str, Any]) -> bytes:
    resp = session.get(
        url,
        headers=_default_headers(payload, SCHEDULER_URL),
        timeout=booker.captcha_ocr_timeout(),
    )
    if not resp.ok:
        raise ValueError(f"Captcha download failed: {resp.status_code}")
    return resp.content


def _normalize_sex(student: Dict[str, Any]) -> str:
    sex_value = str(student.get("Sex", "")).strip()
    sex_map = {
        "male": "1",
        "female": "2",
        "unknown": "3",
        "not applicable": "4",
        "notapplicable": "4",
        "m": "1",
        "f": "2",
    }
    normalized = sex_map.get(sex_value.lower(), sex_value)
    return normalized if normalized in {"1", "2", "3", "4"} else sex_value


def build_turbo_submit_form(
    fresh: booker.FreshFields,
    payload: Dict[str, Any],
    captcha_text: str,
) -> Dict[str, str]:
    """Build application/x-www-form-urlencoded body for capture form POST."""
    student = payload.get("student") or {}
    captcha_norm = booker.normalize_captcha_text(captcha_text)

    dsgvo_raw: Any = payload.get("dsgvoAccepted", payload.get("DSGVOAccepted", True))
    if isinstance(dsgvo_raw, str):
        want_dsgvo = dsgvo_raw.strip().lower() in ("1", "true", "yes", "on")
    else:
        want_dsgvo = bool(dsgvo_raw)

    form: Dict[str, str] = {
        "Office": str(payload.get("Office") or ""),
        "Language": str(payload.get("Language") or "en"),
        "CalendarId": str(payload.get("CalendarId") or ""),
        "PersonCount": str(payload.get("PersonCount") or "1"),
        "Token": fresh.token,
        "StartTime": fresh.start_time,
        "BDC_VCID_Captcha": fresh.bdc_vcid,
        "BDC_Hs_Captcha": fresh.bdc_hs,
        "BDC_SP_Captcha": fresh.bdc_sp,
        "BDC_BackWorkaround_Captcha": fresh.bdc_bw or "0",
        "CaptchaText": captcha_norm,
        "Command": "Next",
        "Lastname": str(student.get("Lastname") or ""),
        "Firstname": str(student.get("Firstname") or ""),
        "DateOfBirth": str(student.get("DateOfBirth") or ""),
        "TraveldocumentNumber": str(student.get("TraveldocumentNumber") or ""),
        "Street": str(student.get("Street") or ""),
        "Postcode": str(student.get("Postcode") or ""),
        "City": str(student.get("City") or ""),
        "Telephone": str(student.get("Telephone") or ""),
        "Email": str(student.get("Email") or ""),
        "LastnameAtBirth": str(student.get("LastnameAtBirth") or ""),
        "PlaceOfBirth": str(student.get("PlaceOfBirth") or ""),
        "TraveldocumentDateOfIssue": str(student.get("TraveldocumentDateOfIssue") or ""),
        "TraveldocumentValidUntil": str(student.get("TraveldocumentValidUntil") or ""),
        "Sex": _normalize_sex(student),
        "Country": str(student.get("Country") or ""),
        "NationalityAtBirth": str(student.get("NationalityAtBirth") or ""),
        "CountryOfBirth": str(student.get("CountryOfBirth") or ""),
        "NationalityForApplication": str(student.get("NationalityForApplication") or ""),
        "TraveldocumentIssuingAuthority": str(student.get("TraveldocumentIssuingAuthority") or ""),
    }
    if want_dsgvo:
        form["DSGVOAccepted"] = "true"
    return form


def turbo_submit_capture_form(
    session: requests.Session,
    fresh: booker.FreshFields,
    payload: Dict[str, Any],
    captcha_text: str,
) -> Tuple[str, float, str]:
    """POST booking form — turbo launch."""
    body = build_turbo_submit_form(fresh, payload, captcha_text)
    referer = fresh.booking_endpoint if fresh.booking_endpoint.startswith("http") else SCHEDULER_URL
    t0 = time.perf_counter()
    resp = session.post(
        fresh.booking_endpoint,
        data=body,
        headers={
            **_default_headers(payload, referer),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=60,
    )
    ms = (time.perf_counter() - t0) * 1000
    _merge_set_cookie(session, resp)
    return resp.text, ms, resp.url


def _verify_start_time(fresh: booker.FreshFields, payload: Dict[str, Any], run_id: str) -> None:
    want = str(payload.get("currentSlot") or "").strip()
    got = str(fresh.start_time or "").strip()
    if not want or not got:
        return
    if booker._normalize_slot_for_compare(want) != booker._normalize_slot_for_compare(got):
        booker.dlog(
            run_id,
            "H_turbo",
            "app_turbo:_verify_start_time",
            "start_time_mismatch",
            {"payloadSlot": want, "htmlStartTime": got},
        )
        if not booker.booking_allow_slot_fallback():
            raise ValueError(
                f"StartTime mismatch: page has {got!r}, payload currentSlot is {want!r}. "
                "Use fresh sessionCookie + useSchedulerPost or captureFormHtml from same capture."
            )


def _payload_nested(payload: Dict[str, Any]) -> Dict[str, Any]:
    nested = payload.get("_payload")
    return nested if isinstance(nested, dict) else {}


def _pick_payload_field(payload: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        if payload.get(key) not in (None, ""):
            return str(payload[key])
        nested = _payload_nested(payload)
        if nested.get(key) not in (None, ""):
            return str(nested[key])
    return ""


def _fresh_fields_from_payload_override(
    fresh: booker.FreshFields, payload: Dict[str, Any]
) -> booker.FreshFields:
    """Overlay browser-copied Token/BDC_* from payload (F12 / n8n _payload) onto parsed HTML."""
    token = _pick_payload_field(payload, "Token", "token")
    start_time = _pick_payload_field(payload, "StartTime", "startTime")
    vcid = _pick_payload_field(payload, "BDC_VCID_Captcha")
    hs = _pick_payload_field(payload, "BDC_Hs_Captcha")
    sp = _pick_payload_field(payload, "BDC_SP_Captcha")
    bw = _pick_payload_field(payload, "BDC_BackWorkaround_Captcha") or fresh.bdc_bw
    endpoint = _pick_payload_field(payload, "bookingEndpoint") or fresh.booking_endpoint
    if not (token and start_time and vcid):
        return fresh
    captcha_url = _pick_payload_field(payload, "captchaImageUrl")
    if not captcha_url and vcid:
        captcha_url = f"{APPOINTMENT_ORIGIN}/BotDetectCaptcha.ashx?get=image&c=Captcha&t={vcid}"
    return booker.FreshFields(
        booking_endpoint=endpoint,
        token=token,
        start_time=start_time,
        bdc_vcid=vcid,
        bdc_hs=hs or fresh.bdc_hs,
        bdc_sp=sp or fresh.bdc_sp,
        bdc_bw=bw,
        captcha_image_url=captcha_url or fresh.captcha_image_url,
        html=fresh.html,
    )


def _resolve_capture_html(payload: Dict[str, Any], session: requests.Session) -> Tuple[str, float, str]:
    """Returns (html, scheduler_ms, source)."""
    inline = str(payload.get("captureFormHtml") or payload.get("captureHtml") or "").strip()
    skip = payload.get("skipSchedulerPost") is True or payload.get("useSchedulerPost") is False
    if inline:
        return inline, 0.0, "captureFormHtml"
    if skip:
        raise ValueError(
            "skipSchedulerPost/useSchedulerPost=false requires captureFormHtml pasted from browser "
            "(Elements → copy outerHTML on <html> or save page source on Capture data step)."
        )
    html, ms = turbo_post_select_slot(session, payload)
    return html, ms, "scheduler_post"


def _save_turbo_artifacts(run_dir: Path, name: str, content: str | bytes, *, binary: bool = False) -> str:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / name
    if binary:
        path.write_bytes(content)  # type: ignore[arg-type]
    else:
        path.write_text(content, encoding="utf-8")
    return str(path)


def run_turbo_http_booking_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
    run_id = booker.resolve_run_id(payload)
    payload["_runId"] = run_id
    payload["runId"] = run_id

    raw_retries = payload.get("maxCaptchaRetries", payload.get("maxCaptchaAttempts", 3))
    try:
        max_attempts = max(1, min(int(raw_retries), 25))
    except (TypeError, ValueError):
        max_attempts = 3

    run_dir = ARTIFACTS_DIR / f"{run_id}-turbo"
    run_dir.mkdir(parents=True, exist_ok=True)

    timings: Dict[str, float] = {}
    t_total = time.perf_counter()

    booker.dlog(run_id, "H_turbo", "app_turbo:run_turbo_booking_flow", "turbo_entry", {"maxAttempts": max_attempts})

    session = _session_from_payload(payload)
    capture_html, sched_ms, html_source = _resolve_capture_html(payload, session)
    timings["schedulerPostMs"] = sched_ms

    _save_turbo_artifacts(run_dir, "01_capture_form.html", capture_html)

    pre_err = booker.classify_post_submit_html(capture_html)
    if pre_err:
        result_type, msg = pre_err
        return {
            "ok": False,
            "resultType": result_type,
            "message": msg,
            "mode": "turbo",
            "submitMode": "http",
            "runId": run_id,
            "timingsMs": timings,
            "htmlSource": html_source,
        }

    if "BDC_VCID_Captcha" not in capture_html or 'id="Token"' not in capture_html:
        raise ValueError("Capture form not found after scheduler POST (missing Token/captcha fields).")

    fresh = booker.parse_fresh_fields(capture_html)
    fresh = _fresh_fields_from_payload_override(fresh, payload)
    _verify_start_time(fresh, payload, run_id)

    booker.dlog(
        run_id,
        "H_turbo",
        "app_turbo:run_turbo_booking_flow",
        "fresh_fields",
        {
            "startTime": fresh.start_time,
            "vcidTail": fresh.bdc_vcid[-8:] if fresh.bdc_vcid else "",
            "hsTail": fresh.bdc_hs[-8:] if fresh.bdc_hs else "",
            "bookingEndpoint": fresh.booking_endpoint,
            "htmlSource": html_source,
            "hasPayloadOverride": bool(_pick_payload_field(payload, "Token", "token")),
        },
    )

    provided_captcha = str(payload.get("captchaText") or "").strip()
    if provided_captcha:
        max_attempts = 1
    result: Dict[str, Any] = {}

    for attempt in range(1, max_attempts + 1):
        if attempt > 1 and html_source == "scheduler_post":
            capture_html, _, _ = _resolve_capture_html(payload, session)
            fresh = booker.parse_fresh_fields(capture_html)
            fresh = _fresh_fields_from_payload_override(fresh, payload)
            _verify_start_time(fresh, payload, run_id)

        captcha_text = provided_captcha if (provided_captcha and attempt == 1) else ""
        captcha_source = "payload" if captcha_text else ""

        if not captcha_text:
            t0 = time.perf_counter()
            captcha_bytes = turbo_fetch_captcha(session, fresh.captcha_image_url, payload)
            timings[f"captchaFetchMs_attempt{attempt}"] = (time.perf_counter() - t0) * 1000
            _save_turbo_artifacts(
                run_dir,
                f"attempt_{attempt:02d}_captcha.png",
                captcha_bytes,
                binary=True,
            )
            t0 = time.perf_counter()
            captcha_text = booker.solve_captcha(
                captcha_bytes, run_id=run_id, attempt=attempt, vcid=fresh.bdc_vcid
            )
            timings[f"captchaSolveMs_attempt{attempt}"] = (time.perf_counter() - t0) * 1000
            captcha_source = "ocr_or_gemini"

        submit_html, submit_ms, final_url = turbo_submit_capture_form(
            session, fresh, payload, captcha_text
        )
        timings[f"submitPostMs_attempt{attempt}"] = submit_ms
        _save_turbo_artifacts(run_dir, f"attempt_{attempt:02d}_response.html", submit_html)

        booker.dlog(
            run_id,
            "H_turbo",
            "app_turbo:run_turbo_booking_flow",
            "submit_result",
            {
                "attempt": attempt,
                "submitMs": round(submit_ms, 1),
                "captchaSource": captcha_source,
                "captchaLen": len(captcha_text),
                "finalUrl": final_url,
            },
        )

        if booker.is_success_html(submit_html) or "Confirmation of reservation" in submit_html:
            timings["totalMs"] = (time.perf_counter() - t_total) * 1000
            result = {
                "ok": True,
                "resultType": "SUCCESS",
                "attempt": attempt,
                "captchaText": captcha_text,
                "finalUrl": final_url,
                "mode": "turbo",
                "submitMode": "http",
                "runId": run_id,
                "timingsMs": timings,
                "artifactsDir": str(run_dir),
                "htmlSource": html_source,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            break

        submit_err = booker.classify_post_submit_html(submit_html)
        if submit_err:
            result_type, err_msg = submit_err
            timings["totalMs"] = (time.perf_counter() - t_total) * 1000
            result = {
                "ok": False,
                "resultType": result_type,
                "attempt": attempt,
                "captchaText": captcha_text,
                "message": err_msg,
                "finalUrl": final_url,
                "mode": "turbo",
                "submitMode": "http",
                "timingsMs": timings,
                "artifactsDir": str(run_dir),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            break

        if "Captcha: The text from the picture does not match" in submit_html or "BDC_VCID_Captcha" in submit_html:
            provided_captcha = ""
            continue

        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
        result = {
            "ok": False,
            "resultType": "OTHER_FAIL",
            "attempt": attempt,
            "captchaText": captcha_text,
            "finalUrl": final_url,
            "mode": "turbo",
            "submitMode": "http",
            "runId": run_id,
            "timingsMs": timings,
            "snippet": submit_html[:800],
            "artifactsDir": str(run_dir),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        break

    if not result:
        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
        result = {
            "ok": False,
            "resultType": "CAPTCHA_FAIL",
            "attempt": max_attempts,
            "message": "Captcha retries exhausted.",
            "mode": "turbo",
            "submitMode": "http",
            "runId": run_id,
            "timingsMs": timings,
            "artifactsDir": str(run_dir),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    booker.dlog(
        run_id,
        "H_turbo",
        "app_turbo:run_turbo_booking_flow",
        "turbo_done",
        {
            "ok": result.get("ok"),
            "resultType": result.get("resultType"),
            "totalMs": timings.get("totalMs"),
        },
    )
    return result


def run_turbo_booking_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
    mode = _turbo_submit_mode(payload)
    booker.dlog(
        str(payload.get("runId") or "no-runid"),
        "H_turbo",
        "app_turbo:run_turbo_booking_flow",
        "dispatch",
        {"submitMode": mode},
    )
    if mode == "http":
        return run_turbo_http_booking_flow(payload)
    return run_turbo_browser_booking_flow(payload)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "mode": "turbo"}


@app.post("/webhook/booker-turbo")
def webhook_booker_turbo(payload: Dict[str, Any]) -> Dict[str, Any]:
    booker.dlog(
        str(payload.get("runId") or "no-runid"),
        "H_turbo",
        "app_turbo:webhook_booker_turbo",
        "webhook_entry",
        {
            "hasSessionCookie": bool(payload.get("sessionCookie")),
            "hasCaptchaText": bool(payload.get("captchaText")),
            "hasCaptureHtml": bool(payload.get("captureFormHtml")),
        },
    )
    required = ["sessionCookie", "Language", "Office", "CalendarId", "currentMonday", "currentSlot", "student"]
    missing = [k for k in required if k not in payload or payload.get(k) in (None, "")]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing)}")
    try:
        return run_turbo_booking_flow(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app_turbo:app", host="0.0.0.0", port=8001, reload=False)
