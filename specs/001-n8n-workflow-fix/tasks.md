# Tasks: n8n Workflow Analysis and Fix

**Input**: Design documents from `/specs/001-n8n-workflow-fix/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/booker-webhook-contract.md, quickstart.md

**Tests**: Manual validation only — no automated test tasks. Validation via n8n UI import + webhook trigger (see quickstart.md).

**Organization**: Tasks are grouped by workflow section. Since this is a single-file edit (`n8n/latest booker.json`), tasks are ordered by dependency: remove dead nodes → fix entry path → fix booking loop → add missing handlers → fix connections → validate.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (maps to spec scenarios)
- Include exact file paths in descriptions

## Path Conventions

- Primary target: `n8n/latest booker.json` (all booker workflow tasks)
- Already fixed: `n8n/latest watcher.json` (no changes needed)
- Test payload: `fixtures/payloads/payload-booker.json`
- Spec docs: `specs/001-n8n-workflow-fix/`

---

## Phase 1: Setup (Backup + Validation)

**Purpose**: Save current state and verify the starting point before making changes

- [X] T001 Create backup of current booker workflow by copying `n8n/latest booker.json` to `n8n/latest booker.backup-pre-redesign.json`
- [X] T002 Validate `n8n/latest booker.json` is valid JSON using `python -m json.tool` or `Get-Content -Raw | ConvertFrom-Json`
- [X] T003 [P] Verify `n8n/latest watcher.json` is valid JSON and contains prior fixes (trigger named "Every 5 Seconds", no dead HTTP Request / Analyze image nodes, no Pass Through Session node)

---

## Phase 2: Remove Dead Nodes (Blacklist + FALSE Path)

**Purpose**: Delete all 18 nodes related to the blacklist table and FALSE retry path

**Goal**: Strip the workflow down to the nodes we're keeping, leaving connections to be rewired in Phase 4

- [X] T004 Remove `Get All Blacklisted Slots` node (duplicate blacklist fetch for TRUE path) from `n8n/latest booker.json`
- [X] T005 Remove `Get_All_Blacklisted_Slots` node (blacklist fetch for FALSE path) from `n8n/latest booker.json`
- [X] T006 Remove `Filter Fresh Slots` node (blacklist dedup logic + retry path) from `n8n/latest booker.json`
- [X] T007 Remove `Any Fresh Slots?` node (TRUE/FALSE branch split) from `n8n/latest booker.json`
- [X] T008 Remove `Prepare Slots for Insert` node (blacklist insert preparation) from `n8n/latest booker.json`
- [X] T009 Remove `Insert All Slots to Blacklist` node (blacklist table write) from `n8n/latest booker.json`
- [X] T010 Remove `Rebuild Payload` node (rebuild payload after blacklist insert, TRUE path) from `n8n/latest booker.json`
- [X] T011 Remove `Rebuild_Payload` node (rebuild payload from blacklist rows, FALSE path) from `n8n/latest booker.json`
- [X] T012 Remove `Filter_slots` node (hardcoded KOPENHAGEN + PENDING filter, FALSE path) from `n8n/latest booker.json`
- [X] T013 Remove `If` node (slotValue notEmpty check, FALSE path) from `n8n/latest booker.json`
- [X] T014 Remove `Replace Me` node (placeholder noOp, 5 nodes routed through it) from `n8n/latest booker.json`
- [X] T015 Remove `Update Slot → IN_PROGRESS` node (blacklist slot status update) from `n8n/latest booker.json`
- [X] T016 Remove `Update Slot → PENDING` node (blacklist slot reset on HTTP error) from `n8n/latest booker.json`
- [X] T017 Remove `Mark Slot BOOKED` node (blacklist slot mark on SUCCESS) from `n8n/latest booker.json`
- [X] T018 Remove `Mark Slot SLOT_TAKEN` node (blacklist slot mark on SLOT_TAKEN) from `n8n/latest booker.json`
- [X] T019 Remove `Mark Slot CAPTCHA_FAIL` node (blacklist slot mark on CAPTCHA_FAIL) from `n8n/latest booker.json`
- [X] T020 Remove `Mark Slot PENDING` node (blacklist slot reset on ALREADY_BOOKED) from `n8n/latest booker.json`
- [X] T021 Remove `update the lock_status to be unlocked1` node (broken filter, no keyName, FALSE path exit) from `n8n/latest booker.json`
- [X] T022 Remove `update the lock_status to be unlocked6` node (duplicate unlock after No Students Left) from `n8n/latest booker.json`
- [X] T023 Validate `n8n/latest booker.json` is still valid JSON after all node removals

---

## Phase 3: Fix Entry Path (Webhook → Lock → Split)

**Purpose**: Rewire the entry path to go directly from lock acquisition to slot splitting, skipping the removed dedup/blacklist nodes

**Independent Test**: Import workflow in n8n, trigger webhook with test payload, verify lock is acquired and slots are split into individual items

- [X] T024 [US1] Verify entry path nodes unchanged: `Webhook1` (path: "booker", POST), `get_lock_status` (reads lock_status table nvSE5JHoAyn6BT3E), `Filter` (lock_status != "locked"), `update the lock_status to be locked` (sets locked, filter keyValue: "1") — all in `n8n/latest booker.json`, confirm no changes needed
- [X] T025 [US1] Modify `Split Into Slots` node code to split `foundSlots` from webhook body into one item per slot, including all webhook payload fields (sessionCookie, Office, CalendarId, Language, currentMonday, BDC_* captcha fields) in each output item in `n8n/latest booker.json`
- [X] T026 [US1] Rewire connection: `update the lock_status to be locked` → `Split Into Slots` (was: → `Get All Blacklisted Slots`) in `n8n/latest booker.json` connections section
- [X] T027 [US1] Rewire connection: `Split Into Slots` → `One Slot at a Time` (already exists, verify it's intact after node removals) in `n8n/latest booker.json`
- [X] T028 [US1] Validate `n8n/latest booker.json` is valid JSON and entry path is connected: Webhook1 → get_lock_status → Filter → locked → Split Into Slots → One Slot at a Time

---

## Phase 4: Fix Booking Loop (Student Assignment + HTTP Call)

**Purpose**: Ensure the loop correctly fetches students, merges data, marks IN_PROGRESS, and calls the Python booker

**Independent Test**: Import in n8n, trigger with payload + a PENDING student in applicants table, verify student is marked IN_PROGRESS and HTTP call is made

- [X] T029 [US1] Verify booking loop nodes unchanged: `One Slot at a Time` (splitInBatches, batchSize: 1), `Get Next PENDING Student` (applicants table, status=PENDING, limit: 1), `Student Found?` (TraveldocumentNumber notEmpty), `Merge Student + Slot` (combines student + slotData), `Update Student → IN_PROGRESS` (sets status=IN_PROGRESS) — all in `n8n/latest booker.json`, confirm no changes needed
- [X] T030 [US1] Verify `Book the Appointment` HTTP node `onError` is `continueErrorOutput` and `retryOnFail: false` in `n8n/latest booker.json` — the `maxTries` and `waitBetweenTries` fields are ignored when `retryOnFail: false`, so no retry occurs. Keep as-is per AGENT_HANDOFF.md guidance (retry only on 503, not on all failures)
- [X] T030b [US1] Verify `Book the Appointment` HTTP node `url` field points to the Python booker service (currently `https://porthole-portfolio-napped.ngrok-free.dev/webhook/booker`), NOT the n8n webhook URL — the n8n URL (`n8n.acmeai.crabdance.com`) is for watcher→booker, this node calls booker→Python. Update the URL if the ngrok tunnel has changed since last import in `n8n/latest booker.json`
- [X] T031 [US1] Verify `Book the Appointment` HTTP node `jsonBody` expression builds the full payload from `Merge Student + Slot` per the contract in `specs/001-n8n-workflow-fix/contracts/booker-webhook-contract.md` — confirm it matches contract, no changes needed unless contract drift found
- [X] T032 [US1] Verify `Check Booking Response` code node parses `resultType`, `ok`, `message`, `attempt` from booker response — keep as-is in `n8n/latest booker.json`
- [X] T033 [US1] Validate entry to booking loop: One Slot at a Time → Get Next PENDING Student → Student Found? → Merge → Update Student → Book HTTP → Check Response → Result Switch in `n8n/latest booker.json`

