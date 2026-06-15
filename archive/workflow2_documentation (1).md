# Workflow 2 — The Booker
## Full Technical Documentation

**Project:** Austrian Embassy Cairo — Automated Appointment Booking  
**Workflow:** 2 of 2 — Booker  
**Trigger:** HTTP POST from Workflow 1 via Webhook  
**Status:** 🔲 Ready to build  
**Last Updated:** April 28, 2026  

---

## 1. Background & Context

### 1.1 Why Workflow 2 Exists

Workflow 1 (The Watcher) runs every 20 seconds and detects available appointment slots on the Austrian Embassy Cairo booking system. When slots are found, Workflow 1 has already:
- Navigated to the booking form
- Selected the first available slot
- Extracted all CAPTCHA fields and form tokens
- Sent the full session data to Workflow 2 via HTTP POST

Workflow 2 receives this live session data and immediately books appointments for all pending students before the session expires or slots get taken.

### 1.2 The Core Challenge

The entire booking sequence must complete quickly because:
- Slots disappear within ~30 seconds of becoming available
- The ASP.NET session is tied to the CAPTCHA — if the session dies, the CAPTCHA is invalid
- Multiple students may need to be booked across multiple slots in one execution cycle

### 1.3 How Workflow 1 Connects

The final node in Workflow 1 (`Extract Booking Form Data`) sends an HTTP POST to:
```
POST https://n8n.acmeai.crabdance.com/webhook/booker
Content-Type: application/json
```

---

## 2. Critical Findings from Real Submission Analysis

These were discovered by analyzing a real curl submission to `/HomeWeb/Appointment`:

### 2.1 DSGVOAccepted must be sent TWICE

The browser sends the GDPR consent field twice — this is standard HTML checkbox behavior where both the checkbox value and a hidden fallback value are submitted:
```
DSGVOAccepted=true
DSGVOAccepted=false
```
Both must be included in the POST body in this exact order. Sending only `true` or only `false` will likely cause a validation error.

### 2.2 BDC_BackWorkaround_Captcha flips between 0 and 1

In the real submission curl the value was `1`, but in the fresh form response HTML it is `0`. This field alternates per page load. Always extract it dynamically from the booking form HTML rather than hardcoding it. The `Extract Fresh CAPTCHA` node in Workflow 2 handles this correctly.

### 2.3 Date formats

Two different date formats are used on the same form:
- `DateOfBirth` — no zero-padding: `3/11/2016` (M/D/YYYY)
- `TraveldocumentDateOfIssue` — zero-padded month: `04/21/2026` (MM/DD/YYYY)
- `TraveldocumentValidUntil` — zero-padded month: `04/28/2026` (MM/DD/YYYY)

Store dates in the applicants table in `M/D/YYYY` format and let the Code node handle any formatting needed.

### 2.4 Command=Next is NOT sent in the booking body

Unlike the scheduler POST which uses `Command=Next`, the `/HomeWeb/Appointment` POST does not include a Command field. The submit button name is `Command` but the value `Next` comes from the button ID `nextButton`. The server processes the form without it in the body. **Do not include Command in the booking POST.**

### 2.5 Success detection via page title

The server returns the same form page (`title: "Austrian appointment system - Capture data"`) when the submission fails — whether due to CAPTCHA error, validation error, or slot taken. A successful booking will return a different page title (confirmation page). This is the most reliable detection method:

```javascript
// SUCCESS: title does NOT contain "Capture data"
// FAIL: title contains "Capture data" — form returned
const isSuccess = !html.includes('Capture data');
```

When the form returns, it includes a **fresh CAPTCHA** — meaning a CAPTCHA retry can happen immediately without re-selecting the slot.

### 2.6 Error types when form returns

When the form comes back, check for these specific patterns:
- **CAPTCHA wrong**: `html.includes('BDC_VCID_Captcha')` — form returned with new CAPTCHA, no explicit error text
- **Slot taken**: `html.includes('no appointments available')` or `html.includes('keine Termine')`  
- **Validation error**: `html.includes('validation-summary-errors')` or `html.includes('field-validation-error')`

### 2.7 Fresh CAPTCHA on retry

When the form returns after a failed submission, it contains a **completely new CAPTCHA** (`BDC_VCID_Captcha`, `BDC_Hs_Captcha`, `BDC_SP_Captcha`, `Token` all change). This means for CAPTCHA retries, we don't need to re-select the slot — just:
1. Extract new CAPTCHA fields from the returned HTML
2. Download new CAPTCHA image
3. Solve with Gemini
4. Resubmit

