#!/usr/bin/env python3
"""Loop scan + n8n sandbox booker until a student reaches COMPLETED."""
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
    OFFICE,
    SANDBOX_WORKFLOW_ID,
    WEBHOOK_PATH,
    api_headers,
    build_watcher_payload,
    clear_lock,
    load_env,
    scan_kopenhagen,
    seed_dummy_applicant,
)

MAX_ROUNDS = 8


def sanitize_settings(settings: dict | None) -> dict:
    if not settings:
        return {}
    allowed = {
        "executionOrder", "timezone", "saveDataErrorExecution", "saveDataSuccessExecution",
        "saveManualExecutions", "saveExecutionProgress", "callerPolicy", "errorWorkflow",
        "timeSavedMode", "availableInMCP",
    }
    return {k: v for k, v in settings.items() if k in allowed}


def clear_lock_safe(base: str, env: dict) -> None:
    h = api_headers(env)
    wf = requests.get(f"{base}/api/v1/workflows/{SANDBOX_WORKFLOW_ID}", headers=h, timeout=60).json()
    wf["staticData"] = {"global": {"lockBusy": False, "lockHolder": "", "lockExpires": 0}}
    body = {k: wf[k] for k in ("name", "nodes", "connections", "settings", "staticData") if k in wf}
    if "settings" in body:
        body["settings"] = sanitize_settings(body.get("settings"))
    was = wf.get("active")
    if was:
        requests.post(f"{base}/api/v1/workflows/{SANDBOX_WORKFLOW_ID}/deactivate", headers=h, timeout=60)
    requests.put(f"{base}/api/v1/workflows/{SANDBOX_WORKFLOW_ID}", headers=h, json=body, timeout=120).raise_for_status()
    if was:
        requests.post(f"{base}/api/v1/workflows/{SANDBOX_WORKFLOW_ID}/activate", headers=h, timeout=60)


def get_applicant_status(base: str, env: dict, passport: str) -> str | None:
    h = api_headers(env)
    r = requests.get(
        f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows",
        headers=h,
        params={"limit": 200},
        timeout=60,
    )
    r.raise_for_status()
    for row in r.json().get("data") or []:
        if row.get("TraveldocumentNumber") == passport:
            return row.get("status")
    return None


def latest_booker_exec(base: str, env: dict) -> dict | None:
    h = api_headers(env)
    r = requests.get(
        f"{base}/api/v1/executions",
        headers=h,
        params={"workflowId": SANDBOX_WORKFLOW_ID, "limit": 1},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json().get("data") or []
    return data[0] if data else None


def exec_last_node(base: str, env: dict, eid: str) -> str:
    h = api_headers(env)
    r = requests.get(
        f"{base}/api/v1/executions/{eid}",
        headers=h,
        params={"includeData": "true"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json().get("data", {}).get("resultData", {}).get("lastNodeExecuted") or ""


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")

    health = requests.get("http://127.0.0.1:8010/health", timeout=10)
    health.raise_for_status()
    print("Booker health OK")

    passport = f"SBX-KOPEN-{uuid.uuid4().hex[:6].upper()}"
    student = seed_dummy_applicant(env, passport_suffix=passport.split("-")[-1])
    passport = student["TraveldocumentNumber"]
    print(f"Target applicant: {passport}")

    report = {"rounds": [], "passport": passport, "office": OFFICE}

    for rnd in range(1, MAX_ROUNDS + 1):
        print(f"\n=== Round {rnd}/{MAX_ROUNDS} ===")
        scan = scan_kopenhagen()
        if not scan.get("ok") or not scan.get("slots"):
            print("No slots; wait 30s")
            report["rounds"].append({"round": rnd, "error": "no_slots"})
            time.sleep(30)
            continue

        payload = build_watcher_payload(scan)
        clear_lock_safe(base, env)
        time.sleep(1)

        t0 = time.time()
        r = requests.post(f"{base}/webhook/{WEBHOOK_PATH}", json=payload, timeout=600)
        elapsed = time.time() - t0
        try:
            body = r.json()
        except Exception:
            body = r.text[:500]

        status = get_applicant_status(base, env, passport)
        ex = latest_booker_exec(base, env)
        last_node = exec_last_node(base, env, ex["id"]) if ex else ""

        row = {
            "round": rnd,
            "http": r.status_code,
            "elapsedSec": round(elapsed, 1),
            "webhookBody": body if isinstance(body, dict) else str(body)[:200],
            "applicantStatus": status,
            "executionId": ex.get("id") if ex else None,
            "lastNode": last_node,
            "slots": scan.get("slots"),
        }
        report["rounds"].append(row)
        print(json.dumps(row, indent=2))

        if status == "COMPLETED":
            print(f"\nSUCCESS: {passport} is COMPLETED")
            out = ROOT / "artifacts" / "book-until-success.json"
            out.write_text(json.dumps(report, indent=2), encoding="utf-8")
            return 0

        time.sleep(5)

    out = ROOT / "artifacts" / "book-until-success.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFAIL after {MAX_ROUNDS} rounds — see {out}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
