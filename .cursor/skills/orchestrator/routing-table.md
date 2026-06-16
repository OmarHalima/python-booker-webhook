# Orchestrator routing table

Full reference for `.cursor/skills/orchestrator/SKILL.md`. Update when adding skills, subagents, rules, or MCP servers.

---

## Available subagents (Cursor Task tool)

| Subagent | Use when |
|----------|----------|
| `explore` | Fast codebase search, file patterns, "where is X" |
| `generalPurpose` | Multi-step research + implementation |
| `shell` | Git, pytest, deploy commands, VPS scripts |
| `code-reviewer` | Generic structure review, plan alignment |
| `deep-web-debugger` | Captcha DOM, Playwright, CORS, flaky forms |
| `bugbot` | User explicitly asks for Bugbot-style diff review |
| `security-review` | User explicitly asks for security review of changes |
| `ci-investigator` | Single failing PR CI check |
| `best-of-n-runner` | Isolated git worktree experiments |

## Project agents (`.cursor/agents/`)

Invoke via Task or user `@agent` when domain-specific expertise beats generic subagents.

| Agent | Use when |
|-------|----------|
| `captcha-investigator` | OCR/Gemini/BotDetect, CAPTCHA_FAIL, desync, solver env |
| `vps-booker-operator` | Deploy, restart, Docker, logs on 193.203.191.168 |
| `booker-domain-reviewer` | Post-refactor review against project rules + AGENT_HANDOFF |
| `n8n-booker-specialist` | booker.json, watcher-v3.json, result switches, lock workflows |
| `orchestrator` | Meta-routing, multi-lane planning |
| `deep-web-debugger` | Browser/network timeline (also Cursor built-in subagent) |

---

## Project skills (`.cursor/skills/`)

| Skill | Use when |
|-------|----------|
| `orchestrator` | Session start, classify intent, pick skills/subagents |
| `captcha-pipeline` | Captcha OCR/Gemini/BotDetect debugging |
| `vps-booker-ops` | VPS deploy, health, SSH MCP ops |
| `n8n-booker-workflows` | n8n MCP workflow edit + export to `n8n/` |
| `booker-e2e-kopenhagen` | scan_slots, turbo payload, live KOPENHAGEN tests |
| `booker-pytest` | Add/run tests in `tests/unit|integration|e2e/` |
| `booker-package-extract` | Phase 4 monolith → `booker/` package |
| `gitnexus-code-intelligence` | GitNexus MCP queries, impact, flows — see [docs/GITNEXUS.md](../../docs/GITNEXUS.md) |

---

## Superpowers skills (plugin)

### Process — invoke before acting

| Skill | Trigger |
|-------|---------|
| `using-superpowers` | Every session; any possible skill match |
| `brainstorming` | New feature, behavior change, creative work |
| `systematic-debugging` | Any bug, test failure, unexpected behavior |
| `writing-plans` | Multi-step task with spec/requirements |
| `executing-plans` | Written plan in separate session |
| `subagent-driven-development` | Plan with independent tasks in one session |
| `dispatching-parallel-agents` | 2+ independent parallel tasks |
| `using-git-worktrees` | Isolated feature branch work |
| `verification-before-completion` | Before claiming done/fixed/passing |
| `test-driven-development` | New feature or bugfix implementation |
| `receiving-code-review` | Feedback from reviewer to evaluate |
| `requesting-code-review` | After major step, before merge |
| `finishing-a-development-branch` | Implementation complete, choose merge/PR |

---

## Project rules (`.cursor/rules/`)

| Rule | Scope | Enforces |
|------|-------|----------|
| `project-architecture.mdc` | always | Folder layout, runtime flow, where files go |
| `refactor-guardrails.mdc` | always | No ephemeral commits, no sandbox python dupes |
| `orchestrator.mdc` | always | Load orchestrator skill first |
| `python-booker.mdc` | `python-booker-webhook/**` | Modules, endpoints, env |
| `testing.mdc` | `tests/**`, `test_*.py` | pytest layout, fixtures |
| `n8n-workflows.mdc` | `n8n/**`, workflow JSON | Canonical exports, HTTP settings |

---

## MCP servers

