# Sandbox lock-integration-v1

Isolated copy of Python booker + n8n workflows. **Does not modify** repo root `app.py`, `current booker.json`, or live n8n workflow `booker` (id `V2giHI6bqFpWq8ff`).

## Layout

```
lock-integration-v1/
  python/           # copy of turbo booker (run on port 8010)
  workflows/        # booker.json (source), booker-lock-v1.json (patched)
  scripts/          # patch, push, dev helpers
  artifacts/        # runtime artifacts for this sandbox
  .env.n8n.local    # n8n + ngrok secrets (gitignored)
  docs/             # n8n-workflow-ids.json after push
```

## Scripts

| Script | Purpose |
|--------|---------|
| `patch_booker_lock.py` | Build `workflows/booker-lock-v1.json` (lock + loop-back + early unlock paths) |
| `n8n_push_booker_lock.py` | Push/update sandbox workflow on n8n (never touches live booker) |
| `n8n_push_watcher_sandbox.py` | Push KOPENHAGEN sandbox watcher |
| `patch_watcher_kopenhagen.py` | Patch watcher for KOPENHAGEN + `booker-lock-sandbox` webhook |
| `start_ngrok_sandbox.py` | Start or detect ngrok on port 8010; writes `BOOKER_PUBLIC_URL` |
| `load_workflow_ids.py` | Read booker/watcher IDs from `docs/n8n-workflow-ids.json` |
| `run_kopenhagen_e2e_test.py` | Scan KOPENHAGEN, seed dummy applicant, n8n webhook E2E |
| `run_full_watcher_booker_test.py` | Single tick: watcher scan → booker → turbo (1 applicant) |
| `run_five_watcher_bookings.py` | Seed 5 PENDING; watcher→booker n8n path (`--watch-sec N`) |
| `run_five_bookings.py` | 5× direct turbo (local scan each round; baseline comparison) |
| `verify_no_slots_unlock.py` | Regression: empty-slot webhook must release booker lock |
| `watcher_30s_lock_test.py` | Arm watcher 30s, inspect executions, `clear_lock()` helper |
| `run_lock_integration_tests.py` | Lock smoke tests (T2 busy skip) |
| `loop_kopenhagen_full_test.py` | Wrapper for `/loop` ticks |

## KOPENHAGEN watcher + booker (full E2E)

Sandbox watcher `watcher-kopenhagen-sandbox-v1` (`8o4tnLxWbWKRgfTq`):
- Scans **KOPENHAGEN** / `9677702` every **5s** (Init Week State → current Monday → BMEIA scheduler POST)
- Sends slots to **`/webhook/booker-lock-sandbox`** (300s timeout)

```powershell
python scripts\patch_watcher_kopenhagen.py
python scripts\n8n_push_watcher_sandbox.py
python scripts\run_full_watcher_booker_test.py
```

Loop (every 5m): full test re-run via `run_full_watcher_booker_test.py`

### Five-applicant watcher test

Watcher scans schedules; booker picks PENDING students and calls Python turbo via ngrok.

```powershell
# Run until 5/5 COMPLETED (up to 10 min)
python scripts\run_five_watcher_bookings.py

# Fixed window (e.g. 1 minute) — watcher deactivated when time is up
python scripts\run_five_watcher_bookings.py --watch-sec 60
```

Reports: `artifacts/five-watcher-bookings-report.json` or `five-watcher-bookings-60s-report.json`

Direct turbo baseline (no n8n watcher, local scan each round):

```powershell
python scripts\run_five_bookings.py
```

Report: `artifacts/five-bookings-direct-turbo-report.json`

## KOPENHAGEN test (dummy data)

```powershell
# Scan + seed applicant + n8n sandbox webhook (default)
python scripts\run_kopenhagen_e2e_test.py

# Optional: also hit sandbox Python directly (uses 2nd slot if 2+ found)
python scripts\run_kopenhagen_e2e_test.py --direct-turbo
```

- Fixture: `fixtures/kopenhagen-dummy-applicant.json` (Egyptian-style fields, `Office` still KOPENHAGEN)
- `seed_dummy_applicant()` generates unique name/DOB/phone/email per run (any suffix, not hex-only)
- Outputs: `artifacts/kopenhagen-*.json`, `artifacts/kopen-scan-*.json`

## Quick start

