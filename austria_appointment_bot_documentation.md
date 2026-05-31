# Austrian Embassy Appointment Bot — Project Documentation

**Project:** Automated appointment watcher & booker for the Austrian Embassy Cairo  
**Target:** "Aufenthaltsbewilligung Student (nur Bachelor)" (Student Residence Permit — Bachelor only)  
**Site:** https://appointment.bmeia.gv.at/HomeWeb/Scheduler  
**Status:** Workflow 1 (Watcher) — ✅ Complete & Verified | Workflow 2 (Booker) — 🔲 In Progress

---

## 1. Problem Statement

Egyptian students applying for an Austrian student residence permit must book an appointment at the Austrian Embassy in Cairo through the online appointment system. The system releases only 2–3 appointment slots at unpredictable times, and those slots are claimed within approximately 30 seconds by the large volume of applicants monitoring the site. Manual monitoring is practically impossible.

**Goal:** Build a fully automated n8n workflow that:
1. Continuously checks the scheduler for available slots
2. Immediately detects any slot the moment it appears
3. Automatically selects the slot, solves the CAPTCHA, and submits the booking form with the applicant's data
4. Notifies the user via Telegram at every stage

---

## 2. Site Architecture & Discovery

### 2.1 The Booking Flow (Browser)

Through analysis of captured curl requests and HTML responses, the full manual booking flow was reverse-engineered:

```
Step 1: GET /  →  Server sets AspxAutoDetectCookieSupport=1 (302 redirect)
Step 2: GET /?fromSpecificInfo=True  →  Homepage with office selector
Step 3: POST /HomeWeb/Scheduler  →  Scheduler page (week view with slots or "no slots")
Step 4: POST /HomeWeb/Scheduler (Command=Next week)  →  Navigate to next week
Step 5: POST /HomeWeb/Scheduler (Start=<slot>, Command=Next)  →  Select slot → booking form
Step 6: POST /HomeWeb/Appointment  →  Submit booking with personal data + CAPTCHA
```

### 2.2 Key Technical Findings

**Session management:**
- The server issues `AspxAutoDetectCookieSupport=1` on the first GET to `/`
- A fresh `ASP.NET_SessionId` is generated automatically on the **first POST** to `/HomeWeb/Scheduler` — no pre-existing session needed
- This means Steps 1 and 2 above are **not required** for headless automation. A single POST with `Cookie: AspxAutoDetectCookieSupport=1` is sufficient

**Slot detection:**
- When slots are available, the HTML contains `<input name="Start" type="radio" value="...">` elements
- When no slots exist, the HTML contains `<p class="message-error">For your selection there are unfortunately no appointments available</p>`
- The presence/absence of `<input type="submit" name="Command" value="Next week">` indicates whether more weeks can be scanned

**Kairo-specific behavior:**
- The Kairo office currently shows no available weeks beyond `7/6/2026`
- When the bot POSTs with `Command=Next`, the server jumps directly to the furthest available week (currently `7/6/2026`)
- `hasNextWeek: false` at this point is correct — it means the site itself has no more weeks to offer
- When slots open, they will appear on one of these weeks

**CAPTCHA:**
- Type: BotDetect image CAPTCHA (grayscale distorted text)
- The CAPTCHA appears on the booking form page (after slot selection)
- Required fields: `BDC_VCID_Captcha`, `BDC_Hs_Captcha`, `BDC_SP_Captcha`, `BDC_BackWorkaround_Captcha`, `CaptchaText`
- CAPTCHA image URL: `/BotDetectCaptcha.ashx?get=image&c=Captcha&t={BDC_VCID_Captcha}`
- Solving service: 2captcha (or CapSolver) via API — ~$3 per 1000 solves

**Booking form endpoints:**
- Student visa (Kairo): `POST /HomeWeb/Appointment`
- Passport/ID (Copenhagen): `POST /HomeWeb/AppointmentPassport`
- The endpoint is embedded in the form's `action` attribute — must be extracted dynamically

