# n8n workflows

## booker.json

Booking workflow: slot queue → merge student → `POST /webhook/booker` → result switch (SUCCESS / CAPTCHA_FAIL / SLOT_TAKEN / ALREADY_BOOKED).

**Import:** n8n → Workflows → Import from file → select `booker.json`.

**After import:** update the HTTP Request node URL to your booker endpoint (ngrok or VPS).

**HTTP Request settings (already in export):**

- Timeout: 300000 ms
- Body includes: `runId`, `captureArtifacts`, `useSchedulerPost`, `submitMode: browser`
- Retry on fail: 2 attempts, 60s between (handles 503 `BOOKING_SERVER_BUSY`)

See [docs/n8n-booking-result-flow.md](../docs/n8n-booking-result-flow.md) for result handling.
