#!/usr/bin/env python3
"""Scan BMEIA scheduler for open slots; returns sessionCookie + slot list."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
sys.path.insert(0, str(PYTHON_DIR))


def _context_session_cookie(context) -> str:
    parts: List[str] = []
    for c in context.cookies():
        if "bmeia.gv.at" not in (c.get("domain") or ""):
            continue
        parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


def scan_slots(
    *,
    office: str = "KOPENHAGEN",
    calendar_id: str = "9677702",
    language: str = "en",
    person_count: str = "1",
    max_weeks: int = 4,
    run_id: str = "slot-scan",
) -> Dict[str, Any]:
    import booker_legacy as booker
    from playwright.sync_api import sync_playwright

    payload: Dict[str, Any] = {
        "Office": office,
        "CalendarId": calendar_id,
        "Language": language,
        "PersonCount": person_count,
        "currentMonday": "",
        "currentSlot": "",
        "_runId": run_id,
        "runId": run_id,
    }

    all_slots: List[str] = []
    monday = ""
    t0 = time.perf_counter()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=booker.playwright_headless())
        context = browser.new_context(
            base_url=booker.APPOINTMENT_ORIGIN,
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        run_dir = booker.ARTIFACTS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        booker._open_landing_page(page, run_id)
        landing_sets = {
            "#Office": office,
            "select[name='Office']": office,
            "#Language": language,
            "select[name='Language']": language,
        }
        for selector, value in landing_sets.items():
            booker._set_scheduler_value(page, selector, value)
        booker._submit_next_command(page, run_id, stage="scan_landing_next")

        booker._apply_reservation_calendar_select(page, payload, run_id, lambda _: None)
        booker._repair_calendar_id_dom_if_needed(page, payload, run_id)
        booker._submit_next_command(page, run_id, stage="scan_reservation_next")

        page.wait_for_selector("select[name='PersonCount']", timeout=booker.person_count_wait_ms())
        booker._set_scheduler_value(page, "select[name='PersonCount']", person_count)
        booker._submit_next_command(page, run_id, stage="scan_person_count_next")

        page.wait_for_function(
            """() => {
              const t = (document.title || '').toLowerCase();
              const pc = document.querySelector("select[name='PersonCount']");
              const infoLike = t.includes('information') || t.includes('informationen');
              return infoLike && !pc;
            }""",
            timeout=booker.information_page_wait_ms(),
        )
        booker._submit_next_command(page, run_id, stage="scan_information_next")
        booker._wait_for_start_radios(page, run_id)

        for week_idx in range(max_weeks):
            slots = booker._list_start_radio_values(page)
            if page.locator("#Monday").count():
                monday = (page.locator("#Monday").first.get_attribute("value") or "").strip()
            elif page.locator("input[name='Monday']").count():
                monday = (
                    page.locator("input[name='Monday']").first.get_attribute("value") or ""
                ).strip()

            for s in slots:
                if s not in all_slots:
                    all_slots.append(s)

            if slots:
                page.screenshot(path=str(run_dir / f"week_{week_idx + 1}_slots.png"), full_page=True)
                (run_dir / f"week_{week_idx + 1}.html").write_text(page.content(), encoding="utf-8")
                break

            next_week = page.locator("input[name='Command'][value*='Next'], #nextButton, input[value='Next week']")
            advanced = False
            for sel in ("input[value='Next week']", "#nextButton", "input[name='Command'][value='Next']"):
                loc = page.locator(sel)
                if loc.count() > 0:
                    try:
                        booker._submit_next_command(page, run_id, stage=f"scan_week_{week_idx}")
                        booker._wait_for_start_radios(page, run_id)
                        advanced = True
                        break
                    except Exception:
                        continue
            if not advanced:
                break

        session_cookie = _context_session_cookie(context)
        browser.close()

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    out = {
        "ok": len(all_slots) > 0,
        "office": office,
        "calendarId": calendar_id,
        "language": language,
        "currentMonday": monday,
        "slots": all_slots,
        "slotCount": len(all_slots),
        "firstSlot": all_slots[0] if all_slots else "",
        "sessionCookie": session_cookie,
        "detectedAt": datetime.utcnow().isoformat() + "Z",
        "scanMs": elapsed_ms,
        "runId": run_id,
    }
    (booker.ARTIFACTS_DIR / run_id / "scan_result.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan BMEIA for appointment slots.")
    parser.add_argument("--office", default="KOPENHAGEN")
    parser.add_argument("--calendar-id", default="9677702")
    parser.add_argument("--language", default="en")
    parser.add_argument("--max-weeks", type=int, default=4)
    parser.add_argument("--run-id", default="slot-scan")
    parser.add_argument("-o", "--output", type=Path, help="Write JSON result to file")
    args = parser.parse_args()

    out = scan_slots(
        office=args.office,
        calendar_id=args.calendar_id,
        language=args.language,
        max_weeks=args.max_weeks,
        run_id=args.run_id,
    )
    text = json.dumps(out, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
