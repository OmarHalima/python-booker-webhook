# Research: n8n Workflow Analysis and Fix

## Date: 2026-07-11

---

## Research Task 1: Can the Slots blacklist table be removed safely?

### Decision: YES — Remove the blacklist table entirely.

### Rationale

**BMEIA behavior**: Booked slots are removed from the BMEIA appointment calendar instantly. When the watcher polls the scheduler API in the next 5-second cycle, previously booked slots no longer appear in the radio-button `<input>` list. This was confirmed by the user: "BMEIA is removing booked slots instantly."

**Dedup is therefore unnecessary**: The sole purpose of the blacklist was to prevent reprocessing slots the watcher already found. If BMEIA removes booked slots from its own calendar, the watcher will not find them again. The next cycle only returns unbooked slots.

**Retry is broken anyway**: The FALSE path (reprocessing old PENDING slots from a previous session) fails because:
- `sessionCookie` and `token` are session-bound (ASP.NET_SessionId)
- A slot detected in session A cannot be booked using session B's cookie
- BMEIA's scheduler ties slot selection to the session that detected it
- The `Rebuild_Payload` node spreads the current webhook's `sessionCookie`/`token`/`Office`/`CalendarId` over blacklist rows from previous sessions, causing wrong-session errors

**Lock prevents concurrent reprocessing**: The `lock_status` table ensures only one booker execution runs at a time. Skipped webhook invocations (while locked) are simply dropped — n8n does not queue them. So there's no risk of the same slot being processed twice in parallel.

### Alternatives Considered

1. **Keep blacklist as audit log only (insert, never read for dedup)** — Rejected by user. Adds complexity without value. The applicants table + Telegram messages + n8n execution history already serve as audit trail.

2. **Keep blacklist with fix (override office/calendarId from row, not webhook)** — Rejected. Even with the office/calendarId fix, the sessionCookie/token mismatch remains. The FALSE path still fails.

3. **Keep blacklist, remove FALSE path only** — Rejected. If we're removing the read path, there's no reason to keep the write path. BMEIA is the dedup.

---

## Research Task 2: What result types does the Python booker return?

### Decision: 6 standardized types + HTTP errors

### Findings

From `AGENT_HANDOFF.md` and `app.py`:

| Result Type | Meaning | Current Handling |
|-------------|---------|------------------|
| `SUCCESS` | Booking confirmed by BMEIA | ✅ Handled (Mark Student COMPLETED) |
| `CAPTCHA_FAIL` | All captcha attempts exhausted | ✅ Handled (Reset Student PENDING) |
| `SLOT_TAKEN` | BMEIA says slot no longer available | ✅ Handled (Reset Student PENDING) |
| `ALREADY_BOOKED` | Student already has an appointment | ✅ Handled (Mark Student ALREADY_BOOKED) |
| `OTHER_FAIL` | Unspecified failure | ❌ NOT HANDLED (empty branch) |
| `BOOKING_SERVER_BUSY` | BMEIA server busy/overloaded | ❌ NOT HANDLED (empty branch) |

The `Result Switch` node has 6 outputs:
- Output 0: SUCCESS → connected
- Output 1: CAPTCHA_FAIL → connected
- Output 2: SLOT_TAKEN → connected
- Output 3: ALREADY_BOOKED → connected
- Output 4: OTHER_FAIL → **empty `[]`**
- Output 5: extra (fallback) → **empty `[]`**

### Fix
Add `BOOKING_SERVER_BUSY` as output 5 (or merge with OTHER_FAIL). Add `Reset Student PENDING (other)` + `Telegram: Other Fail` for the OTHER_FAIL/BOOKING_SERVER_BUSY/HTTP_ERROR cases.

The `Book the Appointment` HTTP node's error output (`onError: continueErrorOutput`) currently goes to `Update Slot → PENDING` only — no student reset, no lock release, no notification. This must route to the new `Reset Student PENDING (other)` + `Telegram: HTTP Error` path.

---

## Research Task 3: What is the correct webhook URL for the Python booker?

### Decision: Use the active ngrok tunnel URL

### Findings

- **Watcher → n8n booker**: `https://n8n.acmeai.crabdance.com/webhook/booker` (n8n's own webhook URL — correct)
- **n8n booker → Python booker**: `https://porthole-portfolio-napped.ngrok-free.dev/webhook/booker` (current, may be stale)

The Python booker runs on the VPS at `127.0.0.1:8000`, exposed via ngrok. The ngrok URL changes when the tunnel restarts. The current URL `porthole-portfolio-napped.ngrok-free.dev` is hardcoded in the HTTP Request node.

### Recommendation
The workflow JSON should use the current active ngrok URL. Since n8n doesn't support environment variables in HTTP node URLs natively (without expressions), the URL must be updated in the JSON when the tunnel changes. For now, keep the existing URL — the user can update it after import.

---

## Research Task 4: Lock release timing — per-booking or per-execution?

### Decision: Per-execution (acquire once at start, release once at end)

### Findings

The current workflow releases the lock after **every single booking** via the `Replace Me → unlock` path. This means:
1. Slot 12:30 booked → lock released
2. Slot 12:45 processing starts → **lock not held**
3. A second webhook arrives → lock is unlocked → second execution starts → **race condition**

The lock should prevent concurrent *executions*, not concurrent *bookings* (which are already serial via `splitInBatches`). The lock should be:
- Acquired: once, after passing the `Filter` (not locked) check
- Released: once, after the `splitInBatches` loop is done (done output → unlock → Telegram: All Done)

This is already the correct design for the `Telegram: All Done` path (loop done → unlock). The problem is the 5 per-result paths that also unlock via `Replace Me`. These should **not** unlock — they should loop back to `One Slot at a Time` for the next slot.

### Implementation
- Remove all `Replace Me → unlock` connections
- Connect each Telegram result node back to `One Slot at a Time` (loop continues)
- Only the `splitInBatches` done output unlocks

---

## Research Task 5: What nodes need schema cleanup?

### Decision: Remove `removed: true` schema entries and slim down dataTable update nodes

### Findings

Every `dataTable` update node carries a full `schema` array listing all columns in the table, with `removed: true/false` flags. For the `applicants` table, this is ~24 columns — repeated in every update node. The `removed: true` entries are columns not being updated in that node. This bloats the JSON significantly (4047 lines, ~60% is schema boilerplate).

### Recommendation
Keep schema entries only for columns being updated (`removed: false`). This is a cosmetic optimization — n8n ignores `removed: true` entries. Can be done as a cleanup pass after the core logic is fixed.

---

## Research Task 6: Does the retry path (`retryCount > 0`) serve any purpose?

### Decision: Remove entirely

### Findings

`Filter Fresh Slots` (line 1396) has a retry path:
```js
if (retryCount > 0) {
  return [{ json: { ...payload, freshSlots: [payload.currentSlot], isRetry: true, retryCount } }];
}
```

This was designed for n8n's `retryOnFail` on the HTTP node. However:
1. The HTTP node has `retryOnFail: false` (line 3234) — so n8n never retries
2. The `retryCount` field is set by the watcher? No — the watcher doesn't send `retryCount`
3. The `isRetry` flag is set but never consumed downstream
4. The retry path bypasses blacklist filtering — but we're removing the blacklist anyway

With the blacklist removed and the HTTP node not retrying, this code is dead. Remove it entirely. The entire node goes away.
