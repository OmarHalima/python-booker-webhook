"""Capture form must load on a real BMEIA URL (not about:blank) for browser submit."""

import pytest

from booker_legacy import APPOINTMENT_ORIGIN, load_capture_html_in_browser, parse_fresh_fields

_SAMPLE_CAPTURE_HTML = """
<html><head><title>Austrian appointment system - Capture</title></head><body>
<form method="post" action="/HomeWeb/AppointmentPassport">
<input type="hidden" id="Token" name="Token" value="tok123" />
<input type="hidden" id="StartTime" name="StartTime" value="6/11/2026 11:30:00 AM" />
<input type="hidden" id="BDC_VCID_Captcha" name="BDC_VCID_Captcha" value="vcid123" />
<input type="hidden" id="BDC_Hs_Captcha" name="BDC_Hs_Captcha" value="" />
<input type="hidden" id="BDC_SP_Captcha" name="BDC_SP_Captcha" value="" />
<input type="hidden" id="BDC_BackWorkaround_Captcha" name="BDC_BackWorkaround_Captcha" value="0" />
<input type="text" id="CaptchaText" name="CaptchaText" />
<button type="submit" id="nextButton">Next</button>
</form>
</body></html>
"""


def test_parse_fresh_fields_capture_endpoint():
    fresh = parse_fresh_fields(_SAMPLE_CAPTURE_HTML)
    assert fresh.booking_endpoint == f"{APPOINTMENT_ORIGIN}/HomeWeb/AppointmentPassport"


@pytest.mark.skipif(
    not pytest.importorskip("playwright"),
    reason="playwright required",
)
def test_load_capture_html_in_browser_real_url():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(base_url=APPOINTMENT_ORIGIN)
        page = context.new_page()
        load_capture_html_in_browser(page, _SAMPLE_CAPTURE_HTML, run_id="unit-capture-nav")
        assert page.url.startswith(APPOINTMENT_ORIGIN)
        assert "about:blank" not in page.url
        assert "/HomeWeb/AppointmentPassport" in page.url
        assert page.locator("#Token").count() == 1
        browser.close()
