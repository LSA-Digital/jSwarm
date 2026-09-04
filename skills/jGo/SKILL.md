---
name: jGo
description: Symlink to the global /jGo command that executes a plan through progressive-disclosure lifecycle seams and flips plan status ACTIVE->READY_FOR_MERGE at completion.
---

# /jGo — Execute a Plan

## Safety and invocation

- **Default is read-only / no-write.** No args, `--help`, or `status` only inspect and report.
- **Confirm before mutation.** State-changing work requires explicit approval, or an approved preview/dry-run.
- Agents may inspect freely; this contract gates writes.

```text
/jGo                          # auto-detect a plan
/jGo TICKET-XXX                # ticket plan
/jGo path/to/plan.md           # explicit plan
/jGo status                    # progress only
/jGo --help                    # usage only
/jGo TICKET-XXX --phased       # stop after each accepted phase (default)
/jGo TICKET-XXX --continuous   # continue through accepted phases
```

`/jGo` is the canonical executor. `/jGo` remains a separate, thin compatibility alias that passes arguments verbatim to `/jGo`.

## Localization and plan resolution

Before non-smoke work, resolve project localization through
`managed_commands.implement.md`: apply configured anchors whose markers occur
here; a required missing, empty, or unresolved anchor is a `LOCALIZATION ERROR`.
`--localization-smoke` performs only that inspection and reports each anchor.

Resolve a plan once: prefer `.jswarm/plans/TICKET-XXX.plan.*.md`, then fall
back to `docs/plans/TICKET-XXX-*.md`. Keep one ticket in one location. An
explicit path wins; otherwise use existing ticket auto-detection. A missing or
non-executable plan blocks and routes to `/jPlan`; a `READY_FOR_MERGE` or `DONE`
plan requires confirmation before re-running.

<!-- inject:project-advisories -->

<!-- inject:project-lifecycle-checklist -->

<!-- inject:project-worktree-policy -->

<!-- inject:project-colgrep-worktree-policy -->

<!-- inject:project-required-reading -->

## Lifecycle router

Keep the public ordering: admission before `started`; accepted phase before
`phase-advanced`; final applicable gates, then `implement-gate`, ready status,
and the `ready-for-merge` event. Preserve lifecycle stage `implement` and the
existing `implement_progress_contract` dashboard contract.

Load **only one companion at a time**, at its seam:

1. Load [session.md](session.md) for admission, progress, and mode context.
2. Load [task-cycle.md](task-cycle.md) for the next incomplete task.
3. Load [phase-exit.md](phase-exit.md) **only** when the active phase's tasks are complete.
4. Load [completion.md](completion.md) **only** when every phase is complete.

The following localized content applies only at the `phase-exit.md` seam:

<!-- inject:component-attach-at-creation -->

After `session.md` returns executable context, repeat `task-cycle.md` for the
current phase. When its tasks are accepted, route through `phase-exit.md`.
In `--phased` mode, stop only after that complete transaction; in `--continuous`
mode, begin the next session/task seam. Route to `completion.md` only after the
last phase is accepted.

## Status and completion summary

Report the resolved plan, current phase, next task or blocker, selected mode,
and applicable gates concisely. `/jGo` builds and stops: it does not run tests
beyond what the plan's own phases already required, and it does not issue a
UAT round. At completion, report A/C and validation state and the ready
transition; do not duplicate the owners of testing, review, checkpoints,
infrastructure, dashboard mechanics, or ticket status.

**Next:** run `/jTest` in this project's agent session to run automated tests
and the agent smoke walk.
