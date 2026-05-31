---
name: Selenium Node Replacement
overview: Replace the n8n node chain from slot selection through booking submit with an external VPS-hosted Python Selenium script, while preserving the same output contract so downstream n8n nodes continue to work.
todos:
  - id: define-vps-api-contract
    content: Define JSON input/output contract between n8n and VPS Selenium worker
    status: pending
  - id: implement-selenium-worker
    content: Implement Python Selenium script/service for slot selection, CAPTCHA extraction, Gemini solve, and booking submit
    status: pending
  - id: replace-n8n-chain
    content: Replace six booking-related nodes with one VPS HTTP call node in workflow
    status: pending
  - id: compatibility-check-response
    content: Ensure downstream Check Booking Response works unchanged or patch minimally
    status: pending
  - id: validate-end-to-end
    content: Test success/failure branches and confirm retry logic still behaves correctly
    status: pending
isProject: false
---

# Replace Booking Nodes With VPS Selenium Script

## Scope
Migrate this node chain in `[c:\Users\WD\Downloads\files\Austria Booker — Workflow 2.json](c:\Users\WD\Downloads\files\Austria Booker — Workflow 2.json)`:
- `POST Select Slot`
- `Extract Fresh CAPTCHA`
- `GET CAPTCHA Image`
- `Solve CAPTCHA with Gemini`
- `Build Booking Body`
- `POST Booking Form`

And keep compatibility with existing downstream flow:
- `Check Booking Response` (expects HTTP-like response body text plus prior context fields)

## Implementation Approach
1. Build a VPS Python Selenium worker that performs the full booking sequence currently spread across those nodes:
   - Open scheduler with session cookie context
   - Select the slot (`currentMonday`, `currentSlot`)
   - Extract fresh CAPTCHA hidden fields and token
   - Capture CAPTCHA image
   - Solve CAPTCHA via Gemini API (same strict prompt behavior)
   - Fill applicant form and submit booking
   - Return raw HTML response + extracted metadata
2. Expose the worker as an HTTP endpoint (e.g., `/run-booking-attempt`) that accepts the merged student/slot payload currently produced by `Merge Student + Slot`.
3. In n8n, replace the 6-node chain with one HTTP Request node (or webhook call) to the VPS service.
4. Normalize VPS response shape so n8n receives fields equivalent to what `POST Booking Form` + `Build Booking Body` were providing (especially `body`, `bookingBody`, `token`, CAPTCHA fields), allowing `Check Booking Response` to run without major logic changes.
5. Add retry/timeout/error mapping so Selenium failures become deterministic workflow outcomes (e.g., transient browser failure vs booking validation failure).

## Data Contract (Compatibility)
The VPS service response should include at minimum:
- `body`: final booking response HTML (string)
- `statusCode`: synthetic/real status code
- `bookingBody`: submitted form payload
- `token`, `BDC_VCID_Captcha`, `BDC_Hs_Captcha`, `BDC_SP_Captcha`, `BDC_BackWorkaround_Captcha`
- `captchaText`
- `sessionCookie`
- original merged context fields used by downstream nodes

This preserves `Check Booking Response` parsing behavior and avoids large downstream refactors.

## Execution Flow
```mermaid
flowchart LR
mergeStudentSlot[MergeStudentPlusSlot] --> updateStates[UpdateInProgressNodes]
updateStates --> n8nCall[VpsBookingHttpCall]
n8nCall --> seleniumWorker[SeleniumPythonWorkerOnVPS]
seleniumWorker --> geminiSolve[GeminiCaptchaSolve]
geminiSolve --> submitForm[SubmitBookingForm]
submitForm --> normalizedResponse[NormalizedResponseJSON]
normalizedResponse --> checkResponse[CheckBookingResponse]
```

## Validation Plan
- Run one known test applicant/slot through new VPS call and verify:
  - n8n receives `body` HTML and required context fields.
  - `Check Booking Response` still classifies `SUCCESS`, `CAPTCHA_FAIL`, `SLOT_TAKEN`, `OTHER_FAIL` correctly.
- Force one CAPTCHA failure and confirm retry branch (`Prepare CAPTCHA Retry`) still works.
- Compare old-vs-new payload field names to ensure no contract drift.
- Add structured logs on VPS (request ID, slot, outcome, timings) for troubleshooting.

## Deliverables
- Python Selenium booking script/service on VPS.
- n8n workflow update replacing the 6 nodes with a single VPS call node.
- Optional small adjustment in `Check Booking Response` only if response wrapper differs slightly.
- Runbook notes: env vars (Gemini key, browser path), startup command, timeout settings.