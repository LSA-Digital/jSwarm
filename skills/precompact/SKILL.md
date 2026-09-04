---
name: precompact
description: "[DEPRECATED — use /jPrecompact] Compatibility alias for the renamed precompact command; delegates to /jPrecompact."
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/precompact/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy.
     COMPATIBILITY ALIAS (COM-358 T2): /precompact has been renamed to /jPrecompact. This thin alias
     delegates to /jPrecompact; the full checkpoint protocol + all surface/gate content now live under
     skills/jPrecompact/. This alias is the ONE surviving skills/precompact/ artifact — it carries its
     own distinct catalog identity and is EXCLUDED from any old-folder retire set, so /precompact keeps
     resolving through the deprecation window. Alias retirement is owned by a separate ticket
     (ceiling no earlier than 2026-11-10).
     NOT affected by this rename: the `PreCompact` hook event, precompact-auto.py, precompact-runner.py,
     jswarm/precompact_reconcile, .jswarm/state/precompact/, and the sibling /precompact-update skill. -->

# /precompact → renamed to /jPrecompact (compatibility alias)

> ⚠️ **`/precompact` has been renamed to `/jPrecompact`.** This alias still works during the deprecation window, but it will be retired (separate ticket, ceiling no earlier than **2026-11-10**). Please use **`/jPrecompact`** going forward.

## Delegation (imperative — this is the entire behavior of the alias)

When invoked as `/precompact` (with any arguments), **immediately invoke `/jPrecompact` and run the full jPrecompact workflow.** Pass through every argument verbatim — ticket key, `--lite`, `status`, `--help`, and any scope detail — exactly as received.

`/jPrecompact` is the canonical checkpoint command: it runs the four-surface checkpoint protocol before context compaction, gates on promotion review in full mode, and persists durable retro lessons into the canonical retros. It owns ALL checkpoint logic, including the safety contract and the lite/full mode split. **Do not run any checkpoint logic in this file** — it exists only so the old `/precompact` invocation keeps resolving to the renamed command during the deprecation window.

Surface the one-line deprecation notice above to the user on invocation, then proceed as `/jPrecompact`.

**Note:** only the *command entrypoint* was renamed. The `PreCompact` hook event, the `precompact-auto.py` / `precompact-runner.py` handlers, `jswarm/precompact_reconcile`, the `.jswarm/state/precompact/` state root, and the separate `/precompact-update` skill are all unchanged.
