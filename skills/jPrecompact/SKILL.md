---
name: jPrecompact
description: Symlink to the global /jPrecompact command that runs the 4-surface checkpoint protocol before context compaction and persists durable retro lessons.
---

# /jPrecompact: Execute Pre-Compaction Protocol

## Safety contract (destructive skill, agent-invocable)

- **Default is read-only / no-write.** Invoked with no args (or `--help`/`status`), this skill only inspects and reports; it performs NO write, apply, push, delete, remote, or trim.
- **Confirm before any mutation.** Before any state-changing mode, the invoker (human or agent) must obtain explicit confirmation, or run an approved preview/dry-run first and act only on that approved plan.
- **Agents are NOT locked out** (no `disable-model-invocation`); this contract (not frontmatter) is what gates writes, so the read-only path is freely usable and a mutation requires approval.


Execute the full checkpoint protocol before context compaction. Full mode first runs a mandatory promotion-review gate so the developer can approve, deny, or request changes for plan items that are ready to move forward; only after that decision is reconciled may the command write checkpoint surfaces. **Also** persist durable lessons into the **same canonical retros** as `/jClose` **Step 2 — Write the retro** (see `skills/jClose/SKILL.md`), then include those edits in the checkpoint commit.

Lite mode is intentionally narrow: it records session-continuity state, a scoped plan Status Updates row, and retros for this session's activity. When its jStatus currency gate fires, it runs `/jStatus --lite`, never a full `/jStatus`. It does not run promotion review, matrix reconcile, a full checkpoint commit, TaskList, broad plan maintenance, or artifact dispatch.

```
/jPrecompact                    # Auto-detect from current ticket signal or generic state
/jPrecompact TICKET-XXX          # Specific ticket
/jPrecompact --lite             # Minimal continuity checkpoint: state file + scoped plan Status Updates + retros; stale status via /jStatus --lite
/jPrecompact --fast             # Alias for --lite
```

> **Source protocol:** Extracted from `/jGo` — Auto-Context Management checkpoint protocol. Use this command whenever you want to safely compact context, whether or not `/jGo` is active.

---

## Step 1: Detect Context

### 1a: Find active ticket

```bash
# Check current-ticket signals before any state-file fallback
# 1) command argument, 2) session cache/title if available, 3) git branch ticket pattern
git branch --show-current

# Check the exact state file only after a current ticket is known
ls .jswarm/plans/TICKET-XXX/TICKET-XXX.state.md 2>/dev/null

# Check no-ticket carry-forward state only when no ticket signal exists
ls .jswarm/state/precompact-state.md 2>/dev/null

# Check recent plan files (prefer .jswarm/plans/ for new tickets; fall back to docs/plans/ for legacy)
ls -lt .jswarm/plans/*.plan.*.md 2>/dev/null | head -5
ls -lt docs/plans/TICKET-*.md docs/plans/*-*.md 2>/dev/null | head -5
```

**Resolution order:**
1. Explicit argument `TICKET-XXX`.
2. Current session cache or session title containing a ticket key.
3. Git branch name matching ticket pattern (`feat/KEY-NNN`, `KEY-NNN-...`).
4. If a current ticket signal is found but `.jswarm/plans/TICKET-XXX/TICKET-XXX.state.md` is missing, do **not** fall back to any other ticket's state file; checkpoint/recover that ticket explicitly.
5. No-ticket carry-forward state `.jswarm/state/precompact-state.md`, only when no ticket signal exists.
6. Most recent plan file in `.jswarm/plans/` (preferred) or `docs/plans/` (legacy fallback), only as a plan-discovery aid after the state/ticket checks above.

**No ticket found:** Proceed with generic checkpoint (skip plan-file surface).

### 1b: Confirm with user

> I'll checkpoint for TICKET-XXX. Current phase: [from state file or "unknown"]. Proceed? [yes / no]

This confirmation is mandatory for full mode and lite mode.

### 1c: Local precompact include resolution

Resolve local standards for the checkpoint agent at run time. After the active ticket is known and before any 4-surface work begins, check for optional local include files from the current project root. Missing include files are normal: skip absent files silently and continue with the lower-layer behavior unchanged.

**Discovery:**
- project-local precompact include: `.claude/precompact.local.md` — project-wide checkpoint standards, required reading, extra surfaces, or project-specific artifact conventions.
- ticket-local precompact include: `.jswarm/plans/TICKET-XXX/.precompact.md` — ticket-specific checkpoint standards. This **fixed-name dotfile** MUST live in the ticket's own plan folder alongside its other artifacts (plan, specs, retro, UAT), so anyone working the ticket finds the per-ticket standards in one predictable place. The `.precompact.md` name is canonical: do not key-prefix it or store it elsewhere. Resolve `TICKET-XXX` from the active ticket first; a dotfile is hidden from a plain `ls`, so discover it explicitly (e.g. `ls -a .jswarm/plans/TICKET-XXX/`).

**Merge semantics:**
- Layering order: global → project → ticket. Start with this global command, then apply the project-local precompact include, then apply the ticket-local precompact include.
- Additive local standards append to the checkpoint checklist, planning-artifact updates, evidence expectations, required reading, or local templates.
- When ordinary operational defaults conflict, later layers override earlier layers; prefer the ticket-local instruction over the project-local instruction, and the project-local instruction over the global default.
- Local includes may refine paths, surfaces, templates, required reading, status rows, or project/ticket-specific verification details, but they must not weaken safety, canonical retro, or plan-maintenance requirements. They also must not weaken evidence, commit, no-source-loss, absolute worktree-safe symlink, or final `/jClose` obligations.
- When a local include incorporates project/auto-memory items, distill each to a ONE-LINE directive; never paste memory bodies verbatim.

### 1d: Lite / fast mode routing

If `--lite` or `--fast` is present, run **only** the Lite Checkpoint Protocol below and then emit the lite banner. Do **not** run the full promotion gate, 4-surface checkpoint, matrix reconcile, TaskList update, artifact dispatch, full plan-maintenance contract, or a full `/jStatus`. Lite **does** file retros (ticket retro plus standing feature-feedback retros) and may make a scoped retro-only commit when retro content changed. Stale or missing status is refreshed with `/jStatus --lite` only.

`--fast` is a compatibility alias for `--lite`; it must not mean "full checkpoint but skip questions."

### 1e: Full-mode promotion review gate (blocking, pre-surface)

Before the 4-surface checkpoint, **adjudicate every plan item that appears ready to promote** and make a **per-item recommendation — promote now, or hold/defer** — so the developer can sign off ready items mid-stream instead of discovering them only at `/jClose`. This is a *blocking question harness*, not an advisory note: the developer must answer and the decision must be reconciled before any checkpoint surface is written.

Skip silently only when Step 1a resolved no ticket/plan, or the loaded plan has no promotable plan sections. Otherwise, the full-mode gate is mandatory even when every candidate is a hold/defer recommendation.

