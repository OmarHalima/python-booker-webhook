# Data Model: n8n Workflow Analysis and Fix

## Date: 2026-07-11

---

## Overview

The booker workflow operates on two n8n data tables and one external service. No new tables are created. The Slots blacklist table is removed from the workflow (no reads, no writes).

## Entities

### 1. Applicants Table (n8n data table, name: `applicants`)

Student records with personal data and booking status.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| Lastname | string | yes | Student last name |
| Firstname | string | yes | Student first name |
| DateOfBirth | string | yes | Date of birth |
| TraveldocumentNumber | string | yes | Passport/travel doc number |
| Sex | number | yes | Sex (numeric code) |
| Street | string | yes | Street address |
| Postcode | number | yes | Postal code |
| City | string | yes | City |
| Country | number | yes | Country (numeric code) |
| Telephone | number | yes | Phone number |
| Email | string | yes | Email address |
| LastnameAtBirth | string | yes | Last name at birth |
| NationalityAtBirth | number | yes | Nationality at birth (code) |
| CountryOfBirth | number | yes | Country of birth (code) |
| PlaceOfBirth | string | yes | Place of birth |
| NationalityForApplication | number | yes | Nationality for application (code) |
| TraveldocumentDateOfIssue | string | yes | Travel doc issue date |
| TraveldocumentValidUntil | string | yes | Travel doc expiry date |
| TraveldocumentIssuingAuthority | number | yes | Issuing authority (code) |
| **status** | string | yes | **PENDING → IN_PROGRESS → COMPLETED / ALREADY_BOOKED** |
| bookedSlot | string | no | Slot value that was booked |
| bookedAt | string | no | ISO timestamp of booking |
| notifyTelegram | number | no | Telegram notification flag |

#### State Transitions

```
PENDING → IN_PROGRESS  (when assigned to a slot for booking)
IN_PROGRESS → COMPLETED  (SUCCESS from Python booker)
IN_PROGRESS → ALREADY_BOOKED  (ALREADY_BOOKED from Python booker)
IN_PROGRESS → PENDING  (CAPTCHA_FAIL / SLOT_TAKEN / OTHER_FAIL / BOOKING_SERVER_BUSY / HTTP_ERROR)
COMPLETED  [terminal]
ALREADY_BOOKED  [terminal]
```

**Rule**: A student in IN_PROGRESS is actively being booked. If any failure occurs, they return to PENDING for the next cycle. No student is ever left in IN_PROGRESS.

### 2. Lock Status Table (n8n data table, ID: `nvSE5JHoAyn6BT3E`)

Single-row distributed lock for mutual exclusion.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| **lock_status** | string | yes | `locked` or `unlocked` |

#### State Transitions

```
unlocked → locked  (lock acquired at workflow start)
locked → unlocked  (lock released at workflow end / loop done)
```

**Rule**: Lock is acquired exactly once per workflow execution and released exactly once. If the lock is already `locked` when the webhook fires, the `Filter` node blocks execution and the webhook is dropped.

### 3. Python Booker Service (external, HTTP POST)

Not a data entity — consumed as an external service.

| Endpoint | Method | Timeout | Purpose |
|----------|--------|---------|---------|
| `/webhook/booker` | POST | 300000ms | Book an appointment on BMEIA via Playwright |

**Request contract**: See `contracts/booker-webhook-contract.md`

**Response**: JSON with `ok`, `resultType`, `message`, `attempt`, `captchaText`, `finalUrl`, `timestamp`

### 4. Removed: Slots Blacklist Table (was ID: `B0yhFJlFzV7EozJz`)

**STATUS: REMOVED FROM WORKFLOW**

Previously tracked slot-level status (PENDING, IN_PROGRESS, BOOKED, SLOT_TAKEN, CAPTCHA_FAIL). No longer read or written by the booker workflow.

**Rationale**: BMEIA removes booked slots from the calendar instantly, making dedup unnecessary. Retry of old PENDING slots fails because session cookies are session-bound. See `research.md` Task 1.

## Relationships

```
Watcher webhook payload
  └─ contains: foundSlots[], Office, CalendarId, sessionCookie, token, BDC_* captcha fields
       │
       ▼
Booker workflow
  ├─ reads: applicants (Get Next PENDING Student)
  ├─ reads: lock_status (get_lock_status)
  ├─ writes: lock_status (acquire → release)
  ├─ writes: applicants (IN_PROGRESS → COMPLETED/PENDING/ALREADY_BOOKED)
  └─ calls: Python booker HTTP (POST /webhook/booker)
```

No foreign keys between tables. The booker workflow does not track which slot was assigned to which student — the Python booker handles that internally. The applicants table only tracks student status, not slot status.

## Validation Rules

1. **TraveldocumentNumber must be non-empty** — `Student Found?` node checks `{{ $json.TraveldocumentNumber }}` is not empty before proceeding to booking.
2. **Lock must be unlocked** — `Filter` node checks `lock_status != "locked"`. If locked, execution is silently dropped.
3. **foundSlots must be a non-empty array** — `Split Into Slots` maps `foundSlots` to items. If empty, the `splitInBatches` loop immediately hits "done" and releases the lock.
4. **resultType must be one of the 6 standardized types** — `Result Switch` routes to the correct handler. Unknown types fall to the `extra` (fallback) output which handles them as OTHER_FAIL.
