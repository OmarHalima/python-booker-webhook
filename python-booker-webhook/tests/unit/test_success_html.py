"""Success vs capture-page HTML heuristics (booker_legacy.is_success_html)."""

from booker_legacy import is_success_html

_CAPTURE_FORM_HTML = """
<html><head><title>Austrian appointment system - Capture data</title></head>
<body><h1>Capture data</h1>
<input id="Token" value="abc" />
<input id="BDC_VCID_Captcha" value="x" />
</body></html>
"""

_CONFIRMATION_HTML = """
<html><head><title>Confirmation of reservation</title></head>
<body><h1>Confirmation of reservation</h1>
<p>Your appointment has been reserved.</p>
</body></html>
"""

_OTHER_PAGE_HTML = """
<html><head><title>Austrian appointment system</title></head>
<body><p>Please select an office.</p></body></html>
"""


def test_is_success_html_false_on_capture_form():
    assert is_success_html(_CAPTURE_FORM_HTML) is False


def test_is_success_html_false_when_capture_title_present():
    html = "<html><title>Austrian appointment system - Capture</title><body></body></html>"
    assert is_success_html(html) is False


def test_is_success_html_true_on_confirmation_page():
    assert is_success_html(_CONFIRMATION_HTML) is True


def test_is_success_html_true_when_not_capture_page():
    assert is_success_html(_OTHER_PAGE_HTML) is True
