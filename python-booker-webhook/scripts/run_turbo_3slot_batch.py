#!/usr/bin/env python3
"""Serial 3-slot turbo test with dummy students — reports per-slot and total timing."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCAN = json.loads((ROOT / "artifacts" / "slot-scan-3test" / "scan_result.json").read_text(encoding="utf-8"))

SLOTS = SCAN["slots"][:3]
SESSION = SCAN["sessionCookie"]
MONDAY = SCAN["currentMonday"]

STUDENTS = [
    {
        "Lastname": "TESTONE",
        "Firstname": "ALICE",
        "DateOfBirth": "1/15/1994",
        "TraveldocumentNumber": "G4444444",
        "Sex": "2",
        "Street": "1 Dummy St",
        "Postcode": "2100",
        "City": "Copenhagen",
        "Country": "49",
        "Telephone": "4511111111",
        "Email": "alice.testone.dummy@example.com",
        "LastnameAtBirth": "TESTONE",
        "NationalityAtBirth": "49",
        "CountryOfBirth": "49",
        "PlaceOfBirth": "Copenhagen",
        "NationalityForApplication": "49",
        "TraveldocumentDateOfIssue": "1/1/2020",
        "TraveldocumentValidUntil": "1/1/2030",
        "TraveldocumentIssuingAuthority": "49",
    },
    {
        "Lastname": "TESTTWO",
        "Firstname": "BOB",
        "DateOfBirth": "2/20/1993",
        "TraveldocumentNumber": "H5555555",
        "Sex": "1",
        "Street": "2 Dummy St",
        "Postcode": "2100",
        "City": "Copenhagen",
        "Country": "49",
        "Telephone": "4522222222",
        "Email": "bob.testtwo.dummy@example.com",
        "LastnameAtBirth": "TESTTWO",
        "NationalityAtBirth": "49",
        "CountryOfBirth": "49",
        "PlaceOfBirth": "Aarhus",
        "NationalityForApplication": "49",
        "TraveldocumentDateOfIssue": "2/1/2020",
        "TraveldocumentValidUntil": "2/1/2030",
        "TraveldocumentIssuingAuthority": "49",
    },
    {
        "Lastname": "TESTTHREE",
        "Firstname": "CAROL",
        "DateOfBirth": "3/25/1992",
        "TraveldocumentNumber": "I6666666",
        "Sex": "2",
        "Street": "3 Dummy St",
        "Postcode": "2100",
        "City": "Copenhagen",
        "Country": "49",
        "Telephone": "4533333333",
        "Email": "carol.testthree.dummy@example.com",
        "LastnameAtBirth": "TESTTHREE",
        "NationalityAtBirth": "49",
        "CountryOfBirth": "49",
        "PlaceOfBirth": "Odense",
        "NationalityForApplication": "49",
        "TraveldocumentDateOfIssue": "3/1/2020",
        "TraveldocumentValidUntil": "3/1/2030",
        "TraveldocumentIssuingAuthority": "49",
    },
]


def main() -> int:
    from app import run_turbo_booking_flow

    results = []
    t_batch = time.perf_counter()

    for i, (slot, student) in enumerate(zip(SLOTS, STUDENTS), start=1):
        payload = {
            "runId": f"turbo-3slot-{i:02d}",
            "submitMode": "browser",
            "useSchedulerPost": True,
            "sessionCookie": SESSION,
            "Language": "en",
            "Office": SCAN["office"],
            "CalendarId": SCAN["calendarId"],
            "PersonCount": "1",
            "currentMonday": MONDAY,
            "currentSlot": slot,
            "maxCaptchaRetries": 3,
            "student": student,
        }
        t0 = time.perf_counter()
        out = run_turbo_booking_flow(payload)
        wall_ms = round((time.perf_counter() - t0) * 1000, 1)
        timings = out.get("timingsMs") or {}
        results.append(
            {
                "index": i,
                "slot": slot,
                "student": f"{student['Firstname']} {student['Lastname']} / {student['TraveldocumentNumber']}",
                "resultType": out.get("resultType"),
                "ok": out.get("ok"),
                "attempt": out.get("attempt"),
                "captchaText": out.get("captchaText"),
                "wallMs": wall_ms,
                "reportedTotalMs": timings.get("totalMs"),
                "schedulerNavMs": timings.get("schedulerNavMs"),
                "submitMs": timings.get(f"submitMs_attempt{out.get('attempt', 1)}"),
                "artifactsDir": out.get("artifactsDir"),
            }
        )
        print(json.dumps(results[-1], indent=2), flush=True)

    batch_ms = round((time.perf_counter() - t_batch) * 1000, 1)
    successes = sum(1 for r in results if r["ok"])
    wall_times = [r["wallMs"] for r in results]
    avg_ms = round(sum(wall_times) / len(wall_times), 1) if wall_times else 0

    summary = {
        "testRun": "turbo-3slot-batch",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scanMs": SCAN.get("scanMs"),
        "sessionTail": SESSION.split("SessionId=")[-1][:12] if "SessionId=" in SESSION else "",
        "slotsRequested": SLOTS,
        "results": results,
        "batch": {
            "count": len(results),
            "successes": successes,
            "failures": len(results) - successes,
            "batchWallMs": batch_ms,
            "batchWallSec": round(batch_ms / 1000, 2),
            "avgPerSlotMs": avg_ms,
            "avgPerSlotSec": round(avg_ms / 1000, 2),
            "minSlotMs": min(wall_times) if wall_times else 0,
            "maxSlotMs": max(wall_times) if wall_times else 0,
        },
        "estimates": {
            "perSlotTypicalSec": "4–11 (browser turbo, depends on OCR retries)",
            "perSlotPlaywrightSec": "~26–29 (full app.py for comparison)",
            "threeSlotsTurboSec": round(batch_ms / 1000, 2),
            "threeSlotsPlaywrightEstSec": round(27 * 3, 0),
            "speedupVsPlaywright": f"~{round((27 * 3) / (batch_ms / 1000), 1)}x" if batch_ms else "n/a",
        },
    }

    out_path = ROOT / "artifacts" / "turbo-3slot-batch" / "summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n=== BATCH SUMMARY ===")
    print(json.dumps(summary, indent=2))
    return 0 if successes == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
