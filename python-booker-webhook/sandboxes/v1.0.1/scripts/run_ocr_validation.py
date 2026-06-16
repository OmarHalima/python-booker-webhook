#!/usr/bin/env python3
"""Full OCR fix validation: DOM vs URL compare + N turbo bookings."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run_step(cmd: list[str], label: str) -> tuple[int, str]:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out)
    return proc.returncode, out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--skip-turbo", action="store_true")
    args = parser.parse_args()

    py = sys.executable
    report = {
        "version": "v1.0.1",
        "startedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "steps": [],
    }
    failed = 0

    code, out = run_step(
        [py, str(SCRIPTS / "compare_captcha_fetch.py"), "--samples", str(args.samples)],
        f"Step 1: compare_captcha_fetch ({args.samples} samples)",
    )
    report["steps"].append({"name": "compare_captcha_fetch", "exitCode": code})
    if code != 0:
        failed += 1

    if not args.skip_turbo:
        code, out = run_step(
            [py, str(SCRIPTS / "run_three_turbo_bookings.py"), "--rounds", str(args.rounds)],
            f"Step 2: direct turbo ({args.rounds} rounds, reuse browser)",
        )
        report["steps"].append({"name": "run_three_turbo_bookings", "exitCode": code})
        if code != 0:
            failed += 1

    report["finishedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report["passed"] = failed == 0
    out_path = ROOT / "artifacts" / "ocr-fix-validation-report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nValidation report -> {out_path}")
    print("PASS" if failed == 0 else f"FAIL ({failed} step(s))")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
