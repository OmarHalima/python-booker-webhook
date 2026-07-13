---
name: booker-e2e-kopenhagen
description: Runs end-to-end Austrian booker tests using KOPENHAGEN as Cairo stand-in. Use for scan_slots, turbo payload runs, slot booking benchmarks, Playwright e2e, and live sessionCookie flows.
---

# E2E testing (KOPENHAGEN stand-in)

## When to use

- Run a real booking attempt locally or against VPS
- Refresh payload slots from live scheduler
- Benchmark turbo path (~6-12s)
- Debug slot mismatch / calendar ID issues

## Rule

Scanner and booker must share **same** `Office`, `CalendarId`, `sessionCookie` chain.

| Field | Test value |
|-------|------------|
| Office | `KOPENHAGEN` |
| CalendarId | `9677702` |

Full guide: `docs/TESTING_KOPENHAGEN.md`

## Workflow

### 1. Scan slots

```bash
cd python-booker-webhook
.venv\Scripts\python.exe scripts\scan_slots.py --office KOPENHAGEN --calendar-id 9677702
```

Copy `sessionCookie`, `currentMonday`, `currentSlot` into payload.

### 2. Update payload

Edit `fixtures/payloads/payload-booker.json` with fresh cookie/slot fields.

### 3. Run turbo (inline, no server)

```bash
.venv\Scripts\python.exe scripts\run_turbo_payload.py fixtures\payloads\payload-booker.json
```

### 4. Or POST to running server

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
# POST to http://localhost:8000/webhook/booker
```

Optional flags in payload: `captureArtifacts`, `maxCaptchaRetries`, `useSchedulerPost`

## E2E pytest (Playwright + live session)

```bash
pytest tests/e2e -q   # manual; needs live session
```

## Artifacts

Output: `artifacts/<runId>/` (gitignored). Use for captcha-debug with `captcha-pipeline` skill.

## Production switch

Use `fixtures/payloads/payload-kairo-production.json` — change n8n scanner to `KAIRO` only when going live.

## Delegate

- Captcha failures → `captcha-pipeline`
- DOM/browser issues → `deep-web-debugger`
