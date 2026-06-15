"""Tests for scheduler HTML parsing and session cookie merge helpers."""

from booker_legacy import merge_session_cookie_from_set_cookie, parse_cookie_header, resolve_calendar_id_option_value
from booker_legacy import _parse_inject_calendar_flags
from scheduler_html import parse_scheduler_choose_appointment_html


SAMPLE_HTML = """
<html><body>
<input type="hidden" name="Monday" id="Monday" value="7/6/2026 12:00:00 AM" />
<input type="hidden" name="CalendarId" id="CalendarId" value="44281520" />
<input type="radio" name="Start" value="7/7/2026 11:00:00 AM" />
<input type="radio" name="Start" value="7/8/2026 2:30:00 PM" />
</body></html>
"""


def test_parse_scheduler_monday_and_starts():
    out = parse_scheduler_choose_appointment_html(SAMPLE_HTML)
    assert out["monday"] == "7/6/2026 12:00:00 AM"
    assert out["startValues"] == ["7/7/2026 11:00:00 AM", "7/8/2026 2:30:00 PM"]
    assert out["calendarId"] == "44281520"


def test_merge_session_cookie_from_set_cookie():
    base = "foo=1; ASP.NET_SessionId=OLD"
    set_cookie = "ASP.NET_SessionId=NEW123; path=/; HttpOnly"
    merged = merge_session_cookie_from_set_cookie(base, set_cookie)
    pairs = parse_cookie_header(merged)
    assert pairs["ASP.NET_SessionId"] == "NEW123"
    assert pairs["foo"] == "1"


def test_merge_session_cookie_no_set_cookie_unchanged():
    assert merge_session_cookie_from_set_cookie("a=b", "") == "a=b"
    assert merge_session_cookie_from_set_cookie("a=b", None) == "a=b"  # type: ignore[arg-type]


def test_resolve_calendar_id_exact():
    opts = [
        {"value": "0", "text": "Please select"},
        {"value": "32529659", "text": "Familienzusammenführung sonstige"},
    ]
    assert resolve_calendar_id_option_value("32529659", "", opts) == "32529659"


def test_resolve_calendar_id_fallback_substring():
    opts = [
        {"value": "0", "text": "Please select"},
        {"value": "32528820", "text": "Familienzusammenführung ÄgypterInnen"},
        {"value": "32529659", "text": "Familienzusammenführung sonstige Staatsangehörige"},
    ]
    assert resolve_calendar_id_option_value("44281520", "Ägypter", opts) == "32528820"


def test_resolve_calendar_id_invalid_no_hint():
    opts = [{"value": "0", "text": "Please select"}, {"value": "111", "text": "Other"}]
    assert resolve_calendar_id_option_value("44281520", "", opts) is None


def test_parse_inject_calendar_flags():
    assert _parse_inject_calendar_flags({"injectMissingCalendarId": True})[0] is True
    assert _parse_inject_calendar_flags({"injectMissingCalendarId": "true"})[0] is True
    assert _parse_inject_calendar_flags({"injectMissingCalendarId": "false"})[0] is False
    assert _parse_inject_calendar_flags({"injectMissingCalendarId": "0"})[0] is False
    assert _parse_inject_calendar_flags({"injectCalendarId": "44281520"})[0] is False
