# Quickstart: n8n Workflow Validation

## Prerequisites

1. **n8n instance** running (self-hosted or cloud)
2. **Python booker service** running and accessible (default: `https://porthole-portfolio-napped.ngrok-free.dev/webhook/booker`)
3. **Telegram bot** configured with credential `YqEjQvYeHBuiR4Cl` in n8n
4. **Data tables** in n8n:
   - `applicants` (student records, at least 1 with status = PENDING and a valid TraveldocumentNumber)
   - `lock_status` (ID: `nvSE5JHoAyn6BT3E`, single row with lock_status = "unlocked")
5. **Test payload**: `fixtures/payloads/payload-booker.json`

## Setup

### Import the Workflow

1. Open n8n UI
2. Import `n8n/latest booker.json`
3. Verify the workflow named "booker" appears with ~20 nodes

### Verify the Python Booker

```powershell
# Health check
curl http://127.0.0.1:8000/health

# Expected: {"status":"ok"} or similar
```

### Verify Lock is Unlocked

1. In n8n, open the `lock_status` data table (ID: `nvSE5JHoAyn6BT3E`)
2. Verify the single row has `lock_status = "unlocked"`
3. If locked, manually set to "unlocked"

## Validation Scenarios

### Scenario 1: Successful Booking (Happy Path)

**Goal**: Verify the workflow books an appointment end-to-end

1. Ensure `applicants` table has at least 1 PENDING student with valid TraveldocumentNumber
2. Ensure lock_status = "unlocked"
3. Trigger the webhook with the test payload:
   ```powershell
   $payload = Get-Content "fixtures/payloads/payload-booker.json" -Raw
   Invoke-RestMethod -Uri "http://localhost:5678/webhook/booker" -Method POST -Body $payload -ContentType "application/json"
   ```
4. **Expected flow**:
   - `Webhook1` receives payload with `foundSlots` array
   - `get_lock_status` → `Filter` passes (unlocked) → lock acquired
   - `Split Into Slots` creates one item per slot
   - `One Slot at a Time` starts loop
   - `Get Next PENDING Student` returns first PENDING student
   - `Student Found?` → TRUE (TraveldocumentNumber not empty)
   - `Merge Student + Slot` combines data
   - `Update Student → IN_PROGRESS` marks student
   - `Book the Appointment` POSTs to Python booker
   - `Check Booking Response` parses result
   - `Result Switch` → SUCCESS output
   - `Mark Student COMPLETED` updates applicants table
   - `Telegram: Booked!` sends success message
   - Loop continues for next slot
   - When done: `Telegram: All Done` → lock released

5. **Verify**:
   - Check applicants table: student status = COMPLETED, bookedSlot set, bookedAt set
   - Check lock_status: "unlocked"
   - Check Telegram: received "✅ APPOINTMENT BOOKED!" message
   - Check n8n execution: completed without errors

### Scenario 2: No PENDING Students

**Goal**: Verify the workflow handles the no-students case gracefully

1. Ensure `applicants` table has NO students with status = PENDING (all COMPLETED or ALREADY_BOOKED)
2. Reset lock_status to "unlocked"
3. Trigger webhook
4. **Expected flow**:
   - Slots split and loop starts
   - `Get Next PENDING Student` returns empty
   - `Student Found?` → FALSE
   - `Telegram: No Students Left` sends message
   - Loop ends → `Telegram: All Done` → lock released

5. **Verify**:
   - Check Telegram: received "⚠️ No more PENDING students!" message
   - Check lock_status: "unlocked"
   - No students modified

### Scenario 3: Lock Already Held

**Goal**: Verify concurrent execution is prevented

1. Manually set lock_status to "locked"
2. Trigger webhook
3. **Expected flow**:
   - `get_lock_status` reads lock_status = "locked"
   - `Filter` (lock_status != "locked") blocks — no items pass through
   - Workflow ends immediately

4. **Verify**:
   - No booking attempted
   - No Telegram messages sent
   - lock_status unchanged ("locked")
   - n8n execution shows Filter node producing 0 items

### Scenario 4: CAPTCHA_FAIL Result

**Goal**: Verify captcha failure handling

1. Ensure `applicants` table has a PENDING student
2. Set the Python booker to fail captcha (or use a mock that returns `{"ok": false, "resultType": "CAPTCHA_FAIL"}`)
3. Reset lock_status to "unlocked"
4. Trigger webhook
5. **Expected flow**:
   - Booking loop starts
   - `Book the Appointment` returns CAPTCHA_FAIL
   - `Result Switch` → CAPTCHA_FAIL output
   - `Reset Student PENDING (captcha)` sets student back to PENDING
   - `Telegram: CAPTCHA Failed` sends message
   - Loop continues to next slot (or ends if no more slots)

6. **Verify**:
   - Check applicants table: student status = PENDING (reverted)
   - Check Telegram: received "❌ CAPTCHA failed" message

### Scenario 5: OTHER_FAIL / BOOKING_SERVER_BUSY

**Goal**: Verify previously-unhandled result types now have handlers

1. Set the Python booker to return `{"ok": false, "resultType": "OTHER_FAIL"}`
2. Trigger webhook
3. **Expected flow**:
   - `Result Switch` → OTHER_FAIL output (previously empty, now connected)
   - `Reset Student PENDING (other)` sets student back to PENDING
   - `Telegram: Other Fail` sends message
   - Loop continues

4. **Verify**:
   - Student status = PENDING
   - Telegram message received
   - No deadlock (previously OTHER_FAIL had no handler → student stuck IN_PROGRESS)

### Scenario 6: HTTP Error (timeout / connection refused)

**Goal**: Verify HTTP error output is handled

1. Set the Python booker URL to an invalid address (or stop the Python service)
2. Trigger webhook
3. **Expected flow**:
   - `Book the Appointment` HTTP request fails
   - Error output routes to `Reset Student PENDING (other)` (NEW — previously went to `Update Slot → PENDING` with no student reset)
   - `Telegram: HTTP Error` sends message
   - Loop continues

4. **Verify**:
   - Student status = PENDING (not stuck in IN_PROGRESS)
   - lock_status eventually = "unlocked" (after loop completes)
   - Telegram message received

## Cleanup

After testing:
1. Reset any test students to status = PENDING
2. Verify lock_status = "unlocked"
3. Delete test executions from n8n history

## Links

- [Data Model](../data-model.md) — Entity definitions and state transitions
- [Webhook Contract](booker-webhook-contract.md) — HTTP request/response schema
- [Research](../research.md) — Architecture decisions and rationale
