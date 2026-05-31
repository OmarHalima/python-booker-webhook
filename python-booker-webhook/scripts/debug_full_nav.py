"""Full navigate_to_booking_form smoke test in headed browser."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

import booker_legacy as app


def main() -> None:
    payload = {
        "runId": "browser-full-nav",
        "Language": "en",
        "Office": "KOPENHAGEN",
        "CalendarId": "9677702",
        "PersonCount": "1",
        "injectMissingCalendarId": True,
        "currentMonday": "",
        "currentSlot": "",
        "captureArtifacts": False,
    }
    out_dir = ROOT / "artifacts" / "browser-full-nav"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(ignore_https_errors=True)
        app._configure_playwright_context(context)
        page = context.new_page()
        app.navigate_to_booking_form(page, payload, run_dir=out_dir)
        meta = {
            "url": page.url,
            "title": page.title(),
            "hasToken": page.locator("#Token").count() > 0,
            "hasCaptcha": page.locator("#CaptchaText").count() > 0,
            "hasStartRadios": page.locator("input[name='Start']").count() > 0,
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out_dir / "capture_form.png"), full_page=True)
        print(json.dumps(meta, indent=2))
        browser.close()


if __name__ == "__main__":
    main()
