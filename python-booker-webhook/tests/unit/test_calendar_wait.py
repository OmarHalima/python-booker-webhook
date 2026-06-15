"""Calendar reservation wait configuration."""
import booker_legacy as app


def test_calendar_options_wait_default(monkeypatch):
    monkeypatch.delenv("CALENDAR_OPTIONS_WAIT_SEC", raising=False)
    assert app._calendar_options_wait_seconds() == 10.0
    assert app._calendar_selector_timeout_ms() == 10000


def test_calendar_options_wait_env(monkeypatch):
    monkeypatch.setenv("CALENDAR_OPTIONS_WAIT_SEC", "7")
    assert app._calendar_options_wait_seconds() == 7.0


def test_form_load_timeout_env(monkeypatch):
    monkeypatch.setenv("FORM_LOAD_TIMEOUT_MS", "5000")
    assert app.form_load_timeout_ms() == 5000


def test_nav_timeouts_env(monkeypatch):
    monkeypatch.setenv("PERSON_COUNT_WAIT_MS", "12000")
    monkeypatch.setenv("CHOOSE_APPOINTMENT_WAIT_MS", "15000")
    assert app.person_count_wait_ms() == 12000
    assert app.choose_appointment_wait_ms() == 15000


def test_resolve_fails_when_id_not_in_loaded_options():
    opts = [
        {"value": "0", "text": "Please select"},
        {"value": "44279679", "text": "Student Master PhD"},
    ]
    assert app.resolve_calendar_id_option_value("44281520", "", opts) is None
    assert (
        app.resolve_calendar_id_option_value("44281520", "Master", opts) == "44279679"
    )
