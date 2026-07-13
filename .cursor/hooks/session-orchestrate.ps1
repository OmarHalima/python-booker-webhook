# SessionStart — inject orchestrator routing card (Hermes-style triage)
$ErrorActionPreference = 'Stop'

$card = @'
# Orchestra - Austrian Booker

Before acting: classify intent -> invoke mapped Superpowers skill -> delegate subagent if needed -> apply .cursor/rules/ -> verify.

| Intent | First skill | Subagent |
|--------|-------------|----------|
| new feature | brainstorming | explore |
| bug/captcha | systematic-debugging, captcha-pipeline | captcha-investigator |
| refactor/structure | booker-package-extract | booker-domain-reviewer |
| tests | booker-pytest, TDD | (none) |
| e2e booking | booker-e2e-kopenhagen | (none) |
| n8n | n8n-booker-workflows | n8n-booker-specialist |
| VPS deploy | vps-booker-ops | vps-booker-operator |
| review | requesting-code-review | code-reviewer |

Full router: .cursor/skills/orchestrator/SKILL.md
Routing table: .cursor/skills/orchestrator/routing-table.md
Onboarding: AGENTS.md + python-booker-webhook/docs/AGENT_HANDOFF.md

Verify before done: cd python-booker-webhook && pytest tests/unit tests/integration -q
'@

function Escape-Json([string]$s) {
    $s = $s -replace '\\', '\\'
    $s = $s -replace '"', '\"'
    $s = $s -replace "`r", ''
    $s = $s -replace "`n", '\n'
    return $s
}

$escaped = Escape-Json $card
Write-Output "{ `"additional_context`": `"$escaped`" }"
exit 0
