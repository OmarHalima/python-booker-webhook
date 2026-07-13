#!/usr/bin/env python3
"""Fire 4 concurrent POST /webhook/booker requests (stress test)."""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SCAN = json.loads((ROOT / "parallel-4-scan.json").read_text(encoding="utf-8"))

SLOTS = list(SCAN["slots"])
while len(SLOTS) < 4:
    SLOTS.append(SLOTS[-1])

SESSION = SCAN["sessionCookie"]
MONDAY = SCAN["currentMonday"]
BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/webhook/booker"

STUDENTS = [
    ("PARA", "ONE", "P6140001"),
    ("PARA", "TWO", "P6140002"),
    ("PARA", "THREE", "P6140003"),
    ("PARA", "FOUR", "P6140004"),
]


def build_payload(idx: int) -> dict:
    last, first, passport = STUDENTS[idx]
    return {
        "runId": f"parallel-4-{idx + 1}",
        "useSchedulerPost": True,
        "submitMode": "browser",
        "maxCaptchaRetries": 3,
        "captureArtifacts": True,
        "sessionCookie": SESSION,
        "Language": "en",
        "Office": SCAN["office"],
        "CalendarId": SCAN["calendarId"],
        "PersonCount": "1",
        "currentMonday": MONDAY,
        "currentSlot": SLOTS[idx],
        "student": {
            "Lastname": last,
            "Firstname": first,
            "DateOfBirth": f"{idx + 1}/15/1994",
            "TraveldocumentNumber": passport,
            "Sex": "1" if idx % 2 == 0 else "2",
            "Street": f"{idx + 1} Parallel St",
            "Postcode": "2100",
            "City": "Copenhagen",
            "Country": "49",
            "Telephone": f"451499880{idx}",
            "Email": f"para{idx + 1}.test@example.com",
            "LastnameAtBirth": last,
            "NationalityAtBirth": "49",
            "CountryOfBirth": "49",
            "PlaceOfBirth": "Copenhagen",
            "NationalityForApplication": "49",
            "TraveldocumentDateOfIssue": "1/1/2020",
            "TraveldocumentValidUntil": "1/1/2030",
            "TraveldocumentIssuingAuthority": "49",
        },
    }


def fire_one(idx: int) -> dict:
    payload = build_payload(idx)
    t0 = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.post(BASE_URL, json=payload, timeout=300)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        return {
            "index": idx + 1,
            "runId": payload["runId"],
            "applicant": f"{payload['student']['Firstname']} {payload['student']['Lastname']}",
            "slot": payload["currentSlot"],
            "httpStatus": resp.status_code,
            "startedAt": started,
            "elapsedMs": elapsed_ms,
            "body": body,
        }
    except Exception as exc:
        return {
            "index": idx + 1,
            "runId": payload["runId"],
            "applicant": f"{payload['student']['Firstname']} {payload['student']['Lastname']}",
            "slot": payload["currentSlot"],
            "httpStatus": None,
            "startedAt": started,
            "elapsedMs": round((time.perf_counter() - t0) * 1000, 1),
            "error": str(exc),
        }


def main() -> int:
    print(f"Target: {BASE_URL}")
    print(f"Session tail: ...{SESSION[-20:]}")
    print(f"Slots: {SLOTS[:4]}")
    print("Launching 4 concurrent requests...\n")

    t_batch = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fire_one, i) for i in range(4)]
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: r["index"])
    batch_ms = round((time.perf_counter() - t_batch) * 1000, 1)

    summary = {
        "batchMs": batch_ms,
        "sharedSession": True,
        "slotCountAvailable": SCAN["slotCount"],
        "results": results,
    }
    out_path = ROOT / "parallel-4-results.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    for r in results:
        b = r.get("body") or {}
        detail = b.get("detail") if isinstance(b, dict) else None
        if isinstance(detail, dict):
            rt = detail.get("error", detail)
        else:
            rt = b.get("resultType") if isinstance(b, dict) else r.get("error")
        ok = b.get("ok") if isinstance(b, dict) else False
        print(
            f"#{r['index']} {r['applicant']:12} slot={r['slot'][:22]:22} "
            f"HTTP={r['httpStatus']} ok={ok} result={rt} {r['elapsedMs']}ms"
        )

    print(f"\nBatch wall time: {batch_ms}ms")
    print(f"Full JSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
