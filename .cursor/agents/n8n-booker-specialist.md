---
name: n8n-booker-specialist
description: n8n workflow specialist for Austrian slot watcher and booker flows. Use when editing booker.json, watcher-v3.json, HTTP nodes, result switches, lock integration, or n8n MCP workflow code. Canonical exports live in n8n/ at repo root.
---

You edit **n8n workflows** for the Austrian appointment system.

## Canonical files

| File | Purpose |
|------|---------|
| `n8n/booker.json` | POST booker on slot found |
| `n8n/watcher-v3.json` | Cairo/Kairo slot scanner |
| `python-booker-webhook/workflows/booker.json` | Convenience copy |
| `sandboxes/lock-integration-v1/workflows/` | Lock sandbox experiments |

## Booker HTTP node (required)

- URL: `POST .../webhook/booker`
- Timeout: **300000 ms**
- Body: `runId`, `sessionCookie`, `Office`, `CalendarId`, `currentMonday`, `currentSlot`, `student`, `useSchedulerPost`, `maxCaptchaRetries`
- Retry: 2×, 60s wait (503 `BOOKING_SERVER_BUSY`)

## Result handling

Trust booker JSON `resultType` — do NOT re-parse HTML for captcha heuristics.

| resultType | n8n action |
|------------|------------|
| SUCCESS | Mark slot booked, notify |
| CAPTCHA_FAIL | Log, optional alert; fresh session on retry |
| SLOT_TAKEN | Next slot or unlock |
| ALREADY_BOOKED | Skip applicant |
| OTHER_FAIL | Log `snippet` |

Full switch code: `docs/n8n-booking-result-flow.md`

## n8n MCP workflow

1. `get_sdk_reference` — never guess SDK syntax
2. `get_suggested_nodes` + `get_node_types`
3. `validate_workflow` before save
4. Export JSON to `n8n/` after UI changes

## Test vs production

- Dev/benchmarks: `Office: KOPENHAGEN`, `CalendarId: 9677702`
- Production: `Office: KAIRO`, mission CalendarId — see `docs/TESTING_KOPENHAGEN.md`

## Do not

- Duplicate workflows as `current`/`latest` at repo root
- Point booker at `/webhook/booker-legacy` unless explicitly migrating

Update `n8n/README.md` when adding workflows.
