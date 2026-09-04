---
name: "code-overview"
description: "Symlink to the global /code-overview slash command: builds a bounded, data-driven code overview for one UAT scenario grouping instead of brute-force reading the whole repo."
---

# /code-overview — Scenario-Guided Code Overview

Build a bounded, data-driven overview of code for one UAT scenario grouping. Use this command to understand a feature slice without brute-force reading the whole repository.

The falsifiable speedup benchmark for this workflow lives at `jswarm/uat-scenarios/benchmark_code_overview.py` (legacy v1 runner, unchanged) and, for the v2 seam-spine workflow below, at `jswarm/code-overview/benchmark/runner.py` (reconciled v2 runner; same pass/fail contract, generic over any spine).

## Global Command Project Localization

This is a global/shared command. Project-local rendered command files are not the source of truth.

Before executing any non-smoke instruction in this command:

1. Determine the active project root from the current working directory.
2. If `.claude/project-command-injections.yaml` exists, read `managed_commands.code-overview.md`.
3. For each configured anchor whose marker appears in this command, read its `snippet_path` or inline `content` and treat that content as if it replaced the matching `<!-- inject:... -->` marker.
4. If a configured anchor is `required: true` but the marker is missing, the snippet is missing, or the snippet is empty, stop with `LOCALIZATION ERROR` and explain the missing anchor.
5. If no project manifest exists, continue with the global command body as-is.

If invoked with `--localization-smoke`, do only the localization pass, print the project root, each configured anchor name, whether it resolved, and the first non-empty line of each resolved snippet; then stop without running the normal command workflow. The v2 command shell's `--localization-smoke` additionally reports every `code-overview-v2-*` anchor below and benchmark-runner availability; see "v2 Command Shell" below.

## Localization Anchors

Projects localize this generic command by providing the following injection anchors:

<!-- inject:code-overview-required-reading -->

Required-reading contract: a project may inject architecture files, interface contracts, generated-doc policy, or subsystem maps that must be read before code traversal. Keep this set small and project-specific.

<!-- inject:code-overview-scenario-source -->

Scenario-source contract: a project must inject the canonical UAT scenarios JSON path used by `jswarm/uat-scenarios/query_uat_scenarios.py --list-groupings`. The generated Markdown scenario document is derived/read-only and must not be used as the menu source.

<!-- inject:code-overview-smoke-config -->

Smoke-config contract: a project may inject smoke grouping conventions, required smoke IDs, or a compact command smoke that proves this command still resolves the canonical scenarios JSON.

### v2 Localization Anchors

The v2 command shell (`jswarm/code-overview/cli.py`) adds ten opt-in anchors. Every one of these is `required: false`: a project adopting v2 configures only the ones it needs, and an unconfigured v2 anchor never blocks the legacy v1 workflow above.

<!-- inject:code-overview-v2-spine-manifest -->

Spine-manifest contract: path to the project's seam-spine JSONL manifest. Until configured, `query` and `benchmark` fall back to the packaged pilot fixture.

<!-- inject:code-overview-v2-carrier-vocabulary -->

Carrier-vocabulary contract: the project's carrier names, for `query --carrier` narrowing.

<!-- inject:code-overview-v2-source-roots -->

Source-roots contract: source-code root globs the project's spine anchors resolve against.

<!-- inject:code-overview-v2-trace-sources -->

Trace-sources contract: runtime trace-source adapter configuration for `trace ingest`.

<!-- inject:code-overview-v2-debt-sources -->

Debt-sources contract: tier-1 optional debt-signal adapter locations for the `debt` subcommand.

<!-- inject:code-overview-v2-freeze-ledger -->

Freeze-ledger contract: path to the project's audit freeze ledger.

<!-- inject:code-overview-v2-close-ticket-policy -->

Close-ticket-policy contract: whether `/jClose` audit integration is advisory or blocking for this project.

<!-- inject:code-overview-v2-generated-views-policy -->

Generated-views-policy contract: generated-view formats and output locations the `render` subcommand should target.

<!-- inject:code-overview-v2-benchmark-config -->

