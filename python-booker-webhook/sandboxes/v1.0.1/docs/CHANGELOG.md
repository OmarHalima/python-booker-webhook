# Changelog — v1.0.1 sandbox

## v1.0.1 (2026-06-15)

Release snapshot copied from **`ocr-fix-v1`**. No additional code changes in this tag — baseline is the validated OCR-fix sandbox.

### Included patches (from ocr-fix-v1)

**`booker_legacy.py`**

- `fetch_captcha_with_parallel_prefetch` — DOM-only solve; parallel URL prefetch-only
- `wait_botdetect_ready` — bool return; 3× retry in fetch path
- `fill_captcha_with_hs_guard` — HS stale guard before submit
- `solve_captcha_with_ocr` — SHA256 + unique OCR upload filename

**`app.py`**

- Re-capture DOM bytes before OCR
- `fill_captcha_with_hs_guard` before submit
- Gemini-first on captcha round ≥ 2
- `CAPTCHA_MAX_STYLE_RELOADS` default `0`

### Validation

```powershell
cd python-booker-webhook\sandboxes\v1.0.1
python scripts\run_ocr_validation.py
```

---

## Prior (ocr-fix-v1, 2026-06-14)

See `../ocr-fix-v1/docs/CHANGELOG.md` for original sandbox creation notes.
