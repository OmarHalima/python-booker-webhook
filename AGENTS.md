# Agent guide — Austrian appointment system

Start here. Read `.cursor/skills/orchestrator/SKILL.md` **every session** to route intent → skills → subagents → rules → MCP.

## Architecture (what runs where)

```
n8n watcher → slots queue → n8n booker → POST /webhook/booker → app.py (turbo)
                                                         ↓
                                              OCR (8001) + Gemini + Playwright → BMEIA
```

| Layer | Path |
|-------|------|
| n8n workflows | `n8n/` |
| Python booker | `python-booker-webhook/` |
| Unit/integration tests | `tests/unit/`, `tests/integration/` |
| Test payload JSON | `fixtures/payloads/` (not `docs/`, not root) |
| Dev sandbox (OCR) | `sandboxes/v1.0.1/` (port 8011) |
| Runtime debug output | `artifacts/<runId>/` (gitignored) |

**Modules:** `app.py` (FastAPI routes), `booker_legacy.py` (Playwright core — don't grow), `captcha_ocr.py` (OCR client), `scheduler_html.py` (HTML parsing).

## Debug workflow (evidence-first)

For captcha/BotDetect/Playwright/flaky-form bugs, invoke `deep-web-debugger` subagent (`.cursor/agents/deep-web-debugger.md`). Also `captcha-investigator` agent for OCR/Gemini-specific diagnosis.

**Standard approach:**
1. **Intake** — freeze observed vs expected, env, artifacts, logs
2. **Timeline** — order: navigation → DOM → network → response → new DOM
3. **Console** — CSP, CORS, BotDetect JS errors, `net::ERR_*`
4. **Network** — hidden field values at solve vs submit (captcha token/VCID must match), cookies, redirects
5. **HTML** — validation blocks (`.validation-summary-errors`), form action/method, captcha hidden inputs
6. **Web research** — search quoted error strings, known BotDetect/ASP.NET WebForms/Playwright issues
7. **Ranked hypotheses** — likelihood + falsification test
8. **Root cause** — one paragraph: what broke, why the UI message misleads, exact mechanism
9. **Minimal fix** — concrete patch + verification + guardrails

**Pre-read before captcha/VPS/n8n changes:** `python-booker-webhook/docs/AGENT_HANDOFF.md`

## Captcha pipeline (solver order)

`CAPTCHA_SOLVER=ocr_with_gemini_fallback` (default): OCR first → if fail, Gemini. Rounds ≥2 start with Gemini. Style-gate: `CAPTCHA_MAX_STYLE_RELOADS`, `CAPTCHA_MIN_STYLE`.

DOM-only capture (`CAPTCHA_PARALLEL_FETCH=false`). VPS still on `gemini_first` — see AGENT_HANDOFF.

**Result types from booker:** SUCCESS, CAPTCHA_FAIL, SLOT_TAKEN, ALREADY_BOOKED, OTHER_FAIL, BOOKING_SERVER_BUSY.

## Commands

```powershell
cd python-booker-webhook

# Tests (baseline: 77, unit + integration)
pytest tests/unit tests/integration -q

# Run server
uvicorn app:app --host 0.0.0.0 --port 8000

# OCR check (manual)
python scripts/run_ocr_captcha_api.py

# Turbo payload (dev — KOPENHAGEN)
python scripts/run_turbo_payload.py fixtures/payloads/payload-booker.json

# Captcha harvest for finetune dataset
python scripts/harvest_captcha_images.py --count 5000 --method http
```

## Hard rules

- **Tests** only in `tests/unit|integration|e2e/` — never at package root
- **No** ephemeral outputs committed (`*-scan.json`, `local-fix-*`, `artifacts/`)
- **No** duplicate Python in `sandboxes/` — sandboxes = scripts + workflows only
- `uvicorn app:app` entry stays — don't break
- KOPENHAGEN for dev, KAIRO constants for production
- Extract new pure logic into modules — don't grow `booker_legacy.py`
- **Phase 4** (not done): extract `booker/` package from `booker_legacy.py`

## Debug artifacts

When `captureArtifacts: true`, writes to `artifacts/<runId>-turbo/`:
- `attempt_N_captcha.png` — captcha image fed to solver
- `attempt_N_response.html` — server response HTML
- `01_capture_form.html` — capture form before submit
- `solved_captchas/` — successful solves for training dataset

First stop for "why did it fail?": read `attempt_N_response.html` for validation messages or `classify_post_submit_html()` result.