---

## 3. Data Tables

### 3.1 `applicants` Table (24 columns — exact CSV column names)

| Column | Example Value | Notes |
|---|---|---|
| `Lastname` | `AHMED` | As in passport |
| `Firstname` | `MOHAMED` | As in passport |
| `DateOfBirth` | `1/15/2000` | M/D/YYYY no zero-padding |
| `TraveldocumentNumber` | `A12345678` | Used as unique key |
| `Sex` | `1` | 1=Male, 2=Female |
| `Street` | `15 Tahrir St` | |
| `Postcode` | `11511` | |
| `City` | `Cairo` | |
| `Country` | `65` | Egypt = 65 |
| `Telephone` | `+201001234567` | |
| `Email` | `mohamed@email.com` | |
| `LastnameAtBirth` | `AHMED` | |
| `NationalityAtBirth` | `71` | Egypt = 71 |
| `CountryOfBirth` | `71` | Egypt = 71 |
| `PlaceOfBirth` | `Cairo` | |
| `NationalityForApplication` | `71` | Egypt = 71 |
| `TraveldocumentDateOfIssue` | `1/15/2020` | M/D/YYYY |
| `TraveldocumentValidUntil` | `1/15/2030` | M/D/YYYY |
| `TraveldocumentIssuingAuthority` | `71` | Egypt = 71 |
| `status` | `PENDING` | PENDING / IN_PROGRESS / COMPLETED / FAILED |
| `bookedSlot` | `5/5/2026 12:00:00 PM` | Filled after success |
| `bookedAt` | `2026-04-26T12:00:45Z` | ISO timestamp |
| `notifyTelegram` | `+201001234567` | For per-student notification |

**Student Status Flow:**
```
PENDING → IN_PROGRESS → COMPLETED
                      ↘ PENDING  (slot taken or captcha exhausted)
                      ↘ FAILED   (permanent error)
```

### 3.2 `Slots blacklist` Table (10 columns — exact CSV column names)

| Column | Example Value | Notes |
|---|---|---|
| `slotValue` | `5/5/2026 12:00:00 PM` | Unique slot key |
| `weekLabel` | `4/27/2026 - 5/3/2026` | |
| `office` | `KAIRO` | |
| `calendarId` | `44281520` | |
| `detectedAt` | `2026-04-26T11:59:00Z` | ISO timestamp |
| `status` | `PENDING` | PENDING / IN_PROGRESS / BOOKED / SLOT_TAKEN / FAILED |
| `assignedToStudentId` | `A12345678` | TraveldocumentNumber |
| `attemptedAt` | `2026-04-26T12:00:15Z` | ISO timestamp |
| `result` | `SUCCESS` | SUCCESS / SLOT_TAKEN / CAPTCHA_FAIL / OTHER_FAIL |

---

## 4. Webhook Input Payload

```json
{
  "sessionCookie": "AspxAutoDetectCookieSupport=1; ASP.NET_SessionId=...",
  "foundSlots": ["5/5/2026 12:00:00 PM", "5/6/2026 9:30:00 AM"],
  "firstSlot": "5/5/2026 12:00:00 PM",
  "captchaImageUrl": "https://appointment.bmeia.gv.at/BotDetectCaptcha.ashx?get=image&c=Captcha&t=...",
  "BDC_VCID_Captcha": "536aa500a3ec4d659d0f9b547385108c",
  "BDC_Hs_Captcha": "8c1637bd699774591a5307a69303fa123f97d29e",
  "BDC_SP_Captcha": "1110986155",
  "BDC_BackWorkaround_Captcha": "0",
  "token": "253B95AD...",
  "startTime": "5/5/2026 12:00:00 PM",
  "bookingEndpoint": "https://appointment.bmeia.gv.at/HomeWeb/Appointment",
  "Office": "KAIRO",
  "CalendarId": "44281520",
  "Language": "en",
  "currentMonday": "4/27/2026 12:00:00 AM",
  "weekLabel": "4/27/2026 - 5/3/2026",
  "retryCount": 0,
  "currentSlot": null
}
```

> `retryCount` and `currentSlot` are used only on CAPTCHA retry calls. On the first call from Workflow 1 they are absent — code defaults them to 0 and null.

