"""
Parse BMEIA /HomeWeb/Scheduler "choose appointment" HTML for n8n alignment and tests.

Use the returned ``monday`` and ``startValues`` as webhook ``currentMonday`` and
``currentSlot`` (exact ``value=`` strings from ``input[name=Start]``).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def parse_scheduler_choose_appointment_html(html: str) -> Dict[str, Any]:
    """
    Extract hidden Monday and Start radio values from scheduler response HTML.

    Returns:
        monday: value of #Monday / name=Monday hidden input, or None
        startValues: list of value attributes for Start radios (order as in DOM)
        calendarId: hidden CalendarId if present, or None
        personCount: hidden PersonCount if present, or None
    """
    text = html or ""
    monday: Optional[str] = None
    m = re.search(
        r'<input[^>]+(?:id="Monday"|name="Monday")[^>]*value="([^"]*)"',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        m = re.search(
            r'<input[^>]+value="([^"]*)"[^>]*(?:id="Monday"|name="Monday")',
            text,
            re.IGNORECASE | re.DOTALL,
        )
    if m:
        monday = m.group(1)

    starts: List[str] = []
    for part in re.finditer(
        r'<input[^>]*name=["\']Start["\'][^>]*value=["\']([^"\']*)["\']',
        text,
        re.IGNORECASE,
    ):
        starts.append(part.group(1))
    if not starts:
        for part in re.finditer(
            r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']Start["\']',
            text,
            re.IGNORECASE,
        ):
            starts.append(part.group(1))

    cal_m = re.search(
        r'<input[^>]+(?:id="CalendarId"|name="CalendarId")[^>]*value="([^"]*)"',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    calendar_id = cal_m.group(1) if cal_m else None

    pc_m = re.search(
        r'<input[^>]+(?:id="PersonCount"|name="PersonCount")[^>]*value="([^"]*)"',
        text,
        re.IGNORECASE | re.DOTALL,
    )
    person_count = pc_m.group(1) if pc_m else None

    return {
        "monday": monday,
        "startValues": starts,
        "calendarId": calendar_id,
        "personCount": person_count,
    }