Read the plan and matrices **as they stand at checkpoint start** (they reflect the previous run's reconcile; this run refreshes them later in Steps 2.0a–2.0c). Promotable candidates include:

- **UAT rows** in `## UAT-Scenario Traceability Matrix` at `🟡 Ready` or equivalent automation ceiling.
- **NFR rows** in `## A/C-to-NFR Traceability Matrix` at `🟡` (measured) or equivalent automation ceiling.
- **Static-proof NFR rows** on `Automated NFR: no` tickets where a `🔴` row can only move by developer acceptance of cited static tests/evidence.
- **A/C-to-Test rows** in `## A/C-to-Test Traceability Matrix` whose cited regression/unit/integration evidence is green but awaits developer acceptance.
- **Acceptance Criteria checkboxes** in `## Acceptance Criteria` that are still `[ ]` but whose mapped UAT/NFR/A/C-to-Test evidence is complete and green. A/C promotion means `[ ]` → `[x]`; never invent or rewrite A/C in this gate.

For each candidate, **inspect its cited evidence** — read the result docs (`TICKET-XXX.uat-test.md`, `TICKET-XXX.nfr-test.md`, `TICKET-XXX.regression-test.md`) and the named tests/files in the row — and classify:
- **✅ Promote** — evidence is complete and green: the UAT scenario's result-doc row is `PASS` and not stale; the NFR threshold is measured-pass; `Automated NFR:no` static proof cites tests/files that exist and are green; A/C-to-Test evidence is green; or an A/C's mapped evidence is complete and green.
- **⏸ Hold / defer** — recommend NOT promoting, with the one-line gap: cited result is `FAIL`/`BLOCKED`/absent/stale; a cited test is missing or red; the threshold is unmeasured; mapped evidence is incomplete; or the row is explicitly deferred (e.g. regression promotion or functional adoption deferred). State what would unblock it.

**Fail-safe:** if you cannot positively confirm a candidate's evidence, classify it **⏸ Hold**, never ✅ Promote (under-recommend rather than over-promote). Recommending a row that lacks real evidence would be a silent false-green.

Emit ONE recommendation block (flip nothing yet) — split into promote vs hold:

```
🔔 Promotion review (required before checkpoint surfaces) — <P> promote, <H> hold:
   ✅ Recommended promotions:
      UAT-1, UAT-3 — result-doc PASS (CLI-smoke); cited tests green
      NFR-008-SCHEMA-VALID-OR-NO-WRITE — Automated NFR:no; cited tests green → 🟢 Done
      AC-4 — mapped UAT-1/UAT-3 + A/C-to-Test rows green → check `[x]`
   ⏸ Hold / defer:
      UAT-5 — cited stub test is collect-only/pending → promote after /jGo adds it
      NFR-017-VENV-ONLY-EXECUTION — venv-only check not yet run → measure, then accept
   Why the counts look stuck: uat_complete / nfr_complete read 0/N (HUD circle 🟡/🔴) until you sign off —
   the DoD ladder caps automation at 🟡 Ready and counts only 🟢 Done. Expected, not a failure.
   Choose: approve all recommended / approve selected / deny all / request changes / abort checkpoint.
   I will reconcile your answer before writing state, plan, retro, commit, or TaskList surfaces.
```

(Omit a section when it has no rows; if every candidate is ⏸ Hold, recommend no promotions yet and name the gaps.)

**Blocking question harness:** ask the developer to choose one:

1. **Approve all recommended promotions** — apply every ✅ item.
2. **Approve selected promotions** — ask which IDs, then apply only those.
3. **Deny all promotions** — apply none; continue the checkpoint with a note in the state/plan Status Updates row.
4. **Request changes** — gather corrections, re-adjudicate the affected items, then show the recommendation block again.
5. **Abort checkpoint** — stop before writing any surface.

The developer's answer is required in full mode unless there are no promotable sections at all.

**Reconcile the answer before any surface update:**
- For approved UAT/NFR/A/C-to-Test rows, flip the named rows to `🟢 Done` (or the plan's canonical done marker).
- For approved A/C, flip only the named `[ ]` checkboxes to `[x]`.
- Leave holds, denials, and unconfirmed items unchanged.
- Record the decision summary for the later state file and plan Status Updates row.
- After approved flips, run Step 2.1 normalize during Surface 2 so `ac_complete` / `nfr_complete` / `uat_complete` and HUD reflect the accepted promotions.

---

## Lite Checkpoint Protocol (`--lite` / `--fast`)

Lite mode is for continuity when the developer wants a small session checkpoint without full evidence reconciliation. It is scoped to activity in the current session. The state-file Main divergence line applies in lite mode too, because lite writes the state file. Lite **does** file retros: the ticket retro when this interval has lessons, plus standing feature-feedback retros when they apply.

**jStatus currency gate (lite — runs before the state file is written):** read `.jswarm/plans/TICKET-XXX/.jstatus.quick.latest.md` provenance (`generated_at`, `repository_head`) and spot-check its cycle header against the governing files (latest decision-review publication, portal round registration) and material events since the render (commits, gate verdicts, review verdicts, deploys, owner rulings). If that quick/lite report is missing or STALE on any of these, run `/jStatus --lite` FIRST (`--quick` / `--fast` are synonyms) and only then write the state file. **Never** run a full `/jStatus` from lite. If `/jStatus --lite` cannot render (no quick template), record that gap in the state file and continue — do not escalate to full `/jStatus`. The state file must NOT repeat information the linked jStatus report carries — it LINKS to `.jstatus.quick.latest.md` as the picture for this checkpoint and records only resume mechanics (live agent ids, uncommitted-tree inventory, standing cross-session obligations, deltas newer than the render). Duplicating report content into the state file is a checkpoint defect (owner ruling 2026-08-30). Full `/jPrecompact` uses the separate Surface 1 gate against `.jstatus.latest.md` and a full `/jStatus` render.

**Allowed writes only:**
1. **State file** — write or refresh `.jswarm/plans/TICKET-XXX/TICKET-XXX.state.md` (or `.jswarm/state/precompact-state.md` when no ticket) with current phase/task, completed work this session, next action, pending A/C/NFR/UAT notes, background/open agent runs, and timestamp. Keep it under 200 lines. Apply the jStatus currency gate above: link `.jstatus.quick.latest.md`; no duplication.
2. **Master plan Status Updates row** — if a plan exists, add one lean row summarizing this session's activity and current next step. Update the checkpoint marker only if it directly describes this session boundary.
3. **Canonical retros** — file the ticket retro and any standing feature-feedback retros using the Retrospective checkpoint location, naming, template, and evidence rules. Refresh the project symlink for each touched retro. Skip silently when there is nothing to record (`N/A — no new lessons this interval` in the state file).
4. **Scoped retro-only commit** — when this lite run wrote retro content, commit those retro real files (and project symlinks) per the dual-repo order in Surface 3. Do not create an empty checkpoint commit, and do not widen the commit to promotion, matrix, TaskList, or artifact work.

**Forbidden in lite mode:**
- promotion recommendation or sign-off harness
- matrix migrate/rebuild/reconcile (`precompact_reconcile`)
- `/update-plan --apply`, HUD/count normalization, or session bind
- broad plan maintenance beyond the Status Updates row
- A/C checkbox flips, UAT/NFR/A/C-to-Test status flips, scope changes, Required Reading changes, or Critical Files changes
- git commit other than the scoped retro-only commit (including empty checkpoint commits)
- TaskList rewrite
- artifact subagent dispatch
- full `/jStatus` (standard-mode render of `.jstatus.latest.md`); lite refreshes status only via `/jStatus --lite`

**Then file retros** — after the two continuity surfaces. Use the Retrospective checkpoint authoring flow (resolve the canonical file, append or create, refresh the project symlink). Apply the **Standing feature-feedback retro directive**. When retro content changed, run the scoped retro-only commit. When there are no ticket lessons and no feature feedback, record `N/A` in the state file and do not create empty files.

**Lite banner:**

```
━━━ LITE CONTEXT CHECKPOINT ━━━
Done:   state file updated; plan Status Updates row added/updated (if plan exists); retro filed / N/A; jStatus --lite refreshed / current / N/A (no quick template)
Skipped: promotion review, matrix reconcile, checkpoint commit, TaskList, broad plan maintenance, full /jStatus
Retro:  ${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro[.<kind>].md updated / N/A; project symlink OK / N/A
Next:   <immediate next action>
State:  <state-file> (N lines)
Recommend /compact now only if this lite checkpoint is enough for resume.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

After lite mode emits the banner, stop. Do not continue into Step 2.

---

## Standing feature-feedback retro directive (full and lite)

When filing retros, write a **separate** kind-file for each recently updated DevOps feature you actually used in this interval **and** for which you have feedback. Do not invent a feature retro because the feature exists, because it was nearby, or because this roster names it. Unused features and sessions with no notes skip silently — never create an empty feature file.

**Current standing roster** (extend the list when another newly updated feature is in active field use):

| Feature | Kind file | Write when |
|---------|-----------|------------|
| test UAT lifecycle | `TICKET-XXX.retro.test-uat-lifecycle.md` | You ran or were steered by the Level 1 UAT cycle (`jTest/test-uat.lifecycle.md`) this interval and have feedback |
| fix cycle (`/jFix` methodology, incl. orchestrator-driven Diagnose→Contract→Repair→Prove) | `TICKET-XXX.retro.fix-cycle.md` (or fold into `test-uat-lifecycle` when the interval's fix and UAT narrative is one arc — cross-link, don't duplicate) | Fix cycles ran this interval and you have feedback (review verdicts, architect placement, contract mechanics) |
| fix-uat portal (decision-review publications, round registration/cutover, delivery) | `TICKET-XXX.retro.fix-uat-portal.md` (same fold-in option as above) | Portal machinery was exercised this interval and you have feedback |

**Heavy-use rule (owner directive 2026-08-30, BOTH full and lite):** beyond the fixed roster, any skill or lifecycle mechanism in HEAVY USE during the ticket session (repeated invocations, or central to the interval's work — e.g. `/jFix`, `/jTest uat`, the fix-uat portal, `/jStatus` templates) gets detailed retro coverage when there is feedback: what worked, what was corrected by the owner, costs vs benefits. Heavy use with owner corrections and NO retro coverage is a checkpoint defect. The roster is a floor, not a ceiling.

Each feature retro must weigh **benefits versus costs**:

- **Benefits** — re-work avoided, risk avoided, defects caught earlier, a wrong path stopped before it compounded
- **Costs** — wall-clock time, tokens, ceremony friction, extra hops

Label estimates when the cost or benefit is not instrumented. One short table or a few `+` / `−` / `Δ` rows is enough. Cross-link the ticket retro; do not copy the same narrative into both files.

This roster is a named exception to "prefer one ticket retro until a second narrative is warranted": these feature files stay separate so field feedback is mineable.

---

## Step 2: 4-Surface Checkpoint (MANDATORY)

Every full checkpoint writes to **all four** surfaces. No single surface is sufficient — they cross-reference each other. Lite mode is the only exception and must stop before this section.

**Execution order** (do not reorder): Surface 1 (state draft) → Surface 2 (planning artifacts) → **Retrospective checkpoint (jClose-aligned)** → Surface 3 (git commit, includes retro + plans + state) → Surface 4 (TaskList) → finalize state file with recorded commit SHA. The Step 2.0-rules-tools maintenance window is an explicit background exception: launch it during Surface 2, record its task ID, and continue this order without waiting for its result.

### Surface 1: State File

**Path:** `.jswarm/plans/TICKET-XXX/TICKET-XXX.state.md` (or `.jswarm/state/precompact-state.md` if no ticket)

**jStatus currency gate (full mode):** verify `.jstatus.latest.md` is accurate first; if stale or missing, run full `/jStatus` before writing the state file, then link `.jstatus.latest.md` from the state file and do not repeat its content (owner ruling 2026-08-30). Lite mode uses the separate Lite Checkpoint Protocol gate: `/jStatus --lite` against `.jstatus.quick.latest.md`, never this full render.

This state file is the post-compaction resume anchor. It is separate from the standards include `.jswarm/plans/TICKET-XXX/.precompact.md`: `<KEY>.state.md` records volatile WHERE-we-are carry-forward state; `.precompact.md` remains the stable HOW-to-checkpoint standards file.

**Contents (≤200 lines):**
- Ticket identifier
- Completed phases / tasks (with 🟢 markers)
- Current phase and next task
- Pending A/C (not yet verified)
- Pending NFR validation (when the project is NFR-adopted + plan `NFR catalog: applicable`): applicable NFRs not yet 🟢 in the A/C↔NFR matrix, and the ticket NFR working-slice / proposals-sidecar paths (`TICKET-XXX.nfr.md` / `TICKET-XXX.nfr-proposals.json`) — so the NFR authoring/promotion state survives compaction. Non-adopted projects skip (fail-open).
- Background agent task IDs (critical — prevents orphaning across compaction)
- Open agent runs + resume pointers (continuity, fail-open): when the project has the agent-run ledger (`jswarm/agent_run_ledger.py` present), fold any **open** sub-agent runs and their resume pointers into the state so an in-flight run survives main-session compaction. Run it fail-open — never let it block the checkpoint:

  ```bash
  .venv/bin/python jswarm/agent_run_ledger.py list-open 2>/dev/null || true
  ```

  Paste the summary lines under `## Open Agent Runs` below. Absent ledger / `JSWARM_AGENT_RUN_LEDGER=0` / any error ⇒ skip silently (omit the section). Records are redaction-safe (digest, no raw prompt).
- Harness-health line (AC-7, fail-open): every checkpoint carries the harness-health state so degradation is visible at the resume boundary. Run and paste the one-line summary under `## Harness Health`:

  ```bash
  PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.harness_health --fast 2>/dev/null || true
  ```

  When the recommendation is `checkpoint-and-rotate: ...`, SURFACE it to the developer in the checkpoint summary and recommend rotating the session after this checkpoint rather than continuing heavy dispatches — advisory only; it never gates the checkpoint. Any sampler error ⇒ skip silently.
- Last commit SHA
- Timestamp

**Template:** Add one `Main divergence` line to every state file — including lite mode — so the resuming session inherits the divergence fact instead of rediscovering it.

```markdown
# Precompact State: TICKET-XXX
**Written:** [ISO timestamp]
**Commit:** [last SHA]
**Main divergence:** behind by [N]; relevant surfaces changed on main: [paths or none]; checked [ISO timestamp]

## Progress
- [x] Phase 1: [description] — 🟢 (commit: [sha])
- [ ] Phase 2: [description] — 🟡 in progress
- [ ] Phase 3: [description] — 🔴 not started

## Current Work
- **Phase:** [N]
- **Task:** [description]
- **Next action:** [concrete next step]

## Pending A/C
- [ ] AC-1: [description]
- [ ] AC-2: [description]

## Background Agents
| Agent | Task ID | Status | Purpose |
|-------|---------|--------|---------|
| [name] | [id] | [running/completed] | [description] |

## Open Agent Runs
<!-- continuity (fail-open): output of `agent_run_ledger.py list-open`; omit this section entirely if the ledger is absent/disabled/errors. -->
- [run_id] [subagent_type] attempts=[n] resume<-[checkpoint path or "(no checkpoint — bounded cold restart)"]

## Notes
[any working variables, decisions, or context needed on resume]

## Plan sections touched (this checkpoint)
List only sections actually edited (omit line if none): A/C · traceability matrix · UAT matrix · phases/tasks · Testing Strategy · Status Updates · Required Reading (+/− paths) · Critical Files · Scope · Last Updated

## Retro (this checkpoint)
- Common real file: `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro[.<kind>].md` — created/appended this interval **or** `N/A — no new lessons this interval`
- Common commit: [sha] **or** N/A
- Project symlink: `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md` → absolute path into common (worktree-safe) **or** `N/A — common-only or no ticket`
- Project commit: [sha] **or** N/A (same as Common commit when common-only)
```

### Surface 2: Planning Artifacts (PARALLEL DISPATCH)

**All ticket-local planning files must be updated.** The orchestrator dispatches agents in parallel — one per artifact — to maximize throughput.

**Step 2.0-hygiene — Ticket-folder tidy via jOps (FULL MODE ONLY; owner-standing directive 2026-07-08, fail-open).** Dispatch a `jOps` agent to tidy `.jswarm/plans/TICKET-XXX/` at the start of Surface 2 so the moves ride this checkpoint's commit:
- **Target layout:** canonical assets STAY AT ROOT (`<KEY>.plan.*`, `<KEY>.state.md`, `.precompact.md`, `<KEY>.uat-scenarios.md`, `<KEY>.uat-scenario-steps.md` (legacy `uat-test.md`), `<KEY>.nfr*.md`, `<KEY>.regression-tests.md`, `<KEY>.deferred-items.md`, retro symlinks, `<KEY>.MOVED-MAP.md`). Loose artifacts move into subfolders: `specs/` (ticket-specs, design-inputs), `designs/` (`<KEY>.design.*`), `debug/`, `uat-results/` (evidence PNGs, session JSONs), `notes/` (worksheets, one-off notes, misc).
- **In-flight safety (MANDATORY):** the orchestrator supplies an exclusion list of files referenced by currently-running lanes — dispatch prompts hold absolute paths, and moving a file a live agent will Read breaks that lane. When in doubt, DEFER the move and record it in the MOVED-MAP as `DEFERRED`.
- **Mechanics:** `git mv` for tracked files, `mv` for untracked; append every `old → new` (and `DEFERRED`) pair to `<KEY>.MOVED-MAP.md`; update references to moved files WITHIN the ticket folder only (historical gate reports/retros elsewhere stay untouched — the MOVED-MAP resolves their stale paths); explicit-path staging only, never `git add -A`.
- **Fail-open:** jOps unavailable, or the folder already tidy ⇒ skip silently. Lite mode NEVER runs this step.

**Step 2.0-rules-tools — Background architecture rules + tools maintenance window (FULL MODE ONLY; fail-open, NO WAIT).** Alongside Step 2.0-hygiene and the deterministic matrix reconcile, launch one `jOps` lane in the background to reconcile `docs/architecture/rules/` and `docs/architecture/tools/` against the current pipeline. This is maintenance, not a checkpoint gate: it **MUST NOT block or slow `/jPrecompact`**. Do not wait for it before later Surface 2 work, the retrospective, commit, TaskList, or banner. Record its task ID in the state file so its result can be collected later.

Scope the background lane to:
- add concise architecture catalog cards for newly introduced pipeline rules/tools; mark retired entries; flag code/registry/runtime rules or LLM-usable tools that lack architecture catalog cards;
- refresh `docs/architecture/rules/rules_pipeline_summary.json`;
- when project ColGREP indexing support is healthy, refresh the project code index/worktree overlay so semantic search remains useful; if degraded/unavailable, record the limitation and continue;
- mirror existing pipeline-trace maintenance: read `.claude/skills/jDebug/pipeline-trace.md` (or the installed equivalent), and when an orchestration mechanism changed, validate/regenerate `docs/architecture/architecture.v3.pipeline-trace.matrix.json` plus its rendered `docs/architecture/architecture.v3.pipeline-trace.md` companion.

Do not introduce a heavyweight ceremony, approval loop, or blocking gate. Respect the active project's agent-depth cap: this helper performs the maintenance directly and **must not spawn further agents**.

```typescript
task(subagent_type="jOps", load_skills=[], run_in_background=true,
  description="Maintain architecture rules tools",
  prompt="BACKGROUND / NO WAIT: reconcile docs/architecture/rules/ and docs/architecture/tools/ against the current pipeline. Add architecture cards for newly introduced rules/tools, mark retired entries, flag code/registry/runtime rules and LLM-usable tools without architecture catalog cards, and refresh docs/architecture/rules/rules_pipeline_summary.json. When project ColGREP indexing support is healthy, refresh the project code index/worktree overlay; otherwise record the degraded state and continue. Read jDebug/pipeline-trace.md and, only when an orchestration mechanism changed, validate/regenerate architecture.v3.pipeline-trace.matrix.json plus rendered architecture.v3.pipeline-trace.md. Fail open; do not block or slow precompact; do not spawn further agents; return a compact changed-files/gaps summary when finished.")
```

**Step 2.0a — Auto-migrate legacy assets to current standards (AC-16, fail-open).** `/jPrecompact` does not require an agent to "figure anything out": it first self-heals a legacy ticket so it behaves as though the current lifecycle assets existed at creation. Idempotently SEEDS a missing slice index (`## UAT Scenario Index` / `## A/C-to-NFR Index` / `## A/C-to-Test Index`) from the plan's (possibly hand-authored) matrix rows, and ADDS a missing plan matrix section when a slice index exists without one. Regression A/C-to-Test seeding is **matrix-present only** — `/jPrecompact` never auto-scaffolds a regression slice for a ticket that has no `## A/C-to-Test Traceability Matrix`:

```bash
# /jPrecompact delegates the deterministic plan-maintenance chain to /update-ticket,
# which orchestrates migrate → rebuild-rows → reconcile-status → count in canonical order over
# the same engines (byte/exit parity proven by jswarm/tests/test_update_ticket_*).
# The wrapper is common-owned and invoked by ABSOLUTE COMMON PATH (it resolves its engines from
# common, not the target project), with --repo-root . naming the project: so this works in every
# project. Steps 2.0a–2.1 below describe each section's behavior; all four run from this ONE
# fail-open delegation. (The promotion-review gate in §1e is interactive prose, also homed in the
# `update-ticket` skill; it is NOT part of this deterministic call.)
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_ticket/cli.py --ticket TICKET-XXX --repo-root . --sections migrate,rebuild-rows,reconcile-status,count || true
```

It never re-seeds or clobbers an existing index/matrix (idempotent), and creates `TICKET-XXX.nfr.md` / `TICKET-XXX.regression-tests.md` / appends to `TICKET-XXX.uat-scenarios.md` only when seeding is needed. **AC-16** seeds from an existing hand-authored matrix; **AC-17** scaffolds empty UAT/NFR matrix sections + starter slice index files for a *declared-applicable* ticket that has neither matrix nor slice (e.g. a legacy ticket) — applicability is read from the plan headers (`**NFR catalog:** applicable` / `**Automated UAT:** yes`), never guessed. Regression tests are excluded from AC-17 auto-scaffold; they seed only from an existing A/C-to-Test matrix. After this, the slice index is the source of truth and the rebuild (2.0b) is a no-op delta on an already-migrated ticket. Fail-open (`|| true`). **To upgrade an existing in-flight ticket, see `docs/agent-system/upgrade-existing-ticket.md`.**

**Step 2.0b — Rebuild the count-bearing matrices' ROWS from the ticket-local working slices (AC-11, fail-open).** After migration (2.0a) and before reconciling statuses (2.0c) or counting (2.1), regenerate each matrix's *rows* from the working slices so an agent never hand-authors them — the **UAT-Scenario matrix** from `TICKET-XXX.uat-scenarios.md`, the **A/C-to-NFR matrix** from `TICKET-XXX.nfr.md`, and the **A/C-to-Test traceability matrix** from `TICKET-XXX.regression-tests.md` when that regression slice exists. Each slice carries a fixed-format **index table** under a canonical heading (`## UAT Scenario Index`, `## A/C-to-NFR Index`, `## A/C-to-Test Index`) whose columns are exactly the matrix columns minus Status (defined by `docs/templates/UAT_SCENARIO_EXTRACT_TEMPLATE.md` / `NFR_CATALOG_EXTRACT_TEMPLATE.md` / `TEST_EXTRACT_TEMPLATE.md` — the same templates `/jPlan` instantiates):

*(The `rebuild-rows` section — runs within the single `/update-ticket` delegation in Step 2.0a above, not as a separate invocation.)*

It **adds** rows newly in an index, **drops** rows no longer present, and **preserves** the Status cell of a surviving row (so a row already reconciled to 🟢 is not reset; a brand-new row defaults to 🔴 — never green). Regression rows are keyed by the full `(A/C, Test path, Test name / evidence check)` tuple; path-only or partial matches are rejected. **Discipline:** an EMPTY/absent index (missing slice) leaves that matrix **unchanged** — a missing slice never wipes rows; idempotent; fail-open (`|| true`). This is the "rebuild the matrices" step behind 2.0b/2.1 — without it rows drift from the local source-of-truth slices.

**Step 2.0c — Reconcile the count-bearing matrices' STATUSES from ticket-local results (AC-10, fail-open).** `/jPrecompact` runs *repeatedly* across a ticket's life (unlike one-shot `/jGo`), so it is the checkpoint that keeps the **NFR**, **UAT-scenario**, and **regression A/C-to-Test** traceability matrices truthful. **After** the row-rebuild (2.0b) and **before** the count in Step 2.1, feed those matrices' Status cells from the ticket-local result docs (`.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-test.md`, `TICKET-XXX.nfr-test.md`, `TICKET-XXX.regression-test.md`) using the deterministic reconciler — kept **separate** from update-plan so update-plan stays count-only:

*(The `reconcile-status` section — runs within the single `/update-ticket` delegation in Step 2.0a above, not as a separate invocation.)*

It maps each result-doc row (`UAT-<n>` / `NFR-<descriptor>` / regression full tuple → PASS/FAIL, text or 🟢/🔴) onto the matching plan matrix row (UAT matrix keyed by scenario id; A/C-to-NFR matrix keyed by the NFR ref; regression matrix keyed by `(A/C, Test path, Test name / evidence check)` from `TICKET-XXX.regression-test.md`). **Discipline:** a row with no local result is left **unchanged — never silently flipped green** (fail-safe = under-report); idempotent; fail-open (`|| true`, never blocks the checkpoint). A project whose result docs are non-structured falls back to an agent-driven update of the matrix Status cells (still **before** Step 2.1).

**Step 2.1 — Normalize + archive hygiene + HUD-refresh the master plan (fail-open).** Now that the matrices reflect the latest results, run the deterministic `/update-plan` module entrypoint so the plan's frontmatter is normalized (frontmatter hoisted to line 1; `status`/`phase`/`ac_complete` **and the AC-9 `nfr_complete`/`uat_complete` counts** derived from `plan_status` + the `## Acceptance Criteria` section + the freshly-reconciled traceability matrices), explicitly eligible old sections are moved into the ticket `_archive/`, explicit tech-spec sections are relocated to specs, and the HUD's single source of truth is refreshed:

*(The `count` section — runs within the single `/update-ticket` delegation in Step 2.0a above, not as a separate invocation. It reuses the `update-plan` normalize/count/hygiene engine, `--apply`.)*

**Step 2.1a — Regression test-catalog preview + completeness warning (fail-open).** When a ticket carries `TICKET-XXX.regression-tests.md`, preview the ticket-scoped regression catalog and warn on touched-but-uncited tests before compacting. Skip silently when the regression slice or project catalog sources do not exist; all commands are fail-open (`|| true`) and must never affect a ticket without a regression slice:

```bash
if [ -f ".jswarm/plans/TICKET-XXX/TICKET-XXX.regression-tests.md" ]; then
  .venv/bin/python jswarm/test-catalog/generate_test_catalog.py     --scope ticket     --plan ".jswarm/plans/TICKET-XXX.plan.<desc>.md"     --extract ".jswarm/plans/TICKET-XXX/TICKET-XXX.regression-tests.md"     --uat "docs/uat-scenarios.json"     --nfr "docs/nfr/nfr-catalog.json"     --inventory "tests/regression-inventory.json"     --template "docs/templates/TEST_CATALOG.md.j2"     --output ".jswarm/plans/TICKET-XXX/TICKET-XXX.test-catalog.preview.md"     --strict-links || true
  BASE_REF="$(git merge-base HEAD origin/main 2>/dev/null || true)"
  if [ -n "$BASE_REF" ]; then
    .venv/bin/python jswarm/test-catalog/completeness_gate.py       --stage precompact       --plan ".jswarm/plans/TICKET-XXX.plan.<desc>.md"       --extract ".jswarm/plans/TICKET-XXX/TICKET-XXX.regression-tests.md"       --base "$BASE_REF"       --json || true
  fi
fi
```

**Step 2.1b — Bind THIS session to its ticket (per-session HUD, fail-open).** `normalize` (above) refreshes the HUD's *metrics*; this refreshes the HUD's *identity* so a session whose terminal HUD would otherwise fall back to the global-freshest plan shows ITS OWN ticket. The `bind` subcommand writes the per-session `active-ticket.json` binding **without** recording a transition — so it works at every checkpoint regardless of `plan_status` state (e.g. a registry that lags the plan). Keyed by `CLAUDE_CODE_SESSION_ID` (== the statusline payload `session_id`); a no-op outside a Claude Code session or for a ticket not in this project:

```bash
.venv/bin/python jswarm/plan_status/cli.py bind TICKET-XXX || true
```

This reuses `normalize_plan_file` (no reimplementation) and prints a summary line (`… ac=… nfr=… uat=… normalized=…`). It is **fail-open** (`|| true`) so it never blocks the checkpoint. **Determinism boundary:** update-plan only *counts* the matrices already in the plan — never the local slices/test files; keeping those matrices accurate is Steps 2.0a–2.0c's job (AC-16 migrate + AC-11 row-rebuild + AC-10 status reconcile). The regression A/C-to-Test slice/result are reconciled before this point when present, but a ticket with no regression slice remains unaffected. Pull-render semantics: the next ccstatusline render reflects the refreshed frontmatter — there is no push/redraw. `--apply` is intentional in `/jPrecompact`: it moves only explicitly eligible sections/markers, leaves anchored discoverability links, and still never moves Acceptance Criteria, active blockers, unresolved decisions, current verification requirements, Scope, Changelog, Required Reading, or semantically ambiguous content. (Skill facade: `.claude/skills/update-plan/SKILL.md`; a bare `/update-plan` remains propose-only, but `/jPrecompact` applies hygiene so old checkpoint content does not accumulate in the live plan.)

**Discover existing files (dual-path):**
```bash
# new tickets (>= 2026-05-22): master + per-ticket artifact subfolder
ls .jswarm/plans/TICKET-XXX.plan.*.md 2>/dev/null         # master
ls .jswarm/plans/TICKET-XXX/TICKET-XXX.*.md 2>/dev/null   # artifacts

# Legacy: pre-2026-05-22 tickets keep their existing paths
ls docs/plans/TICKET-XXX-*.md 2>/dev/null
```

Resolve once: if the new master `.jswarm/plans/TICKET-XXX.plan.*.md` exists, this is a NEW-LOCATION ticket — use only `.jswarm/plans/TICKET-XXX/` for artifact reads/writes. Otherwise this is a LEGACY ticket — use `docs/plans/TICKET-XXX-*.md` paths. **Never mix locations for one ticket.**

### Plan maintenance contract (master plan file, MANDATORY)

Every full checkpoint must bring the **master plan file** to a **current, lean execution snapshot**. Update **status and evidence only** — not scope prose. The plan is the resume map; the retro holds narrative lessons. Lite mode is limited to the Status Updates row described in the Lite Checkpoint Protocol.

**Lean prose rule (non-negotiable):** table rows, checkboxes, status glyphs (🟢/🟡/🔴), one-line cells, and short bullets only. **Do not** add paragraphs, rewrites of Overview/Risk/Agent rationales, or duplicate retro content into the plan. If a section did not change this interval, **leave it unchanged** — do not pad with N/A or filler.

**Update every section that exists in the plan and changed this interval:**

| Section | What to update | Lean rule |
|---------|----------------|-----------|
| **Acceptance Criteria** | Flip `[ ]` → `[x]` only when **verified** this interval; one-line blocker note if stuck | No **new** A/C without explicit user approval |
| **A/C-to-Test Traceability Matrix** | Status column from `TICKET-XXX.regression-test.md`; test file/name rows come from `TICKET-XXX.regression-tests.md` | Rows only — no narrative; do not hand-author rows when the regression slice exists |
| **UAT test matrix** / per-phase UAT tables (in plan or cross-ref `uat-test.md`) | Pass / fail / pending per scenario; evidence link or commit | One line per row delta |
| **Implementation Phases** | Active phase marker; **Tasks** checkboxes + 🟢/🟡/🔴; **Exit Criteria** met/pending | Mark tasks only — no new tasks/phases without user approval |
| **Testing Strategy** table | Layer **Status** when proof landed or deferral changed | Table cells only |
| **Status Updates** | One new row: date, phase, outcome, commit SHA | One row per checkpoint |
| **Required Reading** | **Add** a row when a new architecture/spec/doc became load-bearing (e.g. key `docs/architecture/*.md`, controlled-config, parent feature spec); **remove** rows superseded or no longer referenced | Path + one short "Why" cell — **never** paste doc body |
| **Critical Files** | Add/remove paths when the file map changed | Path list only |
| **Scope** (In/Out) | Only when user **approved** a scope change — brief bullet delta | No speculative expansion |
| **Last Updated** | Set to checkpoint calendar date | - |
| Frontmatter `status` / `revisions` | Only when state machine flipped (`ACTIVE` → `READY_FOR_MERGE`, etc.) | Per `/jGo` + plan-status rules |
| Frontmatter `phase` / `ac_complete` | **Do NOT hand-edit** — DERIVED by the reconcile hook (`normalize_plan_file`) from `plan_status` + the `## Acceptance Criteria` section; the checkpoint reads them, the hook maintains them, frontmatter stays at line 1 | Read-only; never manually set |

**Checkpoint marker:** add `<!-- checkpoint: [ISO timestamp] phase N complete -->` adjacent to the phase that finished (or the phase in progress when pausing mid-flight).

**Orchestrator verify before commit:** skim the master plan — every verified A/C is checked; active phase/tasks match the state file; Required Reading includes any doc the next phase depends on; no new prose blocks appeared.

**Artifacts to update (dispatch one agent each, in parallel):**

| File (new-location form) | Legacy fallback form | Purpose | Update Action |
|------|------|---------|---------------|
| `.jswarm/plans/TICKET-XXX.plan.<desc>.md` (master) | `docs/plans/TICKET-XXX-DESCRIPTION.md` | Main plan file | Apply **Plan maintenance contract** above: A/C, traceability matrix, UAT matrix rows, phases/tasks, Testing Strategy status, Status Updates row, Required Reading, Critical Files, Last Updated; checkpoint comment |
| `.jswarm/plans/TICKET-XXX/TICKET-XXX.specs.<desc>.md` | `docs/plans/TICKET-XXX.specs.md` | Technical design spec | **Lean:** status rows + one-line decision/deviation notes only; update implementation status, record design decisions, note spec deltas |
| `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenarios.md` | `docs/plans/TICKET-XXX.uat-scenarios.md` | UAT scenario extract | **Lean:** scenario status column + one-line change note; no new scenarios without evidence |
| `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-test.md` | `docs/plans/TICKET-XXX.uat-test.md` | Executable UAT doc | **Lean:** UAT test matrix pass/fail/pending + evidence refs; sync per-phase expectations with results |
| `.jswarm/plans/TICKET-XXX/TICKET-XXX.regression-tests.md` | `docs/plans/TICKET-XXX.regression-tests.md` | Regression A/C-to-Test extract | **Lean:** maintain `## A/C-to-Test Index` rows only when regression proofs changed; tuple key is `(A/C, Test path, Test name / evidence check)` |
| `.jswarm/plans/TICKET-XXX/TICKET-XXX.regression-test.md` | `docs/plans/TICKET-XXX.regression-test.md` | Regression result doc | **Lean:** result table rows with full tuple + PASS/FAIL + evidence; sync from executed test evidence |
| `.jswarm/plans/TICKET-XXX/TICKET-XXX.integr-fixes.md` | `docs/plans/TICKET-XXX-integr-fixes.md` | Integration defect tracker (Feature plans only) | Update defect statuses (OPEN→FIXED→VERIFIED), add new defects found |
| `.jswarm/plans/TICKET-XXX/TICKET-XXX.pe2e-fixes.md` | `docs/plans/TICKET-XXX-pe2e-fixes.md` | PE2E defect tracker (Feature plans only) | Update defect statuses, add screenshot evidence references |

**Parallel dispatch pattern (use resolved paths from the dual-path lookup above):**

```typescript
// Spawn one flash-tasker per artifact — all in parallel
// PLAN_FILE / SPECS_FILE / UAT_SCEN_FILE / UAT_TEST_FILE resolved per the dual-path table above
task(subagent_type="jOps", load_skills=[], run_in_background=true,
  description="Update plan file checkpoint",
  prompt="Read <PLAN_FILE>. Apply the Plan maintenance contract: update A/C checkboxes (verified only), A/C-to-Test traceability matrix status, UAT test matrix rows, Implementation Phases task statuses (🟢/🟡/🔴), Testing Strategy table status, one Status Updates row, Required Reading (add load-bearing docs discovered this interval; remove stale rows), Critical Files if changed, Last Updated date, and <!-- checkpoint: timestamp phase N complete -->. LEAN: table rows and one-line cells only — no new prose blocks. MUST NOT add new A/C, tasks, or phases without user approval. MUST NOT rewrite Overview, Scope prose, or Agent Assignment rationales.")

task(subagent_type="jOps", load_skills=[], run_in_background=true,
  description="Update specs checkpoint",
  prompt="Read <SPECS_FILE>. LEAN updates only: implementation-status table/rows, one-line design decisions, one-line spec deviations. MUST NOT change technical approach or add narrative sections.")

task(subagent_type="jOps", load_skills=[], run_in_background=true,
  description="Update UAT scenarios checkpoint",
  prompt="Read <UAT_SCEN_FILE>. LEAN: update scenario status fields + one-line change notes only. MUST NOT add scenarios without implementation evidence.")

task(subagent_type="jOps", load_skills=[], run_in_background=true,
  description="Update UAT test doc checkpoint",
  prompt="Read <UAT_TEST_FILE>. LEAN: update UAT test matrix + per-phase result rows (pass/fail/pending + evidence). MUST NOT change execution strategy without a one-line documented reason.")
```

<!-- uat-scenarios:write-integration -->
When a project has adopted the UAT-scenario engine, WRITE scenario changes (merge-back / overlay) as a structured **JSON-patch sidecar** applied via `jswarm/uat-scenarios/apply_uat_patch.py` (RFC-6902 ops; `base_sha256` for optimistic-concurrency conflict detection) → it updates the canonical JSON and regenerates the generated Markdown. Never hand-edit the generated `.md` or the canonical JSON directly. On a conflict (stale `base_sha256` or failed `test` op) the apply aborts without writing — rebase the patch on the current canonical and retry. Non-adopted projects keep the markdown merge-back path.

**Dispatch rules:**
- Only dispatch agents for files that **exist** — skip non-existent artifacts
- All artifact-update agents run `run_in_background=true` — collect their results before the **retrospective checkpoint** and git commit. **Exception:** Step 2.0-rules-tools is explicitly no-wait; preserve its task ID and continue.
- Each agent prompt includes: TASK (what to update), MUST DO (specific fields to update), MUST NOT DO (scope boundaries), and the active project's depth cap (helpers must not spawn further agents)
- **Master plan agent** (or orchestrator inline edit) owns the **Plan maintenance contract** checklist — artifact subagents do not replace master-plan A/C / Required Reading / phase updates

**No planning artifacts exist:** Skip this surface. Note in state file that no planning artifacts were found.

### Retrospective checkpoint (before Git commit)

**Authoritative procedure:** `/jClose` **Step 2 — Write the retro (mandatory, autonomous)** in `skills/jClose/SKILL.md`. Precompact applies the **same location, naming, template shape, and evidence discipline** as closeout, but typically as an **append** for this session interval (phase N). Full formal closeout still runs `/jClose` when the ticket completes. Lite uses this authoring flow and the **Standing feature-feedback retro directive** without running the surrounding 4-surface work; when lite writes retro content it commits those files only.

**Canonical write path (single rule — common archive + project symlink):**

The retro's **real file** always lives in the common archive `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro[.<kind>].md` (committed in the `common` repo). The project repo holds only an **absolute symlink** at `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md` → that file. **Never** write a real retro file under `.jswarm/plans/` (or anywhere in the project), and **never** put a retro symlink in the project root.

**Resolution rule:** check `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro*.md`. If a file exists for the ticket → **append** this interval's section there. Else → **create** `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.md`. Then create/refresh the project symlink (rule 6 + Surface 3).

**Authoring flow (same 3 steps `/jClose` uses — do not reorder):**

1. **Resolve the canonical file** — append to an existing common retro or create `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.md` from the `/jClose` retro template when this interval has meaningful findings.
2. **Commit the real file in the `common` repo** when retro content changed this interval (`docs/retros/TICKET-XXX.retro*.md`).
3. From the **project repo** (when an application checkout is in play), create/refresh the **absolute** symlink at `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md` → the common file (below), and commit the symlink in the project repo.

> **Never** write a real retro file under `.jswarm/plans/` (or anywhere in the project), and **never** put a retro symlink in the project root. Both are violations of the canonical retro convention (`${JSWARM_HOME:-$HOME/dev/jswarm}/.claude/skills/retros/SKILL.md`; `/retros --apply <project>` repairs drift). A **common-only** ticket has no project symlink — the common real file is the whole deliverable.

**Naming (same as `/jClose`):**

| Use | Common real file (authoritative) | Project symlink (absolute → common) |
|-----|----------------------------------|-------------------------------------|
| Default — one retro for the ticket | `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.md` | `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro.md` |
| Split by kind (DevOps/tooling vs product/code, coordination vs implementation, etc.) | `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.<kind>.md` | `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro.<kind>.md` |

Prefer **one** `TICKET-XXX.retro.md` until a **second** narrative is clearly warranted; then add `TICKET-XXX.retro.<kind>.md` and cross-link. **Exception:** the **Standing feature-feedback retro directive** requires a separate kind-file when that gate is met — those files do not replace the ticket retro. **Legacy** archive files (`TICKET-XXX-SLUG.retro.md`, date-stamped names, etc.) remain valid as the common real file when continuing an established file; the project symlink still uses the clean `TICKET-XXX.retro[.<kind>].md` name.

**Plan path** in the retro body links to the ticket's plan file — `.jswarm/plans/TICKET-XXX.plan.<desc>.md` (new) or `docs/plans/TICKET-XXX-DESCRIPTION.md` (legacy).

**Gather evidence** (same sources as `/jClose` — use what is available this interval):

1. Plan file — phases, scope changes, status updates, deferrals  
2. `git log --oneline` for commits touching this ticket  
3. `ls docs/tool-failure-reports/TICKET-XXX.toolfail.*.md 2>/dev/null` (from repo root or common, as applicable)  
4. Technical design spec — `.jswarm/plans/TICKET-XXX/TICKET-XXX.specs.*.md` (new) or `docs/plans/TICKET-XXX.specs.md` (legacy) — planned vs actual deltas  
5. Feature defect logs — `TICKET-XXX-integr-fixes.md` / `TICKET-XXX-pe2e-fixes.md` (legacy) or `.jswarm/plans/TICKET-XXX/TICKET-XXX.integr-fixes.md` / `.pe2e-fixes.md` (new) if they exist  
6. Conversation / session — stalls, rework, surprises  

**Writing rules (`/jClose`-compliant):**

1. **If the canonical retro file already exists:** **UPDATE only** — append new findings; add a **Changelog** row (`YYYY-MM-DD`, author, `Precompact checkpoint — phase N`); **never** overwrite existing body. For a precompact boundary, add a section such as `## Precompact — YYYY-MM-DD (phase N)` and, under **Retrospective**, include only categories with new material this interval (skip categories with no findings — do not pad with N/A). Use the same **table shape** as `/jClose`: `+` / `-` / `Δ` per category row.  
2. **If no file exists yet** and this interval has **meaningful** findings: **create** the file using the **full markdown template `/jClose` uses**. Use **`TICKET-XXX.retro.md`** unless this interval is **only** about an orthogonal “kind” (then `TICKET-XXX.retro.<kind>.md` and link to/from any sibling retro). Populate **Depth** appropriately (often `Quick` for a mid-flight precompact). Leave follow-up polish for final `/jClose` if the ticket is not done.  
3. **If no meaningful findings** this interval: **do not** create an empty file. Record in the state file **Retro** section: `N/A — no new lessons this interval`.  
4. **Categories** when you do write: use the `/jClose` list (Project / Domain Knowledge, Planning, Testing & QA, AI Agent Effectiveness, Tooling, Architecture / Infrastructure, UAT ↔ E2E Alignment, Other) — include a category only when there is something worth recording.  
5. **Action items:** If precompact surfaces concrete follow-ups, add rows to **Action Items** (or reference existing open rows).  
6. **Project symlink — worktree-safe.** When the working tree is an **application repo** and you created/appended a common retro this interval, ensure **each** touched retro has an **absolute** symlink co-located with the ticket's plan artifacts:

   ```bash
   mkdir -p .jswarm/plans/TICKET-XXX
   # absolute target ($HOME expands at ln time); -n so re-running refreshes in place
   ln -sfn "${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/<common-basename>.md" \
           ".jswarm/plans/TICKET-XXX/TICKET-XXX.retro.md"     # + one per <kind> file
   ```

   The target MUST be a fully-expanded **absolute** path — never relative (worktrees sit at varying depths: `.claude/worktrees/<wt>/`, even `/private/tmp/...`) and never a literal `~` (it does not expand inside a symlink and dangles). Because it is absolute, the symlink resolves in main and in every worktree and survives a worktree→main merge unchanged. **Never** place the symlink in the project root and **never** write a real retro file in the project. Skip if already correct; when working **only** in common with no app checkout, note `N/A` in state.

**Discovery helpers:**

```bash
# Canonical real file (authoritative: the `common` archive)
ls ${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro*.md 2>/dev/null
ls ${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX*.retro*.md 2>/dev/null   # legacy / phased / slugged names

# Project symlink (co-located with plan artifacts; points into common)
ls -l .jswarm/plans/TICKET-XXX/TICKET-XXX.retro*.md 2>/dev/null
```

**No ticket** (generic precompact): record **Retro: N/A** in `.jswarm/state/precompact-state.md` unless the session produced a portfolio-wide lesson that belongs in an existing common retro by explicit user/repo convention; do not invent orphan filenames.

Retro edits must be **committed in their target repo before the banner** — never leave a touched retro real file or project symlink unstaged. When both repos changed, **both** commits are required (see table below).

### Surface 3: Git Commit

**Repo-aware staging** (resolve `pwd` / git root before committing):

| Working tree | Commit in this repo | Retro real file (`docs/retros/`) |
|---|---|---|
| **`${JSWARM_HOME:-$HOME/dev/jswarm}`** (common-only ticket, or retro written/appended here) | Retro real file + state + plan artifacts | **Same commit** |
| **Application project** (hai-sim-engine, etc.) | State + plan artifacts + retro **symlinks** under `.jswarm/plans/TICKET-XXX/` | **Separate commit in `${JSWARM_HOME:-$HOME/dev/jswarm}` first** |

Lite's scoped retro-only commit uses this same dual-repo order but stages **only** the touched retro real files and project retro symlinks — never state, plan, matrix, or other checkpoint surfaces.

**Dual-repo order (application ticket with retro content this interval):**

1. `cd ${JSWARM_HOME:-$HOME/dev/jswarm}` — stage `docs/retros/TICKET-XXX.retro*.md` (+ any common-side plan/state edits); commit.
2. From the project root — `ln -sfn` the symlink if needed; stage state, plan artifacts, and `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro*.md` symlinks; commit.

Record **both** SHAs in the state file when dual-repo (`Common commit:` / `Project commit:`). When retro is **`N/A — no new lessons`**, a single-repo checkpoint commit is enough.

**Actions:**
1. Stage only this repo's checkpoint artifacts — **never `git add -A`** (respect parallel-session WIP).
2. Commit with conventional message:

```
checkpoint(ticket): phase N — [brief summary]

Pre-compact checkpoint. State: .jswarm/plans/TICKET-XXX/TICKET-XXX.state.md
```

3. Record the commit SHA (or both SHAs when dual-repo) in the state file

**Nothing to commit:** Write state file anyway. Use `--allow-empty` if needed to create the checkpoint marker commit, but prefer committing real changes when they exist.

### Surface 4: TaskList

**Actions:**
- Mark all truly finished tasks as `completed`
- Ensure current task is `in_progress`
- Cancel stale tasks no longer relevant
- Verify no tasks are stuck `in_progress` that are actually done

**Key rule:** TaskList survives compaction natively. It must match reality.

---

## Step 2.9: Role-aligned PM/PO rollup comments (opt-in)

**Gate:** run ONLY when the plan frontmatter has `rollup_comments: on` (absent or `off` ⇒ skip entirely — post nothing, write nothing). **`/jPrecompact --lite` never runs this step** — rollups are a full-checkpoint surface only.

**Not implemented in this distribution:** the PO/PM-altitude rendering this step depends on is not available here (see `/jPlan`'s Q10). With the flag left at its default `off`, this step adds no obligation; a project that sets it `on` gets a skipped step, not a working rollup.

---

## Step 3: Optional Surfaces

### Notepad (short-term working variables)

If there are active working variables, in-progress calculations, or short-lived context that doesn't warrant the state file, append them to a native `.jswarm` scratch notepad:

```bash
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) TICKET-XXX: [working context]" >> .jswarm/state/notepad-TICKET-XXX.md
```

---

## Step 4: Emit Checkpoint Banner

After writing all surfaces, emit this fixed-format banner:

```
━━━ CONTEXT CHECKPOINT ━━━
Done:   <bulleted list of deliverables + file paths + commit SHAs>
Artifacts updated: <list of planning files>; plan sections: <A/C, matrix, phases, Required Reading, …>
Retro:  ${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro[.<kind>].md updated / N/A; project symlink .jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md (absolute → common): OK / N/A
Next:   <bulleted list of immediate next actions>
State:  .jswarm/plans/TICKET-XXX/TICKET-XXX.state.md (N lines)
Commit: <sha> <subject>  (dual-repo: Common <sha> + Project <sha>)

Recommend /compact now. On resume, my first action is Read <state-file> + TaskList.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Banner rules:**
- Fixed format — user's eye knows where to look
- Concrete references — file paths, commit SHAs, line counts
- Keep under ~25 lines

---

## Step 5: Discipline Guarantees (VERIFICATION)

Before declaring the checkpoint complete, verify ALL:

1. ☐ State file exists and is under 200 lines
2. ☐ **Master plan lean maintenance:** A/C checkboxes, A/C-to-Test / UAT matrix rows, phase tasks + exit criteria, Testing Strategy status, Status Updates row, Required Reading (added/removed load-bearing docs), Critical Files, and `Last Updated` reflect this interval — **no new prose blocks** added
3. ☐ Other planning artifacts updated (specs, UAT scenarios, UAT test doc, defect trackers — or note absence of each)
3a. ☐ **Rules/tools maintenance window:** Step 2.0-rules-tools launched in background when applicable; task ID recorded; checkpoint did not wait for it or let it delay later surfaces/commit/banner
4. ☐ **Retro (`/jClose`-aligned; full and lite):** real file written/appended at `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro[.<kind>].md` (full template on create, **Changelog** row on append, **no overwrite** of existing body) or state records `N/A — no new lessons`; standing feature-feedback kind-files written only when used-and-have-feedback; **common retro committed in `common` when content changed**; project symlink at `.jswarm/plans/TICKET-XXX/` is absolute + resolves or N/A; **no real retro file in the project, no symlink in the project root**; nothing left unstaged in either repo for touched retros
5. ☐ Git commit(s) capture working-tree changes (no uncommitted state in the repo(s) touched). Lite: this applies only to touched retros (scoped retro-only commit); state and plan Status Updates may remain uncommitted
6. ☐ TaskList matches reality (no false `in_progress` items). Lite skips this guarantee
7. ☐ Checkpoint banner emitted to user

**If any guarantee fails → fix it before emitting the banner.**

---

## Post-Compact Resume Protocol

After compaction, the agent's **first actions** must be:

1. **Read the state file** (`.jswarm/plans/TICKET-XXX/TICKET-XXX.state.md`, or `.jswarm/state/precompact-state.md` when no ticket)
2. **Check TaskList** — confirm expected phase/task status
3. **Read the plan file** (if exists) — full scope context
4. **If the state file lists a canonical retro path touched** — skim that file for the latest Precompact section or Changelog entry

Then announce:
> Resuming at Phase N. Next action: X. Prior checkpoint was commit <sha>.

**If state file is missing or stale:**
1. `git log --oneline -20` — see recent commits
2. Check expected files (dual-path):
   ```bash
   ls .jswarm/plans/TICKET-XXX.plan.*.md .jswarm/plans/TICKET-XXX/ 2>/dev/null   # new location
   ls docs/plans/TICKET-XXX-*                                        2>/dev/null   # legacy
   ```
3. Ask user: "I'm resuming — last I recall we were at Phase N, is that right?"

Never silently assume state after a compaction. Confirm.

---

## When to Use /jPrecompact (Triggers)

| Trigger | Example | Why |
|---------|---------|-----|
| User asks to compact | "Run /compact" or "Context getting long" | Ensures state is saved before compaction |
| End of a major phase | "Phase 2 complete" | Natural boundary; next phase is separate |
| Every ~60 min of active execution | Long single phase with many sub-steps | Prevents unbounded transcript growth |
| Before an irreversible operation | `git mv`, destructive migrations, Jira label sprays | Commits "before" state for rollback |
| After a large background agent returns | Critic with 2000-word payload | Payload absorbed; safe to compact |
| User approves scope change | Accepting critic findings; adjusting phase order | Pins new consensus to durable storage |
| Before a long batch of tool calls | Parallel Jira API writes; multi-file bulk edits | Reduces blast radius of crash mid-batch |
| Proactive — feeling context pressure | Agent notices it's re-reading files it already read | Self-aware compaction trigger |

**Don't checkpoint:** every tool call, mid-conversation while user is actively answering, when no new durable decisions have landed.

---

## When to Skip /jPrecompact

- Single-phase ticket completing in <15 min
- <3 file edits with no background agents
- Pure read-only investigation (nothing durable to checkpoint)
- User is actively driving each step (no long-running autonomous work)

---

## Failure Recovery

| Failure | Recovery |
|---------|----------|
| State file never written, user compacted | Reconstruct from `git log` + working tree; ask user to confirm; write `.jswarm/plans/TICKET-XXX/TICKET-XXX.state.md` immediately |
| Banner emitted but commit failed | Re-stage + re-commit; update state file with new SHA |
| User compacts before background agent finishes | Agent continues; completion notification lands in compacted context. Reconnect via agent ID from state file |
| Plan file and state file disagree | State file is authoritative for execution status; plan is authoritative for scope. Fix whichever drifted |
| Plan gained prose blocks at checkpoint | Strip narrative back to table/checkbox deltas; move lessons to retro; re-verify lean maintenance checklist |
| No ticket-specific state file exists | Use the missing-state recovery above for tickets; for no-ticket checkpoints, write `.jswarm/state/precompact-state.md` with git context |

---

## What Can and Cannot Be Automated

| Can automate | Cannot automate |
|-------------|----------------|
| Writing state files, plan updates, canonical retros (`TICKET-XXX.retro.md` / `TICKET-XXX.retro.<kind>.md` per `/jClose`) | **Invoking `/compact`** — only user can type it |
| Running `git commit` at boundaries | Knowing exact token usage |
| Updating TaskList status | Preventing tool-result payload loss (write to disk before compact) |
| Emitting checkpoint banner | Recovering background agent state across compaction (write agent IDs to state file) |
| Re-reading state file on resume | |

**Mitigation for agent IDs:** Write background agent task IDs to the state file before any compaction that could orphan them.

---

## Rules

1. **Full mode never skips a surface.** All four full-checkpoint surfaces must be written. If a surface is inapplicable (no plan file), document WHY it was skipped in the state file. Lite mode is the only exception and must write only the allowed lite surfaces.
2. **State file is the resume anchor.** After compaction, the agent reads `.jswarm/plans/TICKET-XXX/TICKET-XXX.state.md` (or `.jswarm/state/precompact-state.md` when no ticket). Keep it under 200 lines.
3. **Commit SHA goes in the state file for full mode.** Cross-reference is mandatory after a full checkpoint commit. In lite mode, record `Commit: N/A — lite mode (no checkpoint commit)` unless this lite run made a scoped retro-only commit (record that SHA) or an earlier commit SHA is being carried forward as context.
4. **Background agent IDs go in the state file.** Orphaned agents = lost work.
5. **Banner is mandatory.** User needs to see what was saved and what's next.
6. **Retro (`/jClose`-aligned, single rule; full and lite).** The real file always lives in the common archive `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro[.<kind>].md`; the project holds an **absolute, worktree-safe** symlink at `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md`. Append-only with a Changelog row when the file exists, full template when creating; then create/refresh the project symlink (absolute target, never the project root, never a real retro file in the project). Or record **`N/A — no new lessons`** in the state file — never silent skip. Also apply the **Standing feature-feedback retro directive**: separate kind-files for recently updated features (see the standing roster) only when used this interval and you have feedback, weighing benefits (re-work and risk avoided) against costs (time and tokens). Precompact does **not** replace final `/jClose` tracker/plan polish. Lite files retros and may make a scoped retro-only commit.
7. **Plan lean maintenance (Surface 2; full mode).** Every full checkpoint updates the master plan execution snapshot: A/C, traceability matrix, UAT matrix, phases/tasks, Testing Strategy, Status Updates, **Required Reading**, Critical Files, Last Updated — status deltas only. No new A/C/tasks/phases without user approval; no Overview/Risk rewrites; no retro duplication in the plan. Lite mode writes only a scoped Status Updates row.

---

## ColGREP Index Lifecycle Check (no-badgering)

During checkpoint (after the plan/state is written; full mode), run the read-only ColGREP lifecycle check. It silently lets the certain-only evictor handle stale/orphan indices and surfaces ONE consolidated question only for genuinely ambiguous candidates — and only once per unchanged set (receipt-backed; no badgering):

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/colgrep_generation_reaper.py \
  sweep-certain --caller precompact --worktree "$PWD" --max-targets 3 --json || true

${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/colgrep_index_lifecycle.py \
  check --command precompact --json || echo '{"note": "ColGREP unavailable, continuing"}'
```

ColGREP is optional; an uninstalled or erroring check must never block the checkpoint.

- `question` null or `suppressed: true`, or the command itself failed to run → proceed silently; no action needed.
- `question` present → surface its `prompt` + each `candidate` (with its `reasons`) using the actions **keep-protect / delete-now / defer / inspect-details**. Advisory only — never block the checkpoint, and never auto-`--apply` cleanup from a lifecycle command (deletion stays operator-gated; dry-run is the default).

After the eviction check, run the **ticket-scoped ColGREP index-health check** so the checkpoint records whether *this ticket's* index is fresh, lagging, or stalled — scoped to the resolved ticket, never a blanket all-index scan:

```bash
# Substitute $TICKET with the ACTIVE ticket key (from Step 1a). If the active
# ticket is unknown, OMIT --ticket entirely so the CLI falls back to
# session-binding -> session-title -> git-branch resolution: an explicit --ticket
# WINS over fallback, so a literal "TICKET-XXX" placeholder would force
# health_state=unknown. Use --repo-root "$PWD" (the resolved repo root), not ".".
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/colgrep_index_lag_eta.py \
  health --command precompact --ticket "$TICKET" --repo-root "$PWD" --json || true
```

- **Fail-open:** the health CLI always exits 0 and emits `colgrep.ticket-index-health.v1`; the trailing `|| true` guarantees a health-command failure NEVER blocks the checkpoint.
- **Order:** runs immediately AFTER the eviction `check` above (evict/adjudicate first, then report this ticket's freshness).
- **Record the health advisory into checkpoint state:** fold the resulting **health advisory** (its `health_state`, plus any outstanding-file count / ETA / `stalled` note) into the Surface 1 **state** file under a short "ColGREP index health" line — advisory only; it never gates the checkpoint.

---

## Changelog

| Date       | Author   | Change |
|------------|----------|--------|
| 2026-08-31 | Grok 4.6 | **`/jPrecompact --lite` runs `/jStatus --lite`, not full `/jStatus`.** Lite currency gate inspects `.jstatus.quick.latest.md`; stale/missing ⇒ `/jStatus --lite` only. Full mode still inspects `.jstatus.latest.md` and re-renders with full `/jStatus`. Lite must not escalate to a standard-mode render when the quick template is absent — record the gap and continue. |
| 2026-08-30 | Fable 5  | **jStatus currency gate + heavy-use retro rule (owner-directed; full AND lite).** Before the state file is written, verify `.jstatus.latest.md` is accurate (provenance + cycle header vs governing files + material events); stale ⇒ run full `/jStatus` first. The state file links the report and never repeats its content — resume mechanics and post-render deltas only. Feature-feedback roster extended (fix cycle, fix-uat portal) plus a heavy-use rule: any skill/mechanism in heavy use this interval gets detailed retro coverage when there is feedback; heavy use with owner corrections and no retro coverage is a checkpoint defect. |
| 2026-08-22 | Grok 4.6 | **Lite files retros + standing feature-feedback directive.** `/jPrecompact --lite` now files the ticket retro and separate kind-files for recently updated features (jGuardrail, jCheckin, test UAT lifecycle) only when those features were used this interval and there is feedback, weighing benefits (re-work and risk avoided) against costs (time and tokens). Lite may make a scoped retro-only commit; it still skips promotion review, matrix reconcile, checkpoint commit, TaskList, and broad plan maintenance, and it does not run the full-mode jCheckin telemetry re-extract or changelog-only "no new activity" rows. |
| 2026-08-12 | Opus 5 | **jCheckin enrolment freshness (Step 3.5) — full and lite.** `/jGo` enrols once and nothing re-enrolled, so the frozen evaluator kept answering truthfully against expired facts and the check-in gate never fired. `/jPrecompact` is the surface that actually recurs and has just reconciled the plan/state, so it now refreshes enrolment before the banner: eligibility/enrolment check, mandatory untrap-marker check first (a marker means report `suspended` and do NOT call `start` — re-enrolment is the only thing that clears a marker), quiet-boundary requirement with an explicit carve-out for the declared no-wait Step 2.0-rules-tools lane, then `/jCheckin start` with facts rebuilt from the current plan (identical = no-op, changed = deliberate revision bump, refusal blocks the banner). Added the outcome line to both banners, a lite allowed-write plus a matching lite prohibition on every other jCheckin write, discipline guarantee 6a, and corrected the retro step's now-false claim that precompact does nothing for jCheckin. Same pass, same defect class (documentation instructing a command that does not exist): the retro step no longer instructs calling the deferred, non-callable `/jCheckin checkpoint` — the check-in review row is read from the ticket evidence already captured at check-in time — and the telemetry breakout re-attributes the no-reconstruction rule to its real home, `checkin-review.md` `## Recording`. |
| 2026-07-13 | GPT-5.6 | **Fail-open rules/tools maintenance window.** Full-mode Surface 2 now launches a background/no-wait jOps lane to reconcile architecture rule/tool cards and the rules summary, flag catalog gaps, refresh healthy ColGREP project indexing, and mirror pipeline-trace matrix/render maintenance when mechanisms changed. The checkpoint never waits for this lane and helpers may not spawn further agents. |
| 2026-06-22 | GPT-5.5 | **Full-mode promotion gate + lite mode.** Promoted Step 1d from advisory UAT/NFR note to a blocking pre-surface promotion-review harness covering UAT, NFR, A/C-to-Test, and A/C checkboxes: developer must approve all, approve selected, deny, request changes, or abort before any full checkpoint surface is written. Added `/jPrecompact --lite` and `--fast` alias as a narrow continuity checkpoint that writes only the state file and a scoped plan Status Updates row; it skips promotion review, matrix reconcile, retro, commit, TaskList, artifact dispatch, and broad plan maintenance. Removed `--force`; the proceed confirmation and full-mode promotion harness are mandatory. Updated full-mode surface rules to make lite the explicit exception. |
| 2026-06-21 | -        | **Early UAT/NFR sign-off recommendation (Step 1d).** Added a read-only, non-blocking step at the start of the checkpoint that **adjudicates each** ceiling row (UAT 🟡 Ready, A/C-to-NFR 🟡, `Automated NFR: no` static-proof rows) by inspecting its cited evidence and giving a **per-item ✅ Promote / ⏸ Hold verdict** with the reason — not just a list — fail-safe to ⏸ Hold when evidence can't be confirmed (no false-green). Includes the "counts read 0/N until you sign off — DoD ladder caps automation at 🟡 and counts only 🟢" explanation and optional same-turn flip + Step 2.1 re-normalize on explicit sign-off. Surfaces the promote/hold call early instead of only at `/jClose`. |
| 2026-06-06 | -        | Repointed post-compaction carry-forward state to `.jswarm/plans/TICKET-XXX/TICKET-XXX.state.md` (ticket) / `.jswarm/state/precompact-state.md` (no ticket), and removed the legacy fallback so command state handling is `.jswarm`-only. Added memory-body one-line distillation rule for local includes. |
| 2026-06-05 | -        | **Local precompact include layering.** Added runtime discovery for optional project-local and ticket-local precompact includes, with precedence `global → project → ticket`, additive merge semantics, later-layer override for ordinary defaults, and non-weakenable safety/retro/plan-maintenance boundaries. |
| 2026-06-02 | Cursor | **Plan lean maintenance contract (Surface 2).** Mandatory master-plan checklist: A/C, A/C-to-Test matrix, UAT matrix, phases/tasks, Testing Strategy, Status Updates, Required Reading (+/− load-bearing docs such as architecture specs), Critical Files, Last Updated — table/checkbox deltas only, no prose bloat. Fixed flash-tasker prompt (was incorrectly forbidding A/C updates). State template + banner + Rule #7 + discipline guarantee #2; lean prompts for specs/UAT artifacts. |
| 2026-06-02 | Cursor | **Dual-repo commit workflow aligned with `/jClose`.** Added explicit 3-step authoring flow (write in common → commit common → symlink + commit project), `/retros --apply` drift repair pointer, repo-aware Surface 3 table (common-first order when both repos touch retro), dual SHA fields in state template + banner, and discipline guarantees for unstaged cross-repo retro work. Fixes the Surface 3 contradiction that implied a single commit could cover retro real files outside the cwd. |
| 2026-06-01 | Claude Opus 4.8 | **Retro model unified (supersedes the 2026-05-22 dual-location split).** Retro real file ALWAYS lives in `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/`; project ALWAYS holds an **absolute, worktree-safe** symlink at `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md` → the common file. Removed the new-location-real-file branch and the project-root-symlink branch across the state-file template, Surface-2/3, Rule #6, banner, discipline checklist, and discovery helpers (project-symlink step now `ln -sfn "$HOME/..."`). Matches `/retros` + `/jClose`; worktree merges carry the absolute symlink unchanged. Authority doc `docs/agent-system/agent-write-permissions.md` updated in lockstep. |
| 2026-05-22 | -        | Dual-path plan resolution (`.jswarm/plans/TICKET-XXX.plan.*.md` master + `.jswarm/plans/TICKET-XXX/` artifacts for tickets ≥ 2026-05-22; `docs/plans/` legacy fallback). Retro destination flipped: NEW tickets write retros to `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro.md`; LEGACY tickets (retro already exists in `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/`) keep appending there. Banner + Rule #6 + state-file template updated. Discovery helpers expanded for dual locations. Per canonical doc `docs/agent-system/agent-write-permissions.md`. |
| 2026-05-08 | -        | Retro naming aligned with `/jClose`: `TICKET-XXX.retro.md` default, `TICKET-XXX.retro.<kind>.md` for split narratives; multi-symlink. |
| 2026-04-21 | Sisyphus | Initial version — extracted from `/jGo` Auto-Context Management checkpoint protocol. Expanded Surface 2 from plan-file-only to all planning artifacts (specs, UAT scenarios, UAT test doc, defect trackers) with parallel agent dispatch. `/jGo` and `/jPlan` now reference this command instead of duplicating the protocol. |
