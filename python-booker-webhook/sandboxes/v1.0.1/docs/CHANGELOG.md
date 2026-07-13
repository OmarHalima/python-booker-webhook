# Changelog — v1.0.1 sandbox

## v1.0.1 (2026-06-16)

### Parallel fetch removed (code-level)

- `captcha_parallel_fetch_enabled()` always returns **`False`** — `CAPTCHA_PARALLEL_FETCH` env is ignored
- Removed `ThreadPoolExecutor` URL prefetch from `fetch_captcha_with_parallel_prefetch`
- Captcha bytes for OCR/solving: **DOM screenshot only** (`dom_screenshot` source)

### Validation (2026-06-16, KOPENHAGEN)

| Step | Result |
|------|--------|
| `compare_captcha_fetch.py --samples 2` | DOM ≠ URL **0/2** (expected; confirms URL fetch must not solve) |
| `run_three_turbo_bookings.py --rounds 2` @ `:8011` | **2/2 SUCCESS**, `captchaParallelUsed: false` |
| `pytest tests/unit tests/integration` (production root) | **44 passed** (sandbox isolated; baseline unchanged) |

Report: `artifacts/ocr-fix-turbo-2round-report.json`

Both turbo successes on **attempt 2** (round-1 captcha fail → Gemini on round 2).

---

## v1.0.1 (2026-06-15)

Release snapshot copied from **`ocr-fix-v1`**.

### Included patches (from ocr-fix-v1)

**`booker_legacy.py`**

- `fetch_captcha_with_parallel_prefetch` — DOM-only solve
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

Original dev sandbox — **archived** 2026-06-16 to [`../../../archive/sandboxes/ocr-fix-v1/`](../../../archive/sandboxes/ocr-fix-v1/).
