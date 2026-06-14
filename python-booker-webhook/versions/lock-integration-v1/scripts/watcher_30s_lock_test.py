#!/usr/bin/env python3
"""Reset booker lock, run watcher 30s, deactivate, inspect executions."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BOOKER_ID = "CLTCcjVtgfjqyMnF"
WATCHER_ID = "8o4tnLxWbWKRgfTq"
RUN_SECONDS = 30


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env.n8n.local").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def h(env: dict) -> dict:
    return {"X-N8N-API-KEY": env["N8N_API_KEY"], "Content-Type": "application/json"}


def get_static_lock(base: str, env: dict) -> dict:
    wf = requests.get(f"{base}/api/v1/workflows/{BOOKER_ID}", headers=h(env), timeout=60).json()
    return (wf.get("staticData") or {}).get("global") or {}


def sanitize_settings(settings: dict | None) -> dict:
    if not settings:
        return {}
    allowed = {
        "executionOrder", "timezone", "saveDataErrorExecution", "saveDataSuccessExecution",
        "saveManualExecutions", "saveExecutionProgress", "callerPolicy", "errorWorkflow",
        "timeSavedMode", "availableInMCP",
    }
    return {k: v for k, v in settings.items() if k in allowed}


def clear_lock(base: str, env: dict) -> dict:
    hdrs = h(env)
    wf = requests.get(f"{base}/api/v1/workflows/{BOOKER_ID}", headers=hdrs, timeout=60).json()
    before = (wf.get("staticData") or {}).get("global") or {}
    wf["staticData"] = {"global": {"lockBusy": False, "lockHolder": "", "lockExpires": 0}}
    body = {k: wf[k] for k in ("name", "nodes", "connections", "settings", "staticData") if k in wf}
    if "settings" in body:
        body["settings"] = sanitize_settings(body.get("settings"))
    was_active = wf.get("active")
    if was_active:
        requests.post(f"{base}/api/v1/workflows/{BOOKER_ID}/deactivate", headers=hdrs, timeout=60)
    r = requests.put(f"{base}/api/v1/workflows/{BOOKER_ID}", headers=hdrs, json=body, timeout=120)
    if not r.ok:
        raise RuntimeError(f"PUT workflow failed {r.status_code}: {r.text[:300]}")
    if was_active:
        requests.post(f"{base}/api/v1/workflows/{BOOKER_ID}/activate", headers=hdrs, timeout=60)
    after = get_static_lock(base, env)
    return {"before": before, "after": after, "wasActive": was_active}


def set_workflow_active(base: str, env: dict, wf_id: str, active: bool) -> None:
    ep = "activate" if active else "deactivate"
    requests.post(f"{base}/api/v1/workflows/{wf_id}/{ep}", headers=h(env), timeout=60).raise_for_status()


def list_execs(base: str, env: dict, wf_id: str, limit: int = 50) -> list:
    r = requests.get(
        f"{base}/api/v1/executions",
        headers=h(env),
        params={"workflowId": wf_id, "limit": limit},
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("data") or []


def summarize_booker(base: str, env: dict, eid: str) -> dict:
    r = requests.get(
        f"{base}/api/v1/executions/{eid}",
        headers=h(env),
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
        "stoppedAt": ej.get("stoppedAt"),
        "lastNode": rd.get("lastNodeExecuted"),
        "error": (rd.get("error") or {}).get("message"),
    }
    for name, key in [
        ("Try Acquire Lock", "acquire"),
        ("IF Lock Acquired", None),
        ("Build Busy Response", "busy"),
        ("Unlock Lock", "unlock"),
        ("HTTP Request", "http"),
        ("Check Booking Response", "check"),
    ]:
        if name not in run or not run[name]:
            continue
        chunk = run[name][0]
        if not isinstance(chunk, list) or not chunk:
            continue
        item = chunk[0]
        j = item.get("json") or {}
        if name == "Try Acquire Lock":
            out["acquire"] = {
                "lockAcquired": j.get("lockAcquired"),
                "skipped": j.get("skipped"),
                "reason": j.get("reason"),
                "lockHolder": j.get("lockHolder"),
            }
        elif name == "Build Busy Response":
            out["busy"] = j
        elif name == "Unlock Lock":
            out["unlock"] = True
        elif name == "HTTP Request":
            out["http"] = {
                "error": item.get("error"),
                "resultType": j.get("resultType"),
                "ok": j.get("ok"),
            }
        elif name == "Check Booking Response":
            out["check"] = {"resultType": j.get("resultType"), "ok": j.get("ok"), "errorText": j.get("errorText")}
    return out


def summarize_watcher(base: str, env: dict, eid: str) -> dict:
    r = requests.get(
        f"{base}/api/v1/executions/{eid}",
        headers=h(env),
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
    if "Parse HTML for Slots" in run and run["Parse HTML for Slots"][0]:
        items = run["Parse HTML for Slots"][0]
        if items and isinstance(items, list):
            j = items[0].get("json", {})
            out["slotsFound"] = j.get("slotsFound")
            out["foundSlots"] = j.get("foundSlots")
            out["weekLabel"] = j.get("weekLabel")
    if "Send to Booker Webhook" in run and run["Send to Booker Webhook"][0]:
        items = run["Send to Booker Webhook"][0]
        if items and isinstance(items, list):
            item = items[0]
            out["bookerHttp"] = {
                "error": item.get("error"),
                "statusCode": item.get("json", {}).get("statusCode"),
                "bodyPreview": str(item.get("json", {}))[:120],
            }
    return out


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    hdrs = h(env)
    started_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    print("=== 1) Lock state BEFORE reset ===")
    before_lock = get_static_lock(base, env)
    print(json.dumps(before_lock, indent=2))

    print("\n=== 2) Reset lock ===")
    reset = clear_lock(base, env)
    print("before:", json.dumps(reset["before"], indent=2))
    print("after:", json.dumps(reset["after"], indent=2))

    print("\n=== 3) Activate watcher for 30s ===")
    set_workflow_active(base, env, WATCHER_ID, True)
    set_workflow_active(base, env, BOOKER_ID, True)
    t0 = time.time()
    while time.time() - t0 < RUN_SECONDS:
        time.sleep(5)
        lock = get_static_lock(base, env)
        print(f"  +{int(time.time()-t0)}s lock={json.dumps(lock)}")

    print("\n=== 4) Deactivate watcher ===")
    set_workflow_active(base, env, WATCHER_ID, False)
    print("Watcher deactivated (unpublished)")

    time.sleep(3)
    after_lock = get_static_lock(base, env)
    print("\n=== 5) Lock state AFTER 30s run ===")
    print(json.dumps(after_lock, indent=2))

    print("\n=== 6) Executions during window ===")
    watcher_execs = [e for e in list_execs(base, env, WATCHER_ID, 40) if (e.get("startedAt") or "") >= started_iso]
    booker_execs = [e for e in list_execs(base, env, BOOKER_ID, 40) if (e.get("startedAt") or "") >= started_iso]
    print(f"Watcher runs: {len(watcher_execs)}")
    print(f"Booker runs: {len(booker_execs)}")

    watcher_summaries = [summarize_watcher(base, env, e["id"]) for e in watcher_execs[:12]]
    booker_summaries = [summarize_booker(base, env, e["id"]) for e in booker_execs[:20]]

    busy_count = sum(1 for b in booker_summaries if b.get("busy") or b.get("lastNode") == "Respond Webhook" and b.get("acquire", {}).get("lockAcquired") is False)
    acquired = [b for b in booker_summaries if b.get("acquire", {}).get("lockAcquired") is True]
    unlocked = [b for b in booker_summaries if b.get("unlock")]

    print(f"\nBooker: acquired={len(acquired)} busy={len([b for b in booker_summaries if b.get('busy')])} unlock={len(unlocked)}")

    for b in booker_summaries[:10]:
        ac = b.get("acquire") or {}
        tag = "BUSY" if b.get("busy") else ("ACQUIRED" if ac.get("lockAcquired") else "?")
        print(
            b["id"],
            b.get("startedAt", "")[11:19],
            tag,
            b.get("lastNode"),
            ac.get("lockHolder", "")[:8] if ac.get("lockHolder") else "",
            b.get("check", {}).get("resultType") or b.get("http", {}).get("resultType") or "",
        )

    # Find when lock got stuck: first acquire without matching unlock in same execution
    stuck_at = None
    for b in sorted(booker_summaries, key=lambda x: x.get("startedAt") or ""):
        if b.get("acquire", {}).get("lockAcquired") and not b.get("unlock") and b.get("lastNode") not in (
            "Build Done Response",
            "Respond Webhook",
            "Telegram: All Done",
        ):
            if b.get("lastNode") not in ("Build Busy Response",):
                stuck_at = b
                break

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lockBefore": before_lock,
        "lockAfterReset": reset["after"],
        "lockAfter30s": after_lock,
        "watcherRuns": len(watcher_execs),
        "bookerRuns": len(booker_execs),
        "watcherSummaries": watcher_summaries,
        "bookerSummaries": booker_summaries,
        "likelyStuckAt": stuck_at,
    }
    out_path = ROOT / "artifacts" / "watcher-30s-lock-report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport -> {out_path}")

    if after_lock.get("lockBusy"):
        print("\nLOCK STILL BUSY after test — holder:", after_lock.get("lockHolder"), "expires:", after_lock.get("lockExpires"))
    else:
        print("\nLock is IDLE after test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
