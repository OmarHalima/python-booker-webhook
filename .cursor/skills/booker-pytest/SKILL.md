---
name: booker-pytest
description: Adds and runs pytest tests for the Austrian booker webhook. Use when writing unit or integration tests, setting up conftest fixtures, or maintaining the 44-test baseline in tests/unit and tests/integration.
---

# Booker pytest conventions

## When to use

- Add new unit/integration tests
- Fix test discovery after module moves
- Set up mocks for OCR, FastAPI, Playwright

## Layout (mandatory)

| Dir | Scope |
|-----|-------|
| `tests/unit/` | Pure logic, mocks, no network |
| `tests/integration/` | TestClient, semaphores |
| `tests/e2e/` | Playwright + live BMEIA |

**Never** add `test_*.py` at package root.

## Setup

```bash
cd python-booker-webhook
pip install -r requirements-dev.txt
pytest tests/unit tests/integration -q
```

Config: `pyproject.toml` (`pythonpath = ["."]`)

## Writing unit tests

```python
from captcha_ocr import normalize_captcha_text
from scheduler_html import parse_scheduler_choose_appointment_html
from booker_legacy import merge_session_cookie_from_set_cookie
```

Existing examples:

- `tests/unit/test_ocr_captcha_unit.py` — mocked HTTP
- `tests/unit/test_turbo_captcha_solver.py` — solver matrix
- `tests/unit/test_scheduler_parse.py` — HTML parsing

## Integration tests

```python
import pytest
from fastapi.testclient import TestClient
from app import app

pytestmark = pytest.mark.integration
client = TestClient(app)
```

## Fixtures

- Payload JSON: `fixtures/payloads/`
- Shared pytest: `tests/conftest.py`
- Markers: `@pytest.mark.integration`, `@pytest.mark.e2e`

## TDD flow (Superpowers)

1. Red — failing test in `tests/unit/`
2. Green — minimal implementation
3. Refactor — extract to module if pure logic

## Before claiming done

```bash
pytest tests/unit tests/integration -q
```

Show output. Use `verification-before-completion` skill.

## Delegate

Extract pure functions from `booker_legacy.py` → `booker-package-extract` skill
