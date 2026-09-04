---
name: update-plan
description: Normalize JSWARM plan frontmatter, propose or apply explicit content hygiene moves, and refresh HUD-visible plan state.
---

# Update Plan

## Safety contract (destructive skill, agent-invocable)
- **Default is read-only / no-write.** Invoked with no args (or `--help`/`status`), this skill only inspects and reports/proposes frontmatter and content hygiene candidates; it performs NO write, apply, push, delete, remote, trim, or content-relocation/archive move.
- **Confirm before any mutation.** Before any state-changing mode, the invoker (human or agent) must obtain explicit confirmation, or run an approved preview/dry-run first and act only on that approved plan.
- **Agents are NOT locked out** (no `disable-model-invocation`); this contract (not frontmatter) is what gates writes, so an agent can use the read-only path freely and must pause for approval before mutating.

**Last Updated:** 2026-06-14

A reliable single entry point for agents to update a JSWARM (JarviSWARM) plan file. The skill
is a thin **facade**: all behavior lives in the deterministic Python module
`jswarm/update_plan/cli.py` (run via `.venv/bin/python`), so the live-global
lifecycle path never depends on model-mediated prose. `/jPrecompact` calls the **same**
entrypoint directly.

Every operation is **fail-open** (exit 0): a missing/malformed plan is reported and
skipped, never aborting the agent or the checkpoint.

---

## When to Use This Skill

Load this skill whenever you need to bring a plan file back to a clean, lean state:

- At a phase boundary or before pausing work, to normalize the plan's frontmatter.
- When a plan has accreted technical-spec detail or historic content and you want to
  relocate/archive it (with discoverability links left behind).
- From `/jPrecompact` Surface 2, where the deterministic entrypoint is invoked before the
  planning-artifact dispatch so the HUD's single source of truth (frontmatter) is fresh.

The plan file is for **planning**, not a catch-all dump. This skill keeps it that way
without ever silently losing content.

---

## Step 1: Resolve the plan

The module resolves the master plan from either an explicit path or a ticket key:

```bash
# Explicit plan path
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_plan/cli.py --plan .jswarm/plans/KEY-XXX.plan.<slug>.md

# By ticket key (globs .jswarm/plans/KEY-XXX.plan.*.md under --repo-root)
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_plan/cli.py --ticket KEY-XXX --repo-root .
```

If no plan resolves (no ticket, missing file, ambiguous/invalid key), the module prints
`update-plan KEY: no plan ...` and exits 0 (**fail-open, no write**).

---

## Step 2: Lightweight validation / normalize

The module reuses `jswarm/plan_status/reconcile.normalize_plan_file` (no
reimplementation). It hoists frontmatter to line 1, derives `status` / `phase` /
`ac_complete` from `plan_status` + the `## Acceptance Criteria` section, **plus the
AC-9 counts `nfr_complete` / `uat_complete`** (🟢 rows / total rows of the
`## A/C-to-NFR Traceability Matrix` and `## UAT-Scenario Traceability Matrix`; omitted
when the matrix is absent ⇒ the HUD shows no segment), and canonicalizes field order.
The operation is **idempotent**: re-running on an already-clean plan produces no diff.

It then prints a deterministic summary line:

```
update-plan KEY-XXX: status=ACTIVE phase=2.planning ac=1/8 nfr=2/3 uat=1/4 normalized=Y
```

**Determinism boundary (AC-9/AC-10):** this module only *counts* the matrices
already in the plan; it never reads the ticket-local test/result files, which keeps the
count path deterministic and project-agnostic. Keeping those matrix Status cells truthful
from the local result docs is a **separate** pass owned by `/jPrecompact` Step 2.0
(`jswarm/precompact_reconcile/cli.py`, AC-10), which runs **before** this count. Chain:
local result docs → (AC-10 reconcile) → plan matrices → (this module counts) →
`nfr_complete`/`uat_complete` frontmatter → HUD.

---

## Step 3: Content hygiene: propose by default, `--apply` to move

A **bare** invocation (no `--apply`) moves **zero** content. It normalizes frontmatter
and emits a *proposal* listing candidate moves; plan/spec/archive bytes are otherwise
byte-for-byte unchanged.

```bash
# Dry-run: normalize + list candidate tech-spec / archive moves (default; non-destructive)
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_plan/cli.py --plan <plan> --propose

# Apply: actually relocate/archive the explicitly-marked sections
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_plan/cli.py --plan <plan> --apply
```

Only with explicit `--apply` is content moved, and only under explicit headings/markers:

- **Tech-spec relocation** → `.jswarm/plans/KEY-XXX/KEY-XXX.specs.*.md` for sections headed
  `## Technical Specification` / `## Tech Spec` / `## Architecture` / `## API Design`, or
  any section carrying `<!-- update-plan:move-to-specs -->`. Leaves an inline pointer to the
  moved specs heading anchor.
- **Archive** → `.jswarm/plans/KEY-XXX/_archive/KEY-XXX.plan-archive.summary-of-archive.YYYYMMDD.md`
  for sections explicitly labeled `Historical` / `Superseded` / `Deprecated` / `Completed Phase` /
  `… Log`, or carrying `<!-- update-plan:archive -->`. Leaves an anchored back-link under the
  plan's stable `## Archived Detail` section.

**Never** moved, even under `--apply`: Acceptance Criteria, active blockers, unresolved
decisions, current verification requirements, Scope, Changelog, Required Reading, or any
semantically ambiguous content. Content is **never** moved by age or length alone.

Same-day `--apply` runs **append** to the existing archive file (no clobber) and add no
duplicate back-links; a re-run with nothing new produces no diff.

---

## Step 4: HUD refresh: pull-render, no fake push

The HUD (ccstatusline widgets) is **pull-rendered**: it re-reads the plan each render
cycle. "Refresh" therefore means *the module writes normalized frontmatter (the HUD's
single source of truth) and prints the summary line*; the **next** statusline render
reflects the new `status` / `phase` / `ac_complete`. The module does **not** fake a redraw
by touching unrelated files or forcing side effects. The only write is to the plan file
itself (plus the specs/archive files when `--apply` moves content).

---

## Step 5: Backfill sweep (maintenance)

To normalize every **active** plan at once (used as the gate before the HUD's file/body
fallbacks were removed; see `.claude/hud/README.jswarm-hud.md`):

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_plan/cli.py backfill --repo-root . \
  --evidence .jswarm/plans/TICKET-XXX/TICKET-XXX.backfill-evidence.md
```

"Active" = `.jswarm/plans/*.plan.*.md` whose derived `status` ∈ {ACTIVE, READY_FOR_MERGE}
(DONE / WONT_DO / DEFERRED and `_archive/` contents are excluded). The sweep records
before/after counts to the evidence artifact and is fail-open.

---

## Maintenance

| Date | Author | Change |
|------|--------|--------|
| 2026-06-14 | (Phase 2, /jGo) | Initial skill facade over `jswarm/update_plan/cli.py`: resolve → normalize → propose/apply content hygiene → pull-render HUD refresh → backfill. Fail-open; `.venv/bin/python` only. |
| 2026-06-14 | (Phase 8, /jGo) | AC-9: module now also derives `nfr_complete`/`uat_complete` (matrix 🟢/total) into frontmatter + summary line. AC-10: documented the determinism boundary + the separate `/jPrecompact` Step 2.0 reconciler (`jswarm/precompact_reconcile/`) that feeds the matrices before this count. |
