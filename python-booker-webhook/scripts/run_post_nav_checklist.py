"""
Post-navigation checklist: captcha DOM/VCID, OCR fallback, human typing, artifacts.

Writes artifacts/post-nav-checklist/checklist-report.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import booker_legacy as app  # noqa: E402

REPORT_DIR = ROOT / "artifacts" / "post-nav-checklist"
REPORT_PATH = REPORT_DIR / "checklist-report.json"

NAV_SCREENSHOTS = [
    "00_goto_from_specific_info",
    "01_after_landing_setters",
    "02_after_landing_next",
    "02b_after_reservation_calendar",
    "02c_before_reservation_next",
    "03_reservation_type_ready",
    "04_after_reservation_type_next",
    "05_person_count_ready",
    "06_after_person_count_next",
    "07_information_page",
    "08_after_information_next",
    "09_choose_appointment_ready",
    "10_after_slot_selection",
    "11_after_choose_appointment_next",
    "12_capture_form_navigation",
]


def _item(section: str, name: str, passed: bool, detail: str = "") -> Dict[str, Any]:
    return {"section": section, "name": name, "passed": passed, "detail": detail}


def check_unit_classification(items: List[Dict[str, Any]]) -> None:
    html_ab = '<p class="message-error">You have already reserved an appointment.</p>'
    r = app.classify_post_submit_html(html_ab)
    items.append(
        _item(
            "2-multi-slot",
            "ALREADY_BOOKED classification",
            r == ("ALREADY_BOOKED", "You have already reserved an appointment."),
            str(r),
        )
    )
    html_st = (
        '<p class="message-error">The appointment was unfortunately '
        "allocated otherwise in the meantime.</p>"
    )
    r2 = app.classify_post_submit_html(html_st)
    items.append(
        _item(
            "2-multi-slot",
            "SLOT_TAKEN classification",
            r2[0] == "SLOT_TAKEN",
            str(r2),
        )
    )


def check_ocr_gemini_fallback(items: List[Dict[str, Any]]) -> None:
    with patch("app.solve_captcha_with_ocr", side_effect=ValueError("ocr down")):
        with patch("app.solve_captcha_with_gemini", return_value="FALLBACK1"):
            with patch.dict("os.environ", {"CAPTCHA_SOLVER": "ocr_with_gemini_fallback"}, clear=False):
                out = app.solve_captcha(b"fakepng", run_id="checklist", attempt=1, vcid="abc")
    items.append(
        _item(
            "1-captcha",
            "ocr_with_gemini_fallback",
            out == "FALLBACK1",
            f"solve_captcha returned {out!r}",
        )
    )


def check_captcha_dom_and_vcid(items: List[Dict[str, Any]]) -> Optional[Path]:
    from playwright.sync_api import sync_playwright

    out_dir = REPORT_DIR / "browser-captcha"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "runId": "post-nav-checklist",
        "Language": "en",
        "Office": "KOPENHAGEN",
        "CalendarId": "9677702",
        "PersonCount": "1",
        "injectMissingCalendarId": True,
        "currentMonday": "",
        "currentSlot": "",
    }

    dom_vcid = ""
    img_src = ""
    dom_vcid_match = False
    dom_screenshot_ok = False
    img_src_has_vcid = False
    ocr_ok = False
    ocr_detail = ""
    typing_events_ok = False
    typing_detail = ""
    recorder_run_dir: Optional[Path] = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        app._configure_playwright_context(context)
        page = context.new_page()
        run_dir = out_dir / "with-recorder"
        recorder_run_dir = run_dir
        recorder = app.ArtifactRecorder(run_dir=run_dir, page=page)
        app.navigate_to_booking_form(page, payload, recorder=recorder, run_dir=run_dir)

        fresh = app.extract_fresh_from_dom(page, run_id="post-nav-checklist")
        dom_vcid = page.locator("#BDC_VCID_Captcha").first.input_value()
        img_src = page.locator("#Captcha_CaptchaImage").first.get_attribute("src") or ""
        dom_vcid_match = dom_vcid == fresh.bdc_vcid
        img_src_has_vcid = dom_vcid in img_src

        try:
            png = app.capture_captcha_bytes_from_dom(page)
            dom_screenshot_ok = len(png) > 500
            (out_dir / "captcha_dom.png").write_bytes(png)
        except Exception as exc:
            dom_screenshot_ok = False
            ocr_detail = f"dom capture failed: {exc}"

        if dom_screenshot_ok and app.captcha_ocr_base_url():
            try:
                text = app.solve_captcha(png, run_id="post-nav-checklist", attempt=1, vcid=dom_vcid)
                ocr_ok = bool(text and text.isalnum())
                ocr_detail = f"OCR returned {text!r}"
            except Exception as exc:
                ocr_detail = f"OCR/Gemini error: {exc}"
        elif not app.captcha_ocr_base_url():
            ocr_detail = "CAPTCHA_OCR_BASE_URL not set — skipped live OCR"

        page.evaluate(
            """() => {
              window.__captchaEvents = [];
              const el = document.querySelector('#CaptchaText');
              if (!el) return;
              for (const ev of ['keydown', 'keypress', 'input', 'keyup']) {
                el.addEventListener(ev, () => window.__captchaEvents.push(ev), true);
              }
            }"""
        )
        app._fill_captcha_text_like_user(page, "TEST1")
        events = page.evaluate("() => window.__captchaEvents || []")
        has_input = "input" in events
        has_key = any(e in events for e in ("keydown", "keypress", "keyup"))
        filled = page.locator("#CaptchaText").first.input_value()
        typing_events_ok = has_input and has_key and filled.upper() == "TEST1"
        typing_detail = f"events={events}, filled={filled!r}"

        browser.close()

    items.append(
        _item(
            "1-captcha",
            "VCID DOM matches fresh fields",
            dom_vcid_match,
            f"dom_vcid tail={dom_vcid[-8:] if dom_vcid else ''}",
        )
    )
    items.append(
        _item(
            "1-captcha",
            "Captcha image src contains VCID",
            img_src_has_vcid,
            f"src tail={img_src[-40:] if img_src else ''}",
        )
    )
    items.append(
        _item(
            "1-captcha",
            "DOM screenshot #Captcha_CaptchaImage",
            dom_screenshot_ok,
            f"bytes>500={dom_screenshot_ok}",
        )
    )
    items.append(
        _item(
            "1-captcha",
            "Live OCR/Gemini solve (optional)",
            ocr_ok or "skipped" in ocr_detail.lower() or "not set" in ocr_detail.lower(),
            ocr_detail,
        )
    )
    items.append(
        _item(
            "1-captcha",
            "Human typing events on #CaptchaText",
            typing_events_ok,
            typing_detail,
        )
    )
    return recorder_run_dir


def check_artifacts_from_last_run(
    items: List[Dict[str, Any]], prefer_dir: Optional[Path] = None
) -> None:
    found_dir = prefer_dir
    if found_dir is None:
        shot_files = sorted(
            (ROOT / "artifacts").glob("**/screenshots/*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for png in shot_files[:50]:
            if "00_goto_from_specific_info" in png.name or "12_capture_form" in png.name:
                found_dir = png.parent.parent
                break

    if found_dir is None or not found_dir.is_dir():
        items.append(
            _item(
                "3-monitoring",
                "Navigation artifacts 00-12",
                False,
                "No run with screenshots/00_goto under artifacts/",
            )
        )
        items.append(
            _item(
                "3-monitoring",
                "Captcha attempt artifacts",
                False,
                "none — run full POST /webhook/booker with captureArtifacts: true",
            )
        )
        return

    shots_dir = found_dir / "screenshots"
    all_names = " ".join(p.name for p in shots_dir.glob("*.png"))
    missing = [s for s in NAV_SCREENSHOTS if s not in all_names]
    captcha_artifacts = list(found_dir.glob("**/attempt_*_captcha*.png"))
    rel = found_dir.relative_to(ROOT) if found_dir.is_relative_to(ROOT) else found_dir
    items.append(
        _item(
            "3-monitoring",
            "Navigation artifacts 00-12",
            len(missing) == 0,
            f"run={rel} missing={missing[:5]}" if missing else f"run={rel} all present",
        )
    )
    items.append(
        _item(
            "3-monitoring",
            "Captcha attempt artifacts",
            len(captcha_artifacts) > 0,
            f"found {len(captcha_artifacts)} captcha png(s)"
            if captcha_artifacts
            else "none yet — expected after full /webhook/booker submit",
        )
    )


def check_debug_log_fields(items: List[Dict[str, Any]]) -> None:
    log_path = ROOT / "debug-ce612b.log"
    if not log_path.is_file():
        items.append(
            _item("3-monitoring", "H_submit_result in debug log", False, "debug-ce612b.log missing")
        )
        return
    text = log_path.read_text(encoding="utf-8", errors="replace")
    has_submit_result = "H_submit_result" in text or "H_submit_payload" in text
    has_captcha_sync = "H_captcha_sync" in text
    has_vcid_match = bool(re.search(r'"vcidMatch"\s*:\s*true', text))
    items.append(
        _item(
            "3-monitoring",
            "Debug log submit/captcha entries",
            has_submit_result and has_captcha_sync,
            f"H_submit={has_submit_result} H_captcha_sync={has_captcha_sync} vcidMatch=true seen={has_vcid_match}",
        )
    )


def check_session_docs(items: List[Dict[str, Any]]) -> None:
    parallel_doc = (ROOT / "docs" / "PARALLEL_BOOKING.md").read_text(encoding="utf-8")
    n8n_doc = (ROOT / "docs" / "n8n-booking-result-flow.md").read_text(encoding="utf-8")
    items.append(
        _item(
            "2-multi-slot",
            "Session uniqueness documented",
            "fresh" in parallel_doc.lower() and "sessionCookie" in parallel_doc,
            "docs/PARALLEL_BOOKING.md",
        )
    )
    items.append(
        _item(
            "2-multi-slot",
            "n8n SLOT_TAKEN / ALREADY_BOOKED routing documented",
            "SLOT_TAKEN" in n8n_doc and "ALREADY_BOOKED" in n8n_doc,
            "docs/n8n-booking-result-flow.md",
        )
    )
    items.append(
        _item(
            "3-monitoring",
            "n8n HTTP timeout 300000ms documented",
            "300000" in n8n_doc,
            "docs/n8n-booking-result-flow.md",
        )
    )


def main() -> int:
    items: List[Dict[str, Any]] = []
    check_unit_classification(items)
    check_ocr_gemini_fallback(items)
    check_session_docs(items)
    check_debug_log_fields(items)

    recorder_dir: Optional[Path] = None
    print("=== Browser captcha checks (live site) ===")
    try:
        recorder_dir = check_captcha_dom_and_vcid(items)
    except Exception as exc:
        items.append(_item("1-captcha", "Browser captcha flow", False, str(exc)[:300]))

    check_artifacts_from_last_run(items, prefer_dir=recorder_dir)

    passed = sum(1 for i in items if i["passed"])
    failed = sum(1 for i in items if not i["passed"])
    report = {
        "summary": {"total": len(items), "passed": passed, "failed": failed},
        "items": items,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    print(f"\nReport: {REPORT_PATH}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
