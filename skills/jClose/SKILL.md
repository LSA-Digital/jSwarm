---
name: jClose
description: Symlink to the global /jClose command that completes work, writes the retro, closes the Jira issue, and invokes /jMerge when applicable.
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/jClose/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy.
     COM-358 T2: /jClose has been renamed to /jClose. The old skills/jClose/SKILL.md
     survives as a thin compatibility alias that delegates here; it carries its own distinct catalog
     identity. The `ceremony: "close-ticket"` schema value is NOT renamed. -->

# /jClose — Complete Work and Close Jira

## Safety contract (COM-219 — destructive skill, agent-invocable)

- **Default is read-only / no-write.** Invoked with no args (or `--help`/`status`), this skill only inspects and reports; it performs NO write, apply, push, delete, remote, or trim.
- **Confirm before any mutation.** Before any state-changing mode, the invoker (human or agent) must obtain explicit confirmation — or run an approved preview/dry-run first and act only on that approved plan.
- **Agents are NOT locked out** (no `disable-model-invocation`); this contract — not frontmatter — is what gates writes, so the read-only path is freely usable and a mutation requires approval.


Close a ticket after `/jGo` has completed all work and (when applicable) invoke `/jMerge` to integrate the source branch to main.

> **Prerequisite:** `/jGo` enforces TDD, marks all tests 🟢, and flips plan status `ACTIVE → READY_FOR_MERGE` at last-phase completion. If plan still shows `ACTIVE` or any items incomplete, run `/jGo` first.

**Lifecycle:** `/jPlan` creates plan with `status: ACTIVE` → `/jGo` flips to `READY_FOR_MERGE` at completion → `/jClose` invokes `/jMerge` → flips to `DONE` after green merge. State machine canon: [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/state-machine.md`](../../docs/merge/state-machine.md).

---

## Global Command Project Localization

This is a global/shared command. Project-local rendered command files are not the source of truth.

Before executing any non-smoke instruction in this command:

1. Determine the active project root from the current working directory.
2. If `.claude/project-command-injections.yaml` exists, read `managed_commands.close-ticket.md`.
3. For each configured anchor whose marker appears in this command, read its `snippet_path` or inline `content` and treat that content as if it replaced the matching `<!-- inject:... -->` marker.
4. If a configured anchor is `required: true` but the marker is missing, the snippet is missing, or the snippet is empty, stop with `LOCALIZATION ERROR` and explain the missing anchor.
5. If no project manifest exists (or it has no `managed_commands.close-ticket.md` entry), continue with the global command body as-is — the inert `<!-- inject:... -->` markers are skipped and any gated step is a no-op (fail-open; unchanged for projects that have not opted in).

If invoked with `--localization-smoke`, do only the localization pass, print the project root, each configured anchor name, whether it resolved, and the first non-empty line of each resolved snippet; then stop without running the normal command workflow.

---

## Step 0: Main-sync preflight (SILENT unless escalation needed)

Run the main-sync portion of [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/preflight.md`](../../docs/merge/preflight.md). Auto-resolve gitignored chaff + ahead-only push + behind-only pull silently. Halt + escalate ONLY for tracked-file dirt or diverged branch state. Emit one-line success log (`✓ Preflight: ...`) before Step 1. FAQ: `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/agent-faq.merge-readiness.md`.

Source-side preflight (commit + push the feature branch) is now `/jMerge` Step 0b's responsibility — `/jClose` does NOT preflight the source.

---

## Step 1: Identify ticket & load plan

```bash
git branch --show-current | grep -o 'TICKET-[0-9]*'
```

If not found, ask: **"Which ticket? [TICKET-XXX]"**

Load plan file (COM-91 dual-path; prefer new location, fall back to legacy):
1. `.jswarm/plans/TICKET-{XXX}.plan.*.md` (master, new — post-2026-05-22)
2. `docs/plans/TICKET-{XXX}-*.md` (legacy, pre-2026-05-22)

Resolve once; if the new master exists, use only `.jswarm/plans/TICKET-XXX/` for artifact reads/writes. **Never mix locations for one ticket.**

---

## Step 1.5: Refresh evidence surfaces before validation (BLOCKING post-check)

`/jPrecompact` normally keeps the plan evidence surfaces current. When a ticket goes directly from `/jGo` to `/jClose`, `/jClose` must run the same deterministic refresh before applying the close gate. This is **not** plan-status repair: do not flip `plan_status` here, and do not treat this step as a substitute for `/jGo` recording `5.closed.ready_for_merge`.

For new-location tickets, delegate the deterministic refresh to `/update-ticket` — the `close-refresh` section set's refresh half (`migrate → rebuild-rows → reconcile-status → count`, fail-open). The wrapper is common-owned and invoked by **absolute common path** (it resolves its engines from `common`, not the target project), with `--repo-root .` naming this ticket's project — so this works in every project:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_ticket/cli.py --ticket TICKET-XXX --repo-root . --sections migrate,rebuild-rows,reconcile-status,count || true
```

For legacy `docs/plans/` tickets, the deterministic slice pipeline may no-op; still apply the strict post-check below against the loaded plan.

**R3 — silent-0/N / un-seedable close gate (BLOCKING, COM-167).** The reconcile chain above is fail-open by design (a missing result doc or un-seedable matrix never blocks the refresh). This gate is the fail-loud boundary: it blocks closing a ticket that reached a closing stage at silent `0/N` or with an un-seedable UAT/NFR matrix — exactly the COM-176/COM-197 failure (a ticket merged/ready showing `0/N` with nobody alerted). Run the R3 close gate via `/update-ticket` — the `close-refresh` section set's fail-loud tail (the `audit` section, stage `close`); it **propagates exit 1** to block closure (never `|| true`):

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_ticket/cli.py --ticket TICKET-XXX --repo-root . --sections audit --audit-stage close
```

`BLOCK block-zero-of-n` / `BLOCK block-unseedable` (exit 1) is a hard blocker — present it with the Step 2 blocker format and route back to `/jGo TICKET-XXX` to author/reconcile the result docs (`TICKET-XXX.uat-test.md` / `TICKET-XXX.nfr-test.md`) until the matrix counts move off `🔴`, or to fix a non-canonical matrix the engine refuses to seed. `OK warn` (missing result files on an incomplete dimension / declared-applicable-no-matrix) is surfaced but non-blocking. Pre-implementation, abandoned-terminal, and non-applicable tickets self-skip (`OK skip`) — the R6 guardrail: `0/N` before implementation is EXPECTED, never a close blocker.

After the refresh, verify every plan surface maintained by `/jPrecompact` Surface 2 / `/update-plan` is current and terminal before Step 2:

- `## Acceptance Criteria`: all intended-to-close A/C are checked.
- `## A/C-to-Test Traceability Matrix`: every A/C has named test/evidence and a terminal status.
- `## A/C-to-NFR Traceability Matrix` and `## UAT-Scenario Traceability Matrix` when present or declared applicable: rows are terminal, and `nfr_complete` / `uat_complete` frontmatter matches the refreshed matrix counts.
- UAT/per-phase test rows, implementation phases/tasks/exit criteria, and Testing Strategy rows: terminal or explicitly non-applicable.
- Status Updates, Required Reading, Critical Files, and Last Updated: no stale placeholders or missing load-bearing evidence from the completed work.

Terminal means `🟢` / `✅`, or explicit `N/A`, `Deferred`, or `Canceled` with a reason and follow-up/reference. `🟡`, `🟠`, `🔴`, blank cells, `TBD`, `New`, stale evidence links, unparseable result docs, or missing rows are blockers. Present blockers using the Step 2 blocker format and route back to `/jGo TICKET-XXX` or the relevant closeout doc-update step.

---

## Step 2: Validate completion (BLOCKING)

<!-- inject:project-lifecycle-checklist -->

Run the teardown close gate in this standard form. Pass `--worktree-relpath`, `--branch`, and `--compose-project` every time; they are required for the legacy receiptless path and ignored when a receipt exists because the envelope wins.

**Live-worktree structural defer:** Before invoking the gate, classify the checkout. When `/jClose` is running from a live feature worktree (`branch != main` and the Git top-level is under `.claude/worktrees/`) and `/jMerge` has not yet completed its teardown-producing Step 10.8 family, the Step-2 teardown gate **self-skips** and records the exact typed result `DEFERRED-TO-MERGE` wherever close-flow gate results are recorded. This fixes the false structural block from a live worktree: the gate's postcondition is produced later in the same flow by `/jMerge`. `DEFERRED-TO-MERGE` is a structural defer, not a pass or waiver; the `/jMerge` Step 10.8 family must perform the deferred post-merge verification, and close is not fully discharged until that verification is recorded. If the postcondition already holds (merge already completed, or this is a non-worktree close), evaluate the gate exactly as today. Never emit or use `DEFERRED-TO-MERGE` in any other situation, and never use it to waive an actual teardown failure. Only when this defer condition does not apply, run the command below.

