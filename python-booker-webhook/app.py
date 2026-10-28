"""
Austria Booker Webhook — turbo primary entry point (v2).

Default path: scheduler POST + minimal Playwright submit (~4–11s).
Legacy full Playwright wizard remains on POST /webhook/booker-legacy.

Run:
  uvicorn app:app --host 0.0.0.0 --port 8000

Endpoints:
  POST /webhook/booker          — turbo (submitMode browser default)
  POST /webhook/booker-turbo    — alias of /webhook/booker
  POST /webhook/booker-legacy   — full Playwright wizard (~26s)

Payload extras vs legacy-only fields:
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

import booker_legacy as booker

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

APPOINTMENT_ORIGIN = booker.APPOINTMENT_ORIGIN
SCHEDULER_URL = booker.SCHEDULER_URL
ARTIFACTS_DIR = booker.ARTIFACTS_DIR

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

app = FastAPI(title="Austria Booker Webhook", version="2.0.0")


def _turbo_submit_mode(payload: Dict[str, Any]) -> str:
    """browser = form submit in Playwright (works); http = requests POST (often CAPTCHA_FAIL)."""
    mode = str(payload.get("submitMode") or os.getenv("TURBO_SUBMIT_MODE", "browser")).strip().lower()
    if mode in ("http", "fetch", "requests"):
        return "http"
    return "browser"


def _botdetect_settle_ms() -> int:
    try:
        return max(0, int(os.getenv("BOTDETECT_SETTLE_MS", "150")))
    except (TypeError, ValueError):
        return 150


def _wait_botdetect_ready(page) -> bool:
    return booker.wait_botdetect_ready(page, settle_ms=_botdetect_settle_ms())


def _save_solved_captcha_for_training(
    run_dir: Path,
    *,
    captcha_bytes: bytes,
    vcid: str,
    captcha_text: str,
) -> None:
    """Persist successful captcha image + label for future custom OCR training."""
    if not captcha_bytes or not captcha_text:
        return
    safe_vcid = re.sub(r"[^A-Za-z0-9_-]", "_", vcid or "unknown")[:64]
    safe_text = re.sub(r"[^A-Za-z0-9]", "", captcha_text.upper())[:16]
    if not safe_text:
        return
    out_dir = run_dir / "solved_captchas"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{safe_vcid}_{safe_text}.png"
    _save_turbo_artifacts(out_dir, name, captcha_bytes, binary=True)


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


def _turbo_captcha_solver_mode() -> str:
    """Turbo solver order. Default fastcaptcha_with_gemini_fallback; override with TURBO_CAPTCHA_SOLVER or CAPTCHA_SOLVER."""
    return (
        os.getenv("TURBO_CAPTCHA_SOLVER") or os.getenv("CAPTCHA_SOLVER") or "fastcaptcha_with_gemini_fallback"
    ).strip().lower()


def _primary_solver(solver_mode: str) -> Tuple[Any, str]:
    """Map solver mode -> (callable, label). FastCaptcha is the sandbox primary."""
    if solver_mode.startswith("fastcaptcha"):
        return booker.solve_captcha_with_fastcaptcha, "fastcaptcha"
    if solver_mode.startswith("ocr"):
        return booker.solve_captcha_with_ocr, "ocr"
    return booker.solve_captcha_with_fastcaptcha, "fastcaptcha"


def _solve_turbo_captcha(
    image_bytes: bytes,
    *,
    run_id: str,
    attempt: int,
    captcha_round: int,
    vcid: str,
    max_attempts: int,
) -> Tuple[str, str]:
    """Solve captcha per TURBO_CAPTCHA_SOLVER / CAPTCHA_SOLVER (default: fastcaptcha_with_gemini_fallback)."""
    mode = _turbo_captcha_solver_mode()
    gemini_first_after = int(os.getenv("CAPTCHA_GEMINI_FIRST_AFTER", "2"))
    use_gemini_first = captcha_round >= gemini_first_after or attempt >= gemini_first_after

    if mode == "ocr":
        text = booker.solve_captcha_with_ocr(
            image_bytes, run_id=run_id, attempt=attempt, vcid=vcid
        )
        return text, "ocr"

    if mode == "gemini":
        text = booker.solve_captcha_with_gemini(
            image_bytes, run_id=run_id, attempt=attempt, vcid=vcid
        )
        return text, "gemini"

    if mode == "fastcaptcha":
        text = booker.solve_captcha_with_fastcaptcha(
            image_bytes, run_id=run_id, attempt=attempt, vcid=vcid
        )
        return text, "fastcaptcha"

    if mode in ("fastcaptcha_with_gemini_fallback", "ocr_with_gemini_fallback"):
        primary, primary_source = _primary_solver(mode)
        if use_gemini_first:
            try:
                text = booker.solve_captcha_with_gemini(
                    image_bytes, run_id=run_id, attempt=attempt, vcid=vcid
                )
                return text, "gemini"
            except Exception as exc:
                booker.dlog(
                    run_id,
                    "H_turbo",
                    "app_turbo:_solve_turbo_captcha",
                    "gemini_round_failed_fallback_primary",
                    {"attempt": attempt, "captchaRound": captcha_round, "mode": mode, "error": str(exc)[:200]},
                )
        try:
            text = primary(
                image_bytes, run_id=run_id, attempt=attempt, vcid=vcid
            )
            return text, primary_source
        except Exception as exc:
            booker.dlog(
                run_id,
                "H_turbo",
                "app_turbo:_solve_turbo_captcha",
                f"{primary_source}_failed_fallback_gemini",
                {"attempt": attempt, "captchaRound": captcha_round, "mode": mode, "error": str(exc)[:200]},
            )
        text = booker.solve_captcha_with_gemini(
            image_bytes, run_id=run_id, attempt=attempt, vcid=vcid
        )
        return text, "gemini_fallback"

    # gemini_first (legacy fallback when mode explicitly set)
    try:
        text = booker.solve_captcha_with_gemini(
            image_bytes, run_id=run_id, attempt=attempt, vcid=vcid
        )
        return text, "gemini"
    except Exception as exc:
        booker.dlog(
            run_id,
            "H_turbo",
            "app_turbo:_solve_turbo_captcha",
            "gemini_failed_fallback_solver",
            {"attempt": attempt, "error": str(exc)[:200]},
        )
    text = booker.solve_captcha(image_bytes, run_id=run_id, attempt=attempt, vcid=vcid)
    return text, "fastcaptcha_fallback"


_SLOT_POST_RESULT_TYPES = {
    "SLOT_TAKEN",
    "ALREADY_BOOKED",
    "CAPTCHA_FAIL",
    "OTHER_FAIL",
    "BOOKING_SERVER_BUSY",
}

_SCHEDULER_POST_ERR_RE = re.compile(r"^Scheduler POST: (\w+) — (.*)$")


def _navigate_scheduler_post_safe(
    page, context, payload, run_id, run_dir
) -> Optional[Tuple[str, str]]:
    """Navigate to capture form via scheduler POST.

    Returns None on success, or a (resultType, message) tuple if the scheduler
    POST itself reports a result before we ever reach captcha. Scheduler HTTP
    failures must be returned as structured results, never as webhook HTTP 500.
    """
    try:
        booker.navigate_to_capture_form_via_scheduler_post(
            page, context, payload, run_id, run_dir=run_dir
        )
        return None
    except ValueError as exc:
        m = _SCHEDULER_POST_ERR_RE.match(str(exc))
        if m:
            return m.group(1), m.group(2)
        msg = str(exc)
        result_type = (
            "BOOKING_SERVER_BUSY"
            if re.search(r"\b(?:5\d{2}|429)\b|timed out|request failed", msg, re.IGNORECASE)
            else "OTHER_FAIL"
        )
        return result_type, msg


def _scheduler_post_failure_result(
    exc: Exception,
    *,
    run_id: str,
    timings: Dict[str, float],
    run_dir: Path,
    submit_mode: str,
) -> Dict[str, Any]:
    """Convert a scheduler-POST ValueError into a structured booking response.

    Triggered when BMEIA returns a known validation page (e.g. SLOT_TAKEN) or
    a session-stale response during nominate_to_capture_form_via_scheduler_post,
    or when the HTTP turbo path's scheduler POST fails. Without this, the
    turbo flow propagates the ValueError to webhook_booker's 500 handler and
    n8n emits the generic "HTTP request failed" telegram instead of routing
    by resultType.

    The error message is shaped like:
      "Scheduler POST: SLOT_TAKEN — The appointment was unfortunately allocated ..."
      "Scheduler POST returned StartTime ... but currentSlot is ..."
      "POST /HomeWeb/Scheduler did not return the capture form ..."
      "Scheduler POST failed: 503 Service Unavailable ..."
    """
    msg = str(exc)
    result_type = "OTHER_FAIL"
    upper = msg.upper()
    for candidate in _SLOT_POST_RESULT_TYPES:
        if candidate in upper:
            result_type = candidate
            break
    timings["totalMs"] = timings.get("totalMs", 0.0)
    return {
        "ok": False,
        "resultType": result_type,
        "attempt": 1,
        "captchaText": "",
        "message": msg[:500],
        "finalUrl": "",
        "mode": "turbo",
        "submitMode": submit_mode,
        "runId": run_id,
        "timingsMs": timings,
        "artifactsDir": str(run_dir),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def run_turbo_browser_booking_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Scheduler POST + OCR + in-browser Next click (~4–6s). Fixes HTTP CAPTCHA_FAIL."""
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
    last_captcha_bytes: bytes = b""
    last_captcha_vcid = ""
    parallel_used_any = False
    captcha_round = 0
    with booker.open_booking_page(
        run_id=run_id,
        run_dir=run_dir,
        capture_artifacts=False,
        ignore_https_errors=False,
        base_url=APPOINTMENT_ORIGIN,
        viewport={"width": 1280, "height": 900},
    ) as (context, page, _video_dir, runtime_meta):
        booker.dlog(
            run_id,
            "H_turbo",
            "app_turbo:run_turbo_browser_booking_flow",
            "playwright_launch",
            {
                "headless": runtime_meta.get("headless"),
                "slowMo": runtime_meta.get("slowMo"),
                "reusedBrowser": runtime_meta.get("reused"),
                "runtimeRuns": runtime_meta.get("runtimeRuns"),
                "runtimeAgeSec": runtime_meta.get("runtimeAgeSec"),
            },
        )
        booker.set_context_cookies(context, str(payload.get("sessionCookie") or ""))

        t_nav = time.perf_counter()
        booker._goto_scheduler_page(page, run_id)
        early = _navigate_scheduler_post_safe(page, context, payload, run_id, run_dir)
        timings["schedulerNavMs"] = (time.perf_counter() - t_nav) * 1000
        if early:
            result_type, msg = early
            booker.dlog(
                run_id, "H_turbo",
                "app_turbo:run_turbo_browser_booking_flow", "scheduler_post_short_circuit",
                {"resultType": result_type, "error": msg[:300]},
            )
            timings["totalMs"] = (time.perf_counter() - t_total) * 1000
            return {
                "ok": False,
                "resultType": result_type,
                "attempt": 1,
                "captchaText": "",
                "message": msg[:500],
                "finalUrl": "",
                "mode": "turbo",
                "submitMode": "browser",
                "runId": run_id,
                "timingsMs": timings,
                "artifactsDir": str(run_dir),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        _save_turbo_artifacts(run_dir, "01_capture_form.html", page.content())

        try:
            booker.fill_form_fields(page, payload)
        except Exception as exc:
            booker.dlog(
                run_id, "H_turbo",
                "app_turbo:run_turbo_browser_booking_flow", "fill_form_fields_failed",
                {"error": str(exc)[:300]},
            )
            timings["totalMs"] = (time.perf_counter() - t_total) * 1000
            return _scheduler_post_failure_result(
                ValueError(f"fill_form_fields failed: {exc}"),
                run_id=run_id, timings=timings, run_dir=run_dir, submit_mode="browser",
            )

        for attempt in range(1, max_attempts + 1):
            # Cleared per attempt: only an image fetched in THIS attempt may
            # arm the submit floor.
            image_at = 0.0
            if attempt > 1:
                html = booker._safe_page_content(page)
                if "BDC_VCID_Captcha" in html and 'id="Token"' in html:
                    booker.dlog(
                        run_id, "H_turbo",
                        "app_turbo:run_turbo_browser_booking_flow", "retry_use_existing_page",
                        {"attempt": attempt, "reason": "page_has_fresh_captcha"},
                    )
                    try:
                        booker.fill_form_fields(page, payload)
                    except Exception as exc:
                        booker.dlog(
                            run_id, "H_turbo",
                            "app_turbo:run_turbo_browser_booking_flow", "fill_form_fields_failed_retry",
                            {"attempt": attempt, "error": str(exc)[:300]},
                        )
                        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                        return _scheduler_post_failure_result(
                            ValueError(f"fill_form_fields failed: {exc}"),
                            run_id=run_id, timings=timings, run_dir=run_dir, submit_mode="browser",
                        )
                else:
                    t_nav = time.perf_counter()
                    early = _navigate_scheduler_post_safe(page, context, payload, run_id, run_dir)
                    timings[f"schedulerNavMs_attempt{attempt}"] = (time.perf_counter() - t_nav) * 1000
                    if early:
                        result_type, msg = early
                        booker.dlog(
                            run_id, "H_turbo",
                            "app_turbo:run_turbo_browser_booking_flow", "scheduler_post_short_circuit_retry",
                            {"attempt": attempt, "resultType": result_type, "error": msg[:300]},
                        )
                        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                        return {
                            "ok": False,
                            "resultType": result_type,
                            "attempt": attempt,
                            "captchaText": "",
                            "message": msg[:500],
                            "finalUrl": "",
                            "mode": "turbo",
                            "submitMode": "browser",
                            "runId": run_id,
                            "timingsMs": timings,
                            "artifactsDir": str(run_dir),
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        }
                    try:
                        booker.fill_form_fields(page, payload)
                    except Exception as exc:
                        booker.dlog(
                            run_id, "H_turbo",
                            "app_turbo:run_turbo_browser_booking_flow", "fill_form_fields_failed_retry",
                            {"attempt": attempt, "error": str(exc)[:300]},
                        )
                        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                        return _scheduler_post_failure_result(
                            ValueError(f"fill_form_fields failed: {exc}"),
                            run_id=run_id, timings=timings, run_dir=run_dir, submit_mode="browser",
                        )

            try:
                fresh = booker.parse_fresh_fields(page.content())
                fresh = _fresh_fields_from_payload_override(fresh, payload)
                _verify_start_time(fresh, payload, run_id)
            except ValueError as exc:
                booker.dlog(
                    run_id,
                    "H_turbo",
                    "app_turbo:run_turbo_browser_booking_flow",
                    "fresh_fields_or_start_time_failed",
                    {"attempt": attempt, "error": str(exc)[:300]},
                )
                timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                return _scheduler_post_failure_result(
                    exc,
                    run_id=run_id,
                    timings=timings,
                    run_dir=run_dir,
                    submit_mode="browser",
                )

            captcha_text = provided_captcha if (provided_captcha and attempt == 1 and captcha_round == 0) else ""
            if not captcha_text:
                captcha_round += 1
                t0 = time.perf_counter()
                try:
                    (
                        last_captcha_bytes,
                        _site_captcha_png,
                        captcha_fetch_source,
                        parallel_timings,
                    ) = booker.fetch_captcha_with_parallel_prefetch(
                        page=page,
                        context=context,
                        fresh=fresh,
                        session_cookie=str(payload.get("sessionCookie") or ""),
                        settle_ms=_botdetect_settle_ms(),
                        run_id=run_id,
                        attempt=attempt,
                    )
                except booker.DomCaptchaCaptureError as exc:
                    booker.dlog(
                        run_id,
                        "H_captcha_source",
                        "app_turbo:run_turbo_browser_booking_flow",
                        booker.DomCaptchaCaptureError.reason,
                        {"attempt": attempt, "error": str(exc)[:200]},
                    )
                    if attempt < max_attempts:
                        continue
                    timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                    result = {
                        "ok": False,
                        "resultType": "CAPTCHA_FAIL",
                        "attempt": attempt,
                        "message": str(exc),
                        "reason": booker.DomCaptchaCaptureError.reason,
                        "mode": "turbo",
                        "submitMode": "browser",
                        "runId": run_id,
                        "timingsMs": timings,
                        "artifactsDir": str(run_dir),
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                    break
                timings[f"captchaFetchMs_attempt{attempt}"] = (time.perf_counter() - t0) * 1000
                timings[f"captchaParallelWaitMs_attempt{attempt}"] = parallel_timings.get(
                    "captchaParallelWaitMs", 0.0
                )
                if "captchaParallelFetchMs" in parallel_timings:
                    timings[f"captchaParallelFetchMs_attempt{attempt}"] = parallel_timings["captchaParallelFetchMs"]
                parallel_used_any = parallel_used_any or (captcha_fetch_source == "parallel_url_fetch")
                _save_turbo_artifacts(
                    run_dir, f"attempt_{attempt:02d}_captcha.png", last_captcha_bytes, binary=True
                )

                # Style-gate: reload captcha until an acceptable difficulty is served.
                _max_reloads = max(0, int(os.getenv("CAPTCHA_MAX_STYLE_RELOADS", "0")))
                _min_style = (os.getenv("CAPTCHA_MIN_STYLE", "easy") or "easy").strip().lower()
                for _style_n in range(_max_reloads + 1):
                    _difficulty = booker._assess_captcha_difficulty(last_captcha_bytes)
                    booker.dlog(
                        run_id, "H_captcha_style",
                        "app_turbo:run_turbo_browser_booking_flow", "style_assessment",
                        {"attempt": attempt, "styleReload": _style_n, "difficulty": _difficulty,
                         "vcidTail": (fresh.bdc_vcid or "")[-8:]},
                    )
                    _acceptable = (
                        _min_style == "any"
                        or _difficulty == "easy"
                        or (_min_style == "medium" and _difficulty in ("easy", "medium"))
                    )
                    if _acceptable or _style_n >= _max_reloads:
                        break
                    _old_vcid = fresh.bdc_vcid or ""
                    if not booker._reload_botdetect_captcha(page, run_id, _old_vcid):
                        booker.dlog(run_id, "H_captcha_style",
                                    "app_turbo:run_turbo_browser_booking_flow", "reload_failed",
                                    {"attempt": attempt, "styleReload": _style_n})
                        break
                    fresh = booker.parse_fresh_fields(page.content())
                    last_captcha_bytes = booker.capture_captcha_bytes_from_dom(page)
                    _save_turbo_artifacts(
                        run_dir,
                        f"attempt_{attempt:02d}_captcha_reload{_style_n + 1}.png",
                        last_captcha_bytes, binary=True,
                    )
                last_captcha_vcid = fresh.bdc_vcid or ""
                last_captcha_bytes = booker.capture_captcha_bytes_from_dom(page)
                # The image the submit will carry was just captured; the server's
                # "old enough" timer started when it served it, so it is already
                # running by the time we get here.
                image_at = time.monotonic()
                _save_turbo_artifacts(
                    run_dir, f"attempt_{attempt:02d}_captcha_ocr.png", last_captcha_bytes, binary=True
                )

                t0 = time.perf_counter()
                try:
                    captcha_text, captcha_source = _solve_turbo_captcha(
                        last_captcha_bytes,
                        run_id=run_id,
                        attempt=attempt,
                        captcha_round=captcha_round,
                        vcid=fresh.bdc_vcid,
                        max_attempts=max_attempts,
                    )
                except ValueError as exc:
                    timings[f"captchaSolveMs_attempt{attempt}"] = (time.perf_counter() - t0) * 1000
                    booker.dlog(
                        run_id, "H_turbo",
                        "app_turbo:run_turbo_browser_booking_flow", "captcha_solve_failed",
                        {"attempt": attempt, "error": str(exc)[:300]},
                    )
                    if attempt < max_attempts:
                        provided_captcha = ""
                        continue
                    timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                    result = {
                        "ok": False,
                        "resultType": "CAPTCHA_FAIL",
                        "attempt": attempt,
                        "message": str(exc),
                        "mode": "turbo",
                        "submitMode": "browser",
                        "runId": run_id,
                        "timingsMs": timings,
                        "artifactsDir": str(run_dir),
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                    break
                timings[f"captchaSolveMs_attempt{attempt}"] = (time.perf_counter() - t0) * 1000
                booker.dlog(
                    run_id,
                    "H_turbo",
                    "app_turbo:run_turbo_browser_booking_flow",
                    "captcha_solved",
                    {
                        "attempt": attempt,
                        "source": captcha_source,
                        "fetchSource": captcha_fetch_source,
                        "captchaRound": captcha_round,
                        "parallelUsed": captcha_fetch_source == "parallel_url_fetch",
                        "len": len(captcha_text),
                    },
                )

            hs_ok = booker.fill_captcha_with_hs_guard(
                page,
                captcha_text,
                run_id=run_id,
                settle_ms=_botdetect_settle_ms(),
            )
            if not hs_ok:
                booker.dlog(
                    run_id,
                    "H_captcha_hs",
                    "app_turbo:run_turbo_browser_booking_flow",
                    "hs_never_updated_after_fill",
                    {"attempt": attempt, "captchaRound": captcha_round},
                )
                provided_captcha = ""
                if attempt < max_attempts:
                    continue
                timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                result = {
                    "ok": False,
                    "resultType": "CAPTCHA_FAIL",
                    "attempt": attempt,
                    "captchaText": captcha_text,
                    "message": "BDC_Hs_Captcha did not update after captcha fill.",
                    "mode": "turbo",
                    "submitMode": "browser",
                    "runId": run_id,
                    "timingsMs": timings,
                    "artifactsDir": str(run_dir),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
                break

            # Log DOM state immediately after fill to verify #CaptchaText value and
            # that BDC_Hs_Captcha was updated by BotDetect JS before we submit.
            try:
                _dom_ct = page.locator("#CaptchaText").first.input_value() if page.locator("#CaptchaText").count() else ""
                _dom_hs = page.locator("#BDC_Hs_Captcha").first.input_value() if page.locator("#BDC_Hs_Captcha").count() else ""
                _dom_vcid = page.locator("#BDC_VCID_Captcha").first.input_value() if page.locator("#BDC_VCID_Captcha").count() else ""
                booker.dlog(
                    run_id,
                    "H_turbo",
                    "app_turbo:run_turbo_browser_booking_flow",
                    "pre_submit_dom_state",
                    {
                        "attempt": attempt,
                        "captchaText": _dom_ct,
                        "captchaLen": len(_dom_ct),
                        "captchaEmpty": not _dom_ct,
                        "hsTail": _dom_hs[-8:] if _dom_hs else "",
                        "vcidTail": _dom_vcid[-8:] if _dom_vcid else "",
                        "captchaTextMatchesOcr": _dom_ct.upper() == captcha_text.upper() if captcha_text else False,
                    },
                )
            except Exception:
                pass

            # Hold the submit until the captcha image is old enough to be
            # accepted (measured ~3s floor from image serve, not from capture).
            # Prefer the response-listener clock (when the site served the
            # image), then resource timing, then our own capture mark.
            if image_at:
                dom_image_age, age_source = booker.resolve_captcha_image_age(
                    page, fresh.bdc_vcid, image_at
                )
                timings[f"captchaImageAgeMs_attempt{attempt}"] = (
                    round(dom_image_age * 1000, 1) if dom_image_age is not None else -1.0
                )
                if age_source == "capture_mark":
                    booker.dlog(
                        run_id, "H_submit_captcha",
                        "app_turbo:run_turbo_browser_booking_flow",
                        "captcha_image_probe_miss",
                        {"attempt": attempt, **booker.captcha_image_probe_diag(page)},
                    )
                timings[f"captchaImageFloorWaitMs_attempt{attempt}"] = (
                    booker.wait_out_captcha_image_floor(
                        image_at,
                        image_age_sec=dom_image_age,
                        age_source=age_source,
                        run_id=run_id,
                        attempt=attempt,
                    ) * 1000
                )

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
                _save_solved_captcha_for_training(
                    run_dir,
                    captcha_bytes=last_captcha_bytes,
                    vcid=last_captcha_vcid,
                    captcha_text=captcha_text,
                )
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
        {
            "ok": result.get("ok"),
            "resultType": result.get("resultType"),
            "totalMs": timings.get("totalMs"),
            "captchaParallelUsed": parallel_used_any,
        },
    )
    result["captchaParallelUsed"] = parallel_used_any
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
    timeout_ms = booker.scheduler_post_timeout_ms()
    try:
        resp = session.post(
            SCHEDULER_URL,
            data=form,
            headers={**_default_headers(payload, referer), "Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout_ms / 1000,
        )
    except requests.Timeout as exc:
        raise ValueError(f"Scheduler POST timed out after {timeout_ms}ms.") from exc
    except requests.RequestException as exc:
        raise ValueError(f"Scheduler POST request failed: {exc}") from exc
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
    try:
        capture_html, sched_ms, html_source = _resolve_capture_html(payload, session)
    except ValueError as exc:
        booker.dlog(
            run_id,
            "H_turbo",
            "app_turbo:run_turbo_http_booking_flow",
            "scheduler_post_failed_short_circuit",
            {"error": str(exc)[:300]},
        )
        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
        return _scheduler_post_failure_result(
            exc,
            run_id=run_id,
            timings=timings,
            run_dir=run_dir,
            submit_mode="http",
        )
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
        # Scheduler POST returned 200 but the body is no longer the capture
        # form (session stale, slot gone, validation page returned). Convert
        # to a structured OTHER_FAIL response instead of crashing to 500 so
        # n8n routes by resultType instead of emitting the bare
        # "HTTP request failed" telegram.
        msg = "Capture form not found after scheduler POST (missing Token/captcha fields)."
        booker.dlog(
            run_id,
            "H_turbo",
            "app_turbo:run_turbo_http_booking_flow",
            "capture_form_missing_short_circuit",
            {"htmlSource": html_source, "preview": capture_html[:200]},
        )
        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
        return _scheduler_post_failure_result(
            ValueError(msg),
            run_id=run_id,
            timings=timings,
            run_dir=run_dir,
            submit_mode="http",
        )

    try:
        fresh = booker.parse_fresh_fields(capture_html)
        fresh = _fresh_fields_from_payload_override(fresh, payload)
        _verify_start_time(fresh, payload, run_id)
    except ValueError as exc:
        booker.dlog(
            run_id,
            "H_turbo",
            "app_turbo:run_turbo_http_booking_flow",
            "fresh_fields_or_start_time_failed",
            {"error": str(exc)[:300]},
        )
        timings["totalMs"] = (time.perf_counter() - t_total) * 1000
        return _scheduler_post_failure_result(
            exc,
            run_id=run_id,
            timings=timings,
            run_dir=run_dir,
            submit_mode="http",
        )

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
        # Marks when the captcha IMAGE the submit uses arrived. 0.0 = the text
        # came from the payload, so the image clock cannot be observed here.
        image_at = 0.0
        if attempt > 1 and html_source == "scheduler_post":
            try:
                capture_html, _, _ = _resolve_capture_html(payload, session)
                fresh = booker.parse_fresh_fields(capture_html)
                fresh = _fresh_fields_from_payload_override(fresh, payload)
                _verify_start_time(fresh, payload, run_id)
            except ValueError as exc:
                booker.dlog(
                    run_id,
                    "H_turbo",
                    "app_turbo:run_turbo_http_booking_flow",
                    "scheduler_post_failed_short_circuit_retry",
                    {"attempt": attempt, "error": str(exc)[:300]},
                )
                timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                return _scheduler_post_failure_result(
                    exc,
                    run_id=run_id,
                    timings=timings,
                    run_dir=run_dir,
                    submit_mode="http",
                )

        captcha_text = provided_captcha if (provided_captcha and attempt == 1) else ""
        captcha_source = "payload" if captcha_text else ""

        if not captcha_text:
            try:
                t0 = time.perf_counter()
                captcha_bytes = turbo_fetch_captcha(session, fresh.captcha_image_url, payload)
                # Server-side clock starts when it serves this image, so mark it
                # before the solve eats into the mandatory window.
                image_at = time.monotonic()
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
            except Exception as exc:
                timings["totalMs"] = (time.perf_counter() - t_total) * 1000
                booker.dlog(
                    run_id, "H_turbo",
                    "app_turbo:run_turbo_http_booking_flow", "captcha_fetch_or_solve_failed",
                    {"attempt": attempt, "error": str(exc)[:300]},
                )
                return {
                    "ok": False,
                    "resultType": "CAPTCHA_FAIL",
                    "attempt": attempt,
                    "message": str(exc)[:500],
                    "mode": "turbo",
                    "submitMode": "http",
                    "runId": run_id,
                    "timingsMs": timings,
                    "artifactsDir": str(run_dir),
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }

        # Hold the submit until the captcha image is old enough to be accepted
        # (measured ~3s floor from image serve, not from the capture POST).
        if image_at:
            timings[f"captchaImageFloorWaitMs_attempt{attempt}"] = (
                booker.wait_out_captcha_image_floor(image_at, run_id=run_id, attempt=attempt) * 1000
            )

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
    return {"status": "ok", "mode": "turbo", "version": "2.0.0"}


_REQUIRED_BOOKER_FIELDS = [
    "sessionCookie",
    "Language",
    "Office",
    "CalendarId",
    "currentMonday",
    "currentSlot",
    "student",
]


def _validate_booker_payload(payload: Dict[str, Any]) -> None:
    missing = [k for k in _REQUIRED_BOOKER_FIELDS if k not in payload or payload.get(k) in (None, "")]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing)}")


def _webhook_run_id(payload: Dict[str, Any]) -> str:
    return str(payload.get("runId") or payload.get("_runId") or "no-runid")


def _with_booking_slot(payload: Dict[str, Any], flow):
    run_id = _webhook_run_id(payload)
    with booker._booking_execution_slot(run_id, str(payload.get("sessionCookie") or "")):
        return flow(payload)


@app.post("/webhook/booker")
@app.post("/webhook/booker-turbo")
def webhook_booker(payload: Dict[str, Any]) -> Dict[str, Any]:
    booker.dlog(
        _webhook_run_id(payload),
        "H_turbo",
        "app:webhook_booker",
        "webhook_entry",
        {
            "hasSessionCookie": bool(payload.get("sessionCookie")),
            "hasCaptchaText": bool(payload.get("captchaText")),
            "hasCaptureHtml": bool(payload.get("captureFormHtml")),
            "submitMode": _turbo_submit_mode(payload),
        },
    )
    _validate_booker_payload(payload)
    try:
        return _with_booking_slot(payload, run_turbo_booking_flow)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/webhook/booker-legacy")
def webhook_booker_legacy(payload: Dict[str, Any]) -> Dict[str, Any]:
    booker.dlog(
        _webhook_run_id(payload),
        "H_entry",
        "app:webhook_booker_legacy",
        "webhook_entry",
        {
            "hasSessionCookie": bool(payload.get("sessionCookie")),
            "hasStudent": bool(payload.get("student")),
            "hasCalendarId": bool(payload.get("CalendarId")),
        },
    )
    _validate_booker_payload(payload)
    try:
        return _with_booking_slot(payload, booker.run_booking_flow)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
