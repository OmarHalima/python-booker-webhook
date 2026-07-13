# Specification: n8n Workflow Analysis and Fix

## Overview

Analyze the two n8n workflows (booker and watcher) used in the Austrian appointment automation system, identify all logical errors, structural problems, data-flow inconsistencies, and failure-handling gaps, and update them to be logically correct and reliably working.

## Clarifications

### Session 2026-07-11

- Q: What should the watcher schedule trigger be named and set to? → A: Named "Every 5 Seconds" with 5-second interval.
- Q: Should the Slots blacklist table be kept? → A: **Remove entirely.** BMEIA removes booked slots from the calendar instantly, making dedup unnecessary. Retry of old PENDING slots fails because session cookies are session-bound. The lock prevents concurrent execution. Only applicants + lock_status tables are needed.
- Q: Should the FALSE path (reprocessing old PENDING slots) be kept? → A: **No.** Only process fresh slots from the current webhook. BMEIA is the source of truth.

## Actors

- **System Administrator**: Monitors Telegram alerts, manages applicant data, configures office/calendar parameters
- **Watcher Workflow**: Scheduled workflow that polls BMEIA appointment scheduler for available slots
- **Booker Workflow**: Webhook-triggered workflow that receives slot notifications and calls the Python booker service

## Functional Requirements

### Booker Workflow (latest booker.json)

1. **Lock acquisition**: Before processing, acquire a distributed lock (once per execution) to prevent concurrent execution. If lock is already held, abort immediately. Lock is released once when the booking loop completes.
2. **Slot splitting**: Split the incoming `foundSlots` array from the webhook payload into one item per slot for sequential processing.
3. **Sequential student assignment**: For each slot (one at a time via splitInBatches), fetch the next PENDING student from the applicants table and assign them. If no PENDING student exists, notify and end the loop.
4. **Student validation**: Verify the student has a valid TraveldocumentNumber before proceeding. If missing, skip to next slot.
5. **Student state tracking**: Mark the student as IN_PROGRESS before dispatching to the Python booker.
6. **Result routing**: After receiving the booking result, correctly route to one of seven handlers based on resultType:
   - SUCCESS → Mark student COMPLETED, send Telegram success notification
   - CAPTCHA_FAIL → Reset student to PENDING, send Telegram captcha alert
   - SLOT_TAKEN → Reset student to PENDING, send Telegram taken alert
   - ALREADY_BOOKED → Mark student ALREADY_BOOKED, send Telegram already-booked alert
   - OTHER_FAIL → Reset student to PENDING, send Telegram failure alert
   - BOOKING_SERVER_BUSY → Reset student to PENDING, send Telegram server-busy alert
   - HTTP_ERROR → Reset student to PENDING, send Telegram HTTP-error alert
7. **Lock release**: Always release the distributed lock after the booking loop completes, regardless of outcomes.
8. **HTTP error handling**: The HTTP Request node must be configured with `onError: continueErrorOutput` and its error output must reset the student to PENDING and send a Telegram alert. No result type or HTTP error leaves a student in IN_PROGRESS.

### Watcher Workflow (latest watcher.json)

10. **Scheduled scanning**: Run every 5 seconds. The trigger node must be named "Every 5 Seconds" to match its actual interval.
11. **Week-scroll pagination**: Starting from the current week, POST to the BMEIA scheduler API for each week, extract radio-button slots from HTML, and continue to "Next week" until slots are found or max weeks are scanned.
12. **Session cookie management**: Extract the ASP.NET_SessionId from Set-Cookie headers on each response and pass it forward. Remove hardcoded session IDs.
13. **Slot extraction**: Parse all `<input type="radio" name="Start" value="...">` elements from the HTML response. Handle URL-encoded values.
14. **Done condition**: Stop scanning when any of: slots found, no "Next week" button present, or max weeks reached.
15. **CalendarId validation**: Before proceeding to booking, verify the configured CalendarId exists in the dropdown. If not found, notify and skip.
16. **Slot selection**: POST to select the first available slot, which triggers the appointment form with captcha fields.
17. **Captcha data extraction**: Extract BDC_VCID_Captcha, BDC_Hs_Captcha, BDC_SP_Captcha, BDC_BackWorkaround_Captcha, and Token from the booking form HTML.
18. **Webhook dispatch**: Send all extracted data (student info, slot, session, captcha fields, form action) to the booker webhook endpoint.
19. **Watcher booking delegation**: After slot selection and captcha form data extraction, the watcher sends the complete payload (student info, slot, session, captcha fields) to the booker webhook for actual booking execution.

## User Scenarios