**Form fields for Kairo student visa:**

| Field | Type | Notes |
|---|---|---|
| `Office` | hidden | `KAIRO` |
| `Language` | hidden | `en` |
| `CalendarId` | hidden | `44281520` |
| `Token` | hidden | ASP.NET anti-forgery token — extracted from form |
| `PersonCount` | hidden | `1` |
| `StartTime` | hidden | Selected slot value |
| `Lastname` | text | |
| `Firstname` | text | |
| `DateOfBirth` | text | Format: `3/15/2000` |
| `TraveldocumentNumber` | text | Passport number |
| `Sex` | select | `1`=Male, `2`=Female |
| `Street` | text | |
| `Postcode` | text | |
| `City` | text | |
| `Country` | select | Egypt = `65` |
| `Telephone` | text | |
| `Email` | text | |
| `LastnameAtBirth` | text | Name at birth |
| `NationalityAtBirth` | select | Egypt = `71` |
| `CountryOfBirth` | select | Egypt = `71` |
| `PlaceOfBirth` | text | |
| `NationalityForApplication` | select | Egypt = `71` |
| `TraveldocumentDateOfIssue` | text | Format: `3/15/2020` |
| `TraveldocumentValidUntil` | text | Format: `3/15/2030` |
| `TraveldocumentIssuingAuthority` | select | Egypt = `71` |
| `DSGVOAccepted` | checkbox | Must be `true` |
| `BDC_VCID_Captcha` | hidden | From booking form |
| `BDC_BackWorkaround_Captcha` | hidden | `0` or `1` from form |
| `BDC_Hs_Captcha` | hidden | From booking form |
| `BDC_SP_Captcha` | hidden | From booking form |
| `CaptchaText` | text | Solved CAPTCHA answer (uppercase) |
| `Command` | submit | `Next` |

---

## 3. n8n Stack & Configuration

**Platform:** n8n (self-hosted, v2.15.1)  
**Instance:** `n8n.acmeai.crabdance.com`

### 3.1 Credentials

| Credential | Type | Status |
|---|---|---|
| Telegram Bot | Telegram API | ✅ Configured |
| 2captcha API Key | HTTP Header / Code node | 🔲 Pending |

### 3.2 Telegram Setup

- Bot created via @BotFather
- Chat ID: `1377621332` (numeric, not username)
- Credential name in n8n: `Telegram account`

---

## 4. Workflow 1 — The Watcher

**Status:** ✅ Complete and verified against both Kairo (no slots) and Copenhagen (slots found)

### 4.1 Node Map

```
Every 60 Seconds
  → Config
  → Set Cookie (hardcoded)
  → Pass Through Session
  → Init Week State
  → POST Scheduler (check week)
  → Parse HTML for Slots
  → Done scanning?
      TRUE  → Merge Results
      FALSE → POST Next Week → Parse Next Week HTML → Done (loop check)?
                  TRUE  → Merge Results
                  FALSE → POST Next Week (loops)
  → Merge Results
  → Slots found?
      TRUE  → Prepare Notification
                → Send Telegram Alert (parallel)
                → POST Select Slot
                    → Extract Booking Form Data  ← feeds Workflow 2
      FALSE → Log: No Slots Found
```

### 4.2 Node Details

**Config**
```javascript
const CONFIG = {
  Office: 'KAIRO',
  CalendarId: '44281520',
  PersonCount: '1',
  Language: 'en',
  MaxWeeksToScan: 12
};
return [{ json: CONFIG }];
```

**Set Cookie (hardcoded)**  
Sets `sessionCookie: 'AspxAutoDetectCookieSupport=1'`. No HTTP request needed — the server generates a fresh `ASP.NET_SessionId` on the first POST automatically.

**Init Week State**  
Calculates the current Monday's date and initializes loop state: `weekIndex: 0`, `slotsFound: false`, `done: false`.

