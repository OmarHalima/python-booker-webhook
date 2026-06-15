---
name: n8n-booker-workflows
description: Edits and validates n8n booker and watcher workflows for Austrian appointments using n8n-mcp SDK. Use for workflow JSON, HTTP booker nodes, result switches, lock integration, or exporting to n8n/ folder.
---

# n8n booker workflows

## When to use

- Edit `n8n/booker.json` or `n8n/watcher-v3.json`
- Add HTTP node, retry logic, result switch
- Lock-integration sandbox workflows
- Programmatic workflow create/update via MCP

## Canonical paths

```
n8n/booker.json          # export after changes
n8n/watcher-v3.json
sandboxes/lock-integration-v1/workflows/
```

Rule: `n8n-workflows.mdc`

## n8n MCP workflow (strict order)

1. `get_sdk_reference`
2. `get_suggested_nodes` for technique categories
3. `search_nodes` + `get_node_types` for exact params
4. `validate_node_config` per node
5. `validate_workflow` full graph
6. `create_workflow_from_code` or `update_workflow`

## Booker HTTP node checklist

- [ ] URL: `POST /webhook/booker` (turbo)
- [ ] Timeout: 300000 ms
- [ ] Body includes `runId`, full `student`, slot fields
- [ ] Retry on fail: 2 attempts, 60s between
- [ ] Trust `resultType` from response (see `docs/n8n-booking-result-flow.md`)

## Test vs prod payloads

| | Test | Production |
|---|------|------------|
| Office | KOPENHAGEN | KAIRO |
| CalendarId | 9677702 | mission-specific |

See `docs/TESTING_KOPENHAGEN.md`

## After edit

1. Export JSON to `n8n/`
2. Update `n8n/README.md` if new workflow
3. Sync copy in `python-booker-webhook/workflows/` if booker changed

## Delegate

Complex n8n architecture → `n8n-booker-specialist` agent
