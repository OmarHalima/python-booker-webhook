# Sandbox v1.0.1 — OCR captcha iteration (port 8011)

Isolated booker for **continued captcha/OCR experiments** after the v1.0.1 pipeline merged to production root (`app.py`, `booker_legacy.py` on port **8000**).

Use this sandbox when you need a separate uvicorn process without touching production entry or n8n.

## Status (2026-06-16)

| Item | State |
|------|--------|
| Pipeline fixes | **Merged** to production root |
| Local validation | 5/5 KOPENHAGEN SUCCESS on `:8000`; 77 pytest pass |
| VPS deploy | **Deferred** — after OCR finetune (5k dataset) |
| Predecessor `ocr-fix-v1` | **Archived** → [`../../archive/sandboxes/ocr-fix-v1/`](../../archive/sandboxes/ocr-fix-v1/) |

## vs production root

Both trees should stay aligned on captcha behavior. Re-sync `python/` from root after major production changes.

| Area | Production `:8000` | v1.0.1 `:8011` |
|------|-------------------|----------------|
| Captcha bytes | DOM screenshot only | Same (parallel fetch hard-disabled) |
| HS guard | `fill_captcha_with_hs_guard()` | Same |
| Turbo solver | Gemini-first on captcha round ≥ 2 | Same |
| DOM capture failure | `DomCaptchaCaptureError` | Same (sync from root) |

## Layout

```
v1.0.1/
  python/           # isolated booker copy (port 8011)
  scripts/          # compare, scan, validation, start_booker
  artifacts/        # runtime output (gitignored except .gitkeep)
  fixtures/         # kopenhagen-dummy-applicant.json
  docs/CHANGELOG.md
```

## Quick start

```powershell
cd python-booker-webhook\sandboxes\v1.0.1

# Env template → python\.env (see python\.env.example)
copy python\.env.example python\.env
# Fill CAPTCHA_OCR_BASE_URL, GEMINI_API_KEY

python scripts\start_booker.py
python scripts\run_ocr_validation.py
```

HTTP server (manual):

```powershell
cd python
python -m uvicorn app:app --host 127.0.0.1 --port 8011 --workers 1
```

## Related

| Doc | Purpose |
|-----|---------|
| [`docs/V1_0_1_PROBLEMS_AND_MERGE_PLAN.md`](../../docs/V1_0_1_PROBLEMS_AND_MERGE_PLAN.md) | Merge checklist, remaining P3 work |
| [`../../.env.example`](../../.env.example) | Full env reference (production + sandbox) |
| [`../lock-integration-v1/`](../lock-integration-v1/) | n8n lock + KOPENHAGEN E2E (port 8010) |