| Server | Use when |
|--------|----------|
| `user-engram` | Session memory, save decisions (`mem_save`, `mem_context`) |
| `user-n8n-mcp` | Create/update/validate n8n workflows programmatically |
| `user-ssh-remote-management` | VPS at `193.203.191.168` (user `omar`) — deploy, logs, Hermes |
| `cursor-ide-browser` | Live BMEIA page inspection, captcha DOM |
| `user-github` | PRs, issues, CI |
| `user-gitnexus` | Code graph on VPS — **orchestrator intents:** `explore-only`, `code-intelligence`, `code-review`, `refactor-structure`. Full doc: [docs/GITNEXUS.md](../../docs/GITNEXUS.md) |
| `plugin-context7-context7` | Library/framework docs lookup |

**n8n MCP:** Always read SDK reference before writing workflow code.

**SSH MCP:** Read tool schema before `remote-ssh`. Host: `193.203.191.168`, user: `omar`.

---

## Intent → full playbook

### new-feature

```
1. brainstorming
2. writing-plans (if >1 file or architectural choice)
3. using-git-worktrees (if isolation needed)
4. test-driven-development
5. implement (match python-booker.mdc)
6. pytest tests/unit tests/integration -q
7. requesting-code-review → code-reviewer subagent
8. finishing-a-development-branch (if branch complete)
```

### bug-debug (booking/captcha/slots)

```
1. systematic-debugging
2. captcha-pipeline skill (if captcha-related)
3. Read AGENT_HANDOFF.md
4. captcha-investigator agent OR deep-web-debugger subagent
5. fix minimal diff
6. booker-pytest: pytest tests/unit tests/integration -q
7. verification-before-completion
```

### e2e-test

```
1. booker-e2e-kopenhagen skill
2. scan_slots → update fixtures/payloads/payload-booker.json
3. run_turbo_payload.py or POST /webhook/booker
4. captcha-pipeline if CAPTCHA_FAIL
```

### refactor-structure

```
1. writing-plans
2. gitnexus-code-intelligence: impact on symbols to move
3. code-reviewer subagent (baseline audit)
4. incremental moves (see refactor-guardrails.mdc Phase 4)
5. pytest after each step
6. code-reviewer subagent (final)
```

### code-intelligence / explore-only

```
1. gitnexus-code-intelligence skill
2. list_repos → query → context (→ impact if editing)
3. Read AGENT_HANDOFF.md if touching production paths
```

See [docs/GITNEXUS.md](../../docs/GITNEXUS.md).

### n8n-workflow

```
1. n8n-booker-workflows skill
2. n8n-booker-specialist agent (complex changes)
3. n8n-mcp: get_sdk_reference → validate_workflow
4. Export to n8n/
```

### vps-ops

```
1. vps-booker-ops skill
2. vps-booker-operator agent
3. ssh-remote-management MCP
4. verification-before-completion
```

---

## Parallel lanes (Hermes fan-out)

Split when user request contains independent outcomes:

| User says | Lane A | Lane B |
|-----------|--------|--------|
| "fix captcha and update n8n workflow" | bug-debug → deep-web-debugger | n8n-workflow → n8n-mcp |
| "add tests and review structure" | test-work → TDD | code-review → code-reviewer |
| "deploy VPS and run pytest" | vps-ops → shell | test-work (local) |

Use `dispatching-parallel-agents` or parallel Task calls. Merge results before final verification.

---

## Domain quick reference

| Topic | Canonical location |
|-------|---------------------|
| Production state | `python-booker-webhook/docs/AGENT_HANDOFF.md` |
| Test payloads | `python-booker-webhook/fixtures/payloads/` |
| n8n exports | `n8n/` |
| Unit tests | `python-booker-webhook/tests/unit/` |
| Sandbox lock work | `python-booker-webhook/sandboxes/lock-integration-v1/` |
| OCR fix / iteration sandbox | `sandboxes/v1.0.1/` (port 8011); archived: `archive/sandboxes/ocr-fix-v1/` |
| GitNexus code graph | [docs/GITNEXUS.md](../../docs/GITNEXUS.md) — MCP `user-gitnexus` |
| VPS booker | port 8000, `uvicorn app:app` |
| VPS OCR | port 8001, `/predict` |

---

## Commands cheat sheet

```bash
# Tests
cd python-booker-webhook && pytest tests/unit tests/integration -q

# Run booker locally
uvicorn app:app --host 0.0.0.0 --port 8000

# OCR check
python scripts/run_ocr_captcha_api.py

# Turbo payload
python scripts/run_turbo_payload.py fixtures/payloads/payload-booker.json
```
