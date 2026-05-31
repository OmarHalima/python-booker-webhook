# Migration: v1 Playwright → v2 Turbo (2026-05-30)

## What changed

1. **`app.py`** is now the turbo FastAPI app (was `app_turbo.py`).
2. **`booker_legacy.py`** holds shared helpers + `run_booking_flow` (old full wizard).
3. **`/webhook/booker`** on port **8000** runs turbo browser flow (~4–11s).
4. **`/webhook/booker-legacy`** runs the old ~26s Playwright path.
5. **`/webhook/booker-turbo`** is an alias of `/webhook/booker` on port 8000.
6. **`app_turbo.py`** re-exports from `app` (deprecated, still importable).

## n8n — minimal change

If your workflow already POSTs to `http://host:8000/webhook/booker`:

- **No URL change required.**
- Behavior is now **turbo** (faster). Payload shape unchanged.
- Add `"useSchedulerPost": true` if not already set.
- Optional: `"maxCaptchaRetries": 5` for Gemini on late attempts.

## n8n — if you used port 8001 turbo server

Change HTTP Request node from:

`http://host:8001/webhook/booker-turbo`

to:

`http://host:8000/webhook/booker`

(or keep `/webhook/booker-turbo` on port 8000)

## Rollback

1. POST to `/webhook/booker-legacy` instead of `/webhook/booker`, **or**
2. Restore `docs/turbo-booker-archive/app_v1.py` as `app.py` (manual revert).

## Verify

```bash
python -c "from app import app; print(app.title, app.version)"
python -c "import app_turbo; print(app_turbo.app is app)"
```

Expected: `Austria Booker Webhook 2.0.0` and `True`.

## Clean repo layout (post-migration)

```
python-booker-webhook/
  app.py                 # Primary FastAPI (turbo v2)
  booker_legacy.py       # Shared helpers + legacy wizard
  app_turbo.py           # Deprecated re-export
  captcha_ocr.py
  scheduler_html.py
  examples/payload-booker.json
  scripts/               # scan_slots, run_turbo_payload, etc.
  docs/                  # n8n guides + turbo-booker-archive/
  artifacts/.gitkeep     # Runtime outputs (gitignored)
  test_*.py
```

Test payloads and old run outputs live under `docs/turbo-booker-archive/test-payloads/`.

## Test office vs production (Kairo)

Development and benchmarks use **`KOPENHAGEN` + `CalendarId: 9677702`** as a test double for **Cairo (`KAIRO`)** production. Student payloads use Egyptian applicant fields; only `Office` / `CalendarId` change at go-live.

See [TESTING_KOPENHAGEN.md](../TESTING_KOPENHAGEN.md).