```bash
"$COMMON_ROOT/.venv/bin/python" "$COMMON_ROOT/scripts/jinfra_close_gate.py" \
    --ticket "$TICKET" --cwd "$PRIMARY_CHECKOUT" \
    --worktree-relpath ".claude/worktrees/$WORKTREE_NAME" \
    --branch "feat/$TICKET" --compose-project "$WORKTREE_NAME"
```

Only exit `0` permits close: `CLOSE_GATE_OK`, `CLOSE_GATE_OK_YELLOW`, and `CLOSE_GATE_SKIPPED` all permit. The structural self-skip above records `DEFERRED-TO-MERGE` instead of invoking this gate and permits the pre-merge close flow only when the required `/jMerge` Step 10.8 post-merge verification is carried through. Surface `CLOSE_GATE_OK_YELLOW`'s `[WARN] lifecycle.result: unavailable` in the close summary. Exit `1`, `2`, `3`, or `4` blocks close.

Apply the checklist in [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/close-ticket/validation-checklist.md`](../../docs/close-ticket/validation-checklist.md). High-level gate categories:

| Block | What it checks | On blocker |
|---|---|---|
| **1 — Implementation completeness** | A/C checkboxes, A/C-to-Test matrix, all lifecycle-maintained evidence surfaces, plan status already reconciled to `5.closed.ready_for_merge` | A/C/test/evidence gaps → `/jGo TICKET-XXX` or the relevant closeout doc-update step. Missing/invalid/planning/implementation/all-A-C-met plan_status values, including `3.implementation.phase_*` and `4.closed.all_ac_met`, block closeout and route back to `/jGo TICKET-XXX`; `4.closed.all_ac_met` is not ready for merge. Legacy pre-2026-05-11 plans may use the explicit escape hatch `proceed anyway? [yes/no]`. **Commit/push state is NOT checked here** — `/jMerge` Step 0b is authoritative; uncommitted/unpushed source is `/jMerge`'s concern, not `/jClose`'s (Fix #5a, 2026-05-11). |
| **2 — Worktree state** (OpenCode) | safe-to-cleanup check + typed confirmation + STATUS=0 + `[STATE] CLEANUP_OK` | Run `/worktree-cleanup` from main checkout |
| **3 — UAT completeness** (when `Automated UAT: yes`) | UAT report, mode/driver declaration, overlay/runtime monitor evidence, per-phase UAT gate, script freshness | `/jGo` (Plan Completion reruns mode-correct UAT) |
| **3a — UAT round tracking readiness** (when `UAT round tracking: on`) | Bug Master Ledger has no `1.OPEN` rows remaining; the round file (`TICKET-XXX.UAT-CURRENT-ROUND.md`) is terminal, or absent if never opened; every deferral has a named home (ticket or destination) per the `2.DEFERRED→TICKET` grammar | route back to `/jFix TICKET-XXX` to close remaining `1.OPEN` rows or name a deferral home |
| **4 — Regression / E2E policy** | per-ticket promotion artifact OR explicit deferral note; deterministic test data | `/jGo` |
| **5 — Catalog freshness** | TEST_CATALOG validate mode; `Relevant UAT` per Regression row | `/jGo` Step 3.1 catalog regen |
| **5a — Test completeness** | close-time test-catalog completeness gate for touched-but-uncited tests; `completeness_gate.py --stage close` must BLOCK on any orphan unless the plan records an explicit deferral | cite the test in catalog/traceability or add the explicit deferral before close |
| **5b — Component/artifact governance (COM-114/COM-123)** | **Bookkeeping (unchanged, Step 3):** for each id in the plan's `components:` frontmatter, flip the component record `status` (`draft → active`) when this ticket completes it; re-run `build_catalog.py` to refresh the GENERATED `contributing_tickets[]` reverse index (NEVER hand-edit that field). **Capability files this ticket ADDS must be CLASSIFIED per the value-based taxonomy (package → feature → component → artifact) and slotted in BOTH the component `artifacts[]` AND the owning feature's `realized_by` — status-flipping pre-existing components is not enough; follow the Account recipe in step 2.6 and require `build_catalog --dry-run` exit 0 (the coverage gate does not catch `COMPONENT-ARTIFACT-ESCAPE`).** **Governance gate (localized — see step 2.6 below):** when a project wires `managed_commands.close-ticket.md` (today: common only), the gate BLOCKS close on this ticket's unaccounted + undeferred delta artifacts (account-or-defer); absent that manifest entry the anchor is inert → no gate (fail-open, unchanged). | step 2.6 gate + `build_catalog.py` regen + `/devops-maint component-health` (mode 32) |
| **5c — Controlled-config one-home (COM-176)** | When this ticket added, retired, or renamed a skill/hook/command/rule master under `docs/_CONTROLLED_CONFIG/dotclaude/**`, regenerate catalog-authoritative inventory/homing and require `catalog_one_home.py validate-all` exit 0. Tickets with no matching master delta self-skip (fail-open). | step 2.7 gate; non-green routes back to `/jGo TICKET-XXX` and blocks close |
| **6 — Architecture freshness** | scenario merge-back to official inventory, or deferral note | Resolve before closure (Step 3.3a) |
| **7 — Feature plans only** | zero OPEN in `TICKET-XXX-integr-fixes.md` + `TICKET-XXX-pe2e-fixes.md` | `/jGo` |

The full row-level definitions, evidence requirements, and override semantics live in the checklist file. The command file presents them as block-level gates to keep this file readable.

**Feature defect file check:** If the plan header contains `Integration Defects:` or `PE2E Defects:` links, read those files and check the Summary table. Any phase with OPEN > 0 is a blocker. Skip for non-Feature plans.

**Blocker presentation format:**

```
❌ Cannot close TICKET-XXX — incomplete items:

**A/C (Block 1):** [ ] A/C 2: [description]
**Tests (Block 1):** test_foo: 🆕 New
**Evidence surfaces (Block 1):** A/C-to-Test row AC-4 is 🔴 / NFR row NFR-XYZ is 🟡 Ready / Testing Strategy row lacks final evidence
**Plan status (Block 1):** 3.implementation.phase_N or 4.closed.all_ac_met — /jGo verification completion did not record 5.closed.ready_for_merge
**UAT (Block 3):** Live-show claimed but evidence is headless

Run `/jGo TICKET-XXX` first, or proceed anyway? [yes/no]
```

The normal close path must not repair a stale plan_status late. `/jClose` proceeds only when the plan is already reconciled to `5.closed.ready_for_merge`, except for the explicit legacy escape hatch; default is refuse.

### 2.6 — Component/artifact governance gate (COM-123 — localized, common-only)

> Project-localized via the preamble above. When the active project's `.claude/project-command-injections.yaml` configures `managed_commands.close-ticket.md` with the `project-component-governance-gate` anchor (today: **common only**), the snippet below replaces the marker and runs a BLOCKING ticket-delta coverage gate (account-or-defer). When no such manifest entry exists, the marker is inert and this step is a no-op (fail-open — other projects' close path is unchanged). This replaces the old fail-open "bookkeeping never blocks" posture of Block 5b for opted-in projects.

<!-- inject:project-component-governance-gate -->

<!-- inject:project-jregister-registration -->

### 2.7 — Controlled-config one-home close gate (COM-176 — localized, common-only)

Resolve the active Git root and canonical common root first. If they are not the same physical directory, print `SKIP controlled-config one-home — common-only gate` and proceed with exit 0. After this guard, invoke the common `.venv/bin/python` and scripts by absolute path so another project's similarly named files cannot select the wrong tooling.

Run this gate only when **THIS ticket's own git delta** added, retired, or renamed a controlled-config skill/hook/command/rule master under `docs/_CONTROLLED_CONFIG/dotclaude/**`. This is a membership/topology gate, so ordinary content-only `M` edits do not trigger it. Match `A`, `D`, and both sides of `R*` records whose path matches:

```text
^docs/_CONTROLLED_CONFIG/dotclaude/[^/]+/(skills|hooks|commands|rules)/
```

Determine the ticket-owned delta with the same checkout-aware convention as Step 2.6. In an isolated feature branch/worktree, union the source-base-to-`HEAD` name-status diff, staged/unstaged `HEAD` diff, and untracked paths (`git ls-files --others --exclude-standard`, treated as `A`). On shared `main`, union exact-ticket-key commit records (`git diff-tree --root --name-status -M --diff-filter=ADR`), the captured ticket delta manifest, and any explicitly attributed current-ticket working-tree candidates. If matching uncommitted candidates are not manifest-attributed, do not silently skip or absorb them: present only those candidates for `current ticket` versus `unrelated` attribution, persist current-ticket selections in the delta manifest, and trigger only from attributed records. Explicitly unrelated candidates remain fail-open. Do not let sibling commits or unattributed working-tree dirt trigger this ticket's gate.

If no matching attributed record exists, print `SKIP controlled-config one-home — no skill/hook/command/rule master membership change` and proceed with exit 0. This is the required **fail-open localization** for unrelated closes; it adds no regeneration or validation work to tickets that did not change controlled-config master membership.

When triggered, run the existing source-side generators in their catalog-authoritative order, then validate. These commands intentionally do **not** run `deploy.py apply`, `--capture`, or mutate live `~/.claude` targets:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/controlled_config/build_inventory.py
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/controlled_config/generate_homing_map.py
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/controlled_config/catalog_one_home.py validate-all --repo-root ${JSWARM_HOME:-$HOME/dev/jswarm}
```

Run `jswarm/catalog/build_catalog.py` first only if this ticket also changed catalog sources and the normal catalog freshness/build check says the generated catalog read models are stale; it is not otherwise a prerequisite. If the same `target_relpath` was intentionally renamed between `user` and `repo.common`, and both rename sides are attributed to this ticket, reconcile only that target's stale generated homing row before invoking `generate_homing_map.py`, preserving every unrelated row and its metadata; an unattributed context disagreement blocks and escalates rather than auto-normalizing. Interpret each command's actual exit code directly (no masking pipe). Exit 0 from all three means proceed. Any nonzero generator or `validate-all` result **BLOCKS close**: present it with the Step 2 blocker format, route back to `/jGo TICKET-XXX`, regenerate/fix the authoritative catalog or masters, explicitly commit the three generated artifacts (`.jswarm/plans/COM-176/COM-176.inventory.json`, `.jswarm/plans/COM-176/COM-176.inventory.md`, `.jswarm/plans/COM-176/COM-176.shared-artifact-homing.yaml`), and rerun `/jClose`.

### 2.8 — Deploy-verify gate (COM-202 — `/jdeploy --verify`, no-write, common-wired)

After registration, run the inverse-gap deploy-verify gate for the **producing** ticket so a declared-but-undeployed required artifact fails *this* ticket's close — the inverse of the historical incident where the gap surfaced only during an unrelated downstream close. The gate is **no-write** (it runs `/jdeploy --verify` = plan + inverse-gap only; it never materializes a target) and records `jdeploy.report.v1` evidence:

```bash
PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.jdeploy.close_ticket_gate \
  --ticket TICKET-XXX --repo-root "$PWD" \
  --evidence-dir ".jswarm/plans/TICKET-XXX/evidence/close-ticket"
```

Interpret the **exit code**, not stdout text: `0` = proceed (every declared deployment resolves, **or** the gate is inert/fail-open because no deploy set resolved — an unwired non-common project, or no saved jregister preview at `.jswarm/plans/TICKET-XXX/TICKET-XXX.jregister-preview.json` and no explicit `--closure`). Any non-zero exit **BLOCKS** close: `1` = a required target is missing/dangling/stale/wrong-type/unverifiable (see the recorded report's `inverse_gap.blocking_findings`), `2` = untrusted closure, `3` = scope-fence violation, `4` = unusable deploy-set input (an invalid ticket key, or a saved preview / explicit closure that could not be loaded). Resolve the deployment (run `/jdeploy --since TICKET-XXX` or `/jdeploy apply`) and re-run the gate before closing. The gate consumes the saved jregister preview / explicit closure only — it never infers a deploy set from git history.

### 2.9 — Optimization call-out (protocol change 2026-08-03 — COM-294 degradation)

<!-- joptimize:call-out -->
Before finalization, preview the ticket's optimization certification through the cwd-safe wrapper: `${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/joptimize/joptimize certify-check --project-root "$PWD" --ticket TICKET-XXX --preview --format json`. Never apply automatically. If it returns typed `{"outcome":"substrate-unavailable","missing":[...],"bootstrap_hint":"..."}` — or an older equivalent such as missing authority, missing gate ledger, cwd/module/import failure — append one visible line: `jOptimize: substrate-unavailable; missing=<pieces>; bootstrap_hint=<hint or inferred next command>` and proceed. Never treat failure as a silent no-op gate or block ticket work on missing optimization substrate.

### 2.10 — Nonblocking check-in telemetry

Before finalization, invoke `${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/joptimize/checkin_runtime.py --lifecycle-boundary --caller jClose --project-root "$PWD" --context-file "$CHECKIN_CONTEXT" --common-root "$COMMON_ROOT" --boundary-id "$BOUNDARY_ID" --format json`. It ensures this checkout's canonical enrollment, appends the close boundary event, and repairs the project read-only views in one process; record/warn/continue. If common is unavailable, accept the typed event-loss result and do not create a local spool or copy attempt.

### 2.11 — Check-in decision corpus, close-time collection

Collect the ticket's whole check-in cycle **before** finalization, because finalization and the F-54 garbage collector destroy the correlation linkage this corpus exists to preserve. Run it from the consuming checkout:

```bash
PYTHONSAFEPATH=1 PYTHONPATH="$COMMON_ROOT/scripts" "$COMMON_ROOT/.venv/bin/python" -m checkin_decision_corpus.cli collect --repo-root "$PWD" --ticket "$TICKET" --mode close
```

All collection outcomes are advisory `nonblocking=true`; record/warn/continue before finalization. Reporting, when requested, creates a stable snapshot at report time; close does not create or maintain a reporting mirror.

---

## Step 3: Finalize

### 3.1 — Regenerate TEST_CATALOG.md (when applicable; detector-governed close gate)

Projects may have manually curated, hybrid, or generated catalogs. Handle the project-local contract through the common detector first, never by guessing from examples:

1. From the active repository root, run the detector with the common repo Python environment:
   ```bash
   PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/test_catalog_detector.py --repo-root "$PWD" --json
   ```
2. Interpret the detector result using both `mode` and `explicit_generated_adoption`. The close step must never infer explicit generated adoption merely because an implicit signal such as `.jswarm/e2e-manifest.json` exists.

   | Detector result | Close behavior |
   |---|---|
   | `mode: generated` and `explicit_generated_adoption: true` | The close step runs the generator as a subprocess using the detector-reported `attempted_generator_command` / project-declared generator. It blocks on `generator-failed-to-run`, stale `--check` diff, and invalid metadata / invalid coverage. A stale `--check` diff means re-run the generator in write mode, include the generated `tests/TEST_CATALOG.md` diff, then re-run `--check`; do not close while the check is stale. |
   | `mode: generated` and `explicit_generated_adoption: false` | Legacy implicit generator-only adoption: warn once, file the tool-failure report when the generator cannot run, and do not block closeout. This preserves fail-graceful behavior for projects discovered only from implicit generator hints. |
   | `mode: manual / hybrid` | Skip generated promotion. Update `tests/TEST_CATALOG.md` manually only if the active plan requires it; otherwise record why no generator applies. |
   | `mode: no-catalog` | Skip with no warning. Non-adopted projects have no engine/frontmatter contract; the lifecycle gate self-skips as a fail-open exit 0. |
   | `mode: invalid-metadata-blocks` | BLOCK closure with the detector/generator metadata error. |
   | `mode: command-unavailable-warns` | Treat as legacy implicit behavior unless paired with `explicit_generated_adoption: true`: warn once, file the tool-failure report, and continue for implicit adopters; BLOCK for explicit adopters. |

3. For explicit generated adopters, classify the subprocess result deterministically:
   - `generator-failed-to-run` (script path stale, command not found, container not available, dependency missing, etc.) → BLOCK closure with the failing command, stderr/stdout summary, and remediation path.
   - Generator ran and reported invalid metadata or invalid coverage (schema violations, missing required fields, `Relevant UAT` missing on Regression rows, malformed source rows) → BLOCK closure with the generator's specific error output.
   - Generator ran and reported stale markdown (`--check` diff) → BLOCK closure; instruct write-mode regeneration, include the rendered `tests/TEST_CATALOG.md` diff, and rerun `generate_test_catalog.py --check` before closing.
   - Generator ran clean → pass.
4. For implicit/non-explicit generated adopters, preserve the current fail-graceful path when a generator fails to run: emit a single one-line log, file `docs/tool-failure-reports/${TICKET}.toolfail.test-catalog-generator.YYYYMMDD.md`, and continue. Do not narrate the decision in chat beyond the one-line log.

Do not copy generator commands from other projects. A project-specific command is valid only when the detector reports it from project-declared metadata such as the TEST_CATALOG header, `.jswarm/e2e-manifest.json`, `tests/catalog/README.md`, or `CLAUDE.md`.

Run the close-time completeness gate **only when the ticket has a test-catalog / traceability surface to cite into** — i.e. the project is a test-catalog adopter (a `regression-inventory` exists) OR the plan carries an `## A/C-to-Test` traceability matrix/slice. **A non-adopted project with no citation surface skips the completeness gate (fail-open exit 0)** — specifically, no regression inventory and no `## A/C-to-Test` matrix/slice means the orphan-completeness gate must never block an unrelated, non-adopting ticket's close (Oracle #1). When it applies, it uses the final touched-test set, not committed files only, and must see committed, staged, unstaged, and untracked test paths against the merge base or recorded implementation base:

```bash
BASE_REF="$(git merge-base HEAD origin/main)"
PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/test-catalog/completeness_gate.py \
  --stage close \
  --plan "$PLAN_FILE" \
  --extract "$TRACEABILITY_SLICE" \
  --base "$BASE_REF" \
  ${SCOPE_TICKET_FLAG} \
  --json
```

**COM-198 — shared/interleaved-checkout scoping (`--scope-ticket`).** On a **shared checkout where `base..HEAD` interleaves sibling tickets' commits** (the common case for direct-on-`main` work), the raw `base..HEAD` touched-test set sweeps in OTHER tickets' tests and false-flags them as this ticket's orphans. In that case set `SCOPE_TICKET_FLAG="--scope-ticket $TICKET"` so the committed range is restricted to commits referencing this ticket (the same COM-133 scoping the component-coverage gate uses; working-tree changes stay included). On an **isolated feature branch / worktree**, leave `SCOPE_TICKET_FLAG=""` (omit it) — `base..HEAD` is already exactly the ticket's own range, and `--grep` scoping could wrongly drop a ticket commit whose message does not name the key. Decide once from the checkout: `SCOPE_TICKET_FLAG=""; git rev-parse --show-toplevel | grep -q '/.claude/worktrees/' || [ "$(git branch --show-current)" = "main" ] && SCOPE_TICKET_FLAG="--scope-ticket $TICKET"`.

At close stage, block on orphan touched-but-uncited tests unless each orphan is cited in the test catalog / traceability surfaces or covered by an explicit deferral in the plan or traceability slice. If no traceability slice was produced, omit `--extract` only when the plan itself carries the complete citations/deferrals. Route unresolved orphans back to `/jGo TICKET-XXX` or the relevant closeout doc-update step.

**SUPERSEDES note:** this detector-governed close gate is authoritative over the legacy "FAIL-GRACEFUL" Block 5 in `docs/close-ticket/validation-checklist.md`. For `explicit_generated_adoption: true` adopters, generator-failed-to-run, stale generated Markdown, and invalid metadata cases BLOCK closure; Block 5's fail-graceful behavior applies only to implicit/non-explicit adopters.

### 3.2 — Update plan file (status stays at READY_FOR_MERGE for now)

Set the status update narrative; the actual `READY_FOR_MERGE → DONE` flip happens AFTER `/jMerge` returns green (Step 3.10 below).

```markdown
**Status:** READY_FOR_MERGE (will flip to DONE after /jMerge succeeds)

## Status Updates
| Date | Status | Notes |
|------|--------|-------|
| YYYY-MM-DD | READY_FOR_MERGE → /jClose invoked | A/C verified, /jMerge dispatching |
```

### 3.2a — COM-97 Security & Compliance post-risk capture

Before parent-plan reconciliation, check the loaded plan for COM-97 baseline risk context captured by `/jPlan` under `## Outcome Metrics`, `## Security & Compliance Baseline Risk`, or `com97_security_compliance_baseline`.

For each dimension, security and compliance:

1. If baseline applicability is `none` or `N/A`, preserve the explicit non-applicable reason in the closeout notes; do not fabricate after-risk values.
2. If baseline applicability is `applicable`, capture `probability_after`, `impact_after`, `controls`, closeout comments, and final `why_rationale`.
3. Controls are recorded as id, label, and comment. `id` may be empty or provisional until COM-58 provides canonical IDs, but `label` and `comment` are required when controls affected the risk change.
4. If no before estimate exists, mark the outcome `before_missing` / `risk_reduced_not_computable`; do not treat missing before-state as zero and do not infer a baseline silently.
5. Do not persist raw prompt text, transcripts, or unrelated conversation content. Store normalized rationale and evidence references only.

When before and after values are available, create a schema-valid COM-97 record and call the repo-local writer with `.venv/bin/python`:

```bash
PYTHONPATH="scripts" .venv/bin/python - <<'PY'
from pathlib import Path
from jswarm.outcome_telemetry import compute_risk_metric, record_outcome_benefit

# Build one record per applicable dimension using the ticket plan values.
# Required: correlation, rollup_dimensions, evidence, outcome, dashboard_projection,
# why_rationale, lifecycle_capture, and controls when controls affected risk.
result = record_outcome_benefit(Path.cwd(), record, runtime="close-ticket")
print(result)
PY
```

The writer appends to `logs/jswarm-outcome-telemetry.ndjson` and is fail-open. If it returns `failed-open`, record the status in the plan or retro and continue closeout unless another closeout gate blocks.

### 3.3 — Update parent plan file (if applicable)

Check if this ticket has a parent Feature:
```bash
grep -l "TICKET-XXX" .jswarm/plans/*.plan.*.md .jswarm/plans/TICKET-XXX/ docs/plans/*.md 2>/dev/null | head -5
```

If a parent Feature plan exists:
- Update this story's status row in the **User Stories** table (🔴 → 🟢 Complete)
- Update **Acceptance Criteria** section (check off completed A/C)
- Update total effort (X complete / Y remaining)
- Update **Status Updates** table with completion note
- Update **Implementation Order** dependency diagram if this unblocks other stories

#### 3.3 boundary with `/feature-reconcile` (codified 2026-05-13 at HAS-381 event #21)

When the Feature uses Governance Rings methodology (presence of `.jswarm/plans/HAS-<FEATURE>/HAS-<FEATURE>.reconciliation-log.md` (new) or `docs/plans/HAS-<FEATURE>.reconciliation-log.md` (legacy) is the signal), the parent-plan reconciliation work splits **explicitly** between this Step 3.3 and the next `/feature-reconcile` event. The split is necessary because the orchestrator-session that runs `/jClose` has multi-Story context that organically pulls in master-plan + Mermaid + Gantt + strangler-class work, while `/feature-reconcile` owns the formal verification + log entry + AC graduation. Without an explicit boundary, work gets duplicated or (worse) silently dropped.

**`/jClose` Step 3.3 owns** (pre-merge additive — *land the substrate so it survives the merge*):

| Surface | What to do |
|---|---|
| §Tactical Tickets | NEW rows for any child Stories spawned at /jGo Phase 4 (e.g., follow-on Ring-0 lite Stories filed from the closing Story's scope) |
| §Sequencing View Mermaid flowchart | NEW nodes + edges for the spawned child Stories (`:::business` for unplanned, with `📋 ` prefix if Ring-2-planned) |
| §Sequencing View Gantt | NEW bars for spawned child Stories |
| §Sequencing View class directives | Add new node IDs to `unplanned` list |
| Strangler §2 (when applicable) | NEW Story-class taxonomy entry if the closing Story introduces a class not yet codified (e.g., `feature_level_governance_event`) |
| Retro append | `## /jClose TICKET-XXX — YYYY-MM-DD` section with closeout findings (per §3.5) |

**`/jClose` Step 3.10 owns** (post-merge styling — *flip styling to reflect merged reality*):

| Surface | What to do |
|---|---|
| §Tactical Tickets row (closing Story) | 🔵 PLAN DETAILED → 🟢 **Merged YYYY-MM-DD** with merge SHA |
| §Sequencing View Mermaid | `:::business` → `:::done` for the closing Story's node + label suffix `(merged YYYY-MM-DD)` |
| §Sequencing View Gantt | `:crit` → `:crit, done` (or default → `done`) for the closing Story's bar |
| §Phase Boundary Acceptance | Status field updates **but ONLY** to 🟡 Pre-graduation when AC requires post-/jMerge verification; full ✅ Met flips belong to `/feature-reconcile` |

**`/feature-reconcile` owns** (post-merge formal — *verify the substrate + record the event*):

| Surface | What to do |
|---|---|
| Step 1 5-bullet diff | Canonical INTENDED / DELIVERED / SCOPE-DELTA / SURPRISES / CARRY-FORWARD shape |
| Step 2.5 Goal Re-rationalization | 1-paragraph delta per Goal G1..GN |
| Steps 3-4 Blast radius scoring + Ring-1 update drafts | A/B/C-band per impacted sibling |
| Step 5 Developer approvals | B/C-band block + wait |
| Step 6 Sibling Ring-1 sub-entries | Short audit-pass blocks appended to each impacted sibling's plan file |
| Step 7 Leanness audit | 6 guardrails verification |
| Step 7.5 Strangler-pattern checkpoint | 6 invariants verification |
| Step 7.6 Coverage Allocation Checkpoint | 5 invariants verification (when matrix exists) — **THE primary post-merge governance gate** |
| Step 8 Reconciliation log entry | Append formal `## YYYY-MM-DD — TICKET-XXX closed; /feature-reconcile Nth event` entry |
| Step 9 Summary | AC graduation flips (🟡 → ✅ Met) + summary emit |

**Why this split:** /jClose Step 3.3 happens **pre-merge** in the orchestrator session — it must land additive surfaces (new rows, new nodes, new edges) so they integrate cleanly with the merge. The formal verification happens **post-merge** at /feature-reconcile because that's when main is the source of truth + cross-worktree concurrent state has settled. If /jClose attempted Step 7.6 / Step 8 work, it would either (a) duplicate /feature-reconcile or (b) run against pre-merge state and miss post-merge regressions like INV-4 violations.

**Originating event:** HAS-381 /feature-reconcile event #21 (HAS-431 close, 2026-05-13). The 21st event was the **first** to catch a Step 7.6 INV-4 violation (4 records allocated to closed HAS-407 during /jGo HAS-431 Phase 4 — resolved via NEW HAS-438 5th newly-filed Story). Codified inline at the same event into both this file and `feature-reconcile.md` (project-local) per user directive: *"clarify the ambiguity directly in the command files to ensure work is not duplicated."* 13th promotion-to-global candidate ratified inline rather than awaiting v1.11 retro pass.

**Memory pointers:** `feedback_only_orchestrator_edits_parent_files.md` PERMANENT (HAS-423 incident codification §4.5); `feedback_sequencing_view_is_accountability_substrate.md` PERMANENT (event #18 codification — Sequencing View as first-class output gate).

### 3.3a — Merge ticket-local UAT scenarios back into official inventory (if applicable)

When the ticket used Automated UAT and has its UAT scenario file (`.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenarios.md` new, or `docs/plans/TICKET-XXX.uat-scenarios.md` legacy):

1. Read the local extracted scenario file and identify: the official high-level UAT inventory path; which scenarios are **Modified / Extended / New**; any unresolved questions
2. Prepare a scoped merge back — preferably an architecture-level inventory under `docs/architecture/*test-scenarios*.md`. Update only ticket-relevant scenarios, never unrelated sections.
3. **If overwriting existing official scenario text or if intended behavior is ambiguous, ask the user focused confirmation questions before editing the official doc.**
4. Merge the confirmed scenario changes back.
5. Update `TICKET-XXX.uat-scenarios.md` with merge-back status, date, what was merged.

<!-- uat-scenarios:write-integration -->
When a project has adopted the COM-122 UAT-scenario engine, WRITE scenario changes (merge-back / overlay) as a structured **JSON-patch sidecar** applied via `jswarm/uat-scenarios/apply_uat_patch.py` (RFC-6902 ops; `base_sha256` for optimistic-concurrency conflict detection) → it updates the canonical JSON and regenerates the generated Markdown. Never hand-edit the generated `.md` or the canonical JSON directly. On a conflict (stale `base_sha256` or failed `test` op) the apply aborts without writing — rebase the patch on the current canonical and retry. Non-adopted projects keep the markdown merge-back path.

If no official inventory exists yet, ask whether to promote the ticket-local file into a new official inventory now or leave as follow-up.

### 3.3a.NFR — Promote ticket-local NFRs into the project NFR catalog (if applicable)

<!-- nfr-catalog:write-integration -->
Run the NFR lifecycle gate first; it self-skips non-adopted / `NFR catalog: N/A` projects (fail-open, exit 0 — never blocks closure of unrelated tickets):

```bash
.venv/bin/python jswarm/nfr-catalog/lifecycle_gate.py \
  --project-root "$REPO_ROOT" --plan "$PLAN_FILE" --stage close --json
```

When the project has adopted the COM-169 NFR-catalog engine (an `nfr-catalog-source` anchor resolving to the canonical NFR JSON) and the ticket authored NFRs (`.jswarm/plans/TICKET-XXX/TICKET-XXX.nfr-proposals.json` + `TICKET-XXX.nfr.md`), promote them **deterministically** — never by hand-edited JSON-patch:

1. `jswarm/nfr-catalog/draft_nfr_patch.py --from-proposals TICKET-XXX.nfr-proposals.json --catalog <project.nfr-catalog.json> --out <sidecar.json>` — composes an ID-anchored RFC-6902 sidecar (`test` ops on `nfr.id`/`category.id`; `base_sha256` optimistic concurrency).
2. `jswarm/nfr-catalog/apply_nfr_patch.py --catalog <project.nfr-catalog.json> --schema … --patch <sidecar.json> --in-place` — schema/semantic-validates, aborts without writing on conflict/invalid, then regenerates the read-only Markdown.
3. `jswarm/nfr-catalog/render_nfr_catalog.py --catalog … --schema … --output … --strict-links` — confirm every citation resolves as a full `NFR-<n>-<DESCRIPTOR>` ref.
4. `jswarm/nfr-catalog/merge_gate.py` — non-droppable (`priority:must ∧ status:active`) coverage + generated-file integrity. The three modes are **mutually exclusive** (run as separate invocations, all need `--catalog`/`--config`/`--schema` as applicable):
   - `merge_gate.py --validate-config "$NFR_MERGE_CONFIG" --catalog "$NFR_CATALOG_JSON" --schema jswarm/nfr-catalog/schema/nfr-catalog.schema.json --json`
   - `merge_gate.py --freshness --config "$NFR_MERGE_CONFIG" --catalog "$NFR_CATALOG_JSON" --schema jswarm/nfr-catalog/schema/nfr-catalog.schema.json --json`
   - `merge_gate.py --guard-changeset --config "$NFR_MERGE_CONFIG" --catalog "$NFR_CATALOG_JSON" --schema jswarm/nfr-catalog/schema/nfr-catalog.schema.json --changed "$CHANGED_LIST" --json`

On a conflict (stale `base_sha256` or failed `test` op) the apply aborts without writing — rebase the sidecar on the current canonical and retry. If overwriting an existing project NFR or intended scope is ambiguous, ask the user before applying. Non-adopted projects skip this step entirely. Update `TICKET-XXX.nfr.md` merge-back status.

### 3.3a.TEST — Promote ticket-local regression rows into the test catalog (if applicable)

<!-- test-catalog:write-integration -->
Run the test-catalog lifecycle gate first; it self-skips non-adopted / `mode: no-catalog` projects (fail-open exit 0 — never blocks closure of unrelated tickets):

```bash
PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/test_catalog_detector.py --repo-root "$REPO_ROOT" --json
```

When the project has adopted the generated test-catalog engine and the ticket added or changed regression rows, promote them **deterministically** — JSON is authoritative, Markdown is generated, and neither the canonical regression inventory nor generated `tests/TEST_CATALOG.md` is hand-edited:

Capture the pre-promotion baseline inventory before applying the sidecar (or obtain it from `origin/main`): `BASELINE_REGRESSION_INVENTORY_JSON="${TEST_PATCH_SIDECAR%.json}.baseline-regression-inventory.json"; git show "$(git merge-base HEAD origin/main):${REGRESSION_INVENTORY_REL_PATH}" > "$BASELINE_REGRESSION_INVENTORY_JSON"`.

Then run the real argparse contracts in order:

```bash
.venv/bin/python jswarm/test-catalog/draft_test_patch.py \
  --inventory "$REGRESSION_INVENTORY_JSON" --from-proposals "$TEST_PROPOSALS_JSON" \
  --uat "$UAT_JSON" --nfr "$NFR_JSON" --out "$TEST_PATCH_SIDECAR"
.venv/bin/python jswarm/test-catalog/apply_test_patch.py \
  --inventory "$REGRESSION_INVENTORY_JSON" --sidecar "$TEST_PATCH_SIDECAR" \
  --schema jswarm/test-catalog/schema/regression-inventory.schema.json \
  --uat "$UAT_JSON" --nfr "$NFR_JSON"
.venv/bin/python jswarm/test-catalog/generate_test_catalog.py \
  --uat "$UAT_JSON" --nfr "$NFR_JSON" --inventory "$REGRESSION_INVENTORY_JSON" \
  --template "$TEST_CATALOG_TEMPLATE" --output "$TEST_CATALOG_MD" --strict-links --check
.venv/bin/python jswarm/test-catalog/merge_gate.py \
  --uat "$UAT_JSON" --nfr "$NFR_JSON" --inventory "$REGRESSION_INVENTORY_JSON" \
  --baseline-inventory "$BASELINE_REGRESSION_INVENTORY_JSON" \
  --template "$TEST_CATALOG_TEMPLATE" --output "$TEST_CATALOG_MD" --json
```

Documented invocations for argparse-contract validation: `jswarm/test-catalog/draft_test_patch.py --inventory "$REGRESSION_INVENTORY_JSON" --from-proposals "$TEST_PROPOSALS_JSON" --uat "$UAT_JSON" --nfr "$NFR_JSON" --out "$TEST_PATCH_SIDECAR"`; `jswarm/test-catalog/apply_test_patch.py --inventory "$REGRESSION_INVENTORY_JSON" --sidecar "$TEST_PATCH_SIDECAR" --schema jswarm/test-catalog/schema/regression-inventory.schema.json --uat "$UAT_JSON" --nfr "$NFR_JSON"`; `jswarm/test-catalog/generate_test_catalog.py --uat "$UAT_JSON" --nfr "$NFR_JSON" --inventory "$REGRESSION_INVENTORY_JSON" --template "$TEST_CATALOG_TEMPLATE" --output "$TEST_CATALOG_MD" --strict-links --check`; `jswarm/test-catalog/merge_gate.py --uat "$UAT_JSON" --nfr "$NFR_JSON" --inventory "$REGRESSION_INVENTORY_JSON" --baseline-inventory "$BASELINE_REGRESSION_INVENTORY_JSON" --template "$TEST_CATALOG_TEMPLATE" --output "$TEST_CATALOG_MD" --json`.

The draft composes a scoped sidecar from ticket-local regression proposals and records the tri-source snapshot. The apply schema/semantic-validates, aborts without writing on conflict, and records the tri-source digest for the applied sources. The generator `--check` proves generated `tests/TEST_CATALOG.md` bytes are fresh after the JSON update; if stale, run write mode, include the generated diff, and rerun `--check`. The merge gate enforces generated-file integrity, non-droppable regression coverage, and traceability rules before merge.

On a conflict (stale digest / failed `test` op / semantic validation failure), the apply aborts without writing — rebase the sidecar on the current canonical and retry. If overwriting an existing regression row or intended coverage is ambiguous, ask the user before applying. Non-adopted projects skip this step entirely.

### 3.3b — Record live-show to regression promotion (if applicable)

When the ticket used Live Show UAT:

1. Read the UAT test doc (`.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-test.md` new, or `docs/plans/TICKET-XXX.uat-test.md` legacy) and the UAT report
2. Confirm each passing live-show scenario has one of: promoted regression-mode Playwright spec path (normally under `tests/e2e/primary/` for real-backend journeys per `.jswarm/e2e-manifest.json`); feature-level deferral with target feature / PE2E contract; explicit N/A reason
3. If the promoted spec is a mocked UI contract rather than a real-backend acceptance journey, record why it belongs under `tests/e2e/ui/` and do not count it as UAT proof
4. Update `tests/TEST_CATALOG.md` so the promoted regression spec is discoverable
5. For Regression E2E/PE2E catalog rows, populate `Relevant UAT` with the live-show scenario ID, `uat-test.md` anchor, or `N/A — [reason]`

### 3.3b.1 — Reconcile UAT script with shipped behavior

Before closing a ticket that used Automated UAT:

1. Read `TICKET-XXX.uat-test.md`, the UAT report, the final implementation summary
2. Confirm the executable UAT script reflects what shipped, not just what was planned
3. Update if implementation changed any of: filters/sorting/routing/retry/visible text; required data setup or state-mode labels; API endpoints/request-response/auth/feature flags; pass/fail criteria or diagnostic fallback or evidence capture
4. Record status: `UAT script freshness: current — matches shipped behavior` | `updated during closeout — see diff` | `N/A — Automated UAT disabled because [reason]`

Do not close with a stale `uat-test.md`; future regression promotion depends on this document matching the real shipped flow.

### 3.3c — Merge durable test data cluster updates (if applicable)

When the ticket created or changed a managed test data cluster:

1. Read the ticket plan, `TICKET-XXX.uat-test.md`, UAT report, promoted regression specs
2. Confirm every regression-mode E2E/PE2E artifact references deterministic data
3. If exploratory live-show data revealed a useful path: convert it into a managed cluster before regression promotion, OR record promotion as deferred until cluster exists
4. Merge durable cluster details into `docs/testing/test-data-clusters.md` or project-local equivalent
5. Update `TEST_CATALOG.md` or generated test indexes with cluster IDs for affected tests

### 3.4 — Update architecture docs (relevance-gated)

Only update architecture docs if this work made decisions that changed how the system works — not for every ticket.

| Signal | Update needed |
|--------|---------------|
| New API endpoints or changed API contracts | Yes — update relevant architecture doc |
| New auth/authz patterns or changes | Yes — update auth architecture |
| Infrastructure changes (new services, config, deployment) | Yes — update infra architecture |
| New UI patterns, routing, or state management approach | Yes — update UI architecture |
| Data model changes (new tables, schema migrations) | Yes — update data architecture |
| Bug fix with no design changes | No |
| Refactor preserving existing behavior | No |

Check at two levels:
1. **Feature-level specs:** `.jswarm/plans/FEATURE-XXX/FEATURE-XXX.specs.*.md` (new) or `docs/plans/FEATURE-XXX.specs.md` (legacy) — update if implementation deviated from original design or future stories need to know
2. **Project-level architecture siblings (relevance-filtered):** update the affected `docs/architecture/*.md` plus `docs/architecture/rules/` and `docs/architecture/tools/`. When an orchestration mechanism changed, also reconcile `docs/architecture/architecture.v3.pipeline-trace.matrix.json` and its rendered `docs/architecture/architecture.v3.pipeline-trace.md` companion using the existing jDebug pipeline-trace maintenance procedure.

This final resync is **fail-open and nonblocking**: do not add a new heavyweight close gate or delay closure solely because maintenance tooling/indexing is unavailable. Keep updates concise — record decision and rationale, not implementation details — and explicitly flag unresolved rules/tools catalog gaps or follow-up work in the closeout/retro.

### 3.5 — Retrospective (MANDATORY)

Every closeout includes a structured reflection. The agent writes it autonomously.

**Canonical location (single rule — common archive + project symlink):**

Every retro's **real file** lives in the canonical common archive, git-tracked in the `common` repo:

```
${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro[.<kind>].md
```

The **project repo never holds a real retro file.** It holds an **absolute symlink**, co-located with the ticket's other plan artifacts:

```
.jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md  ->  /Users/<you>/dev/common/docs/retros/<same-or-slugged-basename>.md
```

**Authoring flow is INVERTED — write in common, symlink from the project (never the reverse):**

1. **Resolve the canonical file:** check `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro*.md`. If a file already exists for this ticket → **append there** (never create a second file for the same key). **Narrow exception:** a completed check-in may be transcribed into its own dedicated retro file alongside the ticket retro. That transcription is reporting, not release authority — **never refuse closure over its shape, presence, or cross-referencing.** Closure is gated only by Step 2.10's quiescence check. Else → **create** `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.md` from the template below.
2. **Commit the real file in the `common` repo.**
3. From the project repo, create/refresh the **absolute** symlink at `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md` → the common file (see §3.5a), and commit the symlink in the project repo.

> **Never** write a real retro file under `.jswarm/plans/` (or anywhere in the project), and **never** put a retro symlink in the project root. Both are violations of the canonical retro convention (`${JSWARM_HOME:-$HOME/dev/jswarm}/.claude/skills/retros/SKILL.md`; `/retros --apply <project>` repairs drift). A `common`-only ticket has no project symlink — the common real file is the whole deliverable.

**Filenames:**
| Case | Common real file (authoritative) | Project symlink (absolute → common) |
|---|---|---|
| Default — one retro for the ticket | `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.md` | `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro.md` |
| Split by kind (devops/tooling vs product, coordination vs implementation) | `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.<kind>.md` | `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro.<kind>.md` |

Prefer a single `TICKET-XXX.retro.md` until mixed concerns or length make a second file clearly better. Legacy archive names (`TICKET-XXX-SLUG.retro.md`, date-stamped names) remain valid as the **common** real file when continuing an established file; the project symlink should still use the clean `TICKET-XXX.retro[.<kind>].md` name.

**Evidence sources** (gather what's available):
1. Plan file — phases, scope changes, deferred items
2. `git log --oneline` for ticket commits
3. Tool failure reports — `ls docs/tool-failure-reports/TICKET-XXX.toolfail.*.md 2>/dev/null`
4. Technical design spec — `.jswarm/plans/TICKET-XXX/TICKET-XXX.specs.*.md` (new) or `docs/plans/TICKET-XXX.specs.md` (legacy) — planned vs actual deltas
5. Defect tracker files (Feature only) — `.jswarm/plans/TICKET-XXX/TICKET-XXX.integr-fixes.md` / `.pe2e-fixes.md` (new) or `docs/plans/TICKET-XXX-integr-fixes.md` / `-pe2e-fixes.md` (legacy)
6. Conversation context — stalls, wrong approaches, rework, surprises

**Writing rules:**
- If the canonical retro exists: **UPDATE only** — append findings; add Changelog row; never overwrite body
- If creating fresh: use the full template (categories: Project/Domain, Planning, Testing & QA, AI Agent Effectiveness, Tooling, Architecture/Infrastructure, UAT ↔ E2E Alignment, Other). **Skip categories with no findings** — do not pad with N/A.
- Use the `+ / - / Δ` row shape per category

**Retro template** (when creating fresh):

```markdown
# Retrospective: TICKET-XXX — [Short Title]

**Date:** YYYY-MM-DD
**Ticket:** [link]
**Plan:** [.jswarm/plans/TICKET-XXX.plan.*.md (new) | docs/plans/TICKET-XXX-DESCRIPTION.md (legacy)]
**Depth:** [Quick/Standard/Deep]
**Duration:** [start → close]
**Tool Failure Reports:** [list or None]

## Summary
[1-2 sentences]

## Retrospective
[Categories with findings only]
### [Category]
| | Finding |
|---|---|
| + | [what went well] |
| - | [what didn't] |
| Δ | [what to change] |

## Action Items
| # | Action | Owner | Target | Status |
|---|---|---|---|---|

## Changelog
| Date | Author | Change |
|---|---|---|
| YYYY-MM-DD | [agent/human] | Retro created via /jClose |
```

### 3.5a — Project symlink for retros (worktree-safe)

For **each** retro file touched in the common archive, ensure the project repo holds an **absolute** symlink co-located with the ticket's plan artifacts. Run from the **project root** (skip only when no app repo is in play — e.g. a `common`-only ticket):

```bash
mkdir -p .jswarm/plans/TICKET-XXX
# absolute target ($HOME expands at ln time); -n so re-running refreshes in place
ln -sfn "${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/<common-basename>.md" \
        ".jswarm/plans/TICKET-XXX/TICKET-XXX.retro.md"     # + one per <kind> file
```

- **Absolute target, always.** The link must resolve to a fully-expanded absolute path. A relative target (`../common/...`) breaks because worktrees live at different depths (`.claude/worktrees/<wt>/`, or even `/private/tmp/...`); a literal `~` is **not** expanded inside a symlink and dangles. Verify with `[ -e .jswarm/plans/TICKET-XXX/TICKET-XXX.retro.md ]`.
- **Worktree-safe by construction.** Because the target is absolute, the symlink resolves identically in the main checkout and in every worktree, and a worktree→main merge carries the symlink unchanged — it keeps pointing at the same common file. The real file lives in the separate `common` repo, so all worktrees share one source of truth.
- **Governed artifact symlinks follow the same rule.** Any governed artifact symlink placed under `.jswarm/plans/TICKET-XXX/` must point to an absolute, common-owned target (normally under `${JSWARM_HOME:-$HOME/dev/jswarm}/...`); relative targets and literal `~` targets are invalid for the same worktree-safety reasons as retros.
- **Never in the project root.** The symlink belongs in `.jswarm/plans/TICKET-XXX/`, never at the repo root, and the project never holds a real retro file.

### 3.5b — Update plan and Jira

- Add a Jira comment summarizing the retro (top 3 findings + action item count) through the retry-safe helper below
- If the plan has a `## Retrospective` section, update it with links to every retro file touched

**Jira write rule:** Closeout Jira writes must use `${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/jira_mcp_closeout.py`. It retries the local Atlassian MCP HTTP endpoint and, if Jira still fails, writes `docs/tool-failure-reports/<ticket>.toolfail.atlassianjira.<timestamp>.md` with the exact manual action. Treat a nonzero helper exit as a closeout blocker until the manual Jira action is applied or the report is resolved.

### 3.6 — Jira pre-merge comment

Add the work-completed comment now; Jira Done transition is gated on `/jMerge` returning green (Step 3.10).

```bash
cat > /tmp/TICKET-XXX.close-ticket-comment.md <<'EOF'
## Work Completed
**Plan:** <PLAN_FILE>  <!-- .jswarm/plans/TICKET-XXX.plan.*.md (new) or docs/plans/TICKET-XXX-*.md (legacy) -->
### A/C Status
- [x] A/C 1: ...
- [x] A/C 2: ...
*Pre-merge close via /jClose; final Done transition follows /jMerge return.*
EOF

${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/jira_mcp_closeout.py \
  --ticket-context TICKET-XXX \
  comment TICKET-XXX \
  --body-file /tmp/TICKET-XXX.close-ticket-comment.md
```

If working on `main` (no worktree, OR already integrated by prior standalone `/jMerge`): skip Step 3.9 and route directly to Jira Done in Step 3.10.

---

### 3.6a — Role-aligned PM/PO rollup comments (opt-in, AC-20)

**Gate:** run ONLY when the plan frontmatter has `rollup_comments: on` (set once at `/jPlan`; absent or `off` ⇒ skip — post nothing, write nothing). This is **distinct from** the Step 3.5 retro comment and the Step 3.6 work-completed comment.

When enabled, at close:
1. Render the **po-ticket-outcome** at **PO altitude** via `/plain-english` and post/update it as a comment on the **story/task** Jira issue.
2. If the ticket is a child of a Feature, render the **pm-feature-rollup** at **PM altitude** via `/plain-english` and post/update it as a comment on the **Feature** Jira issue (the `/feature-reconcile` that follows refreshes it with reconciled truth).
3. Post via the jira-sync-engine glue `jswarm/installer/walkthrough/rollup_sync.py` (retry-safe wrapper over `jira_mcp_closeout.py` `add_comment`); capture the **comment deep link** (comment URL) and record it in the plan.
4. `run_id`-namespaced + ledgered (NFR-016/NFR-029) for teardown.
5. **Jira-off fallback:** write a local **receipt** and record its path in the plan.

---

## Step 3.9: Merge feature branch via `/jMerge` (one-line dispatch)

`/jClose` MUST NOT merge feature branches directly or do merge preflight. All merge logic — including source-side commit/push prep, classification, conflict resolution, runtime gates, worktree teardown — flows through `/jMerge`. See `~/.claude/skills/jMerge/SKILL.md`.

### 3.9.1 — Idempotence check

If a prior standalone `/jMerge` already integrated this ticket:

```bash
SOURCE_SHA=$(git rev-parse "origin/feat/TICKET-XXX" 2>/dev/null)
INTEGRATED=$(git log "${TARGET}" --format="%H %P" --merges | awk -v sha="${SOURCE_SHA}" '$0 ~ sha {print $1}')
if [ -n "$INTEGRATED" ]; then
    echo "Already integrated by merge commit $INTEGRATED — skipping merge phase"
    # If SHA matches expected: proceed to Step 3.10 Jira Done + status flip
fi
```

### 3.9.2 — Detect worktree state

```bash
branch=$(git branch --show-current)
git_top=$(git rev-parse --show-toplevel)
if [[ "$branch" != "main" ]] && [[ "$git_top" == *".claude/worktrees/"* ]]; then
    NEEDS_MERGE=true
fi
```

### 3.9.3 — Invoke `/jMerge`

If on a feature branch in a worktree (or branch ready to merge):

```
# COM-91 dual-path — resolve <PLAN_FILE> before invocation
# New tickets (>= 2026-05-22): --plan .jswarm/plans/TICKET-XXX.plan.*.md
# Legacy tickets:               --plan docs/plans/TICKET-XXX-*.md
/jMerge --quick --source TICKET-XXX --target main --mode close-ticket --plan <PLAN_FILE>
```

This single dispatch handles ALL merge prep:
- Step 0b: source-side preflight (auto-commit Class B/C dirt, push branch) — NO paste-prep needed from developer
- Step 0c: plan-status awareness (proceeds silently on `READY_FOR_MERGE`)
- Lean transaction (Q.4) or full protocol if lean rejects
- Push + remote source branch delete + Jira finalize + worktree teardown (Step 10.6 — incl. fail-closed ColGREP worktree-overlay teardown: fleet supervisor + watcher stop, served generation index + on-disk generation-dir removal, `worktrees.json` registry purge; no orphaned overlay/watcher/thrash. COM-175 A/C 8)

Returns `MERGE_LEAN_GREEN` (lean path) or `MERGE_FINAL_GREEN` (full path). If `/jMerge` surfaces a Class D/E escalation, bubble it up verbatim — do NOT reinterpret.

### 3.9.4 — COM-100 dual-render close-ticket boundary

The no-merge path delegates Ring and visual close to /jMerge because /jMerge is the first command that knows the authoritative merge SHA. /jClose must not invent a merge SHA.

If the parent Feature uses dashboard JSON and managed markdown blocks, /jClose may verify that the required inputs exist and may run check mode with `--markdown-output`, but it does not write the normal close-flip set. The idempotence path is verify-only unless explicit drift repair is requested by the operator or by a failed source-of-truth backstop.

---

## Step 3.10: Post-merge — Flip plan status to DONE + Jira final Done

This step runs AFTER `/jMerge` returns green (or after Step 3.9.1 idempotence detected prior integration).

**3.10.1 — Flip plan status `READY_FOR_MERGE → DONE`:**

> **COM-138 — record the `plan_status` transition; `status:`/`phase:`/`ac_complete:` are DERIVED.**
> The canonical write is `plan_status → 6.closed.merged` (via `cli.py record`); the reconcile hook's
> `normalize_plan_file` then derives `status:` (→ `DONE`), `phase:`, and `ac_complete:` and keeps
> frontmatter at line 1 in canonical order. The YAML below shows the resulting derived state.

Canonical action (the hook derives `status:`/`phase:`):

```bash
.venv/bin/python jswarm/plan_status/cli.py record TICKET-XXX 6.closed.merged \
  --actor /jClose --proof-source post-merge --sync-jira
```

Resulting derived frontmatter (normalize writes this — do NOT hand-edit `status:`/`phase:`):

```yaml
plan_status: "6.closed.merged"   # the canonical change
status: DONE                      # DERIVED from plan_status
phase: "6.merged"                 # DERIVED
closed_at: YYYY-MM-DD
revisions:
  - date: YYYY-MM-DD
    band: terminal
    field: plan_status            # the canonical field that changed
    diff: "5.closed.ready_for_merge → 6.closed.merged"
    actor: /jClose (post-merge)
    merge_sha: <merge commit SHA>
```

Plan body `**Status:**` line reflects the derived `DONE`. Status Updates table gets a `DONE` row.

**3.10.2 — Jira final Done transition:**

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/jira_mcp_closeout.py \
  --ticket-context TICKET-XXX \
  transition-done TICKET-XXX
```

**Why this gating matters:** if Jira transitions to Done BEFORE `/jMerge` succeeds and `/jMerge` later fails (smoke failure, critic block), Jira state would lie about reality. Gating ensures Jira reflects post-merge runtime truth, not pre-merge intent.

---

## Step 4: Summary

```
✅ TICKET-XXX closed

Jira: https://lsadigital.atlassian.net/browse/TICKET-XXX → Done
Plan: <PLAN_FILE> → status: DONE  (.jswarm/plans/TICKET-XXX.plan.*.md new, or docs/plans/TICKET-XXX-*.md legacy)
Retro: ${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro[.<kind>].md → Written/Appended (real file, common archive)
Symlink: .jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md → absolute, worktree-safe (points into common; N/A for common-only ticket)
Parent plan: [updated / N/A]
Architecture docs: [updated X / no changes needed]
Official UAT inventory: [merged / no official inventory / deferred]
Catalog: TEST_CATALOG.md regenerated
UAT script freshness: [current / updated during closeout / N/A]
Merge: [/jMerge MERGE_LEAN_GREEN @ <sha> / MERGE_FINAL_GREEN @ <sha> / N/A (worked on main)]
Teardown gate: [CLOSE_GATE_OK / CLOSE_GATE_OK_YELLOW / CLOSE_GATE_SKIPPED / DEFERRED-TO-MERGE — post-merge verification recorded by /jMerge Step 10.8 / blocked]
Worktree: [torn down by /jMerge Step 10.8 / N/A]
```

---

## Error recovery

| Error | Action |
|---|---|
| Plan not found | Ask for path or close Jira only |
| Tests incomplete (Block 1 blocker) | Run `/jGo` first |
| Plan status `ACTIVE` (Block 1.4 blocker) | `/jGo` final phase didn't complete; rerun |
| `/jMerge` returns escalation (Class D/E) | Bubble up verbatim; do NOT auto-resolve from /jClose |
| Jira transition fails | Check available transitions; helper auto-writes tool-failure report |

---

## ColGREP Index Lifecycle Check (COM-204 — no-badgering)

At **close-out (before final `/jMerge`)**, run the read-only ColGREP lifecycle check. It silently lets the certain-only evictor handle stale/orphan indices and surfaces ONE consolidated question only for genuinely ambiguous candidates — and only once per unchanged set (receipt-backed; no badgering):

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/colgrep_index_lifecycle.py \
  check --command close-ticket --json
```

- `question` null or `suppressed: true` → proceed silently; no action needed.
- `question` present → surface its `prompt` + each `candidate` (with its `reasons`) using the actions **keep-protect / delete-now / defer / inspect-details**. Advisory only — never block close-out, and never auto-`--apply` cleanup from a lifecycle command (deletion stays operator-gated; dry-run is the default).

---

## Changelog

| Date | Author | Change |
|---|---|---|
| 2026-08-23 | jWriter | Step 2 teardown gate self-skips with exact `DEFERRED-TO-MERGE` only for a live worktree before `/jMerge` teardown, with required post-merge verification in the `/jMerge` Step 10.8 family; other paths remain unchanged. |
| 2026-07-13 | GPT-5.6 | Step 3.4 now names architecture rules/tools catalogs and the pipeline-trace matrix JSON/rendered Markdown as relevance-filtered final resync siblings; maintenance remains fail-open/nonblocking and unresolved catalog gaps are recorded as follow-ups. |
| 2026-06-20 | GPT-5.5 | Added Step 1.5 evidence-surface refresh before validation: run the precompact reconcile chain (`migrate → rows → statuses → update-plan`) when `/jPrecompact` was skipped, then block closure on non-terminal A/C-to-Test, NFR/UAT, UAT/per-phase, phase/exit, Testing Strategy, or stale evidence surfaces. Kept `plan_status` repair forbidden in `/jClose`. |
| 2026-06-01 | Claude Opus 4.8 | **Retro model unified (supersedes the 2026-05-22 COM-91 dual-location split).** One rule: the retro real file ALWAYS lives in `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/` (committed in `common`); the project ALWAYS holds an **absolute, worktree-safe** symlink at `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro[.<kind>].md` → the common file. Removed the "new tickets write a real retro file under `.jswarm/plans/`" branch and the "legacy symlink in project root" branch (§3.5 + §3.5a rewritten, §3.5a now `ln -sfn "$HOME/..."`). Matches the `/retros` canonical convention; worktree merges carry the absolute symlink unchanged. Authority doc `docs/agent-system/agent-write-permissions.md` updated in lockstep. |
| 2026-05-25 | Sisyphus | COM-97: Added Security & Compliance post-risk capture for /jClose, including missing-before not-computable handling and fail-open outcome telemetry writer guidance. |
| 2026-05-22 | COM-91 Phase 3b | Dual-path plan resolution (`.jswarm/plans/TICKET-XXX.plan.*.md` for new tickets ≥ 2026-05-22; `docs/plans/TICKET-XXX-*.md` legacy fallback) across plan-file load (Step 1), grep helpers, Feature reconciliation-log path, UAT scenarios/test paths, Feature spec path, retro Evidence Sources, Jira comment template, /jMerge invocation, final summary. **Retro destination flipped:** NEW tickets write retros to `.jswarm/plans/TICKET-XXX/TICKET-XXX.retro.<desc>.md`; LEGACY tickets keep appending to `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.md`. §3.5a symlink rule applies to legacy retros only (new-location retros are project-local). Per canonical doc `docs/agent-system/agent-write-permissions.md`. |
| 2026-05-11 | Sisyphus | **Lifecycle + leanness pass.** Removed plan-status BLOCKER from Step 2 (now Block 1.4 reads `READY_FOR_MERGE` — `/jGo` owns the flip, not `/jClose`). Simplified Step 3.9 to one-line `/jMerge` dispatch (source-side preflight + commit/push prep moved to `/jMerge` Step 0b). Added Step 3.10 post-merge status flip (`READY_FOR_MERGE → DONE`). Extracted 17-row validation table to `docs/close-ticket/validation-checklist.md`. Reduced 505 → ~250 lines. |
| 2026-05-10 | Sisyphus | Step 3.9 invokes /jMerge --quick first; full protocol only after lean rejection or explicit user request. |
| 2026-05-08 | — | Retro filenames: default `TICKET-XXX.retro.md`; optional split `TICKET-XXX.retro.<kind>.md`; symlink per basename. |
| 2026-05-03 | GPT-5.5 | Added generated TEST_CATALOG validation, `Relevant UAT` enforcement, UAT script freshness reconciliation. |
| 2026-05-02 | GPT-5.5 | Added closeout gates for Playwright-based Live Show UAT, regression artifact promotion, architecture scenario merge-back. |
| 2026-05-01 | Sisyphus | Step 3.9 + 3.6 reworked to call `/jMerge` instead of direct git merge. Worktree feature branch integration flows through merge protocol. |
| 2026-04-21 | Sisyphus | Added Per-Phase UAT Gate validation to Step 2. |
| 2026-04-15 | Claude | Added closeout merge-back workflow + Live Show UAT + E2E policy validation. |
| 2026-04-03 | Claude | Retro is now autonomous (no user Q&A). Filename convention superseded 2026-05-08. |
| 2026-04-01 | Claude | Added Step 3.5 (Retrospective). |
