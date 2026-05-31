# Turbo fetch injector (Tampermonkey) — experimental

Playwright via `POST /webhook/booker-legacy` remains available for the full wizard (~26s in live tests).

Turbo (`POST /webhook/booker`) is now the **default** production path (~4–11s).

This userscript is a **turbo option** for speed experiments (target 3–5s after you are already on the capture page with captcha solved).

## Install

1. Install [Tampermonkey](https://www.tampermonkey.net/) in Chrome/Edge.
2. Create new script → paste contents of [`tampermonkey-turbo-injector.user.js`](./tampermonkey-turbo-injector.user.js).
3. Save and enable.

## Manual test (F12 console)

1. Navigate manually (or via n8n scheduler) until you reach **Capture data** (`#Token`, `#CaptchaText`, `#Captcha_CaptchaImage`).
2. Open DevTools → Console.
3. Run:

```javascript
// Read live tokens (same as app.py extract_fresh_from_dom)
bmeiaTurbo.readFreshFormState()

// Fill applicant (example)
bmeiaTurbo.fillApplicant({
  Lastname: 'HASSAN',
  Firstname: 'SARA',
  DateOfBirth: '3/15/1995',
  TraveldocumentNumber: 'B8888888',
  Sex: '2',
  Street: '12 Nile St',
  Postcode: '11511',
  City: 'Cairo',
  Country: '49',
  Telephone: '201122334455',
  Email: 'sara@example.com',
})

// After OCR/Gemini gives captcha text (recommended — form submit, not fetch):
bmeiaTurbo.submitTurboForm('BEYJ')
// After page loads:
bmeiaTurbo.classifyPageResult()  // → 'SUCCESS'

// fetch-only (often CAPTCHA_FAIL in live tests):
// await bmeiaTurbo.submitTurbo('YC6A4')
```

4. Watch **Network** tab for POST to `/HomeWeb/AppointmentPassport`.
5. Check `resultType` in the returned object (`SUCCESS`, `SLOT_TAKEN`, `CAPTCHA_FAIL`, etc.).

## Copy-as-fetch workflow

After a successful Playwright booking:

1. DevTools → Network → find POST to `AppointmentPassport`.
2. Right-click → **Copy → Copy as fetch**.
3. Compare payload fields with `bmeiaTurbo.buildFormBody()` output.
4. Re-run from console on a **fresh** capture page (new VCID/captcha).

## Limits

| Item | Turbo fetch | Playwright booker |
|------|-------------|-------------------|
| Navigation (landing → slot → capture) | Manual / n8n | Automated |
| Fresh VCID + captcha image sync | DOM read at submit time | DOM screenshot + OCR |
| Session cookie | Browser tab only | Injected via payload |
| Multi-slot (3 students) | One tab, serial submits | Serial webhook calls |
| Anti-bot (BotDetect) | `submitTurboForm` + `simulateHumanCaptchaInput` | `press_sequentially` + Next click |

## Live test results (2026-05-30, session `twbqdyaogpdukegbef0cpdqd`)

| Method | Slot | Time | Result |
|--------|------|------|--------|
| `fetch` (`submitTurbo`) | 6/8 11:30 AM | ~4.3s | **CAPTCHA_FAIL** |
| `app_turbo.py` (HTTP) | various | ~3.5s | **CAPTCHA_FAIL** |
| Form submit + human captcha events | 6/9 10:30 AM | **~4.1s** | **SUCCESS** ✓ |
| Playwright `/webhook/booker-legacy` | 6/10 3:00 PM | ~29s | **SUCCESS** ✓ |
| Turbo `/webhook/booker` | 6/11 10:30 AM | ~10.6s | **SUCCESS** ✓ |

**Conclusion:** BotDetect accepts in-browser **form submit** with human keystroke events, but rejects **fetch POST** even from the same tab. Use `submitTurboForm()`, not `submitTurbo()`.

Automated repro: `scripts/run_turbo_inject_test.py` (`--method fetch` vs `--method form_submit`).

## Recommendation

- **Production:** n8n serial calls → `/webhook/booker` (turbo), unique student per slot, fresh `sessionCookie` when parallel.
- **Legacy rollback:** `/webhook/booker-legacy` for full Playwright wizard.
- **R&D:** Use turbo script only when you are already on the capture page and want to measure fetch-only latency.
