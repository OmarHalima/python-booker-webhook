#!/usr/bin/env python3
"""Seed 5 PENDING students, arm KOPENHAGEN watcher → booker n8n path, poll until COMPLETED."""
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
sys.path.insert(0, str(ROOT / "scripts"))

from load_workflow_ids import booker_id, watcher_kopenhagen_id  # noqa: E402
from run_kopenhagen_e2e_test import APPLICANTS_TABLE, load_env, seed_dummy_applicant  # noqa: E402
from watcher_30s_lock_test import clear_lock  # noqa: E402

TARGET = 5
POLL_SEC = 8
MAX_WAIT_SEC = 600
EXEC_LIMIT = 40


def api_headers(env: dict) -> dict:
    return {"X-N8N-API-KEY": env["N8N_API_KEY"], "Content-Type": "application/json"}


def set_active(base: str, h: dict, wf_id: str, active: bool) -> None:
    ep = "activate" if active else "deactivate"
    requests.post(f"{base}/api/v1/workflows/{wf_id}/{ep}", headers=h, timeout=60).raise_for_status()


def list_rows(base: str, h: dict) -> list:
    r = requests.get(
        f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows",
        headers=h,
        params={"limit": 200},
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("data") or []


def count_completed(base: str, h: dict, passports: list[str]) -> list[dict]:
    done = []
    for row in list_rows(base, h):
        p = row.get("TraveldocumentNumber") or ""
        if p in passports and row.get("status") == "COMPLETED":
            done.append(
                {
                    "passport": p,
                    "bookedSlot": row.get("bookedSlot"),
                    "bookedAt": row.get("bookedAt"),
                }
            )
    return done


def recent_execs(base: str, h: dict, wf_id: str, since_iso: str, limit: int = EXEC_LIMIT) -> list:
    r = requests.get(
        f"{base}/api/v1/executions",
        headers=h,
        params={"workflowId": wf_id, "limit": limit},
        timeout=60,
    )
    r.raise_for_status()
    return [e for e in r.json().get("data", []) if (e.get("startedAt") or "") >= since_iso]


def exec_summary(base: str, h: dict, eid: str) -> dict:
    r = requests.get(
        f"{base}/api/v1/executions/{eid}",
        headers=h,
        params={"includeData": "true"},
        timeout=120,
    )
    r.raise_for_status()
    ej = r.json()
    rd = ej.get("data", {}).get("resultData", {})
    run = rd.get("runData") or {}
    out = {
        "id": eid,
        "status": ej.get("status"),
        "startedAt": ej.get("startedAt"),
        "lastNode": rd.get("lastNodeExecuted"),
    }
    if "Parse HTML for Slots" in run and run["Parse HTML for Slots"]:
        items = run["Parse HTML for Slots"][0]
        if items and isinstance(items, list) and items[0]:
            j = items[0].get("json") or {}
            out["slotsFound"] = j.get("slotsFound")
            out["foundSlots"] = (j.get("foundSlots") or [])[:5]
    if "Send to Booker Webhook" in run and run["Send to Booker Webhook"]:
        items = run["Send to Booker Webhook"][0]
        if items and isinstance(items, list) and items[0]:
            item = items[0]
            out["bookerHttp"] = {
                "error": item.get("error"),
                "statusCode": (item.get("json") or {}).get("statusCode"),
            }
    if "HTTP Request" in run and run["HTTP Request"]:
        for chunk in run["HTTP Request"]:
            if not isinstance(chunk, list):
                continue
            for sub in chunk:
                if isinstance(sub, dict) and (sub.get("json") or {}).get("resultType"):
                    out["turboResult"] = sub["json"].get("resultType")
                    out["turboAttempt"] = sub["json"].get("attempt")
                    break
    if "Mark Student COMPLETED" in run and run["Mark Student COMPLETED"]:
        items = run["Mark Student COMPLETED"][0]
        if items and isinstance(items, list) and items[0]:
            out["student"] = (items[0].get("json") or {}).get("TraveldocumentNumber")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed 5 PENDING, run watcher→booker n8n path")
    parser.add_argument(
        "--watch-sec",
        type=int,
        default=MAX_WAIT_SEC,
        help="How long to keep watcher active and poll (default: 600)",
    )
    args = parser.parse_args()
    watch_sec = max(15, args.watch_sec)

    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    h = api_headers(env)
    booker_wf_id = booker_id()
    watcher_wf_id = watcher_kopenhagen_id()

    requests.get("http://127.0.0.1:8010/health", timeout=10).raise_for_status()
    ngrok = env.get("BOOKER_PUBLIC_URL", "")
    if ngrok:
        try:
            requests.get(f"{ngrok.rstrip('/')}/health", timeout=15, headers={"ngrok-skip-browser-warning": "1"}).raise_for_status()
            print(f"ngrok OK: {ngrok}")
        except Exception as exc:
            print(f"WARN ngrok health failed: {exc}")

    passports: list[str] = []
    for i in range(TARGET):
        suffix = uuid.uuid4().hex[:6].upper()
        row = seed_dummy_applicant(env, passport_suffix=suffix)
        p = row["TraveldocumentNumber"]
        passports.append(p)
        print(f"Seeded [{i + 1}/{TARGET}] {p}")

    clear_lock(base, env)
    time.sleep(1)
    set_active(base, h, booker_wf_id, True)
    started_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    t0 = time.time()
    set_active(base, h, watcher_wf_id, True)
    print(f"\nWatcher {watcher_wf_id} ACTIVE (schedule ~5s) — running {watch_sec}s ...")

    report: dict = {
        "mode": "watcher_n8n",
        "target": TARGET,
        "watchSec": watch_sec,
        "passports": passports,
        "startedAt": started_iso,
        "polls": [],
        "completed": [],
    }
    seen_watcher: set[str] = set()
    seen_booker: set[str] = set()

    try:
        while time.time() - t0 < watch_sec:
            done = count_completed(base, h, passports)
            poll = {
                "elapsedSec": round(time.time() - t0, 1),
                "completedCount": len(done),
                "completed": done,
            }

            for e in recent_execs(base, h, watcher_wf_id, started_iso):
                eid = e["id"]
                if eid not in seen_watcher and e.get("status") == "success":
                    seen_watcher.add(eid)
                    try:
                        s = exec_summary(base, h, eid)
                    except Exception as exc:
                        s = {"id": eid, "status": e.get("status"), "parseError": str(exc)[:200]}
                    poll.setdefault("watcherEvents", []).append(s)
                    slots = s.get("foundSlots") or []
                    print(
                        f"  [watcher {eid[-4:]}] slots={len(slots)} "
                        f"last={s.get('lastNode', '')[:40]}"
                    )

            for e in recent_execs(base, h, booker_wf_id, started_iso):
                eid = e["id"]
                if eid not in seen_booker and e.get("status") in ("success", "error"):
                    seen_booker.add(eid)
                    try:
                        s = exec_summary(base, h, eid)
                    except Exception as exc:
                        s = {"id": eid, "status": e.get("status"), "parseError": str(exc)[:200]}
                    poll.setdefault("bookerEvents", []).append(s)
                    print(
                        f"  [booker  {eid[-4:]}] turbo={s.get('turboResult', '-')} "
                        f"last={s.get('lastNode', '')[:40]}"
                    )

            report["polls"].append(poll)
            report["completed"] = done

            if len(done) >= TARGET:
                print(f"\n{TARGET}/{TARGET} COMPLETED in {poll['elapsedSec']}s")
                break

            time.sleep(POLL_SEC)
        else:
            print(f"\nWatch window ended ({watch_sec}s) — {len(report['completed'])}/{TARGET} COMPLETED")
    finally:
        set_active(base, h, watcher_wf_id, False)
        print("Watcher deactivated")

    report["elapsedSec"] = round(time.time() - t0, 1)
    report["successCount"] = len(report["completed"])
    out_path = ROOT / "artifacts" / f"five-watcher-bookings-{watch_sec}s-report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report -> {out_path}")
    return 0 if report["successCount"] >= TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
