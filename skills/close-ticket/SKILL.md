---
name: close-ticket
description: "[DEPRECATED — use /jClose] Compatibility alias for the renamed close-ticket command; delegates to /jClose."
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/close-ticket/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy.
     COMPATIBILITY ALIAS (COM-358 T2): /close-ticket has been renamed to /jClose. This thin alias
     delegates to /jClose; the full closure protocol + all step/gate content now live under
     skills/jClose/. This alias is the ONE surviving skills/close-ticket/ artifact — it carries its
     own distinct catalog identity and is EXCLUDED from any old-folder retire set, so /close-ticket
     keeps resolving through the deprecation window. Alias retirement is owned by a separate ticket
     (ceiling no earlier than 2026-11-10). -->

# /close-ticket → renamed to /jClose (compatibility alias)

> ⚠️ **`/close-ticket` has been renamed to `/jClose`.** This alias still works during the deprecation window, but it will be retired (separate ticket, ceiling no earlier than **2026-11-10**). Please use **`/jClose`** going forward.

## Delegation (imperative — this is the entire behavior of the alias)

When invoked as `/close-ticket` (with any arguments), **immediately invoke `/jClose` and run the full jClose workflow.** Pass through every argument verbatim — ticket key, plan file, `status`, `--help`, and any scope detail — exactly as received.

`/jClose` is the canonical ticket-closure command: it completes the work, runs the retrospective, closes the Jira issue, and invokes the merge command when applicable. It owns ALL closure logic, including the safety contract, the registration/reconciliation gates, and the retro protocol. **Do not run any closure logic in this file** — it exists only so the old `/close-ticket` invocation keeps resolving to the renamed command during the deprecation window.

Surface the one-line deprecation notice above to the user on invocation, then proceed as `/jClose`.

**Note:** only the *command entrypoint* was renamed. The domain vocabulary is unchanged — the `ceremony: "close-ticket"` schema value and "close ticket" as ordinary English stay as they are.
