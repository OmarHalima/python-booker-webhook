# n8n: Scheduler HTML → booker payload + session cookie chain

The choose-appointment HTML from `POST https://appointment.bmeia.gv.at/HomeWeb/Scheduler` embeds the active week in hidden **`Monday`** and lists slots as **`input[name=Start][type=radio]`** with exact **`value`** strings. The booker webhook expects **`currentMonday`** and **`currentSlot`** to match those strings byte-for-byte (the Playwright path also tries a small normalization for seconds / spacing).

## 1. Parse HTML after Scheduler POST (Code node)

Use the **response body** string from your HTTP Request node as `html`.

**Vanilla JS (no Cheerio)** — works in default n8n Code nodes:

```javascript
// n8n Code node — input: items[0].json with { html: "<!DOCTYPE ...>" }
const html = String($input.first().json.html || '');
let monday = '';
const mMon = html.match(/<input[^>]*(?:id="Monday"|name="Monday")[^>]*value="([^"]*)"/i)
  || html.match(/<input[^>]*value="([^"]*)"[^>]*(?:id="Monday"|name="Monday")/i);
if (mMon) monday = mMon[1].trim();
const startValues = [];
const re = /<input[^>]*name=["']Start["'][^>]*value=["']([^"']*)["']/gi;
let m;
while ((m = re.exec(html)) !== null) startValues.push(m[1].trim());
if (startValues.length === 0) {
  const re2 = /<input[^>]*value=["']([^"']*)["'][^>]*name=["']Start["']/gi;
  while ((m = re2.exec(html)) !== null) startValues.push(m[1].trim());
}
return [{ json: { currentMonday: monday, currentSlot: startValues[0] || '', startValues, monday } }];
```

**Cheerio** (only if your environment provides `cheerio`):

```javascript
// n8n Code node — input: items[0].json with { html: "<!DOCTYPE ...>" }
const cheerio = require('cheerio');
const $ = cheerio.load($input.first().json.html || '');
const monday = ($('#Monday').attr('value') || $('input[name="Monday"]').attr('value') || '').trim();
const startValues = [];
$('input[type="radio"][name="Start"]').each((_, el) => {
  const v = ($(el).attr('value') || '').trim();
  if (v) startValues.push(v);
});
return [{ json: { currentMonday: monday, currentSlot: startValues[0] || '', startValues, monday } }];
```

Wire **`currentMonday`** and your chosen **`currentSlot`** (one of **`startValues`**) into the booker webhook JSON.

**Important — avoid slot time mismatch:** after slot detect, call the booker with **`useSchedulerPost: true`** (or `"bookingFormReady": true`). This POSTs `/HomeWeb/Scheduler` with the same `Monday`/`Start` as n8n instead of re-walking the full wizard with a reused cookie (which can show a different week). See [SLOT_MISMATCH_INVESTIGATION.md](./SLOT_MISMATCH_INVESTIGATION.md).

Example booker body fields:

```json
{
  "useSchedulerPost": true,
  "sessionCookie": "...",
  "currentMonday": "6/8/2026 12:00:00 AM",
  "currentSlot": "6/9/2026 11:00:00 AM",
  "Office": "KOPENHAGEN",
  "CalendarId": "9677702",
  "Language": "en"
}
```

**Test vs production:** This repo uses **`KOPENHAGEN` / `9677702`** for all integration tests (stand-in for Cairo). For live Cairo bookings use `"Office": "KAIRO"` and the correct mission `CalendarId` — same scheduler/booker flow. See [TESTING_KOPENHAGEN.md](./TESTING_KOPENHAGEN.md).

The repo also ships **`scheduler_html.parse_scheduler_choose_appointment_html(html)`** in Python for parity with tests and scripts.

## 2. Merge `Set-Cookie: ASP.NET_SessionId` into `sessionCookie`

After each response that may rotate the session, update the cookie string you pass to the next HTTP step or to **`POST /webhook/booker`**.

```javascript
// n8n Code node — input: { sessionCookie: "...", setCookieHeader: items[0].json.headers['set-cookie'] }
// set-cookie may be a string or array of strings depending on node/version.
function mergeAspNetSessionId(sessionCookie, setCookieRaw) {
  const raw = Array.isArray(setCookieRaw) ? setCookieRaw.join('\n') : String(setCookieRaw || '');
  const m = raw.match(/ASP\.NET_SessionId\s*=\s*([^;\s]+)/i);
  if (!m) return sessionCookie;
  const sid = m[1].trim();
  const parts = String(sessionCookie || '')
    .split(';')
    .map((s) => s.trim())
    .filter(Boolean);
  const map = new Map();
  for (const p of parts) {
    const eq = p.indexOf('=');
    if (eq > 0) map.set(p.slice(0, eq).trim(), p.slice(eq + 1).trim());
  }
  map.set('ASP.NET_SessionId', sid);
  return Array.from(map.entries())
    .map(([k, v]) => `${k}=${v}`)
    .join('; ');
}

const j = $input.first().json;
return [{ json: { sessionCookie: mergeAspNetSessionId(j.sessionCookie, j.setCookieHeader) } }];
```

In Python the same merge is available as **`app.merge_session_cookie_from_set_cookie(session_cookie, set_cookie_raw)`**.

## 3. POST body alignment

- **`Monday`** in the Scheduler POST body should be the same week as the HTML you parsed (use the parsed **`monday`** for the next POST when changing week).
- **`Referer`**: the booker’s `post_select_slot` helper defaults Referer to **`https://appointment.bmeia.gv.at/?fromSpecificInfo=True`**; override with payload key **`schedulerReferer`** if your capture shows a different chain.

## 4. Booker / Playwright behavior (reference)

- On the choose-appointment step, a mismatch between **`payload.currentMonday`** and DOM **`#Monday`** is logged as **`monday_dom_vs_payload`** in **`debug-ce612b.log`**.
- If **`currentSlot`** does not exactly match a radio **`value`**, the server treats it as no selection (**“Please choose an appointment!”**). The booker tries a normalized match (e.g. `:00` seconds) before falling back to the first **`Start`** radio.

## 5. Booking results after `/webhook/booker`

See **[n8n-booking-result-flow.md](./n8n-booking-result-flow.md)** for `resultType` routing (`SUCCESS`, `SLOT_TAKEN`, `ALREADY_BOOKED`, `CAPTCHA_FAIL`, `OTHER_FAIL`) and the updated **Check Booking Response** Code node.
