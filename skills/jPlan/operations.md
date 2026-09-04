
# Operations

## Step 0a — Main-sync preflight (SILENT unless escalation needed)

Run main-sync portion of [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/preflight.md`](../../docs/merge/preflight.md). Auto-resolve gitignored chaff + ahead-only push + behind-only pull silently. Escalate ONLY for tracked-file dirt or diverged branch state. Emit one-line success log before Step 1.

## Invocation, output requirements, and operational reminders

Invoke as **`/jPlan`**, **`/jPlan --lite`**, **`/jPlan rapid-vibe-ui`**, or with the user stating Lite intent (**"lite"** / **"briefing only"** / **"ticket + context only"**) or explicit rapid existing-UI intent (**"rapid-vibe-ui"**).

> **Point-of-impact reminders (COM-133 — migrated from auto-memory):**
> - **Work item → plan (Step 2A):** after creating ANY work item — even a side-task filed mid-work — create at least a `/jPlan --lite` plan at `.jswarm/plans/ID.plan.<slug>.md`. For a side-task inside another item's active session, do NOT rename the session away from the parent item.

| # | Output | Required When | Tool/Method | DONE? |
|---|---|---|---|---|
| 1 | **Work item identified and local state written** | Always | Step 2A identity resolution + `.jswarm/work/<id>/` | ? |
| 2 | **Session renamed** | Always | See Step 2B | ? |
| 3 | **Technical design spec written** | Standard + Deep (skipped in Lite) | Write to `.jswarm/plans/TICKET-XXX/TICKET-XXX.specs.<descriptive>.md` (per canonical doc `agent-write-permissions.md`) | ? |
| 4 | **Plan file written** | Always | Write master to `.jswarm/plans/TICKET-XXX.plan.<descriptive>.md` (frontmatter `status: ACTIVE` per state machine; per-ticket artifact subfolder `.jswarm/plans/TICKET-XXX/` auto-created on first artifact write) | ? |
| 5 | **Ticket-local UAT scenario extract written** | When `Automated UAT: yes` and ticket changes user-visible behavior (never in Lite) | Write `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenarios.md` from `UAT_SCENARIO_EXTRACT_TEMPLATE.md` | ? |
| 6 | **Executable UAT doc written** | When `Automated UAT: yes` and ticket changes user-visible behavior (never in Lite) | Write `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-test.md` from `UAT_TEST_TEMPLATE.md` | ? |

**Lite** (`--lite` / "briefing only"): Outputs **1, 2, 4** only. Plan file is a **briefing** — problem/opportunity, rich context, user-provided examples, scope, acceptance criteria. **Do not** "solve" the work in that document.

**Quick (depth 1):** Outputs 1, 2, 4 required. No spec.
**Standard (depth 2):** Outputs 1, 2, 3, 4 required. jOracle review recommended.
**Deep (depth 3):** Outputs 1, 2, 3, 4 required. jOracle review mandatory.
**Automated UAT tickets:** Outputs 5 + 6 also required when ticket changes user-visible behavior and Lite is not used.
**Rapid vibe UI:** Outputs 1, 2, 4, and 5. Output 5 is the authoritative living contracts surface; omit outputs 3 and 6 because speculative design and a duplicate executable UAT document create stale ceremony for this delivery shape.

**If you finish without all required outputs for the chosen mode, you have FAILED.** For `rapid-vibe-ui`, output 5 is intentionally living and begins with no speculative defect rows; its required birth state is the file plus its schema, reporting contract, and final-walkthrough placeholder. Do not ask user about next steps until required outputs are done.

**Procedural detail owners:** `step-3-context.md`, `step-4-spec.md`, and `step-5-assemble-plan.md`. This module owns the user-facing full-mode flow (questions + dispatch + summary); those owner files contain the executable procedural detail.

> **Advisory — Background Tasks & Hang Prevention:** Run delegated tasks/agents as background (`run_in_background=true`) with task IDs. Don't poll every turn. Use completion notifications. For long-running work, use externally monitorable execution: explicit timeouts, redirected log files, heartbeats/progress, process status.

## Step 1 — Full-mode questions and ceremony selection

**If Lite mode applies, use Lite questions above instead.**

**Defaults pre-fill from project parameters (COM-128 WS4).** If the localization pass (preamble step 6) resolved a `parameters:` block for this project, present its values as the **pre-selected defaults** for Q5–Q9 below (depth, execution team, review tier, arch tier, automated-UAT, E2E policy, test-data strategy) — e.g. "Planning depth? [default: standard]". The developer overrides any answer freely. When no `parameters:` block resolved, use the global defaults shown on each question.

**Q6 is rubric-governed, not pre-fill-governed.** The resolved `default_review_tier` / `default_arch_tier` are only a *lean starting point*. The agent-team rubric (Q6 below) still runs and still governs any escalation from a named trigger. A project may NOT pre-default review to `critic-xhigh` or arch to `architect-master` — those tiers are trigger-gated, and the renderer rejects them fail-loud, so a resolved default can only ever be a lean tier the rubric escalates *from*.

Ask these together in a single message:

1. What feature/fix are you working on?
2. New ticket or existing? `[new / TICKET-XXX]`
3. If new: Issue type? `[Task / Story / Bug / Subtask / Feature]`
   - **Feature** = multi-story epic-child with own user stories. Uses `PLAN_FEATURE_TEMPLATE.md`.
   - **Feature requires Standard (2) or Deep (3) depth.** Quick (1) is not allowed for Features.
4. Scope and acceptance criteria? Author FEW HIGH-LEVEL A/C that each summarize a cluster of UAT scenarios/NFRs (1 A/C : N, never 1:1), with perspective flexibility, per `docs/agent-system/ac-uat-nfr-traceability.design.md`.

<!-- jPlan.ceremony-selector:hml -->
### H/M/L ceremony selector (Story/Task/Bug full mode only)

Applies only to Story/Task/Bug in full mode. Lite mode bypasses this selector, and Feature tickets bypass this selector because Features use the feature template and Standard/Deep planning rules above.

Terminology clarifier (NFR-022): a catalog Pattern ceremony preset (H/M/L) and the Q6 execution-team Pattern 1/2 are different concepts, not aliases.

**Owner-directed `rapid-vibe-ui` bypass:** before invoking H/M/L, check whether the owner explicitly selected rapid existing-UI refinement. It applies only when the ticket refines an existing interface, the concrete item list is expected to emerge through immediate browser feedback, and the owner is available to accept/reject each item. It does NOT apply to a genuine feature, new architecture, backend/workflow behavior, migration, shared contract, security/compliance surface, or UI work whose requirements can be planned up front. A valid selection bypasses H/M/L, compiles Quick, and adds `--with rapid-vibe-ui`; record `Ceremony tier: Low` and `Effective pattern: rapid-vibe-ui@1 (owner-approved bypass)`. Read and apply `pattern.rapid-vibe-ui.md` in full.

After Q1–Q4, invoke the `jPlan.ceremony-selector` skill when the rapid-vibe-ui bypass does not apply. The skill owns selector input drafting, deterministic compilation via the runner, H/M/L card rendering, Jarvi recommendation rendering, and the tier-first question harness. Do not inline script mechanics here; route the conversation through the skill and show its rendered output verbatim.

Picking a tier compiles Q5–Q9 plus worktree, NFR, precompact cadence, and orchestrator-context settings from the chosen preset. The compiled levers are the answers: Q5–Q9 are not re-asked as independent questions unless the compiler leaves a required detail unresolved after the preset compile.

Persist the validated decision state returned by the `jPlan.ceremony-selector` skill's `apply` boundary to the plan: `selected_ceremony_tier`, `engine_recommended_tier` (the deterministic Medium-default baseline), `jarvi_recommended_tier` (Jarvi's authored situational recommendation), `situational_rationale`, `selector_signals`, `hard_high_triggers`, `owner_approved_high` when High is selected, and, when a downgrade below a fired High bar is accepted, `downgrade_rationale`. Do not persist a prose-only recording — persist the JSON the `apply` boundary emits. Medium is the default pick. High requires explicit owner approval (`--owner-approved-high`).

Scope-drift re-evaluation: when scope or A/C changes mid-conversation, have the `jPlan.ceremony-selector` skill re-eval the selector inputs through the runner. If `reprompt` is true because the recommended tier shifted or a hard-High trigger newly fired, re-present the updated cards and include the one-line `why_changed` explanation. If `reprompt` is false, do not nag the user; keep the prior tier because it still fits.

High-bar and owner-approval gates: the engine recommends High only when two or more hard-High signals are high, or a solo security/migration signal is high. A single `user_visible_behavior`, `shared_contract_surface`, `scope_blast_radius`, or `novelty_architecture_uncertainty` high stays Medium. Selecting High always requires explicit owner approval. Downgrade-rationale gate (NFR-023): when that High bar has fired, choosing below High still requires a recorded risk rationale. The Q6 rubric stays authoritative: the compiler supplies trigger evidence only, and never auto-routes `jCritic` at xhigh effort or `jArchitect` at xhigh effort.

Jarvi callout behavior: Jarvi callout leads explanation turns, including the recommendation intro, scope-drift re-eval, and `explain <tier>` responses. In non-TTY contexts the callout degrades to a plain `Jarvi:` prefix with no box drawing.

Legacy-safe: plans with no ceremony selection / no-selection recorded remain valid and may continue through the existing Q5–Q9 fields.

5. Planning depth? `[1=Quick / 2=Standard / 3=Deep]`
6. **Ticket execution agent team + review/architecture tiers** (for `/jGo` and `/jFix`). Reply with Pattern `1` or `2`. The review tier and architecture tier are decided by **rubric, not by gut feel** — this is what stops overuse of `jCritic` at xhigh effort / `jArchitect` at xhigh effort.

   **STOP. Before answering Q6, read `docs/jplan/agent-team-rubric.md` in full and apply its rubric to determine the review tier and the architecture tier.** Use the `agent-team-advisor` skill with `lifecycle_stage: plan` to select dynamic topology and staffing: read the agent-team catalog index first and use its fallback on failure; never block.
   - **Step 5 writes the inherited plan-header line** (consumed verbatim by `/jGo` and `/jFix`):
     `**Recommended agent team:** Pattern <1|2> · review:<critic|critic-xhigh> · arch:<none|architect|architect-master> · escalation-trigger:<verbatim trigger or "none">`
   - **Immediately after the `Recommended agent team` line, the planner emits:**
     `**Agent-team catalog selection:** ATP-NNN@V`
   - **Keep the frozen-header capture and record** any override/pin and escalation above the default (`jCritic` at xhigh effort / `jArchitect` / `jArchitect` at xhigh effort) in the plan; log the escalation to `docs/jplan/agent-escalation-log.md` with its trigger + `outcome / warranted`.

7. **Automated UAT needed?** `[yes / no]`
   - **yes** — only when ticket has UI/E2E/user-journey impact. After implementation + lower-level tests pass, `jQATester` executes `TICKET-XXX.uat-test.md` against the running app in the declared mode. Standard live-show driver: Playwright MCP headed/foreground or headed Playwright. Headless automation never claimed as developer-watched Live Show UAT. Chrome DevTools MCP only for documented Chrome/CDP diagnostic exception. Catches bugs unit tests miss: event propagation, CSS rendering, data flow, stale caches, confusing user journeys.
   - **no** — required for backend-only/database-only/migration-only/infrastructure-only/internal-refactor/tooling-only tickets with no UI/E2E impact. Prove with unit, integration, migration/schema, API/contract, smoke, or targeted CLI tests.
   - Default `yes` only when UI/E2E/user-journey impact is clear. Otherwise default `no` with `Automated UAT: no — no UI/E2E impact`.
   - When **yes**: plan includes UAT phase/gate; UAT doc declares execution mode, browser driver, visible-browser expectation, overlay/callout mode, runtime monitor command/task (or N/A reason), report path, evidence path before `/jGo` delegates.
   - **UAT document chain:** official high-level UAT inventory → `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenarios.md` (extracted working slice) → `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-test.md` (derived from extracted).
   - **UAT templates:** `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/templates/UAT_SCENARIO_EXTRACT_TEMPLATE.md`, `UAT_TEST_TEMPLATE.md`, `UAT_REPORT_TEMPLATE.md`, `UAT_TEST_EXAMPLE.md`.

8. **E2E test policy?** `[per-ticket / deferred (Recommended)]`
   - **per-ticket** — stable live-show flows promoted to durable headless Playwright regression artifacts near the **end of the ticket**, not as per-phase gate.
   - **deferred** (default) — regression-mode E2E moves to feature-level verification after related tickets complete. Unit + integration + live-show UAT provide per-ticket coverage.
   - Default: **deferred**.

9. **Test data strategy?** `[managed cluster / inline fresh setup / exploratory-ad-hoc / N/A]`
   - **managed cluster** — named cluster with manifest, seed/preflight/reset commands, invariants. Required for regression-mode E2E/PE2E unless test creates equivalent deterministic state itself.
   - **inline fresh setup** — UAT/regression test creates all state in setup steps.
   - **exploratory-ad-hoc** — Live Show UAT only. Cannot be promoted to regression until converted to deterministic setup or managed cluster.
   - **N/A** — No stateful UAT/regression data needed.

10. **Enable PM/PO Jira rollup comments for this ticket?** `[no (default) / yes]`
   - **Default: No.** Rollup comments are OFF unless the developer opts in here — this is the single opt-in moment (asked once at `/jPlan`). Record the answer as the plan-frontmatter flag `rollup_comments: on|off`.
   - **yes** → frontmatter `rollup_comments: on`. When on, `/jPrecompact` (full) and `/jClose` render a PO-altitude **po-ticket-outcome** via `/plain-english` and post it as a comment on the story/task Jira issue; for a Feature child they also post a PM-altitude **pm-feature-rollup** on the Feature issue (refreshed at `/feature-reconcile`). The producing plan records a deep link to each comment; if Jira is unavailable a local receipt is written instead. This is for non-technical PM/PO readers — no markdown-file or dev-env step is ever required of them.
   - **no** (default) → frontmatter `rollup_comments: off` (or omit). No rollup comment is ever posted.
   - Independent of the existing `/jClose` retro (Step 3.5) and work-completed (Step 3.6) comments.

<!-- HAS-340: per-project worktree policy lives below this anchor. Projects opt in
     via .claude/project-command-injections.yaml + a snippet at
     .claude/command-injections/new-work-worktree-policy.md. -->
<!-- inject:project-worktree-policy -->

**Wait for user response before proceeding.**

## Step 2 — Work item and session

### Step 2A — Work item identity (tracker-optional)

`/jPlan <work-item>` accepts either identity form (`jswarm.workitem.identity.parse`):

- a tracker key, `^[A-Z][A-Z0-9]+-\d+$` (e.g. `PS-14`) — only meaningful when a tracker is configured;
- a slug, `^[a-z0-9][a-z0-9-]*$` (e.g. `add-csv-export`) — always available.

An argument that parses as neither is a usage error; show both accepted shapes.

**Check the tracker before anything else is created:**

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.tracker.cli is-configured --repo "$PROJECT_ROOT"
```

**The one hard stop:** if the argument looks like a tracker key (matches the tracker-key pattern above) and `is-configured` prints `{"configured": false}`, STOP:

```
This looks like a tracker key (<ARG>), but no tracker is configured for this repo.

Run `./install.sh adopt --jira-key <KEY>` from the jSwarm clone to configure one, or
re-run `/jPlan` with a slug instead (e.g. `/jPlan add-csv-export`) to plan this
work locally with no tracker.
```

Do not invent a placeholder key and do not silently fall back to a slug — ask the user to choose.

**Otherwise, write local state before touching the tracker:**

```bash
mkdir -p ".jswarm/work/<ID>"
```

Record the work item's identity, kind, and plan-file path in `.jswarm/work/<ID>/state.json` (create fresh, or update if it already exists from a prior `/jPlan` pass on this item):

```json
{"id": "<ID>", "kind": "tracker-key|slug", "plan_file": ".jswarm/plans/<ID>.plan.<slug>.md", "created_at": "<UTC ISO-8601>"}
```

**Then, only when a tracker is configured, resolve or create the tracked issue.** With a tracker key argument, resolve it:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.tracker.cli resolve <ID> --repo "$PROJECT_ROOT"
```

There is no tracker `create` verb at the boundary in v0.1.0 (only `resolve`/`comment`/`transition`) — when the user wants a brand-new tracked issue rather than an existing key or a local slug, create it directly with the connected tracker's own tool (for Jira: the connected Atlassian MCP's create-issue tool) using the project key from `${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/devops_command_injection.py jira-key --project-root <PROJECT_ROOT>`, then treat the returned key as `<ID>` for the rest of this step. With a slug argument, or with no tracker configured at all, there is nothing to create upstream — local state is the whole of it.

`resolve` returning `null` (issue not found, or `is-configured` was already false) is not a hard stop for an otherwise-valid slug flow; it only matters for a tracker-key argument, and the hard stop above already covers "no tracker at all". A tracker-key argument that a *configured* tracker cannot resolve is reported to the user as a normal not-found condition, not this skill inventing a substitute id.

> **Point-of-impact reminder (COM-133):** after establishing ANY work item — even a side-task filed mid-work — create at least a `/jPlan --lite` plan at `.jswarm/plans/ID.plan.<slug>.md`. For a side-task inside another item's active session, do NOT rename the session away from the parent item.

### Step 2B — Session rename

Title format: `TICKET-{NUMBER}-{DESCRIPTION}` — UPPERCASE, hyphens, ~40 chars max.

| Runtime | Agent-automated? | How |
|---|---|---|
| **Claude Code** | Yes (without `/rename`) | Write title as single line to `.jswarm/state/pending-session-rename`. Hooks / user submit apply `sessionTitle`. Do NOT claim you ran `/rename`. |
| **OpenCode** | Yes (via server API) | Set `OPENCODE_SERVER_URL`, then `PATCH /session/:id` with `{"title": "..."}`. See `/opencode-test-session-rename`. |
| **Any** | User fallback | User runs `/rename TICKET-XXX-DESCRIPTION` in TUI. |

> **Feature-orchestrator carve-out — SKIP 2B.** When you are the **Feature orchestrator** running `/jPlan` serially for child Stories under a Feature you own, skip this rename entirely: do **not** write `.jswarm/state/pending-session-rename` and do **not** claim a rename. `/jGo` runs in a separate chat, so per-Story renames are pointless churn that pollute the orchestrator session title — whose correct identity is the FEATURE, not the latest child Story. **Detection:** parent is a Feature AND you are running multiple consecutive `/jPlan` cycles in one chat. In the Step 6 summary, mark the Session row `n/a (Feature orchestrator)`. Applies in Lite too. If you already wrote a rename intent before realizing, don't revert — just stop renaming for subsequent cycles.


#### UAT scenario JSON READ integration
<!-- uat-scenarios:read-integration -->
When a project has adopted the COM-122 UAT-scenario engine (canonical scenarios JSON + generated `.md`), resolve scenario data from the **JSON** via `jswarm/uat-scenarios/query_uat_scenarios.py` (`--list-groupings` / `--get-scenario <id>` / `--list-scenarios [--grouping <id>]`); treat the generated `.md` as **derived/read-only** (edit JSON, regenerate). The scenarios JSON path is project-supplied (see the engine README / project localization). Projects that have NOT adopted the engine continue using the markdown UAT inventory.

## Tool Failure Reports

When MCP tools fail during `/jPlan` (Jira creation, ColGREP timeout), file a report **immediately**.

**Write to:** `docs/tool-failure-reports/TICKET-XXX.toolfail.TOOLCATEGORY.YYYYMMDD.md`
**Template:** `docs/templates/TOOL_FAILURE_REPORT_TEMPLATE.md`

File when: 3+ consecutive failures, blocking timeouts, auth errors, consistent wrong data. Continue with workarounds after filing.

## Screenshot Evidence Rule

All Playwright screenshot evidence goes in **LOCAL PLAN FILE** using rendered markdown:
```markdown
![AC-1: Feature works](./screenshots/TICKET-XXX/ac1-feature.png)
```

**NEVER embed screenshots or image links in a tracker comment.** A tracker (when configured) gets text-only comments. Plan file is source of truth for visual evidence.

## Step 6 — Sync the tracker and show summary

**Full-plan mode — tracker comment (skipped cleanly when no tracker is configured):**

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.tracker.cli comment <ID> --repo "$PROJECT_ROOT" --text "$(cat <<'EOF'
Plan: .jswarm/plans/ID.plan.<descriptive>.md
Spec: .jswarm/plans/ID/ID.specs.<descriptive>.md (if applicable)
Local UAT scenarios: .jswarm/plans/ID/ID.uat-scenarios.md (if Automated UAT)
Depth: [Quick/Standard/Deep]
jOracle: [Yes/No/N/A]
Execution team: [Pattern 1 / Pattern 2; review tier]
UAT: [jQATester enabled | N/A]
Status: ACTIVE
EOF
)"
```

Print the result's `message` once. `skipped: true` (no tracker configured) and `ok: false` (tracker reachable but the call failed) are both non-blocking here — local state was already written in Step 2A; continue to the summary either way.

**Full-plan mode summary:**
```markdown
## Work Initialized

| Output | Status |
|---|---|
| **Work item** | ID (tracker-key: [link] / slug: local only) |
| **Session** | Renamed to ID-DESCRIPTION |
| **Technical Design Spec** | .jswarm/plans/ID/ID.specs.<descriptive>.md (or N/A for Quick) |
| **Plan** | .jswarm/plans/ID.plan.<descriptive>.md (status: ACTIVE) |
| **Local UAT Scenarios** | .jswarm/plans/ID/ID.uat-scenarios.md (or N/A) |
| **jOracle Review** | Complete / Skipped / N/A |
| **Execution team** | Pattern 1 / Pattern 2 + review tier |
| **UAT verification** | jQATester enabled / N/A |
| **Tracker sync** | commented / skipped (no tracker) / failed — see message above |

**Move the tracked issue to "In Progress"?** [yes/no/N/A — no tracker] — on yes, `python -m jswarm.tracker.cli transition <ID> --repo "$PROJECT_ROOT" --state "In Progress"`.

**Next:** run `/jGo` in this project's agent session to execute the plan with TDD.
```

> **When to use `/jFix` instead of `/jPlan` + `/jGo`:** If work is a bug fix with clear symptom (error message, failing test, broken behavior) and you want investigation + fix in one pass rather than formal plan first, use `/jFix "<description>"` directly instead of `/jPlan`. Use `/jPlan` when the bug requires formal planning, multiple phases, or coordination with other work items.

## Auto-context management

Long `/jPlan` sessions (Deep planning with jOracle review, Feature decomposition, multi-repo research) consume tokens rapidly.

> **Canonical protocol:** Run `/jPrecompact TICKET-XXX` for full 4-surface checkpoint.

| Trigger | Why |
|---|---|
| After jOracle/research agent returns | Large payload absorbed; safe to compact |
| After spec is written (Step 4C complete) | Spec is durable; plan hasn't started |
| ~60 min active work | Prevents unbounded transcript |
| Before irreversible op (Jira label sprays, scope changes) | Commits "before" state |

State file: `.jswarm/plans/TICKET-XXX/TICKET-XXX.new-work-state.md` (≤200 lines: current step, completed outputs 1-6, pending decisions, agent IDs).

**Post-compact resume:** Read state file → check TaskList → read plan file (if created). Announce: *"Resuming at Step N. Next action: X. Prior checkpoint was commit <sha>."* If state file missing: `git log --oneline -10` + `ls .jswarm/plans/TICKET-XXX.plan.*.md .jswarm/plans/TICKET-XXX/` + ask user.

**Skip protocol for:** Lite mode, Quick (depth 1) <15 min, simple single-ticket with no jOracle/research, user actively driving each step.

## ColGREP Index Lifecycle Check (COM-204 — no-badgering)

During **plan setup (before dispatching implementation)**, run the read-only ColGREP lifecycle check. It silently lets the certain-only evictor handle stale/orphan indices and surfaces ONE consolidated question only for genuinely ambiguous candidates — and only once per unchanged set (receipt-backed; no badgering). ColGREP is optional; an uninstalled or erroring check must never block planning:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/colgrep_index_lifecycle.py \
  check --command jPlan --json || echo '{"note": "ColGREP unavailable, continuing"}'
```

- `question` null or `suppressed: true`, or the command itself failed to run → proceed silently; no action needed.
- `question` present → surface its `prompt` + each `candidate` (with its `reasons`) using the actions **keep-protect / delete-now / defer / inspect-details**. Advisory only — never block planning, and never auto-`--apply` cleanup from a lifecycle command (deletion stays operator-gated; dry-run is the default).

---
