# n8n: Booking result handling (booker webhook)

The Python booker (`POST /webhook/booker`) **classifies BMEIA errors in the browser** and returns JSON. n8n should **trust `resultType`** from that response — do **not** re-parse HTML with the old `BDC_VCID_Captcha` heuristic (that caused real slot / already-booked errors to look like `CAPTCHA_FAIL`).

## Webhook response shape

```json
{
  "ok": true,
  "resultType": "SUCCESS",
  "attempt": 1,
  "captchaText": "ABC12",
  "finalUrl": "https://appointment.bmeia.gv.at/...",
  "timestamp": "2026-05-15T12:00:00.000000Z",
  "artifacts": { "runId": "...", "screenshots": [], ... }
}
```

Failure examples:

| `resultType` | Meaning | Typical `message` |
|--------------|---------|-------------------|
| `SUCCESS` | Left capture page (confirmation) | — |
| `SLOT_TAKEN` | Slot race or no slots | *allocated otherwise in the meantime* / *no appointments available* |
| `ALREADY_BOOKED` | Applicant already has an appointment | *You have already reserved an appointment.* |
| `CAPTCHA_FAIL` | 3 wrong captchas inside booker (`maxCaptchaRetries`) | *Captcha retries exhausted.* |
| `OTHER_FAIL` | Unknown error on capture page | `snippet` in response |

**Retries:** `maxCaptchaRetries` in the POST body is handled **inside** the booker (default 3). n8n does **not** need a second captcha-retry loop unless you want to re-call the whole webhook with a new session.

---

## 1. Replace **Check Booking Response** (Code node)

Place this **after** **HTTP Request** → booker. It merges the booker JSON with your merge node data.

```javascript
// After HTTP Request to POST /webhook/booker
const booker = $input.first().json;
const merge = $('Merge Student + Slot').first().json;

// n8n may nest body on some versions — prefer top-level booker fields
const resultType = String(booker.resultType || booker.body?.resultType || 'OTHER_FAIL');
const message = String(booker.message || booker.body?.message || booker.detail || '');
const attempt = booker.attempt ?? booker.body?.attempt ?? null;

// Only real captcha exhaustion should trigger n8n "retry" path (optional)
const shouldRetry = resultType === 'CAPTCHA_FAIL' && booker.ok === false;

return [{
  json: {
    ...merge,
    ok: booker.ok === true,
    resultType,
    errorText: message || resultType,
    shouldRetry,
    bookerAttempt: attempt,
    captchaText: booker.captchaText || '',
    finalUrl: booker.finalUrl || '',
    timestamp: booker.timestamp || new Date().toISOString(),
    webhookResponse: {
      ok: booker.ok,
      resultType,
      attempt,
      message,
      artifacts: booker.artifacts || null,
    },
  },
}];
```

Remove the old logic that set `CAPTCHA_FAIL` whenever `html.includes('BDC_VCID_Captcha')`.

---

## 2. Update **Result Switch**

Add a rule **before** `OTHER_FAIL` / fallback:

| Output name | Condition |
|-------------|-----------|
| SUCCESS | `{{ $json.resultType }}` equals `SUCCESS` |
| CAPTCHA_FAIL | equals `CAPTCHA_FAIL` |
| SLOT_TAKEN | equals `SLOT_TAKEN` |
| **ALREADY_BOOKED** | equals `ALREADY_BOOKED` |
| OTHER_FAIL | equals `OTHER_FAIL` |

Route outputs:

| Output | Action (recommended) |
|--------|----------------------|
| **SUCCESS** | Existing: Mark Student COMPLETED → Mark Slot BOOKED → Telegram Booked |
| **SLOT_TAKEN** | Existing: Reset Student PENDING → Mark Slot SLOT_TAKEN → Telegram Slot Taken |
| **ALREADY_BOOKED** | **New branch** (see §3) — **not** CAPTCHA_FAIL |
| **CAPTCHA_FAIL** | Reset Student PENDING → Mark Slot FAILED / CAPTCHA_FAIL → Telegram — only when OCR truly failed 3× |
| **OTHER_FAIL** | Telegram + mark slot FAILED; student PENDING or manual review |

**Disconnect** `Prepare CAPTCHA Retry` / `Retries Exhausted?` unless you intentionally want a **second** full booker call after `CAPTCHA_FAIL` (usually redundant with `maxCaptchaRetries: 3` in the payload).

---

## 3. New branch: **ALREADY_BOOKED**

This is a **student** problem, not a slot race. Do **not** blacklist the slot as `SLOT_TAKEN`.

Suggested nodes (mirror your SLOT_TAKEN layout):

1. **Update applicants** — `status`: `ALREADY_BOOKED` (not `PENDING`)
2. **Update Slots blacklist** — `status`: `PENDING`, `result`: `ALREADY_BOOKED` (release slot for another student) *or* leave slot unchanged per your policy
3. **Telegram** — example text:

```
⚠️ Student already has an appointment

{{ $json.student.Firstname }} {{ $json.student.Lastname }}
Passport: {{ $json.student.TraveldocumentNumber }}
BMEIA: {{ $json.errorText }}
Slot was NOT consumed: {{ $json.currentSlot }}
```

---

## 4. **HTTP Request** body (unchanged fields + note)

Keep `maxCaptchaRetries: 3` in the JSON body. Optional `runId` in the payload (e.g. `curl-test-001`); if omitted, the booker assigns **`run-000001`**, **`run-000002`**, … under `artifacts/` (`RUN_ID_MODE=sequential`, default).

Optional tuning on the VPS:

```json
{
  "maxCaptchaRetries": 3,
  "runId": "{{ $json.runId || $execution.id }}",
  "captureArtifacts": true
}
```

**HTTP Request node:** set **Timeout** to **300000** ms (5 minutes) or higher so navigation (up to ~60s choose-appointment wait) plus captcha OCR/Gemini and retries fit in one call.

Deploy **latest `app.py`** on the VPS and restart uvicorn so `ALREADY_BOOKED` / `SLOT_TAKEN` are returned on attempt 1.

---

## 5. Before / after (why Telegram showed CAPTCHA)

| Scenario | Old n8n + old booker | Now |
|----------|----------------------|-----|
| Slot allocated otherwise | `CAPTCHA_FAIL` × 3 | `SLOT_TAKEN` attempt 1 → Slot Taken Telegram |
| Already reserved | `CAPTCHA_FAIL` × 3 | `ALREADY_BOOKED` attempt 1 → new Telegram branch |
| Wrong captcha | `CAPTCHA_FAIL` | `CAPTCHA_FAIL` (still valid) |

---

## 6. Switch wiring checklist

```
HTTP Request (booker)
  → Check Booking Response (new code)
  → Result Switch
       SUCCESS        → Mark Student COMPLETED → …
       CAPTCHA_FAIL   → Reset PENDING (captcha) → Mark CAPTCHA_FAIL → Telegram
       SLOT_TAKEN     → Reset PENDING (taken) → Mark SLOT_TAKEN → Telegram
       ALREADY_BOOKED → [new nodes §3]
       OTHER_FAIL     → [your failure handling]
```

HTTP Request **error** output → keep **Update Slot → PENDING** / **Update Student → PENDING** for transport/500 errors.

---

## Related

- Scheduler HTML / cookies: [n8n-scheduler-workflow.md](./n8n-scheduler-workflow.md)
- Python classifier: `classify_post_submit_html()` in `app.py`
