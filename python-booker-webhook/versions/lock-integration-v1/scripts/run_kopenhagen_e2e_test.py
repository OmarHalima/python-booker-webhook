#!/usr/bin/env python3
"""Seed KOPENHAGEN dummy applicant + scan slots + run sandbox booker E2E test."""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
FIXTURES = ROOT / "fixtures"
APPLICANTS_TABLE = "ot4qrm5xtiRHxW12"
PROJECT_ID = "u6GSSZJOn8Ti3yOW"
SANDBOX_WORKFLOW_ID = "CLTCcjVtgfjqyMnF"
WEBHOOK_PATH = "booker-lock-sandbox"
OFFICE = "KOPENHAGEN"
CALENDAR_ID = "9677702"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env.n8n.local").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def api_headers(env: dict) -> dict:
    return {"X-N8N-API-KEY": env["N8N_API_KEY"], "Content-Type": "application/json"}


def clear_lock(base: str, env: dict) -> None:
    h = api_headers(env)
    wf = requests.get(f"{base}/api/v1/workflows/{SANDBOX_WORKFLOW_ID}", headers=h, timeout=60).json()
    wf["staticData"] = {"global": {"lockBusy": False, "lockHolder": "", "lockExpires": 0}}
    body = {k: wf[k] for k in ("name", "nodes", "connections", "settings", "staticData") if k in wf}
    requests.post(f"{base}/api/v1/workflows/{SANDBOX_WORKFLOW_ID}/deactivate", headers=h, timeout=60)
    requests.put(f"{base}/api/v1/workflows/{SANDBOX_WORKFLOW_ID}", headers=h, json=body, timeout=120)
    requests.post(f"{base}/api/v1/workflows/{SANDBOX_WORKFLOW_ID}/activate", headers=h, timeout=60)


def scan_kopenhagen() -> dict:
    sys.path.insert(0, str(PYTHON_DIR))
    sys.path.insert(0, str(ROOT / "scripts"))
    from scan_slots import scan_slots  # type: ignore

    run_id = f"kopen-scan-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    result = scan_slots(
        office=OFFICE,
        calendar_id=CALENDAR_ID,
        language="en",
        person_count="1",
        max_weeks=4,
        run_id=run_id,
    )
    out_path = ROOT / "artifacts" / f"{run_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Scan saved {out_path}")
    return result


def seed_dummy_applicant(env: dict, passport_suffix: str | None = None) -> dict:
    row = json.loads((FIXTURES / "kopenhagen-dummy-applicant.json").read_text(encoding="utf-8"))
    suffix = passport_suffix or uuid.uuid4().hex[:6].upper()
    try:
        seed_num = int(suffix[:4], 16)
    except ValueError:
        seed_num = sum(ord(c) for c in suffix)
    row["TraveldocumentNumber"] = f"SBX-KOPEN-{suffix}"
    row["Email"] = f"sandbox.kopen.{suffix.lower()}@example.com"
    row["Telephone"] = f"2012345{suffix[:5].ljust(5, '0')}"[:12]
    row["Firstname"] = f"KOPEN{suffix[:4]}"
    row["Lastname"] = f"SANDBOX{suffix[-2:]}"
    row["DateOfBirth"] = f"{3 + (seed_num % 9)}/15/199{6 + ((seed_num >> 4) % 3)}"
    row["PlaceOfBirth"] = f"City{suffix[:3]}"
    row["LastnameAtBirth"] = row["Lastname"]

    base = env["N8N_API_BASE_URL"].rstrip("/")
    h = api_headers(env)
    url = f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows"
    r = requests.post(url, headers=h, json={"data": [row]}, timeout=60)
    r.raise_for_status()
    print(f"Seeded applicant {row['Firstname']} {row['Lastname']} passport={row['TraveldocumentNumber']}")
    return row


