# Parallel bookings (`--workers` + n8n)

## Capacity formula

```
max parallel bookings = uvicorn_workers × MAX_CONCURRENT_BOOKINGS
```

Example: `--workers 3` and `MAX_CONCURRENT_BOOKINGS=1` → **at most 3** bookings running at once on the VPS.

A **4th** request is **queued inside one worker** for up to `BOOKING_SLOT_WAIT_SEC` (default **120s**), then returns **HTTP 503** `BOOKING_SERVER_BUSY` with header `Retry-After`.

This is **not** a global queue unless you enable `BOOKING_USE_GLOBAL_LOCK=true`.

## Blank “Loading…” page (`fail-navigate-uncaught`)

If navigation-debug shows:

- `"title": "Loading https://appointment.bmeia.gv.at/"`
- empty `<body></body>`
- all selector `counts` are `0`

the browser never got a real landing page. This happens when **several Chromium instances open the site at once** (parallel n8n branches or `--workers 3` without a global lock). The fix is **`BOOKING_USE_GLOBAL_LOCK=true`** and **serial** booker calls (or 1s spacing is not enough).

`app.py` retries landing up to `LANDING_GOTO_MAX_ATTEMPTS` and waits for `#Office`, but **cannot fix true parallel load** on the appointment server.

## Why only 1 of 4 “worked” (common causes)

### 1. Same `sessionCookie` on all 4 branches (most common)

BMEIA uses `ASP.NET_SessionId`. Two browsers with the **same** session fight over state; one flow wins, others stall or land on the wrong step after **Next**.

**Fix:** One **fresh** scheduler session per student/booking. Never fan out one cookie to parallel HTTP nodes.

### 2. Same slot / same student

Only one appointment can take a given slot. Others get `SLOT_TAKEN` or empty scheduler.

**Fix:** Different `currentSlot` and students, or run bookings **serially**.

### 3. n8n HTTP timeout vs server wait

The 4th call may still be **waiting for a slot** (up to 120s) while n8n already timed out (60–120s). n8n shows “failed”; the server may still be busy.

**Fix:**

- HTTP node timeout ≥ 5 minutes **or**
- Retry on **503** with backoff (use `Retry-After` header)
- Lower `BOOKING_SLOT_WAIT_SEC` so overload fails fast with 503

### 4. Too many Chromium processes

3 workers × headless Chromium + video artifacts can exhaust RAM; extra runs crash or hang at navigation.

**Fix:** `PLAYWRIGHT_HEADLESS=true`, `captureArtifacts: false`, `PLAYWRIGHT_SLOW_MO=0`, fewer workers on small VPS.

## “Frozen” parallel calls (important)

If n8n fires 4 HTTP requests at once with `BOOKING_USE_GLOBAL_LOCK=true`:

- **Before fix:** requests 2–4 **blocked** on the file lock for the whole booking (~5+ min) → Axios timeout, empty “Loading…” artifacts.
- **After fix:** requests 2–4 get **HTTP 503** `BOOKING_SERVER_BUSY` within `BOOKING_SLOT_WAIT_SEC` (default **15s**) and `Retry-After: 60`.

Configure n8n to **retry 503** (or use a **single serial** branch). Do not treat 503 as student failure.

## Recommended setups

### A — Safe default (one booking at a time)

```env
BOOKING_USE_GLOBAL_LOCK=true
MAX_CONCURRENT_BOOKINGS=1
BOOKING_SLOT_WAIT_SEC=15
```

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

n8n: **no parallel** booker calls; unique `sessionCookie` per run; `"runId": "{{ $execution.id }}"`; retry **503** or chain HTTP nodes sequentially.

### B — Controlled parallelism (3 at once)

```env
BOOKING_USE_GLOBAL_LOCK=false
MAX_CONCURRENT_BOOKINGS=1
```

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 3
```

n8n:

- **4 unique** `sessionCookie` values (one scheduler capture each)
- **4 unique** `runId`s
- Retry **503** `BOOKING_SERVER_BUSY` (not student failure)
- Do not expect 4 parallel if you only have 3 workers and no retry

## Debug on VPS

```bash
grep -E 'waiting_for_slot|slot_acquired|slot_wait_timeout|global_flock|playwright_launch' debug-ce612b.log | tail -80
```

Check:

- How many `slot_acquired` at the same timestamp (should be ≤ worker count)
- `sessionIdTail` — same tail on parallel runs ⇒ shared session bug
- `pid` — which worker held the slot

## n8n HTTP node

- Map `503` + `detail.error === "BOOKING_SERVER_BUSY"` → **Wait** + retry
- Timeout: ≥ 300000 ms if you rely on queueing instead of 503+retry