### Scenario 1: Successful booking cycle
1. Watcher detects 3 available slots in week 2
2. Watcher sends webhook payload with foundSlots to booker
3. Booker acquires lock, splits foundSlots into individual slots
4. Booker fetches first slot, fetches next PENDING student
5. Booker marks student IN_PROGRESS
6. Booker POSTs to Python service → SUCCESS
7. Booker marks student COMPLETED, sends Telegram success notification
8. Loop continues for next slot (or ends if no more slots)
9. When all slots processed, booker releases lock, sends Telegram "All Done"

### Scenario 2: Captcha failure
1. Booker attempts booking, Python service returns CAPTCHA_FAIL
2. Booker resets student to PENDING, sends Telegram captcha alert
3. Loop continues to next slot (or ends)
4. When done, booker releases lock
5. Student remains PENDING and will be reassigned in next cycle

### Scenario 3: Slot already taken
1. Booker attempts booking, Python service returns SLOT_TAKEN
2. Booker resets student to PENDING, sends Telegram taken alert
3. Loop continues to next slot
4. When done, booker releases lock
5. Student remains PENDING and will be reassigned to next available slot

### Scenario 4: No PENDING students left
1. Booker receives webhook with foundSlots
2. Booker acquires lock, splits slots, starts loop
3. Booker fetches next PENDING student → none found
4. Booker sends "No more PENDING students" Telegram notification
5. Loop ends → booker releases lock, sends "All Done"

### Scenario 5: Watcher detects no slots
1. Watcher scans all max weeks
2. No slots found
3. Watcher logs "no slots" and waits for next schedule trigger

### Scenario 6: Concurrent execution prevented
1. Booker receives webhook while previous execution is still running
2. Lock check finds lock_status = "locked"
3. Booker aborts immediately, releases nothing

## Success Criteria

- **Booking completion rate**: 90%+ of attempted bookings with valid slots and PENDING students result in SUCCESS — *post-launch production metric, not testable via quickstart scenarios*
- **No concurrent executions**: Lock mechanism prevents 100% of overlapping executions
- **No duplicate student assignment**: No student is ever assigned to more than one slot simultaneously
- **No stale locks**: Lock is released after the booking loop completes — *note: per-execution hold time = (number of slots × max time per slot, up to 300s). The 30-second target is per-slot, not per-execution. A batch of 8 slots with 70s CAPTCHA_FAIL each will hold the lock for ~9 minutes*
- **Watcher scan completeness**: Each scan covers up to MaxWeeksToScan weeks or until slots found
- **Watcher extracts booking form data**: Watcher successfully extracts captcha hidden fields (BDC_VCID, BDC_Hs, BDC_SP, BDC_BW) and Token from the booking form after slot selection
- **Correct routing**: Every resultType from the booker webhook response routes to the correct handler without ambiguity
- **No dead nodes**: Every node in both workflows is connected and serves a purpose

## Key Entities

- **Applicants (data table)**: Student records with personal data, status (PENDING, IN_PROGRESS, COMPLETED, ALREADY_BOOKED), bookedSlot, and notification flags
- **Lock status (data table)**: Simple single-row table with lock_status (locked/unlocked) for distributed mutual exclusion
- **Python booker service**: External HTTP service at POST /webhook/booker that performs the actual Playwright-based booking on BMEIA

## Assumptions

- Two distinct webhook URLs serve two different hops: (1) Watcher → Booker: the n8n webhook URL `https://n8n.acmeai.crabdance.com/webhook/booker` (the watcher sends foundSlots to this endpoint); (2) Booker → Python booker: the Python service URL `https://porthole-portfolio-napped.ngrok-free.dev/webhook/booker` (the n8n booker workflow's `Book the Appointment` HTTP node POSTs to this endpoint to trigger the Playwright booking). The Python booker URL changes when the ngrok tunnel restarts and must be updated in the `Book the Appointment` node after import.
- Office constants: KOPENHAGEN for dev/testing, KAIRO for production
- The blacklist data table (`B0yhFJlFzV7EozJz`) is no longer used — BMEIA is the source of truth for slot availability; the applicant table is the source of truth for student status
- The BMEIA appointment scheduler endpoint is `https://appointment.bmeia.gv.at/HomeWeb/Scheduler`
- The watcher schedule interval is 5 seconds (low-latency detection is prioritized over API rate concerns)
- MaxWeeksToScan defaults to 12 weeks
- BMEIA removes booked slots from the calendar instantly, so dedup against a local table is unnecessary
- The lock prevents concurrent booker executions; skipped webhook invocations (while locked) are dropped, not queued

## Dependencies

- Python booker webhook service must be running and accessible
- Telegram bot credentials must be configured for notifications
- Data tables (applicants, lock_status) must exist in the n8n data store
- BMEIA appointment system must be accessible from the n8n instance