---

## Phase 5: Fix Result Switch (Add Missing Branches)

**Purpose**: Add the OTHER_FAIL, BOOKING_SERVER_BUSY, and HTTP_ERROR handlers to the Result Switch so no result type is left unhandled

**Independent Test**: Mock each result type in Check Booking Response output, verify each routes to the correct handler

- [X] T041 [US2] Modify `Result Switch` node: add output 4 routing rule for `resultType == "OTHER_FAIL"` (currently empty `[]`) in `n8n/latest booker.json`
- [X] T042 [US2] Modify `Result Switch` node: add output 5 routing rule for `resultType == "BOOKING_SERVER_BUSY"` (currently in the `extra` fallback output) in `n8n/latest booker.json`
- [X] T043 [US2] Verify `Result Switch` output 6 (`extra` fallback) catches unrecognized result types — keep as fallback in `n8n/latest booker.json`
- [X] T044 [US2] Create `Reset Student PENDING (other)` dataTable update node: update `applicants` table set `status: "PENDING"` filtered by student ID from `Merge Student + Slot` in `n8n/latest booker.json`
- [X] T045 [US2] Create `Telegram: Other Fail` telegram node: message text "❌ Booking failed\n\nStudent: {{ Firstname }} {{ Lastname }}\nResult: {{ resultType }}\nMessage: {{ errorText }}\nStudent returned to PENDING\nTime: {{ timestamp }}" in `n8n/latest booker.json`
- [X] T046 [US2] Create `Telegram: HTTP Error` telegram node: message text "🔴 HTTP request failed\n\nStudent: {{ Firstname }} {{ Lastname }}\nSlot: {{ currentSlot }}\nError: {{ $json.message }}\nStudent returned to PENDING" in `n8n/latest booker.json`
- [X] T047 [US2] Validate `n8n/latest booker.json` — Result Switch has 7 outputs (0-6), each connected to a handler

