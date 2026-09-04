# The merge state machine

Canonical reference for the two states every JarviSWARM plan carries, and how
one derives the other. The implementation is
[`jswarm/plan_status/state.py`](../../jswarm/plan_status/state.py) — this
document is prose over that module, not a separate source of truth; if the
two ever disagree, the code is right and this file is stale.

## Two vocabularies, one plan

A plan carries a `plan_status` value (the detailed lifecycle state a Story,
Task, or Bug plan is actually in) and a `status` value (the coarse merge-gate
field that `/jMerge`, dashboards, and the reconcile tooling read). `status`
is **derived** from `plan_status` via `derive_merge_status()` — it is never
hand-authored, and reconcile tooling overwrites a stale `status:` on every
pass.

### `plan_status` (detailed lifecycle)

| Value | Meaning |
| --- | --- |
| `null` | No registry/frontmatter entry yet (sentinel, not a real state). |
| `0.planning.lite_init` | `/jPlan --lite` seed: ticket + context only. |
| `1.planning.lite_refine` | Lite plan being refined toward Standard/Deep, or staying Lite. |
| `2.planning.detailed` | Full Standard/Deep plan, spec and phases written. |
| `3.implementation.phase_<N>[.<slug>]` | Actively implementing phase `<N>` of the plan. |
| `4.closed.all_ac_met` | All acceptance criteria met, but not yet merge-proven. |
| `5.closed.ready_for_merge` | Merge-proven (see `/jMerge` gates); ready to land. |
| `6.closed.merged` | Landed on the target branch. |
| `terminal.wont_do` | Closed without implementation, by owner decision. |
| `terminal.deferred` | Shelved; may resume later via a manual transition. |

### `status` (merge-gate field)

| Value | Derived from `plan_status` category |
| --- | --- |
| `ACTIVE` | `null`, `0.*`, `1.*`, `2.*`, `3.*` (any phase), `4.closed.all_ac_met` |
| `READY_FOR_MERGE` | `5.closed.ready_for_merge` |
| `DONE` | `6.closed.merged` |
| `WONT_DO` | `terminal.wont_do` |
| `DEFERRED` | `terminal.deferred` |

The load-bearing gate is `4 -> 5`: a plan with every acceptance criterion
checked off (`4.closed.all_ac_met`) still reports `status: ACTIVE`. Only the
explicit `5.closed.ready_for_merge` transition — which the merge-proof step
performs, not the author — flips `status` to `READY_FOR_MERGE`. Nothing
downstream (`/jMerge`, dashboards) may treat "all A/C met" as "ready to
merge"; those are different claims.

## Transitions

Every transition is classified as one of:

- **allowed** — the normal path; no confirmation needed.
- **idempotent** — re-asserting the same state (a no-op), or a phase advance
  / same-phase description update within `3.implementation.*`.
- **manual** — legal only with an explicit developer override (a reopen or
  correction, with a reason). `validate_transition(..., manual=True)` is
  required or it raises.
- **illegal** — never permitted; always raises `InvalidTransitionError`.

The full matrix lives in `_build_matrix()` in `state.py`; the shape that
matters for a reader:

- Forward planning progresses `0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6` as **allowed**
  steps (each stage to the next).
- Skipping a stage forward (e.g. `0 -> 3`) is **illegal** — you cannot
  jump straight to implementation without a detailed plan.
- Moving backward (e.g. `3 -> 2`, `5 -> 3`) is **manual** — legitimate for a
  reopen, but never silent.
- Any non-terminal state can move to `terminal.wont_do` or
  `terminal.deferred` as **allowed** (closing out is always available).
- Leaving a terminal state (`wont_do`/`deferred` back to planning or impl) is
  **manual** — a deliberate reopen, not an automatic bounce.
- `6.closed.merged` is otherwise terminal; only `manual` corrections back
  into it or out of it (e.g. a post-merge phase note) are permitted, and only
  with an override.

## Derived frontmatter fields

Two more frontmatter fields are pure derivations from `plan_status` (and, for
one of them, the plan body) — never hand-authored, always recomputed by
`reconcile.normalize_plan_file`:

- **`phase:`** — `derive_phase()`. For `3.implementation.phase_<N>[.<slug>]`,
  this is `"<N>.<heading-or-slug>"` (the plan body's own
  `## Phase <N>: <description>` heading wins over the slug when both exist).
  For every other valid, non-null state it is the fixed `STAGE_LABEL` for
  that state (e.g. `2.planning`, `5.ready_for_merge`).
- **`ac_complete:`** — `derive_ac_complete()`. A `"<done>/<total>"` count of
  `- [x]` / `- [ ]` / `- [~]` checkboxes under the plan's own
  `## Acceptance Criteria` section, scoped to that section only. This is a
  progress heuristic for humans skimming the plan list, not the acceptance
  source of truth — the actual gate is the A/C review, not the checkbox
  count.

## Where this is enforced

- `jswarm/plan_status/state.py` — the matrix, `classify_transition`,
  `validate_transition`, `derive_merge_status`, `derive_phase`,
  `derive_ac_complete`.
- `jswarm/plan_status/reconcile.py` (or wherever `normalize_plan_file` lives)
  applies the derived fields on every plan-file write.
- `/jMerge` reads `status: READY_FOR_MERGE` as its entry gate.
- `/jPlan`'s `template.CORE.md` seeds every new plan at `status: ACTIVE`
  (frontmatter), consistent with every non-terminal, pre-`5` `plan_status`.
