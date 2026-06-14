#!/usr/bin/env python3
"""Inspect sandbox applicants, slots, and recent booker execution outcomes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
APPLICANTS = "ot4qrm5xtiRHxW12"
SLOTS = "B0yhFJlFzV7EozJz"
BOOKER_ID = "CLTCcjVtgfjqyMnF"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env.n8n.local").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def get_rows(base: str, h: dict, table_id: str, limit: int = 100) -> list:
    for path in (
        f"{base}/api/v1/data-tables/{table_id}/rows",
        f"{base}/api/v1/data-tables/{table_id}/rows?limit={limit}",
    ):
        r = requests.get(path, headers=h, params={"limit": limit}, timeout=60)
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                return data
            return data.get("data") or data.get("rows") or []
    return []


def summarize_execution(base: str, h: dict, eid: str) -> dict:
    r = requests.get(
        f"{base}/api/v1/executions/{eid}",
        headers=h,
        params={"includeData": "true"},
        timeout=120,
    )
    r.raise_for_status()
    ej = r.json()
    rd = ej.get("data", {}).get("resultData", {})
    out = {
        "id": eid,
        "status": ej.get("status"),
        "startedAt": ej.get("startedAt"),
        "lastNode": rd.get("lastNodeExecuted"),
        "error": (rd.get("error") or {}).get("message"),
    }
    run = rd.get("runData") or {}
    for name in (
        "HTTP Request",
        "Check Booking Response",
        "Result Switch",
        "Update Student → PENDING",
        "Build Busy Response",
        "Get Next PENDING Student",
    ):
        if name not in run:
            continue
        chunk = run[name]
        if not chunk or not chunk[0]:
            continue
        items = chunk[0]
        if isinstance(items, list) and items:
            j = items[0].get("json", {})
            if name == "HTTP Request":
                out["httpRequest"] = {
                    "error": items[0].get("error"),
                    "jsonKeys": list(j.keys())[:15],
                    "resultType": j.get("resultType") or j.get("body", {}).get("resultType"),
                    "ok": j.get("ok"),
                    "message": (j.get("message") or j.get("body", {}).get("message") or "")[:200],
                }
            elif name == "Check Booking Response":
                out["bookingCheck"] = {
                    "resultType": j.get("resultType"),
                    "ok": j.get("ok"),
                    "errorText": j.get("errorText"),
                }
            elif name == "Get Next PENDING Student":
                out["student"] = {
                    "passport": j.get("TraveldocumentNumber"),
                    "name": f"{j.get('Firstname')} {j.get('Lastname')}",
                    "status": j.get("status"),
                }
            elif name == "Build Busy Response":
                out["lockBusy"] = j
    return out


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    h = {"X-N8N-API-KEY": env["N8N_API_KEY"]}

    print("=== APPLICANTS (SBX-* sandbox) ===")
    applicants = get_rows(base, h, APPLICANTS, 200)
    sbx = [a for a in applicants if str(a.get("TraveldocumentNumber", "")).startswith("SBX")]
    by_status: dict[str, int] = {}
    for a in sbx:
        st = a.get("status") or "?"
        by_status[st] = by_status.get(st, 0) + 1
    print(f"Total SBX rows: {len(sbx)}")
    print("By status:", json.dumps(by_status, indent=2))
    for a in sorted(sbx, key=lambda x: str(x.get("TraveldocumentNumber", "")))[-12:]:
        print(
            f"  {a.get('TraveldocumentNumber')} | {a.get('status')} | slot={a.get('bookedSlot') or '-'} | {(a.get('Email') or '')[:40]}"
        )

    print("\n=== SLOTS BLACKLIST (KOPENHAGEN recent) ===")
    slots = get_rows(base, h, SLOTS, 200)
    kopen = [s for s in slots if str(s.get("office", "")).upper() == "KOPENHAGEN" or "6/" in str(s.get("slotValue", ""))]
    by_slot_status: dict[str, int] = {}
    for s in kopen:
        st = s.get("status") or "?"
        by_slot_status[st] = by_slot_status.get(st, 0) + 1
    print(f"KOPENHAGEN-ish rows: {len(kopen)}")
    print("By status:", json.dumps(by_slot_status, indent=2))
    for s in sorted(kopen, key=lambda x: str(x.get("slotValue", "")))[-10:]:
        print(f"  {s.get('slotValue')} | {s.get('status')} | result={s.get('result') or '-'}")

    print("\n=== RECENT BOOKER EXECUTIONS ===")
    ex_list = requests.get(
        f"{base}/api/v1/executions",
        headers=h,
        params={"workflowId": BOOKER_ID, "limit": 12},
        timeout=60,
    ).json().get("data", [])

    summaries = []
    for ex in ex_list[:8]:
        s = summarize_execution(base, h, ex["id"])
        summaries.append(s)
        busy = "BUSY" if s.get("lockBusy") else ""
        http = s.get("httpRequest") or {}
        bc = s.get("bookingCheck") or {}
        print(
            ex["id"],
            s.get("lastNode"),
            busy,
            f"http={http.get('resultType') or http.get('error') or '-'}",
            f"check={bc.get('resultType') or '-'}",
        )

    out_path = ROOT / "artifacts" / "inspect-pending-state.json"
    out_path.write_text(
        json.dumps({"applicants_sbx": sbx, "slots_kopen": kopen[-20:], "executions": summaries}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
