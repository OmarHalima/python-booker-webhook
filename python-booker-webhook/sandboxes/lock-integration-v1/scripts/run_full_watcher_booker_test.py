#!/usr/bin/env python3
"""Full test: seed dummy student, run KOPENHAGEN watcher, poll booker execution."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
APPLICANTS_TABLE = "ot4qrm5xtiRHxW12"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env.n8n.local").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def load_meta() -> dict:
    p = ROOT / "docs" / "n8n-workflow-ids.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def headers(env: dict) -> dict:
    return {"X-N8N-API-KEY": env["N8N_API_KEY"], "Content-Type": "application/json"}


def clear_booker_lock(base: str, env: dict, booker_id: str) -> None:
    h = headers(env)
    wf = requests.get(f"{base}/api/v1/workflows/{booker_id}", headers=h, timeout=60).json()
    wf["staticData"] = {"global": {"lockBusy": False, "lockHolder": "", "lockExpires": 0}}
    body = {k: wf[k] for k in ("name", "nodes", "connections", "settings", "staticData") if k in wf}
    requests.post(f"{base}/api/v1/workflows/{booker_id}/deactivate", headers=h, timeout=60)
    requests.put(f"{base}/api/v1/workflows/{booker_id}", headers=h, json=body, timeout=120)
    requests.post(f"{base}/api/v1/workflows/{booker_id}/activate", headers=h, timeout=60)


def seed_student(env: dict) -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    from run_kopenhagen_e2e_test import seed_dummy_applicant

    suffix = __import__("uuid").uuid4().hex[:6].upper()
    return seed_dummy_applicant(env, passport_suffix=suffix)


def deactivate_workflow(base: str, env: dict, wf_id: str) -> None:
    h = headers(env)
    requests.post(f"{base}/api/v1/workflows/{wf_id}/deactivate", headers=h, timeout=60)


def activate_workflow(base: str, env: dict, wf_id: str) -> None:
    h = headers(env)
    wf = requests.get(f"{base}/api/v1/workflows/{wf_id}", headers=h, timeout=60).json()
    if not wf.get("active"):
        requests.post(f"{base}/api/v1/workflows/{wf_id}/activate", headers=h, timeout=60).raise_for_status()


def run_watcher(base: str, env: dict, watcher_id: str) -> str | None:
    h = headers(env)
    for endpoint, payload in (
        (f"{base}/api/v1/workflows/{watcher_id}/run", {}),
        (f"{base}/api/v1/executions", {"workflowId": watcher_id}),
    ):
        r = requests.post(endpoint, headers=h, json=payload, timeout=60)
        if r.status_code in (200, 201):
            data = r.json()
            eid = data.get("executionId") or data.get("id") or (data.get("data") or [{}])[0].get("id")
            if eid:
                return str(eid)
    return None


def wait_execution(base: str, env: dict, execution_id: str, timeout_sec: int = 360) -> dict:
    h = headers(env)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = requests.get(f"{base}/api/v1/executions/{execution_id}", headers=h, timeout=60)
        r.raise_for_status()
        ej = r.json()
        if ej.get("finished") or ej.get("status") in ("success", "error", "crashed", "canceled"):
            return ej
        time.sleep(5)
    return {"status": "timeout", "id": execution_id}


def latest_execution_for(base: str, env: dict, workflow_id: str, after_iso: str) -> dict | None:
    h = headers(env)
    r = requests.get(
        f"{base}/api/v1/executions",
        headers=h,
        params={"workflowId": workflow_id, "limit": 5},
        timeout=60,
    )
    r.raise_for_status()
    for row in r.json().get("data", []):
        if row.get("startedAt", "") >= after_iso:
            return row
    return None


def summarize_execution(base: str, env: dict, execution_id: str) -> dict:
    h = headers(env)
    r = requests.get(
        f"{base}/api/v1/executions/{execution_id}",
        headers=h,
        params={"includeData": "true"},
        timeout=120,
    )
    r.raise_for_status()
    ej = r.json()
    rd = ej.get("data", {}).get("resultData", {})
    last = rd.get("lastNodeExecuted")
    err = (rd.get("error") or {}).get("message")
    nodes = list((rd.get("runData") or {}).keys())
    return {"id": execution_id, "status": ej.get("status"), "lastNode": last, "error": err, "nodes": nodes}


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    meta = load_meta()
    watcher_id = (meta.get("watcher_kopenhagen_sandbox") or {}).get("id")
    booker_id = (meta.get("booker_lock_sandbox") or {}).get("id", "CLTCcjVtgfjqyMnF")
    if not watcher_id:
        print("Missing watcher_kopenhagen_sandbox id — run patch + push watcher first", file=sys.stderr)
        return 1

    requests.get("http://127.0.0.1:8010/health", timeout=10).raise_for_status()
    print("Booker health OK")

    student = seed_student(env)
    print(f"Seeded student {student['TraveldocumentNumber']}")
    deactivate_workflow(base, env, watcher_id)
    clear_booker_lock(base, env, booker_id)
    activate_workflow(base, env, booker_id)

    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    time.sleep(2)
    activate_workflow(base, env, watcher_id)
    print(f"Watcher armed for one tick {watcher_id} ...")
    exec_id = None
    for _ in range(12):
        time.sleep(1)
        row = latest_execution_for(base, env, watcher_id, started_at)
        if row:
            exec_id = row["id"]
            deactivate_workflow(base, env, watcher_id)
            print(f"Watcher execution {exec_id} started; watcher deactivated")
            break
    if not exec_id:
        deactivate_workflow(base, env, watcher_id)
        print("No watcher execution within 12s")
        return 1

    watcher_ex = wait_execution(base, env, exec_id, timeout_sec=120)
    watcher_summary = summarize_execution(base, env, exec_id)
    print("Watcher:", json.dumps(watcher_summary, indent=2))

    print("Waiting for booker execution ...")
    booker_ex = None
    for _ in range(72):
        row = latest_execution_for(base, env, booker_id, started_at)
        if row:
            booker_ex = wait_execution(base, env, row["id"], timeout_sec=300)
            break
        time.sleep(5)

    booker_summary = summarize_execution(base, env, booker_ex["id"]) if booker_ex else {"status": "not_found"}
    print("Booker:", json.dumps(booker_summary, indent=2))

    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "studentPassport": student["TraveldocumentNumber"],
        "watcher": watcher_summary,
        "booker": booker_summary,
    }
    out_path = ROOT / "artifacts" / "full-watcher-booker-summary.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Summary -> {out_path}")

    ok = watcher_summary.get("lastNode") not in (None, "Log: No Slots Found") and booker_summary.get("status") == "success"
    busy = isinstance(booker_summary.get("nodes"), list) and "Build Busy Response" in booker_summary.get("nodes", [])
    if booker_summary.get("lastNode") in ("Telegram: Booked!", "Mark Student COMPLETED", "Respond Webhook") and not busy:
        print("PASS: full watcher->booker path completed with booking attempt")
        return 0
    if booker_summary.get("status") == "success":
        print("PARTIAL: booker ran; check lastNode for SUCCESS vs SLOT_TAKEN")
        return 0
    return 1 if not ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
