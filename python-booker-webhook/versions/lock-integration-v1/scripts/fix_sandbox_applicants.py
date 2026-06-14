#!/usr/bin/env python3
"""Fix BMEIA country codes (818 ISO -> 49 KOPENHAGEN) on sandbox applicant rows."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_kopenhagen_e2e_test import APPLICANTS_TABLE, api_headers, load_env  # noqa: E402

KOPENHAGEN_COUNTRY = 49
ISO_EGYPT_WRONG = 818

COUNTRY_PATCH = {
    "Country": KOPENHAGEN_COUNTRY,
    "NationalityAtBirth": KOPENHAGEN_COUNTRY,
    "CountryOfBirth": KOPENHAGEN_COUNTRY,
    "NationalityForApplication": KOPENHAGEN_COUNTRY,
    "TraveldocumentIssuingAuthority": KOPENHAGEN_COUNTRY,
}


def get_rows(base: str, h: dict) -> list:
    r = requests.get(
        f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows",
        headers=h,
        params={"limit": 200},
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("data") or []


def update_passport(base: str, h: dict, passport: str, data: dict) -> dict:
    body = {
        "filter": {
            "type": "and",
            "filters": [
                {"columnName": "TraveldocumentNumber", "condition": "eq", "value": passport}
            ],
        },
        "data": data,
        "returnData": True,
    }
    r = requests.patch(
        f"{base}/api/v1/data-tables/{APPLICANTS_TABLE}/rows/update",
        headers=h,
        json=body,
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"{passport}: {r.status_code} {r.text[:300]}")
    out = r.json()
    if isinstance(out, list):
        rows = out
    else:
        rows = out.get("data") or out.get("rows") or []
    return rows[0] if rows else {}


def needs_country_fix(row: dict) -> bool:
    for key in COUNTRY_PATCH:
        val = row.get(key)
        if val in (ISO_EGYPT_WRONG, str(ISO_EGYPT_WRONG)):
            return True
    return False


def main() -> int:
    env = load_env()
    base = env["N8N_API_BASE_URL"].rstrip("/")
    h = api_headers(env)

    rows = get_rows(base, h)
    targets = [r for r in rows if str(r.get("TraveldocumentNumber", "")).startswith("SBX")]
    print(f"Found {len(targets)} SBX applicant(s)")

    report = {"updated": [], "skipped": []}
    for row in targets:
        passport = row.get("TraveldocumentNumber") or ""
        if not passport:
            continue
        if not needs_country_fix(row):
            report["skipped"].append(passport)
            continue

        patch = dict(COUNTRY_PATCH)
        status = row.get("status") or ""
        if status in ("PENDING", "FAILED", "IN_PROGRESS"):
            patch["bookedSlot"] = ""
            patch["bookedAt"] = ""
            if status == "IN_PROGRESS":
                patch["status"] = "PENDING"

        updated = update_passport(base, h, passport, patch)
        report["updated"].append(
            {
                "passport": passport,
                "status": updated.get("status") or status,
                "Country": updated.get("Country"),
            }
        )
        print(f"  fixed {passport} -> Country={updated.get('Country')} status={updated.get('status')}")

    out_path = ROOT / "artifacts" / "fix-sandbox-applicants-report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nUpdated {len(report['updated'])} row(s), skipped {len(report['skipped'])}")
    print(f"Report -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
