#!/usr/bin/env python3
"""Build turbo payload from scan + local dummy student (no n8n)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
OFFICE = "KOPENHAGEN"
CALENDAR_ID = "9677702"


def dummy_student(suffix: str) -> dict:
    row = json.loads((FIXTURES / "kopenhagen-dummy-applicant.json").read_text(encoding="utf-8"))
    try:
        seed_num = int(suffix[:4], 16)
    except ValueError:
        seed_num = sum(ord(c) for c in suffix)
    row["TraveldocumentNumber"] = f"SBX-OCR-{suffix}"
    row["Email"] = f"ocr.fix.{suffix.lower()}@example.com"
    row["Telephone"] = f"2012345{suffix[:5].ljust(5, '0')}"[:12]
    row["Firstname"] = f"OCR{suffix[:4]}"
    row["Lastname"] = f"FIX{suffix[-2:]}"
    row["DateOfBirth"] = f"{3 + (seed_num % 9)}/15/199{6 + ((seed_num >> 4) % 3)}"
    row["PlaceOfBirth"] = f"City{suffix[:3]}"
    row["LastnameAtBirth"] = row["Lastname"]
    return row


def build_turbo_payload(scan: dict, student: dict, *, slot: str, run_id: str) -> Dict[str, Any]:
    return {
        "runId": run_id,
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
        "currentSlot": slot,
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
