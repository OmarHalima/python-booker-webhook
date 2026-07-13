# Implementation Plan: n8n Workflow Analysis and Fix

**Branch**: `001-n8n-workflow-fix` | **Date**: 2026-07-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-n8n-workflow-fix/spec.md`

## Summary

Redesign the booker workflow to a minimal, lock-gated, fresh-slots-only pipeline. Remove the Slots blacklist data table entirely — BMEIA is the source of truth (booked slots are removed from the calendar instantly). The booker receives slots from the watcher webhook, acquires a distributed lock, splits slots into a sequential loop, assigns each slot to the next PENDING student, calls the Python booker service, routes the result through a complete 7-branch switch (with no dead branches), updates the applicants table, releases the lock, and exits. The watcher workflow fixes (already applied: trigger name, dead node removal, no-op node cleanup) remain.

## Technical Context

**Language/Version**: n8n workflow JSON (n8n cloud/self-hosted, n8n-nodes-base v2+)

**Primary Dependencies**:
- n8n data tables: `applicants` (student records), `lock_status` (ID: `nvSE5JHoAyn6BT3E`)
- n8n nodes: webhook, dataTable (get/update), filter, if, code, splitInBatches, httpRequest, switch, telegram, noOp
- Python booker service (external): POST /webhook/booker (Port 8000, uvicorn)
- Telegram bot: chatId `1377621332`, credential `YqEjQvYeHBuiR4Cl`

**Storage**: n8n internal data tables (applicants, lock_status). No external DB. The Slots blacklist table (`B0yhFJlFzV7EozJz`) is removed from this workflow — no reads, no writes.

**Testing**: Manual — import workflow JSON into n8n, trigger with test webhook payload (`fixtures/payloads/payload-booker.json`), observe execution in n8n UI. No automated workflow tests exist.

**Target Platform**: n8n (self-hosted Docker on VPS, accessed via ngrok tunnel)

**Project Type**: n8n workflow (JSON configuration, not code)

**Performance Goals**: 
- Booker execution: <300s per slot (HTTP timeout: 300000ms)
- Lock hold time: <30s per slot (target: <10s per SUCCESS, <70s per CAPTCHA_FAIL with retries)
- End-to-end: watcher fires → booker completes in <2 minutes for 8 slots

**Constraints**:
- One booking at a time (BOOKING_USE_GLOBAL_LOCK=true on Python side, lock_status table on n8n side)
- Python booker must run with `--workers 1` (Playwright sync API constraint)
- BMEIA session is per-webhook: sessionCookie and token are valid only for the current watcher cycle
- Webhook URL must match the Python booker's ngrok tunnel (currently `porthole-portfolio-napped.ngrok-free.dev`)

**Scale/Scope**: ~24 working nodes in final booker (9 structural + 12 notification/utility + 3 new), down from 28 original (removed 19, kept 9 structural + 12 utility, added 3). JSON size significantly reduced by removing 19 nodes with their full schema arrays. Watcher unchanged from prior fixes (22 nodes).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Evidence-First Debugging | ✅ PASS | Analysis grounded in reading the full 4047-line workflow JSON, connection map, and AGENT_HANDOFF.md. All identified bugs traced to specific line numbers. |
| II. Test Discipline & Isolation | ✅ PASS | No Python code changes. n8n workflow JSON only. Python test baseline (77 tests) unaffected. Test payload in `fixtures/payloads/`. |
| III. Modular Pure Logic | ✅ PASS | No Python module changes. Workflow JSON is configuration, not logic. |
| IV. Stable Entry Point & Environment Contracts | ✅ PASS | `uvicorn app:app` entry point unchanged. KOPENHAGEN for dev, KAIRO for production — workflow is office-agnostic (receives Office from webhook payload). |
| V. Captcha Pipeline Integrity | ✅ PASS | Result types (SUCCESS, CAPTCHA_FAIL, SLOT_TAKEN, ALREADY_BOOKED, OTHER_FAIL, BOOKING_SERVER_BUSY) all handled by the 7-branch Result Switch. No result type left unhandled. |

**Post-Phase 1 Re-check**: ✅ PASS — data model uses only applicants + lock_status tables. No new tables. No new Python code. Workflow branches on all 6 standardized result types + HTTP error.

## Project Structure

### Documentation (this feature)

```text
specs/001-n8n-workflow-fix/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── booker-webhook-contract.md  # Webhook payload → booker HTTP contract
└── tasks.md             # Phase 2 output (not created by /speckit.plan)
```

### Source Code (repository root)

```text
n8n/
├── latest booker.json     # PRIMARY TARGET — rewrite (28→24 nodes: remove 19, keep 21, add 3)
└── latest watcher.json    # Already fixed (prior session), no changes

