"""Headed Playwright walk-through to reproduce choose-appointment navigation."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

import booker_legacy as app  # noqa: E402


def snap(page, label: str, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = label.replace(" ", "_")
    meta = {
        "label": label,
        "url": page.url,
        "title": page.title(),
        "startRadios": page.locator("input[type='radio'][name='Start']").count(),
        "personCount": page.locator("select[name='PersonCount']").count(),
        "token": page.locator("#Token").count(),
        "stuckLoading": app._page_looks_stuck_loading(page),
    }
    page.screenshot(path=str(out_dir / f"{safe}.png"), full_page=True)
    try:
        html = app._safe_page_content(page)
        (out_dir / f"{safe}.html").write_text(html[:120000], encoding="utf-8")
    except Exception as exc:
        meta["htmlError"] = str(exc)
    print(json.dumps(meta, indent=2))
    return meta


def main() -> None:
    out_dir = ROOT / "artifacts" / "browser-debug-nav"
    payload = {
        "runId": "browser-debug-nav",
        "Language": "en",
        "Office": "KOPENHAGEN",
        "CalendarId": "9677702",
        "PersonCount": "1",
        "injectMissingCalendarId": True,
        "currentMonday": "",
        "currentSlot": "",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(ignore_https_errors=True)
        app._configure_playwright_context(context)
        page = context.new_page()

        print("=== Stage: landing ===")
        app._open_landing_page(page, "browser-debug-nav")
        snap(page, "01_landing", out_dir)

        for sel, val in [
            ("#Office", payload["Office"]),
            ("#Language", payload["Language"]),
        ]:
            app._set_scheduler_value(page, sel, val)

        print("=== Stage: landing_next ===")
        app._submit_next_command(page, "browser-debug-nav", stage="landing_next")
        snap(page, "02_after_landing_next", out_dir)

        print("=== Stage: calendar ===")
        app._apply_reservation_calendar_select(page, payload, "browser-debug-nav", lambda *_: None)
        snap(page, "03_calendar_set", out_dir)

        print("=== Stage: reservation_next ===")
        app._submit_next_command(page, "browser-debug-nav", stage="reservation_type_next")
        snap(page, "04_after_reservation_next", out_dir)

        print("=== Stage: person_count ===")
        page.wait_for_selector("select[name='PersonCount']", timeout=app.person_count_wait_ms())
        app._set_scheduler_value(page, "select[name='PersonCount']", "1")
        snap(page, "05_person_count", out_dir)

        print("=== Stage: person_count_next ===")
        app._submit_next_command(page, "browser-debug-nav", stage="person_count_next")
        snap(page, "06_after_person_count_next", out_dir)

        print("=== Stage: information wait ===")
        page.wait_for_function(
            """() => {
              const t = (document.title || '').toLowerCase();
              const pc = document.querySelector("select[name='PersonCount']");
              const infoLike = t.includes('information') || t.includes('informationen')
                || t.includes('auskunft');
              return infoLike && !pc;
            }""",
            timeout=app.information_page_wait_ms(),
        )
        snap(page, "07_information", out_dir)

        print("=== Stage: information_next ===")
        t0 = time.time()
        app._submit_next_command(page, "browser-debug-nav", stage="information_next")
        snap(page, "08_immediately_after_information_next", out_dir)

        print("=== Polling for Start radios (45s) ===")
        deadline = time.time() + 45
        last_meta = None
        while time.time() < deadline:
            last_meta = snap(page, f"poll_{int(time.time()-t0)}s", out_dir)
            if last_meta.get("startRadios", 0) > 0:
                print("SUCCESS: Start radios appeared")
                break
            if last_meta.get("stuckLoading"):
                print(f"  stuck loading at {int(time.time()-t0)}s — waiting...")
            page.wait_for_timeout(2000)
        else:
            print("FAIL: no Start radios after 45s")

        browser.close()


if __name__ == "__main__":
    main()
