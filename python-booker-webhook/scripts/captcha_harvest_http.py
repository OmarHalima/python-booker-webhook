"""Fast HTTP-based BMEIA captcha image harvesting (no Playwright per image)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def captcha_image_url(origin: str, vcid: str) -> str:
    return f"{origin}/BotDetectCaptcha.ashx?get=image&c=Captcha&t={vcid}"


def scheduler_headers(session_cookie: str, origin: str, scheduler_url: str) -> Dict[str, str]:
    return {
        "Cookie": session_cookie,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": origin,
        "Referer": f"{origin}/?fromSpecificInfo=True",
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def captcha_get_headers(session_cookie: str, scheduler_url: str) -> Dict[str, str]:
    return {
        "Cookie": session_cookie,
        "Referer": scheduler_url,
        "User-Agent": USER_AGENT,
        "Accept": "image/png,image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }


def post_select_slot_http(
    *,
    session_cookie: str,
    payload: Dict[str, Any],
    origin: str,
    scheduler_url: str,
    timeout: int = 30,
) -> Tuple[str, str]:
    """POST scheduler slot selection; returns (html, session_cookie)."""
    import booker_legacy as booker

    headers = scheduler_headers(session_cookie, origin, scheduler_url)
    form = {
        "Language": payload["Language"],
        "Office": payload["Office"],
        "CalendarId": str(payload["CalendarId"]),
        "PersonCount": str(payload.get("PersonCount") or "1"),
        "Monday": payload["currentMonday"],
        "Start": payload["currentSlot"],
        "Command": "Next",
    }
    resp = requests.post(scheduler_url, headers=headers, data=form, timeout=timeout)
    if resp.status_code == 429:
        raise ValueError("Scheduler POST rate limited (429)")
    if not resp.ok:
        raise ValueError(f"Scheduler POST failed: {resp.status_code} {resp.reason}")
    set_cookie = resp.headers.get("Set-Cookie") or resp.headers.get("set-cookie") or ""
    updated = booker.merge_session_cookie_from_set_cookie(session_cookie, set_cookie)
    return resp.text, updated


def fetch_captcha_http(
    *,
    session_cookie: str,
    vcid: str,
    origin: str,
    scheduler_url: str,
    timeout: int = 15,
) -> bytes:
    url = captcha_image_url(origin, vcid)
    resp = requests.get(
        url,
        headers=captcha_get_headers(session_cookie, scheduler_url),
        timeout=timeout,
    )
    if resp.status_code == 429:
        raise ValueError("Captcha GET rate limited (429)")
    if not resp.ok:
        raise ValueError(f"Captcha GET failed: {resp.status_code} {resp.reason}")
    data = resp.content
    if len(data) < 100:
        raise ValueError(f"Captcha response too small ({len(data)} bytes)")
    return data


def discover_session_playwright(
    *,
    office: str,
    calendar_id: str,
    language: str,
    person_count: str,
    run_id: str,
) -> Dict[str, Any]:
    """One Playwright pass: cookies + slot fields for HTTP harvesting."""
    import booker_legacy as booker
    from playwright.sync_api import sync_playwright

    payload: Dict[str, Any] = {
        "Office": office,
        "CalendarId": calendar_id,
        "Language": language,
        "PersonCount": person_count,
        "currentMonday": "",
        "currentSlot": "",
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=booker.playwright_headless())
        context = browser.new_context(
            base_url=booker.APPOINTMENT_ORIGIN,
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        booker._open_landing_page(page, run_id)
        for sel, val in {
            "#Office": office,
            "select[name='Office']": office,
            "#Language": language,
            "select[name='Language']": language,
        }.items():
            booker._set_scheduler_value(page, sel, val)
        booker._submit_next_command(page, run_id, stage="fast_harvest_landing")
        booker._apply_reservation_calendar_select(page, payload, run_id, lambda _: None)
        booker._repair_calendar_id_dom_if_needed(page, payload, run_id)
        booker._submit_next_command(page, run_id, stage="fast_harvest_reservation")
        page.wait_for_selector("select[name='PersonCount']", timeout=booker.person_count_wait_ms())
        booker._set_scheduler_value(page, "select[name='PersonCount']", person_count)
        booker._submit_next_command(page, run_id, stage="fast_harvest_person")
        for week in range(4):
            if page.locator("input[name='Start']").count() > 0:
                payload["currentMonday"] = page.locator("#Monday").input_value()
                payload["currentSlot"] = page.locator("input[name='Start']").first.get_attribute("value") or ""
                break
            booker._submit_next_command(page, run_id, stage=f"fast_harvest_week_{week}")
        else:
            browser.close()
            raise RuntimeError("No Start radios found for session discovery")

        parts: List[str] = []
        for c in context.cookies():
            if "bmeia.gv.at" not in (c.get("domain") or ""):
                continue
            parts.append(f"{c['name']}={c['value']}")
        browser.close()

    payload["sessionCookie"] = "; ".join(parts)
    if not payload["sessionCookie"]:
        raise RuntimeError("No session cookies captured")
    payload["Language"] = language
    return payload


def open_capture_form_http(payload: Dict[str, Any]) -> Tuple[Any, str]:
    """POST slot via HTTP; returns (FreshFields, session_cookie)."""
    return refresh_vcid_http(
        session_cookie=payload["sessionCookie"],
        payload=payload,
    )


def refresh_vcid_http(
    *,
    session_cookie: str,
    payload: Dict[str, Any],
    origin: Optional[str] = None,
    scheduler_url: Optional[str] = None,
    timeout: int = 30,
) -> Tuple[Any, str]:
    """Re-POST scheduler slot selection to obtain a new BotDetect VCID (t= param)."""
    import booker_legacy as booker

    origin = origin or booker.APPOINTMENT_ORIGIN
    scheduler_url = scheduler_url or booker.SCHEDULER_URL
    html, cookie = post_select_slot_http(
        session_cookie=session_cookie,
        payload=payload,
        origin=origin,
        scheduler_url=scheduler_url,
        timeout=timeout,
    )
    fresh = booker.parse_fresh_fields(html)
    if not fresh.bdc_vcid:
        raise ValueError("Scheduler response missing BDC_VCID_Captcha")
    return fresh, cookie


def save_session_cache(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_session_cache(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("sessionCookie"):
        return None
    return data


def harvest_captcha_batch_http(
    *,
    count: int,
    payload: Dict[str, Any],
    delay_ms: int = 350,
    vcid_refresh_every: int = 4,
    session_refresh_every: int = 400,
    on_image: Callable[[bytes, str], bool],
    on_vcid_refresh: Optional[Callable[[str], None]] = None,
    on_session_refresh: Optional[Callable[[], None]] = None,
    discover_session: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Fetch captcha images via HTTP GET with rotating VCID.

    Each GET uses BotDetectCaptcha.ashx?get=image&c=Captcha&t={vcid}.
    After every ``vcid_refresh_every`` images, re-POST the scheduler slot to
    obtain a fresh ``t=`` value (BMEIA rate-limits ~5 GETs per VCID).
    """
    import booker_legacy as booker

    origin = booker.APPOINTMENT_ORIGIN
    scheduler_url = booker.SCHEDULER_URL

    saved = 0
    errors = 0
    vcid_refreshes = 0
    session_refreshes = 0
    fetches_on_vcid = 0
    total_fetches = 0
    t0 = time.perf_counter()

    fresh, cookie = open_capture_form_http(payload)
    vcid = fresh.bdc_vcid

    while saved < count:
        try:
            if fetches_on_vcid >= max(1, vcid_refresh_every):
                fresh, cookie = refresh_vcid_http(
                    session_cookie=cookie,
                    payload=payload,
                    origin=origin,
                    scheduler_url=scheduler_url,
                )
                vcid = fresh.bdc_vcid
                fetches_on_vcid = 0
                vcid_refreshes += 1
                payload["sessionCookie"] = cookie
                if on_vcid_refresh:
                    on_vcid_refresh(vcid)

            if total_fetches >= max(50, session_refresh_every) and discover_session:
                payload = discover_session()
                payload["sessionCookie"] = payload.get("sessionCookie") or cookie
                fresh, cookie = open_capture_form_http(payload)
                vcid = fresh.bdc_vcid
                fetches_on_vcid = 0
                total_fetches = 0
                session_refreshes += 1
                if on_session_refresh:
                    on_session_refresh()

            img_bytes = fetch_captcha_http(
                session_cookie=cookie,
                vcid=vcid,
                origin=origin,
                scheduler_url=scheduler_url,
            )
            fetches_on_vcid += 1
            total_fetches += 1
            if on_image(img_bytes, vcid):
                saved += 1

            if delay_ms > 0 and saved < count:
                time.sleep(delay_ms / 1000.0)

        except ValueError as exc:
            if "429" in str(exc):
                time.sleep(2.0)
                try:
                    fresh, cookie = refresh_vcid_http(
                        session_cookie=cookie,
                        payload=payload,
                        origin=origin,
                        scheduler_url=scheduler_url,
                    )
                    vcid = fresh.bdc_vcid
                    fetches_on_vcid = 0
                    vcid_refreshes += 1
                    payload["sessionCookie"] = cookie
                    if on_vcid_refresh:
                        on_vcid_refresh(vcid)
                except Exception:
                    errors += 1
                continue
            errors += 1
            if errors > 30 and saved == 0:
                raise
            time.sleep(1.0)
        except Exception:
            errors += 1
            if errors > 30 and saved == 0:
                raise
            time.sleep(1.0)

    return {
        "saved": saved,
        "errors": errors,
        "vcidRefreshes": vcid_refreshes,
        "sessionRefreshes": session_refreshes,
        "harvestMs": round((time.perf_counter() - t0) * 1000, 1),
    }