def build_watcher_payload(scan: dict) -> dict:
    slots = scan.get("slots") or []
    if not slots:
        raise RuntimeError("No KOPENHAGEN slots found — cannot run E2E")
    monday = scan.get("currentMonday") or ""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "Office": OFFICE,
        "CalendarId": CALENDAR_ID,
        "PersonCount": "1",
        "Language": "en",
        "MaxWeeksToScan": 12,
        "sessionCookie": scan.get("sessionCookie", ""),
        "currentMonday": monday,
        "weekIndex": 1,
        "slotsFound": True,
        "foundSlots": slots,
        "done": True,
        "weekLabel": monday,
        "hasNextWeek": True,
        "hasError": False,
        "token": "",
        "firstSlot": slots[0],
        "detectedAt": now,
        "startTime": slots[0],
        "notificationMessage": f"Sandbox test — {len(slots)} slot(s) at {OFFICE}",
        "telegramMessage": f"Sandbox: {len(slots)} slot(s) {OFFICE}",
        "bookingFormReady": False,
    }


def build_turbo_payload(scan: dict, student: dict) -> dict:
    slots = scan.get("slots") or []
    return {
        "runId": f"sandbox-kopen-{uuid.uuid4().hex[:8]}",
        "useSchedulerPost": True,
        "submitMode": "browser",
        "maxCaptchaRetries": 10,
        "captureArtifacts": True,
        "sessionCookie": scan.get("sessionCookie", ""),
        "Language": "en",
        "Office": OFFICE,
        "CalendarId": CALENDAR_ID,
        "PersonCount": "1",
        "currentMonday": scan.get("currentMonday", ""),
        "currentSlot": slots[0],
        "student": {
            "Lastname": student["Lastname"],
            "Firstname": student["Firstname"],
            "DateOfBirth": student["DateOfBirth"],
            "TraveldocumentNumber": student["TraveldocumentNumber"],
            "Sex": str(student["Sex"]),
            "Street": student["Street"],
            "Postcode": str(student["Postcode"]),
            "City": student["City"],
            "Country": str(student["Country"]),
            "Telephone": str(student["Telephone"]),
            "Email": student["Email"],
            "LastnameAtBirth": student["LastnameAtBirth"],
            "NationalityAtBirth": str(student["NationalityAtBirth"]),
            "CountryOfBirth": str(student["CountryOfBirth"]),
            "PlaceOfBirth": student["PlaceOfBirth"],
            "NationalityForApplication": str(student["NationalityForApplication"]),
            "TraveldocumentDateOfIssue": student["TraveldocumentDateOfIssue"],
            "TraveldocumentValidUntil": student["TraveldocumentValidUntil"],
            "TraveldocumentIssuingAuthority": str(student["TraveldocumentIssuingAuthority"]),
        },
    }


def post_sandbox_webhook(base: str, payload: dict) -> tuple[int, dict | str]:
    url = f"{base}/webhook/{WEBHOOK_PATH}"
    r = requests.post(url, json=payload, timeout=600)
    try:
        body = r.json()
    except Exception:
        body = r.text[:2000]
    return r.status_code, body


def post_sandbox_turbo(booker_url: str, payload: dict) -> tuple[int, dict | str]:
    url = f"{booker_url.rstrip('/')}/webhook/booker"
    r = requests.post(url, json=payload, timeout=600)
    try:
        body = r.json()
    except Exception:
        body = r.text[:2000]
    return r.status_code, body