**POST Scheduler (check week)**  
- URL: `https://appointment.bmeia.gv.at/HomeWeb/Scheduler`
- Method: POST
- Body: `Language`, `Office`, `CalendarId`, `PersonCount`, `Monday`, `Command=Next`
- Headers: `Cookie`, `Content-Type: application/x-www-form-urlencoded`, `Origin`, `Referer`, `User-Agent`
- Response format: **full response + text** (required to access both headers and body)

**Parse HTML for Slots**  
Extracts from HTML response:
- Session cookie from `Set-Cookie` response header
- Available slots via regex on `name="Start" type="radio"`
- Week label, Monday value, Token
- `hasNextWeek` flag (presence of `value="Next week"` button)
- Sets `done: true` if slots found, no more weeks, or max weeks reached

> ⚠️ **Critical:** n8n returns the response body in `response.data` (not `response.body`) when `responseFormat` is `text`. Always use `response.body || response.data || ''`

**Done scanning? / Done (loop check)?**  
IF nodes checking `$json.done === true`. True branch → Merge. False branch → POST Next Week.

**POST Next Week**  
Same as POST Scheduler but with `Command=Next week` and `Referer: .../HomeWeb/Scheduler`.

**Prepare Notification + Send Telegram Alert**  
Formats and sends a Telegram message with slot details. Connected in parallel with POST Select Slot so the notification fires immediately while slot selection proceeds.

**POST Select Slot**  
- Body includes `Start={{ $json.firstSlot }}` and `Command=Next`
- Returns the booking form HTML with CAPTCHA

**Extract Booking Form Data**  
Parses the booking form HTML to extract: `Token`, `StartTime`, `BDC_VCID_Captcha`, `BDC_Hs_Captcha`, `BDC_SP_Captcha`, `captchaImageUrl`, `bookingFormReady`.

### 4.3 Verified Outputs

**Kairo (no slots):**
```json
{
  "weekLabel": "7/6/2026 - 7/12/2026",
  "currentMonday": "7/6/2026 12:00:00 AM",
  "hasNextWeek": false,
  "hasError": true,
  "slotsFound": false,
  "done": true,
  "sessionCookie": "AspxAutoDetectCookieSupport=1; ASP.NET_SessionId=..."
}
```

**Copenhagen (slots found — test):**
```json
{
  "weekLabel": "4/20/2026 - 4/26/2026",
  "slotsFound": true,
  "foundSlots": [
    "4/21/2026 11:30:00 AM",
    "4/23/2026 11:00:00 AM",
    "4/24/2026 11:30:00 AM"
  ],
  "BDC_VCID_Captcha": "d7eace0ec4e24b40810f11e37cb75024",
  "BDC_Hs_Captcha": "1c5a0022f68fad5cc188ddffc426c4f700e6a08c",
  "BDC_SP_Captcha": "1271994736",
  "bookingFormReady": true,
  "captchaImageUrl": "https://appointment.bmeia.gv.at/BotDetectCaptcha.ashx?get=image&c=Captcha&t=d7eace0ec4e24b40810f11e37cb75024"
}
```

---

## 5. Problems Encountered & Solutions

