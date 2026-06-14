#!/usr/bin/env python3
"""Push sandbox workflow to n8n as NEW workflow (does not update live booker)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict:
    env = {}
    path = ROOT / ".env.n8n.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    env.setdefault("N8N_API_BASE_URL", os.getenv("N8N_API_BASE_URL", ""))
    env.setdefault("N8N_API_KEY", os.getenv("N8N_API_KEY", ""))
    return env


def sanitize_settings(settings: dict | None) -> dict:
    """Public API rejects some settings keys (e.g. binaryMode) on create/update."""
    if not settings:
        return {}
    allowed = {
        "executionOrder",
        "timezone",
        "saveDataErrorExecution",
        "saveDataSuccessExecution",
        "saveManualExecutions",
        "saveExecutionProgress",
        "callerPolicy",
        "errorWorkflow",
        "timeSavedMode",
        "availableInMCP",
    }
    return {k: v for k, v in settings.items() if k in allowed}


def strip_for_create(wf: dict) -> dict:
    out = {k: v for k, v in wf.items() if k in (
        "name", "nodes", "connections", "settings", "staticData", "pinData"
    )}
    if "settings" in out:
        out["settings"] = sanitize_settings(out.get("settings"))
    return out


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    key = env["N8N_API_KEY"]
    if not base or not key:
        print("Missing N8N_API_BASE_URL or N8N_API_KEY in .env.n8n.local", file=sys.stderr)
        return 1

    wf_path = ROOT / "workflows" / "booker-lock-v1.json"
    if not wf_path.exists():
        print(f"Missing {wf_path}; run patch_booker_lock.py first", file=sys.stderr)
        return 1

    meta_path = ROOT / "docs" / "n8n-workflow-ids.json"
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    body = strip_for_create(wf)
    headers = {"X-N8N-API-KEY": key, "Content-Type": "application/json"}

    existing_id = (meta.get("booker_lock_sandbox") or {}).get("id")
    if existing_id:
        get_r = requests.get(f"{base}/api/v1/workflows/{existing_id}", headers=headers, timeout=60)
        if get_r.status_code == 404:
            existing_id = None

    if not existing_id:
        list_r = requests.get(f"{base}/api/v1/workflows", headers=headers, params={"limit": 250}, timeout=60)
        list_r.raise_for_status()
        for row in list_r.json().get("data", []):
            if row.get("name") == body["name"]:
                existing_id = row["id"]
                break

    if existing_id:
        get_active = requests.get(f"{base}/api/v1/workflows/{existing_id}", headers=headers, timeout=60)
        was_active = get_active.json().get("active") if get_active.ok else False
        if was_active:
            requests.post(f"{base}/api/v1/workflows/{existing_id}/deactivate", headers=headers, timeout=60)
        put_r = requests.put(
            f"{base}/api/v1/workflows/{existing_id}",
            headers=headers,
            json=body,
            timeout=120,
        )
        put_r.raise_for_status()
        print(f"Updated workflow {existing_id} ({body['name']})")
        wf_id = existing_id
        if was_active:
            act = requests.post(f"{base}/api/v1/workflows/{existing_id}/activate", headers=headers, timeout=60)
            if act.ok:
                print(f"Re-activated workflow {existing_id}")
    else:
        post_r = requests.post(f"{base}/api/v1/workflows", headers=headers, json=body, timeout=120)
        post_r.raise_for_status()
        wf_id = post_r.json().get("id")
        print(f"Created workflow {wf_id} ({body['name']})")

    meta_path = ROOT / "docs" / "n8n-workflow-ids.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["booker_lock_sandbox"] = {
        "id": wf_id,
        "name": body["name"],
        "webhookPath": "booker-lock-sandbox",
        "bookerPublicUrl": env.get("BOOKER_PUBLIC_URL", ""),
    }
    meta["live_booker"] = {"id": "V2giHI6bqFpWq8ff", "name": "booker", "webhookPath": "booker"}
    meta["live_watcher"] = {"id": "KTw5aLQjJhT7fpxN", "name": "Austria Kairo Appointment Watcher v3"}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Saved {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
