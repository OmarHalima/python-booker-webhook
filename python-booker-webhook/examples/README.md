# Example payloads

## Test (KOPENHAGEN stand-in for Kairo)

Use **`payload-booker.json`** for all local and n8n integration tests:

- `"Office": "KOPENHAGEN"`, `"CalendarId": "9677702"`
- Egyptian-style **student** data (Cairo applicant profile) on a Copenhagen test queue

See [docs/TESTING_KOPENHAGEN.md](../docs/TESTING_KOPENHAGEN.md) for why and how to switch to production.

```bash
.venv\Scripts\python.exe scripts\run_turbo_payload.py examples\payload-booker.json
```

Replace `sessionCookie`, `currentMonday`, and `currentSlot` from:

```bash
.venv\Scripts\python.exe scripts\scan_slots.py --office KOPENHAGEN --calendar-id 9677702
```

## Production (Cairo / KAIRO)

Use **`payload-kairo-production.json`** when pointing n8n at the real Cairo mission (`Office: KAIRO`, mission `CalendarId`).

Historical dev payloads: `docs/turbo-booker-archive/test-payloads/`.