Benchmark-config contract: resolved location of the project's benchmark script, if different from the packaged reconciled runner.

<!-- inject:code-overview-v2-live-test-case -->

Live-test-case contract: the project or worktree serving as this project's live pilot for v2 rollout.

## Invocation

```bash
/code-overview
/code-overview <grouping_id>
/code-overview --localization-smoke
```

## No-Arg Menu

When no grouping is supplied:

1. Resolve the project root.
2. Resolve the project's canonical scenarios JSON path from `<!-- inject:code-overview-scenario-source -->` or the project localization manifest.
3. Run the project's bundled query tool against the canonical JSON and schema:

   ```bash
   .venv/bin/python jswarm/uat-scenarios/query_uat_scenarios.py \
     --scenarios <canonical-scenarios-json> \
     --schema jswarm/uat-scenarios/schema/uat-scenarios.schema.json \
     --list-groupings
   ```

4. Present the returned rows exactly as data: `groupings[]` first, then `smoke.smoke_groupings[]`, preserving each row's `id`, `title`, `scenario_ids`, and `source`.
5. Do not hardcode grouping IDs, titles, scenario IDs, or smoke rows in this command.

## Selected Grouping Workflow

When a grouping ID is supplied, produce a top-down and bottom-up overview with an explicit tool trace.

Tool budget cap: target 12 tool calls for a normal overview; hard cap 20 unless the user explicitly asks to continue. Count semantic searches, code-pathfinder calls, and file reads in the trace. Prefer the JSON grouping/touchpoint pathway before broad codebase reads.

### 1. Top-Down Setup

1. Read the injected project required-reading set.
2. Load the canonical scenarios JSON and schema-validate it through `jswarm/uat-scenarios/query_uat_scenarios.py`.
3. Resolve `<grouping_id>` from `groupings[]` or `smoke.smoke_groupings[]`.
4. Resolve the grouping's `scenario_ids[]` and fetch each scenario from JSON.
5. Extract JSON contracts relevant to the selected scenarios: user-visible contract, stage rails, global rules, traceability, and scenario system detail.

Trace entries must include at least:

```json
{"kind":"required_reading","path":"..."}
{"kind":"scenario_source","path":"..."}
{"kind":"grouping","grouping_id":"...","scenario_ids":["..."]}
{"kind":"scenario","scenario_id":"...","title":"..."}
```

### 2. Bottom-Up Code Pathway

For each resolved scenario:

1. Read only `evidence_links[]` entries where `type == "code"` as initial touchpoints.
2. Emit one touchpoint trace entry per code ref:

   ```json
   {"kind":"touchpoint","ref":"path/from/json","scenario_id":"SCN-..."}
   ```

3. Use ColGREP semantic search to expand from the selected scenario goal and code refs, not from the whole repository. Keep the query narrow and record the query/result count in the trace.
4. Use code-pathfinder for backend call paths when the touchpoints identify backend symbols or service entrypoints. Record callers/callees inspected.
5. Use ColGREP plus narrow Read calls for frontend or non-symbol files. Do not read a large file in full when a line range or symbol is enough.
6. Stop at the budget cap with a useful partial map rather than continuing into broad discovery.

### 3. Output Shape

Return:

- selected grouping and scenarios
- required-reading files consumed
- JSON contracts that shape behavior
- code touchpoints from scenario evidence
- backend call-path findings, if applicable
- frontend or integration findings, if applicable
- bounded tool trace with counts and budget use
- explicit caveats for any skipped expansion due to budget cap

## Benchmark Discipline

The benchmark at `jswarm/uat-scenarios/benchmark_code_overview.py` proves the contract is not rhetorical:

- brute force reads every file under a fixture codebase
- guided mode reads only grouping -> scenario -> `type == "code"` evidence refs
- pass requires at least 3x speedup on source lines and non-zero pathway nodes
- degenerate all-files fixtures must fail with `pass=false`

Operational rule: this command should behave like the guided strategy first, then use semantic search and code-pathfinder only to enrich the already-bounded pathway.

