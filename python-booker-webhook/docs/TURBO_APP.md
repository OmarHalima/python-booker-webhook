# Turbo booker (primary — `app.py` v2)

Fast booking: **scheduler POST + minimal Playwright submit** (~6–12s; best ~6.4s on attempt 1). Default: `POST /webhook/booker`.

Legacy full Playwright wizard: `POST /webhook/booker-legacy` (~26s).

Archive and full project history: [turbo-booker-archive/README.md](./turbo-booker-archive/README.md).

**Test office:** All benchmarks use `KOPENHAGEN` / `9677702` as a stand-in for Cairo (`KAIRO`) — [TESTING_KOPENHAGEN.md](./TESTING_KOPENHAGEN.md).

**Captcha investigation:** Deep-search findings on BotDetect mechanics, Hs race, style-gate strategy — [CAPTCHA_INVESTIGATION.md](./CAPTCHA_INVESTIGATION.md).

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
| `maxCaptchaRetries` | Default 3; use **5–10** — OCR first, Gemini fallback |

## Captcha configuration (recommended)

Turbo uses **OCR first, Gemini fallback** (`ocr_with_gemini_fallback`). Override with
`TURBO_CAPTCHA_SOLVER` or `CAPTCHA_SOLVER`. The captcha image sent to the solver must be a
**DOM screenshot** of `#Captcha_CaptchaImage` — not HTTP URL fetch. See
[CAPTCHA_INVESTIGATION.md](./CAPTCHA_INVESTIGATION.md).

| Env var | Recommended | Purpose |
|---------|-------------|---------|
| `CAPTCHA_SOLVER` | `ocr_with_gemini_fallback` | OCR first; Gemini on OCR error |
| `TURBO_CAPTCHA_SOLVER` | unset (uses `CAPTCHA_SOLVER`) | Optional turbo-only override |
| `CAPTCHA_PARALLEL_FETCH` | **`false`** | URL fetch returns different image than DOM (0/5 match) |
| `CAPTCHA_OCR_ENHANCE` | `false` | Send raw captcha bytes to Gemini |
| `CAPTCHA_KEYSTROKE_DELAY_MS` | `20` | Floor 20 ms — BotDetect updates `BDC_Hs_Captcha` per keystroke |
| `BOTDETECT_SETTLE_MS` | `150` | Extra wait after captcha image + JS ready |
| `CAPTCHA_MAX_STYLE_RELOADS` | `0` | Style reload not reliable yet; `0` avoids 15s reload timeouts |
| `GEMINI_API_KEY` | required | Fallback when OCR fails |
| `CAPTCHA_OCR_BASE_URL` | required for OCR path | Primary captcha solver |

Regression tool: `python scripts/compare_captcha_fetch.py` — confirms DOM vs URL bytes differ.

Debug artifacts per run: `artifacts/<runId>-turbo/` — captcha PNG, response HTML,
`pre_submit_dom_state` in `debug-ce612b.log` (`captchaText`, `hsTail`, `vcidTail`).

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
| **`browser`** | ~6–12s | **Works** | DOM captcha screenshot → Gemini → keystrokes → Next click |
| `http` | ~3.5s | Usually fails | Pure `requests` POST — BotDetect rejects without browser submit |

Env: `TURBO_SUBMIT_MODE=browser` (default).

## vs Legacy Playwright (`/webhook/booker-legacy`)

| | Legacy | Turbo (`browser`) |
|--|--------|-------------------|
| Navigation | Full wizard or scheduler POST | Scheduler POST only |
| Submit | Next click | Next click (minimal browser) |
| Typical time | ~26s | ~6–12s (best ~6.4s) |

Tampermonkey (manual capture page): [TURBO_INJECTOR.md](./TURBO_INJECTOR.md) — use `submitTurboForm()`, not `fetch`.
