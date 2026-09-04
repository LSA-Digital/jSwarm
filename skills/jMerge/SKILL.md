---
name: jMerge
description: Symlink to the global /jMerge command that integrates feature branches into a target branch, runtime-verifies a working system, and cleans up; owns all merge logic.
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/jMerge/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy.
     COM-358 T1.3: /jMerge has been renamed to /jMerge. The old skills/jMerge/SKILL.md survives as a
     thin compatibility alias that delegates here; it carries its own distinct catalog identity. -->

# /jMerge — Working Merge Protocol

## Safety contract (COM-219 — destructive skill, agent-invocable)

- **Default is read-only / no-write.** Invoked with no args (or `--help`/`status`), this skill only inspects and reports; it performs NO write, apply, push, delete, remote, or trim.
- **Confirm before any mutation.** Before any state-changing mode, the invoker (human or agent) must obtain explicit confirmation — or run an approved preview/dry-run first and act only on that approved plan.
- **Agents are NOT locked out** (no `disable-model-invocation`); this contract — not frontmatter — is what gates writes, so the read-only path is freely usable and a mutation requires approval.


Integrate one or more source feature branches into a target branch (typically `main`), produce a runtime-verified working system before declaring the merge complete, and clean up. Owns ALL merge logic; `/jClose` invokes this command but does NOT reimplement merge mechanics.

## Durable UAT form-event artifacts

A ticket-local `.jswarm/plans/<TICKET>/form-events/` directory is a durable UAT
record, not disposable runtime state. It contains the append-only `events.ndjson`,
its `consumer/consumed.ndjson` acknowledgement ledger, and `watch.json`; without
those files, a worktree merge carries owner feedback but loses proof of delivery
and processing.

**Required source gate:** before accepting Step 3.2 Clean sources and before the
ticket commit, run this non-mutating gate from the source worktree:

```bash
"${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" \
  "${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/jmerge_form_events_gate.py" \
  --repo-root "$(git rev-parse --show-toplevel)" --ticket "<TICKET>"
```

Exit `0` is silent: the directory is absent or contains no untracked/ignored event
files. Exit `1` blocks the merge and names every at-risk path plus the exact
`git add` command to run; an ignored path is reported with the required `git add -f`
command rather than falsely passing. Exit `2` means the gate could not inspect the
worktree and also blocks the merge. The gate never stages files itself, so it cannot
silently mutate another operator's index. After its requested staging command is
run, the normal ticket commit must include the durable records. Do not stage lock
sidecars or temporary files. The event schema permits identities, digests,
timestamps, and continuation metadata only — never payload text — so these durable
records belong in the normal ticket commit and merge with the feedback artifact.

> **Point-of-impact reminder (COM-133 — migrated from auto-memory):**
> - **Lean default before escalating:** default to a lean, fast, evidence-gated path first; escalate (`jArchitect` / `jArchitect` at xhigh effort, full UAT, extra worktrees) only after cheap git evidence shows real complexity or a rejected path. For simple single-source merges, inspect branch/diff/conflict risk with cheap git commands; use path-limited stash only after explicit user choice for classified local paths. Don't involve architects or spin a separate merge worktree without conflict evidence, except when the operator explicitly requests the read-only default-effort `jArchitect` Step 0.plan advisory through `--arch-plan`; xhigh remains subject to CHK-AM.

**Deep semantics:** [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/protocol-spec.md`](../../docs/merge/protocol-spec.md) (state machine, traceability matrix schema, full per-source transaction, gate definitions)
**State machine:** [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/state-machine.md`](../../docs/merge/state-machine.md) (`ACTIVE → READY_FOR_MERGE → DONE`)
**Conflict examples:** [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/conflict-examples.md`](../../docs/merge/conflict-examples.md)
**Preflight (main-sync + source-side):** [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/preflight.md`](../../docs/merge/preflight.md)

---

## Reserved vocabulary (LOCK)

These status terms are **PROHIBITED** in any agent or orchestrator output until the runtime-proof gate (Step 7) passes for the relevant phase:

> `complete` · `verified` · `high-quality` · `working` · `working-system` · `ready to push` · `merged` · `done` · `clean merge`

**Permitted before runtime-proof gate:**

> `planned` · `in-progress` · `conflicts-resolved-uncommitted` · `committed-unverified` · `migration-pending` · `smoke-failing` · `smoke-passing` · `full-uat-failing` · `full-uat-passing` · `critic-failing` · `critic-passing` · `blocked`

Applies to BOTH the executing orchestrator AND any delegated sub-agent reports. Per `protocol-spec.md` §1.1.

---

## Conflict Classification Policy (auto-resolve vs escalate)

Classify EACH conflicted file BEFORE asking the user. Most conflicts have deterministic resolutions; only Class D/E surface as user decisions. Canonical memory: `feedback_merge_autonomous_doc_conflicts.md` PERMANENT. Worked examples: `conflict-examples.md`.

| Class | File pattern | Resolution rule | Surfaces? |
|---|---|---|---|
| **A. Gitignored / ephemeral** | `.jswarm/state/*`, `*.tmp`, `*.scratch.*`, `.DS_Store`, any gitignored path | Discard source's version; keep target | No |
| **B. Feature plan files** | `.jswarm/plans/HAS-*.plan.*.md` (new), `docs/plans/HAS-*.feature-plan.md` (legacy), master plans | **UNION RULE:** keep BOTH Story nodes, BOTH Gantt rows, BOTH §6a rows, BOTH classDef listings. Verify Mermaid parses (per `feedback_master_plan_flowchart_freshness.md` PERMANENT). | No (log what unioned) |
| **C. Append-only logs / retros** | `.jswarm/plans/*/*.retro.md` + `.jswarm/plans/*/*.reconciliation-log.md` (new, per-ticket subfolder), `docs/plans/*.reconciliation-log.md` + `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/*.retro.md` (legacy) | Chronological concat by date frontmatter; preserve all entries; preserve internal same-date ordering | No (log line count) |
| **D. Source code / tests** | `app/**`, `tests/**`, `src/**`, `lib/**` | ESCALATE using plain-language template (below) | YES |
| **E. Config / lockfiles** | `package.json`, `pyproject.toml`, `*.lock`, `*.config.json` | ESCALATE — semantic-merge risk too high | YES |

After auto-resolves complete, if zero Class D/E remain → continue merge silently, emit one-line summary of what auto-resolved. If any Class D/E remain → present escalation template and block.

### Plain-language escalation template (Class D/E only)

```
🟡 Merge decision needed: <BRANCH> → <TARGET>

Plain-language situation:
Both <BRANCH> and <TARGET> changed `<file>` in overlapping ways.

What each side did:
- <BRANCH>: <one-sentence intent in user's words, not "the HEAD ref">
- <TARGET>: <one-sentence intent>

Options:
A) Take <BRANCH>'s version
   - Gain: <what this preserves>  - Lose: <what this drops>  - Risk: <regression concern>
B) Take <TARGET>'s version
   - Gain: <preserves>  - Lose: <drops>  - Risk: <regression concern>
C) Manual layered merge (Recommended in most cases)
   - What it does: <plain English>  - Risk: <specific concern>  - ETA: <minutes>

Recommendation: <X, usually C>. Reason: <one sentence>.

Choose: A / B / C
```

