# Changelog — ocr-fix-v1 sandbox

## 2026-06-14

### Created from production + lock-integration OCR patches

**Root cause addressed:** parallel URL captcha fetch returned a different image than `#Captcha_CaptchaImage`, so OCR solved the wrong bytes.

### `booker_legacy.py`

- `fetch_captcha_with_parallel_prefetch` — DOM-only solve path; parallel URL is prefetch-only.
- `wait_botdetect_ready` — returns `bool`; 3× retry in fetch path.
- `fill_captcha_with_hs_guard` — accepts ready when `#CaptchaText` matches OCR and HS is present (BotDetect may not change HS tail on fill).
- `solve_captcha_with_ocr` — SHA256 + unique OCR upload filename.
- `PLAYWRIGHT_REUSE_BROWSER` default **`true`** (production speed; safe for in-process multi-booking).

### `app.py`

- Re-capture DOM bytes before OCR (`attempt_XX_captcha_ocr.png`).
- `fill_captcha_with_hs_guard` before submit; skip submit on HS failure.
- `captcha_round` tracking; Gemini-first on round ≥ 2.
- `CAPTCHA_MAX_STYLE_RELOADS` default `0`.

### Validation scripts

- `compare_captcha_fetch.py` — expect dom==url 0/N.
- `run_three_turbo_bookings.py` — 3× inline turbo with browser reuse.
- `run_ocr_validation.py` — runs both steps.
