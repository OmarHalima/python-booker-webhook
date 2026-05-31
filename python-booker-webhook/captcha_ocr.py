"""
VPS OCR captcha API client (anuashok/ocr-captcha-v3 style /predict endpoints).

Used by app.py and test_ocr_captcha_api.py.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

import requests

CAPTCHA_MIN_LENGTH = 4
DEFAULT_SAMPLE_IMAGE_URL = (
    "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQAqD7xvkayl_hCJFCF-sa6To8w_6D-KXnWlA&s"
)


def normalize_captcha_text(text: str) -> str:
    """Strip non-alphanumeric and uppercase (BMEIA submit convention)."""
    return re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()


def ocr_request_headers() -> Dict[str, str]:
    """ngrok free tier requires this to skip the browser warning interstitial."""
    return {"ngrok-skip-browser-warning": "1"}


def captcha_ocr_base_url() -> str:
    return (os.getenv("CAPTCHA_OCR_BASE_URL") or "").strip().rstrip("/")


def captcha_ocr_timeout() -> int:
    try:
        return max(5, int(os.getenv("CAPTCHA_OCR_TIMEOUT", "30")))
    except (TypeError, ValueError):
        return 30


def parse_ocr_response(body: Any) -> str:
    if not isinstance(body, dict):
        raise ValueError(f"OCR response is not a JSON object: {type(body).__name__}")
    if "result" not in body:
        raise ValueError(f"OCR response missing 'result' key: {body!r}")
    return str(body["result"])


def validate_captcha_text(text: str, source: str = "OCR") -> str:
    normalized = normalize_captcha_text(text)
    if len(normalized) < CAPTCHA_MIN_LENGTH:
        raise ValueError(f"{source} returned suspicious captcha text: {normalized!r}")
    return normalized


def predict_url(base_url: str, image_url: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    url = f"{base_url}/predict_url"
    resp = requests.post(
        url,
        headers={**ocr_request_headers(), "Content-Type": "application/json"},
        json={"url": image_url},
        timeout=timeout or captcha_ocr_timeout(),
    )
    resp.raise_for_status()
    return resp.json()


def predict_file(
    base_url: str, image_bytes: bytes, filename: str = "captcha.png", timeout: Optional[int] = None
) -> Dict[str, Any]:
    url = f"{base_url}/predict"
    resp = requests.post(
        url,
        headers=ocr_request_headers(),
        files={"file": (filename, image_bytes, "image/png")},
        timeout=timeout or captcha_ocr_timeout(),
    )
    resp.raise_for_status()
    return resp.json()


def solve_captcha_bytes_with_ocr(
    image_bytes: bytes,
    base_url: Optional[str] = None,
    timeout: Optional[int] = None,
) -> str:
    root = (base_url or captcha_ocr_base_url()).strip().rstrip("/")
    if not root:
        raise ValueError("CAPTCHA_OCR_BASE_URL is missing.")
    data = predict_file(root, image_bytes, timeout=timeout)
    raw = parse_ocr_response(data)
    return validate_captcha_text(raw, source="OCR")