---

## Phase 6: Rewire Connections (All Branches)

**Purpose**: Connect all result branches to their handlers, remove the removed `Replace Me` intermediate hop (which previously routed results → unlock, releasing the lock per-booking), and instead connect results back to the loop for the next slot. The existing Result Switch → student-update connections (outputs 0-3) already exist; these tasks remove the `Replace Me → unlock` hop and add the missing connections for outputs 4-6 + HTTP error.

**Independent Test**: Import in n8n, trace each branch from Result Switch → student update → Telegram → loop next

- [X] T048 [US2] Rewire `Result Switch` output 0 (SUCCESS): → `Mark Student COMPLETED` → `Telegram: Booked!` in `n8n/latest booker.json`
- [X] T049 [US2] Rewire `Result Switch` output 1 (CAPTCHA_FAIL): → `Reset Student PENDING (captcha)` → `Telegram: CAPTCHA Failed` in `n8n/latest booker.json`
- [X] T050 [US2] Rewire `Result Switch` output 2 (SLOT_TAKEN): → `Reset Student PENDING (taken)` → `Telegram: Slot Taken` in `n8n/latest booker.json`
- [X] T051 [US2] Rewire `Result Switch` output 3 (ALREADY_BOOKED): → `Mark Student ALREADY_BOOKED` → `Telegram: already booked` in `n8n/latest booker.json`
- [X] T052 [US2] Connect `Result Switch` output 4 (OTHER_FAIL): → `Reset Student PENDING (other)` → `Telegram: Other Fail` in `n8n/latest booker.json`
- [X] T053 [US2] Connect `Result Switch` output 5 (BOOKING_SERVER_BUSY): → `Reset Student PENDING (other)` (same node as OTHER_FAIL) → `Telegram: Other Fail` in `n8n/latest booker.json`
- [X] T054 [US2] Connect `Result Switch` output 6 (extra/fallback for unrecognized result types): → `Reset Student PENDING (other)` → `Telegram: Other Fail` (with message including "unrecognized resultType: {{ $json.resultType }}") in `n8n/latest booker.json` — this is NOT the HTTP error path; it catches unknown result types from the Python booker response
- [X] T055 [US2] Rewire `Book the Appointment` error output (output 1, for HTTP failures like timeout/connection refused): → `Reset Student PENDING (other)` → `Telegram: HTTP Error` in `n8n/latest booker.json` — this is the HTTP transport failure path, separate from the Result Switch fallback above
- [X] T056 [US2] Remove all connections from result Telegram nodes to `Replace Me` (node already removed in T014) and to `update the lock_status to be unlocked` — results must NOT release the lock in `n8n/latest booker.json`
- [X] T057 [US2] Connect each result Telegram node (`Telegram: Booked!`, `Telegram: CAPTCHA Failed`, `Telegram: Slot Taken`, `Telegram: already booked`, `Telegram: Other Fail`, `Telegram: HTTP Error`) back to `One Slot at a Time` input 0 (loop continues to next slot) in `n8n/latest booker.json`
- [X] T058 Verify `Student Found?` FALSE output: → `Telegram: No Students Left` in `n8n/latest booker.json` (existing, confirm)
- [X] T059 [US2] Connect `Telegram: No Students Left` → `Telegram: All Done` (which then → unlock) — this triggers loop completion and lock release when no PENDING students are found, same as the loop-done output in `n8n/latest booker.json`
- [X] T060 Verify `One Slot at a Time` done output: → `Telegram: All Done` in `n8n/latest booker.json` (existing, confirm)
- [X] T061 [US2] Connect `Telegram: All Done` → `update the lock_status to be unlocked` (release lock once, at loop completion) in `n8n/latest booker.json`
- [X] T062 [US2] Validate `n8n/latest booker.json` is valid JSON and all connections are wired: no dangling outputs, no references to removed nodes

