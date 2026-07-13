<!--
  SYNC IMPACT REPORT
  ==================
  Version change: (none) → 1.0.0  [INITIAL RATIFICATION]
  Bump rationale: First constitution created from project context (AGENTS.md,
  python-booker-webhook/README.md). No prior version existed; this is the
  baseline for all future amendments.

  Modified principles: none (all are new)
  Added sections:
    - Core Principles I–V (Evidence-First Debugging, Test Discipline & Isolation,
      Modular Pure Logic, Stable Entry Point & Environment Contracts,
      Captcha Pipeline Integrity)
    - Operational Constraints
    - Development Workflow
    - Governance
  Removed sections: none

  Templates requiring updates:
    - .specify/templates/plan-template.md  ✅ consistent (Constitution Check is
      generic placeholder; no project-specific references to change)
    - .specify/templates/spec-template.md  ✅ consistent (no constitution refs)
    - .specify/templates/tasks-template.md ✅ consistent (test paths align with
      tests/unit|integration|e2e rule)
    - .specify/templates/commands/*.md     ✅ not present; nothing to verify

  Deferred TODOs: none
-->

# Austrian Appointment System Constitution

## Core Principles

### I. Evidence-First Debugging

For any captcha, BotDetect, Playwright, or flaky-form defect, diagnosis MUST
follow the evidence-first workflow before any code change:
1. Freeze observed vs expected behavior, environment, artifacts, and logs.
2. Reconstruct the timeline: navigation → DOM → network → response → new DOM.
3. Inspect console for CSP, CORS, BotDetect JS errors, and `net::ERR_*` failures.
4. Compare hidden-field values (captcha token/VCID) at solve vs submit; verify
   cookies and redirects.
5. Parse response HTML for validation blocks (`.validation-summary-errors`).
6. Web-search quoted error strings for known BotDetect/ASP.NET WebForms issues.
7. Rank hypotheses by likelihood with a falsification test.
8. State the root cause in one paragraph.
9. Apply the minimal fix with verification and guardrails.

Decisions MUST be grounded in artifacts (`attempt_N_response.html`,
`classify_post_submit_html()` output), never in guesswork.

### II. Test Discipline & Isolation

- Test code MUST live only under `tests/unit/`, `tests/integration/`, or
  `e2e/` — never at the package root.
- The unit + integration baseline MUST remain green (baseline: 77 passing).
- Dev and CI benchmarks MUST use `Office: KOPENHAGEN` as the stand-in for
  production Cairo (`KAIRO`); production constants MUST stay KAIRO-based.
- Test payload fixtures MUST live in `fixtures/payloads/` (not `docs/` or root).
- Integration tests MUST cover the `/webhook/booker` (turbo) contract and the
  result-type classification.

### III. Modular Pure Logic

- New pure logic (parsing, classification, field extraction) MUST be extracted
  into dedicated modules — e.g. `app.py`, `captcha_ocr.py`, `scheduler_html.py`.
- `booker_legacy.py` is the Playwright core and MUST NOT grow with new logic;
  new behavior goes into its own module.
- `sandboxes/` MUST hold only scripts and workflows; duplicate production
  Python MUST NOT be added there.
- Phase 4 (extract a `booker/` package from `booker_legacy.py`) remains the
  planned direction and MUST not be blocked by adding logic into the legacy file.

### IV. Stable Entry Point & Environment Contracts

- The server entry `uvicorn app:app` MUST stay intact and unbroken.
- Dev vs production separation is NON-NEGOTIABLE: `KOPENHAGEN` for dev,
  `KAIRO` constants for production.
- Ephemeral outputs MUST NOT be committed: `*-scan.json`, `local-fix-*`,
  and `artifacts/` are gitignored debug output.
- Runtime debug artifacts, when enabled, MUST be written under
  `artifacts/<runId>/` and never checked into the repository.

### V. Captcha Pipeline Integrity

- The solver order MUST follow `CAPTCHA_SOLVER`: default
  `ocr_with_gemini_fallback` (OCR first, Gemini on failure; rounds ≥2 start
  with Gemini). The VPS gate (`CAPTCHA_MAX_STYLE_RELOADS`, `CAPTCHA_MIN_STYLE`)
  applies.
- Capture MUST be DOM-only (`CAPTCHA_PARALLEL_FETCH=false`).
- The booker MUST return only the standardized result types:
  SUCCESS, CAPTCHA_FAIL, SLOT_TAKEN, ALREADY_BOOKED, OTHER_FAIL,
  BOOKING_SERVER_BUSY — consumers MUST branch on these exactly.
- On captcha failure, fresh hidden fields MUST be extracted from the returned
  page before retrying.

## Operational Constraints

- Architecture is fixed: `n8n watcher → slots queue → n8n booker →
  POST /webhook/booker → app.py (turbo)`; OCR (8001) + Gemini + Playwright
  reach BMEIA. New layers MUST not bypass this flow.
- Before any captcha, VPS, or n8n change, `docs/AGENT_HANDOFF.md` MUST be read
  for current VPS status and open work.
- Debug workloads MUST be routed to the `deep-web-debugger` and
  `captcha-investigator` agents per AGENTS.md, not ad-hoc script edits.
- `artifacts/<runId>/` output (captcha PNGs, response HTML, form captures,
  solved captchas) is local-only and MUST stay gitignored.

## Development Workflow

- All feature work SHOULD originate from a spec + plan that passes the
  Constitution Check gate before research.
- Debugging follows Principle I; fixes MUST be minimal and verified against the
  failing artifact.
- Commits MUST exclude ephemeral outputs (Principle IV) and MUST keep the test
  baseline green (Principle II).
- The constitution file is the source of truth; AGENTS.md and runtime docs MUST
  be updated when principles change.

## Governance

This constitution supersedes all ad-hoc practices. Any PR or review MUST verify
compliance with the Core Principles; complexity that violates a principle MUST
be explicitly justified in a Complexity Tracking note.

**Versioning policy** — semantic versioning:
- MAJOR: removal or backward-incompatible redefinition of a principle.
- MINOR: new principle/section or materially expanded guidance.
- PATCH: clarifications, wording, or typo fixes with no behavioral change.

**Amendment procedure** — propose a change via PR that (1) updates this file,
(2) increments `CONSTITUTION_VERSION` per the policy, (3) prepends a Sync
Impact Report, and (4) updates any dependent templates or docs. Amendments
require review sign-off before merge.

**Compliance review** — compliance is checked at every PR and at each
constitution amendment; the ratification and last-amended dates MUST be kept
current.

**Version**: 1.0.0 | **Ratified**: 2026-07-10 | **Last Amended**: 2026-07-10