```powershell
cd python-booker-webhook\versions\lock-integration-v1

# 1) Patch workflow locally
python scripts\patch_booker_lock.py

# 2) Start booker (port 8010 — not 8000)
cd python
python -m uvicorn app:app --host 127.0.0.1 --port 8010 --workers 1

# 3) ngrok (another terminal)
python scripts\start_ngrok_sandbox.py
# Re-run patch + push so HTTP node gets new URL if tunnel changed:
python scripts\patch_booker_lock.py
python scripts\n8n_push_booker_lock.py

# 4) Push/update sandbox workflow on n8n
python scripts\n8n_push_booker_lock.py

# 5) Run lock smoke tests (T2 busy skip)
python scripts\run_lock_integration_tests.py

# 6) Verify lock released when no slots to book
python scripts\verify_no_slots_unlock.py
```

## Sandbox workflow (n8n)

| Field | Value |
|-------|-------|
| Name | `booker-lock-sandbox-v1` |
| ID | `CLTCcjVtgfjqyMnF` |
| Status | **Active** (for testing) |
| Webhook | `POST /webhook/booker-lock-sandbox` |
| Python URL | ngrok → `http://127.0.0.1:8010/webhook/booker` |

Registry file: [docs/n8n-workflow-ids.json](docs/n8n-workflow-ids.json)

## Lock behavior

- **Storage:** workflow static data (`global`), 10 min TTL
- **Busy:** `200 { ok: true, skipped: true, reason: "BOOKER_BUSY" }`
- **No slots:** `200 { ok: true, skipped: true, reason: "NO_SLOTS" }` → `Unlock Lock Early` → `Respond Webhook`
- **Done:** `200 { ok: true, skipped: false, reason: "COMPLETED" }`
- **Loop-back:** result branches → `One Slot at a Time`; batch done → `Unlock Lock`
- **Early unlock paths (2026-06-14):**
  - `Filter Blacklist Slots` emits `noSlots: true` when blacklist has nothing retryable → unlock without holding lock
  - `Get Next PENDING Student` uses `alwaysOutputData: true` so empty queue still reaches `Student Found?` → loop/unlock
- **Webhook:** `responseMode: "responseNode"` + single `Respond Webhook` node
- **Stuck lock:** `python scripts\verify_no_slots_unlock.py` or `clear_lock()` via `watcher_30s_lock_test.py`

## Python turbo / captcha (sandbox)

Run booker with **`--workers 1`** on port **8010**. Recommended env for n8n path:

| Variable | Sandbox value | Why |
|----------|---------------|-----|
| `PLAYWRIGHT_REUSE_BROWSER` | `false` | Avoids “cannot switch to a different thread” under n8n |
| `MAX_CONCURRENT_BOOKINGS` | `1` | One Playwright session at a time |
| `CAPTCHA_PARALLEL_FETCH` | `false` | Optional; DOM screenshot is always used for OCR |
| `CAPTCHA_MAX_STYLE_RELOADS` | `0` | Skip captcha difficulty reload loop in tests |

**Captcha fixes in this sandbox:**
- OCR always uses DOM screenshot (`#Captcha_CaptchaImage`), not parallel URL bytes
- `fill_captcha_with_hs_guard()` — retries until `BDC_Hs_Captcha` updates; turbo skips submit if still stale
- DOM capture retries 3× before URL fallback
- Gemini preferred on captcha round ≥ 2 (`TURBO_CAPTCHA_SOLVER=ocr_with_gemini_fallback` default)

After changing `BOOKER_PUBLIC_URL`, re-run `patch_booker_lock.py` + `n8n_push_booker_lock.py`.

## Recent test results (KOPENHAGEN sandbox)

| Run | Mode | Result |
|-----|------|--------|
| Watcher n8n | 5 applicants, ~101s | **5/5 COMPLETED** |
| Watcher n8n | `--watch-sec 60` | **4/5 COMPLETED** (5th still PENDING when watcher stopped) |
| Direct turbo | `run_five_bookings.py` | **5/5** in ~77s |

See [docs/CHANGELOG.md](docs/CHANGELOG.md) for a dated list of workflow and code changes.

## Live workflows (unchanged)

| Workflow | ID |
|----------|-----|
| booker | V2giHI6bqFpWq8ff |
| Watcher v3 | KTw5aLQjJhT7fpxN |

See [../../docs/N8N_BOOKER_LOCK_IMPLEMENTATION_PLAN.md](../../docs/N8N_BOOKER_LOCK_IMPLEMENTATION_PLAN.md).
