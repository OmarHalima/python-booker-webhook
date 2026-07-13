# subagentStop — nudge verification after review/debug subagents complete
$inputRaw = [Console]::In.ReadToEnd()
if (-not $inputRaw) { exit 0 }

try {
    $payload = $inputRaw | ConvertFrom-Json
} catch {
    exit 0
}

$type = $payload.subagent_type
if (-not $type) { exit 0 }

$msg = switch -Regex ($type) {
    'code-reviewer' { 'Subagent review finished. Fix Critical/Important findings, run `pytest tests/unit tests/integration -q`, then use verification-before-completion before claiming done.' }
    'deep-web-debugger' { 'Web debug subagent finished. Apply minimal fix, re-run failing test or payload script, cite evidence (HAR/snapshot) in summary.' }
    'security-review' { 'Security review finished. Address Critical/High items before merge.' }
    'booker-domain-reviewer' { 'Domain review finished. Fix Critical/Important items, run pytest, update AGENT_HANDOFF if captcha/VPS decisions changed.' }
    'captcha-investigator' { 'Captcha investigation finished. Apply minimal fix with tests in tests/unit/, cite artifact evidence.' }
    'vps-booker-operator' { 'VPS ops finished. Confirm booker :8000 and OCR :8001 health; report solver env mode.' }
    'n8n-booker-specialist' { 'n8n workflow finished. Export JSON to n8n/ and verify HTTP timeout 300s + runId in body.' }
    'explore' { 'Exploration finished. Proceed with mapped project skill - do not re-explore the same paths.' }
    default { $null }
}

if (-not $msg) { exit 0 }

$escaped = ($msg -replace '\\', '\\' -replace '"', '\"' -replace "`r", '' -replace "`n", '\n')
Write-Output "{ `"followup_message`": `"$escaped`" }"
exit 0
