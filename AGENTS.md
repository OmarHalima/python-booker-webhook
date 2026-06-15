# Agent guide — Austrian appointment system

Start here in a new session before changing code, tests, or n8n workflows.

## Orchestrator (read first)

**`.cursor/skills/orchestrator/SKILL.md`** — Hermes-style router: classifies intent → picks Superpowers skills → delegates subagents → applies rules → verifies.

Compact reference: `.cursor/skills/orchestrator/routing-table.md`

Session hook injects the routing card automatically via `.cursor/hooks.json`.

## Project skills & agents

| Skill | Agent (delegate when deep) |
|-------|----------------------------|
| `captcha-pipeline` | `captcha-investigator` |
| `vps-booker-ops` | `vps-booker-operator` |
| `n8n-booker-workflows` | `n8n-booker-specialist` |
| `booker-e2e-kopenhagen` | — |
| `booker-pytest` | — |
| `booker-package-extract` | `booker-domain-reviewer` |
| `gitnexus-code-intelligence` | — (MCP `user-gitnexus`) |

All under `.cursor/skills/` and `.cursor/agents/`. See [`.cursor/README.md`](.cursor/README.md).

## Code intelligence (GitNexus)

Self-hosted code graph on VPS, wired into the orchestrator for exploration and pre-merge impact analysis.

| Doc | Purpose |
|-----|---------|
| [`docs/GITNEXUS.md`](docs/GITNEXUS.md) | Endpoints, VPS deploy, MCP setup, orchestrator mapping |
| [`.cursor/skills/gitnexus-code-intelligence/SKILL.md`](.cursor/skills/gitnexus-code-intelligence/SKILL.md) | Agent playbook |

MCP server: **`user-gitnexus`** — always pass `repo: "python-booker-webhook"`.

## Read first

| Doc | When |
|-----|------|
| [`.cursor/skills/orchestrator/SKILL.md`](.cursor/skills/orchestrator/SKILL.md) | **Every session** — route skills/subagents/rules |
| [`python-booker-webhook/docs/AGENT_HANDOFF.md`](python-booker-webhook/docs/AGENT_HANDOFF.md) | Captcha, VPS, production state |
| [`python-booker-webhook/README.md`](python-booker-webhook/README.md) | Setup, endpoints, env vars |
| [`.cursor/rules/`](.cursor/rules/) | Architecture and conventions (auto-applied) |

## System overview

```
n8n watcher → slots queue → n8n booker → POST /webhook/booker → app.py (turbo)
                                                      ↓
                                            OCR (8001) + Gemini + Playwright → BMEIA
```

| Layer | Location |
|-------|----------|
| n8n workflows | [`n8n/`](n8n/) |
| Python booker | [`python-booker-webhook/`](python-booker-webhook/) |
| Unit/integration tests | `python-booker-webhook/tests/` |
| Test payloads | `python-booker-webhook/fixtures/payloads/` |
| Sandbox experiments | `python-booker-webhook/sandboxes/` |
| OCR fix release | `sandboxes/v1.0.1/` (port 8011, from ocr-fix-v1) |
| Runtime debug output | `python-booker-webhook/artifacts/` (gitignored) |

## Commands

```bash
cd python-booker-webhook
pip install -r requirements-dev.txt
pytest tests/unit tests/integration -q
uvicorn app:app --host 0.0.0.0 --port 8000
```

## Hard rules

- **Do not** add tests at package root — use `tests/unit|integration|e2e/`.
- **Do not** commit scan JSON, `local-fix-*.json`, or `artifacts/` output.
- **Do not** duplicate production Python into `sandboxes/` — sandboxes hold workflow patches and scripts only.
- **Do not** break `uvicorn app:app` — entry point stays at `python-booker-webhook/app.py`.
- **Prefer** KOPENHAGEN payloads for dev; KAIRO constants for production only.
- **Extract** new pure logic into testable modules; avoid growing `booker_legacy.py` further.

## Planned refactor

Phase 4 (not done): extract `booker/` package from `booker_legacy.py` — move `captcha_ocr.py` and `scheduler_html.py` first, keep compatibility shims.
