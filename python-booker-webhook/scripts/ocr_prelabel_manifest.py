#!/usr/bin/env python3
"""Batch OCR pre-label for harvested captcha images.

Runs the VPS/local OCR service on each PNG in manifest.csv and writes ocr_guess.
Does not set verified=true — use review_captcha_labels.py for human QC.

Usage:
  set CAPTCHA_OCR_BASE_URL=http://127.0.0.1:8001
  python scripts/ocr_prelabel_manifest.py
  python scripts/ocr_prelabel_manifest.py --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from dotenv import load_dotenv

from captcha_ocr import captcha_ocr_base_url, parse_ocr_response, predict_file, validate_captcha_text
from captcha_dataset_lib import (
    default_dataset_dir,
    load_manifest,
    manifest_path,
    save_manifest,
)

load_dotenv(dotenv_path=ROOT / ".env")


def prelabel(
    *,
    dataset_dir: Path,
    force: bool,
    enhance: bool,
    timeout: int,
) -> dict:
    if enhance:
        os.environ["CAPTCHA_OCR_ENHANCE"] = "true"
    else:
        os.environ["CAPTCHA_OCR_ENHANCE"] = "false"

    base = captcha_ocr_base_url()
    if not base:
        raise ValueError("CAPTCHA_OCR_BASE_URL is not set.")

    manifest = manifest_path(dataset_dir)
    rows = load_manifest(manifest)
    if not rows:
        raise ValueError(f"No manifest rows at {manifest}. Run harvest_captcha_images.py first.")

    labeled = 0
    skipped = 0
    failed = 0
    t0 = time.perf_counter()

    for row in rows:
        if row.get("ocr_guess") and not force:
            skipped += 1
            continue

        img_path = dataset_dir / row["path"]
        if not img_path.is_file():
            row["ocr_guess"] = ""
            failed += 1
            continue

        img_bytes = img_path.read_bytes()
        filename = row.get("filename") or img_path.name
        try:
            if enhance:
                from captcha_ocr import enhance_botdetect_image

                img_bytes = enhance_botdetect_image(img_bytes)
            data = predict_file(base, img_bytes, filename=filename, timeout=timeout)
            raw = parse_ocr_response(data)
            guess = validate_captcha_text(raw, source="OCR")
            row["ocr_guess"] = guess
            labeled += 1
        except Exception as exc:
            row["ocr_guess"] = ""
            failed += 1
            print(f"OCR fail {filename}: {exc}", file=sys.stderr)

    save_manifest(manifest, rows)
    summary = {
        "ok": labeled > 0 or skipped > 0,
        "ocrBaseUrl": base,
        "enhance": enhance,
        "labeled": labeled,
        "skipped": skipped,
        "failed": failed,
        "manifest": str(manifest),
        "prelabelMs": round((time.perf_counter() - t0) * 1000, 1),
    }
    (dataset_dir / "prelabel_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR pre-label captcha dataset manifest.")
    parser.add_argument("--dataset-dir", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="Re-run OCR even if ocr_guess exists")
    parser.add_argument(
        "--enhance",
        action="store_true",
        help="Apply enhance_botdetect_image (default: raw bytes, matches CAPTCHA_OCR_ENHANCE=false)",
    )
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    dataset_dir = (args.dataset_dir or default_dataset_dir(ROOT)).resolve()
    try:
        summary = prelabel(
            dataset_dir=dataset_dir,
            force=args.force,
            enhance=args.enhance,
            timeout=max(5, args.timeout),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
