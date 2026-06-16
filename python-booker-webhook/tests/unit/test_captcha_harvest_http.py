"""Unit tests for HTTP captcha harvest helpers."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from captcha_harvest_http import captcha_image_url, scheduler_headers

CAPTURE_HTML = """
<html><body><form action="/HomeWeb/Appointment">
<input name="BDC_VCID_Captcha" value="0b71ccb5b44a4e218c0bab8ef1f3006a" />
<input name="BDC_Hs_Captcha" value="abc" />
<input name="BDC_SP_Captcha" value="123" />
<input id="Token" value="tok" />
<input id="StartTime" value="slot" />
</form></body></html>
"""


def test_captcha_image_url():
    url = captcha_image_url("https://appointment.bmeia.gv.at", "abc123")
    assert url == "https://appointment.bmeia.gv.at/BotDetectCaptcha.ashx?get=image&c=Captcha&t=abc123"


def test_scheduler_headers_include_cookie():
    h = scheduler_headers("ASP.NET_SessionId=xyz", "https://appointment.bmeia.gv.at", "https://x/Scheduler")
    assert "xyz" in h["Cookie"]
    assert h["Content-Type"] == "application/x-www-form-urlencoded"


def test_parse_fresh_fields_from_fixture():
    import booker_legacy as booker

    fresh = booker.parse_fresh_fields(CAPTURE_HTML)
    assert fresh.bdc_vcid == "0b71ccb5b44a4e218c0bab8ef1f3006a"
    assert "BotDetectCaptcha.ashx" in fresh.captcha_image_url


def test_session_cache_roundtrip(tmp_path):
    from captcha_harvest_http import load_session_cache, save_session_cache

    path = tmp_path / "session_cache.json"
    payload = {"sessionCookie": "ASP.NET_SessionId=abc", "Office": "KOPENHAGEN"}
    save_session_cache(path, payload)
    loaded = load_session_cache(path)
    assert loaded == payload
    assert load_session_cache(tmp_path / "missing.json") is None


def test_refresh_vcid_http_parses_vcid(monkeypatch):
    from captcha_harvest_http import refresh_vcid_http

    def fake_post(**kwargs):
        return CAPTURE_HTML, "ASP.NET_SessionId=xyz"

    monkeypatch.setattr("captcha_harvest_http.post_select_slot_http", fake_post)
    fresh, cookie = refresh_vcid_http(
        session_cookie="old",
        payload={"Language": "en", "Office": "KOPENHAGEN", "CalendarId": "1", "PersonCount": "1"},
    )
    assert fresh.bdc_vcid == "0b71ccb5b44a4e218c0bab8ef1f3006a"
    assert cookie == "ASP.NET_SessionId=xyz"