## v2 Command Shell (Seam-Spine Query and Benchmark)

`jswarm/code-overview/cli.py` is a generic, subcommand-first Python entry point layered over the v1 workflow above. It never hard-codes any project vocabulary: every project-specific value (scenario JSON path, spine-manifest path, carrier names, ...) is resolved at run time from `.claude/project-command-injections.yaml`.

```bash
.venv/bin/python jswarm/code-overview/cli.py
.venv/bin/python jswarm/code-overview/cli.py menu
.venv/bin/python jswarm/code-overview/cli.py <grouping_id>
.venv/bin/python jswarm/code-overview/cli.py --localization-smoke
.venv/bin/python jswarm/code-overview/cli.py query <text> [--lod 0..4] [--budget-tokens N] [--scenario <id>] [--carrier <carrier>]
.venv/bin/python jswarm/code-overview/cli.py benchmark [--fixture <path>] [--v1-compat]
.venv/bin/python jswarm/code-overview/cli.py build|audit|trace|debt|impact|render ...
```

The no-arg, `menu`, and `<grouping_id>` paths shell out to the same `jswarm/uat-scenarios/query_uat_scenarios.py` engine used by the v1 workflow, unmodified, so their stdout stays byte-stable with the legacy behavior. `--localization-smoke` reports the v1 anchor proof plus every `code-overview-v2-*` anchor (each `required: false`) and benchmark-runner availability.

`build`, `audit`, `trace`, `debt`, `impact`, and `render` are all shipped and functional today (see the subsections below and the F-55 manpage for the full flag surface, including `audit --anchors|--ci|--changed-scope|--close-ticket`, `trace ingest|diff`, `debt classify`, `impact <question>`, and `render --format mermaid|structurizr|summary`). Invoking a subcommand or flag combination that is genuinely not yet wired (an unrecognized subcommand name, or a recognized one with no matching flags) still prints a clear, non-zero-exit "not implemented in this phase" message on stderr instead of silently doing nothing.

### Where to look first: the pipeline-execution-map artifact home

