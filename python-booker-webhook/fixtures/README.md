# Test fixtures

## Payloads (`payloads/`)

| File | Description |
|------|-------------|
| `payload-booker.json` | Canonical KOPENHAGEN test template (Cairo stand-in) |
| `payload-kairo-production.json` | Production Cairo (KAIRO) template |
| `kopenhagen-dummy-applicant.json` | Sandbox dummy applicant profile |

Archived historical payloads: `payloads/archive/` (migrated from `docs/turbo-booker-archive/test-payloads/`).

## Usage

```bash
.venv\Scripts\python.exe scripts\run_turbo_payload.py fixtures\payloads\payload-booker.json
```

Update `sessionCookie`, `currentMonday`, and `currentSlot` from:

```bash
.venv\Scripts\python.exe scripts\scan_slots.py --office KOPENHAGEN --calendar-id 9677702
```