Anti-patterns: asking user "which side?" for Class A/B/C (autoresolver's job); auto-resolving Class D/E silently (silent code-regression risk); picking one side of Class B instead of unioning (destroys orchestration state); escalating "main not clean" / "main is behind" / "main is ahead" at preflight (preflight handles those silently).

### Class S — Silent semantic collisions (AUTO-MERGED files; advisory, self-scoping)

> Git flags conflicts only where diffs **textually overlap** — so two branches that each rewrote the same behavior in different places merge "cleanly" into code carrying two competing implementations, and whichever check runs first wins silently. **"Auto-merged" ≠ "safe."** (HAS-569 ← main, 2026-07-20: 4 confirmed instances, 3 with zero conflict markers — including an obsolete flag-gated write path that would have silently won over the shipped architecture.)

**Mechanical trigger (compute once per merge; empty set ⇒ advisory self-skips at zero cost):**
`BOTH_TOUCHED_AUTOMERGED = (git diff --name-only BASE..SOURCE) ∩ (git diff --name-only BASE..TARGET) − <conflicted-file set>`
Risk scales with divergence-over-shared-subsystem (long-lived worktree branch + high-velocity target). For each file in the set within source-code subsystems: isolate both parents (`git show HEAD:<f>` / `git show MERGE_HEAD:<f>`), symbol-grep guard keys / feature flags for orphaned writers-vs-readers, and route any **rival flag-gates over the same behavior** to an explicit supersession ruling — never let first-checked-flag-wins decide. Full procedure + worked examples: `protocol-spec.md` §Class S.

---

## When to use this command

| Trigger | Mode |
|---|---|
| Single ticket ready to merge from worktree → main | `/jMerge --source TICKET-XXX --target main` (defaults to lean single-source path) |
| Explicit lean single-source path | `/jMerge --quick --source TICKET-XXX --target main` |
| Force full protocol after lean rejection or user request | `/jMerge --full --source TICKET-XXX --target main` |
| Optional jArchitect advisory merge plan | `/jMerge --arch-plan --source TICKET-XXX --target main` (also available with `--sources ...`; opt-in only) |
| Single-ticket merge from /jClose | `/jMerge --quick --source TICKET-XXX --target main --mode close-ticket --plan <PLAN_FILE>` where `<PLAN_FILE>` is `.jswarm/plans/TICKET-XXX.plan.*.md` (new) or `docs/plans/TICKET-XXX-*.md` (legacy) |
| Multiple tickets ready to merge into main | `/jMerge --sources TICKET-XXX,TICKET-YYY[,...] --target main` |
| Resume an interrupted merge | `/jMerge --resume <merge-plan-path>` (or auto-detect lock; rejects `*.arch-merge-plan.*.md`) |
| Read-only status of an active merge | `/jMerge --status` |

`--arch-plan` is mutually exclusive with `--resume` and `--status`, and requires exactly one of `--source` or `--sources` plus `--target`. `/jMerge --resume` MUST reject `*.arch-merge-plan.*.md` with: `REFUSED: advisory Step 0.plan is not runtime resume state; use --arch-plan with source(s) and target.`

---

## Step 0: Read project merge config (REQUIRED)

### Step 0 isolation check: executor handoff (FIRST OPERATION)

Before reading merge config or entering any merge step, determine whether this session is worktree-isolated: its checkout is not the shared integration checkout, or the isolation guard would refuse writes to the shared checkout.

- **Non-isolated:** continue with the existing Step 0 configuration read and normal flow below.
- **Isolated:** do **not** proceed to Step 1, acquire the shared-checkout lock, or attempt any target/shared-checkout operation. The lane must not die at Step 1; that is the defect this handoff path prevents. Compose a prepared executor handoff and stop cleanly.

Before declaring the handoff, the **LANE** pushes each final source-branch commit and declares the exact final commit SHA for each source. The lane then freezes every source branch: no commit may be created after declaration. A post-declaration commit voids the handoff and requires a new push and re-declaration.

The prepared handoff is a message to the session's boss/orchestrator containing all of the following:

```text
Ticket(s): <source ticket(s)>
Source branch(es): <one source branch per ticket>
Declared final source SHA(s): <exact SHA for each source branch>
Target branch: <target branch>
Plan path(s): <one source plan path per ticket>
Merge-preflight receipts already produced in lane: <paths or N/A>
Isolation finding: <why this checkout is not the shared integration checkout>
Pin/freeze: lane pushed and declared <exact SHA(s)>; source branches frozen after declaration
```

The **EXECUTOR** resumes from this handoff in the shared integration checkout, pins exactly the declared SHA in Step 2, and merges that SHA rather than the branch tip. The executor owns the merge, its verification, and the executor-routed plan-status flip described in `/jClose` integration below. The isolated lane performs no further merge or plan-status work after handing off.

This command is project-agnostic in protocol structure (state machine, gates, vocabulary, traceability) but project-specific in concrete commands (service names, ports, migration framework, admin user). Per-project values resolve at runtime from `${PROJECT_ROOT}/.claude/merge.config.json`.

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
MERGE_CONFIG_PATH="${PROJECT_ROOT}/.claude/merge.config.json"

if [ ! -f "$MERGE_CONFIG_PATH" ]; then
    echo "REFUSED: ${MERGE_CONFIG_PATH} is missing."
    echo "See ~/.claude/skills/jMerge/SKILL.md §Config schema for shape. Reference: ~/dev/hai-sim-engine/.claude/merge.config.json."
    exit 1
fi
```

Hold resolved values in working memory throughout the merge — DO NOT re-read mid-run unless the user explicitly modifies it.

### Required config keys

`backend_service`, `ui_url`, `admin_user`, `migration.{framework,exec_prefix,upgrade_cmd,current_cmd,heads_cmd}`, `workflow.{framework,applies_when_diff_touches,stale_session_clear_cmd}`, `smoke_uat.{scenarios_doc,non_droppable_scenario_ids,evidence_root}`, `merge_artifacts.plan_dir`, `lock.path_template`, `worktree.{path_template,cleanup_command}`. Full schema example in `protocol-spec.md` and reference implementation at `~/dev/hai-sim-engine/.claude/merge.config.json`.

**Removed (COM-303 A/C 6, D2):** `backend_health_url`, `backend_health_check_timeout_sec`, and `stack_restart_cmd` are no longer merge-config keys. The runtime-proof gate they fed (Step 7.1) now calls `$JINFRA --post-merge --repair --unattended-lifecycle`, which owns the restart/recreate mechanic and the health/currency/activation checks itself — the merge config keeps only lifecycle policy, not the mechanic.

> **Invocation (COM-303):** there is no `jInfra` on `PATH` and one must not be created. `$JINFRA` in this document means `"${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" "${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/jinfra_cli.py"`. `protocol-spec.md` §7.1 carries the one literal, copy-runnable form of the Step 7.1 gate — copy it from there, not from prose here.

### Framework conditional behavior

- `migration.framework == "none"` → skip Step 6 (Migration sequencing); MERGE_COMMITTED_UNVERIFIED → MIGRATIONS_APPLIED is no-op.
- `workflow.framework == "none"` → skip Step 7.2 (Stale session clearing); STACK_RESTARTED → STALE_SESSIONS_CLEARED is no-op. The `STACK_RESTARTED` state itself is unconditional and reached via the blocking `$JINFRA --post-merge` call at Step 7.1 (`protocol-spec.md` §7.1) — no per-project config key drives it anymore; there is nothing here for a project to opt out of.

### Optional quick-merge tuning

```json
{
  "quick_merge": {
    "enabled": true,
    "verify_cmd": "<cheap build/lint/typecheck, or empty>",
    "reject_paths": ["alembic/versions/**", "app/workflows/**",
                     "docs/architecture/architecture.uat-scenarios.md",
                     "package-lock.json", "pnpm-lock.yaml", "yarn.lock"],
    "allow_lockfile_changes": false,
    "run_smoke": false
  }
}
```

If absent, use default reject rules in §Q.2 and only run cheap verification commands documented for the project. Do not invent long browser/UAT runs in quick mode.

---

## Step 0.plan (OPTIONAL — runs before Step 0a): jArchitect merge plan

This is the sanctioned on-demand path to involve `jArchitect`; it preserves the lean-first default. Default lean single-source merges skip it and emit: `✓ Step 0.plan: skipped (opt-in; run with --arch-plan for a jArchitect merge plan)`.

Prerequisite: resolve Step 0 project merge configuration before evaluating Step 0.plan. Run this step only when the operator passes `--arch-plan` or explicitly asks for an advisory jArchitect merge plan. A lean rejection may offer `--arch-plan`, but MUST NOT dispatch `jArchitect` unless the operator explicitly accepts. Multi-source merges remain opt-in. `jArchitect` is read-only: survey with cheap git evidence only; do not checkout, merge, commit, push, or otherwise mutate merge state.

Write `${MERGE_CONFIG.merge_artifacts.plan_dir}/HAS-000.arch-merge-plan.<sources>-<date>.md`. This is an upfront advisory strategy document, DISTINCT from the runtime resume-state merge-plan used by `--resume <merge-plan-path>`.

The plan must cover the source(s)↔target diff scope; a Conflict Classification Policy preview (Classes A/B/C auto-resolve; D/E escalate); cross-ticket and feature-plan interactions; migration and NFR touchpoints (Step 0c.NFR and Step 6 sequencing); and plan status. It must recommend lean vs. `--full`, sequence multi-source merges, give a resolution strategy for each conflict class, call out risks, and state an explicit go/no-go recommendation.

> **Advisory only:** the plan informs Step 0a onward; it does not block, replace, or weaken any existing gate. Advisory-plan review or approval does not satisfy mutation confirmation, CHK-2, CHK-AM, CHK-9, or any other existing gate. After the operator reviews it, proceed to Step 0a.

---

## Step 0a: Main-sync preflight (SILENT unless escalation needed)

Run the main-sync portion of [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/preflight.md`](../../docs/merge/preflight.md). Auto-resolve gitignored chaff + ahead-only push + behind-only pull silently. Escalate ONLY for tracked-file dirt or diverged state. Emit one-line success log (`✓ Preflight (main-sync): ...`) before Step 0b.

---

## Step 0b: Source-side preflight (NEW — auto-commit source dirt, auto-push branch)

If a source worktree exists (`.claude/worktrees/<ticket-lower>/`), run the source-side portion of `preflight.md`:

