<!--
  Working notes — Austrian Appointment System (python-booker-webhook)
  Captured: 2026-07-10. Status: EXPLORATORY (no code changed, no spec created yet).
  Purpose: Resume point for the booking-speed / reliability work discussed this session.
-->
# Booker Review & Speed/Reliability Notes (2026-07-10)

## 0. Context
Exploratory review of `python-booker-webhook` (FastAPI + Playwright + OCR/Gemini
captcha). User is tuning for **booking speed** on VPS prod and hit a production
error. Nothing was implemented; this is a findings/resume doc.

---

## 1. Exploratory code-review findings (from `explore` agent)

- `booker_legacy.py` = 4072-line god module, 100+ funcs, ~100 `except Exception`.
  AGENTS.md "Phase 4" (extract `booker/` package) NOT done.
- **Three duplicate booking loops** re-implement retry/classify/success:
  `app.py:190` (browser), `app.py:813` (http), `booker_legacy.py:3461` (legacy).
- **Sandbox duplication** violates AGENTS.md hard rule: `sandboxes/v1.0.1/python/`
  holds stale copies of all 5 core modules; `docs/turbo-booker-archive/` is dead
  history (app_v1.py 3574 lines). Drift risk.
- **Dead "parallel" captcha code**: `captcha_parallel_fetch_enabled()` hardcoded
  `return False`, yet `ThreadPoolExecutor` + `captchaParallel*` timings exist.
- Misleading `dlog(..., "app.py:...")` calls live inside `booker_legacy.py`
  (wrong `location` in debug logs). Hardcoded `sessionId="ce612b"` in logging.
- Committed `tests/__pycache__/*.pyc` (AGENTS.md forbids ephemeral outputs).
- Testing gaps: no test exercises the real booking loop / a real submit response;
  `is_success_html` tests lock in weak behavior.

### Prioritized enhancements
- **P0** Make `is_success_html` a *positive* match (confirmation marker), not a
  negative check (see §2.1). Add negative-case tests.
- **P0** Distinguish `CAPTCHA_FAIL` from form-validation re-render before retry;
  surface `OTHER_FAIL`/`FORM_INVALID`.
- **P1** Extract `booker/` package; collapse 3 loops into one parameterized
  `run_booking_flow(payload, mode)`.
- **P1** Delete sandbox duplicate core modules + `docs/turbo-booker-archive/`;
  import real package.
- **P1** Typed payload model (pydantic) validated once at entry.
- **P2** Structured logging w/ real `location` + correlation ID; fail-fast config
  validation; idempotent `run_id` across workers; remove dead parallel-captcha code.
- **P3** Dry-run mode; retry+backoff/circuit-breaker for OCR & Gemini;
  fix `post_select_slot` `PersonCount` hardcoded `"1"` (`booker_legacy.py:767`).

---

## 2. Production error root cause (user-supplied JSON, record id 4, ELENANI/YOUSSEF)

**Symptom**: n8n shows `AxiosError`, `status: 500`, `code: ERR_BAD_RESPONSE`, msg
`APIRequestContext.post: Timeout 20000ms exceeded → POST https://appointment.bmeia.gv.at/HomeWeb/Scheduler`.

**Chain (verified in code)**:
1. Booker runs turbo flow; at slot-select it calls
   `post_select_slot` → `context.request.post(SCHEDULER_URL, ...)` at
   `booker_legacy.py:773`. `context.request.post` IS Playwright's
   `APIRequestContext.post` — that exact string is Playwright's runtime error.
   No per-call `timeout` ⇒ uses context default timeout.
2. BMEIA `/HomeWeb/Scheduler` did not respond within timeout ⇒ Playwright
   `TimeoutError`.
3. FastAPI catches it: `except Exception → raise HTTPException(500, detail=str(exc))`
   at `app.py:1079-1080` (and `:1101-1102` for legacy). Response body =
   `{"detail": "APIRequestContext.post: Timeout 20000ms exceeded. ..."}`.
4. n8n HTTP node gets 500 ⇒ axios wraps as `AxiosError`. n8n/axios are just the
   messenger; the real signal is the nested Playwright timeout.

