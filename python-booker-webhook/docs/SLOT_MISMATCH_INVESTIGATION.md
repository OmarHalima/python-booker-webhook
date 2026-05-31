# Slot time mismatch — investigation report

## Summary

Live tests booked the **wrong time** not because of mysterious "sticky session POST" on submit, but because:

1. **Full wizard re-navigation** with a **reused `sessionCookie`** showed a **different week** and **different slot list** than n8n detected.
2. When `currentSlot` was **not among** the visible `input[name=Start]` radios, the booker **silently picked the first radio** (`first_start_radio_playwright`).

Evidence from `debug-ce612b.log`:

### live-slot-test-001

| Field | Value |
|-------|--------|
| Requested | `6/2/2026 11:30:00 AM` |
| Radios on page | `["6/3/2026 2:30:00 PM"]` only |
| Strategy | `slot_no_exact_match_no_normalized` → **first_start_radio** |
| Confirmed | `6/3/2026 2:30 PM` |

### live-slot-test-003

| Field | Value |
|-------|--------|
| Requested | `6/3/2026 2:30:00 PM` |
| Payload Monday | `6/1/2026 12:00:00 AM` |
| DOM Monday | `6/8/2026 12:00:00 AM` ← **wrong week** |
| Radios on page | week of 6/8 (11 slots), none on 6/3 |
| Strategy | **first_start_radio** → `6/8/2026 11:00 AM` |

## Root cause

```
n8n slot detect (fresh scheduler POST, week 6/1, slot 6/2)
        ↓
same sessionCookie reused
        ↓
Playwright walks landing → … → choose appointment again
        ↓
server shows different week / slots (session advanced or stale)
        ↓
requested slot not in DOM → old code picked first radio
        ↓
StartTime on capture form ≠ currentSlot in payload
```

`extract_fresh_from_dom` correctly read `#StartTime` from the page — the page had the **wrong** slot selected.

## Fixes (app.py)

### 1. No silent slot fallback (default)

`BOOKING_ALLOW_SLOT_FALLBACK=false` (default): if `currentSlot` is set but not in the DOM, **fail fast** with available slots listed.

### 2. Scheduler POST fast path (recommended for n8n)

Set in webhook payload:

```json
{
  "useSchedulerPost": true,
  "bookingFormReady": true,
  "currentMonday": "6/1/2026 12:00:00 AM",
  "currentSlot": "6/2/2026 11:30:00 AM",
  "sessionCookie": "..."
}
```

This calls `POST /HomeWeb/Scheduler` with exact `Monday` + `Start` (same as n8n), loads capture HTML into Playwright — **no full wizard**.

Also enabled when `navigationMode: "scheduler_post"` or env `BOOKING_DEFAULT_SCHEDULER_POST=true`.

### 3. StartTime verification

After navigation, logs `start_time_dom_vs_payload` and **fails** if normalized `StartTime` ≠ `currentSlot` (unless fallback allowed).

## Turbo injector vs Playwright

| Scenario | Playwright full wizard | Scheduler POST + Playwright | Turbo (Tampermonkey on capture page) |
|----------|------------------------|----------------------------|--------------------------------------|
| Wrong week with reused cookie | High risk | Low — POST uses n8n Monday/Start | N/A — you must be on correct capture page |
| Speed | ~26s | ~15–20s (no wizard) | ~3–5s (submit only) |
| Best for production | Legacy / manual | **n8n slot detect pipeline** | R&D when already on capture form |

Turbo does **not** fix mismatch by itself — it submits whatever `#StartTime` is on the current page. It wins when n8n/scheduler already opened the **correct** capture form.

## n8n recommendation

1. Pass **`useSchedulerPost: true`** on every booker call after slot detect.
2. **Fresh `sessionCookie`** per parallel student (or serial calls).
3. Do **not** reuse cookie across bookings without a new scheduler POST for that slot/week.
4. On failure `"Requested currentSlot … is not available"`, mark slot `SLOT_TAKEN` / stale and rescan.

## Manual turbo test (F12)

On the **correct** capture page (from n8n scheduler, not after full wizard re-walk):

```javascript
bmeiaTurbo.readFreshFormState()  // check startTime matches your slot
await bmeiaTurbo.submitTurbo('CAPTCHA_TEXT')
```

Compare `fresh.startTime` to your `currentSlot` before submitting.
