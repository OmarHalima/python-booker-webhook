#!/usr/bin/env python3
"""Run N direct turbo bookings via local HTTP (port 8011, warm browser reuse)."""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
sys.path.insert(0, str(PYTHON_DIR))
sys.path.insert(0, str(ROOT / "scripts"))

from scan_slots import scan_slots  # noqa: E402
from turbo_payload import build_turbo_payload, dummy_student  # noqa: E402

BOOKER = "http://127.0.0.1:8011"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--booker", default=BOOKER)
    args = parser.parse_args()

    requests.get(f"{args.booker}/health", timeout=10).raise_for_status()

    print("Scanning slots (once)...")
    scan = scan_slots(
        office="KOPENHAGEN",
        calendar_id="9677702",
        max_weeks=12,
        run_id=f"ocr-fix-scan-{uuid.uuid4().hex[:6]}",
    )
    slots = scan.get("slots") or []
    if len(slots) < args.rounds:
        print(f"Need {args.rounds} slots, found {len(slots)}")
        return 1

    import booker_legacy as booker

    print(f"PLAYWRIGHT_REUSE_BROWSER={booker.playwright_reuse_browser()}")
    print(f"CAPTCHA_PARALLEL_FETCH={booker.captcha_parallel_fetch_enabled()}")

    report = {
        "mode": "direct_turbo_http",
        "booker": args.booker,
        "rounds": args.rounds,
        "startedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "results": [],
        "successCount": 0,
    }

    for rnd in range(1, args.rounds + 1):
        print(f"\n=== Round {rnd}/{args.rounds} ===")
        slot = slots[rnd - 1]
        suffix = uuid.uuid4().hex[:6].upper()
        student = dummy_student(suffix)
        run_id = f"ocr-fix-{rnd}-{suffix.lower()}"
        payload = build_turbo_payload(scan, student, slot=slot, run_id=run_id)

        t0 = time.perf_counter()
        r = requests.post(f"{args.booker}/webhook/booker", json=payload, timeout=600)
        elapsed = round(time.perf_counter() - t0, 1)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"message": r.text[:200]}

        row = {
            "round": rnd,
            "runId": run_id,
            "passport": student["TraveldocumentNumber"],
            "slot": slot,
            "http": r.status_code,
            "elapsedSec": elapsed,
            "resultType": body.get("resultType"),
            "attempt": body.get("attempt"),
            "captchaText": body.get("captchaText"),
            "captchaParallelUsed": body.get("captchaParallelUsed"),
            "message": body.get("message"),
        }
        report["results"].append(row)

        if body.get("resultType") == "SUCCESS":
            report["successCount"] += 1
            print(f"SUCCESS {student['TraveldocumentNumber']} @ {slot} in {elapsed}s (attempt {body.get('attempt')})")
        else:
            print(f"FAIL {row['resultType']} — {(row.get('message') or '')[:100]}")

        if rnd < args.rounds:
            time.sleep(0.5)

    report["finishedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    out_path = ROOT / "artifacts" / f"ocr-fix-turbo-{args.rounds}round-report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nDone: {report['successCount']}/{len(report['results'])} SUCCESS — {out_path}")
    return 0 if report["successCount"] == len(report["results"]) and report["results"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

