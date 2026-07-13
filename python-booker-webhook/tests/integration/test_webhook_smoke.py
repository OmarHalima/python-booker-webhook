"""Smoke tests for the booker webhook (no browser, no real session)."""

import pytest
from fastapi.testclient import TestClient

from app import app

pytestmark = pytest.mark.integration

client = TestClient(app)


def test_webhook_rejects_empty_payload() -> None:
    r = client.post("/webhook/booker", json={})
    assert r.status_code == 400
    body = r.json()
    assert "detail" in body
    assert "sessionCookie" in body["detail"]


def test_webhook_rejects_partial_payload() -> None:
    r = client.post(
        "/webhook/booker",
        json={"sessionCookie": "x", "Language": "en"},
    )
    assert r.status_code == 400


if __name__ == "__main__":
    test_webhook_rejects_empty_payload()
    test_webhook_rejects_partial_payload()
    print("ok: webhook smoke tests passed")
