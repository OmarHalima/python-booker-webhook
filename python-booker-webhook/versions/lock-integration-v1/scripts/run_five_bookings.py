#!/usr/bin/env python3
"""Book up to 5 KOPENHAGEN appointments (fresh scan + unique applicant each round)."""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_kopenhagen_e2e_test import (  # noqa: E402
    APPLICANTS_TABLE,
    build_turbo_payload,
    load_env,
    post_sandbox_turbo,
    seed_dummy_applicant,
)
from scan_slots import scan_slots  # noqa: E402

TARGET = 5
BOOKER_LOCAL = "http://127.0.0.1:8010"


def get_status(base: str, h: dict, passport: str) -> dict | None:
    r = requests.get(
        f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows",
        headers=h,
        params={"limit": 100},
        timeout=60,
    )
    r.raise_for_status()
    for row in r.json().get("data") or []:
        if row.get("TraveldocumentNumber") == passport:
            return row
    return None


def mark_completed(base: str, h: dict, passport: str, slot: str) -> None:
    requests.patch(
        f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows/update",
        headers=h,
        json={
            "filter": {
                "type": "and",
                "filters": [
                    {"columnName": "TraveldocumentNumber", "condition": "eq", "value": passport}
                ],
            },
            "data": {
                "status": "COMPLETED",
                "bookedSlot": slot,
                "bookedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            "returnData": True,
        },
        timeout=60,
    ).raise_for_status()


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"], "Content-Type": "application/json"}
    booker_url = env.get("BOOKER_PUBLIC_URL") or BOOKER_LOCAL

    requests.get(f"{BOOKER_LOCAL}/health", timeout=10).raise_for_status()
    print(f"Booker OK — using {booker_url}")

    report = {"target": TARGET, "rounds": [], "successCount": 0}

    for rnd in range(1, TARGET + 1):
        print(f"\n=== Round {rnd}/{TARGET} ===")
        scan = scan_slots(
            office="KOPENHAGEN",
            calendar_id="9677702",
            max_weeks=12,
            run_id=f"five-book-{rnd}-{uuid.uuid4().hex[:6]}",
        )
        slots = scan.get("slots") or []
        print(f"Scan: {len(slots)} slot(s) — {slots[:3]}{'...' if len(slots) > 3 else ''}")

        if not slots:
            report["rounds"].append({"round": rnd, "error": "no_slots_available"})
            print("No slots left — stopping.")
            break

        slot = slots[0]
        suffix = uuid.uuid4().hex[:6].upper()
        student = seed_dummy_applicant(env, passport_suffix=suffix)
        passport = student["TraveldocumentNumber"]
        payload = build_turbo_payload(scan, student)
        payload["currentSlot"] = slot
        payload["maxCaptchaRetries"] = 10
        payload["runId"] = f"five-{rnd}-{suffix.lower()}"

        t0 = time.time()
        code, body = post_sandbox_turbo(booker_url, payload)
        elapsed = time.time() - t0

        row = {
            "round": rnd,
            "passport": passport,
            "slot": slot,
            "http": code,
            "elapsedSec": round(elapsed, 1),
            "resultType": body.get("resultType") if isinstance(body, dict) else str(body)[:200],
            "captchaText": body.get("captchaText") if isinstance(body, dict) else None,
            "attempt": body.get("attempt") if isinstance(body, dict) else None,
            "message": body.get("message") if isinstance(body, dict) else None,
        }

        if isinstance(body, dict) and body.get("resultType") == "SUCCESS":
            mark_completed(base, h, passport, slot)
            row["applicantStatus"] = "COMPLETED"
            report["successCount"] += 1
            print(f"SUCCESS {passport} @ {slot} (attempt {body.get('attempt')}, captcha {body.get('captchaText')})")
        else:
            st = get_status(base, h, passport)
            row["applicantStatus"] = st.get("status") if st else None
            print(f"FAIL {row['resultType']} — {row.get('message', '')[:80]}")

        report["rounds"].append(row)
        if rnd < TARGET:
            time.sleep(1)

    out = ROOT / "artifacts" / "five-bookings-direct-turbo-report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nDone: {report['successCount']}/{TARGET} booked — report -> {out}")
    return 0 if report["successCount"] >= TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