| # | Problem | Cause | Solution |
|---|---|---|---|
| 1 | curl commands not working headlessly | Windows `^` escape syntax vs Linux `\` syntax | Identified correct 3-step curl sequence; tested in Git Bash |
| 2 | Session not established | Assumed GET homepage was required | Discovered POST alone with `AspxAutoDetectCookieSupport=1` creates session automatically |
| 3 | `weekLabel: "Week 1"` instead of actual date | Regex `Week [\d/]+ - [\d/]+` didn't match HTML format with spaces | Fixed to `Week\s+([\d]{1,2}\/[\d]{1,2}\/[\d]{4}\s*-\s*[\d]{1,2}\/[\d]{1,2}\/[\d]{4})` |
| 4 | Slots not detected despite HTML containing them | Parse node read `response.body` which was empty; n8n puts text response in `response.data` | Changed to `response.body \|\| response.data \|\| ''` in all parse nodes |
| 5 | POST Select Slot timing out | Telegram node hung (no credential) before reaching slot selection, expiring the session | Connected Telegram and POST Select Slot in **parallel** from Prepare Notification |
| 6 | `done: true` immediately on Kairo | Kairo has no weeks beyond 7/6/2026 so `hasNextWeek` is false on first response | This is correct behavior — site has nothing more to offer; watcher will catch slots when they appear |

---

## 6. Workflow 2 — The Booker

**Status:** 🔲 Not yet built  
**Trigger:** Output of "Extract Booking Form Data" from Workflow 1

### 6.1 What It Needs To Do

```
Receive booking form data (CAPTCHA fields, Token, StartTime, sessionCookie)
  → GET CAPTCHA image (binary, same session cookie)
  → POST image to 2captcha API (base64 encoded)
  → Poll 2captcha for result (every 3 seconds, max 30 seconds)
  → POST /HomeWeb/Appointment with:
      - All applicant personal data (hardcoded in Config node)
      - All CAPTCHA hidden fields
      - CaptchaText = solved answer (uppercase)
      - Command = Next
  → Check response for success confirmation
  → Send Telegram notification (success or failure)
```

### 6.2 2captcha Integration

```
Submit: POST https://2captcha.com/in.php
  Body: method=base64&body={base64_image}&key={API_KEY}&json=1
  Response: {"status":1,"request":"CAPTCHA_ID"}

Poll:  GET https://2captcha.com/res.php?key={API_KEY}&action=get&id={CAPTCHA_ID}&json=1
  Response (pending): {"status":0,"request":"CAPCHA_NOT_READY"}
  Response (ready):   {"status":1,"request":"SOLVED_TEXT"}
```

### 6.3 Required Before Building

- [ ] **2captcha API key** — sign up at 2captcha.com, add $3 credit
- [ ] **Applicant personal data** — all fields listed in Section 2.2

### 6.4 Timing Concern

The full booking sequence from slot detection to CAPTCHA submission takes approximately:
- Slot detection → POST Select Slot: ~2–3 seconds
- CAPTCHA fetch + 2captcha solve: ~8–12 seconds
- Total: ~10–15 seconds

Given slots disappear in ~30 seconds this is tight but feasible, especially since the session and slot selection happen before CAPTCHA solving begins.

---

## 7. Key Values Reference

| Item | Value |
|---|---|
| Kairo Office name | `KAIRO` |
| Kairo CalendarId | `44281520` |
| Kairo booking endpoint | `POST /HomeWeb/Appointment` |
| Copenhagen Office name | `KOPENHAGEN` |
| Copenhagen CalendarId | `9677702` |
| Copenhagen booking endpoint | `POST /HomeWeb/AppointmentPassport` |
| Country ID — Egypt (Country field) | `65` |
| Nationality ID — Egypt | `71` |
| Sex — Male | `1` |
| Sex — Female | `2` |
| DSGVOAccepted (GDPR consent) | `true` |
| Session cookie (static) | `AspxAutoDetectCookieSupport=1` |
| Polling interval | 60 seconds (can go to 20s on self-hosted) |

---

## 8. Remaining Tasks

- [ ] Build Workflow 2 (booker with CAPTCHA solving)
- [ ] Set up 2captcha account and add API key to n8n
- [ ] Provide applicant personal data for Kairo booking form
- [ ] Switch Config back to KAIRO after Copenhagen testing
- [ ] Test full end-to-end booking flow with Copenhagen (safe test — different visa type)
- [ ] Activate Workflow 1 in production (set to run every 20–60 seconds)
- [ ] Consider adding a deduplication check so the same slot isn't attempted twice
- [ ] Consider adding an error recovery branch if CAPTCHA solve fails (retry with new CAPTCHA)
- [ ] Consider hosting n8n on a server geographically close to Austria for lower latency

---

## 9. File Index

| File | Description |
|---|---|
| `austria_appointment_watcher_v3.json` | Latest Workflow 1 n8n export |
| `austria_appointment_bot_documentation.md` | This file |

---

## 10. Workflow 2 — Complete Architecture (Updated)

**Status:** 🔲 Ready to build  
**CAPTCHA Solver:** Gemini Vision (gemini-1.5-flash) — confirmed working, returns text in ~2-3 seconds  
**Gemini Credential:** `agent7` (id: `SjzEIsq5bEXWRHRD`)

---

### 10.1 The Slot-Student Loop Design

The key insight: when the watcher finds multiple slots AND there are multiple students, we want to match them efficiently:

```
Watcher finds N slots
  ↓
