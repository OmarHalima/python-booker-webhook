#!/usr/bin/env python3
"""Verify booker lock is released on NO_SLOTS early exit."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from watcher_30s_lock_test import clear_lock, get_static_lock, h, load_env  # noqa: E402
from load_workflow_ids import booker_id  # noqa: E402


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    host = base.split("/api")[0]
    wf_id = booker_id()
    wh = f"{host}/webhook/booker-lock-sandbox"

    clear_lock(base, env)
    print("lock before:", get_static_lock(base, env))

    payload = {
        "Office": "KOPENHAGEN",
        "CalendarId": "9677702",
        "foundSlots": [],
        "PersonCount": "1",
        "currentMonday": "6/9/2026",
    }
    r = requests.post(wh, json=payload, timeout=120)
    print("webhook:", r.status_code, r.text[:240])
    time.sleep(4)
    after = get_static_lock(base, env)
    print("lock after:", after)
    if after.get("lockBusy"):
        print("FAIL: lock still busy")
        return 1
    print("PASS: lock cleared on NO_SLOTS path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
