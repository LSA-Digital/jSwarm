---
name: uat-author-scenario
description: Mid-ticket, guide authoring a NEW UAT scenario (or updating an existing one) into the canonical scenarios JSON, generate the ticket-local .uat-test.md runbook, and scaffold collectable unit/integration test stubs — each step preview-gated and fail-loud, writing nothing until the developer approves
user-invocable: true
triggers:
  - uat author scenario
  - author uat scenario
  - add uat scenario mid ticket
  - new uat scenario and tests
argument-hint: "[--scenarios <scenarios.json>]"
level: 2
---


# uat-author-scenario: mid-ticket guided scenario authoring + runbook + test scaffolding

Option 3 of the `/jUAT` menu. It lets a developer, **while another ticket is mid-`/jGo`**, add UAT coverage for newly-discovered behavior without leaving the flow: author a brand-new scenario (or update an existing one), generate that scenario's executable `.uat-test.md` runbook, and scaffold the unit/integration test stubs it implies — then continue with `/jGo` to author the real assertions.

This skill is a **thin orchestration** over the existing UAT-scenario engine. It does not reimplement validation, rendering, or the preview gate — it drives the engine scripts. Two hard rules:

1. **Preview before apply, always.** Never write the canonical scenarios JSON, the runbook, or any test stub before a PREVIEW has been produced and the developer has approved it. Every write is receipt-gated by the underlying tool.
2. **Fail loud, write nothing on doubt.** If the active ticket is unresolved, closed/terminal, missing a plan, or its sources disagree; if input is schema-invalid; if a link is dangling; or if a receipt is stale — stop with a clear error and write nothing. There is no silent fallback substitution (never quietly pick a different ticket, scenarios path, or scenario id).

## Prerequisites

- **Python:** `.venv/bin/python` only (system Python is quarantined). Every command below uses it.
- The engine ships schema + renderer; no PDF backend is needed for this skill.

## Tools this skill drives

| Tool | Role |
|------|------|
| `jswarm/uat-scenarios/resolve_active_ticket.py` | Resolve + validate the active in-progress ticket (fail-loud gate). |
| `jswarm/uat-scenarios/query_uat_scenarios.py` | List existing scenario ids / groupings (NEW vs UPDATE decision). |
| `jswarm/uat-scenarios/create_uat_scenario.py` | NEW scenario: `preview` / `apply` (receipt + schema + strict-link gate). |
| `jswarm/uat-scenarios/populate_scenario_content.py` | UPDATE an existing scenario: `preview` / `apply` (same gate). |
| `jswarm/uat-scenarios/scaffold_uat_tests.py` | Generate the `.uat-test.md` runbook + collectable test stubs: `preview` / `apply`. |
| `jswarm/uat-scenarios/render-uat-scenarios.py` | Re-render derived Markdown; `--check` / `--strict-links` after apply. |

## Workflow (8 steps: keep the developer informed at each)

1. **Resolve + validate the active ticket.** Gather the candidate ticket from the per-session `active-ticket.json` binding, the git branch (`git branch --show-current`), and the session title, then validate with the resolver:

   ```bash
   .venv/bin/python jswarm/uat-scenarios/resolve_active_ticket.py \
     --plans-dir .jswarm/plans \
     --active-ticket "<from active-ticket.json or omit>" \
     --branch "$(git branch --show-current)" \
     --session-title "<session title or omit>" \
     --scenarios "<scenarios.json if known>" --json
   ```

   It accepts ONLY a plan whose frontmatter is `status: ACTIVE` with a planning/implementation `plan_status` (`2.planning.*` or `3.implementation.*`). It exits non-zero (write nothing) when the ticket is unresolved, the sources disagree, the plan is missing, or the ticket is closed/terminal (`READY_FOR_MERGE` / `DONE` / `WONT_DO` / `DEFERRED`). Resolve the target **scenarios JSON** path (project-supplied / ticket-local working slice); if ambiguous, ask — do not guess.

