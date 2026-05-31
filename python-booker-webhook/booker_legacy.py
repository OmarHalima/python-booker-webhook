import base64
import os
import re
import threading
import uuid
from contextlib import contextmanager

try:
    import fcntl
except ImportError:  # Windows dev machines
    fcntl = None  # type: ignore[assignment]
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import json
import time
from urllib.parse import parse_qs

import requests
from fastapi import HTTPException
from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
from dotenv import load_dotenv
from pathlib import Path

from captcha_ocr import (
    captcha_ocr_base_url,
    captcha_ocr_timeout,
    normalize_captcha_text,
    parse_ocr_response,
    predict_file as ocr_predict_file,
    validate_captcha_text,
)


APPOINTMENT_ORIGIN = "https://appointment.bmeia.gv.at"
SCHEDULER_URL = f"{APPOINTMENT_ORIGIN}/HomeWeb/Scheduler"

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

@dataclass
class FreshFields:
    booking_endpoint: str
    token: str
    start_time: str
    bdc_vcid: str
    bdc_hs: str
    bdc_sp: str
    bdc_bw: str
    captcha_image_url: str
    html: str


ARTIFACTS_DIR = Path(__file__).with_name("artifacts")
RUN_ID_COUNTER_FILE = ARTIFACTS_DIR / ".sequential_run_id"
_RUN_ID_ALLOC_LOCK = threading.Lock()
_DEBUG_LOG_LOCK = threading.Lock()
_RUN_ID_SEQ_PATTERN = re.compile(r"^run-(\d+)$", re.IGNORECASE)
DEBUG_LOG_PATH = Path(__file__).with_name("debug-ce612b.log")
_BOOKING_SEM: Optional[threading.Semaphore] = None
_BOOKING_SEM_INIT = threading.Lock()
_GLOBAL_BOOKING_LOCK_PATH = ARTIFACTS_DIR / ".global_booking.lock"

def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def playwright_headless() -> bool:
    """Chromium headless mode. Default false (headed) for captcha/UI parity; set PLAYWRIGHT_HEADLESS=true on VPS without Xvfb."""
    return _env_bool("PLAYWRIGHT_HEADLESS", False)


def max_concurrent_bookings() -> int:
    """
    Playwright sync API is not thread-safe. FastAPI runs sync handlers in a thread pool;
    limit concurrent run_booking_flow calls per process (default 1).
    Scale with multiple uvicorn workers (processes), not higher values here.
    """
    return _env_int("MAX_CONCURRENT_BOOKINGS", 1, minimum=1)


def booking_slot_wait_sec() -> float:
    """Max seconds to wait for a free booking slot before HTTP 503."""
    return _env_float("BOOKING_SLOT_WAIT_SEC", 15.0, minimum=0.0)


def booking_busy_retry_after_sec() -> int:
    return _env_int("BOOKING_BUSY_RETRY_AFTER_SEC", 60, minimum=1)


def use_global_booking_lock() -> bool:
    """Serialize bookings across all uvicorn workers (one Chromium at a time on the host)."""
    return _env_bool("BOOKING_USE_GLOBAL_LOCK", True)


def _worker_log_meta() -> Dict[str, Any]:
    return {"pid": os.getpid()}


def _session_id_tail(session_cookie: str) -> str:
    m = re.search(r"ASP\.NET_SessionId=([^;\s]+)", session_cookie or "", re.IGNORECASE)
    if not m:
        return ""
    val = m.group(1).strip()
    return val[-12:] if len(val) > 12 else val


def _try_acquire_global_flock(lock_fd: int, timeout_sec: float) -> bool:
    """Non-blocking flock poll — avoids holding HTTP connections open for entire booking duration."""
    if fcntl is None:
        return True
    if timeout_sec <= 0:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            return False
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            time.sleep(0.25)
    return False


@contextmanager
def _optional_global_booking_lock(run_id: str, session_cookie: str = ""):
    if not use_global_booking_lock() or fcntl is None:
        yield
        return
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    wait_sec = booking_slot_wait_sec()
    meta = {
        **_worker_log_meta(),
        "waitSec": wait_sec,
        "sessionIdTail": _session_id_tail(session_cookie),
    }
    dlog(
        run_id,
        "H_config",
        "app.py:_optional_global_booking_lock",
        "waiting_global_flock",
        meta,
    )
    lock_file = open(_GLOBAL_BOOKING_LOCK_PATH, "a+", encoding="utf-8")
    try:
        if not _try_acquire_global_flock(lock_file.fileno(), wait_sec):
            dlog(
                run_id,
                "H_config",
                "app.py:_optional_global_booking_lock",
                "global_flock_timeout",
                meta,
            )
            retry_after = str(booking_busy_retry_after_sec())
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "BOOKING_SERVER_BUSY",
                    "message": (
                        "Another booking is already running on this server (global lock). "
                        "Do not run parallel HTTP calls to /webhook/booker — use an n8n queue "
                        "or retry on 503. Parallel calls appear frozen until the active booking finishes."
                    ),
                    "workerPid": os.getpid(),
                    "retryAfterSec": int(retry_after),
                    "globalLock": True,
                },
                headers={"Retry-After": retry_after},
            )
        dlog(
            run_id,
            "H_config",
            "app.py:_optional_global_booking_lock",
            "global_flock_acquired",
            meta,
        )
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            dlog(
                run_id,
                "H_config",
                "app.py:_optional_global_booking_lock",
                "global_flock_released",
                _worker_log_meta(),
            )
    finally:
        lock_file.close()


def _get_booking_semaphore() -> threading.Semaphore:
    global _BOOKING_SEM
    with _BOOKING_SEM_INIT:
        if _BOOKING_SEM is None:
            _BOOKING_SEM = threading.Semaphore(max_concurrent_bookings())
        return _BOOKING_SEM


@contextmanager
def _booking_execution_slot(run_id: str, session_cookie: str = ""):
    # Global flock = one booking for the whole VPS. Timed acquire → 503, not a 5‑minute hang.
    if use_global_booking_lock() and fcntl is not None:
        with _optional_global_booking_lock(run_id, session_cookie):
            yield
        return

    sem = _get_booking_semaphore()
    limit = max_concurrent_bookings()
    wait_sec = booking_slot_wait_sec()
    meta = {
        **_worker_log_meta(),
        "maxConcurrent": limit,
        "waitSec": wait_sec,
        "globalLock": False,
        "sessionIdTail": _session_id_tail(session_cookie),
    }
    dlog(
        run_id,
        "H_config",
        "app.py:_booking_execution_slot",
        "waiting_for_slot",
        meta,
    )
    if wait_sec <= 0:
        acquired = sem.acquire(blocking=False)
    else:
        acquired = sem.acquire(timeout=wait_sec)
    if not acquired:
        dlog(
            run_id,
            "H_config",
            "app.py:_booking_execution_slot",
            "slot_wait_timeout",
            meta,
        )
        retry_after = str(booking_busy_retry_after_sec())
        raise HTTPException(
            status_code=503,
            detail={
                "error": "BOOKING_SERVER_BUSY",
                "message": (
                    f"All {limit} booking slot(s) in this worker process are in use "
                    f"(pid={os.getpid()}). Enable BOOKING_USE_GLOBAL_LOCK=true on Linux VPS "
                    "or run bookings serially in n8n."
                ),
                "maxConcurrentBookings": limit,
                "workerPid": os.getpid(),
                "retryAfterSec": int(retry_after),
            },
            headers={"Retry-After": retry_after},
        )
    dlog(
        run_id,
        "H_config",
        "app.py:_booking_execution_slot",
        "slot_acquired",
        meta,
    )
    try:
        yield
    finally:
        sem.release()
        dlog(
            run_id,
            "H_config",
            "app.py:_booking_execution_slot",
            "slot_released",
            _worker_log_meta(),
        )


_LANDING_OFFICE_SELECTOR = "#Office, select[name='Office']"


# Playwright / form waits — override via .env (see .env.example).
def landing_goto_timeout_ms() -> int:
    return _env_int("LANDING_GOTO_TIMEOUT_MS", 45000, minimum=5000)


def landing_ready_timeout_ms() -> int:
    return _env_int("LANDING_READY_TIMEOUT_MS", 45000, minimum=5000)


def landing_goto_max_attempts() -> int:
    return _env_int("LANDING_GOTO_MAX_ATTEMPTS", 3, minimum=1)


def form_load_timeout_ms() -> int:
    """Capture form: #Token, #StartTime, #BDC_VCID_Captcha."""
    return _env_int("FORM_LOAD_TIMEOUT_MS", 15000, minimum=500)


def postback_settle_timeout_ms() -> int:
    """Wait for ASP.NET postback shell (title 'Loading…') to finish."""
    return _env_int("POSTBACK_SETTLE_TIMEOUT_MS", 60000, minimum=1000)


def postback_stuck_reload_max() -> int:
    return _env_int("POSTBACK_STUCK_RELOAD_MAX", 1, minimum=0)


def booking_allow_slot_fallback() -> bool:
    """When false (default), fail if currentSlot is not among Start radios — no silent first-radio pick."""
    return _env_bool("BOOKING_ALLOW_SLOT_FALLBACK", False)


def _use_scheduler_post_navigation(payload: Dict[str, Any]) -> bool:
    """Skip full wizard; POST /HomeWeb/Scheduler with Monday+Start from payload (matches n8n slot detect)."""
    if _env_bool("BOOKING_DEFAULT_SCHEDULER_POST", False):
        return True
    mode = str(payload.get("navigationMode") or "").strip().lower()
    if mode in ("scheduler_post", "scheduler", "post"):
        return True
    if payload.get("useSchedulerPost") is True:
        return True
    if payload.get("bookingFormReady") is True:
        return True
    return False


def nav_dom_content_loaded_ms() -> int:
    return _env_int("NAV_DOM_CONTENT_LOADED_MS", 20000, minimum=1000)


def nav_network_idle_ms() -> int:
    return _env_int("NAV_NETWORK_IDLE_MS", 12000, minimum=1000)


def nav_settle_dom_ms() -> int:
    return _env_int("NAV_SETTLE_DOM_MS", 25000, minimum=1000)


def select_option_timeout_ms() -> int:
    return _env_int("SELECT_OPTION_TIMEOUT_MS", 8000, minimum=1000)


def person_count_wait_ms() -> int:
    return _env_int("PERSON_COUNT_WAIT_MS", 30000, minimum=1000)


def information_page_wait_ms() -> int:
    return _env_int("INFORMATION_PAGE_WAIT_MS", 35000, minimum=1000)


def choose_appointment_wait_ms() -> int:
    return _env_int("CHOOSE_APPOINTMENT_WAIT_MS", 60000, minimum=1000)


def start_radios_wait_ms() -> int:
    return _env_int("START_RADIOS_WAIT_MS", 25000, minimum=1000)


def submit_next_click_ms() -> int:
    return _env_int("SUBMIT_NEXT_CLICK_MS", 8000, minimum=1000)


def submit_next_navigation_ms() -> int:
    return _env_int("SUBMIT_NEXT_NAVIGATION_MS", 45000, minimum=1000)


def captcha_field_click_ms() -> int:
    return _env_int("CAPTCHA_FIELD_CLICK_MS", 15000, minimum=1000)


def _calendar_options_wait_seconds() -> float:
    return _env_float("CALENDAR_OPTIONS_WAIT_SEC", 10.0, minimum=1.0)


def _calendar_selector_timeout_ms() -> int:
    return int(_calendar_options_wait_seconds() * 1000)


def calendar_options_poll_ms() -> int:
    return _env_int("CALENDAR_OPTIONS_POLL_MS", 400, minimum=50)


