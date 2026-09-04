---
name: implement
description: "[DEPRECATED — use /jGo] Compatibility alias for the renamed implement command; delegates to /jGo."
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/implement/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy.
     COMPATIBILITY ALIAS (COM-358 T2): /implement has been renamed to /jGo. This thin alias
     delegates to /jGo; the full TDD execution protocol + all phase/gate content now live under
     skills/jGo/. This alias is the ONE surviving skills/implement/ artifact — it carries its own
     distinct catalog identity and is EXCLUDED from any old-folder retire set, so /implement keeps
     resolving through the deprecation window. Alias retirement is owned by a separate ticket
     (ceiling no earlier than 2026-11-10). -->

# /implement → renamed to /jGo (compatibility alias)

> ⚠️ **`/implement` has been renamed to `/jGo`.** This alias still works during the deprecation window, but it will be retired (separate ticket, ceiling no earlier than **2026-11-10**). Please use **`/jGo`** going forward.

## Delegation (imperative — this is the entire behavior of the alias)

When invoked as `/implement` (with any arguments), **immediately invoke `/jGo` and run the full jGo workflow.** Pass through every argument verbatim — plan file path, ticket key, phase selection, `status`, `--help`, and any scope detail — exactly as received.

`/jGo` is the canonical plan-execution command: it executes a plan file using strict TDD, continues from wherever the plan left off, commits per phase, and flips plan status `ACTIVE → READY_FOR_MERGE` at last-phase completion. It owns ALL execution logic, including the safety contract, the per-phase commit discipline, and the QA/review gates. **Do not run any execution logic in this file** — it exists only so the old `/implement` invocation keeps resolving to the renamed command during the deprecation window.

Surface the one-line deprecation notice above to the user on invocation, then proceed as `/jGo`.

**Note:** only the *command entrypoint* was renamed. The domain vocabulary is still "implement" — the `"implement"` lifecycle phase value, `ATP_LIFECYCLE_STAGES`, and "implement" as an ordinary verb are unchanged.