---

## Phase 7: Watcher Workflow Verification

**Purpose**: Confirm the watcher workflow is already fixed from prior session and requires no changes — these are verification-only tasks, not implementation tasks. All watcher fixes (trigger name, dead node removal, no-op cleanup) were applied in a prior session.

**Independent Test**: Import `n8n/latest watcher.json` in n8n, verify it runs every 5 seconds and sends webhook to booker

- [X] T063 [P] Verify `n8n/latest watcher.json` trigger node is named "Every 5 Seconds" with 5-second interval
- [X] T064 [P] Verify `n8n/latest watcher.json` has no dead HTTP Request (captcha fetch) or Analyze an image (Gemini) nodes
- [X] T065 [P] Verify `n8n/latest watcher.json` has no Pass Through Session no-op node
- [X] T066 [P] Verify `n8n/latest watcher.json` has no `Log: No Slots Found` code node (replaced by noOp)
- [X] T067 [P] Verify `n8n/latest watcher.json` connections section references "Every 5 Seconds" (not "Every 60 Seconds")

---

## Phase 8: Validate via Quickstart Scenarios

**Purpose**: Run through all 6 validation scenarios from quickstart.md to confirm the workflow behaves correctly

**Independent Test**: Each scenario has specific expected outcomes documented in quickstart.md

- [X] T068 Validate JSON validity of final `n8n/latest booker.json` using `python -m json.tool`
- [X] T069 [US1] Validate Scenario 1 (Successful booking cycle): import booker in n8n, ensure 1 PENDING student + lock unlocked, trigger webhook with `fixtures/payloads/payload-booker.json`, verify student → COMPLETED + Telegram success + lock released
- [X] T070 [US2] Validate Scenario 2 (CAPTCHA_FAIL): mock Python booker returning CAPTCHA_FAIL, trigger webhook, verify student → PENDING + Telegram captcha alert + loop continues
- [X] T071 [US2] Validate Scenario 3 (SLOT_TAKEN): mock Python booker returning SLOT_TAKEN, trigger webhook, verify student → PENDING + Telegram taken alert
- [X] T072 [US2] Validate Scenario 4 (No PENDING students): ensure applicants has 0 PENDING, trigger webhook, verify Telegram "No more PENDING students" + lock released
- [X] T073 [US2] Validate Scenario 5 (Lock already held): set lock_status = "locked", trigger webhook, verify Filter blocks execution, no booking attempted, lock unchanged
- [X] T074 [US2] Validate Scenario 6 (OTHER_FAIL): mock Python booker returning OTHER_FAIL, trigger webhook, verify student → PENDING + Telegram failure alert (previously dead branch, now handled)
- [X] T075 [US2] Validate Scenario 7 (HTTP error): set invalid Python booker URL or stop service, trigger webhook, verify student → PENDING + Telegram HTTP error alert (previously deadlocked, now handled)

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Cleanup and final validation