fixtures/
└── payloads/
    └── payload-booker.json  # Test payload for manual validation

python-booker-webhook/     # NO CHANGES — Python service unchanged
```

**Structure Decision**: Single-file edit target (`n8n/latest booker.json`). No new directories, no new code files. The workflow JSON is the sole deliverable. Supporting docs go under `specs/001-n8n-workflow-fix/`.

## Complexity Tracking

No constitution violations. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

## Architecture Decision: Remove Blacklist

### Rationale

The Slots blacklist data table was used for:
1. **Dedup** — prevent reprocessing slots the watcher already found
2. **Status tracking** — record BOOKED/SLOT_TAKEN/CAPTCHA_FAIL per slot
3. **Retry** — reprocess PENDING slots from previous sessions

**All three uses are removed:**

1. **Dedup is unnecessary** — BMEIA removes booked slots from the calendar instantly. The next watcher cycle will not find already-booked slots. The lock prevents concurrent booker executions, so skipped webhook invocations (while locked) are simply dropped, not queued.

2. **Status tracking moves to applicants table** — student status (PENDING → IN_PROGRESS → COMPLETED/ALREADY_BOOKED) is the only tracking needed. Slot-level status is irrelevant: if BMEIA says SUCCESS, the slot is booked; if SLOT_TAKEN, move to next slot; if CAPTCHA_FAIL, the student goes back to PENDING for the next cycle.

3. **Retry is broken** — retrying PENDING slots from a previous session fails because the sessionCookie and token are session-bound. BMEIA's ASP.NET session ties slot selection to the session that detected it. A new webhook's session cannot book a slot detected in a previous session. The watcher will re-detect slots if they're still available.

### Nodes Removed (19 nodes)

| Node | Reason |
|------|--------|
| `Get All Blacklisted Slots` | No dedup needed |
| `Get_All_Blacklisted_Slots` | Duplicate + FALSE path entry |
| `Filter Fresh Slots` | No blacklist to filter against |
| `Any Fresh Slots?` | No fresh/old distinction |
| `Prepare Slots for Insert` | No blacklist insert |
| `Insert All Slots to Blacklist` | No blacklist |
| `Rebuild Payload` | FALSE path only |
| `Rebuild_Payload` | FALSE path only |
| `Filter_slots` | FALSE path only (also hardcoded KOPENHAGEN) |
| `If` | FALSE path only |
| `Replace Me` | Placeholder, serves no purpose |
| `Update Slot → IN_PROGRESS` | No slot tracking table |
| `Update Slot → PENDING` | No slot tracking table (HTTP error path) |
| `Mark Slot BOOKED` | No slot tracking table |
| `Mark Slot SLOT_TAKEN` | No slot tracking table |
| `Mark Slot CAPTCHA_FAIL` | No slot tracking table |
| `Mark Slot PENDING` | No slot tracking table |
| `update the lock_status to be unlocked1` | Broken filter (no keyName) + FALSE path |
| `update the lock_status to be unlocked6` | Duplicate unlock after No Students Left |

### Nodes Kept + Modified (24 existing nodes kept, 3 new = 27 final)

Original workflow: 28 nodes. Removed: 19. Remaining: 9 structural + 12 Telegram/utility = 21 kept. Plus 3 new nodes = 24 final working nodes (some Telegram nodes serve as terminal outputs, not structural). See table below for all kept nodes.

| Node | Action | Purpose |
|------|--------|---------|
| `Webhook1` | Keep as-is | POST /webhook/booker entry point |
| `get_lock_status` | Keep as-is | Read lock_status table |
| `Filter` | Keep as-is | Pass through only if not locked |
| `update the lock_status to be locked` | Keep as-is | Acquire lock |
| `Split Into Slots` | Modify code: split `foundSlots` from webhook body | One item per slot |
| `One Slot at a Time` | Keep as-is | splitInBatches (1 per batch) |
| `Get Next PENDING Student` | Keep as-is | Query applicants WHERE status=PENDING LIMIT 1 |
| `Student Found?` | Keep as-is | If TraveldocumentNumber notEmpty |
| `Merge Student + Slot` | Keep as-is | Combine student + slot data |
| `Update Student → IN_PROGRESS` | Keep as-is | Mark student being processed |
| `Book the Appointment` | Modify URL to correct ngrok + fix error handling | HTTP POST to Python booker |
| `Check Booking Response` | Keep as-is | Parse resultType from response |
| `Result Switch` | Modify: add OTHER_FAIL + BOOKING_SERVER_BUSY + HTTP_ERROR branches | Route to 7 handlers |
| `Mark Student COMPLETED` | Keep as-is | SUCCESS → status=COMPLETED |
| `Reset Student PENDING (captcha)` | Keep as-is | CAPTCHA_FAIL → status=PENDING |
| `Reset Student PENDING (taken)` | Keep as-is | SLOT_TAKEN → status=PENDING |
| `Mark Student ALREADY_BOOKED` | Keep as-is | ALREADY_BOOKED → status=ALREADY_BOOKED |
| `Telegram: Booked!` | Keep as-is | SUCCESS notification |
| `Telegram: CAPTCHA Failed` | Keep as-is | CAPTCHA_FAIL notification |
| `Telegram: Slot Taken` | Keep as-is | SLOT_TAKEN notification |
| `Telegram: already booked` | Keep as-is | ALREADY_BOOKED notification |
| `Telegram: No Students Left` | Keep as-is | No PENDING students notification |
| `Telegram: All Done` | Keep as-is | Loop complete notification |
| `update the lock_status to be unlocked` | Keep as-is | Release lock (single node, loop done) |

### New Nodes (3 nodes)

| Node | Type | Purpose |
|------|------|---------|
| `Telegram: Other Fail` | telegram | OTHER_FAIL + BOOKING_SERVER_BUSY notification |
| `Telegram: HTTP Error` | telegram | HTTP request failed (timeout, connection) notification |
| `Reset Student PENDING (other)` | dataTable update | OTHER_FAIL / BOOKING_SERVER_BUSY / HTTP_ERROR → status=PENDING |

### Connection Map (New)

```
Webhook1 → get_lock_status → Filter(≠locked) → update lock_status=locked
  → Split Into Slots → One Slot at a Time

