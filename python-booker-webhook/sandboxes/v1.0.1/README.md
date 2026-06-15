# Sandbox v1.0.1 — OCR captcha fix release

Tagged copy of **`ocr-fix-v1`** OCR/captcha pipeline fixes. Use this as the canonical isolated booker for v1.0.1 validation before merging to production root.

**Does not modify** repo root `app.py` / `booker_legacy.py` or live n8n workflows.

## vs production

| Area | Production | v1.0.1 |
|------|------------|--------|
| Captcha bytes for OCR | Parallel URL fetch could be used for solving | **DOM screenshot only**; URL prefetch cache-warm only |
| `BDC_Hs_Captcha` race | Fill + submit | `fill_captcha_with_hs_guard()` — skip submit if HS stale |
| BotDetect ready | Single wait | 3× retry on ready + DOM capture |
| OCR upload | `captcha.png` every request | Unique filename + SHA256 logged |
| Turbo solver rounds | OCR first always | Gemini-first on captcha round ≥ 2 |
| `PLAYWRIGHT_REUSE_BROWSER` | `true` (default) | **`true`** |
| Port | 8000 | **8011** (isolated) |

## Lineage

```
ocr-fix-v1  →  v1.0.1  (this sandbox)
```

See `docs/CHANGELOG.md` for patch detail. Parent sandbox: `../ocr-fix-v1/`.

## Layout

```
v1.0.1/
  python/           # patched booker (port 8011)
  scripts/          # compare, scan, validation, start_booker
  artifacts/        # runtime output (gitignored except .gitkeep)
  fixtures/         # kopenhagen-dummy-applicant.json
  docs/CHANGELOG.md
```

## Quick start

```powershell
cd python-booker-webhook\sandboxes\v1.0.1

# Env: copy from production .env into python\.env
# Required: CAPTCHA_PARALLEL_FETCH=false, CAPTCHA_OCR_BASE_URL, GEMINI_API_KEY

python scripts\start_booker.py
python scripts\run_ocr_validation.py

# Step by step:
python scripts\compare_captcha_fetch.py --samples 3
python scripts\run_three_turbo_bookings.py --rounds 3
```

HTTP server (manual):

```powershell
cd python
python -m uvicorn app:app --host 127.0.0.1 --port 8011 --workers 1
```

## OCR fixes (summary)

1. DOM-only captcha bytes for OCR (parallel URL is prefetch-only)
2. HS guard before submit
3. Pre-OCR DOM re-capture + unique OCR upload filename + SHA256 log
4. Gemini-first on captcha round ≥ 2

Full detail: `docs/CHANGELOG.md`, `../../docs/CAPTCHA_INVESTIGATION.md`

## Related

| Sandbox | Port | Purpose |
|---------|------|---------|
| `../ocr-fix-v1/` | 8011 | Original OCR fix dev sandbox |
| `../lock-integration-v1/` | 8010 | n8n lock + KOPENHAGEN E2E |

## Merge path to production

After v1.0.1 validation passes:

1. Port patches from `python/` to repo root modules
2. `pytest tests/unit tests/integration -q`
3. Update `docs/AGENT_HANDOFF.md`
4. Deploy VPS with `uvicorn app:app` on 8000
