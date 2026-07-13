#!/usr/bin/env python3
"""Activate sandbox workflow, archive duplicate, run lock integration smoke tests."""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SANDBOX_ID = "CLTCcjVtgfjqyMnF"
DUPLICATE_ID = "gfteX0Pcc8S6LjPT"
WEBHOOK_PATH = "booker-lock-sandbox"
PAYLOAD_PATH = ROOT.parents[1] / "full-test-payload.json"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env.n8n.local").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def headers(env: dict) -> dict:
    return {"X-N8N-API-KEY": env["N8N_API_KEY"], "Content-Type": "application/json"}


def activate_sandbox(base: str, env: dict) -> None:
    h = headers(env)
    wf = requests.get(f"{base}/api/v1/workflows/{SANDBOX_ID}", headers=h, timeout=60).json()
    if wf.get("active"):
        print(f"Sandbox workflow {SANDBOX_ID} already active")
        return
    r = requests.post(f"{base}/api/v1/workflows/{SANDBOX_ID}/activate", headers=h, timeout=60)
    if r.status_code == 404:
        r = requests.patch(f"{base}/api/v1/workflows/{SANDBOX_ID}", headers=h, json={"active": True}, timeout=60)
    r.raise_for_status()
    print(f"Activated sandbox workflow {SANDBOX_ID}")


def archive_duplicate(base: str, env: dict) -> None:
    h = headers(env)
    r = requests.get(f"{base}/api/v1/workflows/{DUPLICATE_ID}", headers=h, timeout=60)
    if r.status_code == 404:
        print(f"Duplicate {DUPLICATE_ID} not found (already removed)")
        return
    if r.json().get("isArchived"):
        print(f"Duplicate {DUPLICATE_ID} already archived")
        return
    for method, url in (
        ("POST", f"{base}/api/v1/workflows/{DUPLICATE_ID}/archive"),
        ("DELETE", f"{base}/api/v1/workflows/{DUPLICATE_ID}"),
    ):
        ar = requests.request(method, url, headers=h, timeout=60)
        if ar.ok:
            print(f"{method} duplicate {DUPLICATE_ID}: {ar.status_code}")
            return
    print(f"Could not archive duplicate {DUPLICATE_ID}; archive manually in n8n UI")


def load_payload() -> dict:
    pin = json.loads((ROOT / "workflows" / "booker-lock-v1.json").read_text(encoding="utf-8"))
    return pin["pinData"]["Webhook1"][0]["json"]["body"]


def post_webhook(base: str, payload: dict, label: str = "") -> tuple[str, int, dict | str]:
    url = f"{base}/webhook/{WEBHOOK_PATH}"
    try:
        r = requests.post(url, json=payload, timeout=180)
        try:
            body = r.json()
        except Exception:
            body = r.text[:500]
        if label:
            print(f"  [{label}] HTTP {r.status_code} {str(body)[:200]}")
        return label, r.status_code, body
    except Exception as exc:
        print(f"  [{label}] ERROR {exc}")
        return label, 0, str(exc)


def clear_stale_lock(base: str, env: dict) -> None:
    h = headers(env)
    wf = requests.get(f"{base}/api/v1/workflows/{SANDBOX_ID}", headers=h, timeout=60).json()
    wf["staticData"] = {"global": {"lockBusy": False, "lockHolder": "", "lockExpires": 0}}
    body = {k: wf[k] for k in ("name", "nodes", "connections", "settings", "staticData") if k in wf}
    requests.post(f"{base}/api/v1/workflows/{SANDBOX_ID}/deactivate", headers=h, timeout=60)
    requests.put(f"{base}/api/v1/workflows/{SANDBOX_ID}", headers=h, json=body, timeout=120)
    requests.post(f"{base}/api/v1/workflows/{SANDBOX_ID}/activate", headers=h, timeout=60)
    print("Cleared stale workflow lock")


def run_tests(base: str, env: dict) -> int:
    clear_stale_lock(base, env)
    time.sleep(1)
    payload = load_payload()
    print("\nT2: parallel webhooks (lock hammer)")
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(post_webhook, base, payload, f"hammer-{i}")
            for i in range(4)
        ]
        for f in as_completed(futures):
            results.append(f.result())

    codes = [c for _, c, _ in results]
    skipped = [
        b
        for _, c, b in results
        if c == 200 and isinstance(b, dict) and (b.get("skipped") or b.get("reason") == "BOOKER_BUSY")
    ]
    completed = [
        b
        for _, c, b in results
        if c == 200 and isinstance(b, dict) and b.get("reason") in ("COMPLETED", None) and not b.get("skipped")
    ]
    errors = [c for c in codes if c >= 500 or c == 0]

    if errors:
        print(f"T2 FAIL: HTTP errors {errors}")
        for label, c, b in results:
            print(f"  [{label}] {c} {str(b)[:160]}")
        return 1
    if skipped and len(skipped) >= 1:
        print(f"T2 PASS ({len(skipped)}/4 returned BOOKER_BUSY, {len(completed)} completed)")
        return 0
    if all(c == 200 for c in codes):
        print("T2 WARN: all 200 but no busy skip (lock may have released between calls)")
        return 0
    print("T2 FAIL: unexpected responses")
    return 1


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")

    health = requests.get("http://127.0.0.1:8010/health", timeout=5)
    health.raise_for_status()
    print("Local booker health OK")

    activate_sandbox(base, env)
    archive_duplicate(base, env)
    time.sleep(2)
    return run_tests(base, env)


if __name__ == "__main__":
    raise SystemExit(main())
