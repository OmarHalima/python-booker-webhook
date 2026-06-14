#!/usr/bin/env python3
"""Push sandbox KOPENHAGEN watcher to n8n (does not touch live watcher)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
META_KEY = "watcher_kopenhagen_sandbox"
LIVE_WATCHER_ID = "KTw5aLQjJhT7fpxN"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env.n8n.local").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def sanitize_settings(settings: dict | None) -> dict:
    if not settings:
        return {}
    allowed = {
        "executionOrder", "timezone", "saveDataErrorExecution", "saveDataSuccessExecution",
        "saveManualExecutions", "saveExecutionProgress", "callerPolicy", "errorWorkflow",
        "timeSavedMode", "availableInMCP",
    }
    return {k: v for k, v in settings.items() if k in allowed}


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    key = env["N8N_API_KEY"]
    if not base or not key:
        print("Missing n8n credentials", file=sys.stderr)
        return 1

    wf_path = ROOT / "workflows" / "watcher-kopenhagen-sandbox-v1.json"
    if not wf_path.exists():
        print("Run patch_watcher_kopenhagen.py first", file=sys.stderr)
        return 1

    meta_path = ROOT / "docs" / "n8n-workflow-ids.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    body = {k: wf[k] for k in ("name", "nodes", "connections", "settings") if k in wf}
    body["settings"] = sanitize_settings(body.get("settings"))
    headers = {"X-N8N-API-KEY": key, "Content-Type": "application/json"}

    existing_id = (meta.get(META_KEY) or {}).get("id")
    if existing_id:
        get_r = requests.get(f"{base}/api/v1/workflows/{existing_id}", headers=headers, timeout=60)
        if get_r.status_code == 404:
            existing_id = None

    if not existing_id:
        for row in requests.get(f"{base}/api/v1/workflows", headers=headers, params={"limit": 250}, timeout=60).json().get("data", []):
            if row.get("name") == body["name"]:
                existing_id = row["id"]
                break

    if existing_id:
        was_active = requests.get(f"{base}/api/v1/workflows/{existing_id}", headers=headers, timeout=60).json().get("active")
        if was_active:
            requests.post(f"{base}/api/v1/workflows/{existing_id}/deactivate", headers=headers, timeout=60)
        requests.put(f"{base}/api/v1/workflows/{existing_id}", headers=headers, json=body, timeout=120).raise_for_status()
        wf_id = existing_id
        print(f"Updated watcher {wf_id}")
        if was_active:
            requests.post(f"{base}/api/v1/workflows/{wf_id}/activate", headers=headers, timeout=60)
            print(f"Re-activated watcher {wf_id}")
    else:
        post_r = requests.post(f"{base}/api/v1/workflows", headers=headers, json=body, timeout=120)
        post_r.raise_for_status()
        wf_id = post_r.json()["id"]
        print(f"Created watcher {wf_id}")

    meta[META_KEY] = {"id": wf_id, "name": body["name"], "office": "KOPENHAGEN", "calendarId": "9677702"}
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Saved {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
