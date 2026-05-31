#!/usr/bin/env python3
"""Clean 3/3 turbo batch: fresh scan, 5 captcha retries, unique students, inter-slot delay."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INTER_SLOT_DELAY_SEC = 1.5
MAX_CAPTCHA_RETRIES = 5

STUDENTS = [
    {
        "Lastname": "RASHID",
        "Firstname": "MAHMOUD",
        "DateOfBirth": "8/12/1994",
        "TraveldocumentNumber": "P4444444",
        "Sex": "1",
        "Street": "10 Clean Run St",
        "Postcode": "2100",
        "City": "Copenhagen",
        "Country": "49",
        "Telephone": "4570010001",
        "Email": "mahmoud.rashid.clean1@example.com",
        "LastnameAtBirth": "RASHID",
        "NationalityAtBirth": "49",
        "CountryOfBirth": "49",
        "PlaceOfBirth": "Cairo",
        "NationalityForApplication": "49",
        "TraveldocumentDateOfIssue": "1/1/2021",
        "TraveldocumentValidUntil": "1/1/2031",
        "TraveldocumentIssuingAuthority": "49",
    },
    {
        "Lastname": "SALIM",
        "Firstname": "FATIMA",
        "DateOfBirth": "3/22/1995",
        "TraveldocumentNumber": "Q5555555",
        "Sex": "2",
        "Street": "11 Clean Run St",
        "Postcode": "2100",
        "City": "Copenhagen",
        "Country": "49",
        "Telephone": "4570020002",
        "Email": "fatima.salim.clean2@example.com",
        "LastnameAtBirth": "SALIM",
        "NationalityAtBirth": "49",
        "CountryOfBirth": "49",
        "PlaceOfBirth": "Alexandria",
        "NationalityForApplication": "49",
        "TraveldocumentDateOfIssue": "2/1/2021",
        "TraveldocumentValidUntil": "2/1/2031",
        "TraveldocumentIssuingAuthority": "49",
    },
    {
        "Lastname": "KAMAL",
        "Firstname": "YOUSSEF",
        "DateOfBirth": "11/30/1992",
        "TraveldocumentNumber": "R6666666",
        "Sex": "1",
        "Street": "12 Clean Run St",
        "Postcode": "2100",
        "City": "Copenhagen",
        "Country": "49",
        "Telephone": "4570030003",
        "Email": "youssef.kamal.clean3@example.com",
        "LastnameAtBirth": "KAMAL",
        "NationalityAtBirth": "49",
        "CountryOfBirth": "49",
        "PlaceOfBirth": "Giza",
        "NationalityForApplication": "49",
        "TraveldocumentDateOfIssue": "3/1/2021",
        "TraveldocumentValidUntil": "3/1/2031",
        "TraveldocumentIssuingAuthority": "49",
    },
]


def main() -> int:
    import os
    from app import run_turbo_booking_flow
    sys.path.insert(0, str(ROOT / "scripts"))
    from scan_slots import scan_slots

    solver = (os.getenv("CAPTCHA_SOLVER") or "ocr_with_gemini_fallback").strip()
    print(f"CAPTCHA_SOLVER={solver}", flush=True)

    t_scan = time.perf_counter()
    scan = scan_slots(run_id="slot-scan-clean-333-v2")
    scan_ms = round((time.perf_counter() - t_scan) * 1000, 1)

    if not scan.get("ok") or len(scan.get("slots") or []) < 3:
        print(json.dumps({"ok": False, "error": "Fewer than 3 slots available", "scan": scan}, indent=2))
        return 1

    slots = scan["slots"][:3]
    session = scan["sessionCookie"]
    monday = scan["currentMonday"]

    results = []
    t_batch = time.perf_counter()

    for i, (slot, student) in enumerate(zip(slots, STUDENTS), start=1):
        if i > 1:
            time.sleep(INTER_SLOT_DELAY_SEC)

        payload = {
            "runId": f"turbo-clean333-v2-{i:02d}",
            "submitMode": "browser",
            "useSchedulerPost": True,
            "sessionCookie": session,
            "Language": "en",
            "Office": scan["office"],
            "CalendarId": scan["calendarId"],
            "PersonCount": "1",
            "currentMonday": monday,
            "currentSlot": slot,
            "maxCaptchaRetries": MAX_CAPTCHA_RETRIES,
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
                "wallSec": round(wall_ms / 1000, 2),
                "timingsMs": timings,
                "artifactsDir": out.get("artifactsDir"),
            }
        )
        print(json.dumps(results[-1], indent=2), flush=True)

    batch_ms = round((time.perf_counter() - t_batch) * 1000, 1)
    successes = sum(1 for r in results if r["ok"])
    success_times = [r["wallSec"] for r in results if r["ok"]]

    summary = {
        "testRun": "turbo-clean-333-v2",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": {
            "maxCaptchaRetries": MAX_CAPTCHA_RETRIES,
            "interSlotDelaySec": INTER_SLOT_DELAY_SEC,
            "captchaSolver": solver,
            "submitMode": "browser",
        },
        "scanMs": scan_ms,
        "sessionTail": session.split("SessionId=")[-1][:16] if "SessionId=" in session else "",
        "slotsRequested": slots,
        "results": results,
        "batch": {
            "booked": successes,
            "target": 3,
            "allSuccess": successes == 3,
            "batchWallMs": batch_ms,
            "batchWallSec": round(batch_ms / 1000, 2),
            "avgSuccessSec": round(sum(success_times) / len(success_times), 2) if success_times else None,
            "totalSuccessSec": round(sum(success_times), 2) if success_times else 0,
        },
        "estimates": {
            "threeSlotsBestCaseSec": "~18–21 (scan + 3× first-attempt success)",
            "threeSlotsTypicalSec": "~22–30 (1 OCR retry somewhere)",
            "playwrightThreeSlotsSec": "~81",
        },
    }

    out_dir = ROOT / "artifacts" / "turbo-clean-333-v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== CLEAN 3/3 SUMMARY ===", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if successes == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
