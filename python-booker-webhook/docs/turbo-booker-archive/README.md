# Turbo Booker Project Archive

Complete record of the **turbo booker** arc: slot mismatch fixes, scheduler POST fast path, Tampermonkey injector, 3-slot batch tests, repo cleanup, and promotion of turbo to the primary `app.py` entry point (2026-05-30).

## Testing: KOPENHAGEN stand-in for Kairo

**All live tests in this project use Copenhagen, not Cairo.**

| | Test | Production |
|---|------|------------|
| `Office` | `KOPENHAGEN` | `KAIRO` |
| `CalendarId` | `9677702` | e.g. `44281520` (Bachelor student queue) |
| Student data | Egyptian dummy applicants | Real students |

The pipeline (scan → scheduler POST → turbo book) is the same; only mission fields change for go-live. Student payloads intentionally use Cairo/Egypt field values while `Office` stays KOPENHAGEN so form-fill matches production without using Cairo slots.

Full guide: [TESTING_KOPENHAGEN.md](../TESTING_KOPENHAGEN.md).

## Repo cleanup (migration-ready layout)

Before deploy, the repo was cleaned:

- **`artifacts/`** wiped (runtime only; `.gitkeep` + `.gitignore`)
- **Test payloads** moved to `docs/turbo-booker-archive/test-payloads/`
- **`examples/payload-booker.json`** — canonical KOPENHAGEN test template
- **`examples/payload-kairo-production.json`** — Cairo production template
- Root clutter removed (`debug-ce612b.log`, `*-result.json`, dev JSON at root)
- **`.gitignore`** added for `.env`, artifacts, logs

## Timeline

| Phase | What | Outcome |
|-------|------|---------|
| v1 Playwright | Full wizard in `app.py` (~26s) | Stable but slow; slot mismatch bugs |
| Slot mismatch fix | `useSchedulerPost`, no silent fallback | Root cause documented; fail-fast on wrong week |
| Turbo v0.2 | `app_turbo.py`: scheduler POST + minimal browser submit | ~4–11s; **browser submit works**, HTTP fails |
| Tampermonkey | `tampermonkey-turbo-injector.user.js` | Manual R&D on capture page; form submit only |
| 3-slot tests | `run_turbo_3slot_batch.py`, `run_turbo_clean_333.py` | 1/3 first batch; 2/3 with Gemini late retries |
| Promotion | Turbo → `app.py` v2; legacy → `booker_legacy.py` | `/webhook/booker` is turbo by default |
| Repo cleanup | Artifacts wiped, payloads archived, `.gitignore` | Migration-ready tree |
| Docs | `TESTING_KOPENHAGEN.md`, examples split test/prod | KOPENHAGEN = Kairo test double documented |

## Architecture (after promotion)

```
n8n slot detect
    │  sessionCookie, currentMonday, currentSlot (slotValue → currentSlot)
    ▼
POST /webhook/booker  (app.py v2 — turbo, submitMode=browser)
    │
    ├─ booker_legacy._booking_execution_slot (global lock / semaphore)
    ├─ navigate_to_capture_form_via_scheduler_post (Playwright + POST /HomeWeb/Scheduler)
    ├─ fill_form_fields + OCR captcha (Gemini on attempts 4–5)
    └─ in-browser Next click (BotDetect-safe)
    ▼
SUCCESS / CAPTCHA_FAIL / SLOT_TAKEN / …

Legacy path (unchanged behavior, ~26s):
POST /webhook/booker-legacy → booker_legacy.run_booking_flow
```

### Module layout

| File | Role |
|------|------|
| `app.py` | **Primary** FastAPI app (turbo v2, port 8000) |
| `booker_legacy.py` | Shared Playwright helpers + full wizard `run_booking_flow` |
| `app_turbo.py` | Deprecated re-export of `app` (backward compat) |
| `captcha_ocr.py` | VPS OCR client |
| `docs/turbo-booker-archive/app_v1.py` | Frozen snapshot of original `app.py` before promotion |
| `docs/turbo-booker-archive/app_turbo_v0.2.py` | Frozen snapshot of turbo before merge |

## Slot mismatch — root cause

See also [SLOT_MISMATCH_INVESTIGATION.md](../SLOT_MISMATCH_INVESTIGATION.md).

**Problem:** n8n detected slot on week A; reused `sessionCookie` + full Playwright wizard showed week B; code silently picked **first radio** → wrong `StartTime`.

**Fixes in `booker_legacy.py`:**