def latest_execution(base: str, env: dict) -> dict | None:
    h = api_headers(env)
    r = requests.get(
        f"{base}/api/v1/executions",
        headers=h,
        params={"workflowId": SANDBOX_WORKFLOW_ID, "limit": 1, "includeData": "false"},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json().get("data") or []
    return data[0] if data else None


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="KOPENHAGEN sandbox E2E with dummy applicant")
    parser.add_argument(
        "--direct-turbo",
        action="store_true",
        help="Also POST direct turbo to sandbox Python (uses 2nd slot if available)",
    )
    parser.add_argument(
        "--skip-n8n",
        action="store_true",
        help="Skip n8n webhook E2E (turbo-only with --direct-turbo)",
    )
    args = parser.parse_args()

    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    booker_url = env.get("BOOKER_PUBLIC_URL", "http://127.0.0.1:8010")

    health = requests.get("http://127.0.0.1:8010/health", timeout=10)
    health.raise_for_status()
    print("Sandbox booker health OK")

    print("\n=== 1) Live KOPENHAGEN slot scan ===")
    scan = scan_kopenhagen()
    if not scan.get("ok"):
        print(json.dumps(scan, indent=2))
        print("FAIL: no slots — try again later")
        return 1
    print(f"Found {scan['slotCount']} slot(s); first={scan['firstSlot']}")

    print("\n=== 2) Seed dummy applicant (n8n Data Table) ===")
    try:
        student = seed_dummy_applicant(env)
    except requests.HTTPError as exc:
        print(f"Data table insert failed ({exc.response.status_code}); using inline student for turbo-only test")
        student = json.loads((FIXTURES / "kopenhagen-dummy-applicant.json").read_text(encoding="utf-8"))
        student["TraveldocumentNumber"] = f"SBX-KOPEN-{uuid.uuid4().hex[:6].upper()}"

    slots = scan.get("slots") or []
    if not slots:
        raise RuntimeError("No KOPENHAGEN slots found — cannot run E2E")
    watcher_payload = build_watcher_payload(scan)
    turbo_payload = build_turbo_payload(scan, student)
    payload_path = ROOT / "artifacts" / "kopenhagen-watcher-payload.json"
    turbo_path = ROOT / "artifacts" / "kopenhagen-turbo-payload.json"
    payload_path.write_text(json.dumps(watcher_payload, indent=2), encoding="utf-8")
    turbo_path.write_text(json.dumps(turbo_payload, indent=2), encoding="utf-8")
    print(f"Wrote {payload_path}")
    print(f"Wrote {turbo_path}")

    turbo_ok = False
    code = body = None
    if args.direct_turbo:
        turbo_slot = slots[1] if len(slots) > 1 else slots[0]
        turbo_payload["currentSlot"] = turbo_slot
        print("\n=== 3) Direct turbo test (sandbox Python) ===")
        print(f"Using slot: {turbo_slot}")
        code, body = post_sandbox_turbo(booker_url, turbo_payload)
        print(f"Turbo HTTP {code}")
        print(json.dumps(body, indent=2) if isinstance(body, dict) else body)
        turbo_ok = code == 200 and isinstance(body, dict) and body.get("resultType") == "SUCCESS"

    code2 = body2 = None
    if not args.skip_n8n:
        print("\n=== 4) Sandbox n8n webhook E2E ===")
        clear_lock(base, env)
        time.sleep(2)
        code2, body2 = post_sandbox_webhook(base, watcher_payload)
        print(f"Webhook HTTP {code2}")
        print(json.dumps(body2, indent=2) if isinstance(body2, dict) else body2)
    ex = latest_execution(base, env)
    if ex:
        print(f"Latest n8n execution: {ex.get('id')} status={ex.get('status')}")

    summary = {
        "office": OFFICE,
        "slots": scan.get("slots"),
        "studentPassport": student.get("TraveldocumentNumber"),
        "turboDirect": {"http": code, "success": turbo_ok, "resultType": body.get("resultType") if isinstance(body, dict) else None},
        "n8nWebhook": {"http": code2, "body": body2},
        "executionId": ex.get("id") if ex else None,
    }
    summary_path = ROOT / "artifacts" / "kopenhagen-e2e-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSummary -> {summary_path}")

    if turbo_ok:
        print("\nPASS: direct KOPENHAGEN turbo booking succeeded")
        return 0
    if code2 == 200:
        print("\nPASS: n8n webhook accepted (check execution for booking result)")
        return 0
    if not args.direct_turbo and not args.skip_n8n and code2 == 200:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
