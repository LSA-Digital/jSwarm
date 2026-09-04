# Step 5: Assemble plan file

## Step 5: Assemble plan file

### Pre-plan gate (Standard + Deep only, BLOCKING)

**Do NOT proceed to full plan creation** if ANY of:
- Any Open Technical Question is marked `Blocking = Yes` and `Status = Open`
- Planning Handoff → Planning Readiness is `Blocked`
- Deep plan: Oracle review is not `Complete`
- Oracle recommendation is `Revise Spec` and required changes have not been applied

If blocked, STOP and tell the user which blockers must be resolved.

### Resolve the assembly bundle (MANDATORY)

The assembler is the **ONLY** plan-assembly path. Do not select, copy, or hand-edit a template as a plan-birth fallback.

Resolve exactly one bundle from the ceremony/bypass answers using this frozen truth table:

| Answer / condition | Bundle |
| --- | --- |
| `l1.story.low-essential` | `QUICK` |
| owner-approved `rapid-vibe-ui` bypass | `QUICK` + `--with rapid-vibe-ui` |
| `l1.story.medium-standard` | `FULL` |
| `l1.story.high-assurance` | `FULL` |
| `--lite mode` | `LITE` |
| issue type `Feature` | `FEATURE` |
| legacy/no-selection + Quick depth | `QUICK` |
| legacy/no-selection + Standard depth | `FULL` |
| legacy/no-selection + Deep depth | `FULL` |

Apply bypasses as the table states: `--lite` resolves `LITE`; Feature resolves `FEATURE`; an owner-approved eligible `rapid-vibe-ui` selection resolves `QUICK` plus its registered addon; use the legacy depth rule only when there is no ceremony selection. The rapid-vibe bypass is invalid for Features or any trigger excluded by `pattern.rapid-vibe-ui.md`; fall back to the standard selector rather than weakening planning. If more than one applicable row resolves to different bundles, STOP with the typed bundle-resolution collision failure; do not choose a precedence, invoke the assembler, or invent a bundle. If a ceremony preset is not represented in this table, STOP with the typed unmapped-preset resolution failure; do not invoke the assembler or invent a bundle.

### Assemble the plan (MANDATORY)

Run the assembler **from the CONSUMER repository root** (the working directory of the repo the ticket/plan belongs to) (e.g. `~/dev/<project>`), NOT the `common` tool repo. The interpreter and the assembler script are always referenced by absolute `${JSWARM_HOME:-$HOME/dev/jswarm}/...` path; only the working directory and the relative `--out` path are consumer-repo-relative. Substituting the resolved bundle, ticket key, and plan slug:

When the assembled plan declares `Automated UAT: yes`, compose its single machine-readable trigger section during assembly with `jswarm/uat_trigger.py::compose_trigger_section`; never hand-author the JSON.

```bash
cd <CONSUMER-REPO-ROOT>   # the repo this ticket belongs to; the plan and its receipt land here
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/new_work_templates/assemble.py \
  --bundle <BUNDLE> \
  --ticket <KEY> \
  --out .jswarm/plans/<KEY>.plan.<slug>.md \
  [--with <pattern>...]
```