2. **NEW or UPDATE?** List what exists so the developer chooses correctly:

   ```bash
   .venv/bin/python jswarm/uat-scenarios/query_uat_scenarios.py --scenarios <scenarios.json> --schema <schema.json> --list-scenarios
   ```

3. **Draft** the scenario fields into a proposals sidecar `{"proposals": [{"id": "<id>", "content": { ... }}]}` (interactively or from a source doc). The proposal `id` is authoritative.

4. **PREVIEW (mandatory gate).** Merge in memory, schema-validate, strict-link check, and render a PREVIEW — the canonical JSON stays byte-unchanged. Show the developer.
   - NEW: `.venv/bin/python jswarm/uat-scenarios/create_uat_scenario.py preview --scenarios <scenarios.json> --proposals <draft.json>`
   - UPDATE: `.venv/bin/python jswarm/uat-scenarios/populate_scenario_content.py preview --scenarios <scenarios.json> --proposals <draft.json> --strict-links` (pass `--strict-links` so an update cannot write a dangling `image.path` into canonical JSON; the create path enforces this by default)

5. **APPLY on approval.** Re-validate (schema + strict-link), then write the canonical JSON gated by the fresh preview receipt, and re-render:
   - NEW: `... create_uat_scenario.py apply --scenarios <scenarios.json> --proposals <draft.json> --receipt <...PREVIEW.receipt.json>`
   - UPDATE: `... populate_scenario_content.py apply --scenarios <scenarios.json> --proposals <draft.json> --receipt <...PREVIEW.receipt.json> --strict-links`
   - then: `... render-uat-scenarios.py --scenarios <scenarios.json> --strict-links` (must exit 0).

6. **Generate the uat-test runbook** for the scenario into the active ticket's folder (preview → apply):

   ```bash
   .venv/bin/python jswarm/uat-scenarios/scaffold_uat_tests.py preview \
     --scenarios <scenarios.json> --scenario-id <id> --ticket <KEY> \
     --ticket-dir .jswarm/plans/<KEY> --tests-dir jswarm/uat-scenarios/tests \
     --template docs/templates/UAT_TEST_TEMPLATE.md
   # review, then:
   .venv/bin/python jswarm/uat-scenarios/scaffold_uat_tests.py apply ... --receipt <...PREVIEW.receipt.json>
   ```

   The runbook is `.jswarm/plans/<KEY>/<KEY>.uat-test.md` (new file, or a `## Scenario <id>:` section inserted before `## Pass Criteria` if it already exists — no clobber, idempotent re-append).

7. **Scaffold test stubs.** The same `apply` writes VALID, `pytest --collect-only`-clean unit + integration STUBS (intentionally skipped, explicit `TODO`/`skip` clauses). It will NOT overwrite an existing stub without `--overwrite-stubs`. **These stubs contain no runnable assertion logic** — they are placeholders. Authoring the real assertions belongs to `/jGo`'s TDD lane; do not ship a skipped stub as proof of the scenario.

8. **Report + hand off.** Print the scenario id, scenarios JSON path, runbook path, stub paths, and next steps: run **`/jGo`** to author the real test logic (TDD red-first), and merge authored scenarios back into the official inventory at `/jClose`.

## Boundaries

- **Thin menu only.** The `/jUAT` command file gains one row + one routing bullet; all workflow logic lives here and in the scripts.
- **Path confinement.** Writes are limited to the active ticket folder (`.jswarm/plans/<KEY>/`) and the selected scenarios JSON + project test dir. Nothing else is touched; the final report lists every changed path and leaks no secrets.
- **JSON canonical / Markdown derived.** Edit the JSON (via the tools), then regenerate the Markdown — never hand-edit the derived `.md`.
- **No overreach.** This skill scaffolds stubs and a runbook; it does not author full runnable tests, auto-join scenarios to groupings/PE2E clusters, or run browser/Playwright UAT.
