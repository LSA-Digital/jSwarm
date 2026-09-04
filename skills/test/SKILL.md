---
name: "test"
description: "Symlink to the global /test command: single procedural entrypoint for testing strategy, UAT (scripts, execution, owner invitations), E2E, regression (creation, capture-replay, maintenance), backend-only proof, and diagnosis."
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/test/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy. -->

# /test - Testing Procedure Entry Point

Use `/test` when you need to choose or execute a testing workflow. This is the single procedural entrypoint for the whole testing capability family — strategy, UAT, E2E, regression (creation, capture-replay, maintenance), backend-only proof, and diagnosis — so `CLAUDE.md` / `AGENTS.md` files can stay lean and point here.

Sub-files (loaded per selected option, after `execution-protocol.md`):

- `execution-protocol.md` — universal context-load/handoff/follow-along rules for every option
- `uat.md` — options 2-4: Live Show scripts, jQATester execution, owner UAT invitation rounds
- `regression.md` — options 5-8: E2E regression creation/promotion, deterministic LLM-replay capture/refresh, existing verification
- `strategy-and-proof.md` — options 1, 9: testing strategy, backend/API/CLI-only proof
- `diagnose.md` — option 10: diagnose a failed or flaky testing run

## Global Command Project Localization

This is a global/shared command. Project-local rendered command files are not the source of truth.

For the complete developer-facing UAT cycle, ownership map, and companion navigation, read [test-uat.lifecycle.md](test-uat.lifecycle.md); this entrypoint does not duplicate that policy.

Before executing any non-smoke instruction in this command:

1. Determine the active project root from the current working directory.
2. If `.claude/project-command-injections.yaml` exists, read `managed_commands.test.md`.
3. For each configured anchor whose marker appears in this command, read its `snippet_path` or inline `content` and treat that content as if it replaced the matching `<!-- inject:... -->` marker.
4. If a configured anchor is `required: true` but the marker is missing, the snippet is missing, or the snippet is empty, stop with `LOCALIZATION ERROR` and explain the missing anchor.
5. If no project manifest exists, continue with the global command body as-is.

If invoked with `--localization-smoke`, do only the localization pass, print the project root, each configured anchor name, whether it resolved, and the first non-empty line of each resolved snippet; then stop without running the normal command workflow.

<!-- inject:project-advisories -->

---

## MASTER INVARIANT

Before executing ANY selected option: read and obey `execution-protocol.md`, THEN read the option's sub-file. The universal context-load/handoff/follow-along rules in `execution-protocol.md` bind every option (1-10) — strategy and proof, and diagnose included — not only UAT/regression. Sub-files may ADD requirements; none may skip the protocol.

Before choosing a UAT verification instrument, apply the [instrument selection behavior lock](uat.md#instrument-selection-behavior-lock).

---

## Menu

If the user did not specify an option, show this menu and ask for one choice:

```text
What testing workflow do you need?

1. Decide testing strategy for a ticket                -> strategy-and-proof.md
2. Create or tighten a Live Show UAT script             -> uat.md
3. Execute Live Show UAT with jQATester                 -> uat.md
4. Compose an owner UAT invitation round                -> uat.md
5. Create an E2E regression test                        -> regression.md
6. Promote Live Show UAT to E2E regression               -> regression.md
7. Capture/refresh deterministic LLM-replay fixtures    -> regression.md
8. Run existing E2E/regression verification              -> regression.md
9. Prove backend/API/CLI-only work without UAT          -> strategy-and-proof.md
10. Diagnose a failed or flaky testing run               -> diagnose.md
```

Do not continue until the option is clear. If a ticket is involved, capture the ticket ID and plan path.

---

## Browser tool guard context (BLOCKING)

Before using any browser automation driver for testing — Playwright MCP, Chrome DevTools MCP, headed Playwright, or project wrapper browser execution — create/update this marker:

```json
{
  "source": "/test",
  "status": "active",
  "selected_option": "<1-10>",
  "ticket": "TICKET-XXX or N/A",
  "plan_path": "docs/plans/TICKET-XXX... or N/A",
  "runner_command": "exact command or MCP driver",
  "created_at": "YYYY-MM-DDTHH:MM:SSZ",
  "expires_at": "YYYY-MM-DDTHH:MM:SSZ"
}
```

Write it to `.jswarm/state/testing-context.json` at the project root. Set `expires_at` no more than 4 hours after `created_at`. If `.jswarm/state/` does not exist, create it.

OpenCode `devops-guards` and the Claude `pretool-e2e-wrapper-enforcement.py` hook block direct Playwright/Chrome/browser MCP testing tools when this marker is absent, expired, or not sourced from `/test`. This prevents agents from bypassing the testing workflow and going straight to MCP Playwright.

---

## Final reporting format

End every `/test` run with:

- selected option
- ticket/plan path, if any
- docs/templates used
- command(s) run or handoff created
- evidence/report paths
- PASS/FAIL/BLOCKED status
- next required action
