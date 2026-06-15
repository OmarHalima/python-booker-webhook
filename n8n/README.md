# n8n Workflows

Canonical exports for the Austrian appointment automation system.

| File | Purpose |
|------|---------|
| [`booker.json`](booker.json) | Booker webhook workflow — POST to Python booker on slot found |
| [`watcher-v3.json`](watcher-v3.json) | Cairo/Kairo appointment slot watcher (v3) |

## Import

1. Open n8n → **Workflows** → **Import from file**
2. Select the JSON file
3. Activate and verify credentials (HTTP node URL, timeouts)

## Python booker

The booker workflow calls `POST /webhook/booker` on the Python service. See [`python-booker-webhook/README.md`](../python-booker-webhook/README.md).

A copy also lives at `python-booker-webhook/workflows/booker.json` for convenience when working inside the Python project.

## Workflow IDs

Sandbox-specific lock-integration workflow IDs are documented in [`python-booker-webhook/sandboxes/lock-integration-v1/docs/n8n-workflow-ids.json`](../python-booker-webhook/sandboxes/lock-integration-v1/docs/n8n-workflow-ids.json) when present.
