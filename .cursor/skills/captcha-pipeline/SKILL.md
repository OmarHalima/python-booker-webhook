---
name: captcha-pipeline
description: Debugs Austrian booker OCR, Gemini, and BotDetect captcha flow. Use for CAPTCHA_FAIL, solver env vars, captcha_ocr.py, turbo captcha in app.py, artifact desync, or CAPTCHA_INVESTIGATION work. Routes to captcha-investigator agent for deep dives.
---

# Captcha pipeline

## When to use

- `resultType: CAPTCHA_FAIL` in booker response
- OCR returns wrong text; Gemini not invoked
- Artifact PNG does not match OCR answer
- Changing `CAPTCHA_SOLVER`, `TURBO_CAPTCHA_SOLVER`, `CAPTCHA_PARALLEL_FETCH`

## Read first

1. `python-booker-webhook/docs/AGENT_HANDOFF.md` (captcha sections)
2. `python-booker-webhook/docs/CAPTCHA_INVESTIGATION.md`

## Key modules

| Module | Role |
|--------|------|
| `captcha_ocr.py` | VPS OCR HTTP client |
| `app.py` | `_solve_turbo_captcha`, solver wiring |
| `booker_legacy.py` | DOM capture, BotDetect wait |

## Solver modes

| Env | Behavior |
|-----|----------|
| `ocr_with_gemini_fallback` | Gemini only if OCR **throws** (code default) |
| `gemini_first` | VPS production recommendation |
| `TURBO_CAPTCHA_SOLVER` | Per-request override |

## Debug commands

```bash
cd python-booker-webhook
pytest tests/unit/test_ocr_captcha_unit.py tests/unit/test_turbo_captcha_solver.py -q
python scripts/run_ocr_captcha_api.py
python scripts/run_ocr_captcha_api.py artifacts/<runId>/attempt_01_captcha.png
python scripts/debug/compare_captcha_fetch.py   # if present in scripts/debug/
```

## Fix patterns (minimal)

- Instrument: SHA256 of captcha bytes at capture + OCR send
- Unique OCR filename: `captcha_{runId}_{attempt}.png`
- Gemini after first CAPTCHA_FAIL (product decision — document in AGENT_HANDOFF)
- Never set `CAPTCHA_PARALLEL_FETCH=true` without verify script pass

## Delegate

- DOM/network timeline → `deep-web-debugger` agent or `captcha-investigator` agent
- VPS OCR service down → `vps-booker-ops` skill