Before reading source files to understand a pipeline or debug a runtime issue, check whether the project already has a built pipeline-execution-map artifact home (the directory holding `spine.jsonl`, conventionally `docs/architecture/pipeline-execution-map/` -- resolve the exact path from the project's `code-overview-v2-spine-manifest` anchor). Its sibling files are the fastest way for an agent to get oriented:

| File | What it gives an agent |
| --- | --- |
| `spine.jsonl` | The declared seam-spine itself: seams, channels, edges, UI surfaces, reason codes, and paths -- the structural map of the pipeline before reading any implementation code. |
| `generated/summary-index.json` | The precomputed query-ranking index (`build` output); lets `query` at any LOD rank/filter without re-scanning the spine. |
| `generated/full-index.json` | Precomputed source excerpts for reachable anchors; `query --lod 4` reads real source text from here instead of live-resolving it, so an agent gets grounded code excerpts fast. |
| `generated/build-report.json` | Byte counts, hashes, and spine hash for the generated triad; use it to confirm the generated index is fresh before trusting `--lod 4` output. |
| `trace-overlays/<run_id>.jsonl` | One resolved-or-unresolved runtime observation record per line for a specific `trace ingest` run -- the actual seams/edges a real execution touched, with `resolution_status` (`resolved_exact`/`resolved_rule`/`ambiguous`/`unmapped`/`conflicts`) explaining how each raw event was (or wasn't) mapped to the declared spine. This is the first stop for debugging "did the pipeline actually take the path I expect?". |
| `trace-summary.json` | The run-level rollup of the latest `trace ingest` run (counts by resolution status, source statuses, exit code) without the bulky per-event detail -- a fast triage read before opening the full overlay JSONL. |

`build` and `trace ingest` write these files; an agent should look for them before triggering a fresh `build`/`trace ingest` run, and re-run `build`/`trace ingest` when `build-report.json`'s spine hash is stale or a `trace-overlays/<run_id>.jsonl` for the run of interest doesn't exist yet.

### `build`

Precomputes the `generated/summary-index.json`, `generated/full-index.json`, and `generated/build-report.json` triad from a spine so later `query` calls (especially `--lod 4`) never re-derive ranking or source excerpts live:

```bash
.venv/bin/python jswarm/code-overview/cli.py build \
  --spine <spine.jsonl> --out <artifact-home-dir> --source-root <source-root> --json
```

`--out` is the artifact-home directory itself (the one containing `spine.jsonl`); `build` writes the `generated/` triad as a subdirectory of it, and this same directory is the parent `trace ingest` derives its own output location from (see below).

### `query`

Ranks every `path` record in the resolved seam-spine against the free-text query, using only generic schema fields (`path`, `path_step`, `seam`, `edge`, `channel`, `ui_surface`, `reason_code`, `anchor` -- see `jswarm/code-overview/schema/`). Returns a `code_overview.query.v1` JSON packet on stdout:

```json
{
  "schema_version": "code_overview.query.v1",
  "query": "<text>",
  "lod": 2,
  "source": {"spine_path": "<repo-relative path>"},
  "budget": {"limit_tokens": 1200, "counter": "<counter-name>", "estimated_tokens": 405},
  "ranked_paths": [
    {
      "path_id": "...",
      "seam_ids": ["..."],
      "channel_ids": ["..."],
      "ui_surface_ids": ["..."],
      "reason_code_ids": ["..."],
      "pathway": [{"record_id": "...", "label": "..."}]
    }
  ],
  "omissions": []
}
```

The default token budget is 1,200 (counted with `tiktoken`'s `cl100k_base` encoding when importable, else a deterministic `max(word_count, ceil(char_count / 4))` fallback). If the serialized packet would exceed budget, the lowest-ranked paths are dropped one at a time -- never silently truncated -- and every drop is recorded in `omissions`.

### `trace ingest`

Ingests a runtime-observation source (SSE, outbox, workflow/preview outbox, Temporal history, or UI evidence), normalizes and resolves each raw event against the spine's declared edges (plus any `--rules` file), and both prints the result as JSON on stdout **and persists it as siblings of `--spine`**:

```bash
.venv/bin/python jswarm/code-overview/cli.py trace ingest \
  --source sse --run-id <run_id> --input <raw-source-file> \
  --spine <artifact-home-dir>/spine.jsonl --rules <rules.json> --json
```

Persisted output (derived from `--spine`'s parent directory -- there is no separate `--out` flag):

- `<artifact-home-dir>/trace-overlays/<run_id>.jsonl` -- one JSON object per line, exactly the stdout `overlay_records[]` (both `trace_event` and `trace_observation_summary` records), with no data loss.
- `<artifact-home-dir>/trace-summary.json` -- the run-level rollup (`schema_version`, `run_id`, `overlay_scope`, `exit_code`, `source_statuses`, `raw_event_count`, `normalized_event_count`, `summary` counts by resolution status), excluding the bulky `events`/`overlay_records` lists.

Every raw row produces exactly one output event -- unresolved events are kept as `ambiguous`/`unmapped`, never dropped -- and a source that fails to read is recorded in `source_statuses` with zero fabricated events. Re-running `trace ingest` with the same `--run-id` overwrites that run's overlay/summary files (run-keyed, deterministic for the same input). If every source was unavailable (`exit_code: 5`, nothing computed), no overlay/summary files are written for that run -- there is nothing meaningful to persist.

### `benchmark`

Compares a guided query pathway against an unguided brute-force baseline and reports a falsifiable source-line reduction ratio:

```json
{
  "schema_version": "code_overview.benchmark.v1",
  "benchmark_home": "jswarm/code-overview/benchmark/runner.py",
  "pass": true,
  "speedup_lines": 3.0,
  "pathway_nodes": 1
}
```

Pass requires `speedup_lines >= 3.0` and non-zero `pathway_nodes`; a degenerate all-files-equal fixture must report `pass: false`. `--fixture <path>` evaluates the gate directly from a precomputed `code_overview.benchmark_fixture.v1` JSON object instead of loading a spine.
