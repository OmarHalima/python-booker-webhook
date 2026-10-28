"""
FastCaptcha CAPTCHA solver API client (https://fastcaptcha.org/docs).

Solves BotDetect captcha images via the FastCaptcha AI OCR API:
  POST https://fastcaptcha.org/api/v1/ocr/  (X-API-Key header, base64 JSON body)
  GET  https://fastcaptcha.org/api/v1/balance/  (free balance check)

Sandbox-only solver. Production root keeps the VPS OCR + Gemini pipeline;
this sandbox experiments with FastCaptcha as the primary captcha solver.

Env vars:
  FASTCAPTCHA_API_KEY     required
  FASTCAPTCHA_API_URL     default https://fastcaptcha.org/api/v1/ocr/
  FASTCAPTCHA_TIMEOUT     default 30
  FASTCAPTCHA_ENHANCE     default false (FastCaptcha is an AI solver, raw bytes usually work best)
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

import requests

from captcha_ocr import validate_captcha_text

FASTCAPTCHA_DEFAULT_API_URL = "https://fastcaptcha.org/api/v1/ocr/"
FASTCAPTCHA_DEFAULT_BALANCE_URL = "https://fastcaptcha.org/api/v1/balance/"


def fastcaptcha_api_key() -> str:
    return (os.getenv("FASTCAPTCHA_API_KEY") or "").strip()


def fastcaptcha_api_url() -> str:
    url = (os.getenv("FASTCAPTCHA_API_URL") or FASTCAPTCHA_DEFAULT_API_URL).strip()
    return url.rstrip("/") + "/"


def fastcaptcha_balance_url() -> str:
    url = (os.getenv("FASTCAPTCHA_BALANCE_URL") or FASTCAPTCHA_DEFAULT_BALANCE_URL).strip()
    return url.rstrip("/") + "/"


def fastcaptcha_timeout() -> int:
    try:
        return max(5, int(os.getenv("FASTCAPTCHA_TIMEOUT", "30")))
    except (TypeError, ValueError):
        return 30


def fastcaptcha_enhance_enabled() -> bool:
    return (os.getenv("FASTCAPTCHA_ENHANCE", "false") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def fastcaptcha_headers() -> Dict[str, str]:
    key = fastcaptcha_api_key()
    if not key:
        raise ValueError("FASTCAPTCHA_API_KEY is missing.")
    return {"Content-Type": "application/json", "X-API-Key": key}


def solve_captcha_with_fastcaptcha(
    image_bytes: bytes,
    *,
    case_sensitive: bool = True,
    timeout: Optional[int] = None,
) -> str:
    """POST base64 image to /api/v1/ocr/ and return the validated captcha text.

    Response shape per docs: {"success": true, "text": "XK92B", "confidence": 0.97, ...}
    """
    if fastcaptcha_enhance_enabled():
        from captcha_ocr import enhance_botdetect_image

        image_bytes = enhance_botdetect_image(image_bytes)

    payload = {
        "base64_image": "data:image/png;base64,"
        + base64.b64encode(image_bytes).decode("ascii"),
        "case_sensitive": case_sensitive,
    }
    resp = requests.post(
        fastcaptcha_api_url(),
        headers=fastcaptcha_headers(),
        json=payload,
        timeout=timeout or fastcaptcha_timeout(),
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ValueError(f"FastCaptcha returned success=false: {data!r}")
    return validate_captcha_text(data.get("text", ""), source="FastCaptcha")


def fastcaptcha_balance(timeout: Optional[int] = None) -> Dict[str, Any]:
    """GET /api/v1/balance/ — free credits check. Raises if FASTCAPTCHA_API_KEY missing."""
    resp = requests.get(
        fastcaptcha_balance_url(),
        headers=fastcaptcha_headers(),
        timeout=timeout or fastcaptcha_timeout(),
    )
    resp.raise_for_status()
    return resp.json()
