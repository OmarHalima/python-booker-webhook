#!/usr/bin/env python3
"""Reset sandbox queue state and run one clean n8n booking attempt."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_kopenhagen_e2e_test import (  # noqa: E402
    APPLICANTS_TABLE,
    OFFICE,
    SANDBOX_WORKFLOW_ID,
    WEBHOOK_PATH,
    api_headers,
    build_watcher_payload,
    load_env,
    scan_kopenhagen,
)
from watcher_30s_lock_test import clear_lock  # noqa: E402

SLOTS_TABLE = "B0yhFJlFzV7EozJz"


def update_rows(base: str, h: dict, table_id: str, column: str, value, data: dict) -> None:
    body = {
        "filter": {
            "type": "and",
            "filters": [{"columnName": column, "condition": "eq", "value": value}],
        },
        "data": data,
        "returnData": True,
    }
    r = requests.patch(
        f"{base}/api/v1/data-tables/{table_id}/rows/update",
        headers=h,
        json=body,
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"update {table_id} {column}={value}: {r.status_code} {r.text[:300]}")


def get_status(base: str, h: dict, passport: str) -> str | None:
    r = requests.get(
        f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows",
        headers=h,
        params={"limit": 50},
        timeout=60,
    )
    r.raise_for_status()
    for row in r.json().get("data") or []:
        if row.get("TraveldocumentNumber") == passport:
            return row.get("status")
    return None


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    h = api_headers(env)

    print("=== Cleanup queue ===")
    update_rows(base, h, APPLICANTS_TABLE, "TraveldocumentNumber", "SBX-PROBE-TEST2", {"status": "FAILED"})
    update_rows(
        base,
        h,
        APPLICANTS_TABLE,
        "TraveldocumentNumber",
        "SBX-KOPEN-MCP01",
        {"status": "COMPLETED", "bookedSlot": "6/19/2026 11:30:00 AM", "bookedAt": "2026-06-14T07:26:00Z"},
    )
    update_rows(base, h, SLOTS_TABLE, "status", "IN_PROGRESS", {"status": "PENDING"})
    print("Queue cleaned")

    print("\n=== Scan + webhook ===")
    scan = scan_kopenhagen()
    if not scan.get("ok"):
        print("No slots")
        return 1

    target = "SBX-KOPEN-C12AD8"
    clear_lock(base, env)
    time.sleep(1)

    payload = build_watcher_payload(scan)
    t0 = time.time()
    r = requests.post(f"{base}/webhook/{WEBHOOK_PATH}", json=payload, timeout=600)
    elapsed = time.time() - t0
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:500]

    status = get_status(base, h, target)
    report = {
        "target": target,
        "http": r.status_code,
        "elapsedSec": round(elapsed, 1),
        "webhookBody": body,
        "applicantStatus": status,
        "slots": scan.get("slots"),
    }
    out = ROOT / "artifacts" / "clean-book-attempt.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if status == "COMPLETED":
        print(f"\nSUCCESS: {target} COMPLETED")
        return 0

    # If first pending wasn't target, check any newly completed
    r2 = requests.get(f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows", headers=h, params={"limit": 50}, timeout=60)
    completed = [x for x in (r2.json().get("data") or []) if x.get("status") == "COMPLETED" and str(x.get("TraveldocumentNumber", "")).startswith("SBX")]
    if completed:
        print(f"\nSUCCESS: {completed[-1]['TraveldocumentNumber']} COMPLETED")
        return 0

    print("\nNot COMPLETED yet — retry or inspect execution")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
