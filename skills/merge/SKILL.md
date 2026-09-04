---
name: merge
description: "[DEPRECATED — use /jMerge] Compatibility alias for the renamed merge command; delegates to /jMerge."
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/merge/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy.
     COMPATIBILITY ALIAS (COM-358 T1.3): /merge has been renamed to /jMerge. This thin alias
     delegates to /jMerge; the full merge protocol + all gate/state-machine content now live under
     skills/jMerge/. This alias is the ONE surviving skills/merge/ artifact — it carries its own
     distinct catalog identity and is EXCLUDED from any old-folder retire set, so /merge keeps
     resolving through the deprecation window. Alias retirement is owned by a separate ticket
     (ceiling no earlier than 2026-11-10). -->

# /merge → renamed to /jMerge (compatibility alias)

> ⚠️ **`/merge` has been renamed to `/jMerge`.** This alias still works during the deprecation window, but it will be retired (separate ticket, ceiling no earlier than **2026-11-10**). Please use **`/jMerge`** going forward.

## Delegation (imperative — this is the entire behavior of the alias)

When invoked as `/merge` (with any arguments), **immediately invoke `/jMerge` and run the full jMerge workflow.** Pass through every argument verbatim — source branch(es), target branch, `--arch-plan`, `status`, `--help`, and any scope detail — exactly as received.

`/jMerge` is the canonical merge command: it integrates one or more source feature branches into a target branch, produces a runtime-verified working system before declaring the merge complete, and cleans up. It owns ALL merge logic, including the safety contract, the reserved-vocabulary lock, and the runtime-proof gate. **Do not run any merge logic in this file** — it exists only so the old `/merge` invocation keeps resolving to the renamed command during the deprecation window.

Surface the one-line deprecation notice above to the user on invocation, then proceed as `/jMerge`.

**Note:** only the *command entrypoint* was renamed. The domain operation is still "merge" — `docs/merge/`, `merge.py`, `ceremony: "merge"`, and "merge" as an ordinary verb are unchanged.
