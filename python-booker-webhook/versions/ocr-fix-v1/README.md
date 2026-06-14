# Sandbox ocr-fix-v1

Production booker copy with **OCR/captcha pipeline fixes** only. Does not change repo root `app.py` / `booker_legacy.py` or n8n workflows.

## vs production

| Area | Production | ocr-fix-v1 |
|------|------------|------------|
| Captcha bytes for OCR | Parallel URL fetch could be used for solving | **DOM screenshot only**; URL prefetch cache-warm only |
| `BDC_Hs_Captcha` race | Fill + submit | `fill_captcha_with_hs_guard()` — skip submit if HS stale |
| BotDetect ready | Single wait | 3× retry on ready + DOM capture |
| OCR upload | `captcha.png` every request | Unique filename + SHA256 logged |
| Turbo solver rounds | OCR first always | Gemini-first on captcha round ≥ 2 |
| `PLAYWRIGHT_REUSE_BROWSER` | `true` (default) | **`true`** — keep warm browser for speed |
| Port | 8000 | **8011** (isolated) |

## Layout

```
ocr-fix-v1/
  python/           # patched copy (run on port 8011)
  scripts/          # compare, scan, validation
  artifacts/        # reports + captcha compare PNGs
  fixtures/         # kopenhagen-dummy-applicant.json
  docs/CHANGELOG.md
```

## Quick start

```powershell
cd python-booker-webhook\versions\ocr-fix-v1

# Copy env from production or lock-integration sandbox:
#   copy ..\lock-integration-v1\python\.env python\.env
# Ensure: CAPTCHA_PARALLEL_FETCH=false, PLAYWRIGHT_REUSE_BROWSER=true

# Validate (DOM compare + 3 turbo bookings, HTTP on :8011)
python scripts\start_booker.py
python scripts\run_ocr_validation.py

# Or step by step:
python scripts\compare_captcha_fetch.py --samples 3
python scripts\run_three_turbo_bookings.py --rounds 3

# HTTP server (optional)
cd python
python -m uvicorn app:app --host 127.0.0.1 --port 8011 --workers 1
```

## OCR fixes (detail)

1. **`fetch_captcha_with_parallel_prefetch`** — always returns DOM bytes; parallel HTTP is ignored for solving.
2. **`fill_captcha_with_hs_guard`** — retries captcha fill until `BDC_Hs_Captcha` updates.
3. **Pre-OCR DOM re-capture** — fresh `attempt_XX_captcha_ocr.png` immediately before solver.
4. **`solve_captcha_with_ocr`** — logs `imageSha256`, sends unique upload filename (avoids OCR service cross-talk).
5. **`_solve_turbo_captcha`** — Gemini-first on round 2+ / attempt 2+.

## Related

- Lock workflow sandbox: `../lock-integration-v1/` (port 8010, n8n E2E)
- Investigation: `../../docs/CAPTCHA_INVESTIGATION.md`