def dlog(run_id: str, hypothesis_id: str, location: str, message: str, data: Dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "ce612b",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG_LOCK:
            with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion agent log


def _pick(html: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, html, re.IGNORECASE)
    return m.group(1) if m else None


def parse_fresh_fields(html: str, fallback_endpoint: str = f"{APPOINTMENT_ORIGIN}/HomeWeb/Appointment") -> FreshFields:
    if "BDC_VCID_Captcha" not in html:
        raise ValueError("Booking form/captcha not found in HTML.")

    bdc_vcid = _pick(html, r'name="BDC_VCID_Captcha"[^>]*value="([^"]+)"')
    bdc_hs = _pick(html, r'name="BDC_Hs_Captcha"[^>]*value="([^"]+)"') or ""
    bdc_sp = _pick(html, r'name="BDC_SP_Captcha"[^>]*value="([^"]+)"') or ""
    bdc_bw = _pick(html, r'name="BDC_BackWorkaround_Captcha"[^>]*value="([^"]+)"') or "0"
    token = _pick(html, r'id="Token"[^>]*value="([^"]+)"')
    start_time = _pick(html, r'id="StartTime"[^>]*value="([^"]+)"')
    action = _pick(html, r"<form[^>]*action=\"([^\"]+)\"")

    if not (bdc_vcid and token and start_time):
        raise ValueError("Missing one or more required fields: VCID/Token/StartTime.")

    booking_endpoint = f"{APPOINTMENT_ORIGIN}{action}" if action else fallback_endpoint
    captcha_image_url = f"{APPOINTMENT_ORIGIN}/BotDetectCaptcha.ashx?get=image&c=Captcha&t={bdc_vcid}"

    return FreshFields(
        booking_endpoint=booking_endpoint,
        token=token,
        start_time=start_time,
        bdc_vcid=bdc_vcid,
        bdc_hs=bdc_hs,
        bdc_sp=bdc_sp,
        bdc_bw=bdc_bw,
        captcha_image_url=captcha_image_url,
        html=html,
    )


def parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        pairs[k.strip()] = v.strip()
    return pairs


def merge_session_cookie_from_set_cookie(session_cookie: str, set_cookie_raw: Optional[str]) -> str:
    """
    Overlay ASP.NET_SessionId from a Set-Cookie header (or raw multi-header text)
    onto an existing Cookie header string. Used after POST /HomeWeb/Scheduler when
    the server rotates the session id.
    """
    if not set_cookie_raw or not str(set_cookie_raw).strip():
        return session_cookie
    m = re.search(r"ASP\.NET_SessionId\s*=\s*([^;\s]+)", str(set_cookie_raw), re.IGNORECASE)
    if not m:
        return session_cookie
    new_sid = m.group(1).strip()
    pairs = parse_cookie_header(session_cookie)
    pairs["ASP.NET_SessionId"] = new_sid
    return "; ".join(f"{k}={v}" for k, v in pairs.items())


def _list_start_radio_values(page: Page) -> List[str]:
    try:
        return list(
            page.evaluate(
                """() => Array.from(
                  document.querySelectorAll("input[type='radio'][name='Start']")
                ).map((el) => String((el).value || ''));"""
            )
            or []
        )
    except Exception:
        return []


def _normalize_slot_for_compare(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip()).upper()
    for a, b in (("A.M.", "AM"), ("P.M.", "PM")):
        t = t.replace(a, b)
    # Drop trailing :00 seconds before AM/PM (7/7/2026 11:00:00 AM vs 11:00 AM).
    t = re.sub(r":(\d{2}):00(?=\s*(AM|PM)\b)", r":\1", t)
    return t


def _find_matching_start_value(requested: str, candidates: List[str]) -> Optional[str]:
    if not requested or not candidates:
        return None
    req_n = _normalize_slot_for_compare(requested)
    for c in candidates:
        if c == requested:
            return c
    for c in candidates:
        if _normalize_slot_for_compare(c) == req_n:
            return c
    return None


def set_context_cookies(context: BrowserContext, cookie_header: str) -> None:
    cookies = parse_cookie_header(cookie_header)
    if not cookies:
        return

    context.add_cookies(
        [
            {
                "name": name,
                "value": value,
                "domain": "appointment.bmeia.gv.at",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
            for name, value in cookies.items()
        ]
    )

def _has_selector(page: Page, selector: str) -> bool:
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def post_select_slot(context: BrowserContext, payload: Dict[str, Any]) -> str:
    referer = str(
        payload.get("schedulerReferer")
        or f"{APPOINTMENT_ORIGIN}/?fromSpecificInfo=True"
    )
    headers = {
        "Cookie": payload["sessionCookie"],
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": APPOINTMENT_ORIGIN,
        "Referer": referer,
        "User-Agent": payload.get(
            "userAgent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ),
    }
    form = {
        "Language": payload["Language"],
        "Office": payload["Office"],
        "CalendarId": str(payload["CalendarId"]),
        "PersonCount": "1",
        "Monday": payload["currentMonday"],
        "Start": payload["currentSlot"],
        "Command": "Next",
    }

    response = context.request.post(SCHEDULER_URL, headers=headers, form=form)
    if not response.ok:
        raise ValueError(f"POST Select Slot failed: {response.status} {response.status_text}")
    return response.text()


def fetch_captcha_bytes(context: BrowserContext, url: str, session_cookie: str) -> bytes:
    response = context.request.get(
        url,
        headers={
            "Cookie": session_cookie,
            "Referer": SCHEDULER_URL,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    if not response.ok:
        raise ValueError(f"Captcha download failed: {response.status} {response.status_text}")
    return response.body()

def capture_captcha_bytes_from_dom(page: Page) -> bytes:
    # Capture exactly what is shown in the real browser page.
    img = page.locator("#Captcha_CaptchaImage")
    if img.count() < 1:
        raise ValueError("Captcha image element #Captcha_CaptchaImage not found in DOM.")
    return img.first.screenshot(type="png")


def _call_gemini_generate(model: str, api_key: str, payload: Dict[str, Any], timeout: int = 45) -> requests.Response:
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    return requests.post(endpoint, json=payload, timeout=timeout)


def _extract_captcha_text_from_gemini_json(data: Dict[str, Any]) -> str:
    text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    return normalize_captcha_text(text)


def _captcha_solver_mode() -> str:
    """ocr (default: ocr then gemini fallback), gemini-only, or ocr-only."""
    return (os.getenv("CAPTCHA_SOLVER") or "ocr_with_gemini_fallback").strip().lower()


def solve_captcha_with_ocr(
    image_bytes: bytes, run_id: str = "", attempt: int = 0, vcid: str = ""
) -> str:
    base = captcha_ocr_base_url()
    if not base:
        raise ValueError("CAPTCHA_OCR_BASE_URL is missing.")

    timeout = captcha_ocr_timeout()
    if run_id:
        dlog(
            run_id,
            "H_ocr_req",
            "app.py:solve_captcha_with_ocr",
            "ocr_request_start",
            {
                "attempt": attempt,
                "baseUrl": base,
                "imageBytes": len(image_bytes),
                "timeout": timeout,
                "vcidTail": vcid[-8:] if vcid else "",
            },
        )

    try:
        data = ocr_predict_file(base, image_bytes, timeout=timeout)
    except requests.RequestException as exc:
        if run_id:
            dlog(
                run_id,
                "H_ocr_resp",
                "app.py:solve_captcha_with_ocr",
                "ocr_request_error",
                {"attempt": attempt, "error": str(exc)[:300]},
            )
        raise ValueError(f"OCR captcha request failed: {exc}") from exc

    if run_id:
        dlog(
            run_id,
            "H_ocr_resp",
            "app.py:solve_captcha_with_ocr",
            "ocr_response_status",
            {"attempt": attempt, "hasResultKey": isinstance(data, dict) and "result" in data},
        )

    raw = parse_ocr_response(data)
    captcha = validate_captcha_text(raw, source="OCR")
    if run_id:
        dlog(
            run_id,
            "H_ocr_resp",
            "app.py:solve_captcha_with_ocr",
            "ocr_text_parsed",
            {
                "attempt": attempt,
                "normalizedLen": len(captcha),
                "isAlnum": captcha.isalnum(),
            },
        )
    return captcha


def solve_captcha(image_bytes: bytes, run_id: str = "", attempt: int = 0, vcid: str = "") -> str:
    mode = _captcha_solver_mode()
    if mode == "gemini":
        return solve_captcha_with_gemini(image_bytes, run_id=run_id, attempt=attempt, vcid=vcid)
    if mode == "ocr":
        return solve_captcha_with_ocr(image_bytes, run_id=run_id, attempt=attempt, vcid=vcid)

    # Default: ocr_with_gemini_fallback (and legacy unset CAPTCHA_SOLVER).
    try:
        return solve_captcha_with_ocr(image_bytes, run_id=run_id, attempt=attempt, vcid=vcid)
    except Exception as ocr_exc:
        if run_id:
            dlog(
                run_id,
                "H_ocr_resp",
                "app.py:solve_captcha",
                "ocr_failed_using_gemini_fallback",
                {"attempt": attempt, "error": str(ocr_exc)[:300]},
            )
        return solve_captcha_with_gemini(image_bytes, run_id=run_id, attempt=attempt, vcid=vcid)


def solve_captcha_with_gemini(image_bytes: bytes, run_id: str = "", attempt: int = 0, vcid: str = "") -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing.")

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "").strip()
    if run_id:
        dlog(
            run_id,
            "H_gemini_req",
            "app.py:solve_captcha_with_gemini",
            "gemini_request_start",
            {
                "attempt": attempt,
                "model": model,
                "fallbackModel": fallback_model,
                "imageBytes": len(image_bytes),
                "vcidTail": vcid[-8:] if vcid else "",
            },
        )

    prompt = (
        "Return ONLY the CAPTCHA code exactly as shown in the image, nothing else. "
        "Preserve case exactly. No punctuation, no explanation."
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ]
    }

    resp = _call_gemini_generate(model, api_key, payload, timeout=45)
    if run_id:
        dlog(
            run_id,
            "H_gemini_resp",
            "app.py:solve_captcha_with_gemini",
            "gemini_response_status",
            {
                "attempt": attempt,
                "model": model,
                "statusCode": resp.status_code,
                "bodyLen": len(resp.text or ""),
            },
        )

    # If primary model is rate-limited, immediately try fallback model (if configured).
    if resp.status_code == 429 and fallback_model:
        resp_fallback = _call_gemini_generate(fallback_model, api_key, payload, timeout=45)
        if run_id:
            dlog(
                run_id,
                "H_gemini_resp",
                "app.py:solve_captcha_with_gemini",
                "gemini_fallback_response_status",
                {
                    "attempt": attempt,
                    "model": fallback_model,
                    "statusCode": resp_fallback.status_code,
                    "bodyLen": len(resp_fallback.text or ""),
                },
            )
        resp = resp_fallback

    if resp.status_code >= 400:
        raise ValueError(f"Gemini API error {resp.status_code}: {resp.text[:600]}")

    data = resp.json()
    captcha = validate_captcha_text(
        _extract_captcha_text_from_gemini_json(data), source="Gemini"
    )
    if run_id:
        dlog(
            run_id,
            "H_gemini_resp",
            "app.py:solve_captcha_with_gemini",
            "gemini_text_parsed",
            {
                "attempt": attempt,
                "normalizedLen": len(captcha),
                "isAlnum": captcha.isalnum(),
            },
        )
    return captcha


def _parse_json_object_from_gemini_text(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {}


def _collect_empty_booking_field_ids(page: Page) -> list:
    """Return DOM ids of applicant inputs/selects that are still empty after a fill pass."""
    return page.evaluate(
        """() => {
          const form = document.querySelector('form:has(#Token)') || document.querySelector('form');
          if (!form) return [];
          const skip = new Set([
            'Token', 'PersonCount', 'CalendarId', 'Office', 'Language', 'StartTime',
            'BDC_VCID_Captcha', 'BDC_Hs_Captcha', 'BDC_SP_Captcha',
            'BDC_BackWorkaround_Captcha', 'CaptchaText', 'DSGVOAccepted'
          ]);
          const out = [];
          for (const el of form.querySelectorAll('input, select')) {
            const id = el.id;
            if (!id || skip.has(id)) continue;
            const type = (el.type || '').toLowerCase();
            if (type === 'hidden' || type === 'submit' || type === 'button') continue;
            if (el.tagName === 'SELECT') {
              const v = String(el.value || '');
              if (v === '' || v === '0') out.push(id);
            } else {
              const v = String(el.value || '').trim();
              if (!v) out.push(id);
            }
          }
          return out;
        }"""
    )


def _collect_all_booking_field_ids(page: Page) -> list:
    """Return DOM ids of all applicant inputs/selects (excluding hidden booking/captcha fields)."""
    return page.evaluate(
        """() => {
          const form = document.querySelector('form:has(#Token)') || document.querySelector('form');
          if (!form) return [];
          const skip = new Set([
            'Token', 'PersonCount', 'CalendarId', 'Office', 'Language', 'StartTime',
            'BDC_VCID_Captcha', 'BDC_Hs_Captcha', 'BDC_SP_Captcha',
            'BDC_BackWorkaround_Captcha', 'CaptchaText', 'DSGVOAccepted'
          ]);
          const out = [];
          for (const el of form.querySelectorAll('input, select')) {
            const id = el.id;
            if (!id || skip.has(id)) continue;
            const type = (el.type || '').toLowerCase();
            if (type === 'hidden' || type === 'submit' || type === 'button') continue;
            out.push(id);
          }
          return out;
        }"""
    )


def gemini_fill_missing_applicant_fields(
    student: Dict[str, Any],
    missing_ids: list,
    run_id: str = "",
) -> Dict[str, Any]:
    """
    Ask Gemini for values only for booking form field ids that are still empty.
    Returns a dict of student keys to merge (same names as DOM ids, PascalCase).
    """
    if not missing_ids:
        return {}
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing for form fill.")

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "").strip()

    # Redact nothing for the model — user payload; do not log raw student.
    safe_student = {k: student.get(k) for k in student}
    ids_list = ", ".join(missing_ids)
    prompt = (
        "You complete an Austrian BMEIA appointment booking form. "
        "Given partial applicant data as JSON and a list of HTML field ids that are still empty, "
        "return ONLY a single JSON object whose keys are EXACTLY those field ids (PascalCase matching the ids). "
        "Fill every listed key with a plausible value consistent with the rest of the applicant data. "
        "Rules: Sex must be '1','2','3', or '4'. DateOfBirth, TraveldocumentDateOfIssue, TraveldocumentValidUntil "
        "use US-style dates like M/D/YYYY. Country, NationalityAtBirth, CountryOfBirth, NationalityForApplication, "
        "TraveldocumentIssuingAuthority must be numeric option values as strings (e.g. '49') when the form uses country codes; "
        "reuse codes already present in applicant data when possible. "
        "If LastnameAtBirth is missing, use Lastname. TraveldocumentNumber: alphanumeric. "
        "No markdown, no explanation — only the JSON object.\n\n"
        f"Empty field ids to fill: [{ids_list}]\n\n"
        f"Applicant JSON:\n{json.dumps(safe_student, ensure_ascii=False)}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = _call_gemini_generate(model, api_key, payload, timeout=60)
    if resp.status_code == 429 and fallback_model:
        resp = _call_gemini_generate(fallback_model, api_key, payload, timeout=60)
    if resp.status_code >= 400:
        raise ValueError(f"Gemini form fill error {resp.status_code}: {resp.text[:600]}")
    text = (
        resp.json()
        .get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    merged = _parse_json_object_from_gemini_text(text)
    if run_id:
        dlog(
            run_id,
            "H_gemini_form",
            "app.py:gemini_fill_missing_applicant_fields",
            "gemini_form_fill_parsed",
            {
                "missingCount": len(missing_ids),
                "returnedKeys": list(merged.keys())[:20],
                "returnedKeyCount": len(merged),
            },
        )
    return merged


def inject_base_href(html: str) -> str:
    if "<head>" in html:
        return html.replace("<head>", f'<head><base href="{APPOINTMENT_ORIGIN}/">', 1)
    return f'<base href="{APPOINTMENT_ORIGIN}/">{html}'


def load_capture_html_in_browser(page: "Page", html: str, run_id: str = "") -> None:
    """Load capture form HTML into browser on a real BMEIA URL (not about:blank).

    Navigates to APPOINTMENT_ORIGIN first so fetch/form-submit use the correct
    origin, then injects the HTML via set_content.
    """
    if "about:blank" in (page.url or ""):
        page.goto(f"{APPOINTMENT_ORIGIN}/HomeWeb/Scheduler", wait_until="domcontentloaded")
    page.set_content(inject_base_href(html), wait_until="domcontentloaded")
    _wait_postback_settle(page, run_id, "load_capture_html_in_browser")


def fill_form_fields(page: Page, payload: Dict[str, Any]) -> None:
    run_id = str(payload.get("_runId") or payload.get("runId") or "")
    student = payload["student"]
    text_fields = {
        "#Lastname": student.get("Lastname", ""),
        "#Firstname": student.get("Firstname", ""),
        "#DateOfBirth": student.get("DateOfBirth", ""),
        "#TraveldocumentNumber": student.get("TraveldocumentNumber", ""),
        "#Street": student.get("Street", ""),
        "#Postcode": str(student.get("Postcode", "")),
        "#City": student.get("City", ""),
        "#Telephone": str(student.get("Telephone", "")),
        "#Email": student.get("Email", ""),
        "#LastnameAtBirth": student.get("LastnameAtBirth", ""),
        "#PlaceOfBirth": student.get("PlaceOfBirth", ""),
        "#TraveldocumentDateOfIssue": student.get("TraveldocumentDateOfIssue", ""),
        "#TraveldocumentValidUntil": student.get("TraveldocumentValidUntil", ""),
    }
    for selector, value in text_fields.items():
        loc = page.locator(selector)
        count = loc.count()
        if count > 0:
            # Only log lengths (no PII)
            before = loc.first.input_value()
            page.fill(selector, value)
            after = loc.first.input_value()
            if run_id:
                dlog(
                    run_id,
                    "H_fill_text",
                    "app.py:fill_form_fields",
                    "fill_text_attempt",
                    {
                        "selector": selector,
                        "count": count,
                        "beforeLen": len(before),
                        "valueLen": len(str(value)),
                        "afterLen": len(after),
                    },
                )

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
    if normalized in {"1", "2", "3", "4"} and page.locator("#Sex").count() > 0:
        page.select_option("#Sex", normalized)
        if run_id:
            dlog(
                run_id,
                "H_fill_select",
                "app.py:fill_form_fields",
                "select_sex",
                {"selector": "#Sex", "value": normalized},
            )

    select_fields = {
        "#Country": str(student.get("Country", "")),
        "#NationalityAtBirth": str(student.get("NationalityAtBirth", "")),
        "#CountryOfBirth": str(student.get("CountryOfBirth", "")),
        "#NationalityForApplication": str(student.get("NationalityForApplication", "")),
        "#TraveldocumentIssuingAuthority": str(student.get("TraveldocumentIssuingAuthority", "")),
    }
    for selector, value in select_fields.items():
        if value and page.locator(selector).count() > 0:
            page.select_option(selector, value=value)
            if run_id:
                dlog(
                    run_id,
                    "H_fill_select",
                    "app.py:fill_form_fields",
                    "select_option",
                    {"selector": selector, "valueLen": len(str(value))},
                )

    # GDPR / consent checkbox (e.g. /HomeWeb/Appointment — Cairo and other missions).
    dsgvo_raw: Any
    if "dsgvoAccepted" in payload:
        dsgvo_raw = payload.get("dsgvoAccepted")
    elif "DSGVOAccepted" in student:
        dsgvo_raw = student.get("DSGVOAccepted")
    else:
        dsgvo_raw = True
    if isinstance(dsgvo_raw, str):
        want_dsgvo = dsgvo_raw.strip().lower() in ("1", "true", "yes", "on")
    elif isinstance(dsgvo_raw, bool):
        want_dsgvo = dsgvo_raw
    else:
        want_dsgvo = bool(dsgvo_raw)

    dsgvo = page.locator("#DSGVOAccepted[type='checkbox']")
    if dsgvo.count() > 0:
        if want_dsgvo:
            try:
                if not dsgvo.first.is_checked():
                    dsgvo.first.check()
                if run_id:
                    dlog(
                        run_id,
                        "H_fill_dsgvo",
                        "app.py:fill_form_fields",
                        "dsgvo_checked",
                        {"checked": bool(dsgvo.first.is_checked())},
                    )
            except Exception as exc:
                if run_id:
                    dlog(
                        run_id,
                        "H_fill_dsgvo",
                        "app.py:fill_form_fields",
                        "dsgvo_check_failed",
                        {"error": str(exc)[:200]},
                    )
        elif run_id:
            dlog(
                run_id,
                "H_fill_dsgvo",
                "app.py:fill_form_fields",
                "dsgvo_skipped_by_payload",
                {"wantDsgvo": False},
            )


def is_success_html(html: str) -> bool:
    return ("Capture data" not in html) and ("Austrian appointment system - Capture" not in html)


_MESSAGE_ERROR_RE = re.compile(
    r'<p\s+class=["\']message-error["\'][^>]*>([^<]+)</p>',
    re.IGNORECASE,
)


def extract_message_error_texts(html: str) -> List[str]:
    return [m.strip() for m in _MESSAGE_ERROR_RE.findall(html or "") if m.strip()]


def classify_post_submit_html(html: str) -> Optional[Tuple[str, str]]:
    """
    Map BMEIA <p class="message-error"> (and known phrases) to resultType.
    Checked before captcha retry — the capture form often stays on page with BotDetect fields.
    """
    texts = extract_message_error_texts(html)
    blob = " ".join(texts).lower() if texts else (html or "").lower()

    if "already reserved an appointment" in blob or (
        "bereits" in blob and "termin" in blob and "reserv" in blob
    ):
        msg = texts[0] if texts else "You have already reserved an appointment."
        return ("ALREADY_BOOKED", msg)

    if "allocated otherwise in the meantime" in blob or (
        "zwischenzeit" in blob and "vergeben" in blob
    ):
        msg = texts[0] if texts else "The appointment was allocated otherwise in the meantime."
        return ("SLOT_TAKEN", msg)

    if "no appointments available" in blob or "keine termine" in blob:
        msg = texts[0] if texts else "No appointments available."
        return ("SLOT_TAKEN", msg)

    return None


def _set_scheduler_value(page: Page, selector: str, value: str) -> bool:
    if not value:
        return False
    loc = page.locator(selector)
    if loc.count() < 1:
        return False
    tag_name = (loc.first.evaluate("el => el.tagName") or "").lower()
    if tag_name == "select":
        want = str(value)
        try:
            loc.first.select_option(value=want, timeout=select_option_timeout_ms())
        except Exception:
            pass
        try:
            ok = loc.first.evaluate(
                """(el, v) => {
                  const want = String(v);
                  if (el.value === want) return true;
                  const has = Array.from(el.options).some((o) => o.value === want);
                  if (!has) return false;
                  el.value = want;
                  el.dispatchEvent(new Event("input", { bubbles: true }));
                  el.dispatchEvent(new Event("change", { bubbles: true }));
                  return el.value === want;
                }""",
                want,
            )
            return bool(ok)
        except Exception:
            return False
    try:
        loc.first.fill(str(value))
        return True
    except Exception:
        # Hidden input fallback
        try:
            loc.first.evaluate("(el, v) => { el.value = String(v); }", str(value))
            return True
        except Exception:
            return False


def _calendar_id_reservation_options_snapshot(page: Page) -> List[Dict[str, str]]:
    try:
        raw = page.evaluate(
            """() => {
              const sel = document.querySelector("select[name='CalendarId'], #CalendarId");
              if (!sel) return [];
              return Array.from(sel.querySelectorAll("option")).map((o) => ({
                value: String(o.value || ""),
                text: String((o.textContent || "").trim()),
              }));
            }"""
        )
        return list(raw or [])
    except Exception:
        return []


def resolve_calendar_id_option_value(
    calendar_id: str,
    reservation_match: str,
    options: List[Dict[str, str]],
) -> Optional[str]:
    """
    Decide which <select name='CalendarId'> option value to use.

    Prefer an exact ``calendar_id`` when that value exists on an option (and is
    not the empty "Please select" sentinel ``0``). Otherwise, if
    ``reservation_match`` is non-empty, return the first option (DOM order) whose
    visible text contains that substring (case-insensitive). Used when the
    mission drops old IDs (e.g. Bachelor vs Master-only student queues).
    """
    cal = (calendar_id or "").strip()
    hint = (reservation_match or "").strip()
    values = {str(o.get("value", "")) for o in options}
    if cal and cal in values and cal != "0":
        return cal
    if hint:
        h = hint.lower()
        for o in options:
            v = str(o.get("value", "")).strip()
            if not v or v == "0":
                continue
            text = (o.get("text") or "").lower()
            if h in text:
                return v
    return None


def _parse_inject_calendar_flags(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Whether to inject a synthetic CalendarId <option>. Handles n8n string booleans
    (``bool("false")`` is True in Python — we treat ``\"false\"`` as off).
    """
    label = str(payload.get("injectCalendarIdLabel") or "").strip()

    def _on(raw: Any) -> bool:
        if raw is True:
            return True
        if raw is False or raw is None:
            return False
        if isinstance(raw, (int, float)):
            return int(raw) == 1
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in ("true", "yes", "1", "on"):
                return True
            if s in ("false", "no", "0", "off", ""):
                return False
            if s.isdigit():
                return False
            return False
        return False

    inject = _on(payload.get("injectMissingCalendarId")) or _on(payload.get("injectCalendarId"))
    return inject, label


def _read_calendar_id_dom_value(page: Page) -> str:
    try:
        loc = page.locator("select[name='CalendarId'], #CalendarId").first
        if loc.count() < 1:
            return ""
        return str(loc.evaluate("el => String(el.value || '')")).strip()
    except Exception:
        return ""


def _atomic_calendar_id_append_and_select(page: Page, value: str, label: str) -> bool:
    """
    In one synchronous DOM turn: append <option> if missing, set .value, dispatch
    input/change (and jQuery .trigger if present). Avoids a gap where client scripts
    can remove a separately injected option before Playwright's select_option runs.
    """
    v = (value or "").strip()
    if not v or v == "0":
        return False
    lbl = (label or "").strip() or "Requested queue (automation)"
    try:
        return bool(
            page.evaluate(
                """([cv, cl]) => {
                  const sel = document.querySelector("select[name='CalendarId'], #CalendarId");
                  if (!sel) return false;
                  const v = String(cv);
                  const lbl = String(cl || "Requested queue (automation)");
                  if (!Array.from(sel.options).some((o) => o.value === v)) {
                    const opt = document.createElement("option");
                    opt.value = v;
                    opt.textContent = lbl;
                    sel.appendChild(opt);
                  }
                  if (!Array.from(sel.options).some((o) => o.value === v)) return false;
                  sel.value = v;
                  sel.dispatchEvent(new Event("input", { bubbles: true }));
                  sel.dispatchEvent(new Event("change", { bubbles: true }));
                  try {
                    if (typeof window.jQuery !== "undefined" && window.jQuery(sel).trigger) {
                      window.jQuery(sel).trigger("change");
                    }
                  } catch (e) {}
                  return sel.value === v;
                }""",
                [v, lbl],
            )
        )
    except Exception:
        return False


def _inject_missing_calendar_id_option(
    page: Page, value: str, label: str, run_id: str
) -> bool:
    """
    Append ``<option value=...>`` to the CalendarId select if that value is missing.

    Use when the payload must post an exact ``CalendarId`` that is not returned in
    the mission's GET options (server may still reject the next POST).
    """
    v = (value or "").strip()
    if not v or v == "0":
        return False
    lbl = (label or "").strip() or "Requested queue (automation)"
    try:
        added = page.evaluate(
            """([calValue, calLabel]) => {
              const sel = document.querySelector("select[name='CalendarId'], #CalendarId");
              if (!sel) return false;
              const vv = String(calValue);
              if (Array.from(sel.options).some((o) => o.value === vv)) return false;
              const opt = document.createElement("option");
              opt.value = vv;
              opt.textContent = String(calLabel || "Requested queue (automation)");
              sel.appendChild(opt);
              return true;
            }""",
            [v, lbl],
        )
        if added and run_id:
            dlog(
                run_id,
                "H_scheduler",
                "app.py:_inject_missing_calendar_id_option",
                "calendar_id_option_injected",
                {"value": v, "labelLen": len(lbl)},
            )
        return bool(added)
    except Exception:
        return False


def _repair_calendar_id_dom_if_needed(page: Page, payload: Dict[str, Any], run_id: str) -> None:
    """Re-inject + select when inject mode is on but DOM value drifted (e.g. client scripts)."""
    inject, label = _parse_inject_calendar_flags(payload)
    if not inject:
        return
    want = str(payload.get("CalendarId", "")).strip()
    if not want or want == "0":
        return
    dom_v = _read_calendar_id_dom_value(page)
    if dom_v == want:
        return
    ok = _atomic_calendar_id_append_and_select(page, want, label)
    if run_id:
        dlog(
            run_id,
            "H_scheduler",
            "app.py:_repair_calendar_id_dom_if_needed",
            "calendar_id_preflight_repair",
            {"before": dom_v, "after": _read_calendar_id_dom_value(page), "atomicOk": ok},
        )


def _apply_reservation_calendar_select(
    page: Page, payload: Dict[str, Any], run_id: str, probe: Any
) -> None:
    """Reservation step: set CalendarId from payload (exact id and/or text match)."""
    cal_wait_ms = _calendar_selector_timeout_ms()
    try:
        page.wait_for_selector("select[name='CalendarId']", timeout=cal_wait_ms)
    except PlaywrightTimeoutError as exc:
        probe("fail-wait-calendar-id")
        raise ValueError(
            "Reservation step: select[name=CalendarId] did not appear after landing Next. "
            "See artifacts/<runId>/navigation-debug/"
        ) from exc

    cal_val = str(payload.get("CalendarId", "")).strip()
    match_hint = str(
        payload.get("calendarReservationMatch")
        or payload.get("reservationTypeMatch")
        or ""
    ).strip()
    if not cal_val:
        probe("fail-empty-calendar-payload")
        raise ValueError("CalendarId is missing or empty in payload.")

    inject, inject_label = _parse_inject_calendar_flags(payload)
    last_opts: List[Dict[str, str]] = []
    resolved: Optional[str] = None

    if inject and cal_val and cal_val != "0":
        resolved = cal_val
        last_opts = _calendar_id_reservation_options_snapshot(page)
        if run_id:
            dlog(
                run_id,
                "H_scheduler",
                "app.py:_apply_reservation_calendar_select",
                "calendar_id_inject_fast_path",
                {"calendarId": cal_val},
            )
    else:
        deadline = time.time() + _calendar_options_wait_seconds()
        poll_ms = calendar_options_poll_ms()
        while time.time() < deadline:
            last_opts = _calendar_id_reservation_options_snapshot(page)
            resolved = resolve_calendar_id_option_value(cal_val, match_hint, last_opts)
            if resolved:
                break
            real_opts = [
                o
                for o in last_opts
                if str(o.get("value", "")).strip() not in ("", "0")
            ]
            # Options are loaded but ID/hint cannot match — stop waiting (was 48s spin).
            if real_opts:
                break
            page.wait_for_timeout(poll_ms)

    if not resolved:
        if run_id:
            dlog(
                run_id,
                "H_scheduler",
                "app.py:_apply_reservation_calendar_select",
                "calendar_option_unresolved",
                {
                    "requestedCalendarId": cal_val,
                    "calendarReservationMatch": match_hint,
                    "injectMissingCalendarId": inject,
                    "optionsSample": last_opts[:25],
                },
            )
        probe("fail-set-calendar-id")
        choices = ", ".join(
            f"{o.get('value','')}:{(o.get('text') or '')[:60]!r}"
            for o in (last_opts or [])[:12]
        )
        hint_inject = (
            " Or set injectMissingCalendarId: true (and optional injectCalendarIdLabel) "
            "to append your exact CalendarId as a synthetic <option>."
        )
        raise ValueError(
            "Could not resolve CalendarId on the reservation-type step. "
            "Your payload value must match an option value= on the live page "
            '(e.g. Kairo often lists only specific queues such as "nur Master, PhD…"). '
            f"Requested CalendarId={cal_val!r} matchHint={match_hint!r}. "
            f"Options (value:text, first 12): {choices or '(none parsed)'}."
            + (hint_inject if not inject else "")
        )

    if resolved != cal_val and run_id:
        dlog(
            run_id,
            "H_scheduler",
            "app.py:_apply_reservation_calendar_select",
            "calendar_id_resolved_from_match_hint",
            {
                "payloadCalendarId": cal_val,
                "resolvedValue": resolved,
                "calendarReservationMatch": match_hint,
            },
        )

    applied_cal = False
    if inject:
        applied_cal = _atomic_calendar_id_append_and_select(page, str(resolved), inject_label)
    if not applied_cal:
        applied_cal = _set_scheduler_value(page, "#CalendarId", resolved) or _set_scheduler_value(
            page, "select[name='CalendarId']", resolved
        )
    if not applied_cal:
        try:
            cal_meta = page.evaluate(
                """() => {
                  const sel = document.querySelector("select[name='CalendarId'], #CalendarId");
                  if (!sel) return { hasSelect: false, current: "", options: [] };
                  const options = Array.from(sel.querySelectorAll("option")).map(o => ({
                    value: String(o.value || ""),
                    text: String((o.textContent || "").trim()).slice(0, 80),
                  }));
                  return {
                    hasSelect: true,
                    current: String(sel.value || ""),
                    options: options.slice(0, 40),
                  };
                }"""
            )
            dlog(
                run_id,
                "H_scheduler",
                "app.py:_apply_reservation_calendar_select",
                "calendar_option_missing_or_unsettable",
                {"requestedCalendarId": resolved, **cal_meta},
            )
        except Exception:
            pass
        probe("fail-set-calendar-id")
        raise ValueError("Could not apply CalendarId on the reservation-type step.")

    dom_v = _read_calendar_id_dom_value(page)
    if dom_v != str(resolved):
        if run_id:
            dlog(
                run_id,
                "H_scheduler",
                "app.py:_apply_reservation_calendar_select",
                "calendar_id_dom_mismatch_after_apply",
                {"domValue": dom_v, "resolved": str(resolved)},
            )
        if inject and str(resolved) == str(cal_val):
            _atomic_calendar_id_append_and_select(page, str(resolved), inject_label)
            dom_v = _read_calendar_id_dom_value(page)
    if dom_v != str(resolved):
        probe("fail-set-calendar-id")
        raise ValueError(
            "CalendarId did not remain selected in the DOM (value mismatch). "
            f"Expected {resolved!r}, got {dom_v!r}. "
            "See screenshots 02b_after_reservation_calendar and 02c_before_reservation_next "
            "(02_after_landing_next is captured before calendar selection)."
        )
    if run_id:
        dlog(
            run_id,
            "H_scheduler",
            "app.py:_apply_reservation_calendar_select",
            "calendar_id_dom_ok",
            {"value": dom_v},
        )


def _wait_load_settle(page: Page, dom_timeout_ms: Optional[int] = None) -> None:
    """Best-effort settle after a server round-trip (ASP.NET posts)."""
    dom_ms = dom_timeout_ms if dom_timeout_ms is not None else nav_settle_dom_ms()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=dom_ms)
    except Exception:
        pass
    # networkidle often never fires on ASP.NET (polling/ads); under parallel load it adds long stalls.
    if _env_bool("PLAYWRIGHT_WAIT_NETWORK_IDLE", False):
        try:
            page.wait_for_load_state("networkidle", timeout=nav_network_idle_ms())
        except Exception:
            pass


def _captcha_keystroke_delay_ms() -> int:
    try:
        return max(0, int(os.getenv("CAPTCHA_KEYSTROKE_DELAY_MS", "35")))
    except (TypeError, ValueError):
        return 35


def _fill_captcha_text_like_user(page: Page, captcha_text: str) -> None:
    """Dispatches keystroke/input events BotDetect/client scripts may depend on."""
    loc = page.locator("#CaptchaText").first
    loc.click(timeout=captcha_field_click_ms())
    loc.fill("")
    text = captcha_text or ""
    delay = _captcha_keystroke_delay_ms()
    try:
        loc.press_sequentially(text, delay=delay)
    except Exception:
        loc.type(text, delay=delay)
    loc.evaluate("el => el.blur()")


def _inject_agent_command_next_for_captcha(page: Page) -> bool:
    """Hidden Command=Next so submit payloads match Next even when Next control is fiddled."""
    return bool(
        page.evaluate(
            """() => {
          const form = document.querySelector('form');
          if (!form) return false;
          const prev = form.querySelector(
            'input[type="hidden"][name="Command"][data-agent-captcha="1"]'
          );
          if (prev) prev.remove();
          const cmd = document.createElement('input');
          cmd.type = 'hidden';
          cmd.name = 'Command';
          cmd.value = 'Next';
          cmd.setAttribute('data-agent-captcha', '1');
          form.appendChild(cmd);
          return true;
        }"""
        )
    )


def _submit_captcha_passport_form(page: Page, run_id: str, attempt: int) -> str:
    """Inject Command=Next, then Next click → requestSubmit → form.submit()."""
    if not _inject_agent_command_next_for_captcha(page):
        dlog(
            run_id,
            "H_submit_captcha",
            "app.py:_submit_captcha_passport_form",
            "inject_command_failed",
            {"attempt": attempt},
        )
        return "inject_failed"

    next_loc = page.locator("#nextButton")
    if next_loc.count() > 0:
        btn = next_loc.first
        try:
            is_disabled = bool(
                btn.evaluate(
                    "el => !!(el.disabled || el.getAttribute('disabled') != null)"
                )
            )
        except Exception as exc:
            is_disabled = True
            dlog(
                run_id,
                "H_submit_captcha",
                "app.py:_submit_captcha_passport_form",
                "next_button_disabled_probe_error",
                {"attempt": attempt, "error": str(exc)[:200]},
            )
        if not is_disabled:
            try:
                with page.expect_navigation(timeout=submit_next_navigation_ms()):
                    btn.click(timeout=submit_next_click_ms())
                return "next_click"
            except PlaywrightTimeoutError:
                dlog(
                    run_id,
                    "H_submit_captcha",
                    "app.py:_submit_captcha_passport_form",
                    "next_click_no_navigation",
                    {"attempt": attempt},
                )
            except Exception as exc:
                dlog(
                    run_id,
                    "H_submit_captcha",
                    "app.py:_submit_captcha_passport_form",
                    "next_click_exception",
                    {"attempt": attempt, "error": str(exc)[:200]},
                )

    navigated_via_destroy = False
    try:
        ok_req = page.evaluate(
            """() => {
          const nextBtn = document.querySelector('#nextButton');
          if (!nextBtn) return false;
          const form = nextBtn.form || document.querySelector('form');
          if (!form) return false;
          form.requestSubmit(nextBtn);
          return true;
        }"""
        )
    except Exception as exc:
        msg = str(exc)
        if "Execution context was destroyed" in msg:
            navigated_via_destroy = True
            ok_req = True
        else:
            dlog(
                run_id,
                "H_submit_captcha",
                "app.py:_submit_captcha_passport_form",
                "request_submit_exception",
                {"attempt": attempt, "error": msg[:200]},
            )
            ok_req = False

    if ok_req or navigated_via_destroy:
        return "request_submit"

    navigated_via_destroy_submit = False
    try:
        ok_fs = page.evaluate(
            """() => {
          const form = document.querySelector('form');
          if (!form) return false;
          form.submit();
          return true;
        }"""
        )
    except Exception as exc:
        msg = str(exc)
        if "Execution context was destroyed" in msg:
            navigated_via_destroy_submit = True
            ok_fs = True
        else:
            dlog(
                run_id,
                "H_submit_captcha",
                "app.py:_submit_captcha_passport_form",
                "form_submit_exception",
                {"attempt": attempt, "error": msg[:200]},
            )
            ok_fs = False

    if ok_fs or navigated_via_destroy_submit:
        return "form_submit"

    return "none"


def _navigation_probe_meta(page: Page) -> Dict[str, Any]:
    """Lightweight DOM fingerprint for debugging (no PII)."""
    return {
        "url": page.url,
        "title": page.title(),
        "counts": {
            "selectOffice": page.locator("select[name='Office']").count(),
            "selectLanguage": page.locator("select[name='Language']").count(),
            "selectCalendarId": page.locator("select[name='CalendarId']").count(),
            "selectPersonCount": page.locator("select[name='PersonCount']").count(),
            "startRadios": page.locator("input[type='radio'][name='Start']").count(),
            "token": page.locator("#Token").count(),
            "captchaText": page.locator("#CaptchaText").count(),
        },
    }


def _save_navigation_probe(page: Page, run_dir: Path, slug: str, run_id: str) -> Dict[str, Any]:
    """
    Persist screenshot + HTML + JSON when navigation fails or for step tracing.
    Writes under run_dir/navigation-debug/ (same style as manual browser captures).
    """
    out: Dict[str, Any] = {"slug": slug, "paths": {}}
    try:
        probe_dir = run_dir / "navigation-debug"
        probe_dir.mkdir(parents=True, exist_ok=True)
        safe = _safe_slug(slug)
        meta = _navigation_probe_meta(page)
        meta["slug"] = slug
        png_path = probe_dir / f"{safe}.png"
        html_path = probe_dir / f"{safe}.html"
        json_path = probe_dir / f"{safe}.json"
        try:
            page.screenshot(path=str(png_path), full_page=True)
            out["paths"]["png"] = str(png_path)
        except Exception as shot_exc:
            meta["screenshotError"] = str(shot_exc)[:200]
        try:
            # During fast postback transitions, page.content() can throw while
            # the document is being replaced. Fall back to current outerHTML.
            try:
                html_text = page.content()
            except Exception:
                html_text = page.evaluate("() => document.documentElement?.outerHTML || ''")
            html_path.write_text(html_text, encoding="utf-8")
            out["paths"]["html"] = str(html_path)
        except Exception as html_exc:
            meta["htmlError"] = str(html_exc)[:200]
        try:
            json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            out["paths"]["json"] = str(json_path)
        except Exception:
            pass
        dlog(
            run_id,
            "H_nav_probe",
            "app.py:_save_navigation_probe",
            "saved",
            {"slug": slug, "dir": str(probe_dir), "title": meta.get("title", ""), "url": meta.get("url", "")},
        )
        out["meta"] = meta
    except Exception as exc:
        dlog(run_id, "H_nav_probe", "app.py:_save_navigation_probe", "save_error", {"error": str(exc)[:200]})
    return out


def _safe_page_content(page: Page) -> str:
    """Best-effort HTML snapshot even during rapid navigation transitions."""
    try:
        return page.content()
    except Exception:
        try:
            return page.evaluate("() => document.documentElement?.outerHTML || ''")
        except Exception:
            return ""


def _collect_manual_review_actions(
    page: Page,
    run_dir: Path,
    run_id: str,
    wait_seconds: int,
    recorder: Optional["ArtifactRecorder"] = None,
) -> Dict[str, Any]:
    """
    Keep the page open for manual intervention and capture user interactions.
    Returns metadata and saved file paths.
    """
    out: Dict[str, Any] = {"waitSeconds": wait_seconds, "actionsCount": 0, "paths": {}}
    if wait_seconds <= 0:
        return out

    page.evaluate(
        """() => {
          const w = window;
          const key = '__agentManualActionState';
          if (w[key]?.installed) {
            w[key].actions = [];
            w[key].startTs = Date.now();
            return;
          }
          const state = {
            installed: true,
            startTs: Date.now(),
            actions: [],
          };
          const cssPath = (el) => {
            if (!el || !(el instanceof Element)) return '';
            if (el.id) return `#${el.id}`;
            const parts = [];
            let cur = el;
            while (cur && cur.nodeType === 1 && parts.length < 6) {
              let part = cur.tagName.toLowerCase();
              if (cur.classList && cur.classList.length) {
                part += '.' + Array.from(cur.classList).slice(0, 2).join('.');
              }
              const parent = cur.parentElement;
              if (parent) {
                const siblings = Array.from(parent.children).filter((x) => x.tagName === cur.tagName);
                if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(cur) + 1})`;
              }
              parts.unshift(part);
              cur = parent;
            }
            return parts.join(' > ');
          };
          const push = (kind, e) => {
            const t = e?.target;
            const val = t && 'value' in t ? String(t.value || '').slice(0, 120) : '';
            state.actions.push({
              ts: Date.now(),
              type: kind,
              url: location.href,
              title: document.title,
              target: cssPath(t),
              value: val,
            });
            if (state.actions.length > 400) state.actions.shift();
          };
          document.addEventListener('click', (e) => push('click', e), true);
          document.addEventListener('input', (e) => push('input', e), true);
          document.addEventListener('change', (e) => push('change', e), true);
          document.addEventListener('submit', (e) => push('submit', e), true);
          document.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === 'Tab') push(`keydown:${e.key}`, e);
          }, true);
          w[key] = state;
        }"""
    )

    if recorder:
        try:
            out["paths"]["beforeScreenshot"] = recorder.screenshot("manual_review_before_wait")
        except Exception:
            pass

    dlog(
        run_id,
        "H_manual_review",
        "app.py:_collect_manual_review_actions",
        "manual_review_window_started",
        {"waitSeconds": wait_seconds, "url": page.url},
    )
    page.wait_for_timeout(wait_seconds * 1000)

    actions = page.evaluate(
        """() => {
          const s = window.__agentManualActionState;
          return s?.actions || [];
        }"""
    )
    out["actionsCount"] = len(actions)
    out["finalUrl"] = page.url

    manual_dir = run_dir / "manual-review"
    manual_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    actions_path = manual_dir / f"actions-{stamp}.json"
    actions_path.write_text(json.dumps(actions, ensure_ascii=False, indent=2), encoding="utf-8")
    out["paths"]["actionsJson"] = str(actions_path)

    if recorder:
        try:
            out["paths"]["afterScreenshot"] = recorder.screenshot("manual_review_after_wait")
        except Exception:
            pass

    dlog(
        run_id,
        "H_manual_review",
        "app.py:_collect_manual_review_actions",
        "manual_review_window_finished",
        {"waitSeconds": wait_seconds, "actionsCount": len(actions), "finalUrl": page.url},
    )
    return out


def _click_first_available_slot(page: Page, run_id: str) -> Dict[str, Any]:
    # Scan common slot controls and click the first visible candidate with a time-like label.
    slot_info = page.evaluate(
        """() => {
          const out = { clicked: false, text: '', source: '', hrefTail: '', onclickTail: '' };
          const isVisible = (el) => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const st = window.getComputedStyle(el);
            return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
          };
          const getLabel = (el) => ((el.textContent || el.value || '').trim());
          const timeLike = /\\b\\d{1,2}:\\d{2}\\b|\\bAM\\b|\\bPM\\b/i;
          const deny = /(back|next|reload|change|captcha|sound|help)/i;
          const candidates = [];
          const selectors = [
            'input[type=\"radio\"][name=\"Start\"]',
            'a[href*=\"Start=\"]',
            'a[onclick*=\"Start\"]',
            'button[onclick*=\"Start\"]',
            'input[type=\"submit\"][name*=\"Start\"]',
            'input[type=\"button\"][name*=\"Start\"]',
            'td a',
            'td button',
            'td input[type=\"submit\"]'
          ];
          for (const sel of selectors) {
            for (const el of document.querySelectorAll(sel)) {
              const label = getLabel(el);
              if (!isVisible(el)) continue;
              if (el.disabled) continue;
              if (deny.test(label)) continue;
              const href = (el.getAttribute('href') || '');
              const onclick = (el.getAttribute('onclick') || '');
              const val = (el.value || '').trim();
              const isStartRadio = el.matches && el.matches('input[type=\"radio\"][name=\"Start\"]');
              const isSlotish = isStartRadio || timeLike.test(label) || timeLike.test(val)
                || /Start=/i.test(href) || /Start/i.test(onclick);
              if (!isSlotish) continue;
              candidates.push({ el, label: label || val, sel, href, onclick });
            }
          }
          if (!candidates.length) return out;
          const pick = candidates[0];
          pick.el.click();
          out.clicked = true;
          out.text = pick.label.slice(0, 80);
          out.source = pick.sel;
          out.hrefTail = pick.href.slice(-80);
          out.onclickTail = pick.onclick.slice(-80);
          return out;
        }"""
    )
    dlog(
        run_id,
        "H_scheduler",
        "app.py:_click_first_available_slot",
        "slot_click_attempt",
        {
            "clicked": bool(slot_info.get("clicked")),
            "text": str(slot_info.get("text", "")),
            "source": str(slot_info.get("source", "")),
            "hrefTail": str(slot_info.get("hrefTail", "")),
            "onclickTail": str(slot_info.get("onclickTail", "")),
        },
    )
    return slot_info


def _submit_next_command(page: Page, run_id: str, stage: str) -> None:
    """
    Submit the current form with Command=Next in a way that survives disabled
    submitter controls and input/button role differences.
    """
    url_before = page.url
    submitted = False
    navigation_started = False
    submit_js = """() => {
          const form = document.querySelector('form');
          if (!form) return false;
          const prev = form.querySelector(
            'input[type="hidden"][name="Command"][data-agent-step="1"]'
          );
          if (prev) prev.remove();
          const cmd = document.createElement('input');
          cmd.type = 'hidden';
          cmd.name = 'Command';
          cmd.value = 'Next';
          cmd.setAttribute('data-agent-step', '1');
          form.appendChild(cmd);
          form.submit();
          return true;
        }"""
    try:
        with page.expect_navigation(
            timeout=submit_next_navigation_ms(),
            wait_until="domcontentloaded",
        ):
            submitted = bool(page.evaluate(submit_js))
        navigation_started = True
    except PlaywrightTimeoutError:
        # form.submit() may have fired even if Playwright did not observe navigation.
        submitted = True
    except Exception as exc:
        msg = str(exc)
        if "Execution context was destroyed" in msg:
            navigation_started = True
            submitted = True
        else:
            raise
    dlog(
        run_id,
        "H_nav",
        "app.py:_submit_next_command",
        "submit_next_command",
        {
            "stage": stage,
            "submitted": bool(submitted),
            "navigationStarted": navigation_started,
            "urlBeforeSubmit": url_before,
        },
    )
    if not submitted:
        raise ValueError(f"No form found while submitting Next at stage: {stage}")

    _wait_postback_settle(page, run_id, stage)


def _select_appointment_slot(
    page: Page, payload: Dict[str, Any], run_id: str, run_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Select the requested appointment slot value when present, otherwise use the
    first available visible slot on the page.
    """
    requested_slot = str(payload.get("currentSlot", "")).strip()
    selected = {"clicked": False, "strategy": "", "requestedSlot": requested_slot}

    try:
        page.wait_for_selector("input[type='radio'][name='Start']", timeout=start_radios_wait_ms())
    except PlaywrightTimeoutError:
        dlog(
            run_id,
            "H_scheduler",
            "app.py:_select_appointment_slot",
            "wait_start_radios_timeout",
            {"url": page.url, "title": page.title()},
        )
        if run_dir:
            _save_navigation_probe(page, run_dir, "timeout-wait-Start-radios", run_id)

    start_values = _list_start_radio_values(page)
    if run_id and requested_slot:
        dlog(
            run_id,
            "H_scheduler",
            "app.py:_select_appointment_slot",
            "slot_inventory",
            {
                "requestedSlot": requested_slot,
                "count": len(start_values),
                "firstRadioValue": (start_values[0] if start_values else ""),
                "allValuesSample": start_values[:25],
            },
        )

    match_value: Optional[str] = None
    if requested_slot:
        value_css = json.dumps(requested_slot)
        radio = page.locator(
            f"input[type='radio'][name='Start'][value={value_css}]"
        )
        if radio.count() > 0:
            match_value = requested_slot
        elif start_values:
            norm_pick = _find_matching_start_value(requested_slot, start_values)
            if norm_pick:
                match_value = norm_pick
                dlog(
                    run_id,
                    "H_scheduler",
                    "app.py:_select_appointment_slot",
                    "slot_no_exact_match_used_normalized",
                    {
                        "requestedSlot": requested_slot,
                        "matchedValue": norm_pick,
                    },
                )
            else:
                dlog(
                    run_id,
                    "H_scheduler",
                    "app.py:_select_appointment_slot",
                    "slot_no_exact_match_no_normalized",
                    {
                        "requestedSlot": requested_slot,
                        "normalizedRequested": _normalize_slot_for_compare(requested_slot),
                        "firstRadioValue": start_values[0] if start_values else "",
                        "allValuesSample": start_values[:25],
                    },
                )

    if match_value:
        value_css = json.dumps(match_value)
        radio = page.locator(
            f"input[type='radio'][name='Start'][value={value_css}]"
        )
        if radio.count() > 0:
            radio.first.check(force=True)
            selected["clicked"] = True
            selected["strategy"] = (
                "requested_slot_exact_match"
                if match_value == requested_slot
                else "requested_slot_normalized_match"
            )
            selected["matchedValue"] = match_value
            dlog(
                run_id,
                "H_scheduler",
                "app.py:_select_appointment_slot",
                "slot_selected_by_requested_value",
                {"requestedSlot": requested_slot, "matchedValue": match_value},
            )
            return selected

    any_start = page.locator("input[type='radio'][name='Start']")
    if any_start.count() > 0:
        if requested_slot and not booking_allow_slot_fallback():
            available = start_values or _list_start_radio_values(page)
            if run_dir:
                _save_navigation_probe(page, run_dir, "fail-slot-not-in-dom", run_id)
            raise ValueError(
                f"Requested currentSlot {requested_slot!r} is not available on the choose-appointment page. "
                f"DOM Monday may differ from currentMonday (see monday_dom_vs_payload in logs). "
                f"Available Start values: {available[:10]}. "
                "Use a fresh sessionCookie from the same scheduler capture, or set useSchedulerPost: true. "
                "Set BOOKING_ALLOW_SLOT_FALLBACK=true only to allow booking the first visible slot."
            )
        any_start.first.check(force=True)
        selected["clicked"] = True
        selected["strategy"] = "first_start_radio_playwright"
        selected["warnFallback"] = bool(requested_slot)
        dlog(
            run_id,
            "H_scheduler",
            "app.py:_select_appointment_slot",
            "slot_selected_first_start_radio",
            {
                "count": any_start.count(),
                "url": page.url,
                "requestedSlot": requested_slot,
                "fallbackAllowed": booking_allow_slot_fallback(),
            },
        )
        return selected

    slot_info = _click_first_available_slot(page, run_id)
    if slot_info.get("clicked"):
        selected.update(
            {
                "clicked": True,
                "strategy": "first_available_visible_slot",
                "clickedText": str(slot_info.get("text", "")),
            }
        )
        return selected

    if run_dir:
        _save_navigation_probe(page, run_dir, "fail-no-slot-controls", run_id)
    raise ValueError(
        "No interactable slot found on choose-appointment page. "
        "Use a fresh sessionCookie from the same browser session, and set currentSlot "
        "to an exact value attribute from input[name=Start] on the choose-appointment step. "
        f"See navigation-debug under artifacts. url={page.url!r} title={page.title()!r}"
    )


def _page_title_is_loading(page: Page) -> bool:
    try:
        return (page.title() or "").strip().lower().startswith("loading")
    except Exception:
        return False


def _page_has_empty_body(page: Page) -> bool:
    try:
        text = page.evaluate("() => (document.body?.innerText || '').replace(/\\s+/g, ' ').trim()")
        return len(str(text or "")) < 15
    except Exception:
        return True


def _wait_postback_settle(
    page: Page,
    run_id: str,
    stage: str,
    timeout_ms: Optional[int] = None,
) -> None:
    """
    ASP.NET often sets document.title to 'Loading …' with an empty body while
    the postback completes. domcontentloaded fires on that shell immediately,
    so callers must poll until real content appears.
    """
    budget_ms = timeout_ms if timeout_ms is not None else postback_settle_timeout_ms()
    deadline = time.monotonic() + budget_ms / 1000.0
    saw_loading = False
    while time.monotonic() < deadline:
        loading = _page_title_is_loading(page)
        if loading:
            saw_loading = True
            page.wait_for_timeout(300)
            continue
        if saw_loading and _page_has_empty_body(page):
            page.wait_for_timeout(300)
            continue
        if page.locator("form").count() > 0 or not _page_has_empty_body(page):
            if run_id:
                dlog(
                    run_id,
                    "H_nav",
                    "app.py:_wait_postback_settle",
                    "postback_ready",
                    {
                        "stage": stage,
                        "url": page.url,
                        "title": page.title(),
                        "sawLoading": saw_loading,
                    },
                )
            return
        page.wait_for_timeout(300)
    if run_id:
        dlog(
            run_id,
            "H_nav",
            "app.py:_wait_postback_settle",
            "postback_settle_timeout",
            {
                "stage": stage,
                "url": page.url,
                "title": page.title(),
                "sawLoading": saw_loading,
                "budgetMs": budget_ms,
            },
        )


def _wait_for_start_radios(
    page: Page,
    run_id: str,
    probe: Optional[Any] = None,
) -> None:
    """Wait for choose-appointment slot radios, tolerating long Loading… postbacks."""
    timeout_ms = choose_appointment_wait_ms()
    deadline = time.monotonic() + timeout_ms / 1000.0
    reload_attempts = 0
    max_reloads = postback_stuck_reload_max()
    selector = "input[type='radio'][name='Start']"

    while time.monotonic() < deadline:
        if page.locator(selector).count() > 0:
            if run_id:
                dlog(
                    run_id,
                    "H_scheduler",
                    "app.py:_wait_for_start_radios",
                    "start_radios_ready",
                    {"url": page.url, "title": page.title(), "count": page.locator(selector).count()},
                )
            return

        if _page_title_is_loading(page):
            page.wait_for_timeout(400)
            continue

        if _page_has_empty_body(page) and reload_attempts < max_reloads:
            reload_attempts += 1
            if run_id:
                dlog(
                    run_id,
                    "H_scheduler",
                    "app.py:_wait_for_start_radios",
                    "stuck_empty_shell_reload",
                    {
                        "attempt": reload_attempts,
                        "url": page.url,
                        "title": page.title(),
                    },
                )
            try:
                page.reload(wait_until="domcontentloaded", timeout=nav_dom_content_loaded_ms())
            except Exception:
                pass
            _wait_postback_settle(page, run_id, "choose_appointment_reload")
            continue

        remaining_ms = max(500, int((deadline - time.monotonic()) * 1000))
        try:
            page.wait_for_selector(selector, timeout=min(5000, remaining_ms))
            return
        except PlaywrightTimeoutError:
            continue

    if probe:
        probe("fail-wait-choose-appointment-slots")
    raise ValueError(
        "Choose-appointment step: no input[name=Start] radios after Information Next. "
        "See artifacts/<runId>/navigation-debug/"
    )


def _page_looks_stuck_loading(page: Page) -> bool:
    if _page_title_is_loading(page):
        return True
    # Blank landing shell: title may not say Loading yet but body is empty and Office missing.
    if _page_has_empty_body(page) and page.locator(_LANDING_OFFICE_SELECTOR).count() < 1:
        return True
    return False


def _open_landing_page(page: Page, run_id: str) -> None:
    """
    Load the BMEIA landing page and wait until Office/Language controls exist.
    Parallel Chromium sessions often get a blank 'Loading…' shell; retry goto a few times.
    """
    start_url = f"{APPOINTMENT_ORIGIN}/?fromSpecificInfo=True"
    max_attempts = landing_goto_max_attempts()
    last_err: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            page.goto(
                start_url,
                wait_until="domcontentloaded",
                timeout=landing_goto_timeout_ms(),
            )
            page.wait_for_selector(
                _LANDING_OFFICE_SELECTOR,
                state="visible",
                timeout=landing_ready_timeout_ms(),
            )
            if run_id:
                dlog(
                    run_id,
                    "H_nav",
                    "app.py:_open_landing_page",
                    "landing_ready",
                    {
                        "attempt": attempt,
                        "url": page.url,
                        "title": page.title(),
                        "hasOffice": _has_selector(page, "#Office"),
                    },
                )
            return
        except Exception as exc:
            last_err = exc
            if run_id:
                dlog(
                    run_id,
                    "H_nav",
                    "app.py:_open_landing_page",
                    "landing_attempt_failed",
                    {
                        "attempt": attempt,
                        "maxAttempts": max_attempts,
                        "stuckLoading": _page_looks_stuck_loading(page),
                        "url": page.url,
                        "title": page.title(),
                        "error": str(exc)[:200],
                    },
                )
            if attempt >= max_attempts:
                break
            try:
                page.wait_for_timeout(min(3000 * attempt, 8000))
            except Exception:
                pass
    raise ValueError(
        "Landing page did not load (Office selector never appeared). "
        "Your artifacts show title 'Loading…' and an empty body — typical when several "
        "browsers hit the site at once. Use BOOKING_USE_GLOBAL_LOCK=true and run one "
        "booking at a time, or retry after a few seconds. "
        f"url={page.url!r} title={page.title()!r}"
    ) from last_err


def _configure_playwright_context(context: BrowserContext) -> None:
    nav_ms = landing_goto_timeout_ms()
    context.set_default_navigation_timeout(nav_ms)
    context.set_default_timeout(nav_ms)


def _log_start_time_vs_payload(page: Page, payload: Dict[str, Any], run_id: str, stage: str) -> None:
    """Compare hidden #StartTime on capture form to payload currentSlot."""
    want = str(payload.get("currentSlot") or "").strip()
    if not want or not run_id:
        return
    got = ""
    try:
        if page.locator("#StartTime").count():
            got = (page.locator("#StartTime").first.get_attribute("value") or "").strip()
    except Exception:
        pass
    if not got:
        return
    exact = got == want
    normalized = _normalize_slot_for_compare(got) == _normalize_slot_for_compare(want)
    dlog(
        run_id,
        "H_scheduler",
        "app.py:_log_start_time_vs_payload",
        "start_time_dom_vs_payload",
        {
            "stage": stage,
            "payloadSlot": want,
            "domStartTime": got,
            "exactMatch": exact,
            "normalizedMatch": normalized,
        },
    )
    if not normalized and not booking_allow_slot_fallback():
        raise ValueError(
            f"Capture form StartTime {got!r} does not match requested currentSlot {want!r}. "
            "Session/wizard state does not match n8n slot detect — use useSchedulerPost: true "
            "or a fresh sessionCookie."
        )


def navigate_to_capture_form_via_scheduler_post(
    page: Page,
    context: BrowserContext,
    payload: Dict[str, Any],
    run_id: str,
    recorder: Optional["ArtifactRecorder"] = None,
    run_dir: Optional[Path] = None,
) -> None:
    """
    Fast path: POST Select Slot (same as n8n) then load capture HTML into the browser.
    Avoids full wizard re-walk that can show a different week when sessionCookie is reused.
    """
    html = post_select_slot(context, payload)
    if recorder and run_dir:
        recorder.save_html("scheduler_post_response", html)

    err = classify_post_submit_html(html)
    if err:
        result_type, msg = err
        raise ValueError(f"Scheduler POST: {result_type} — {msg}")

    if "BDC_VCID_Captcha" not in html or 'id="Token"' not in html:
        if run_dir:
            probe_dir = run_dir / "navigation-debug"
            probe_dir.mkdir(parents=True, exist_ok=True)
            (probe_dir / "fail-scheduler-post-no-capture.html").write_text(html[:200000], encoding="utf-8")
        raise ValueError(
            "POST /HomeWeb/Scheduler did not return the capture form (missing Token/captcha). "
            "Slot may be gone or sessionCookie is stale."
        )

    page.set_content(inject_base_href(html), wait_until="domcontentloaded")
    _wait_postback_settle(page, run_id, "scheduler_post_set_content")
    if recorder:
        recorder.screenshot("12_capture_form_scheduler_post")

    fresh_fields = parse_fresh_fields(html)
    want_slot = str(payload.get("currentSlot") or "").strip()
    if want_slot and fresh_fields.start_time:
        if _normalize_slot_for_compare(fresh_fields.start_time) != _normalize_slot_for_compare(want_slot):
            dlog(
                run_id,
                "H_scheduler",
                "app.py:navigate_to_capture_form_via_scheduler_post",
                "scheduler_post_start_time_mismatch",
                {
                    "payloadSlot": want_slot,
                    "htmlStartTime": fresh_fields.start_time,
                },
            )
            if not booking_allow_slot_fallback():
                raise ValueError(
                    f"Scheduler POST returned StartTime {fresh_fields.start_time!r} "
                    f"but currentSlot is {want_slot!r}."
                )

    dlog(
        run_id,
        "H_nav",
        "app.py:navigate_to_capture_form_via_scheduler_post",
        "scheduler_post_capture_ready",
        {
            "url": page.url,
            "startTime": fresh_fields.start_time,
            "bookingEndpoint": fresh_fields.booking_endpoint,
        },
    )
    _log_start_time_vs_payload(page, payload, run_id, "scheduler_post")


def navigate_to_booking_form(
    page: Page,
    payload: Dict[str, Any],
    recorder: Optional["ArtifactRecorder"] = None,
    run_dir: Optional[Path] = None,
) -> None:
    """
    Walk the public UI in order: landing → reservation type → person count →
    information → choose slot → capture form. Each transition waits for the
    next screen; failures write navigation-debug/* under run_dir.
    """
    run_id = str(payload.get("_runId") or payload.get("runId") or "")
    artifact_base = run_dir if run_dir is not None else (ARTIFACTS_DIR / run_id)
    try:
        artifact_base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def probe(slug: str) -> None:
        _save_navigation_probe(page, artifact_base, slug, run_id)

    _open_landing_page(page, run_id)
    if recorder:
        recorder.screenshot("00_goto_from_specific_info")

    if run_id:
        dlog(
            run_id,
            "H_nav",
            "app.py:navigate_to_booking_form",
            "after_goto_landing",
            {
                "url": page.url,
                "hasOffice": _has_selector(page, "#Office"),
                "hasRepresentationSelect": _has_selector(page, "select"),
            },
        )

    # Stage 1: landing office/language.
    landing_sets = {
        "#Office": str(payload.get("Office", "")),
        "select[name='Office']": str(payload.get("Office", "")),
        "#Language": str(payload.get("Language", "")),
        "select[name='Language']": str(payload.get("Language", "")),
    }
    set_results = {}
    for selector, value in landing_sets.items():
        applied = _set_scheduler_value(page, selector, value)
        if applied:
            set_results[selector] = True
    dlog(
        run_id,
        "H_scheduler",
        "app.py:navigate_to_booking_form",
        "landing_fields_applied",
        {"appliedCount": len(set_results), "selectors": list(set_results.keys())[:10]},
    )
    if not set_results:
        probe("fail-landing-office-language")
        raise ValueError(
            "Landing page loaded but Office/Language could not be set. "
            "Check payload Office/Language values and sessionCookie freshness. "
            f"url={page.url!r} title={page.title()!r}"
        )

    if recorder:
        recorder.screenshot("01_after_landing_setters")

    _submit_next_command(page, run_id, stage="landing_next")
    if recorder:
        recorder.screenshot("02_after_landing_next")

    # Stage 2a: reservation type (CalendarId). Screenshot 02 is *before* calendar apply;
    # use 02b/02c to verify inject + selection.
    _apply_reservation_calendar_select(page, payload, run_id, probe)
    if recorder:
        recorder.screenshot("02b_after_reservation_calendar")
    _repair_calendar_id_dom_if_needed(page, payload, run_id)
    if recorder:
        recorder.screenshot("02c_before_reservation_next")

    if recorder:
        recorder.screenshot("03_reservation_type_ready")

    _submit_next_command(page, run_id, stage="reservation_type_next")
    if recorder:
        recorder.screenshot("04_after_reservation_type_next")

    # Stage 2b: person count (1–4 documents).
    try:
        page.wait_for_selector("select[name='PersonCount']", timeout=person_count_wait_ms())
    except PlaywrightTimeoutError as exc:
        probe("fail-wait-person-count")
        raise ValueError(
            "Person-count step: select[name=PersonCount] did not appear after reservation Next. "
            "See artifacts/<runId>/navigation-debug/"
        ) from exc

    person_count = str(payload.get("PersonCount", "1")).strip() or "1"
    applied_pc = _set_scheduler_value(page, "select[name='PersonCount']", person_count) or _set_scheduler_value(
        page, "#PersonCount", person_count
    )
    if not applied_pc:
        probe("fail-set-person-count")
        raise ValueError("Could not apply PersonCount on the person-count step.")

    if recorder:
        recorder.screenshot("05_person_count_ready")

    _submit_next_command(page, run_id, stage="person_count_next")
    if recorder:
        recorder.screenshot("06_after_person_count_next")

    # Stage 3: information page (wait for it — do not advance early).
    try:
        page.wait_for_function(
            """() => {
              const t = (document.title || '').toLowerCase();
              const pc = document.querySelector("select[name='PersonCount']");
              const infoLike = t.includes('information') || t.includes('informationen')
                || t.includes('auskunft');
              return infoLike && !pc;
            }""",
            timeout=information_page_wait_ms(),
        )
    except PlaywrightTimeoutError as exc:
        probe("fail-wait-information-page")
        raise ValueError(
            "Information step not detected after person count (expect title hint and no PersonCount). "
            "See artifacts/<runId>/navigation-debug/"
        ) from exc

    if recorder:
        recorder.screenshot("07_information_page")

    _submit_next_command(page, run_id, stage="information_next")
    if recorder:
        recorder.screenshot("08_after_information_next")

    # Stage 4: choose appointment — wait for slot radios (Loading… postbacks can take 45s+).
    _wait_for_start_radios(page, run_id, probe=probe)

    dom_monday = ""
    try:
        if page.locator("#Monday").count() > 0:
            dom_monday = (page.locator("#Monday").first.get_attribute("value") or "").strip()
        elif page.locator("input[name='Monday']").count() > 0:
            dom_monday = (
                page.locator("input[name='Monday']").first.get_attribute("value") or ""
            ).strip()
    except Exception:
        dom_monday = ""
    want_mon = str(payload.get("currentMonday", "") or "").strip()
    if run_id and want_mon and dom_monday and want_mon != dom_monday:
        dlog(
            run_id,
            "H_scheduler",
            "app.py:navigate_to_booking_form",
            "monday_dom_vs_payload",
            {
                "payloadMonday": want_mon,
                "domMonday": dom_monday,
                "url": page.url,
            },
        )

    if recorder:
        recorder.screenshot("09_choose_appointment_ready")

    slot_selection = _select_appointment_slot(page, payload, run_id, run_dir=artifact_base)
    dlog(
        run_id,
        "H_scheduler",
        "app.py:navigate_to_booking_form",
        "slot_selection_result",
        slot_selection,
    )
    if recorder:
        recorder.screenshot("10_after_slot_selection")

    _submit_next_command(page, run_id, stage="choose_appointment_next")
    if recorder:
        recorder.screenshot("11_after_choose_appointment_next")

    page.wait_for_load_state("domcontentloaded", timeout=nav_dom_content_loaded_ms())
    try:
        page.wait_for_selector("#Token", timeout=form_load_timeout_ms())
    except Exception:
        pass

    if recorder:
        recorder.screenshot("12_capture_form_navigation")
    if run_id:
        dlog(
            run_id,
            "H_nav",
            "app.py:navigate_to_booking_form",
            "after_scheduler_ui_navigation",
            {
                "url": page.url,
                "hasToken": _has_selector(page, "#Token"),
                "hasBdcVcid": _has_selector(page, "#BDC_VCID_Captcha"),
                "hasCaptchaText": _has_selector(page, "#CaptchaText"),
            },
        )
    _log_start_time_vs_payload(page, payload, run_id, "full_wizard")


def extract_fresh_from_dom(page: Page, run_id: str = "") -> FreshFields:
    # Fast path: if fields already exist now, skip waits (prevents race with transient navigation)
    token_now = page.locator("#Token").count() > 0
    start_now = page.locator("#StartTime").count() > 0
    vcid_now = page.locator("#BDC_VCID_Captcha").count() > 0
    if run_id:
        dlog(
            run_id,
            "H_extract",
            "app.py:extract_fresh_from_dom",
            "pre_wait_presence",
            {
                "url": page.url,
                "tokenNow": token_now,
                "startNow": start_now,
                "vcidNow": vcid_now,
            },
        )

    # Wait only when one or more required fields are missing (single combined budget).
    if not (token_now and start_now and vcid_now):
        try:
            page.wait_for_function(
                """() => {
                  return !!(
                    document.querySelector('#Token') &&
                    document.querySelector('#StartTime') &&
                    document.querySelector('#BDC_VCID_Captcha')
                  );
                }""",
                timeout=form_load_timeout_ms(),
            )
        except PlaywrightTimeoutError as exc:
            if run_id:
                dlog(
                    run_id,
                    "H_extract",
                    "app.py:extract_fresh_from_dom",
                    "wait_timeout",
                    {
                        "url": page.url,
                        "tokenCount": page.locator("#Token").count(),
                        "startTimeCount": page.locator("#StartTime").count(),
                        "vcidCount": page.locator("#BDC_VCID_Captcha").count(),
                    },
                )
            raise ValueError("Timed out waiting for booking form fields to appear in DOM.") from exc

    # Debug: capture presence counts (no secrets)
    try:
        # Try to get runId from window.name if available, else skip
        pass
    except Exception:
        pass

    if run_id:
        dlog(
            run_id,
            "H_extract",
            "app.py:extract_fresh_from_dom",
            "selectors_ready",
            {
                "url": page.url,
                "tokenCount": page.locator("#Token").count(),
                "startTimeCount": page.locator("#StartTime").count(),
                "vcidCount": page.locator("#BDC_VCID_Captcha").count(),
                "formCount": page.locator("form").count(),
            },
        )

    # Extract the same fields we used to parse from HTML, but from the live DOM.
    def get_value(selector: str) -> str:
        loc = page.locator(selector)
        if loc.count() < 1:
            return ""
        return loc.first.get_attribute("value") or ""

    bdc_vcid = get_value("#BDC_VCID_Captcha")
    bdc_hs = get_value("#BDC_Hs_Captcha")
    bdc_sp = get_value("#BDC_SP_Captcha")
    bdc_bw = get_value("#BDC_BackWorkaround_Captcha") or "0"
    token = get_value("#Token")
    start_time = get_value("#StartTime")

    # Form action can vary (Appointment / AppointmentPassport / etc). Choose the form that has the Token field.
    form_loc = page.locator("form", has=page.locator("#Token")).first
    action = form_loc.get_attribute("action") if form_loc.count() else None
    if run_id:
        dlog(
            run_id,
            "H_extract",
            "app.py:extract_fresh_from_dom",
            "action_extracted",
            {
                "action": action or "",
                "bookingEndpointMode": "relative" if action and action.startswith("/") else ("absolute" if action else "fallback"),
            },
        )
    booking_endpoint = f"{APPOINTMENT_ORIGIN}{action}" if action and action.startswith("/") else (action or f"{APPOINTMENT_ORIGIN}/HomeWeb/Appointment")

    if not (bdc_vcid and token and start_time and booking_endpoint):
        if run_id:
            dlog(
                run_id,
                "H_extract",
                "app.py:extract_fresh_from_dom",
                "missing_fresh_fields",
                {
                    "hasVcid": bool(bdc_vcid),
                    "hasToken": bool(token),
                    "hasStartTime": bool(start_time),
                    "hasEndpoint": bool(booking_endpoint),
                },
            )
        raise ValueError("Missing fresh fields in DOM (VCID/Token/StartTime/Endpoint).")

    captcha_image_url = f"{APPOINTMENT_ORIGIN}/BotDetectCaptcha.ashx?get=image&c=Captcha&t={bdc_vcid}"
    html = _safe_page_content(page)
    return FreshFields(
        booking_endpoint=booking_endpoint,
        token=token,
        start_time=start_time,
        bdc_vcid=bdc_vcid,
        bdc_hs=bdc_hs,
        bdc_sp=bdc_sp,
        bdc_bw=bdc_bw,
        captcha_image_url=captcha_image_url,
        html=html,
    )


def _safe_slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s.strip())
    return s[:80] or "step"


def _sanitize_run_id(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(raw or "").strip())
    s = s.strip("._-") or "run"
    return s[:64]


def _run_id_seq_width() -> int:
    try:
        return max(4, int(os.getenv("RUN_ID_SEQ_WIDTH", "6")))
    except (TypeError, ValueError):
        return 6


def _max_run_id_from_artifact_dirs() -> int:
    if not ARTIFACTS_DIR.is_dir():
        return 0
    max_n = 0
    for p in ARTIFACTS_DIR.iterdir():
        if not p.is_dir():
            continue
        m = _RUN_ID_SEQ_PATTERN.match(p.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
        elif p.name.isdigit():
            max_n = max(max_n, int(p.name))
    return max_n


def _read_run_id_counter() -> int:
    if not RUN_ID_COUNTER_FILE.exists():
        return _max_run_id_from_artifact_dirs()
    try:
        return int(RUN_ID_COUNTER_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return _max_run_id_from_artifact_dirs()


def _write_run_id_counter(n: int) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    RUN_ID_COUNTER_FILE.write_text(str(n), encoding="utf-8")


def _next_sequential_run_id() -> str:
    width = _run_id_seq_width()
    with _RUN_ID_ALLOC_LOCK:
        n = _read_run_id_counter() + 1
        _write_run_id_counter(n)
        return f"run-{n:0{width}d}"


def resolve_run_id(payload: Dict[str, Any]) -> str:
    """
    Artifact folder name under artifacts/.
    - Payload runId → use as-is (sanitized), e.g. curl-test-001
    - Else RUN_ID_MODE=sequential (default) → run-000001, run-000002, …
    - Else RUN_ID_MODE=uuid → random hex (legacy)
    """
    explicit = payload.get("runId") or payload.get("_runId")
    if explicit not in (None, ""):
        return _sanitize_run_id(str(explicit))
    mode = (os.getenv("RUN_ID_MODE") or "sequential").strip().lower()
    if mode == "uuid":
        return uuid.uuid4().hex
    return _next_sequential_run_id()


class ArtifactRecorder:
    def __init__(self, run_dir: Path, page: Page):
        self.run_dir = run_dir
        self.page = page
        self.shots_dir = run_dir / "screenshots"
        self.html_dir = run_dir / "html"
        self.images_dir = run_dir / "images"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.html_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def screenshot(self, name: str) -> str:
        self._counter += 1
        filename = f"{self._counter:02d}-{_safe_slug(name)}.png"
        path = self.shots_dir / filename
        self.page.screenshot(path=str(path), full_page=True)
        return str(path)

    def save_html(self, name: str, html: str) -> str:
        self._counter += 1
        filename = f"{self._counter:02d}-{_safe_slug(name)}.html"
        path = self.html_dir / filename
        path.write_text(html, encoding="utf-8")
        return str(path)

    def save_png_bytes(self, name: str, data: bytes) -> str:
        self._counter += 1
        filename = f"{self._counter:02d}-{_safe_slug(name)}.png"
        path = self.images_dir / filename
        path.write_bytes(data)
        return str(path)

    def screenshot_element(self, selector: str, name: str) -> str:
        self._counter += 1
        filename = f"{self._counter:02d}-{_safe_slug(name)}.png"
        path = self.shots_dir / filename
        loc = self.page.locator(selector)
        if loc.count() > 0:
            loc.first.screenshot(path=str(path))
        else:
            # Fallback to full page if selector not found
            self.page.screenshot(path=str(path), full_page=True)
        return str(path)


def run_booking_flow(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_retries = payload.get("maxCaptchaRetries", payload.get("maxCaptchaAttempts", 3))
    try:
        max_attempts = int(raw_retries)
    except (TypeError, ValueError):
        max_attempts = 3
    max_attempts = max(1, min(max_attempts, 25))

    capture_artifacts = bool(payload.get("captureArtifacts", True))
    manual_review_seconds = int(payload.get("manualReviewSeconds", 0) or 0)
    manual_review_on_fail = bool(payload.get("manualReviewOnFail", True))

    run_id = resolve_run_id(payload)
    payload["_runId"] = run_id
    payload["runId"] = run_id
    dlog(
        run_id,
        "H_config",
        "app.py:run_booking_flow",
        "attempt_policy",
        {
            "maxAttempts": max_attempts,
            "manualReviewSeconds": manual_review_seconds,
            "manualReviewOnFail": manual_review_on_fail,
        },
    )
    run_dir = ARTIFACTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = None
        context = None
        page = None
        recorder = None
        video_dir = None
        artifacts: Dict[str, Any] = {"runId": run_id}
        result: Dict[str, Any] = {}

        try:
            try:
                slow_mo = max(0, int(os.getenv("PLAYWRIGHT_SLOW_MO", "80")))
            except (TypeError, ValueError):
                slow_mo = 80
            headless = playwright_headless()
            browser = p.chromium.launch(
                headless=headless,
                slow_mo=slow_mo,
                args=["--disable-dev-shm-usage", "--disable-gpu"],
            )
            dlog(
                run_id,
                "H_config",
                "app.py:run_booking_flow",
                "playwright_launch",
                {"headless": headless, "slowMo": slow_mo},
            )
            if capture_artifacts:
                video_dir = run_dir / "video"
                video_dir.mkdir(parents=True, exist_ok=True)
                context = browser.new_context(
                    ignore_https_errors=True,
                    record_video_dir=str(video_dir),
                    record_video_size={"width": 1280, "height": 720},
                )
            else:
                context = browser.new_context(ignore_https_errors=True)

            _configure_playwright_context(context)
            set_context_cookies(context, payload["sessionCookie"])
            page = context.new_page()

            # #region agent log
            def _log_submit_request(req) -> None:
                try:
                    if req.method != "POST":
                        return
                    if "/HomeWeb/AppointmentPassport" not in (req.url or ""):
                        return
                    body = req.post_data or ""
                    parsed = parse_qs(body, keep_blank_values=True)
                    captcha_sent = (parsed.get("CaptchaText") or [""])[0]
                    vcid_sent = (parsed.get("BDC_VCID_Captcha") or [""])[0]
                    hs_sent = (parsed.get("BDC_Hs_Captcha") or [""])[0]
                    token_sent = (parsed.get("Token") or [""])[0]
                    dlog(
                        run_id,
                        "H_submit_payload",
                        "app.py:run_booking_flow",
                        "appointment_post_payload_captured",
                        {
                            "url": req.url,
                            "captchaText": captcha_sent,
                            "captchaLen": len(captcha_sent),
                            "captchaIsAlnum": captcha_sent.isalnum() if captcha_sent else False,
                            "vcidTail": vcid_sent[-8:] if vcid_sent else "",
                            "hsTail": hs_sent[-8:] if hs_sent else "",
                            "tokenTail": token_sent[-12:] if token_sent else "",
                            "bdcSpTail": ((parsed.get("BDC_SP_Captcha") or [""])[0][-8:] if (parsed.get("BDC_SP_Captcha") or [""])[0] else ""),
                            "bdcBw": (parsed.get("BDC_BackWorkaround_Captcha") or [""])[0],
                            "command": (parsed.get("Command") or [""])[0],
                            "hasStartTime": "StartTime" in parsed,
                            "hasBdcSp": "BDC_SP_Captcha" in parsed,
                        },
                    )
                except Exception as exc:
                    dlog(
                        run_id,
                        "H_submit_payload",
                        "app.py:run_booking_flow",
                        "appointment_post_payload_capture_error",
                        {"error": str(exc)[:200]},
                    )

            page.on("request", _log_submit_request)
            # #endregion agent log

            recorder = ArtifactRecorder(run_dir=run_dir, page=page) if capture_artifacts else None

            # Navigate to capture form: scheduler POST (fast) or full wizard.
            try:
                if _use_scheduler_post_navigation(payload):
                    dlog(
                        run_id,
                        "H_nav",
                        "app.py:run_booking_flow",
                        "navigation_mode",
                        {"mode": "scheduler_post"},
                    )
                    navigate_to_capture_form_via_scheduler_post(
                        page, context, payload, run_id, recorder=recorder, run_dir=run_dir
                    )
                else:
                    dlog(
                        run_id,
                        "H_nav",
                        "app.py:run_booking_flow",
                        "navigation_mode",
                        {"mode": "full_wizard"},
                    )
                    navigate_to_booking_form(page, payload, recorder=recorder, run_dir=run_dir)
            except Exception:
                try:
                    _save_navigation_probe(page, run_dir, "fail-navigate-uncaught", run_id)
                except Exception:
                    pass
                raise
            dlog(run_id, "H_nav", "app.py:run_booking_flow", "after_navigation", {"url": page.url})
            if recorder:
                # If navigation fails to reach the form, this captures what we actually landed on.
                artifacts["afterPostSchedulerUrl"] = page.url
                artifacts["afterPostSchedulerHtml"] = recorder.save_html(
                    "after_post_scheduler_dom", _safe_page_content(page)
                )
                artifacts.setdefault("screenshots", []).append(recorder.screenshot("01_after_post_scheduler_dom"))

            try:
                fresh = extract_fresh_from_dom(page, run_id=run_id)
            except Exception as exc:
                dlog(
                    run_id,
                    "H_extract",
                    "app.py:run_booking_flow",
                    "extract_fresh_failed",
                    {"error": str(exc), "url": page.url},
                )
                try:
                    _save_navigation_probe(page, run_dir, "fail-extract-fresh-dom", run_id)
                except Exception:
                    pass
                raise
            dlog(
                run_id,
                "H_fill_text",
                "app.py:run_booking_flow",
                "before_fill_form_fields",
                {
                    "url": page.url,
                    "hasLastname": page.locator("#Lastname").count() > 0,
                    "hasFirstname": page.locator("#Firstname").count() > 0,
                    "hasDob": page.locator("#DateOfBirth").count() > 0,
                    "hasPassport": page.locator("#TraveldocumentNumber").count() > 0,
                },
            )

            if recorder:
                artifacts["screenshots"] = []
                artifacts["screenshots"].append(recorder.screenshot("02_form_loaded_real_url"))
                artifacts["initialUrl"] = page.url
                artifacts["initialHtml"] = recorder.save_html("initial_dom_html", fresh.html)

            fill_form_fields(page, payload)

            gemini_form_on = os.getenv("GEMINI_FILL_FORM", "true").lower() in ("1", "true", "yes")
            force_all = os.getenv("GEMINI_FILL_FORM_FORCE", "").lower() in ("1", "true", "yes")
            if gemini_form_on:
                if force_all:
                    target_ids = _collect_all_booking_field_ids(page)
                else:
                    target_ids = _collect_empty_booking_field_ids(page)
                if target_ids:
                    try:
                        patch = gemini_fill_missing_applicant_fields(
                            payload["student"], target_ids, run_id=run_id
                        )
                        for key, val in patch.items():
                            if val is None:
                                continue
                            payload["student"][str(key)] = val
                        fill_form_fields(page, payload)
                    except Exception as exc:
                        dlog(
                            run_id,
                            "H_gemini_form",
                            "app.py:run_booking_flow",
                            "gemini_form_fill_failed",
                            {"error": str(exc)[:200]},
                        )
            still_empty = _collect_empty_booking_field_ids(page)
            if run_id:
                dlog(
                    run_id,
                    "H_gemini_form",
                    "app.py:run_booking_flow",
                    "form_empty_after_all_fills",
                    {"count": len(still_empty), "ids": still_empty[:25]},
                )

            dlog(
                run_id,
                "H_fill_text",
                "app.py:run_booking_flow",
                "after_fill_form_fields",
                {
                    "lastnameLen": len(page.locator("#Lastname").first.input_value()) if page.locator("#Lastname").count() else -1,
                    "firstnameLen": len(page.locator("#Firstname").first.input_value()) if page.locator("#Firstname").count() else -1,
                    "dobLen": len(page.locator("#DateOfBirth").first.input_value()) if page.locator("#DateOfBirth").count() else -1,
                    "passportLen": len(page.locator("#TraveldocumentNumber").first.input_value()) if page.locator("#TraveldocumentNumber").count() else -1,
                },
            )
            if recorder:
                artifacts["screenshots"].append(recorder.screenshot("03_after_fill_form_fields"))

            for attempt in range(1, max_attempts + 1):
                pre_html = _safe_page_content(page)
                pre_err = classify_post_submit_html(pre_html)
                if pre_err:
                    result_type, err_msg = pre_err
                    result = {
                        "ok": False,
                        "resultType": result_type,
                        "attempt": attempt,
                        "message": err_msg,
                        "finalUrl": page.url,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                    break

                if recorder:
                    artifacts["screenshots"].append(recorder.screenshot(f"attempt_{attempt:02d}_before_captcha_fetch"))

                # Compare live DOM captcha state vs parsed fresh fields before fetching image bytes.
                dom_vcid = ""
                dom_img_src = ""
                if page.locator("#BDC_VCID_Captcha").count():
                    dom_vcid = page.locator("#BDC_VCID_Captcha").first.input_value()
                if page.locator("#Captcha_CaptchaImage").count():
                    dom_img_src = page.locator("#Captcha_CaptchaImage").first.get_attribute("src") or ""
                dlog(
                    run_id,
                    "H_captcha_sync",
                    "app.py:run_booking_flow",
                    "pre_fetch_dom_vs_fresh",
                    {
                        "attempt": attempt,
                        "freshVcidTail": fresh.bdc_vcid[-8:] if fresh.bdc_vcid else "",
                        "domVcidTail": dom_vcid[-8:] if dom_vcid else "",
                        "imgSrcTail": dom_img_src[-32:] if dom_img_src else "",
                        "freshUrlTail": fresh.captcha_image_url[-32:],
                        "vcidMatch": bool(dom_vcid) and (dom_vcid == fresh.bdc_vcid),
                    },
                )

                # Primary source: screenshot from live DOM image to avoid any transfer mismatch.
                captcha_source = "dom_screenshot"
                try:
                    site_captcha_png = b""
                    if page.locator("#Captcha_CaptchaImage").count():
                        site_captcha_png = page.locator("#Captcha_CaptchaImage").first.screenshot(type="png")
                    captcha_bytes = capture_captcha_bytes_from_dom(page)
                except Exception as dom_exc:
                    # Fallback: direct endpoint fetch using fresh VCID.
                    captcha_source = "url_fetch_fallback"
                    dlog(
                        run_id,
                        "H_captcha_source",
                        "app.py:run_booking_flow",
                        "dom_capture_failed",
                        {"attempt": attempt, "error": str(dom_exc)[:180]},
                    )
                    captcha_bytes = fetch_captcha_bytes(context, fresh.captcha_image_url, payload["sessionCookie"])
                    site_captcha_png = b""

                dlog(
                    run_id,
                    "H_captcha_fetch",
                    "app.py:run_booking_flow",
                    "captcha_image_fetched",
                    {
                        "attempt": attempt,
                        "source": captcha_source,
                        "captchaUrlTail": fresh.captcha_image_url[-32:],
                        "vcidTail": fresh.bdc_vcid[-8:] if fresh.bdc_vcid else "",
                        "bytes": len(captcha_bytes),
                    },
                )
                captcha_text = solve_captcha(
                    captcha_bytes, run_id=run_id, attempt=attempt, vcid=fresh.bdc_vcid
                )

                _fill_captcha_text_like_user(page, captcha_text)
                # #region agent log
                dom_captcha_after_fill = page.locator("#CaptchaText").first.input_value() if page.locator("#CaptchaText").count() else ""
                dom_hs_after_fill = page.locator("#BDC_Hs_Captcha").first.input_value() if page.locator("#BDC_Hs_Captcha").count() else ""
                dlog(
                    run_id,
                    "H_submit_payload",
                    "app.py:run_booking_flow",
                    "dom_before_submit",
                    {
                        "attempt": attempt,
                        "captchaText": dom_captcha_after_fill,
                        "captchaLen": len(dom_captcha_after_fill),
                        "captchaIsAlnum": dom_captcha_after_fill.isalnum() if dom_captcha_after_fill else False,
                        "vcidTail": (page.locator("#BDC_VCID_Captcha").first.input_value()[-8:] if page.locator("#BDC_VCID_Captcha").count() else ""),
                        "hsTail": dom_hs_after_fill[-8:] if dom_hs_after_fill else "",
                    },
                )
                # #endregion agent log
                # Re-check VCID after fill to see if page auto-reloaded captcha in between.
                dom_vcid_after = ""
                if page.locator("#BDC_VCID_Captcha").count():
                    dom_vcid_after = page.locator("#BDC_VCID_Captcha").first.input_value()
                dlog(
                    run_id,
                    "H_captcha_fill",
                    "app.py:run_booking_flow",
                    "captcha_filled",
                    {
                        "attempt": attempt,
                        "filledLen": len(page.locator("#CaptchaText").first.input_value()) if page.locator("#CaptchaText").count() else -1,
                        "domVcidAfterTail": dom_vcid_after[-8:] if dom_vcid_after else "",
                        "vcidStable": bool(dom_vcid_after) and (dom_vcid_after == fresh.bdc_vcid),
                    },
                )
                if recorder:
                    # Per-attempt explicit captcha artifact triplet
                    artifacts.setdefault("captchaArtifacts", [])
                    on_site_path = recorder.save_png_bytes(
                        f"attempt_{attempt:02d}_captcha_on_site",
                        site_captcha_png if site_captcha_png else captcha_bytes,
                    )
                    sent_to_gemini_path = recorder.save_png_bytes(
                        f"attempt_{attempt:02d}_captcha_sent_to_gemini",
                        captcha_bytes,
                    )
                    after_paste_path = recorder.screenshot_element(
                        "#CaptchaText",
                        f"attempt_{attempt:02d}_after_paste_on_site",
                    )
                    artifacts["captchaArtifacts"].append(
                        {
                            "attempt": attempt,
                            "onSiteCaptchaImage": on_site_path,
                            "sentToGeminiImage": sent_to_gemini_path,
                            "afterPasteScreenshot": after_paste_path,
                        }
                    )
                    artifacts["screenshots"].append(recorder.screenshot(f"attempt_{attempt:02d}_after_captcha_fill"))

                # #region agent log
                try:
                    submit_snapshot = page.evaluate(
                        """() => {
                          const form = document.querySelector('form');
                          const nextBtn = document.querySelector('#nextButton');
                          const data = {};
                          if (form) {
                            const fd = new FormData(form);
                            for (const [k, v] of fd.entries()) {
                              data[k] = String(v);
                            }
                          }
                          return {
                            hasForm: !!form,
                            command: data.Command || '',
                            captchaText: data.CaptchaText || '',
                            vcidTail: (data.BDC_VCID_Captcha || '').slice(-8),
                            hsTail: (data.BDC_Hs_Captcha || '').slice(-8),
                            spTail: (data.BDC_SP_Captcha || '').slice(-8),
                            bw: data.BDC_BackWorkaround_Captcha || '',
                            nextDisabled: !!(nextBtn && nextBtn.disabled),
                            nextValue: nextBtn ? String(nextBtn.value || '') : '',
                          };
                        }"""
                    )
                    dlog(
                        run_id,
                        "H_submit_formdata",
                        "app.py:run_booking_flow",
                        "pre_submit_formdata_snapshot",
                        {
                            "attempt": attempt,
                            "command": str(submit_snapshot.get("command", "")),
                            "captchaText": str(submit_snapshot.get("captchaText", "")),
                            "captchaLen": len(str(submit_snapshot.get("captchaText", ""))),
                            "vcidTail": str(submit_snapshot.get("vcidTail", "")),
                            "hsTail": str(submit_snapshot.get("hsTail", "")),
                            "spTail": str(submit_snapshot.get("spTail", "")),
                            "bw": str(submit_snapshot.get("bw", "")),
                            "nextDisabled": bool(submit_snapshot.get("nextDisabled", False)),
                            "nextValue": str(submit_snapshot.get("nextValue", "")),
                        },
                    )
                except Exception as exc:
                    dlog(
                        run_id,
                        "H_submit_formdata",
                        "app.py:run_booking_flow",
                        "pre_submit_formdata_snapshot_error",
                        {"attempt": attempt, "error": str(exc)[:200]},
                    )
                # #endregion agent log

                captcha_submit_path = _submit_captcha_passport_form(page, run_id, attempt)
                dlog(
                    run_id,
                    "H_submit_captcha",
                    "app.py:run_booking_flow",
                    "captcha_submit_path",
                    {"attempt": attempt, "captchaSubmitPath": captcha_submit_path},
                )
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=nav_dom_content_loaded_ms())
                except Exception:
                    pass
                _wait_load_settle(page)

                html = _safe_page_content(page)
                # #region agent log
                try:
                    has_captcha_error = "Captcha: The text from the picture does not match" in html
                    dom_vcid_resp = page.locator("#BDC_VCID_Captcha").first.input_value() if page.locator("#BDC_VCID_Captcha").count() else ""
                    dom_hs_resp = page.locator("#BDC_Hs_Captcha").first.input_value() if page.locator("#BDC_Hs_Captcha").count() else ""
                    dom_sp_resp = page.locator("#BDC_SP_Captcha").first.input_value() if page.locator("#BDC_SP_Captcha").count() else ""
                    dom_bw_resp = page.locator("#BDC_BackWorkaround_Captcha").first.input_value() if page.locator("#BDC_BackWorkaround_Captcha").count() else ""
                    dom_captcha_after_submit = page.locator("#CaptchaText").first.input_value() if page.locator("#CaptchaText").count() else ""
                    dom_img_src_resp = page.locator("#Captcha_CaptchaImage").first.get_attribute("src") if page.locator("#Captcha_CaptchaImage").count() else ""
                    summary_text = ""
                    if page.locator(".validation-summary-errors").count():
                        summary_text = page.locator(".validation-summary-errors").first.inner_text()
                    dlog(
                        run_id,
                        "H_submit_result",
                        "app.py:run_booking_flow",
                        "after_submit_response_state",
                        {
                            "attempt": attempt,
                            "url": page.url,
                            "hasCaptchaMismatchMessage": has_captcha_error,
                            "summaryTextLen": len(summary_text or ""),
                            "summaryHasCaptchaWord": ("captcha" in (summary_text or "").lower()),
                            "responseVcidTail": dom_vcid_resp[-8:] if dom_vcid_resp else "",
                            "responseHsTail": dom_hs_resp[-8:] if dom_hs_resp else "",
                            "responseSpTail": dom_sp_resp[-8:] if dom_sp_resp else "",
                            "responseBw": dom_bw_resp,
                            "responseImgSrcTail": (dom_img_src_resp or "")[-32:],
                            "captchaInputLenAfterSubmit": len(dom_captcha_after_submit),
                        },
                    )
                except Exception as exc:
                    dlog(
                        run_id,
                        "H_submit_result",
                        "app.py:run_booking_flow",
                        "after_submit_response_state_error",
                        {"attempt": attempt, "error": str(exc)[:200]},
                    )
                # #endregion agent log
                if recorder:
                    artifacts["lastResponseHtml"] = recorder.save_html(f"attempt_{attempt:02d}_response_html", html)
                    artifacts["screenshots"].append(recorder.screenshot(f"attempt_{attempt:02d}_after_submit"))

                if is_success_html(html):
                    result = {
                        "ok": True,
                        "resultType": "SUCCESS",
                        "attempt": attempt,
                        "captchaText": captcha_text,
                        "finalUrl": page.url,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                    break

                submit_err = classify_post_submit_html(html)
                if submit_err:
                    result_type, err_msg = submit_err
                    result = {
                        "ok": False,
                        "resultType": result_type,
                        "attempt": attempt,
                        "captchaText": captcha_text,
                        "message": err_msg,
                        "finalUrl": page.url,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                    break

                if "Captcha: The text from the picture does not match" in html or "BDC_VCID_Captcha" in html:
                    # Refresh state from the current live DOM/HTML.
                    fresh = extract_fresh_from_dom(page)
                    continue

                result = {
                    "ok": False,
                    "resultType": "OTHER_FAIL",
                    "attempt": attempt,
                    "captchaText": captcha_text,
                    "finalUrl": page.url,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "snippet": html[:800],
                }
                break

            if not result:
                result = {
                    "ok": False,
                    "resultType": "CAPTCHA_FAIL",
                    "attempt": max_attempts,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "message": "Captcha retries exhausted.",
                }

            should_manual_review = (
                manual_review_seconds > 0
                and page is not None
                and ((not result.get("ok", False)) if manual_review_on_fail else True)
            )
            if should_manual_review:
                try:
                    manual_data = _collect_manual_review_actions(
                        page=page,
                        run_dir=run_dir,
                        run_id=run_id,
                        wait_seconds=manual_review_seconds,
                        recorder=recorder,
                    )
                    artifacts["manualReview"] = manual_data
                except Exception as exc:
                    dlog(
                        run_id,
                        "H_manual_review",
                        "app.py:run_booking_flow",
                        "manual_review_failed",
                        {"error": str(exc)[:200]},
                    )
            # Flush video to disk before responding
            try:
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass
            finally:
                try:
                    if context:
                        context.close()
                except Exception:
                    pass
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass

            if capture_artifacts and video_dir:
                videos = sorted(str(p) for p in video_dir.glob("*.webm"))
                if videos:
                    artifacts["videos"] = videos

            return_payload = {**result, "runId": run_id}
            if capture_artifacts:
                return_payload["artifacts"] = artifacts
            return return_payload
        except Exception:
            # Best-effort shutdown on unexpected exceptions
            try:
                if page:
                    page.close()
            except Exception:
                pass
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass
            raise
