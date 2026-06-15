# Sandboxes

Isolated copies for experiments. Production entry remains `python-booker-webhook/app.py` on port **8000**.

| Sandbox | Port | Purpose |
|---------|------|---------|
| [`v1.0.1/`](v1.0.1/) | 8011 | **OCR captcha fix release** (from ocr-fix-v1) |
| [`ocr-fix-v1/`](ocr-fix-v1/) | 8011 | Original OCR fix dev sandbox |
| [`lock-integration-v1/`](lock-integration-v1/) | 8010 | n8n booker-lock + KOPENHAGEN watcher E2E |

Versioned sandboxes (`v1.0.1`) include a full `python/` tree for isolated uvicorn runs.
