---
name: gitnexus-code-intelligence
description: Queries self-hosted GitNexus on VPS for code graphs, execution flows, symbol context, and merge impact on python-booker-webhook. Use for explore-only, code-intelligence, code-review, or refactor-structure intents. Orchestrator routes here via user-gitnexus MCP.
---

# GitNexus code intelligence

- **Full doc:** [docs/GITNEXUS.md](../../../docs/GITNEXUS.md)
- **Orchestrator:** [orchestrator/SKILL.md](../orchestrator/SKILL.md)

## When to use

- How does captcha / turbo / booking flow work?
- Pre-merge or pre-refactor impact
- Code review with graph evidence
- `explore-only` before editing unfamiliar code

## MCP

- Server: **`user-gitnexus`**
- Always: `repo: "python-booker-webhook"`

## Steps

1. `list_repos` — check `lastCommit`
2. `query` — flows for concept
3. `context` — one symbol (`file_path` if ambiguous)
4. `impact` — upstream before edits

## Orchestrator mapping

| Intent | Action |
|--------|--------|
| `explore-only` | query → context |
| `code-intelligence` | full playbook |
| `code-review` | impact on changed symbols |
| `refactor-structure` | impact → detect_changes |

## Do not

- Redeploy VPS GitNexus (already live — see docs/GITNEXUS.md)
- Omit `repo` on MCP calls
- Skip GitNexus when reviewing v1.0.1 → main merges