- [X] T076 [P] Remove all `removed: true` schema entries from remaining dataTable nodes in `n8n/latest booker.json` to reduce JSON size (cosmetic, n8n ignores them)
- [X] T077 [P] Verify node positions are reasonable (no overlapping nodes in n8n canvas) in `n8n/latest booker.json` — adjust if needed
- [X] T078 Verify no orphaned pinData references to removed nodes in `n8n/latest booker.json` pinData section
- [X] T079 [P] Verify `BOOKING_SERVER_BUSY` Telegram message distinguishes itself from OTHER_FAIL message text (different emoji: ⏳ vs ❌)
- [X] T080 Final JSON validation of `n8n/latest booker.json` — confirm valid JSON, all node IDs unique, all connection references point to existing nodes
- [X] T081 Clean up backup file: delete `n8n/latest booker.backup-pre-redesign.json` after all validations pass
- [X] T082 Update `specs/001-n8n-workflow-fix/spec.md` if any discrepancies found during implementation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — can start immediately
- **Phase 2 (Remove Nodes)**: Depends on Phase 1 backup
- **Phase 3 (Fix Entry Path)**: Depends on Phase 2 completion (nodes removed, connections dangling)
- **Phase 4 (Fix Booking Loop)**: Depends on Phase 3 (entry path connected to loop)
- **Phase 5 (Fix Result Switch)**: Depends on Phase 4 ( booking loop reaches Result Switch)
- **Phase 6 (Rewire Connections)**: Depends on Phase 5 (new nodes exist to connect)
- **Phase 7 (Watcher Verify)**: Can run in PARALLEL with Phases 2-6 (different file)
- **Phase 8 (Validate)**: Depends on Phase 6 completion (workflow fully rewired)
- **Phase 9 (Polish)**: Depends on Phase 8 validation passing

### User Story Dependencies

- **US1 (Happy path — lock + split + book SUCCESS)**: Phases 1-4 + partial Phase 6 (SUCCESS branch only). This is the MVP. Tasks T001-T033 + T048 (SUCCESS only).
- **US2 (Failure handling — all result types)**: Phases 5-6 (add missing branches + rewire). Depends on US1 being complete. Tasks T041-T062.
- **Watcher verification**: Phase 7 — independent, can run in parallel with booker work. Tasks T063-T067 (verification only, no implementation).

### Within Each Phase

- T004-T022 (node removals) are all in the same file — do them in a single editing pass, not parallel
- T025-T027 (entry path rewiring) are sequential — each depends on the prior
- T041-T046 (new nodes creation) can be done in parallel within the phase (different node objects)
- T048-T062 (connection rewiring) must be done sequentially after new nodes exist
- T063-T067 (watcher checks) are all [P] — different file, fully parallel

### Parallel Opportunities

- Phase 7 (watcher verification, T063-T067) runs in parallel with Phases 2-6 (booker edits) — different file
- Within Phase 5, new node creation (T044-T046) can be batched since they're independent node objects

---

## Parallel Example: Watcher Verification

```text
# While booker is being edited, verify watcher in parallel:
Task: T063 - Verify trigger node name in n8n/latest watcher.json
Task: T064 - Verify no dead captcha nodes in n8n/latest watcher.json
Task: T065 - Verify no Pass Through Session in n8n/latest watcher.json
Task: T066 - Verify no Log: No Slots Found in n8n/latest watcher.json
Task: T067 - Verify connections reference "Every 5 Seconds" in n8n/latest watcher.json
```

---

## Implementation Strategy

### MVP First (US1 — Happy Path Only)

1. Complete Phase 1: Backup + validate
2. Complete Phase 2: Remove all 18 dead nodes
3. Complete Phase 3: Wire entry path (lock → split → loop)
4. Complete Phase 4: Verify booking loop (student fetch → HTTP → response)
5. Complete partial Phase 6: Wire SUCCESS branch only (Mark COMPLETED → Telegram → loop next)
6. **STOP and VALIDATE**: Import in n8n, trigger with test payload, confirm one successful booking
7. If working, proceed to US2

### Incremental Delivery (US2 — Failure Handling)

1. Complete Phase 5: Add OTHER_FAIL, BOOKING_SERVER_BUSY, HTTP_ERROR branches
2. Complete Phase 6: Wire all result branches to handlers + loop back
3. Validate all 7 scenarios from quickstart.md
4. Each failure type now has a handler — no deadlocks

### Final Polish

1. Complete Phase 8: All validation scenarios pass
2. Complete Phase 9: Cleanup schema, remove backup, final JSON validation
3. Ready for production import

---

## Notes

- This is a single-file edit project (`n8n/latest booker.json`). Tasks are inherently sequential within the same file — [P] markers indicate only that the *concept* is independent, not that two agents can edit the same JSON simultaneously.
- Phase 7 (watcher) is genuinely parallel since it's a different file and requires only verification, not edits.
- All node removals (T004-T022) should be done in ONE editing pass to avoid touching the same JSON 19 times — the task breakdown is for traceability, not for 19 separate edit operations.
- The `id` field of each node must be preserved (n8n uses it for connections). Only the `name` is used in the connections section.
- After removing nodes, search for any connection references to removed node names and clean them up — this is handled in Phase 6.
