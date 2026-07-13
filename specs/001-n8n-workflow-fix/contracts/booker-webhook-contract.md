# Contract: Booker Webhook (n8n → Python)

## Endpoint

```
POST {PYTHON_BOOKER_URL}/webhook/booker
Content-Type: application/json
Timeout: 300000ms (5 minutes)
```

## Request Body

```json
{
  "runId": "string (execution.id)",
  "captureArtifacts": true,
  "useSchedulerPost": true,
  "submitMode": "browser",
  "dsgvoAccepted": true,
  "maxCaptchaRetries": 5,
  "sessionCookie": "string (ASP.NET_SessionId from watcher)",
  "Language": "string (e.g., 'en')",
  "Office": "string (KAIRO | KOPENHAGEN)",
  "CalendarId": "string (numeric)",
  "PersonCount": "string (e.g., '1')",
  "currentMonday": "string (date string)",
  "currentSlot": "string (slot datetime value)",
  "student": {
    "Lastname": "string",
    "Firstname": "string",
    "DateOfBirth": "string",
    "TraveldocumentNumber": "string",
    "Sex": "string (numeric code as string)",
    "Street": "string",
    "Postcode": "string (numeric as string)",
    "City": "string",
    "Country": "string (numeric code as string)",
    "Telephone": "string (numeric as string)",
    "Email": "string",
    "LastnameAtBirth": "string",
    "NationalityAtBirth": "string (numeric as string)",
    "CountryOfBirth": "string (numeric as string)",
    "PlaceOfBirth": "string",
    "NationalityForApplication": "string (numeric as string)",
    "TraveldocumentDateOfIssue": "string",
    "TraveldocumentValidUntil": "string",
    "TraveldocumentIssuingAuthority": "string (numeric as string)"
  }
}
```

## Response Body (200 OK)

```json
{
  "ok": true|false,
  "resultType": "SUCCESS|CAPTCHA_FAIL|SLOT_TAKEN|ALREADY_BOOKED|OTHER_FAIL|BOOKING_SERVER_BUSY",
  "message": "string (human-readable detail)",
  "attempt": "number|null (captcha attempt count)",
  "captchaText": "string (last captcha text tried)",
  "finalUrl": "string (final BMEIA URL)",
  "timestamp": "string (ISO timestamp)",
  "artifacts": "object|null (artifact paths if captureArtifacts=true)"
}
```

## Result Types

| resultType | ok | Student Action | Telegram Message |
|------------|----|-----------------|-------------------|
| SUCCESS | true | status → COMPLETED | ✅ APPOINTMENT BOOKED! |
| CAPTCHA_FAIL | false | status → PENDING | ❌ CAPTCHA failed |
| SLOT_TAKEN | false | status → PENDING | ⚠️ Slot already taken |
| ALREADY_BOOKED | false | status → ALREADY_BOOKED | ⚠️ Student already has appointment |
| OTHER_FAIL | false | status → PENDING | ❌ Booking failed |
| BOOKING_SERVER_BUSY | false | status → PENDING | ⏳ Booking server busy |

## HTTP Error (non-200, timeout, connection error)

When the HTTP request fails (timeout, connection refused, DNS error):
- n8n routes to the error output (`onError: continueErrorOutput`)
- Student status → PENDING
- Telegram: HTTP error notification
- Loop continues to next slot

## n8n Expression Reference

The HTTP Request node builds the JSON body using:

```javascript
const m = $('Merge Student + Slot').item.json;
const s = m.student || {};
return {
  runId: String($execution.id),
  captureArtifacts: true,
  // ... (full body per above schema)
  sessionCookie: m.sessionCookie,
  Office: m.Office,
  CalendarId: String(m.CalendarId),
  currentSlot: m.currentSlot,
  student: {
    Lastname: s.Lastname,
    // ... all student fields
  }
};
```

Data source: `Merge Student + Slot` node output, which combines:
- `slotData` from `One Slot at a Time` (currentSlot, sessionCookie, Office, CalendarId, etc. — all from webhook payload)
- `student` from `Get Next PENDING Student` (all applicant fields from the applicants table)
