# Tests

| Directory | What runs | Command |
|-----------|-----------|---------|
| `unit/` | Pure logic — OCR, scheduler HTML, cookies, captcha solver matrix | `pytest tests/unit -q` |
| `integration/` | FastAPI TestClient, concurrency semaphore | `pytest tests/integration -q` |
| `e2e/` | Playwright + live BMEIA session | `pytest tests/e2e -q` (manual) |

Setup:

```bash
pip install -r requirements-dev.txt
pytest tests/unit tests/integration -q
```

Manual OCR API check (not pytest): `python scripts/run_ocr_captcha_api.py`

Fixtures: [`../fixtures/`](../fixtures/)
