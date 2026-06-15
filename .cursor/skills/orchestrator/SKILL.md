---
name: orchestrator
description: Routes Austrian appointment system tasks to the correct Superpowers skills, Cursor subagents, project rules, and MCP tools. Use at session start, when the user asks to orchestrate, when intent is ambiguous, or before dispatching subagents. Hermes-style triage — classify first, then invoke the right playbook.
---

# Orchestrator — Skill & Subagent Router

Inspired by Hermes `kanban-orchestrator`: **classify → route → delegate → verify**. Do not improvise when a mapped skill or subagent exists.

## Step 0 — Classify (every turn)

Pick the **primary intent** (one only). If multiple independent lanes exist, split and route each separately (see [routing-table.md](routing-table.md)).

| Intent | Signals |
|--------|---------|
| `new-feature` | "add", "build", "implement", new endpoint/behavior |
| `bug-debug` | "fix", "broken", "fails", captcha/slot/navigation errors |
| `refactor-structure` | package extract, folder moves, cleanup |
| `test-work` | unit tests, pytest, coverage |
| `code-review` | review PR/diff/structure |
| `security-review` | security audit of changes |
| `n8n-workflow` | workflow JSON, n8n nodes, watcher/booker |
| `vps-ops` | deploy, restart, VPS, Docker, uvicorn, OCR service |
| `web-debug` | captcha DOM, BotDetect, browser vs HTTP mismatch |
| `docs` | README, AGENT_HANDOFF, architecture docs |
| `ci-github` | failing check, PR status |
| `explore-only` | "where is", "how does", read-only discovery |
| `e2e-test` | live booking, scan_slots, turbo payload, KOPENHAGEN |
| `orchestrate-meta` | user asks to route/orchestrate/plan multi-step work |

If unclear → ask one focused question, then re-classify.

## Step 1 — Load process skills first

**Order matters** (Superpowers priority):

1. **Process** — shapes *how* to work
2. **Implementation** — shapes *what* to build
3. **Verification** — before claiming done

Always invoke `using-superpowers` mindset: if a skill might apply (even 1%), read and follow it.

## Step 2 — Route by intent

Quick map (details in [routing-table.md](routing-table.md)):

| Intent | Skills (in order) | Subagent / Agent | Rules | MCP |
|--------|-------------------|------------------|-------|-----|
| `new-feature` | brainstorming → writing-plans → TDD → verification | `explore` | python-booker, refactor-guardrails | engram |
| `bug-debug` | systematic-debugging → **captcha-pipeline** → verification | **captcha-investigator** or `deep-web-debugger` | python-booker, testing | browser, engram |
| `refactor-structure` | writing-plans → **booker-package-extract** → verification | **booker-domain-reviewer** | refactor-guardrails, project-architecture | — |
| `test-work` | TDD → **booker-pytest** → verification | — | testing | — |
| `code-review` | requesting-code-review | **booker-domain-reviewer** or `code-reviewer` | testing, python-booker | — |
| `security-review` | — | `security-review` | refactor-guardrails | — |
| `n8n-workflow` | **n8n-booker-workflows** | **n8n-booker-specialist** | n8n-workflows | n8n-mcp |
| `vps-ops` | **vps-booker-ops** → verification | **vps-booker-operator** or `shell` | python-booker | ssh-remote-management |
| `web-debug` | systematic-debugging → **captcha-pipeline** | `deep-web-debugger` | python-booker | browser |
| `e2e-test` | **booker-e2e-kopenhagen** | — | testing, python-booker | browser |
| `docs` | documentation-engineering | — | project-architecture | — |
| `ci-github` | verification | `ci-investigator` | testing | github |
| `explore-only` | — | `explore` | project-architecture | gitnexus, engram |
| `orchestrate-meta` | dispatching-parallel-agents | multiple | all applicable | — |

## Step 3 — Apply project rules

Rules in `.cursor/rules/` auto-apply by glob. Confirm the active set:

- **Always:** `project-architecture`, `refactor-guardrails`, `orchestrator`
- **python-booker-webhook/**:** `python-booker`
- **tests/**:** `testing`
- **n8n/**, **workflows/**:** `n8n-workflows`

Read `docs/AGENT_HANDOFF.md` before captcha/VPS/n8n production changes.

## Step 4 — Delegate vs do

| Situation | Action |
|-----------|--------|
| Read-only discovery | `explore` subagent or direct tools |
| Complex bug (captcha/web) | `deep-web-debugger` |
| Major step complete | `code-reviewer` before next step |
| 2+ independent tasks | `dispatching-parallel-agents` / parallel Task |
| Written multi-step plan | `executing-plans` or `subagent-driven-development` |
| Small one-shot fix | Direct work + `verification-before-completion` |

**Anti-pattern:** Running code-review subagent for a simple typo. **Anti-pattern:** Skipping systematic-debugging on captcha/booking failures.

## Step 5 — Verify before done

Mandatory for any code change:

```bash
cd python-booker-webhook && pytest tests/unit tests/integration -q
```

Use `verification-before-completion` skill. Never claim success without command output.

## Step 6 — Report route (when orchestrating)

Tell the user briefly:

> **Intent:** bug-debug → **Skills:** systematic-debugging, verification → **Subagent:** deep-web-debugger → **Rules:** python-booker, testing

## Onboarding reads

| Priority | File |
|----------|------|
| 1 | `python-booker-webhook/docs/AGENT_HANDOFF.md` |
| 2 | `AGENTS.md` |
| 3 | This skill + [routing-table.md](routing-table.md) |
