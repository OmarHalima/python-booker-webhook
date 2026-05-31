# Testing with KOPENHAGEN (stand-in for Kairo)

All development, turbo benchmarks, and n8n integration tests in this repo use the **Copenhagen** mission as a **safe test double** for **Cairo (Kairo)** production. The booking pipeline, captcha flow, scheduler POST, and serial 3-slot tests are identical — only mission-specific fields change when you go live.

## Test vs production

| Field | Test (this repo) | Production (Cairo / Kairo) |
|-------|------------------|----------------------------|
| `Office` | **`KOPENHAGEN`** | **`KAIRO`** |
| `CalendarId` | **`9677702`** | Mission queue ID (see below) |
| `Language` | `en` | `en` or `de` as required |
| `student.*` | Egyptian-style dummy data (Cairo, passport, etc.) | Real applicant data |
| Slot scan | `scripts/scan_slots.py --office KOPENHAGEN` | Same script with `--office KAIRO` |

**Rule:** The scheduler scan and the booker webhook must use the **same** `Office`, `CalendarId`, and `sessionCookie` chain. Do not scan KOPENHAGEN and book KAIRO (or vice versa) in one flow.

## Why KOPENHAGEN for Kairo testing

- Same BMEIA appointment system (`appointment.bmeia.gv.at`) — same forms, captcha, scheduler POST, and turbo path.
- Copenhagen queue **`9677702`** has had **live test slots** during development (used for turbo 3-slot benchmarks, ~4–11s bookings).
- Avoids consuming real Cairo appointment capacity while validating n8n, OCR, slot mismatch fixes, and turbo v2.
- Student payloads use **Egypt-relevant fields** (names, Cairo/Giza, nationality codes) so form-fill logic matches production applicants even though `Office` is Copenhagen.

## Cairo (KAIRO) calendar IDs (production reference)

From mission inspection (`artifacts/inspect-kairo/inspect_result.json`, 2026-05-30):

| CalendarId | Queue (German label) |
|------------|----------------------|
| `44281520` | Aufenthaltsbewilligung Student (nur Bachelor) |
| `44279679` | Aufenthaltsbewilligung Student (nur Master, PhD und Stipendiate) |
| `26425165` | Rot-Weiß-Rot Karte |
| `32528820` | Familienzusammenführung (ÄgypterInnen) |
| `32529659` | Familienzusammenführung (sonstige Staatsangehörige) |
| `20950851` | Österreicher Personenstandsangelegenheiten |
| `134608` | Österreicher (Reisepässe, Staatsbürgerschaft) |
| `2011918` | Visum D zur Arbeitsuche |

Pick the queue that matches the student’s case. Use `calendarReservationMatch` or `injectMissingCalendarId` if the dropdown label differs from the ID (see main [README](../README.md)).

Example production payload skeleton: [`examples/payload-kairo-production.json`](../examples/payload-kairo-production.json).

Historical Kairo sample: `docs/turbo-booker-archive/test-payloads/curl-test-payload-kairo.json`.

## Switching test → production in n8n

1. **Slot scanner** HTTP nodes: `"Office": "KAIRO"`, correct `CalendarId`, same wizard as today.
2. **Booker** HTTP node: still `POST /webhook/booker` on port **8000** (turbo v2).
3. Payload: `"useSchedulerPost": true`, `"maxCaptchaRetries": 5`, map `slotValue` → `currentSlot`.
4. Replace dummy passports with real students; one unique `TraveldocumentNumber` per slot.
5. Keep **serial** booking or fresh `sessionCookie` per parallel student.

No code change required — only payload constants.

## What we validated on KOPENHAGEN (2026-05-30 → 2026-05-31)

| Item | Result |
|------|--------|
| Turbo v2 (`/webhook/booker`) | **SUCCESS** ~6.4s best (attempt 1), ~6–12s typical |
| Legacy (`/webhook/booker-legacy`) | **SUCCESS** ~26s |
| Slot mismatch fix (`useSchedulerPost`) | Correct `StartTime` vs `currentSlot` |
| Warm browser reuse (`PLAYWRIGHT_REUSE_BROWSER=true`) | Working — saves ~2s launch overhead on retries |
| DOM captcha screenshot (`CAPTCHA_PARALLEL_FETCH=false`) | **Required** — URL fetch can return wrong image for session |
| Parallel URL fetch (`CAPTCHA_PARALLEL_FETCH=true`) | **CAPTCHA_FAIL** — solver reads different bytes than browser |
| Gemini-first solver | Default; **SUCCESS attempt 1** in 6.4s with DOM screenshot |
| OCR-first + Gemini fallback | Production default (`CAPTCHA_SOLVER=ocr_with_gemini_fallback`); **SUCCESS ~5.8s** |
| HTTP-only turbo (`submitMode: http`) | **CAPTCHA_FAIL** — use browser mode |
| OCR-only on hard BotDetect styles | **CAPTCHA_FAIL 5/5** — use Gemini-first |
| Style-gate assessment | Logs `easy`/`hard`; reload loop unreliable — use `CAPTCHA_MAX_STYLE_RELOADS=0` |
| `BDC_Hs_Captcha` race fix (attempt 1) | Fixed — `typeof ct.Captcha !== 'undefined'` ensures JS is wired |

Full captcha investigation findings: [CAPTCHA_INVESTIGATION.md](./CAPTCHA_INVESTIGATION.md).  
Full history: [turbo-booker-archive/README.md](turbo-booker-archive/README.md).

## Commands (test office)

```bash
# Scan Copenhagen slots
.venv\Scripts\python.exe scripts\scan_slots.py --office KOPENHAGEN --calendar-id 9677702

# Book with example payload (Office already KOPENHAGEN)
.venv\Scripts\python.exe scripts\run_turbo_payload.py examples\payload-booker.json
```

For Cairo, change `--office KAIRO` and `CalendarId` in the payload to match your queue.
