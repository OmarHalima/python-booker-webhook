# Python Booker Webhook

Receives payloads from n8n via HTTP webhook, books Austrian embassy appointments via Playwright.

**Primary path (v2):** turbo — scheduler POST + minimal browser submit (~6–12s; best ~6.4s) on `POST /webhook/booker`.

**Captcha:** OCR first, Gemini fallback (`CAPTCHA_SOLVER=ocr_with_gemini_fallback`). DOM screenshot only — `CAPTCHA_PARALLEL_FETCH=false`. See [docs/CAPTCHA_INVESTIGATION.md](docs/CAPTCHA_INVESTIGATION.md).

**n8n workflow:** import [workflows/booker.json](workflows/booker.json) (HTTP timeout 300s, `runId`, serial slot booking).

**Legacy path:** full Playwright wizard (~26s) on `POST /webhook/booker-legacy`.

**Testing:** All dev benchmarks use **`Office: KOPENHAGEN`** as a stand-in for **Cairo (KAIRO)** production — see [docs/TESTING_KOPENHAGEN.md](docs/TESTING_KOPENHAGEN.md).

See [docs/turbo-booker-archive/README.md](docs/turbo-booker-archive/README.md) for the full project history.

## 1) Setup

```bash
cd python-booker-webhook
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Create environment variables:

```bash
copy .env.example .env
```

Set `CAPTCHA_OCR_BASE_URL` (your deployed `/predict` service, e.g. ngrok URL) and `GEMINI_API_KEY` for captcha fallback (and optional form-fill) in `.env`.

Test OCR before deploy:

```bash
python test_ocr_captcha_api.py
# optional: python test_ocr_captcha_api.py path\to\artifact_captcha.png
```

## 2) Run server

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Webhook endpoints:

| Endpoint | Flow |
|----------|------|
| `POST http://localhost:8000/webhook/booker` | **Turbo** (default, ~6–12s) |
| `POST http://localhost:8000/webhook/booker-legacy` | Full Playwright wizard (~26s) |
| `POST http://localhost:8000/webhook/booker-turbo` | Alias of `/webhook/booker` |

Optional turbo fields: `submitMode` (`browser` default), `maxCaptchaRetries` (use **5**), `useSchedulerPost: true`.

Key `.env` for turbo captcha: `CAPTCHA_PARALLEL_FETCH=false`, `CAPTCHA_SOLVER=ocr_with_gemini_fallback`, `CAPTCHA_OCR_BASE_URL`, `GEMINI_API_KEY`. Details: [docs/TURBO_APP.md](docs/TURBO_APP.md#captcha-configuration-recommended).

## 3) n8n payload shape

Send the same fields you already use in workflow 2:

- `sessionCookie`
- `Language`
- `Office`
- `CalendarId`
- optional `calendarReservationMatch` (or `reservationTypeMatch`): case-insensitive substring of the **visible** "Reservation for" option text — used if `CalendarId` is not in the live dropdown (e.g. mission removed an old ID or only lists "nur Master, PhD…" for students)
- optional `injectMissingCalendarId` or `injectCalendarId` (boolean): if true, the booker **forces** payload `CalendarId` on the reservation step (no waiting for that value to appear in the server list). It uses a **single DOM update** (append `<option>` if needed, set `value`, fire `input`/`change`, optional jQuery `trigger`) so client scripts cannot remove the option between inject and `select_option`. **The server may still reject** the next POST if the id is invalid.
- optional `injectCalendarIdLabel`: visible label for the injected option (default: `Requested queue (automation)`)
- `currentMonday`
- `currentSlot`
- `student` object (Lastname, Firstname, DateOfBirth, etc.)
- optional `maxCaptchaRetries` (default: `3`, max `25`); alias `maxCaptchaAttempts` also accepted
- optional `captureArtifacts` (default: true)
  - when true, saves screenshots + a `.webm` video under `python-booker-webhook/artifacts/<runId>/`
- optional `manualReviewSeconds` (default: `0` — no post-run wait). Set e.g. `20` to pause for manual inspection on failure (see `manualReviewOnFail`)
- optional `dsgvoAccepted` (default: check GDPR consent when `#DSGVOAccepted` exists); set `false` to skip

Example body (see also [`examples/payload-booker.json`](examples/payload-booker.json) — **KOPENHAGEN test office**; production Cairo: [`examples/payload-kairo-production.json`](examples/payload-kairo-production.json)):

```json
{
  "useSchedulerPost": true,
  "maxCaptchaRetries": 5,
  "sessionCookie": "AspxAutoDetectCookieSupport=1; ASP.NET_SessionId=...",
  "Language": "en",
  "Office": "KOPENHAGEN",
  "CalendarId": "9677702",
  "currentMonday": "5/11/2026 12:00:00 AM",
  "currentSlot": "5/11/2026 11:30:00 AM",
  "maxCaptchaRetries": 3,
  "captureArtifacts": true,
  "student": {
    "Lastname": "MAHMOUD",
    "Firstname": "SARA",
    "DateOfBirth": "11/30/2002",
    "TraveldocumentNumber": "A1234567",
    "Sex": "2",
    "Street": "3 Al Azhar St",
    "Postcode": "11511",
    "City": "Cairo",
    "Country": "49",
    "Telephone": "201112223344",
    "Email": "sara@email.com",
    "LastnameAtBirth": "MAHMOUD",
    "NationalityAtBirth": "49",
    "CountryOfBirth": "49",
    "PlaceOfBirth": "Cairo",
    "NationalityForApplication": "49",
    "TraveldocumentDateOfIssue": "3/15/2016",
    "TraveldocumentValidUntil": "3/15/2031",
    "TraveldocumentIssuingAuthority": "49"
  }
}
```

## 4) Captcha OCR environment

| Variable | Purpose |
|----------|---------|
| `CAPTCHA_OCR_BASE_URL` | Base URL of VPS OCR service (no trailing slash), e.g. `https://….ngrok-free.dev` |
| `CAPTCHA_OCR_TIMEOUT` | HTTP timeout seconds for `/predict` (default `30`) |
| `CAPTCHA_SOLVER` | `ocr_with_gemini_fallback` (default), `ocr`, or `gemini` |

The booker sends captcha PNG bytes to `POST {CAPTCHA_OCR_BASE_URL}/predict` as multipart `file`. For ngrok free tier, the client sends `ngrok-skip-browser-warning: 1`. Response JSON: `{"result": "..."}`.

`GEMINI_API_KEY` is still used when OCR fails and for optional `GEMINI_FILL_FORM`.

## 5) Notes

- **Linux VPS / Docker with no monitor:** run Chromium under **Xvfb** and set `DISPLAY` — see [docs/XVFB_PLAYWRIGHT_DEPLOYMENT.md](docs/XVFB_PLAYWRIGHT_DEPLOYMENT.md).
- Browser is intentionally non-headless (`headless=False`).
- Captcha text is normalized to uppercase before submit.
- On captcha failure, fresh hidden fields are extracted from the returned page and retried.