---

## 5. Complete Booking POST Body

Based on real curl analysis, the exact POST body to `/HomeWeb/Appointment`:

```
Office=KAIRO
Language=en
CalendarId=44281520
Token={token}
PersonCount=1
StartTime={startTime}
Lastname={student.Lastname}
Firstname={student.Firstname}
DateOfBirth={student.DateOfBirth}
TraveldocumentNumber={student.TraveldocumentNumber}
Sex={student.Sex}
Street={student.Street}
Postcode={student.Postcode}
City={student.City}
Country={student.Country}
Telephone={student.Telephone}
Email={student.Email}
LastnameAtBirth={student.LastnameAtBirth}
NationalityAtBirth={student.NationalityAtBirth}
CountryOfBirth={student.CountryOfBirth}
PlaceOfBirth={student.PlaceOfBirth}
NationalityForApplication={student.NationalityForApplication}
TraveldocumentDateOfIssue={student.TraveldocumentDateOfIssue}
TraveldocumentValidUntil={student.TraveldocumentValidUntil}
TraveldocumentIssuingAuthority={student.TraveldocumentIssuingAuthority}
DSGVOAccepted=true        ← sent first
DSGVOAccepted=false       ← sent second (both required)
BDC_VCID_Captcha={BDC_VCID_Captcha}
BDC_BackWorkaround_Captcha={BDC_BackWorkaround_Captcha}
BDC_Hs_Captcha={BDC_Hs_Captcha}
BDC_SP_Captcha={BDC_SP_Captcha}
CaptchaText={solvedCaptcha}
```

**Headers required:**
```
Cookie: {sessionCookie}
Content-Type: application/x-www-form-urlencoded
Origin: https://appointment.bmeia.gv.at
Referer: https://appointment.bmeia.gv.at/HomeWeb/Scheduler
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```

---

## 6. Complete Node Flow (20 nodes)

### Phase 1 — Entry & Deduplication

**Node 1: Webhook**
- Path: `/booker`
- Method: POST
- Immediately responds 200 OK (parallel with processing)

**Node 1b: Respond to Webhook**
- Responds instantly so Workflow 1 doesn't wait
- Connected in parallel from Webhook node

**Node 2: Get All Blacklisted Slots**
- n8n Data Table: Get Many from `Slots blacklist`
- Returns all rows (returnAll: true)

**Node 3: Filter Fresh Slots (Code)**
```javascript
const payload = $('Webhook').first().json.body;
const retryCount = payload.retryCount || 0;

// Retry path — skip filtering, use currentSlot directly
if (retryCount > 0) {
  return [{ json: { ...payload, freshSlots: [payload.currentSlot], isRetry: true, retryCount } }];
}

// Normal path — filter against blacklist
const blacklist = $input.all().map(r => r.json.slotValue).filter(Boolean);
const freshSlots = (payload.foundSlots || []).filter(s => !blacklist.includes(s));
return [{ json: { ...payload, freshSlots, isRetry: false, retryCount: 0 } }];
```

**Node 4: IF — Any fresh slots?**
- Condition: `freshSlots.length > 0`
- FALSE → Stop (all slots already known)
- TRUE → continue

### Phase 2 — Bulk Slot Registration

**Node 5: Prepare Slots for Insert (Code)**
- Explodes `freshSlots` array into one item per slot
- Each item carries `_payload` (full webhook data) for later reconstruction

**Node 6: Insert ALL Slots to Blacklist**
- n8n Data Table: Create record in `Slots blacklist`
- Inserts all fresh slots with `status=PENDING` before any booking attempt
- Guarantees deduplication even if workflow crashes mid-execution

**Node 7: Rebuild Payload After Insert (Code)**
- Collapses multiple items back to one
- Restores full payload from `_payload`

### Phase 3 — Slot Loop

**Node 8: Split Slots (Code)**
- Explodes `freshSlots` into individual items
- Each item gets `currentSlot` and `slotIndex`

**Node 9: Process One Slot at a Time**
- n8n Split In Batches node
- Batch size: 1
- Ensures sequential processing

**Node 10: Get Next PENDING Student**
- n8n Data Table: Get Many from `applicants`
- Filter: `status = PENDING`
- Limit: 1

**Node 11: IF — Student found?**
- Checks `TraveldocumentNumber` is not empty
- FALSE → Telegram "No more pending students" → Stop
- TRUE → continue