For each slot (slot 1, slot 2, slot 3...):
  → Find next PENDING student
  → Mark student as IN_PROGRESS
  → Attempt booking:
      SUCCESS → mark student COMPLETED, notify via Telegram
      FAIL (captcha wrong) → retry up to 3x with fresh captcha
      FAIL (slot taken) → mark student back to PENDING, try next slot
      FAIL (other) → mark student back to PENDING, log error
  → Continue to next slot + next student
```

This means:
- If 3 slots open and 5 students are waiting → 3 students get booked in one cycle
- If a slot gets taken mid-booking → student goes back to PENDING and gets the next slot
- The watcher keeps running every 20 seconds → if more slots open later, remaining students get picked up

---

### 10.2 Student State Machine

Each student has one of these states:

| State | Meaning |
|---|---|
| `PENDING` | Waiting to be booked |
| `IN_PROGRESS` | Currently being attempted (slot selected, filling form) |
| `COMPLETED` | Successfully booked — has appointment |
| `FAILED` | Permanent failure (e.g. passport invalid) — needs manual review |

State is stored using n8n's `$getWorkflowStaticData('global')` which persists between executions without any external database.

---

### 10.3 Applicant Data Table Node

Stored as a Code node called **"Applicants DB"** at the start of Workflow 2. Structure:

```javascript
const APPLICANTS = [
  {
    id: 1,
    // Personal data
    Lastname: 'AHMED',
    Firstname: 'MOHAMED',
    DateOfBirth: '1/15/2000',
    TraveldocumentNumber: 'A12345678',
    Sex: '1',                    // 1=Male, 2=Female
    Street: '15 Tahrir St',
    Postcode: '11511',
    City: 'Cairo',
    Country: '65',               // Egypt
    Telephone: '+201001234567',
    Email: 'student1@email.com',
    LastnameAtBirth: 'AHMED',
    NationalityAtBirth: '71',    // Egypt
    CountryOfBirth: '71',        // Egypt
    PlaceOfBirth: 'Cairo',
    NationalityForApplication: '71',
    TraveldocumentDateOfIssue: '1/15/2020',
    TraveldocumentValidUntil: '1/15/2030',
    TraveldocumentIssuingAuthority: '71',
    // Notification
    notifyPhone: '+201001234567', // for Telegram notification
  },
  {
    id: 2,
    Lastname: 'HASSAN',
    // ... etc
  }
];
```

---

### 10.4 Complete Node Flow

```
[INPUT from Workflow 1: Extract Booking Form Data]
  │  (sessionCookie, captchaImageUrl, BDC fields, token, startTime, foundSlots)
  │
  ▼
1. Applicants DB (Code node)
   — Returns full APPLICANTS array
   — Reads/writes state via $getWorkflowStaticData()
  │
  ▼
2. Pick Slot + Student (Code node)
   — Loops foundSlots array
   — For each slot: find first PENDING student
   — Assigns slot → student pairs
   — Marks each student as IN_PROGRESS
   — Outputs one item per slot-student pair
  │
  ▼
  [If no pending students → Telegram "All students booked!" → Stop]
  │
  ▼
