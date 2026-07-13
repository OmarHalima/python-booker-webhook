# Sandboxes

Isolated copies for experiments. Production entry remains `python-booker-webhook/app.py` on port **8000**.

| Sandbox | Port | Purpose |
|---------|------|---------|
| [`v1.0.1/`](v1.0.1/) | 8011 | **OCR/captcha iteration sandbox** — isolated uvicorn + validation scripts |
| [`lock-integration-v1/`](lock-integration-v1/) | 8010 | n8n booker-lock + KOPENHAGEN watcher E2E |

**Retired:** `ocr-fix-v1` → [`../../archive/sandboxes/ocr-fix-v1/`](../../archive/sandboxes/ocr-fix-v1/) (2026-06-16). Pipeline fixes merged to production root; use `v1.0.1/` for continued `:8011` experiments.

Versioned sandboxes (`v1.0.1`) include a full `python/` tree for isolated uvicorn runs.

**Merge tracking:** [docs/V1_0_1_PROBLEMS_AND_MERGE_PLAN.md](../docs/V1_0_1_PROBLEMS_AND_MERGE_PLAN.md)