**Node 12: Merge Student + Slot Data (Code)**
- Combines student record with slot/session data
- Output: single item with both `student` object and all session fields

**Node 13: Update Slot → IN_PROGRESS**
- n8n Data Table: Update in `Slots blacklist`
- Lookup: `slotValue = currentSlot`
- Set: `status=IN_PROGRESS`, `assignedToStudentId=student.TraveldocumentNumber`, `attemptedAt=now`

**Node 14: Update Student → IN_PROGRESS**
- n8n Data Table: Update in `applicants`
- Lookup: `TraveldocumentNumber = student.TraveldocumentNumber`
- Set: `status=IN_PROGRESS`

### Phase 4 — CAPTCHA Solve

**Node 15: POST Select Slot**
- HTTP POST to `https://appointment.bmeia.gv.at/HomeWeb/Scheduler`
- Body: Language, Office, CalendarId, PersonCount=1, Monday, `Start={currentSlot}`, `Command=Next`
- Gets fresh booking form with new CAPTCHA
- Response: full response + text format

**Node 16: Extract Fresh CAPTCHA (Code)**
- Parses HTML from Node 15
- Extracts: `BDC_VCID_Captcha`, `BDC_Hs_Captcha`, `BDC_SP_Captcha`, `BDC_BackWorkaround_Captcha`, `Token`, `StartTime`, `bookingEndpoint`
- Builds `captchaImageUrl`
- Throws error if VCID not found (booking form didn't load)

**Node 17: GET CAPTCHA Image**
- HTTP GET to `captchaImageUrl`
- Header: `Cookie: {sessionCookie}`
- Response format: **file**
- Output property: `captcha.png`

**Node 18: Solve CAPTCHA with Gemini**
- Model: `models/gemini-2.0-flash`
- Credential: `agent7`
- Input type: binary, property: `captcha.png`
- Prompt: `"Return ONLY the CAPTCHA code exactly as shown in the image, nothing else. No explanation, no punctuation, just the characters."`

**Node 19: Build Booking Body (Code)**
- Extracts CaptchaText from Gemini response: `content.parts[0].text.trim()`
- Assembles complete booking object with all student + CAPTCHA + session fields

### Phase 5 — Booking Submission

**Node 20: POST /HomeWeb/Appointment**
- HTTP POST to `bookingEndpoint`
- Headers: Cookie, Content-Type: form-urlencoded, Origin, Referer, User-Agent
- Body: ALL fields from Node 19
- **DSGVOAccepted sent twice** (true then false) — uses two separate body parameters with same name
- Response: full response + text

**Node 21: Check Booking Response (Code)**
```javascript
const html = response.body || response.data || '';

// Primary detection: page title
// Success = confirmation page (title changes)
// Fail = form returned (title stays "Capture data")
const isCapturePage = html.includes('Austrian appointment system - Capture data');

if (!isCapturePage) {
  return 'SUCCESS'; // confirmation page
}

// Form returned — determine why
if (html.includes('no appointments available') || html.includes('keine Termine')) {
  return 'SLOT_TAKEN';
}
if (html.includes('validation-summary-errors') || html.includes('field-validation-error')) {
  return 'OTHER_FAIL';
}
// Default: CAPTCHA was wrong (form returned with new CAPTCHA, no explicit error)
return 'CAPTCHA_FAIL';
```

> **On CAPTCHA_FAIL**: The returned HTML contains a fresh CAPTCHA. Extract it and retry immediately without re-selecting the slot.

### Phase 6 — Result Handling

**Node 22: Switch (4 branches)**

**Branch A — SUCCESS:**
- Update `applicants`: `status=COMPLETED`, `bookedSlot=currentSlot`, `bookedAt=now`
- Update `Slots blacklist`: `status=BOOKED`, `result=SUCCESS`
- Telegram: `✅ APPOINTMENT BOOKED!\nStudent: [Name]\nSlot: [slot]\nOffice: KAIRO`

**Branch B — CAPTCHA_FAIL:**
- Check `retryCount`:
  - `< 3`: Increment retryCount, extract fresh CAPTCHA from response HTML, POST back to `/webhook/booker` with updated payload including `currentSlot` and `retryCount`
  - `>= 3`: Update student → PENDING, Update slot → FAILED/CAPTCHA_FAIL, Telegram error

**Branch C — SLOT_TAKEN:**
- Update `applicants`: `status=PENDING`
- Update `Slots blacklist`: `status=SLOT_TAKEN`, `result=SLOT_TAKEN`
- Telegram: `⚠️ Slot taken — [Name] returned to queue`
- Loop continues to next batch (next slot)

**Branch D — OTHER_FAIL:**
- Update `applicants`: `status=PENDING`
- Update `Slots blacklist`: `status=FAILED`, `result=OTHER_FAIL`
- Telegram: `❌ Error for [Name]: [error text]`

---

## 7. CAPTCHA Retry Architecture

On CAPTCHA failure the response HTML already contains a new CAPTCHA. The retry avoids re-selecting the slot:

```
CAPTCHA_FAIL detected
  ↓
Extract fresh CAPTCHA fields from response HTML (same Code node as Node 16)
  ↓
retryCount < 3?
  YES → POST to /webhook/booker with:
        {
          ...originalPayload,
          currentSlot: slot,
          retryCount: retryCount + 1,
          isRetry: true,
          // Fresh CAPTCHA fields already extracted
          BDC_VCID_Captcha: newVCID,
          BDC_Hs_Captcha: newHs,
          BDC_SP_Captcha: newSP,
          BDC_BackWorkaround_Captcha: newBW,
          token: newToken,
          captchaImageUrl: newUrl
        }
        → Workflow 2 restarts at Node 1
        → Node 3 detects isRetry=true, skips blacklist filter
        → Goes straight to Node 9 (Split), uses currentSlot
        → Node 13/14 update already-IN_PROGRESS slot/student (no-op)
        → Proceeds to CAPTCHA solve + booking
  NO  → Give up, reset student + slot
```

---

## 8. Workflow 1 — Final Node to Add

Add this node at the end of Workflow 1 after `Extract Booking Form Data`:

**Node: Send to Booker Webhook**
- Type: HTTP Request
- Method: POST
- URL: `https://n8n.acmeai.crabdance.com/webhook/booker`
- Body type: JSON
- Body: `={{ JSON.stringify($json) }}`

---

## 9. Credentials & Configuration

| Item | Value |
|---|---|
| Webhook path | `/booker` |
| Webhook full URL | `https://n8n.acmeai.crabdance.com/webhook/booker` |
| Gemini credential | `agent7` (id: `SjzEIsq5bEXWRHRD`) |
| Gemini model | `models/gemini-2.0-flash` |
| Telegram credential | `Telegram account` (id: `YqEjQvYeHBuiR4Cl`) |
| Telegram chat ID | `1377621332` |
| Applicants table name | `applicants` |
| Blacklist table name | `Slots blacklist` |
| Booking endpoint (Kairo) | `https://appointment.bmeia.gv.at/HomeWeb/Appointment` |
| Egypt Country ID (Country field) | `65` |
| Egypt Nationality ID (all nationality fields) | `71` |

---

## 10. Timing Analysis

| Step | Estimated Time |
|---|---|
| Webhook receive + blacklist query | ~0.5s |
| Insert all slots to blacklist | ~1s |
| Per slot: query student + update tables | ~1s |
| Per slot: POST select slot | ~1-2s |
| Per slot: GET CAPTCHA image | ~0.5s |
| Per slot: Gemini solve | ~2-3s |
| Per slot: POST booking form | ~1-2s |
| Per slot: update tables + Telegram | ~1s |
| **Total per slot** | **~8-11 seconds** |

With 3 slots and 3 students: ~25-33 seconds total. Well within session lifetime.

---

## 11. Remaining Tasks

- [ ] Build Workflow 2 JSON and import into n8n
- [ ] Add "Send to Booker Webhook" node to end of Workflow 1
- [ ] Test with Copenhagen config (safe test — different visa type)
- [ ] Fill real student data in `applicants` table
- [ ] Switch Workflow 1 config to KAIRO and activate both workflows
- [ ] Monitor first real Kairo execution

---

## 12. File Index

| File | Description |
|---|---|
| `austria_watcher_FINAL.json` | Workflow 1 — final n8n export |
| `austria_appointment_bot_documentation.md` | Workflow 1 full documentation |
| `workflow2_documentation.md` | This file |
| `applicants.csv` | Applicants data table sample |
| `slots_blacklist.csv` | Slots blacklist data table sample |
