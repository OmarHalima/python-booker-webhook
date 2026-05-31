"""
Booking slot / concurrency helpers (no browser).
Run: python test_booking_concurrency.py
"""
import os
import threading

from fastapi.testclient import TestClient

import booker_legacy as app_module
from app import app
from booker_legacy import max_concurrent_bookings


def test_max_concurrent_default_is_one() -> None:
    assert max_concurrent_bookings() >= 1


def test_busy_returns_503_when_slot_held() -> None:
    os.environ["MAX_CONCURRENT_BOOKINGS"] = "1"
    os.environ["BOOKING_SLOT_WAIT_SEC"] = "0.2"
    os.environ["BOOKING_USE_GLOBAL_LOCK"] = "false"
    app_module._BOOKING_SEM = None

    sem = app_module._get_booking_semaphore()
    assert sem.acquire(blocking=False)

    client = TestClient(app)
    r = client.post(
        "/webhook/booker",
        json={
            "sessionCookie": "ASP.NET_SessionId=test",
            "Language": "en",
            "Office": "X",
            "CalendarId": "1",
            "currentMonday": "1/1/2026",
            "currentSlot": "1/2/2026",
            "student": {"Lastname": "A"},
        },
    )
    sem.release()
    app_module._BOOKING_SEM = None

    assert r.status_code == 503
    assert r.headers.get("retry-after")
    body = r.json()
    assert body["detail"]["error"] == "BOOKING_SERVER_BUSY"
    assert body["detail"]["workerPid"]


if __name__ == "__main__":
    test_max_concurrent_default_is_one()
    test_busy_returns_503_when_slot_held()
    print("ok: booking concurrency tests passed")
