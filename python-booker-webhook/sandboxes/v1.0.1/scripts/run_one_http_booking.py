#!/usr/bin/env python3
"""POST one turbo booking to local v1.0.1 server (port 8011)."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "python"))

from scan_slots import scan_slots  # noqa: E402
from turbo_payload import build_turbo_payload, dummy_student  # noqa: E402

BOOKER = "http://127.0.0.1:8011"


def main() -> int:
    requests.get(f"{BOOKER}/health", timeout=10).raise_for_status()
    scan = scan_slots(
        office="KOPENHAGEN",
        calendar_id="9677702",
        max_weeks=12,
        run_id=f"http-test-{uuid.uuid4().hex[:6]}",
    )
    slots = scan.get("slots") or []
    if not slots:
        print("no slots")
        return 1

    suffix = uuid.uuid4().hex[:6].upper()
    student = dummy_student(suffix)
    run_id = f"ocr-http-{suffix.lower()}"
    payload = build_turbo_payload(scan, student, slot=slots[0], run_id=run_id)

    t0 = time.perf_counter()
    r = requests.post(f"{BOOKER}/webhook/booker", json=payload, timeout=600)
    elapsed = round(time.perf_counter() - t0, 1)
    print(f"HTTP {r.status_code} in {elapsed}s")
    try:
        body = r.json()
        print(json.dumps(body, indent=2))
        return 0 if body.get("resultType") == "SUCCESS" else 1
    except Exception:
        print(r.text[:500])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
