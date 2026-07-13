"""Shared pytest fixtures for the booker webhook."""

from __future__ import annotations

import pytest

SAMPLE_SCHEDULER_HTML = """
<html><body>
<input type="hidden" name="Monday" id="Monday" value="7/6/2026 12:00:00 AM" />
<input type="hidden" name="CalendarId" id="CalendarId" value="44281520" />
<input type="radio" name="Start" value="7/7/2026 11:00:00 AM" />
<input type="radio" name="Start" value="7/8/2026 2:30:00 PM" />
</body></html>
"""


@pytest.fixture
def sample_scheduler_html() -> str:
    return SAMPLE_SCHEDULER_HTML
