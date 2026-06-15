# Cursor project config

## Orchestrator (Hermes-style routing)

Routes every session to the right skills, subagents, rules, and MCP tools.

| File | Role |
|------|------|
| [`skills/orchestrator/SKILL.md`](skills/orchestrator/SKILL.md) | Main router |
| [`skills/orchestrator/routing-table.md`](skills/orchestrator/routing-table.md) | Full mapping |
| [`docs/GITNEXUS.md`](../docs/GITNEXUS.md) | GitNexus ↔ orchestrator integration |

## Project skills

| Skill | Purpose |
|-------|---------|
| [`captcha-pipeline`](skills/captcha-pipeline/SKILL.md) | OCR/Gemini/BotDetect debugging |
| [`vps-booker-ops`](skills/vps-booker-ops/SKILL.md) | VPS deploy + SSH health checks |
| [`n8n-booker-workflows`](skills/n8n-booker-workflows/SKILL.md) | n8n MCP + workflow exports |
| [`booker-e2e-kopenhagen`](skills/booker-e2e-kopenhagen/SKILL.md) | Live KOPENHAGEN test flows |
| [`booker-pytest`](skills/booker-pytest/SKILL.md) | Unit/integration test conventions |
| [`booker-package-extract`](skills/booker-package-extract/SKILL.md) | Phase 4 monolith extraction |
| [`gitnexus-code-intelligence`](skills/gitnexus-code-intelligence/SKILL.md) | GitNexus MCP — flows, context, impact |

Full GitNexus doc: [`docs/GITNEXUS.md`](../docs/GITNEXUS.md)

## Project agents

| Agent | Purpose |
|-------|---------|
| [`captcha-investigator`](agents/captcha-investigator.md) | Deep captcha pipeline diagnosis |
| [`vps-booker-operator`](agents/vps-booker-operator.md) | VPS incidents + deploy |
| [`booker-domain-reviewer`](agents/booker-domain-reviewer.md) | Domain-aware code review |
| [`n8n-booker-specialist`](agents/n8n-booker-specialist.md) | Watcher + booker workflows |
| [`orchestrator`](agents/orchestrator.md) | Meta-routing |
| [`deep-web-debugger`](agents/deep-web-debugger.md) | Browser/network debugging |

## Hooks & rules

| Path | Role |
|------|------|
| [`hooks.json`](hooks.json) | sessionStart + subagentStop |
| [`rules/`](rules/) | Auto-applied architecture guardrails |

Entry: root [`AGENTS.md`](../AGENTS.md)
