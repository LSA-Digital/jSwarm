---
name: precompact
description: "[DEPRECATED: use /jPrecompact] Compatibility alias for the renamed precompact command; delegates to /jPrecompact."
---

# /precompact → renamed to /jPrecompact (compatibility alias)

> ⚠️ **`/precompact` has been renamed to `/jPrecompact`.** This alias still works during the deprecation window, but it will be retired (separate ticket, ceiling no earlier than **2026-11-10**). Please use **`/jPrecompact`** going forward.

## Delegation (imperative: this is the entire behavior of the alias)

When invoked as `/precompact` (with any arguments), **immediately invoke `/jPrecompact` and run the full jPrecompact workflow.** Pass through every argument verbatim (ticket key, `--lite`, `status`, `--help`, and any scope detail) exactly as received.

`/jPrecompact` is the canonical checkpoint command: it runs the four-surface checkpoint protocol before context compaction, gates on promotion review in full mode, and persists durable retro lessons into the canonical retros. It owns ALL checkpoint logic, including the safety contract and the lite/full mode split. **Do not run any checkpoint logic in this file**: it exists only so the old `/precompact` invocation keeps resolving to the renamed command during the deprecation window.

Surface the one-line deprecation notice above to the user on invocation, then proceed as `/jPrecompact`.

**Note:** only the *command entrypoint* was renamed. The `PreCompact` hook event, the `precompact-auto.py` / `precompact-runner.py` handlers, `jswarm/precompact_reconcile`, the `.jswarm/state/precompact/` state root, and the separate `/precompact-update` skill are all unchanged.