1. **Classify uncommitted files in source worktree** using the 5-class taxonomy (above).
2. **Legacy zero-commits check (NEW — added 2026-05-11 Fix #5b):** if `git rev-list --count origin/main..HEAD` returns `0` (source branch has zero commits beyond main's base) AND working tree has any Class D dirt → route to **Step 0b.L Legacy migration commit flow** below. Skip the rest of Step 0b's auto-handle steps.
3. **Auto-handle (normal post-/jGo state):**
   - **Class A** (gitignored): ignore silently
   - **Class B** (Feature plans, doc plans): auto-commit with synthesized message `chore(${SOURCE_TICKET}): /jMerge auto-commit of plan + doc updates`
   - **Class C** (append-only logs/retros): same as B
   - **Class D** (source code/tests): ESCALATE — `/jGo` ran with Step 4P but didn't commit something; plain-language template
   - **Class E** (config/lockfiles): ESCALATE — semantic risk; plain-language template
4. **Auto-push source branch** with `-u origin <branch>` if no upstream, else `origin <branch>`. Emit one-line log: `✓ Source prep: <N> auto-commits, <branch> pushed`.

If source has no worktree (e.g., direct branch invocation): inspect the named source branch's tip vs origin; if local-only commits exist, push silently with the same one-line log.

**Rationale:** Developer should NEVER paste "commit + push" prep messages to a /jMerge agent. That is /jMerge's job. Per `feedback_merge_main_sync_preflight.md` PERMANENT (expanded 2026-05-11 to cover source-side, again 2026-05-11 for Fix #5b legacy handling).

### Step 0b.L: Legacy migration commit flow (NEW — 2026-05-11 Fix #5b)

Triggered when Step 0b step 2 detects zero commits on source branch AND Class D dirt in worktree. This happens for tickets where `/jGo` ran before Step 4P (per-phase commit + push discipline) was introduced — the implementation work exists only in the worktree.

Surface ONCE per legacy ticket:

```
🟡 Legacy uncommitted state detected for ${SOURCE_TICKET}.
Source branch has 0 commits beyond main; all implementation work is in
the worktree. This typically means /jGo ran pre-2026-05-11 (before
Step 4P per-phase commit discipline).

Files to be bundled into a single legacy-migration commit:
  Class A (gitignored): ignored (N files)
  Class B (plans/docs): <N files>
  Class C (logs/retros): <N files>
  Class D (source/tests): <N files>  ← bundled per legacy migration
  Class E (config/lockfiles): <N files>  ← bundled per legacy migration

Suggested commit message:
  feat(${SOURCE_TICKET}): implementation work (legacy migration commit)

  Bundled commit of ${SOURCE_TICKET} implementation that was completed
  before /jGo Step 4P (per-phase commit discipline) was introduced.
  All work was previously in the worktree only. See:
  ${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/protocol-spec.md §Step 0b.L

Options:
A) Bundle all + push (Recommended for legacy migration)
B) Show me the full diff first, then re-prompt
C) Abort /jMerge — I'll commit manually with proper per-phase messages

Choose: [A] / B / C  (default A — press Enter to accept)
```

**On `A` (default — happy path):**
1. Run `git status --short`, classify every dirty/untracked path into Class A-E, then stage only the enumerated legacy bundle with `git add <path>` for each confirmed path (Class A remains ignored)
2. `git commit -m "<synthesized message above, verbatim>"`
3. `git push -u origin <branch>` (with `-u` since no upstream by definition — zero commits means no upstream)
4. Emit log: `✓ Source prep (legacy migration): bundled <N> files into <sha>, <branch> pushed.`
5. Append entry to `${MERGE_CONFIG.merge_artifacts.plan_dir}/lean-rejection-log.md` (Step Q.5) with stage `Step 0b.L legacy migration` for calibration data
6. Continue to Step 0c (plan-status awareness)

**On `B`:** print `git diff --stat` and `git diff` summary (first 200 lines), then re-surface the prompt.

**On `C`:** abort /jMerge cleanly (no changes made), output:
```
/jMerge aborted. To commit manually:
  cd ${SOURCE_WORKTREE}
  git status --short
  git add <path>   # repeat for each reviewed file that belongs in the commit
  git restore --staged <path>   # unstage any accidental path
  git commit -m "<your message>"
  git push -u origin <branch>
Then re-run /jClose ${SOURCE_TICKET} or /jMerge --source ${SOURCE_TICKET} --target main.
```

**Why this design:**
- Legacy tickets are a one-time migration cost; `/jGo` Step 4P prevents recurrence for new tickets
- Default A means developer just presses Enter — minimum cognitive load
- Synthesized commit message documents the legacy nature for future git archaeology
- Bundling ALL classes (not just D/E) into one commit avoids requiring a coherent message-per-class, which IS the cognitive load we're eliminating
- Option B/C preserve developer control when they want it

**Anti-patterns:**
- Treating legacy migration as the *normal* Step 0b path — it's only triggered by zero-commits AND Class D dirt; normal post-/jGo tickets follow standard Step 0b
- Silently bundling without the prompt — Class D auto-commit without consent is the failure mode this safeguards against
- Forgetting to log to `lean-rejection-log.md` — calibration data tracks how often legacy migration fires; over time this should approach zero as old tickets close out

---

## Step 0c: Plan-status awareness (NEW — non-blocking informational gate)

Read the source plan's `status:` frontmatter field. Apply per `state-machine.md`:

```
ACTIVE           → warn once: "Plan still ACTIVE — looks like WIP merge. Proceed? Y/N (default Y)"
                   single prompt; not a block. Proceed with default Y if no answer in interactive flow.
READY_FOR_MERGE  → proceed silently. Emit: ✓ Plan status: READY_FOR_MERGE
DONE             → REFUSE: "Plan status is DONE — this would be a double-merge."
WONT_DO/DEFERRED → REFUSE: "Plan is deprecated. Do not merge."
<drift value>    → warn + proceed: "Plan status '<value>' is drift; treating as ACTIVE."
```

This is the only consumer of plan status in `/jMerge`. Step 0a/0b are status-agnostic.

---

## Step 0c.NFR: NFR catalog merge gate (NEW — fail-open)

When the project has adopted the COM-169 NFR-catalog engine (an `nfr-catalog-source` anchor), guard generated-file integrity before merging. The lifecycle gate self-skips non-adopted / `NFR catalog: N/A` projects (exit 0 — never blocks an unrelated merge):

```bash
.venv/bin/python jswarm/nfr-catalog/lifecycle_gate.py \
  --project-root "$REPO_ROOT" --plan "$PLAN_FILE" --stage merge \
  ${NFR_GENERATED_FRESH:+--generated-fresh "$NFR_GENERATED_FRESH"} --json
```

(Pass `--generated-fresh` only when a boolean was actually computed — the `${NFR_GENERATED_FRESH:+…}` guard omits the flag when the variable is unset, so a non-adopted project's merge is never broken by an empty value. The gate self-skips non-adopted/N/A projects regardless.)

`block-stale-md` ⇒ the canonical NFR JSON changed without regenerating its `.md` — regenerate via `render_nfr_catalog.py` before merging. Additionally, for **adopted** projects run the engine's own merge gate as **separate** invocations (the three modes are mutually exclusive — never combine them on one line; all require `--catalog`/`--config`/`--schema` as applicable):

```bash
# generated-file freshness
.venv/bin/python jswarm/nfr-catalog/merge_gate.py --freshness \
  --config "$NFR_MERGE_CONFIG" --catalog "$NFR_CATALOG_JSON" \
  --schema jswarm/nfr-catalog/schema/nfr-catalog.schema.json --json

# unpaired-generated-edit guard (changed-files list from the merge diff)
git diff --name-only "$BASE_REF"...HEAD > "$CHANGED_LIST"
.venv/bin/python jswarm/nfr-catalog/merge_gate.py --guard-changeset \
  --config "$NFR_MERGE_CONFIG" --catalog "$NFR_CATALOG_JSON" \
  --schema jswarm/nfr-catalog/schema/nfr-catalog.schema.json --changed "$CHANGED_LIST" --json
```

Non-adopted projects skip both (no NFR merge gate).

---

<!-- dashboard-facts:contract command=merge mode=append-fold-compose-check -->
<!-- dashboard-facts:contract-marker-doc marker convention: each machine cell owned by this command must declare event append via dashboard-facts:machine-cell, and any remaining JSON write must declare an allowed narrative-json-write marker. -->
<!-- dashboard-facts:machine-cell=story_progress via=event-append -->
<!-- dashboard-facts:narrative-json-write fields=recency,changelog -->

## HAS-447 dashboard close protocol — COM-103 fold-primary cutover

When the target project has a feature dashboard data source and the Feature Dashboard System renderer, `/jMerge` owns the close-side story_progress `done` fact exactly once after the runtime-proof gate passes and the merge SHA is known.

1. After the runtime-proof gate passes and the merge SHA is known, append one typed story_progress `done` event with `merge_sha`, `done_at`, terminal `phase`, `total_phases`, source command `/jMerge`, and deterministic `--at`:

```bash
COMMON_ROOT="${JSWARM_HOME:-$HOME/dev/jswarm}"
PYTHONPATH="$COMMON_ROOT/scripts" "$COMMON_ROOT/.venv/bin/python" -m dashboard_facts.cli story-progress done   --repo-root "$REPO_ROOT"   --feature-key "$PARENT"   --ticket-key "$TICKET"   --phase "$TOTAL_PHASES"   --total-phases "$TOTAL_PHASES"   --done-at "$DONE_AT"   --merge-sha "$MERGE_SHA"   --source-command /jMerge   --at "$AT"
```

2. Fold story_progress after the append, then compose the feature dashboard from narrative JSON plus folded machine facts.
3. The folded story_progress read model is the source for derived master_plan presentation: ticket tracker Ring 3 status, sequencing-flowchart done class, and sequencing-gantt done modifier are generated from the folded lifecycle state during composition/rendering, not hand-mutated in dashboard JSON.
4. Continue JSON write-set updates only for narrative recency and changelog fields if the protocol requires them; do not use JSON machine-cell edits for story_progress or derived master_plan lifecycle/status state.
5. Render with `--markdown-output`, then run `--check`. HTML, publish mirror, and managed markdown blocks must all be fresh before push. The executable render/check gate keeps the same flag surface:

```bash
COMMON_ROOT="${JSWARM_HOME:-$HOME/dev/jswarm}"
PYTHONPATH="$COMMON_ROOT/scripts" "$COMMON_ROOT/.venv/bin/python" -m dashboard_facts.cli fold story-progress --repo-root "$REPO_ROOT" --at "$AT"
PYTHONPATH="$COMMON_ROOT/scripts" "$COMMON_ROOT/.venv/bin/python" -m dashboard_facts.compose_feature_dashboard --narrative-json "$DASHBOARD_DATA" --repo-root "$REPO_ROOT" --output "$COMPOSED_DASHBOARD" --at "$AT" --feature-key "$PARENT"
.venv/bin/python "$DASHBOARD_RENDERER" --scoreboard "$COMPOSED_DASHBOARD" --schema "$DASHBOARD_SCHEMA" --template "$DASHBOARD_TEMPLATE" --output "$DASHBOARD_OUTPUT" --feature-key "$PARENT" --title "$PARENT Feature Dashboard" --markdown-output "$DASHBOARD_MARKDOWN_OUTPUT" ${DASHBOARD_PUBLISH_OUTPUT:+--publish-output "$DASHBOARD_PUBLISH_OUTPUT"} --check
.venv/bin/python "$DASHBOARD_RENDERER" --scoreboard "$COMPOSED_DASHBOARD" --schema "$DASHBOARD_SCHEMA" --template "$DASHBOARD_TEMPLATE" --output "$DASHBOARD_OUTPUT" --feature-key "$PARENT" --title "$PARENT Feature Dashboard" --markdown-output "$DASHBOARD_MARKDOWN_OUTPUT" ${DASHBOARD_PUBLISH_OUTPUT:+--publish-output "$DASHBOARD_PUBLISH_OUTPUT"}
```

6. Final push is blocked if append, fold, compose, render, markdown render, or check fails in fold-primary mode. `/feature-reconcile` remains the backstop for narrative/dashboard drift, not the routine machine close-flip writer. /feature-reconcile remains the backstop when drift survives merge.

---

## Step ENV-P: Gitignored runtime-config parity gate (MANDATORY in both lean and full paths — added 2026-08-05, owner-directed from HAS-593)

**Why this exists:** `.env` and its sidecar env files are gitignored, so a merge carries NONE of the runtime configuration a ticket accumulated in its worktree — and the target checkout's stack silently drifts. Proven failure class (HAS-593 B-ledger): missing LLM routing slots detonating mid-build (B6), a lost E2E auth secret (B8), a feature flag lost in an env regeneration turning an endpoint family into 503s (B10). Merges that ignore env files perpetuate exactly this class.

**Mechanic (key NAMES only — never print values):**

1. Enumerate the env files to compare: `merge.config.json` optional key `env_parity.files` (array of repo-relative names; default `[".env"]`, projects with identity/config sidecars list them, e.g. `[".env", ".env.has585-deployment-identity"]`).
2. For each file, key-set diff (`grep -E '^[A-Za-z_][A-Za-z0-9_]*=' <file> | cut -d= -f1 | sort -u`, then `comm`) between the SOURCE worktree copy and the TARGET checkout copy — **both directions** (keys the ticket added that main lacks; keys main gained that the worktree never saw).
3. **Clean:** emit one line `✓ env parity: key sets match (<N> files)` and continue.
4. **Drift found:** STOP at the pre-push checkpoint with a keys-only disposition table and require an operator decision per key:
   - **port** — append-only into the target file (operator supplies/approves the value; typical for feature flags, thresholds, routing slots),
   - **skip** — stack-specific keys that must NOT cross checkouts (ports, per-stack URLs/endpoints, worktree offsets),
   - **credential** — never auto-ported; operator handles out-of-band per project credential rules.
   Never auto-copy values silently: a blind copy cross-wires stacks (port offsets) or leaks credentials into the wrong checkout.
5. Record the disposition table (keys + decisions only) in the merge evidence; after any port, remind the operator that running containers hold creation-time env — the affected stack needs a recreate before the next runtime verification (container-vs-file drift class).

This gate is parity-*visibility*, not parity-*enforcement*: an approved skip list is a normal outcome. The failure it prevents is the SILENT one.

### ENV-P.2 — The wider non-git runtime surface (owner-widened 2026-08-05, HAS-587 fold-in: ".env was an EXAMPLE, not the list")

Env files are one member of a class: **everything the running stack consumes that a git merge cannot carry or update.** A merge conflict is loud; an untracked or environment-derived artifact is *silently loyal to the past* — the stack boots fine and fails on a renamed model identifier, a missing dependency, or a stale compiled module. Build the inventory MECHANICALLY (`git status --short --ignored` + resolved `docker compose config` for every env_file/volume/override the stack actually consumes), never from memory. Minimum checklist, each with a row in the merge evidence (file → consumed-by → drift? → action/flag):

1. **Compose overrides & stack identity** — untracked `docker-compose.override.yml`/`compose.override.yaml`, `COMPOSE_PROJECT_NAME`, per-stack port mappings: preserve the target stack's identity (never let the other checkout's ports/names leak in); confirm `docker compose config` resolves cleanly post-merge with the expected services/ports.
2. **Dependency environments** — if the merge changes `requirements*/pyproject/uv.lock` or `package.json`/JS lockfiles, the local `.venv`/`node_modules` are now BEHIND the code: sync them BEFORE any post-merge verification runs (test results against a stale venv are meaningless), and flag bind-mounted hot containers that need recreate/reinstall.
3. **Tool/router config files outside the env** — LLM router configs, LiteLLM yaml, provisioner-emitted sidecars: find what the merged code actually loads and diff it, tracked or not.
4. **Runtime state files** — guard-state JSONs, `.jswarm/state/**`, DB volumes: NEVER copy across checkouts; flag only when merged code newly *requires* one that is absent on the target.
5. **Direction-agnostic** — this gate applies equally to close-time merges (worktree→target) and mid-ticket fold-ins (main→worktree); in the fold-in direction the untracked files are not at risk of being clobbered but of being STALE, which is the same silent failure wearing the other glove.

For exactly one source branch, `/jMerge --source ...` MUST start here by default. `/jMerge --quick` is the explicit spelling. Full protocol is opt-in via `/jMerge --full` or evidence-triggered after this path refuses.

**Owner:** `git-master`. Performs: fetch, pin SHA, status-classified target handling, merge, conflict detection, commit, optional push, remote source branch deletion, final status. Supporting agents only by explicit escalation: `executor` for cheap verification; `architect` only after lean refuses + user chooses full, except when the operator explicitly requests the read-only Step 0.plan advisory through `--arch-plan`; `architect-master` never automatic.

### Q.2 — Eligibility

Lean mode may proceed only when all are true:
- Exactly one source branch/ticket
- Target has no active merge/rebase/cherry-pick/unresolved index state
- Source branch exists and SHA is pinned after `git fetch origin`
- Source worktree (if present) has no substantive uncommitted changes (Class D/E)
- Source diff does not touch any configured `quick_merge.reject_paths`
- Source diff does not touch migrations, workflow/runtime files, official UAT inventory, shared UI shell files, or dependency lockfiles unless project config explicitly allows that class
- The Class S both-touched-auto-merged set (see Conflict Classification Policy) is EMPTY within source-code / reject-path subsystems — a non-empty set is precisely where lean's cheap verification would ship a silent semantic collision

Default reject paths when config absent: `alembic/versions/**`, `app/workflows/**`, `docs/architecture/architecture.uat-scenarios.md`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `poetry.lock`, `uv.lock`.

### Q.3 — Dirty target handling is status-classified, not complexity

Dirty files on target are not swept into a blanket stash. Start with `git status --short`, confirm the current branch/upstream/ahead-behind state, and classify dirty/untracked paths as expected local noise vs. unrelated work before any merge step. Wrong branch, unexpected branch, branch mismatch, unexpected untracked scope, or unrelated work pauses and escalates instead of being hidden.

```bash
git status --short
git stash push -m "/jMerge --quick preflight stash <timestamp>" -- <path>
# ... merge ...
git stash pop
```

Use the path-limited stash only after the user explicitly chooses that option for the listed path(s). Prefer leaving unrelated work untouched, or staging merge-owned changes surgically with `git add <path>` and undoing accidental staging with `git restore --staged <path>`. If stash pop conflicts after merge: report it as **local workspace conflict after lean merge**, not source-branch merge complexity. The merge result remains intact.

Hard block if path-limited stash creation fails, repo is on the wrong/unexpected branch, repo is in active merge/rebase/cherry-pick, unexpected untracked or unrelated work is present, or source worktree itself is dirty (Class D/E).

### Q.4 — Lean transaction

1. Read `.claude/merge.config.json`
2. Acquire target lock (normal lock path)
3. Fetch origin, pin source SHA
4. Run Q.2 eligibility + Q.3 stash handling
5. Merge source into target
6. **If git reports actual merge conflicts:**
   a. Classify each conflicted file per the Conflict Classification Policy (Class A-E above)
   b. Auto-resolve Class A (discard source), Class B (UNION RULE), Class C (chronological concat) silently per policy. Emit one-line log per class: `Class A: discarded N files. Class B UNION: kept <nodes>. Class C concat: <N>+<M> entries.`
   c. If zero Class D/E remain → continue lean (step 7)
   d. If any Class D/E remain → escalation flow in step 6.1 (do NOT abort merge state; A/B/C resolutions stay staged)
6.1. **Class D/E escalation in lean mode** (NEW — added 2026-05-11 Fix #1):
   - For each Class D/E conflicted file, surface the plain-language escalation template (above) WITH an appended **Option D: switch to full protocol from here**
   - Wait for user choice per file:
     - **A/B/C** → apply choice (manual layered merge for C), stage resolution, continue lean
     - **D** → abort the lean state cleanly (Class A/B/C resolutions stay in the index), transition to full protocol Step 4 (CONTRACT_WRITTEN preparation) using the in-flight merge state. Record the lean→full transition in `lean-rejection-log.md` (Step Q.5) with stage `Q.4 conflict resolution`.
7. Run configured cheap verification only (`quick_merge.verify_cmd`, optional)
8. Commit merge with source ticket in message
9. **Run the Step 7.1 jInfra runtime proof gate (MANDATORY, blocking — COM-303 A/C 6, D2/D8):** the exact same `--post-merge --repair --unattended-lifecycle --force --since "${SINCE_REF}"` gate as the full path, reading the `target_sha_pre_merge` anchor pinned at Step 2. **Copy the literal command from `protocol-spec.md` §7.1** — it is the single copy-runnable form of this mechanic (absolute-path invocation; there is no `jInfra` on `PATH`), and it must not be re-typed or re-described here. Continue only after its receipt validator accepts and durably publishes the result. An accepted jInfra exit `5` is a forced completion, not certified green; any validator/evidence failure blocks push. Route every result exactly as `protocol-spec.md` §7.1 specifies — do not stop at "restart/health/smoke are optional" for lean; this gate is not optional in either path.
9.6. **Run Step ENV-P (gitignored runtime-config parity gate, MANDATORY):** key-set diff the source worktree's env files against the target checkout's per the ENV-P mechanic; clean → one-line log; drift → the keys-only disposition table becomes part of the step-10 checkpoint and push waits for the operator's per-key decisions.
10. Surface concise pre-push checkpoint: source SHA, merge commit SHA, verification output, jInfra gate result, **ENV-P parity result (match line or disposition table)**, stash status, with exactly this section:

    ```markdown
    ## jInfra Step 7.1 gate
    - evidence: `<repository-relative JINFRA_GATE_EVIDENCE>`
    - outcome: `<ordinary-completion|forced-completion-not-green>`
    - jinfra_process_exit: `<0|5>`
    - receipt_exit_code: `<0|5>`
    - applied_waiver_codes: `<comma-separated unique codes, or none>`
    - executed_repair_kinds: `<comma-separated kinds, or none>`
    - reproof_status: `<pass|none>`
    ```
11. If user approves: push target + delete remote source branch (same safety invariant as §10.3)
12. Pop preflight stash if one was created
13. Trigger post-merge worktree teardown (Step 10.8)
14. Release lock and report `MERGE_LEAN_GREEN`

Lean mode MUST NOT write `uat-merge.md`, dispatch browser UAT, run critic, perform UAT contract negotiation, create a merge worktree, or escalate to architecture agents WITHOUT user consent. When lean cannot stay lean, there are two escalation surfaces:

- **At Q.2 eligibility refusal** (reject_paths matched, multi-source, repo state bad, source dirt Class D/E) — surface a single Y/N prompt:

  ```
  🟡 Lean rejected: <reason>
  Switch to full protocol now? [Y/n] (default Y)
  ```

  On `Y` (or no answer in interactive flow): transition state UNLOCKED → LOCKED via full protocol Step 1; continue from there. On `n`: stop with the explicit `/jMerge --full --source ...` command for later re-invocation. Either way, append entry to `lean-rejection-log.md` (Step Q.5).

- **At Q.4 conflict resolution** (Class D/E surfaced mid-merge) — use the escalation flow in Step 6.1 above. Option D ("switch to full protocol") is always available; selecting it transitions to full Step 4 with the in-flight merge state preserved (Class A/B/C resolutions stay in the index).

### Q.5: Lean rejection telemetry (NEW — added 2026-05-11 Fix #4)

Whenever lean is rejected at Q.2 (eligibility) or Q.4 (Class D/E surfaced), append to `${MERGE_CONFIG.merge_artifacts.plan_dir}/lean-rejection-log.md` (create file lazily on first rejection per project — do NOT pre-create empty log files):

```markdown
## YYYY-MM-DD HH:MM — TICKET-XXX lean rejection

- **Stage:** Q.2 eligibility | Q.4 conflict resolution
- **Reason:** <which eligibility check failed | Class D file <path> | Class E file <path> | source dirt Class D/E at Step 0b>
- **Files touched (if reject_paths-related):** <list>
- **User decision:** switched to full immediately | declined (will re-run later with --full) | resolved manually in lean (Option A/B/C from escalation)
- **Final outcome:** MERGE_LEAN_GREEN | MERGE_FINAL_GREEN | aborted | <unknown at write time — append when known>
- **Merge SHA (if completed):** <sha or N/A>
```

**Purpose:** quarterly calibration evidence for `quick_merge.reject_paths` tuning. If certain paths frequently trigger rejection AND the resulting full merges pass critic-green with no real risk surfaced, the path is over-conservative (remove it from reject_paths). If rejections often correlate with smoke failures or critic CRITICAL findings, the path is well-calibrated (keep it).

**When NOT to write to this log:**
- Successful lean transactions (the log is specifically a *rejection* log)
- Q.4 step 6 cases where ONLY Class A/B/C conflicts surfaced and were auto-resolved silently (not a rejection — lean stayed in lean)
- Step 0a/0b preflight escalations that aren't lean-vs-full routing decisions (those have their own one-line success logs)

**Anti-patterns:**
- Pre-creating empty `lean-rejection-log.md` in new projects — lazy creation only
- Skipping the entry when lean transitions to full via Option D mid-transaction — that IS a rejection event and needs entry for calibration
- Treating the log as a general "merge history" — it's narrow-scope: only rejection events
- Quarterly review without correlating to critic/UAT outcomes — calibration needs the FULL signal, not just rejection frequency

---

## Step 1: Acquire target lock (UNLOCKED → LOCKED)

```bash
mkdir -p .jswarm/state
LOCK_PATH=".jswarm/state/merge-lock-${TARGET}.json"

if [ -f "$LOCK_PATH" ]; then
  EXISTING_SESSION=$(jq -r .session_id "$LOCK_PATH")
  if [ "$EXISTING_SESSION" != "$CURRENT_SESSION_ID" ]; then
    echo "REFUSED: another /jMerge is in progress (session $EXISTING_SESSION)"
    /jMerge --status
    exit 1
  fi
  # Same session → resume mode (see protocol-spec.md)
fi
```

Lock file shape and orphan recovery (`MERGE_HEAD` without lock): see `protocol-spec.md` §Lock acquisition.

---

## Step 2: Pin source SHAs (LOCKED → SOURCES_PINNED)

```bash
git fetch origin
# Executor-routed handoff sets DECLARED_FINAL_SOURCE_SHA from the handoff.
if [ -n "${DECLARED_FINAL_SOURCE_SHA:-}" ]; then
  SOURCE_REF_SHA=$(git rev-parse "origin/feat/${SOURCE_TICKET}")
  [ "$SOURCE_REF_SHA" = "$DECLARED_FINAL_SOURCE_SHA" ] || BLOCK "source branch advanced after declaration; require re-declaration"
  SOURCE_SHA=$(git rev-parse "${DECLARED_FINAL_SOURCE_SHA}^{commit}")
  [ "$SOURCE_SHA" = "$DECLARED_FINAL_SOURCE_SHA" ] || BLOCK "declared source SHA is not the exact pinned commit"
else
  SOURCE_SHA=$(git rev-parse "origin/feat/${SOURCE_TICKET}")
fi
# Update lock JSON sources[].sha
```

Re-verify SHAs before EVERY subsequent state transition. If any source ref has advanced since pinning: BLOCK and re-plan. Write checkpoint `02-SOURCES_PINNED`.

**Target anchor (COM-303 A/C 6, D2/D8):** in this same step, once per merge, also capture the target's pre-merge tip and write `target_sha_pre_merge` into the lock JSON — this is the anchor the Step 7.1 jInfra runtime proof gate reads via `--since`, captured before the merge transaction touches the target (see `protocol-spec.md` §SHA pinning and §7.1 for why deriving it later from the merge commit's topology is unsound):
```bash
TARGET_SHA_PRE_MERGE=$(git rev-parse "${TARGET}")
# Update lock JSON target_sha_pre_merge
```

### Pin-freeze final-commit handshake (executor-routed merges)

- **LANE:** push each final source-branch commit, declare each exact SHA in the prepared handoff, and freeze every source branch. Any commit after declaration voids the handoff and requires re-declaration before the executor acts.
- **EXECUTOR:** pin each declared SHA from the handoff, verify that it is the intended source commit, and merge exactly those SHAs. Never resolve or merge a moving source-branch tip after an executor handoff. A missing, changed, or mismatched declared SHA blocks the merge and requires a fresh lane declaration.

For non-isolated merges there is no executor handoff; the existing source-ref pinning flow remains unchanged.

---

## Step 3: Preflight gates (SOURCES_PINNED → CONTRACT_WRITTEN preparation)

All must pass before contract negotiation begins. Summary; full detail in `protocol-spec.md` §Preflight gates:

- **3.1 Clean target** — main in sync with origin/main; no in-progress merge/rebase/cherry-pick; tracked-file dirt either stashed (Q.3) or escalated
- **3.2 Clean sources** — worktrees have no Class D/E uncommitted mods; plan status `READY_FOR_MERGE` (per `state-machine.md`)
- **3.3 Official UAT inventory freshness (BLOCKING)** — every shipped user-visible behavior reflected in `architecture.uat-scenarios.md`. Resolution: update inventory first OR record explicit user-approved exception

<!-- uat-scenarios:merge-gate -->
When a project has adopted the COM-122 UAT-scenario engine, the UAT-inventory freshness gate targets the **canonical JSON**: run `jswarm/uat-scenarios/merge_gate.py --freshness` (renderer `--check` — the generated Markdown must match the JSON) and `--validate-config` (the `merge.config.json` `smoke_uat.scenarios_json` is present and `non_droppable_scenario_ids` includes every exit-gating canonical smoke id). Direct edits to the generated Markdown are rejected by `--guard-changeset` unless paired with a canonical JSON change plus fresh `--check`. Non-adopted projects keep the markdown-inventory freshness gate.

- **3.4 UAT documentation style discipline (BLOCKING)** — `*.uat-scenarios.md` is human-readable expectations only; automation belongs in `*.uat-test.md`

---

## Steps 4–9: Full protocol (CONTRACT_WRITTEN → CRITIC_GREEN)

Full protocol is opt-in via `/jMerge --full` or evidence-triggered after lean rejection. See `protocol-spec.md` §Architect dispatch through §Critic review for deep semantics. Key transitions:

| Transition | Owner | Gate |
|---|---|---|
| CONTRACT_WRITTEN → MERGE_STARTED | architect (architect-master only via CHK-AM) | CHK-2 (contract review, MANDATORY PAUSE) |
| MERGE_STARTED → MERGE_COMMITTED_UNVERIFIED | architect / executor | CHK-3 optional for runway-class |
| MERGE_COMMITTED_UNVERIFIED → MIGRATIONS_APPLIED | hephaestus / executor | revision uniqueness + single head |
| MIGRATIONS_APPLIED → STACK_RESTARTED → STALE_SESSIONS_CLEARED → SMOKE_GREEN | hephaestus + qa-tester-uat | Step 7.1 jInfra runtime proof gate (`protocol-spec.md` §7.1, blocking) + CHK-4 (smoke gate) + CHK-5 (smoke results) |
| SMOKE_GREEN → FULL_UAT_GREEN | qa-tester-uat | CHK-6 (gate) + CHK-7 (results) |
| FULL_UAT_GREEN → CRITIC_GREEN | critic | CHK-8 (verdict) |

Phase-bounded delegation (per `feedback_subagent_crash_recovery.md` PERMANENT): each phase writes a checkpoint and returns before next phase starts. No single agent owns >100 tool calls.

**Contract completeness (Class S — MANDATORY at CHK-2):** the merge contract MUST enumerate the both-touched-auto-merged file list (Conflict Classification Policy §Class S) and assign each file an explicit semantic-verification action alongside the conflicted-file strategies. A contract lacking this list is INCOMPLETE and fails CHK-2 review — verifying auto-merged files must be mandated, not left to the architect happening to notice one.

---

## Step 10: Push + cleanup (CRITIC_GREEN → PUSHED → CLEANED)

### 10.1 — CHK-9 (Pre-push confirmation, MANDATORY PAUSE)

First point where vocabulary `complete` / `verified` / `working` becomes permitted. All gates have passed. Surface final summary: evidence pointers, source SHAs, smoke + full UAT results, critic verdict, **and the Step ENV-P parity result** (run it now if the full path reached this point without it — the match line, or the keys-only disposition table awaiting per-key decisions; push does not proceed with undispositioned env drift). User confirms.

### 10.1a — COM-100 dual-render close-flip set (COM-103 fold-primary)

The machine close-flip is the single `story_progress done` event appended in the
HAS-447 dashboard close protocol above. The folded story_progress read model is the
source for derived master_plan presentation: ticket tracker Ring 3 status,
sequencing-flowchart done class, and sequencing-gantt done modifier are generated
from the folded lifecycle state during composition/rendering, not hand-mutated in
dashboard JSON. Continue JSON write-set updates only for narrative recency and
changelog fields. Render and check with `--markdown-output` (and `--publish-output`
when configured) so HTML, publish mirror, and managed markdown blocks are fresh
before push. `/feature-reconcile` remains the source-of-truth backstop only when
drift survives merge, never the routine machine close-flip writer.

### 10.2 — Push to origin

```bash
git push origin "${TARGET}"
[ "$(git rev-parse "${TARGET}")" = "$(git rev-parse "origin/${TARGET}")" ] || BLOCK "push did not advance origin"
```

### 10.3 — Delete remote source branches

Only after target push verified:

```bash
for source in $SOURCES; do
  git push origin --delete "feat/${source}" || BLOCK "failed to delete origin/feat/${source}"
done
git fetch --prune origin
for source in $SOURCES; do
  remote_lookup=$(git ls-remote --heads origin "feat/${source}")
  [ -z "$remote_lookup" ] || BLOCK "remote branch origin/feat/${source} still exists after delete"
done
```

If deletion blocked by permissions/protection: surface error and stop before Jira finalization.

### 10.4 — Jira finalization

Use the retry-safe helper, not one-shot MCP calls:

```bash
MERGE_SHA=$(git rev-parse "${TARGET}")
for source in $SOURCES; do
  cat > "/tmp/${source}.merge-final-comment.md" <<EOF
## Merge Finalized
**Target:** ${TARGET}
**Merge SHA:** ${MERGE_SHA}
**Status:** MERGE_FINAL_GREEN
EOF

  ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/jira_mcp_closeout.py \
    --ticket-context "${source}" comment "${source}" \
    --body-file "/tmp/${source}.merge-final-comment.md" \
    || BLOCK "Jira merge comment failed for ${source}"

  ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/jira_mcp_closeout.py \
    --ticket-context "${source}" transition-done "${source}" \
    --comment-file "/tmp/${source}.merge-final-comment.md" \
    || BLOCK "Jira Done transition failed for ${source}"
done
```

### 10.5 — CHK-10 (Cleanup confirmation, MANDATORY PAUSE for full protocol; auto-confirm in lean/surgical)

For full protocol: show worktrees + Docker resources that will be torn down; user can keep any. For lean/surgical: auto-proceed (user's CHK-9 directive at 10.1 already implies finalization scope).

### 10.6 — Worktree + Docker teardown (AUTONOMOUS post-push; accounted outcomes)

After Step 10.3 deletion of `origin/feat/${source}` verifies clean, all real work is captured on main and the remote source branch is gone. `/jMerge` owns invoking project cleanup. The operator never bypasses the COM-244 envelope guard, mints envelopes, or performs low-level teardown. The sole lawful operator prerequisite is a live lease: close the session for that worktree, then re-run the same recorded cleanup command.

Pass `--force` to the cleanup script unconditionally. It forces **only** ordinary Git worktree-preservation checks. It cannot override the COM-244 envelope guard, a live lease, ambiguous watcher identity, an invalid/wrong-target/expired envelope, producer refusal, cutoff byte inequality, or the raw-PID prohibition.

`|| true` is deliberately gone. It discarded the cleanup exit status, which is the exact defect that lets `CLEANUP_FAILED` exit 0. Step 10.6 must capture and classify the cleanup terminal line and process exit status together. The verified zsh classifier follows verbatim; do not translate it to bash or reformat it.

```zsh
: "${MERGE_ARTIFACT_DIR:?MERGE_ARTIFACT_DIR must resolve merge_artifacts.plan_dir}"
: "${MERGE_RUN_ID:?MERGE_RUN_ID must identify this merge run}"

cleanup_log_dir="${MERGE_ARTIFACT_DIR}/merge-step-10.6-${MERGE_RUN_ID}"
cleanup_results_file="${cleanup_log_dir}/results.log"

cleanup_setup_rc=0
if mkdir -p "$cleanup_log_dir"; then
  if : > "$cleanup_results_file"; then
    :
  else
    cleanup_setup_rc=$?
  fi
else
  cleanup_setup_rc=$?
fi

cleanup_cleaned=0
cleanup_deferred=0
cleanup_blocking=0
cleanup_reporting_failed=0

# `${=SOURCES}` requests word splitting explicitly because zsh does not
# word-split an ordinary scalar expansion by default. Plain `$SOURCES` would
# process a whitespace-separated scalar as ONE source.
for source in ${=SOURCES}; do
  printf -v source_q '%q' "$source"
  cleanup_cmd="bash jswarm/worktree-cleanup.sh ${source_q} --force"
  cleanup_log="${cleanup_log_dir}/${source}.log"

  cleanup_rc=0
  log_replay_rc=0
  terminal_count=0
  terminal_line=""
  state=""
  reason=""

  if [ "$cleanup_setup_rc" -eq 0 ]; then
    # No pipeline: this status is unambiguously the cleanup process status.
    # The if/else also prevents an inherited `set -e` from aborting the loop.
    if bash jswarm/worktree-cleanup.sh "${source}" --force \
        > "$cleanup_log" 2>&1; then
      cleanup_rc=0
    else
      cleanup_rc=$?
    fi

    # Replay the persisted output to the operator after this source finishes.
    if [ -r "$cleanup_log" ]; then
      if command cat -- "$cleanup_log"; then
        :
      else
        log_replay_rc=$?
      fi
    else
      log_replay_rc=1
    fi

    if [ -r "$cleanup_log" ]; then
      while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
          "[STATE] CLEANUP_OK"*|\
          "[STATE] CLEANUP_FAILED"*|\
          "[STATE] CLEANUP_HELD"*)
            terminal_count=$((terminal_count + 1))
            terminal_line="$line"
            ;;
        esac
      done < "$cleanup_log"
    fi
  else
    # Durable capture could not be initialized. Do not run destructive cleanup
    # without the required per-source evidence, but still classify every source.
    cleanup_rc="$cleanup_setup_rc"
    log_replay_rc="$cleanup_setup_rc"
  fi

  if [ "$terminal_count" -eq 1 ]; then
    state="${terminal_line#"[STATE] "}"
    state="${state%% *}"

    case " $terminal_line " in
      *" reason="*)
        reason="${terminal_line#* reason=}"
        reason="${reason%% *}"
        ;;
    esac
  fi

  if [ "$cleanup_setup_rc" -ne 0 ] || \
     [ "$log_replay_rc" -ne 0 ] || \
     [ "$terminal_count" -ne 1 ]; then
    cleanup_blocking=$((cleanup_blocking + 1))
    summary_line="Worktree teardown contract failure: source=${source} terminal_count=${terminal_count} terminal=${state:-missing} reason=${reason:-missing} exit=${cleanup_rc} log_replay_exit=${log_replay_rc} log=${cleanup_log}"

  elif [ "$state" = "CLEANUP_OK" ] && [ "$cleanup_rc" -eq 0 ]; then
    cleanup_cleaned=$((cleanup_cleaned + 1))
    summary_line="Worktree teardown complete: source=${source} terminal=CLEANUP_OK exit=0 log=${cleanup_log}"

  elif { [ "$state" = "CLEANUP_HELD" ] || \
         [ "$state" = "CLEANUP_FAILED" ]; } && \
       [ "$reason" = "watcher-lineage-live-leased" ] && \
       [ "$cleanup_rc" -ne 0 ]; then
    cleanup_deferred=$((cleanup_deferred + 1))
    summary_line="Worktree teardown deferred: source=${source} reason=watcher-lineage-live-leased exit=${cleanup_rc} rerun=${cleanup_cmd} log=${cleanup_log}"

  elif [ "$state" = "CLEANUP_FAILED" ] && \
       [ -n "$reason" ] && \
       [ "$cleanup_rc" -ne 0 ]; then
    cleanup_blocking=$((cleanup_blocking + 1))
    summary_line="Worktree teardown failed: source=${source} terminal=CLEANUP_FAILED reason=${reason} exit=${cleanup_rc} log=${cleanup_log}"

  elif [ "$state" = "CLEANUP_HELD" ] && \
       [ -n "$reason" ] && \
       [ "$cleanup_rc" -ne 0 ]; then
    cleanup_blocking=$((cleanup_blocking + 1))
    summary_line="Worktree teardown held: source=${source} terminal=CLEANUP_HELD reason=${reason} exit=${cleanup_rc} log=${cleanup_log}"

  else
    cleanup_blocking=$((cleanup_blocking + 1))
    summary_line="Worktree teardown contract failure: source=${source} terminal_count=${terminal_count} terminal=${state:-missing} reason=${reason:-missing} exit=${cleanup_rc} log_replay_exit=${log_replay_rc} log=${cleanup_log}"
  fi

  if printf '%s\n' "$summary_line"; then
    :
  else
    cleanup_reporting_failed=1
  fi

  if [ "$cleanup_setup_rc" -eq 0 ]; then
    if printf '%s\n' "$summary_line" >> "$cleanup_results_file"; then
      :
    else
      cleanup_reporting_failed=1
      printf '%s\n' \
        "Worktree teardown reporting failure: source=${source} results=${cleanup_results_file}" \
        >&2
    fi
  else
    cleanup_reporting_failed=1
  fi
done

if [ "$cleanup_blocking" -gt 0 ] || \
   [ "$cleanup_reporting_failed" -ne 0 ]; then
  cleanup_step_outcome="BLOCKING"
elif [ "$cleanup_deferred" -gt 0 ]; then
  cleanup_step_outcome="DEFERRED_LIVE_LEASE"
else
  cleanup_step_outcome="CLEANUP_OK"
fi
```

The consumer is the paired project-side `hai-sim-engine/scripts/worktree-cleanup.sh`, owned by **HAS-622**, not common. HAS-622 owns auto-minting, no-watcher skip, byte-equality enforcement, and nonzero `CLEANUP_FAILED`. Until it ships, common classifies any terminal/exit disagreement as blocking and must not retain `|| true` as compatibility behavior. A full jInfra `--worktree-teardown` mode remains the explicitly deferred **HAS-599 R1** follow-on; it did not ship in COM-306.

The project cleanup script handles Docker resources, ColGREP state, and Git worktree removal in its established order. Its complete cleanup reason-code list is `safety-failed`, `unknown`, `colgrep-exclude-install-failed`, `colgrep-failed`, `colgrep-leftovers`, `colgrep-teardown-hard-blocker`, `colgrep-teardown-missing`, `docker-failed`, `git-worktree-prune-failed`, `git-worktree-prune-left-record`, `git-worktree-remove-fallback-rmrf-failed`, `overlay-leftovers`, `permission-repair-failed`, `teardown-sentinel-create-failed`, `watcher-lineage-envelope-invalid`, `watcher-lineage-envelope-missing`, `watcher-lineage-retire-failed`, and `watcher-lineage-live-leased`. `watcher-lineage-live-leased` is the **only** lawful non-fatal refusal. Every other `CLEANUP_FAILED`, every `CLEANUP_HELD` other than that live-lease reason, and every provider-contract failure blocks final green.

| Outcome | Detection signal | `/jMerge` action and exact log text | Summary treatment |
| --- | --- | --- | --- |
| Success | Exactly one `[STATE] CLEANUP_OK` terminal line and exit `0` | Continue; `Worktree teardown complete: source=${source} terminal=CLEANUP_OK exit=0 log=${cleanup_log}` | Count as cleaned. |
| Lawful non-fatal live-lease deferral | Exactly one `[STATE] CLEANUP_HELD` or `[STATE] CLEANUP_FAILED` terminal line with `reason=watcher-lineage-live-leased` and nonzero exit | Continue other sources; `Worktree teardown deferred: source=${source} reason=watcher-lineage-live-leased exit=${cleanup_rc} rerun=${cleanup_cmd} log=${cleanup_log}` | Name the residue and render green-with-deferred-residue after all sources finish. |
| Genuine failure | Exactly one `[STATE] CLEANUP_FAILED` terminal line with a nonempty reason other than the lawful live lease and nonzero exit | Block; `Worktree teardown failed: source=${source} terminal=CLEANUP_FAILED reason=${reason} exit=${cleanup_rc} log=${cleanup_log}` | Release the lock, report the already-pushed merge, return nonzero, and do not emit green. |
| Held | Exactly one `[STATE] CLEANUP_HELD` terminal line with a nonempty reason other than the lawful live lease and nonzero exit | Block; `Worktree teardown held: source=${source} terminal=CLEANUP_HELD reason=${reason} exit=${cleanup_rc} log=${cleanup_log}` | Release the lock, report the already-pushed merge, return nonzero, and do not emit green. |
| Provider-contract failure | Durable capture/replay failure, not exactly one terminal line, or any terminal/exit disagreement | Block; `Worktree teardown contract failure: source=${source} terminal_count=${terminal_count} terminal=${state:-missing} reason=${reason:-missing} exit=${cleanup_rc} log_replay_exit=${log_replay_rc} log=${cleanup_log}` | Release the lock, report the already-pushed merge, return nonzero, and do not emit green. |

Aggregate precedence is fixed: **any blocking > any live-lease deferral > all-clean**. A live lease for one source never prevents cleanup of the others. The live lease check is only the target `.colgrep-overlay-watcher.pid` fields `state` and `last_event_at`; the remedy is **close the session for that worktree, then re-run the same cleanup command**. It is not a no-watcher skip and is never forceable.

The no-watcher result is exactly:

```text
watcher-lineage-retire: skipped (no watcher for target)
```

The cutoff is derived at producer invocation time and read byte-for-byte from the validated envelope field `constraints.watcher_lineage_retire.stale_before`; the caller never re-derives or reformats it. When envelope/producer preparation refuses, HAS-622 prints this exact producer recovery invocation to cleanup stderr. `/jMerge` streams, persists, and copies it **verbatim**; it never reconstructs or normalizes it:

```bash
"$COMMON_ROOT/.venv/bin/python" "$COMMON_ROOT/scripts/watcher_lineage_retire_envelope_producer.py" --worktree "$WORKTREE_PATH"
```

`--stale-before` is optional and defaults to the producer invocation time floored to a UTC whole second. When supplied explicitly it must be in exactly that canonical form — `YYYY-MM-DDTHH:MM:SSZ`. Numeric offsets (including `+00:00`), fractional seconds, and other ISO-8601 spellings are refused at the producer boundary with `invalid --stale-before: expected UTC whole-second timestamp YYYY-MM-DDTHH:MM:SSZ` and exit 1, before any plan, envelope, or artifact is created. The recovery invocation above omits `--stale-before` and is unaffected.

### 10.7 — Release lock + write final summary

```bash
rm "$LOCK_PATH"
```

Write final summary at `${MERGE_CONFIG.merge_artifacts.plan_dir}/HAS-000.merge-final.{sources}-{date}.md` — include the per-source teardown log lines, any deferral reasons, and the Step 7.1 gate evidence path plus outcome. Retain `forced-completion-not-green` verbatim; never rewrite it as ordinary green.

---

## Step 11: Failure recovery / merge-fix mode

Per `feedback_no_workarounds_in_uat.md`: defect → fix the defect, NEVER workaround. UAT contract may be renegotiated only with explicit user sign-off.

### Rollback by state

| Failure point | Rollback | Constraint |
|---|---|---|
| Before MERGE_STARTED | release lock; abandon plan | safe |
| MERGE_STARTED → CONFLICTS_RESOLVED_UNCOMMITTED | `git merge --abort` | only if no unrelated staged work |
| CONFLICTS_RESOLVED_UNCOMMITTED → MERGE_COMMITTED_UNVERIFIED | redo conflict resolution | document in plan; resume from MERGE_STARTED |
| MERGE_COMMITTED_UNVERIFIED → CRITIC_GREEN | `git reset --soft HEAD~1` (preserve) OR `--hard HEAD~1` (discard) | hard reset requires explicit user sign-off |
| After PUSHED | revert commit OR hotfix branch | NEVER force-push target unless explicitly requested |

Resume after crash: read latest checkpoint AND live git state. DO NOT replay original plan.

---

## /jPrecompact integration

For multi-source or runway-class merges only, MUST call `/jPrecompact --force` at:
- Before starting conflict resolution (Step 5 entry)
- After each source merge commit (per-source 5.3 exit)
- Before browser UAT (Step 7.4 entry)
- Before final push (Step 10.2 entry)

Addresses the 25-minute crash mode from architect-master's 2026-05-01 dispatch.

---

## /jClose integration

When called from `/jClose` workflow:

```bash
# COM-91 dual-path — caller resolves <PLAN_FILE> before invocation
# New tickets (>= 2026-05-22):
/jMerge --quick --source TICKET-XXX --target main --mode close-ticket --plan .jswarm/plans/TICKET-XXX.plan.*.md
# Legacy tickets:
/jMerge --quick --source TICKET-XXX --target main --mode close-ticket --plan docs/plans/TICKET-XXX-*.md
```

`/jClose` MUST NOT merge directly. Single-source closeout enters this command through the lean path first; full protocol is used only after lean rejection or explicit user request.

**Idempotence:** if `/jClose` is invoked for a ticket whose feature branch was already integrated:
1. Detect existing merge evidence (target's git log for merge commit referencing source ticket's feature branch)
2. Verify SHA matches
3. If valid: skip merge phase, return prior merge status immediately
4. If invalid: re-invoke merge for this source

For an executor-routed merge produced through the Step 0 isolation handoff, the **EXECUTOR** owns the post-merge plan-status flip (`ACTIVE`/`READY_FOR_MERGE` → the `MERGED`-family terminal state represented here as `DONE`) as part of the merge transaction, in the same sitting as the merge and its verification. The isolated lane has stopped after handing off and MUST NOT perform this flip; the executor must not leave it stranded on the lane.

Jira Done transition happens only after this command returns `MERGE_LEAN_GREEN` or `MERGE_FINAL_GREEN`. For the non-isolated `/jClose` flow, the caller flips plan status `READY_FOR_MERGE → DONE` after green return (per `state-machine.md`).

---

## Final summary format

**All-clean form:**

```
✅ /jMerge complete

Sources: HAS-XXX[, HAS-YYY[, ...]]
Target:  main → <new sha>
Merge plan:   ${MERGE_CONFIG.merge_artifacts.plan_dir}/HAS-000.merge-plan.<sources>-<date>.md
UAT contract: ${MERGE_CONFIG.merge_artifacts.plan_dir}/HAS-000.uat-merge.<sources>-<date>.md  (full protocol only)
Final report: ${MERGE_CONFIG.merge_artifacts.plan_dir}/HAS-000.merge-final-<sources>-<date>.md

## jInfra Step 7.1 gate
- evidence: `<repository-relative JINFRA_GATE_EVIDENCE>`
- outcome: `<ordinary-completion|forced-completion-not-green>`
- jinfra_process_exit: `<0|5>`
- receipt_exit_code: `<0|5>`
- applied_waiver_codes: `<comma-separated unique codes, or none>`
- executed_repair_kinds: `<comma-separated kinds, or none>`
- reproof_status: `<pass|none>`

Per-source results:
  HAS-XXX: SMOKE GREEN · FULL UAT GREEN · CRITIC GREEN · pushed @ <sha> · origin/feat/HAS-XXX deleted

Worktrees torn down: <count> · residue: none
Lock released: <path>
Lean rejection telemetry: <log path if rejection happened during this run, else `N/A — lean completed without rejection` or `N/A — full path invoked directly`>

Status: MERGE_FINAL_GREEN  (or MERGE_LEAN_GREEN for lean path)
```

**Green-with-deferred-residue form:** retain the green heading and status, but name each `watcher-lineage-live-leased` source, its `rerun=${cleanup_cmd}`, and the residue count in the final report. This is the only deferral that may finish green.

**Post-push cleanup-blocked form:**

```
/jMerge cleanup blocked after push

Sources: HAS-XXX[, HAS-YYY[, ...]]
Target: main → <new sha> (already pushed)
Final report: ${MERGE_CONFIG.merge_artifacts.plan_dir}/HAS-000.merge-final-<sources>-<date>.md
Cleanup outcome: BLOCKING
Per-source teardown logs: <paths>
Lock released: <path>
Status: nonzero; MERGE_FINAL_GREEN and MERGE_LEAN_GREEN not emitted
```

The cleanup-blocked form reports the already-pushed merge and releases the lock without rendering `/jMerge complete` or any green status.

---

## Anti-patterns

- **Do not** call this command "successful," "verified," "working" before §10.1 CHK-9 passes
- **Do not** delegate the entire merge to one long-running agent (>200 tool uses) — use phase-bounded delegations
- **Do not** dispatch `jArchitect` at xhigh effort without CHK-AM approval when §4.1.1 eligibility applies; default is `jArchitect`
- **Do not** treat dirty target files as merge complexity; classify with `git status --short` and use path-limited stash only after explicit user choice
- **Do not** dispatch `jArchitect` (or `jArchitect` at xhigh effort) before cheap single-source evidence shows complexity, except when the operator explicitly requests the read-only default-effort `jArchitect` Step 0.plan advisory through `--arch-plan`; xhigh remains subject to CHK-AM
- **Do not** create a separate merge worktree unless user asks or merge is multi-source/runway-class
- **Do not** let lean mode silently continue after actual merge conflicts, migrations, workflow/runtime changes, official UAT inventory changes, or rejected dependency lockfile changes
- **Do not** skip the official UAT inventory freshness gate (HAS-325 failure mode)
- **Do not** accept any source's whole-file version of `architecture.uat-scenarios.md` — always three-way synthesize
- **Do not** apply migrations before health-check; multiple-heads detection MUST run first
- **Do not** declare smoke complete based on `npm run build` + host py_compile — those are prerequisites, not satisfaction
- **Do not** force-push target unless user explicitly requests with warning
- **Do not** clean up worktrees before target push and remote source branch deletion succeed
- **Do not** dismiss critic CRITICAL findings without user sign-off recorded in merge plan
- **Do not** ask the user to paste "commit + push my source branch first" prep messages — Step 0b auto-handles
- **Do not** ask the user about plan status routinely — Step 0c warns once on ACTIVE and proceeds with default Y
- **Do not** abort lean on Class A/B/C conflicts — Step Q.4 step 6 classifies first; only Class D/E reach escalation (Fix #1, 2026-05-11)
- **Do not** surface "now run `/jMerge --full --source ...`" as the only path on lean rejection — Step Q.2 eligibility refusal prompts `Switch to full now? [Y/n]` default Y (Fix #1, 2026-05-11)
- **Do not** skip the `lean-rejection-log.md` entry when transitioning lean→full via Option D — that IS a rejection event needing calibration evidence (Fix #4, 2026-05-11)
- **Do not** pre-create empty `lean-rejection-log.md` files in projects — lazy creation on first rejection only

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-08-23 | HAS-537 retro | **Executor handoff and pin-freeze.** Added the Step 0 isolation check and prepared executor handoff, the lane final-SHA declaration/freeze plus executor exact-SHA pinning handshake, and executor ownership of the executor-routed post-merge plan-status flip. |
| 2026-08-01 | COM-306 | **Step 10.6 frozen teardown classifier.** Replaced the exit-discarding `|| true` loop with the empirically verified zsh classifier; documented outcome classification, aggregate precedence, lawful live-lease deferral, no-watcher skip, cutoff byte rule, recovery invocation, paired HAS-622 ownership, and post-push summary forms. |
| 2026-07-20 | claude-fable-5 (HAS-569 merge retro) | **Class S — silent semantic collisions in AUTO-MERGED files.** New advisory in Conflict Classification Policy (mechanical both-touched-auto-merged trigger set, parent-isolation + guard-key-grep recipe, rival-flag supersession ruling); Q.2 lean eligibility requires the set be empty in code subsystems; CHK-2 requires the contract enumerate the set with per-file semantic-verification actions. Evidence: HAS-569 ← main reconcile, 4 instances, 3 zero-marker. Detail: protocol-spec.md §Class S. |
| 2026-05-22 | COM-91 Phase 3b | Dual-path plan resolution in Class B/C conflict policy + Step 76 + Step 556 invocation examples (`.jswarm/plans/TICKET-XXX.plan.*.md` for new tickets, `docs/plans/TICKET-XXX-*.md` for legacy). Retro & reconciliation-log paths extended to recognize `.jswarm/plans/*/*.retro.md` + `.jswarm/plans/*/*.reconciliation-log.md` as Class C in addition to legacy `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/` and `docs/plans/`. Per canonical doc `docs/agent-system/agent-write-permissions.md`. |
| 2026-05-19 | Sisyphus | **Step 10.5-10.8 rewrite — autonomous worktree + Docker teardown.** Collapsed the duplicate "legacy 10.6 vs new 10.8" path into a single Step 10.6 (Worktree + Docker teardown). Made `--force` to `worktree-cleanup.sh` the unconditional post-push default (rationale documented: Step 10.2 push + Step 10.3 deletion already captured all real work; gitignored untracked files are session-local debug detritus). Added explicit fallback recipe for git's "Directory not empty" failure mode on worktree remove (rm -rf + git worktree prune + branch -D). Documented the Docker scope: containers + named volumes + networks + compose project + ColGREP overlay. Triggered by HAS-410 /jMerge close-out where worktree teardown was incorrectly deferred to operator on legacy plugin-cache gitignored artifacts. Memory: feedback_merge_includes_worktree_and_docker_teardown.md PERMANENT. |
| 2026-05-11 | Sisyphus | **Fix #5 — legacy zero-commits handling.** Step 0b now detects "source branch has 0 commits beyond main's base AND Class D dirt in worktree" and routes to Step 0b.L legacy migration commit flow (single Y/N/abort prompt; default Y bundles all classes into one synthesized `feat(TICKET): implementation work (legacy migration commit)` commit + auto-pushes). Eliminates the friction surfaced during HAS-402 closeout where pre-Step-4P tickets hit Class D ESCALATE despite being "done." Also removed /jClose Block 1.3 (commit/push state pre-check) as duplicative with Step 0b. |
| 2026-05-11 | Sisyphus | **Fix #1 + Fix #4 follow-up to lifecycle pass.** Q.4 step 6 now classifies conflicts first and auto-resolves Class A/B/C silently (closes alignment gap with Conflict Classification Policy section); only Class D/E escalate via step 6.1 with new **Option D: switch to full protocol from here** (preserves Class A/B/C resolutions in the index, transitions to full Step 4). Q.2 eligibility refusal now prompts `Switch to full now? [Y/n]` default Y instead of stopping cold. Added Step Q.5 lean rejection telemetry (`lean-rejection-log.md`) for quarterly calibration of `quick_merge.reject_paths`. |
| 2026-05-11 | Sisyphus | **Lifecycle + leanness pass.** Added Step 0b (source-side preflight: auto-commit Class B/C dirt + auto-push source branch) and Step 0c (plan-status awareness consuming `ACTIVE/READY_FOR_MERGE/DONE` state machine). Added Step 10.8 (post-merge worktree teardown trigger). Extracted deep semantics to `docs/merge/protocol-spec.md`, worked examples to `docs/merge/conflict-examples.md`, state machine to `docs/merge/state-machine.md`. Reduced 964 → ~500 lines. Memory: `feedback_merge_lifecycle_state_machine.md` PERMANENT + expansion of `feedback_merge_main_sync_preflight.md` PERMANENT. |
| 2026-05-10 | Sisyphus | Made lean single-source path default for /jMerge --source; moved architect/full-UAT/critic/worktree ceremony behind evidence-based escalation; updated dirty-target handling to prefer stash/pop. |
| 2026-05-09 | GPT-5.5 | Added `/jMerge --quick` fast path owned by `git-master`; fail-closed eligibility; default stash/pop dirty target handling; optional cheap verification config. |
| 2026-05-09 | — | Default analysis/conflict tier is `architect`; `architect-master` only when §4.1.1 eligibility + CHK-AM approval. |
| 2026-05-03 | GPT-5.5 | Added Step 10.3 (delete remote source branches after target push verification + before Jira finalization). |
| 2026-05-01 | Sisyphus + critic + orchestrator | Initial version. Derived from HAS-000.merge-protocol-spec-amended-2026-05-01.md after critic Pass-1 review (RATIFY-WITH-AMENDMENTS, 8 HIGH gaps + amendments A–K). |
