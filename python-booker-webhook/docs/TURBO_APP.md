# Turbo booker (primary — `app.py` v2)

Fast booking: **scheduler POST + minimal Playwright submit** (~4–11s). This is now the default on `POST /webhook/booker`.

Legacy full Playwright wizard: `POST /webhook/booker-legacy` (~26s).

Archive and full project history: [turbo-booker-archive/README.md](./turbo-booker-archive/README.md).

**Test office:** All benchmarks use `KOPENHAGEN` / `9677702` as a stand-in for Cairo (`KAIRO`) — [TESTING_KOPENHAGEN.md](./TESTING_KOPENHAGEN.md).

## Run server (port 8000)

```bash
cd python-booker-webhook
.venv\Scripts\uvicorn.exe app:app --host 0.0.0.0 --port 8000
```

Deprecated alias module (same app):

```bash
.venv\Scripts\uvicorn.exe app_turbo:app --host 0.0.0.0 --port 8000
```

## Endpoints

| Method | Path | Flow |
|--------|------|------|
| GET | `/health` | Status |
| POST | `/webhook/booker` | **Turbo browser** (default) |
| POST | `/webhook/booker-turbo` | Alias of `/webhook/booker` |
| POST | `/webhook/booker-legacy` | Full Playwright wizard |

Same required fields as before, plus optional:

| Field | Purpose |
|-------|---------|
| `submitMode` | `browser` (default, works) or `http` (lab — often CAPTCHA_FAIL) |
| `captchaText` | Skip OCR — use your pre-solved captcha |
| `captureFormHtml` | Skip scheduler POST — paste capture page HTML from browser |
| `useSchedulerPost` | `true` — POST Monday+Start from n8n (recommended) |
| `maxCaptchaRetries` | Default 3; use 5 for Gemini on attempts 4–5 |

## Scan for fresh slots

```bash
.venv\Scripts\python.exe scripts\scan_slots.py --office KOPENHAGEN --calendar-id 9677702 --run-id slot-scan-fresh
```

For Cairo production scanning: `--office KAIRO` and the mission `CalendarId`.

Returns `sessionCookie`, `currentMonday`, `slots[]` in `artifacts/<runId>/scan_result.json`.

## CLI (no server)

```bash
.venv\Scripts\python.exe scripts\run_turbo_payload.py examples\payload-booker.json
```

## Submit modes

| Mode | Time | CAPTCHA | How |
|------|------|---------|-----|
| **`browser`** | ~4–11s | **Works** | Playwright loads capture form, human captcha keystrokes, Next click |
| `http` | ~3.5s | Usually fails | Pure `requests` POST — BotDetect rejects without browser submit |

Env: `TURBO_SUBMIT_MODE=browser` (default).

## vs Legacy Playwright (`/webhook/booker-legacy`)

| | Legacy | Turbo (`browser`) |
|--|--------|-------------------|
| Navigation | Full wizard or scheduler POST | Scheduler POST only |
| Submit | Next click | Next click (minimal browser) |
| Typical time | ~26s | ~4–11s |

Tampermonkey (manual capture page): [TURBO_INJECTOR.md](./TURBO_INJECTOR.md) — use `submitTurboForm()`, not `fetch`.
