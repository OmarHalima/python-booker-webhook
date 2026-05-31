"""Slot selection / StartTime matching helpers."""
from booker_legacy import _find_matching_start_value, _normalize_slot_for_compare, booking_allow_slot_fallback


def test_normalize_slot_compare():
    assert _normalize_slot_for_compare("6/2/2026 11:30:00 AM") == _normalize_slot_for_compare(
        "6/2/2026 11:30 AM"
    )


def test_find_matching_start_value():
    candidates = ["6/3/2026 2:30:00 PM"]
    assert _find_matching_start_value("6/2/2026 11:30:00 AM", candidates) is None
    assert _find_matching_start_value("6/3/2026 2:30:00 PM", candidates) == "6/3/2026 2:30:00 PM"


def test_slot_fallback_default_off():
    assert booking_allow_slot_fallback() is False