def benchmark_method_http_burst(
    *,
    samples: int,
    delay_ms: int,
    payload: Dict[str, Any],
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """Measure HTTP GET captcha throughput after form is open."""
    import booker_legacy as booker

    t_setup = time.perf_counter()
    fresh, cookie = open_capture_form_http(payload)
    setup_ms = (time.perf_counter() - t_setup) * 1000

    vcid = fresh.bdc_vcid
    latencies: List[float] = []
    unique_sha: set[str] = set()
    errors = 0
    t_loop = time.perf_counter()

    for i in range(samples):
        try:
            t0 = time.perf_counter()
            data = fetch_captcha_http(
                session_cookie=cookie,
                vcid=vcid,
                origin=booker.APPOINTMENT_ORIGIN,
                scheduler_url=booker.SCHEDULER_URL,
            )
            latencies.append((time.perf_counter() - t0) * 1000)
            import hashlib

            unique_sha.add(hashlib.sha256(data).hexdigest())
            if on_progress:
                on_progress(i + 1, samples)
            if delay_ms > 0 and i + 1 < samples:
                time.sleep(delay_ms / 1000.0)
        except Exception:
            errors += 1

    loop_ms = (time.perf_counter() - t_loop) * 1000
    ok = len(latencies)
    return {
        "method": "http_burst",
        "samples": samples,
        "ok": ok,
        "errors": errors,
        "setupMs": round(setup_ms, 1),
        "loopMs": round(loop_ms, 1),
        "totalMs": round(setup_ms + loop_ms, 1),
        "msPerImage": round(loop_ms / ok, 1) if ok else None,
        "imagesPerSec": round(ok / (loop_ms / 1000.0), 2) if loop_ms > 0 and ok else 0,
        "uniqueImages": len(unique_sha),
        "duplicateRate": round(1 - len(unique_sha) / ok, 3) if ok else 0,
        "delayMs": delay_ms,
        "vcidTail": vcid[-8:] if vcid else "",
    }