**Conclusion**: Booking died at *slot-selection* (scheduler POST, body len 141),
never reached captcha/submit. BMEIA was slow/unreachable. Code's own guidance at
`booker_legacy.py:2885-2889` names this ("several browsers hit the site at once…
use BOOKING_USE_GLOBAL_LOCK=true"). `post_select_slot` has **no retry/backoff**.
Side note: that record's `Email` had a trailing `"\n"` — data hygiene, unrelated.

---

## 3. Speed-tuning .env review (user pasted VPS prod .env)

**User goal**: booking was "staying ~1 min and not starting", so they lowered
timeouts to fail fast and rely on n8n retry-on-fail (3 retries) to start a fresh
attempt.

**Settings judged GOOD for speed** (keep):
- `CAPTCHA_SOLVER=ocr_with_gemini_fallback`, `CAPTCHA_MIN_STYLE=easy`,
  `CAPTCHA_MAX_STYLE_RELOADS=0` (no reload loops), `CAPTCHA_PARALLEL_FETCH=false`,
  `CAPTCHA_OCR_ENHANCE=false`, `CAPTCHA_KEYSTROKE_DELAY_MS=20`,
  `BOTDETECT_SETTLE_MS=150`.
- `TURBO_SUBMIT_MODE=browser` (avoids fragile HTTP submit path).
- `PLAYWRIGHT_WAIT_NETWORK_IDLE=false`, `PLAYWRIGHT_SLOW_MO=0`.
- `BOOKING_USE_GLOBAL_LOCK=true` + `MAX_CONCURRENT_BOOKINGS=1` (serialize hits).

**🔴 Critical issue — `LANDING_GOTO_TIMEOUT_MS=5000` backfires**:
- `_configure_playwright_context` sets `context.set_default_timeout(nav_ms)` from
  `landing_goto_timeout_ms()` = `LANDING_GOTO_TIMEOUT_MS` (`booker_legacy.py:2893-2896`).
- That default bounds **every** Playwright op incl. the scheduler POST that already
  timed out at 20s ⇒ now it dies at **5s** (4× more likely to fail). The var is
  COUPLED: nav speed AND the booking-critical POST share one timeout.
- Band-aid: raise to ~20000. Proper fix: explicit per-call `timeout` (10–15s) on
  `context.request.post(SCHEDULER_URL, ..., timeout=15000)` + retry/backoff,
  decoupled from nav timeout.

**Tradeoffs noted**:
- `BOOKING_DEFAULT_SCHEDULER_POST=false` is a no-op here — scheduler POST still
  triggers when n8n payload sets `useSchedulerPost:true`
  (`_use_scheduler_post_navigation`, `booker_legacy.py:531`). Don't rely on it.
- `GEMINI_FILL_FORM=true` adds ~1–3s/booking (extra Gemini round-trip). Trade speed
  vs KAIRO correctness.
- `PLAYWRIGHT_REUSE_BROWSER=false` — fresh browser each run (safer, slower launch).
- Tight nav timeouts (`FORM_LOAD_TIMEOUT_MS=2000`, `NAV_DOM_CONTENT_LOADED_MS=5000`,
  `SUBMIT_NEXT_NAVIGATION_MS=5000`) fast on good days, can false-timeout the final
  submit on slow BMEIA.

**🔴 Security flag**: user pasted a LIVE `GEMINI_API_KEY` (`AIzaSyD…`) in chat.
Treat as exposed; rotate in Google Cloud. `.env` is gitignored locally (correct).

---

## 4. "Fail-fast + n8n retry" strategy discussion (user's approach)

User's pattern: 5s timeout → booker returns 500 fast → n8n retry-on-fail (3 retries)
→ fresh booker attempt. **Agreed the pattern is sound** (fresh browser/session often
clears a stuck BMEIA connection). Concerns raised:

1. **Coupling** — 5000ms hits all Playwright ops, not just the stuck network call
   ⇒ more false failures on slow-but-valid steps (see §3).
2. **Retry cookie freshness (KEY OPEN QUESTION)** — strategy only works if n8n
   re-fetches a **fresh `sessionCookie`/ASP.NET_SessionId per retry**. If n8n re-sends
   the same stale cookie 3×, all 3 fail identically (the error's
   `ASP.NET_SessionId=wv2440ee2fwqbe0t0hhqaab4` was a single session). MUST VERIFY.
3. **Immediate vs backed-off retries** — 3 instant retries hammer a slow server and
   risk throttling (contradicts `BOOKING_USE_GLOBAL_LOCK`). `BOOKING_BUSY_RETRY_AFTER_SEC=60`
   only fires when booker returns `BOOKING_SERVER_BUSY` — but timeout currently
   returns **500**, so n8n retries instantly. Suggest: return `BOOKING_SERVER_BUSY`
   on timeout, or add backoff between n8n retries.
4. **Root-cause unknown for the ~1-min hang** — if the 60s stall was on a step NOT
   bounded by `LANDING_GOTO_TIMEOUT_MS`, lowering to 5s won't fix it. Need to confirm
   which stage hangs (check `artifacts/<runId>/` timeline / which wait).

**Suggested tuning (agreed direction)**:
- Keep `LANDING_GOTO_TIMEOUT_MS=5000` for nav.
- Add explicit `timeout` (10–15s) + retry/backoff on scheduler POST & submit (decoupled).
- Surface timeout as `BOOKING_SERVER_BUSY` (not 500) so n8n can apply its own wait.
- Verify n8n refreshes session per retry.
- Prefer 3 retries *with* short backoff over 3 instant retries.

---

## 5. Open items to resume
- [ ] Confirm whether n8n re-fetches a fresh sessionCookie on each retry (decides if
      the whole fail-fast+retry strategy is effective).
- [ ] Identify which stage hangs ~1 min (artifacts timeline) to confirm 5s helps.
- [ ] Spec/plan: explicit scheduler-POST/submit timeouts + retry/backoff + return
      `BOOKING_SERVER_BUSY` on timeout (ties §2 + §3 + §4). Small, high-impact.
- [ ] Spec/plan (later): P0 `is_success_html` positive match + validation-error
      vs captcha-fail distinction (§1).
- [ ] Rotate the leaked `GEMINI_API_KEY`.
