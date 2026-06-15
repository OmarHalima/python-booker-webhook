# GitNexus — code intelligence (self-hosted)

Self-hosted code graph on VPS, connected to Cursor via MCP. Used by the **[orchestrator](../.cursor/skills/orchestrator/SKILL.md)** for exploration, impact analysis, and pre-merge reviews.

## Quick links

| Doc / tool | Purpose |
|------------|---------|
| [Orchestrator skill](../.cursor/skills/orchestrator/SKILL.md) | Routes agents to GitNexus by intent |
| [Routing table — MCP](../.cursor/skills/orchestrator/routing-table.md) | When to call each MCP tool |
| [gitnexus-code-intelligence skill](../.cursor/skills/gitnexus-code-intelligence/SKILL.md) | Agent playbook (short) |
| [Local MCP config](#local-cursor-mcp) | `%USERPROFILE%\.cursor\mcp.json` |
| Web UI | https://git-nexus-git-nexus-web.chattyai.cloud |

---

## Endpoints (live)

| Endpoint | URL |
|----------|-----|
| Web UI + API | https://git-nexus-git-nexus-web.chattyai.cloud |
| MCP (Cursor) | https://git-nexus-git-nexus-web.chattyai.cloud/api/mcp |
| Health | `GET /api/health` → `{"status":"ok"}` |
| Repos | `GET /api/repos` |

---

## What is deployed on VPS (do not redo unless broken)

### EasyPanel services

| Service | Role |
|---------|------|
| **git-nexus** | Backend — clone, index, graph store |
| **git-nexus-web** | UI, REST API, MCP endpoint |

### Web UI LLM

`GITNEXUS_LLM_*` env vars on **git-nexus-web**. Nexus AI chat in the UI works after refresh.

### Private GitHub clones

On **git-nexus** container:

- `GITHUB_TOKEN` for private repos
- Git config in start command:
  ```
  url."https://oauth2:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"
  ```

### Auto-reindex on push (n8n)

```
GitHub push (OmarHalima/python-booker-webhook)
    → n8n GitHub Trigger
    → SSH to 193.203.191.168 (user omar)
    → /home/omar/chatwoot_saas_development/scripts/gitnexus/n8n-ssh-reindex.sh {{ full_name }} {{ branch }}
```

| Branch | Index path on disk |
|--------|-------------------|
| `main` | `python-booker-webhook` |
| `v1.0.1` | `python-booker-webhook-v1.0.1` |

Re-index is **incremental** (same `.gitnexus/` store, refreshed on new commits; skipped if commit unchanged).

Reindex script on VPS: `/home/omar/chatwoot_saas_development/scripts/gitnexus/n8n-ssh-reindex.sh`

---

## Local Cursor MCP

File: **`%USERPROFILE%\.cursor\mcp.json`** (merge — do not remove other servers):

```json
"gitnexus": {
  "url": "https://git-nexus-git-nexus-web.chattyai.cloud/api/mcp"
}
```

Cursor exposes this as MCP server **`user-gitnexus`**.

After editing: **Settings → MCP → refresh**, or restart Cursor.

### Verify from agent

Call MCP tool **`list_repos`** — should return indexed repos and `lastCommit`.

---

## Indexed repository

| Field | Value |
|-------|--------|
| **repo name** (MCP `repo` param) | `python-booker-webhook` |
| **GitHub** | `OmarHalima/python-booker-webhook` |
| **Index path** | `python-booker-webhook-v1.0.1` when `v1.0.1` branch was last pushed |
| **Check live** | `GET /api/repos` or MCP `list_repos` |

Always pass `repo: "python-booker-webhook"` on query/context/impact calls.

---

## Orchestrator integration

GitNexus is wired into the orchestrator for these intents:

| Intent | GitNexus use |
|--------|----------------|
| `explore-only` | **Primary** — `query` + `context` |
| `code-intelligence` | Dedicated GitNexus queries |
| `code-review` | `impact` upstream on changed symbols |
| `refactor-structure` | `impact` + `detect_changes` |
| `new-feature` | Optional `query` for existing flows |

### Agent playbook

1. **`list_repos`** — confirm index + `lastCommit`
2. **`query`** — execution flows by concept (`repo: "python-booker-webhook"`)
3. **`context`** — 360° symbol view (use `file_path` if ambiguous)
4. **`impact`** — upstream blast radius before edits (`includeTests: true` for merges)
5. **`detect_changes`** — diff impact across branches

### Route example

> **Intent:** code-review → **Skill:** gitnexus-code-intelligence → **MCP:** impact on captcha symbols → **Agent:** booker-domain-reviewer

---

## MCP tools reference

| Tool | When |
|------|------|
| `list_repos` | Verify index / staleness |
| `query` | Execution flows for a concept |
| `context` | Callers, callees, one symbol |
| `impact` | Pre-merge / pre-refactor blast radius |
| `detect_changes` | Git diff impact |
| `cypher` | Advanced graph queries |
| `rename` | Coordinated multi-file rename |

---

## Staleness

Index updates on **GitHub push** via n8n — not on every local save.

If local workspace diverges from `lastCommit` in `list_repos`, push the branch or run manual reindex on VPS.

Current `v1.0.1` index includes `sandboxes/v1.0.1/` (not legacy `versions/` paths).

---

## Related docs

| Doc | Relation |
|-----|----------|
| [AGENT_HANDOFF.md](../python-booker-webhook/docs/AGENT_HANDOFF.md) | Production captcha/VPS |
| [v1.0.1 sandbox](../python-booker-webhook/sandboxes/v1.0.1/README.md) | Indexed OCR release |
| [AGENTS.md](../AGENTS.md) | Agent entry |
| [.cursor/README.md](../.cursor/README.md) | Orchestrator, skills, hooks |

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| MCP tools missing | `mcp.json` gitnexus url; reload MCP |
| Empty `list_repos` | VPS service; `GET /api/repos` |
| Wrong code in graph | Branch mapping; push to reindex |
| Symbol not found | Stale index; use `file_path` in `context` |
| Multiple repos error | Set `repo: "python-booker-webhook"` |
