"""
Manual / integration checks for the VPS OCR captcha API.

Usage:
  set CAPTCHA_OCR_BASE_URL=https://your-ngrok-host.ngrok-free.dev
  python test_ocr_captcha_api.py
  python test_ocr_captcha_api.py path\\to\\captcha.png

Exits 0 on success, 1 on failure. Skips with message if CAPTCHA_OCR_BASE_URL is unset.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from captcha_ocr import (
    DEFAULT_SAMPLE_IMAGE_URL,
    captcha_ocr_base_url,
    normalize_captcha_text,
    parse_ocr_response,
    predict_file,
    predict_url,
    validate_captcha_text,
)

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_predict_url(base: str, sample_url: str) -> str:
    _print_section("Test A: POST /predict_url")
    data = predict_url(base, sample_url)
    print("raw JSON:", data)
    raw = parse_ocr_response(data)
    text = validate_captcha_text(raw)
    print("parsed result:", repr(text))
    return text


def test_predict_file_from_url(base: str, sample_url: str) -> str:
    _print_section("Test B: POST /predict (file upload, bytes from sample URL)")
    img = requests.get(sample_url, timeout=30).content
    print("downloaded bytes:", len(img))
    data = predict_file(base, img)
    print("raw JSON:", data)
    raw = parse_ocr_response(data)
    text = validate_captcha_text(raw)
    print("parsed result:", repr(text))
    return text


def test_predict_local_file(base: str, path: Path) -> str:
    _print_section(f"Test C: POST /predict (local file {path})")
    img = path.read_bytes()
    print("file bytes:", len(img))
    data = predict_file(base, img, filename=path.name)
    print("raw JSON:", data)
    raw = parse_ocr_response(data)
    text = validate_captcha_text(raw)
    print("parsed result:", repr(text))
    return text


def main() -> int:
    base = captcha_ocr_base_url()
    if not base:
        print("CAPTCHA_OCR_BASE_URL is not set. Set it in .env or the environment.")
        return 1

    print("CAPTCHA_OCR_BASE_URL:", base)
    sample = DEFAULT_SAMPLE_IMAGE_URL

    try:
        test_predict_url(base, sample)
        test_predict_file_from_url(base, sample)

        if len(sys.argv) > 1:
            local = Path(sys.argv[1])
            if local.is_file():
                test_predict_local_file(base, local)
            else:
                print(f"Warning: local path not found, skipping Test C: {local}")
        else:
            artifacts = Path(__file__).with_name("artifacts")
            candidates = list(artifacts.glob("**/images/*captcha*.png"))
            if candidates:
                test_predict_local_file(base, candidates[0])
            else:
                print("\n(Optional Test C skipped: pass a PNG path or add artifacts/.../images/*captcha*.png)")

        print("\nAll OCR API checks passed.")
        print("normalize_captcha_text demo:", repr(normalize_captcha_text(" ab-12 ")))
        return 0
    except Exception as exc:
        print("\nFAILED:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