One Slot at a Time (splitInBatches):
  loop: Get Next PENDING Student → Student Found?
    TRUE:  Merge Student + Slot → Update Student→IN_PROGRESS → Book the Appointment
           → Check Booking Response → Result Switch
             SUCCESS:         Mark Student COMPLETED → Telegram: Booked! → [loop next]
             CAPTCHA_FAIL:    Reset Student PENDING(captcha) → Telegram: CAPTCHA Failed → [loop next]
             SLOT_TAKEN:      Reset Student PENDING(taken) → Telegram: Slot Taken → [loop next]
             ALREADY_BOOKED:  Mark Student ALREADY_BOOKED → Telegram: already booked → [loop next]
             OTHER_FAIL:      Reset Student PENDING(other) → Telegram: Other Fail → [loop next]
             BOOKING_SERVER_BUSY: Reset Student PENDING(other) → Telegram: Other Fail → [loop next]
             extra/HTTP_ERROR: Reset Student PENDING(other) → Telegram: HTTP Error → [loop next]
    FALSE: Telegram: No Students Left → [loop done]
  done:  Telegram: All Done → update lock_status=unlocked

Book the Appointment (HTTP error output):
  → Reset Student PENDING(other) → Telegram: HTTP Error → [loop next]
```

### Key Design Decisions

1. **Lock acquired once, released once** — at start and at loop-done. Not per-booking. The lock holds for the entire batch of slots. This is correct because the lock prevents concurrent booker executions, not concurrent bookings within one execution.

2. **Every result branch resets student to a consistent state** — SUCCESS→COMPLETED, any failure→PENDING (or ALREADY_BOOKED for that specific case). No student left in IN_PROGRESS.

3. **HTTP error output has a handler** — `Book the Appointment` error output goes to `Reset Student PENDING(other)` → Telegram. No deadlock.

4. **No slot table writes** — the only state change is the applicants table (student status) and the lock_status table (lock acquire/release).

5. **Webhook URL must be corrected** — the HTTP node currently targets `porthole-portfolio-napped.ngrok-free.dev`. This should match the active Python booker tunnel. The watcher sends to `n8n.acmeai.crabdance.com/webhook/booker` (n8n's own URL), which is correct — the booker HTTP node calls the *Python* booker, not itself.

6. **`BOOKING_SERVER_BUSY` as a result type** — treated the same as OTHER_FAIL (student back to PENDING, Telegram alert). The distinction is only in the Telegram message text.

7. **isRetry path removed** — `Filter Fresh Slots` had a retry path that bypassed blacklist filtering. With no blacklist, there's nothing to bypass. The webhook payload no longer needs `retryCount`.

8. **Office/calendarId/sessionCookie from webhook** — the webhook payload (sent by watcher) contains `Office`, `CalendarId`, `sessionCookie`, `Language`, `currentMonday`, `currentSlot`, `BDC_VCID_Captcha`, etc. These are passed directly to the Python booker HTTP call. No n8n-side slot or office state.

## Spec Changes Required

The existing spec references the blacklist table in several places. These need updating:

| Spec Section | Current Text | New Text |
|--------------|-------------|----------|
| FR #2 | "Blacklist deduplication: Fetch the slots blacklist once per execution..." | **Remove** — No dedup. BMEIA is source of truth. |
| FR #3 | "Fresh slot insertion: Insert all previously-unseen slots into the blacklist..." | **Remove** — No blacklist insert. |
| FR #7 | "Mark both the slot and the student as IN_PROGRESS..." | "Mark the student as IN_PROGRESS before dispatching..." — No slot table. |
| FR #7 sub-items | "mark slot BOOKED", "mark slot CAPTCHA_FAIL", etc. | **Remove slot marks** — Only student status updates. |
| Scenario 1 | "Watcher inserts slots into blacklist" | **Remove** — No blacklist insert. |
| Scenario 2 | "marks slot CAPTCHA_FAIL", "skips CAPTCHA_FAIL slot" | **Remove** — No slot tracking. Student returns to PENDING. |
| Scenario 3 | "marks slot SLOT_TAKEN" | **Remove** — No slot tracking. |
| Scenario 4 | "Booker fetches fresh slots, inserts to blacklist" | "Booker receives slots from webhook" — No insert. |
| Key Entities | "Slots blacklist (data table): Tracks all detected slots..." | **Remove** — Table no longer used. |
| Assumptions | "The blacklist data table ID is B0yhFJlFzV7EozJz" | **Remove** — Table no longer used. |
| Dependencies | "Data tables (Slots blacklist, applicants, lock_status) must exist" | "Data tables (applicants, lock_status) must exist" — Remove blacklist. |