1. `BOOKING_ALLOW_SLOT_FALLBACK=false` (default) — fail if `currentSlot` not in DOM
2. `useSchedulerPost: true` — POST `/HomeWeb/Scheduler` with exact `Monday` + `Start` (same as n8n)
3. `_verify_start_time` / `start_time_dom_vs_payload` logging — fail on mismatch

Turbo does **not** auto-fix mismatch; it submits whatever `#StartTime` is on the capture form. Correct scheduler POST + fresh cookie is required.

## Turbo vs Playwright vs HTTP

| Path | Navigation | Submit | Typical time | CAPTCHA |
|------|------------|--------|--------------|---------|
| **Turbo browser** (`/webhook/booker`) | Scheduler POST only | Playwright Next click | **4–11s** | Works |
| Turbo HTTP (`submitMode: http`) | Scheduler POST (requests) | requests POST | ~3.5s | Usually **CAPTCHA_FAIL** |
| Legacy (`/webhook/booker-legacy`) | Full wizard or scheduler POST | Playwright Next click | ~26s | Works |
| Tampermonkey `submitTurboForm()` | Manual / n8n tab | DOM form submit | ~4s | Works |
| Tampermonkey `submitTurbo()` fetch | Manual | fetch POST | ~4s | **CAPTCHA_FAIL** |

**BotDetect** requires browser-like captcha keystrokes and form submit — not bare HTTP/fetch.

### Gemini late captcha (turbo)

On attempts **4 and 5** (when `maxCaptchaRetries` ≥ 4), turbo prefers `solve_captcha_with_gemini` before OCR fallback. Implemented in `app.py:_solve_turbo_captcha`.

## Timing benchmarks (live, 2026-05-30)

### Single slot

| Test | Slot | Result | Time |
|------|------|--------|------|
| Turbo browser | 6/11/2026 10:30 AM | SUCCESS (attempt 3) | ~10.6s |
| Playwright legacy | 6/10/2026 3:00 PM | SUCCESS | ~29s |
| Tampermonkey form | 6/9/2026 10:30 AM | SUCCESS | ~4.1s |

### 3-slot batch (`turbo-3slot-batch`)

| Slot | Student | Result | Wall ms |
|------|---------|--------|---------|
| 6/8 11:30 AM | ALICE | SUCCESS | 5786 |
| 6/9 11:00 AM | BOB | CAPTCHA_FAIL | 8960 |
| 6/9 11:30 AM | CAROL | CAPTCHA_FAIL | 9570 |

Batch: 24.3s total vs ~81s estimated Playwright (~3.3× faster).

### 3-slot clean v2 (`turbo-clean-333-v2`, maxCaptchaRetries=5)

| Slot | Result | Time |
|------|--------|------|
| 6/10 2:00 PM | ALREADY_BOOKED | 18.0s |
| 6/10 2:30 PM | SUCCESS (attempt 4, Gemini) | 23.6s |
| 6/11 11:00 AM | SUCCESS (attempt 1) | 6.5s |

2/3 booked in 51s batch wall time.

## n8n integration

### Required payload fields

Same as before:

- `sessionCookie`, `Language`, `Office`, `CalendarId`
- `currentMonday`, `currentSlot`
- `student` object

**Test:** `Office: KOPENHAGEN`, `CalendarId: 9677702`. **Production Cairo:** `Office: KAIRO` + mission calendar ID — see [TESTING_KOPENHAGEN.md](../TESTING_KOPENHAGEN.md).

### Recommended additions

```json
{
  "useSchedulerPost": true,
  "maxCaptchaRetries": 5,
  "submitMode": "browser"
}
```

### Field mapping

| n8n / workflow | Webhook field | Notes |
|----------------|---------------|-------|
| Slot detect `slotValue` | `currentSlot` | Must match `#StartTime` after scheduler POST |
| Week Monday from scan | `currentMonday` | Same as scan POST |
| ASP.NET session | `sessionCookie` | Fresh per parallel student; rescan if stale |
| Run label | `runId` | Artifacts under `artifacts/<runId>-turbo/` |

### Webhook URLs (breaking change note)

| Endpoint | Before | After |
|----------|--------|-------|
| `POST :8000/webhook/booker` | Full Playwright (~26s) | **Turbo browser (~4–11s)** |
| `POST :8000/webhook/booker-legacy` | — | Full Playwright (old behavior) |
| `POST :8001/webhook/booker-turbo` | Turbo on port 8001 | **Alias** on `:8000/webhook/booker-turbo` |

