---
name: new-work
description: "[DEPRECATED — use /jPlan] Compatibility alias for the renamed planning command; delegates to /jPlan."
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/new-work/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy.
     COMPATIBILITY ALIAS (COM-254 T2.4): /new-work has been renamed to /jPlan. This thin alias
     delegates to /jPlan; the full router + all step/pattern content now live under skills/jPlan/.
     This alias is the ONE surviving skills/new-work/ artifact — it carries its own distinct catalog
     identity and is EXCLUDED from the COM-254 T2.5 old-folder retire set, so /new-work keeps
     resolving through the deprecation window. Alias retirement is owned by COM-251 (ceiling 2026-08-10). -->

# /new-work → renamed to /jPlan (compatibility alias)

> ⚠️ **`/new-work` has been renamed to `/jPlan`.** This alias still works during the deprecation window, but it will be retired (COM-251, ceiling **2026-08-10**). Please use **`/jPlan`** going forward.

## Delegation (imperative — this is the entire behavior of the alias)

When invoked as `/new-work` (with any arguments), **immediately invoke `/jPlan` and run the full jPlan workflow.** Pass through every argument verbatim — `--lite`, briefing intent ("lite", "briefing only", "ticket + context only"), `--localization-smoke`, the ticket key, and any scope detail — exactly as received.

`/jPlan` is the canonical planning router: it initializes a Jira ticket, a technical design spec, and a plan file (full or `--lite`/briefing mode) and owns the ceremony selector (`/jPlan.ceremony-selector`), the composition assembler, and localization. **Do not run any planning logic in this file** — it exists only so the old `/new-work` invocation keeps resolving to the new router during the deprecation window.

Surface the one-line deprecation notice above to the user on invocation, then proceed as `/jPlan`.