**Inputs/outputs ROOT env contract:**
- The assembler resolves its assembly INPUTS from `skills/jPlan/` inside the `common` tool repo by default; you do not normally set anything. `NEW_WORK_TEMPLATE_ROOT` overrides that inputs directory (recovery lever only). A missing template root / manifest fails loudly with the typed **missing-inputs-root** error that names this env var, distinct from a genuinely malformed manifest.
- The provenance RECEIPT is written into the SAME repository as the `--out` plan (derived from the output path's repo root), so running from the consumer repo root makes the receipt land in the consumer's `.jswarm/plans/<KEY>/`, where the Receipt gate below looks. `NEW_WORK_RECEIPT_ROOT` overrides the receipt's repo root (recovery lever only); do not set it in the normal flow.

`--with <pattern>` is the generic, additive mechanism for a separately registered addon pattern. Use it only for an applicable, registered addon; repeat it once for each selected addon. Do not add an addon-specific question or wire a particular addon in this step unless this file defines its evidence-driven selection contract below.

### Pre-issued execution rulings → `--with preissued-rulings`

Before assembly for QUICK, FULL, or FEATURE, assess the evidenced T1-T5 triggers in `pattern.preissued-rulings.md`. If one or more match, append `--with preissued-rulings`, populate only the matching library rulings plus the mandatory escalation-budget capstone, and bind each match to a ticket fact. If none match, omit the addon and section entirely. Lite always omits it, and the assembler rejects that addon/bundle combination. This is an author assessment, not a new owner question. A repository's stricter policy remains controlling, especially strict zero-red baselines, which do not qualify for T4.

### Rapid vibe UI opt-in → `--with rapid-vibe-ui`

When the owner explicitly selects `rapid-vibe-ui` and the eligibility boundary in `pattern.rapid-vibe-ui.md` passes, resolve `QUICK` and append `--with rapid-vibe-ui`. Do not combine it with `uat-round-tracking`: the rapid-vibe living-contracts file already owns the per-item ledger and owner-check interface. Populate `.jswarm/plans/<KEY>/<KEY>.uat-scenarios.md` as the pattern's living schema with no speculative defect rows, omit the separate `<KEY>.uat-test.md`, and replace CORE's placeholder A/C with the pattern's single closure A/C. The H/M/L decision-state lint does not apply because this is an explicit owner-approved bypass, not a selector downgrade.

### UAT round tracking opt-in → `--with uat-round-tracking` (B-08)

When the Q7-family UAT round tracking answer is affirmative (`yes` or `on`), append `--with uat-round-tracking` to the assembler invocation above; repeat it alongside any other selected `--with` addon. When UAT round tracking is negative (`no` or `off`) or was never asked, omit `--with uat-round-tracking`; do not pass it, do not add it by default, and do not infer it from any other answer.

On a nonzero assembler exit, STOP. Surface the typed exit code and stderr **verbatim** to the user. Never hand-author the plan, copy a legacy template, retry through a different assembly procedure, or continue to the plan file as a fallback.

### Receipt gate and assembly provenance (BLOCKING)

Immediately after a successful assembler exit, verify the newest NDJSON record in:

```text
.jswarm/plans/<KEY>/<KEY>.assembly-receipts.ndjson
```

The record must exist and its output path and output SHA-256 must match the plan that was just written at `.jswarm/plans/<KEY>.plan.<slug>.md`. **MISSING RECEIPT = HARD STOP.** Do not proceed with the plan file, plan status, follow-on files, or Jira summary until the receipt exists and matches. A receipt mismatch is also a hard stop; surface it rather than repairing the plan by hand.

The born plan frontmatter is assembler-owned provenance. Expect:

```yaml
created_from_template: CORE@<v>
assembled_patterns: ["<id>@<v>", ...]
```

Do not remove, replace, or hand-author either provenance field. `assembled_patterns` records the resolved bundle patterns plus any valid `--with` addons. The legacy generated templates remain available for LEGACY tickets and readers at `docs/plans/plan-templates/`; they are generated artifacts, not an alternative plan-birth procedure.

### Plan must follow the spec

For Standard/Deep plans: the plan file MUST be informed by the technical design spec:
- Implementation phases follow the spec's proposed technical approach and design decisions
- Error handling strategy reflects the spec's edge case handling and reliability approach
- Tasks respect technical constraints and integration points from the spec
- Observability and security requirements from the spec appear in phase tasks
- If Oracle flagged architectural concerns or design alternatives, the plan reflects them

### Catalog Pattern selection (lean, optional, distinct from Q6 execution-team Pattern)

Before writing the plan body, decide whether 1-3 **catalog Pattern** records apply. Use Pattern IDs such as `PAT-001`; do not paste full Pattern bodies, external-source prose, or pattern literature into the plan. Keep this separate from the Q6 **execution-team Pattern 1/2** answer.

Populate the assembled plan's **Catalog Pattern Selection** section with:

1. **Selected catalog Pattern IDs:** 1-3 IDs, or `N/A ([why no recurring engineering Pattern applies])`.
2. **Rationale:** one short reason per selected Pattern.
3. **Alternatives / rejected Patterns:** IDs considered and why rejected; `N/A` if none.
4. **Constraint surfaces:** plan fields, required/forbidden agent routing, required prompt clauses, anti-pattern/tradeoff warnings, evidence tests, and reviewer enforcement markers to materialize selected Pattern obligations (AC-11).
5. **A/C-to-UAT/test/evidence map:** every acceptance criterion maps to a UAT scenario, automated test, smoke/CLI/schema proof, review artifact, or explicit N/A rationale (AC-12). Non-UI/non-E2E work still needs lower-level evidence maps plus explicit `UAT N/A (no UI/E2E impact because ...)` rationale.
6. **Thin-slice proof point / stop condition:** the smallest acceptance-relevant proof to run before broad coding; stop if the proof cannot run, the evidence map is missing, or the proof no longer matches intended behavior (AC-13).
7. **Per-job / per-Pattern NFR weighting:** reliability, compliance/security, transparency, maintainability, efficiency/readability, and any job-specific NFRs with the design/test/review consequence of dominant weights (AC-14).

Reviewer/implementation enforcement: critic, verifier, and jTestEngineer checks must reject prose-only Pattern compliance, missing A/C-to-UAT/test/evidence rows, broad implementation before the thin-slice proof, or choices that contradict dominant NFR weights.

### UI-touching stories (all plan types except `rapid-vibe-ui`)

`rapid-vibe-ui` intentionally replaces upfront UX A/C and mockups with reported-as-found living GWT contracts; follow its pattern file instead. For every other plan, if the story touches the UI in any way (frontend components, pages, navigation, user-facing feedback), the agent MUST:
1. Populate the plan's **User Experience** section (entry, journey, error states, exit, ASCII mockups, UX A/C)
2. Include at least one UX A/C in the format: "User can [verb] [object] and sees [feedback]"
3. Include ASCII mockups of each distinct screen state

Stories without UX A/C produce technically correct but user-hostile interfaces (a retro finding).

### Story plans under a Feature parent

If this Story belongs to a Feature, the agent MUST:
1. Read the parent Feature plan's `§ PE2E Test Contracts` section
2. Populate the story plan's `Feature PE2E Contributions` section
3. Map each story A/C that contributes to a PE2E journey step explicitly

If the story contributes to no PE2E steps, document why and delete the section.

### Parent Feature plan reconciliation (MANDATORY at end of /jPlan, before commit)

When the parent Feature plan uses the Feature Governance Rings methodology with a Mermaid dependency flowchart (per `TICKET-XXX.devops.feature-gov-rings.md` §5.5):

1. Locate the Story's node in the parent's `§ Sequencing View: Companion dependency flowchart` Mermaid block
2. Add `📋 ` prefix to the label: `S08["📋 TICKET-XXX<br/>OpenAPI Typed UI Client"]`
3. In the `class <node-id> ...` directives at the bottom, move the node ID from the `unplanned` list to the `planned` list. **Mermaid only allows one `:::class` inline; planning state is layered via `class` directive, never chained inline.**
4. Reconcile §6a Active table row: bump Ring column `0 → 2`; update Notes with `/jPlan Ring 2 complete YYYY-MM-DD`
5. Reconcile § User Stories row: status flip + effort update if applicable

Failure to update the flowchart leaves the parent's only Ring-2 visual scoreboard stale. PERMANENT memories: `feedback_master_plan_flowchart_freshness.md` (flowchart-specific), `feedback_post_newwork_parent_plan_reconciliation.md` (broader reconciliation discipline).

### Required plan header lines (Standard/Deep)

```markdown
**Technical Design Spec:** [TICKET-XXX.specs.<descriptive>.md](TICKET-XXX.specs.<descriptive>.md)
**Recommended agent team:** Pattern <1|2> · review:<critic|critic-xhigh> · arch:<none|architect|architect-master> · escalation-trigger:<verbatim trigger or "none">
**Orchestrator model & effort:** [FABL claude-fable-5 | OPUS claude-opus-4-8] · [HIGH | XHIGH throughout | HIGH with phase escalations, list them] (routes the ORCHESTRATOR session only; named j-cores stay route-pinned)
**Catalog Pattern selection:** [1-3 catalog Pattern IDs such as `PAT-001`, or `N/A (no recurring engineering Pattern selected)`; keep separate from execution-team Pattern 1/2]
**Testing strategy:** unit [required/upgrade/N/A]; integration [required/upgrade/N/A]; UAT [live-show-headed/headless-automation/diagnostic-cdp/no]; regression E2E [per-ticket/deferred/N/A]; smoke [impact yes/no]
**Test data strategy:** managed cluster required for regression | managed cluster recommended for scripted UAT | exploratory-ad-hoc allowed for live UAT | N/A ([cluster IDs or setup summary])
**Automated UAT:** yes | no (yes only for UI/E2E/user-journey impact)
**E2E policy:** per-ticket | deferred
**Per-Phase UAT Gate:** Required only for phases with UI/E2E impact when Automated UAT: yes
```

Quick (depth 1) plans: include **Recommended agent team**, **Automated UAT**, **E2E policy**, **Per-Phase UAT Gate** lines. Omit the spec line unless a spec was created anyway.

### When `Automated UAT: yes` and the ticket changes user-visible behavior

For `rapid-vibe-ui`, use the pattern-owned living-contract schema instead of the normal extracted-scenario template and do not create a duplicate executable UAT doc. For all other plans, write `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenarios.md` from `docs/templates/UAT_SCENARIO_EXTRACT_TEMPLATE.md`. Populate with:
- Path to the official high-level UAT inventory (if found)
- Only the ticket-relevant extracted scenarios
- Which scenarios are **Modified / Extended / New**
- Ticket-specific proposed wording / expectations that may change during implementation
- Questions to validate before merge-back at `/jClose`

Record in plan's **Testing Strategy** / **Automated UAT Plan** sections:
- **UAT execution mode:** `live-show-headed`, `headless-automation`, or `diagnostic-cdp`
- **Browser driver:** Playwright MCP / headed Playwright for live-show; headless project wrapper for headless-automation; Chrome DevTools MCP only with diagnostic reason for diagnostic-cdp
- **Overlay / callouts:** enabled by default for live-show-headed; disabled only with a reason
- **Runtime monitor:** required for live-show UAT touching backend, API, workflow, streaming, persistence, or dispatch behavior; include command/task, output path, filters, review cadence, or `N/A ([reason])`
- **Test data strategy:** managed cluster, inline fresh setup, exploratory-ad-hoc, or N/A
- **Regression promotion:** per-ticket headless Playwright artifact, feature-level deferred, or N/A with reason
- **Architecture scenario merge-back:** target architecture scenario inventory path, or note that no official inventory exists yet

### Feature plans only: create defect tracker files

When issue type is Feature, create these two files alongside the plan file:
1. Copy `docs/templates/INTEGR_FIXES_TEMPLATE.md` → `.jswarm/plans/TICKET-{NUMBER}/TICKET-{NUMBER}.integr-fixes.md`
2. Copy `docs/templates/PE2E_FIXES_TEMPLATE.md` → `.jswarm/plans/TICKET-{NUMBER}/TICKET-{NUMBER}.pe2e-fixes.md`

Substitute `TICKET-XXX` with actual ticket number and `TICKET-XXX-DESCRIPTION.md` with actual plan filename. These accumulate defects across all phases during `/jGo`. Do NOT create them for Story/Task/Bug plans.

## Current modular additions

### Components and features frontmatter

The assembled plan seeds `components: []` and `features: []`. Fill them in:
- **`components:`**: the logical-component id(s) this ticket contributes to (records in `docs/_JarviSWARM/components/{id}.component.yaml`). Reference each declared id in the plan body (Scope/AC). Leave `[]` (explicit, never omit) if the ticket touches no durable component. Validation is **WARN-only + fail-open**; an unknown id never blocks `/jPlan` and is **never auto-created**.
- **`features:`**: the marketable-feature id(s) (F-MKT/MTH/TOOL/INF-N) this ticket advances; `[]` allowed.
- **Deliberate new-component registration (only when this ticket genuinely introduces a new logical component):** create the record explicitly: `${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -c "import sys; sys.path.insert(0,'jswarm/catalog'); import component_validator as cv; cv.register_component('<id>', '<F-OWNER>', layer='<layer>', lenses=['<lens>'])"` (or hand-author `docs/_JarviSWARM/components/<id>.component.yaml` per `jswarm/catalog/schema/component.schema.yaml`) with a real `owning_feature`, then populate its `artifacts[]`. Never rely on an unknown id auto-registering; it does not.

### Dashboard-delivering stories (render tier)

**STOP: rule-bearing module.** If the story *delivers* a dashboard (builds/publishes a dashboard UI from a data object, project, security, compliance, feature, or cross-project aggregator, as opposed to merely projecting metrics into an existing dashboard's data substrate, which is the separate "Feature-child Story Dashboard projections" module), read `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/jplan/dashboard-render-tier.md` in full and apply every rule (toolkit, JDS type→render-profile, deployment target, publication-safety gates, the plan's `Dashboard render tier:` line) before proceeding. Do not work from memory.

### Outcome Metrics (Standard/Deep/Quick; skipped in Lite)

Populate the plan's `## Outcome Metrics` section as part of Step 5:

1. **Feature plans:** required. Propose outcome/NFR rows with measure, baseline, projected delta, executable recipe, and what falsifies "improved." If the Feature is refactor, runway, or multi-phase high-risk, instantiate or link the Feature Scoreboard.
2. **Story/Task/Bug plans, including Quick:** optional but explicit. Propose 1-3 candidate metrics when useful; developer keeps, edits, or declines. If declined, write `Outcome metrics: declined (<reason>)`. Never omit the section silently.

### Necessity Gate (only where the ticket builds a durable production surface)

Author a `## Necessity Gate` table in the plan **only when at least one acceptance criterion adds a durable production surface**: something that persists, schedules, sweeps, or mints, **or** any new function, CLI, validator, hook, or schema meant for production use. Docs-only, greenfield, and deterministic-local-change tickets author **no section, record nothing, and pay nothing**; absence is the answer, and there is no flag to set.

Where it does apply, one row per acceptance criterion, eight cells: `A/C · outcome_removed · runtime_basis · production_writer · production_reader · reaching_path · adversary / harm · verdict`. Conditional cells take `n/a (<reason>)`; a bare `n/a` is rejected, because the reason is the auditable part. The verdict is `PROCEED`, `MINOR RESCOPE`, or `RETHINK PREMISE`.

Three things are worth knowing before you write it, because they are what the gate is for:

- **`production_reader` is the load-bearing cell.** It names the caller of the new surface, and it is checked mechanically: an unresolved reader **blocks**. A reader that does not exist yet is not a plan; it is the failure this gate exists to catch. One recorded ticket shipped 3,461 lines of a complete, tested subsystem that nothing called.
- **`none yet` and `a future ticket` are not answers.** A deferral in the writer, reader, or reaching-path cell **defers the criterion**; drop it from this ticket rather than recording an intention to build toward it. Write the writer/reader/reaching-path cells as one of three closed choices plus a one-line justification: `<cited-file:symbol>`, `named-human-decision (<who decides, and when>)`, or `none-yet (<why>)`, because a prose cell lets an intention pass as a plan. `none-yet` is not a soft option: it auto-verdicts `RETHINK PREMISE` and blocks. A reader that is genuinely a scheduled human decision rather than code says so in those words; that is a real answer, and it is checked by a reviewer rather than by the lint.
- **`adversary / harm` is required only where assurance machinery is proposed** (authorization, signing, attestation, nonces, replay windows). Absent a named adversary the failure class is **neglect**, and the remedy is a default, a reminder, or a visible check, not a mechanism.

`RETHINK PREMISE` blocks until the plan is revised, and **an implementation review cannot satisfy or overwrite it**: a reviewer scoped to a contract enforces that contract, so it cannot be what clears a doubt about whether the contract should exist at all.

Enforcement lives in `lifecycle_audit.lint_necessity_gate`, reached by `/jPrecompact` and `/jClose` through the `new-work-lint` preset; the section is checked, not merely requested. Full rules: `.jswarm/plans/TICKET-XXX/designs/TICKET-XXX.design.ac1-necessity-gate.md`. Do not duplicate them here; this call-out exists to tell you when to author the section and what the gate is actually for.

**Authoring the section is required once the plan enters implementation.** A plan that reaches its first `3.implementation.*` status without a `## Necessity Gate` heading is blocked by the same lint. This closes the one demonstrated escape: an incident-born plan that skipped `/jPlan` never authored the section, so the gate never engaged, and that ticket over-built roughly a third of its surface before anything asked whether it was needed. Plans already in implementation before this rule shipped stay validate-if-present, and a plan that has not yet entered implementation is never asked for anything.

**One entry point for all of the above.** The right-sizing controls in this step are the necessity pass, the plan-shape conventions, and the acceptance-tier test inventory below. Use them directly when auditing a plan you did not write, rather than spreading the check across ad-hoc reading of this step.

### Plan shape: an intent clause per A/C, and naming the work you will be tempted to do

Two conventions, a sentence each, authored alongside the acceptance criteria. They apply on the same terms as the Necessity Gate above; a ticket that authors no gate authors neither of these and pays nothing.

**State the intent in one sentence, and have every A/C name the clause it serves.** Put the ticket's purpose near the top of the plan as a single sentence, then have each acceptance criterion name which clause of it that criterion serves, inline, e.g. *(serves the "finish faster" clause)*. This is not decoration. A criterion that cannot name a clause is usually a **real problem in the wrong ticket**, and plan time is the cheapest moment to move it. In one recorded case a criterion that was genuinely worth doing belonged to a different ticket entirely; three consecutive reviews kept it because it was "required by A/C 2", and it was cut the day after close.

**Make `### Out of Scope` name the *tempting* work, with follow-up pointers.** The CORE template already gives every plan this section, so it is the home; do not add a second "not building" list beside it. What it usually lacks is the part that does the work: the adjacent scope you will actually be pulled toward mid-build, each line carrying a follow-up pointer (a ticket key, or `unticketed (<reason>)`). "Not included" is a boundary; "tempting, and here is where it goes instead" is a decision you can hold yourself to. Writing the temptation down is what makes a later drift toward it visible instead of natural. Two rules follow:

- Mid-build, work that matches no A/C, NFR, or UAT scenario gets checked against this list before it is done. Work with no home raises the stop question, *should I be doing this?*, and the honest answer is sometimes **yes**, when ground facts discovered after planning demand it. What is never acceptable is answering it silently.
- **Adding an acceptance criterion mid-build is a check-in event, not a quiet plan edit.** A new A/C is the largest durable category a ticket can gain; adding one by editing the plan turns scope growth into paperwork, leaving the plan describing whatever got built. The check-in contract names which trigger this fires and what the review must return, per your host's check-in contract if one is configured.

### Freeze the acceptance-tier test inventory, per A/C

Name, at plan time, the **few real end-to-end pairs that will constitute completion evidence** for each acceptance criterion: a healthy case and a counterexample, exercising the thing the way production exercises it. Spec them here, or build them through the existing pack-brief surface. The point is that the shape of "done" is decided before implementation starts, not discovered afterwards from whatever tests accumulated.

**This is a tier rule, not a numeric cap.** Nobody is counting tests, and no threshold gates anything. What the tier fixes is *which* tests are allowed to mean "complete":

- **Acceptance tier**: the frozen pairs above. Only these are completion evidence.
- **Supporting tier**: unit and seam tests, added freely during implementation, but only **beneath** a planned acceptance pair. They make debugging cheap. They never certify the criterion.

The failure this prevents is specific and recent: one ticket closed with 8,571 lines of test code and 512 green tests that certified its internal objects in detail, while both of its close-out gates failed, because nothing had tested the thing an operator would actually do. A large green suite is the most convincing possible evidence of completeness, and it is not evidence of completeness at all when its subject is the implementation's own furniture. Freezing the acceptance tier first is what keeps the suite pointed outward.

**Every test cites the A/C, UAT scenario, or NFR it evidences.** A test that cannot name one raises the same question as any other unaligned work, *should I be doing this?*, and the honest answer is sometimes yes, when implementation reveals a class of failure planning missed. The standing carve-out is that a genuinely necessary unplanned test class goes through a check-in, so it is a decision on the record rather than silent accumulation.

### Security & Compliance baseline risk capture (Standard/Deep/Quick; skipped in Lite)

**STOP: rule-bearing module.** For Standard/Deep/Quick (NOT Lite), during Step 5 after the plan file exists and before the Jira summary, read `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/jplan/security-compliance-baseline.md` in full and capture the security + compliance baseline (per-dimension applicability; `probability_before`/`impact_before` when applicable; safe `why_rationale`; `baseline_controls_context`) into the plan. This is required for lifecycle telemetry so `/jClose` has a before-state; the telemetry writer is fail-open. Do not work from memory.

### Feature-child Story Dashboard projections

**STOP: rule-bearing module.** If the Story belongs to a parent Feature that has a dashboard data substrate (`jswarm/feature-dashboard-system/`; data object at `docs/plans/${PARENT}.plan-data.json` / `${PARENT}.feature-dashboard.json` / legacy `.refactor-scoreboard.json`), read `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/jplan/feature-child-projections.md` in full and apply every rule before plan completion: Section A `projected_only` cells, the held-vs-same enum distinction (load-bearing), planned Section B/C/D rows, the validate-`--check`-FIRST-then-render gate, the dual-render projection write rules, and the `Dashboard projection:` plan line. If the parent Feature has no dashboard data object, record `Dashboard projection: N/A (parent Feature has no dashboard)` in the Story plan. Do not work from memory.

### Seed `plan_status` (MANDATORY: lifecycle event + metrics freshness)

Immediately after the master plan is assembled and the receipt gate has passed, record the **initial** `plan_status` through the canonical CLI so the plan frontmatter carries a fresh `plan_status_last_updated` stamp from birth:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python jswarm/plan_status/cli.py record TICKET-XXX <SEED-STATE> \
  --actor /jPlan --proof-source new-work-plan-created \
  --plan-file .jswarm/plans/TICKET-XXX.plan.<descriptive>.md
```

`<SEED-STATE>` = `2.planning.detailed` for a full plan, `0.planning.lite_init` for `--lite`. This stamps `plan_status_last_updated` / `plan_status_actor` (DERIVED; never hand-edit) and records the creation event.

### Ceremony decision-state lint gate (BLOCKING: AC-A)

For full-mode Story/Task/Bug plans that ran the ceremony selector, the persisted `## Ceremony Decision State` must pass the deterministic decision-state lint before `/jPlan` completes. `rapid-vibe-ui` bypasses the selector and therefore this lint. Run it fail-closed on every selector-produced assembled plan:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/patterns/decision_state_lint.py .jswarm/plans/TICKET-XXX.plan.<descriptive>.md
```

Both the interpreter and the script resolve by absolute common-repository path (the script does NOT live in downstream projects); only the PLAN path is target-project-relative. The command propagates failure (exit 1). `decision_state_lint.py` recomputes the High bar and the Medium-default engine baseline from `selector_signals` (it never trusts the authored `hard_high_triggers`/`engine_recommended_tier`), and requires both `engine_recommended_tier` and `jarvi_recommended_tier`, a non-empty `situational_rationale`, `owner_approved_high: true` whenever High is selected, and a `downgrade_rationale` whenever the selected tier is below a fired High bar, so a persisted decision state can neither introduce nor waive those gates. Plans with no `## Ceremony Decision State` section (lite / feature / legacy) fail open (exit 0). Fix the decision state before completing `/jPlan`; do not use `|| true` or prose substitutes.

### NFR catalog and canonical-format authoring gate (BLOCKING: R2)

When the project is NFR-adopted and the ticket declares `**NFR catalog:** applicable`, read `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/jplan/nfr-chain.md` in full and create the required NFR working slice, machine sidecar, and, when Automated NFR is yes, a derived test document. After UAT/NFR authoring, run:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_ticket/cli.py --ticket TICKET-XXX --repo-root . --preset new-work-lint
```

The command propagates failure. Fix canonical index/matrix shape, width, seedability, and every declared-applicable missing dimension before completing `/jPlan`; do not use `|| true` or prose substitutes. A fresh ticket may have `0/0` rows, but declared-applicable dimensions need their canonical starter index/matrix.

### Conditional seventh output: initialize UAT round tracking

Run this seventh `/jPlan` output only after the matching assembly receipt is verified, the final plan is complete, and all applicable UAT/NFR sidecars have passed their authoring gates. Persist `.jswarm/plans/<TICKET>/jcheckin-context.json` from the final plan bytes, then invoke `${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/joptimize/checkin_runtime.py --lifecycle-boundary --caller jPlan --project-root "$PWD" --context-file "$CHECKIN_CONTEXT" --boundary-id "$BOUNDARY_ID" --format json`; record/warn/continue. The command writes directly to canonical `common/logs.jCheckin/`, ensures this checkout's enrollment, appends the boundary event, and repairs the project read-only views. If common is unavailable, the typed event-loss result is recorded or warned and the plan workflow continues without a local spool. Then, when `UAT round tracking: on` (or `yes`), run from the consumer repository root:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/uat_round_materialize.py create \
  --ticket TICKET-XXX \
  --plans-root .jswarm/plans \
  --rules ${JSWARM_HOME:-$HOME/dev/jswarm}/skills/jTest/UAT_RULES.json \
  --patterns ${JSWARM_HOME:-$HOME/dev/jswarm}/skills/jTest/UAT_ROUND_PATTERNS.json \
  --pattern default
```

A successful create produces the default round, pattern state, and create receipt with lifecycle state `INITIALIZED_NOT_READY`. This is plan-birth initialization only: `INITIALIZED_NOT_READY` must not claim ready, `PREPARED`, deployed, or current deployment currency; the separate preparation flow owns those transitions. Because bare `/uat-round` excludes this state from active-focus fallback, a newly born initialized-only ticket cannot displace ready work.

When `UAT round tracking: off`/`no` or unanswered, omit the seventh output and write nothing: no round, state, or receipt artifact. If initialization or materialization fails, preserve the already-born plan and sidecars, report the seventh output as **INCOMPLETE** with the command's exit and stderr, and leave no ready artifact; do not roll back completed outputs 1–6 or claim `/jPlan` completion.

<!-- v1→v2 conservation note:
| v1 section | disposition | v2 treatment |
| --- | --- | --- |
| Step 5: Write plan file (title and duplicate heading) | REWRITTEN | Renamed both headings to “Assemble plan file” to name the assembler-only plan-birth procedure; their Step 5 operator-facing role remains unchanged. |
| Pre-plan gate (Standard + Deep only, BLOCKING) | KEPT | Retained verbatim. |
| Template selection | REWRITTEN | Replaced with manifest truth-table bundle resolution and assembler-only plan birth; selecting/copying a template is prohibited. |
| Template provenance transform (MANDATORY after copy) | REWRITTEN | Replaced by assembler-owned `CORE@<v>` plus `assembled_patterns` provenance and receipt validation. |
| Plan must follow the spec | KEPT | Retained verbatim. |
| Catalog Pattern selection | KEPT | Retained verbatim except “selected template” now refers to the assembled plan. |
| UI-touching stories (all plan types) | KEPT | Retained verbatim. |
| Story plans under a Feature parent | KEPT | Retained verbatim. |
| Parent Feature plan reconciliation (MANDATORY at end of /jPlan, before commit) | KEPT | Retained verbatim. |
| Required plan header lines (Standard/Deep) | KEPT | Retained verbatim. |
| When `Automated UAT: yes` and the ticket changes user-visible behavior | KEPT | Retained verbatim. |
| Feature plans only: create defect tracker files | KEPT | Retained verbatim. |
| Current modular additions | KEPT | Retained as the parent heading. |
| Components and features frontmatter | REWRITTEN | Kept all obligations; changed template-seed wording to assembler-seed wording. |
| Dashboard-delivering stories (render tier) | KEPT | Retained verbatim. |
| Outcome Metrics (Standard/Deep/Quick; skipped in Lite) | REWRITTEN | Kept all obligations; changed selected-template wording to assembled-plan wording. |
| Security & Compliance baseline risk capture (Standard/Deep/Quick; skipped in Lite) | KEPT | Retained verbatim. |
| Feature-child Story Dashboard projections | KEPT | Retained verbatim. |
| Seed `plan_status` (MANDATORY, lifecycle event + metrics freshness) | REWRITTEN | Kept command and state rules; receipt gate now precedes status stamping, and removed obsolete manual provenance-transform wording. |
| NFR catalog and canonical-format authoring gate (BLOCKING, R2) | KEPT | Retained verbatim. |
| `status: ACTIVE` template-default note | DROPPED (obsolete) | Plan birth no longer selects or copies legacy templates; lifecycle state remains assembler-produced and `plan_status`-derived. |
-->