**n8n action:** Point booking HTTP node to `http://<host>:8000/webhook/booker` — same path, faster flow. No payload change required if you already send `useSchedulerPost: true`. To keep old Playwright wizard, use `/webhook/booker-legacy`.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/scan_slots.py` | Scan week for open slots → `artifacts/<runId>/scan_result.json` |
| `scripts/run_turbo_payload.py` | CLI inline turbo or POST to webhook URL |
| `scripts/run_turbo_clean_333.py` | 3-slot clean test with configurable retries |
| `scripts/run_turbo_3slot_batch.py` | 3-slot batch benchmark |
| `scripts/run_turbo_inject_test.py` | Reproduce Tampermonkey fetch vs form_submit |
| `scripts/run_post_nav_checklist.py` | Post-navigation artifact checklist (legacy) |
| `scripts/debug_nav_browser.py` | Legacy nav debugging |
| `scripts/debug_full_nav.py` | Legacy full nav trace |

## Tampermonkey injector

See [TURBO_INJECTOR.md](../TURBO_INJECTOR.md) and [tampermonkey-turbo-injector.user.js](../tampermonkey-turbo-injector.user.js).

Use `bmeiaTurbo.submitTurboForm(captchaText)` — **not** `submitTurbo()` fetch.

## Environment variables

See `.env.example`. Turbo-relevant:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TURBO_SUBMIT_MODE` | `browser` | `browser` or `http` |
| `BOOKING_ALLOW_SLOT_FALLBACK` | `false` | Fail on slot mismatch |
| `BOOKING_DEFAULT_SCHEDULER_POST` | `false` | Legacy default; turbo always uses scheduler POST in browser mode |
| `CAPTCHA_SOLVER` | `ocr_with_gemini_fallback` | OCR primary, Gemini fallback |
| `MAX_CONCURRENT_BOOKINGS` | `1` | Per-worker slot limit |
| `BOOKING_USE_GLOBAL_LOCK` | `true` | One booking at a time on VPS |

## How to run

### Main app (turbo)

```bash
cd python-booker-webhook
.venv\Scripts\uvicorn.exe app:app --host 0.0.0.0 --port 8000
```

### Legacy Playwright only

```bash
curl -X POST http://127.0.0.1:8000/webhook/booker-legacy -H "Content-Type: application/json" -d @payload.json
```

### Scan slots

```bash
.venv\Scripts\python.exe scripts\scan_slots.py --run-id slot-scan-fresh
```

### Inline turbo (no server)

```bash
.venv\Scripts\python.exe scripts\run_turbo_payload.py path\to\payload.json
```

## Artifacts

Turbo runs save under `artifacts/<runId>-turbo/`:

- `01_capture_form.html` — after scheduler POST
- `attempt_NN_captcha.png` — captcha image
- `attempt_NN_response.html` — post-submit page

Slot scans: `artifacts/<runId>/scan_result.json`, `week_1.html`.

## Migration checklist

- [ ] Update n8n HTTP node URL to port **8000** (not 8001) if you used separate turbo server
- [ ] Confirm `currentSlot` maps from n8n `slotValue`
- [ ] Set `useSchedulerPost: true` in booking payload
- [ ] Set `maxCaptchaRetries: 5` for Gemini late attempts
- [ ] Keep serial booking or handle 503 `BOOKING_SERVER_BUSY`
- [ ] Test one live booking on `/webhook/booker`
- [ ] Keep `/webhook/booker-legacy` available for rollback

## File index (this archive)

| File | Description |
|------|-------------|
| `app_v1.py` | Original monolithic `app.py` (FastAPI + full wizard + helpers) |
| `app_turbo_v0.2.py` | Standalone turbo before promotion (`import app as booker`) |
| `README.md` | This document |
| `MIGRATION.md` | Short migration guide |
| `test-payloads/` | Historical dev JSON payloads (KOPENHAGEN tests) |
| `AGENT_PROJECT_DOCUMENTATION.md` | Earlier agent project notes |

## Related docs

- [TESTING_KOPENHAGEN.md](../TESTING_KOPENHAGEN.md) — **KOPENHAGEN test office vs KAIRO production**
- [TURBO_APP.md](../TURBO_APP.md) — operational turbo reference
- [TURBO_INJECTOR.md](../TURBO_INJECTOR.md) — Tampermonkey manual path
- [SLOT_MISMATCH_INVESTIGATION.md](../SLOT_MISMATCH_INVESTIGATION.md) — wrong-slot root cause
- [n8n-scheduler-workflow.md](../n8n-scheduler-workflow.md) — scheduler workflow
- [PARALLEL_BOOKING.md](../PARALLEL_BOOKING.md) — concurrency / 503 behavior