3. POST Select Slot (HTTP Request)
   — Re-selects the specific slot using the live session
   — Gets fresh booking form + fresh CAPTCHA
  │
  ▼
4. Extract Fresh CAPTCHA (Code node)
   — Parses new BDC_VCID, BDC_Hs, BDC_SP, Token from response
   — Builds captchaImageUrl
  │
  ▼
5. GET CAPTCHA Image (HTTP Request)
   — GET captchaImageUrl with sessionCookie
   — Response format: file (binary)
   — Output property: captcha.png
  │
  ▼
6. Solve with Gemini (Google Gemini node)
   — Model: gemini-1.5-flash
   — Input: binary captcha.png
   — Prompt: "Only return the CAPTCHA code in uppercase, nothing else"
   — Returns: CaptchaText (e.g. "TA8WJ")
  │
  ▼
7. Extract CaptchaText (Code node)
   — Gets text from Gemini response
   — Trims whitespace, converts to uppercase
   — Combines all booking form fields + applicant data
  │
  ▼
8. POST /HomeWeb/Appointment (HTTP Request)
   — All applicant fields
   — All CAPTCHA hidden fields
   — CaptchaText = solved answer
   — DSGVOAccepted = true
   — Command = Next
  │
  ▼
9. Check Booking Response (Code node)
   — Parse response HTML
   — SUCCESS: contains confirmation text / no error div
   — CAPTCHA_FAIL: contains "captcha" in error message
   — SLOT_TAKEN: contains "no appointments available"
   — OTHER_FAIL: any other validation error
  │
  ├── SUCCESS →
  │     10a. Mark student COMPLETED (update static data)
  │     10b. Telegram: "✅ Booked! [Name] — [slot datetime]"
  │
  ├── CAPTCHA_FAIL (up to 3 retries) →
  │     10c. Re-fetch fresh CAPTCHA (same session)
  │     10d. Loop back to step 5
  │     After 3 failures → mark student PENDING, try next slot
  │
  ├── SLOT_TAKEN →
  │     10e. Mark student back to PENDING
  │     10f. Telegram: "⚠️ Slot taken for [Name], will retry"
  │     10g. Continue loop to next slot
  │
  └── OTHER_FAIL →
        10h. Mark student PENDING
        10i. Telegram: "❌ Error for [Name]: [error text]"
        10j. Log full HTML for debugging
```

---

### 10.5 Watcher Coordination

**Important:** Workflow 1 (watcher) runs every 20 seconds regardless. This is fine because:

- If no slots → it logs "no slots" and stops (cheap, fast)
- If slots found → it triggers the booking sequence immediately
- The booking sequence takes ~15-20 seconds total
- If the watcher fires again mid-booking, it will find the same slots (they haven't been taken yet by us) and trigger another booking sequence — but the student will already be `IN_PROGRESS` so the Pick Slot + Student node will skip them

**One concern:** If Workflow 1 is still running its booking sequence and fires again, it could try to book the same slot twice. To prevent this, the **"Pick Slot + Student"** node checks `IN_PROGRESS` students and skips them.

---

### 10.6 Static Data Structure

```javascript
// $getWorkflowStaticData('global') stores:
{
  "studentStates": {
    "1": "PENDING",
    "2": "IN_PROGRESS", 
    "3": "COMPLETED",
    "4": "PENDING"
  },
  "bookingResults": {
    "3": {
      "slot": "5/5/2026 12:00:00 PM",
      "bookedAt": "2026-04-26T11:59:00Z"
    }
  }
}
```

---

### 10.7 Remaining Steps to Build

- [ ] Collect real applicant data for all students
- [ ] Build Workflow 2 JSON and import into n8n
- [ ] Test end-to-end with Copenhagen (safe test)
- [ ] Switch to Kairo config and go live
