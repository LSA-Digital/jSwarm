#!/usr/bin/env python3
"""Generic (Layer A) `/code-overview` command-line entry point.

Grammar (subcommand-first, with a legacy back-compatible fallback):

    cli.py                                  legacy no-arg menu (byte-stable)
    cli.py menu                             same as no-arg
    cli.py <grouping_id>                    legacy grouping scenario listing (byte-stable)
    cli.py --localization-smoke             v1 anchor proof + v2 anchor/benchmark report
    cli.py query <text> [--lod N] [...]     token-budgeted ranked-pathway query
    cli.py benchmark [--fixture <path>]     reconciled guided-vs-unguided benchmark
    cli.py audit --anchors --spine <p> --source-root <p> [--scope changed]
                 [--changed-anchors <p>] [--strict] [--json]
                                             anchor freshness/drift audit
    cli.py debt classify --fixture <p> [--strict] [--json]
                                             multi-signal debt-overlay classification
    cli.py impact <question> --fixture <p> [--json]
                                             PM impact-question answer with coverage caveats
    cli.py render --format mermaid|structurizr|summary --spine <p> [--json]
                                             generated seam-path diagram/summary views
    cli.py audit --changed-scope --spine <p> --changed-records <p> [--json]
                                             changed-record-neighborhood-bounded audit
    cli.py audit --close-ticket [--fixture <p>] [--strict] [--describe-integration] [--json]
                                             fixture-ticket stale-anchor close-ceremony audit
    cli.py merge validate --main-spine <p> --branch-spine <p>
                 [--main-source-root <p>] [--branch-source-root <p>] [--json]
                                             branch-vs-main lifecycle-integration audit
    cli.py aggregate --registry <spine-registry.json> --out <spine.jsonl> [--json]
                                             generate the queryable aggregate spine
                                             from scoped spines/*.jsonl in
                                             aggregation_order; the generated
                                             aggregate is do-not-hand-edit -- author
                                             the scoped spines instead and re-run
                                             this command
    cli.py build --spine <p> --out <dir> [--source-root <p>] [--json]
                                             precompute persisted summary/full query
                                             indexes (later-phase subcommands stay
                                             non-silent stubs)
    cli.py query <text> [--lod 4] [--index-dir <dir>] [--json]
                                             LOD4 adds ranked_paths[].source_excerpts[]
                                             and full_pathway_records[] beyond the L0-L3
                                             pathway packet, with NFR-234-002 budget
                                             accounting/omissions. A/C 6: at every LOD
                                             (0-4), ranking/filtering is driven by the
                                             precomputed `generated/summary-index.json`
                                             query index a prior `build` wrote, auto-
                                             discovered at `<project_root>/generated`
                                             (override with `--index-dir`) -- never
                                             live-recomputed from the spine when that
                                             index is present. LOD4 additionally reads
                                             excerpt text from the precomputed
                                             `generated/full-index.json`; it is never
                                             live-resolved from source. `--lod 4` always
                                             requires the generated/ index and fails
                                             closed (exit 8, see below) if it is missing,
                                             unparseable, structurally invalid, or stale
                                             relative to the current spine. LOD0-3
                                             consult the same generated/ index (and fail
                                             closed the same way) only when one already
                                             exists at the resolved directory; with no
                                             prior `build` at all, LOD0-3 fall back to
                                             ranking fully in-memory over the loaded
                                             spine (back-compat).

Every subcommand not listed as functional above prints a clear, non-zero-exit
"not implemented in this phase" message on stderr instead of silently doing
nothing. The legacy no-arg/menu, `<grouping_id>`, and `--localization-smoke`
paths must stay byte-stable with the pre-existing global command; `query`,
`build`, `benchmark`, and `audit --anchors` are the functional subcommands so far.

Exit codes (documented; each is distinct and stable across subcommands):

    0  success (including a `build`/audit "pass"/"warn" status)
    1  audit/build "fail" status, or `--localization-smoke` benchmark check failed
    2  bad arguments, or a spine/rules/overlay file failed to load/parse
    3  a required localization anchor could not be resolved
    4  `audit --anchors` (or similar) reported a hard "fail" verdict
    5  `trace diff` source unavailable
    6  `debt classify --strict` reported a debt-overlay failure
    7  schema/validator reported an unsupported or invalid record
    8  `query --lod 4` (always requires a valid precomputed `generated/`
       index), or `query --lod 0-3` at a project where a `generated/`
       directory already exists, and any part of the triad
       (summary-index.json / full-index.json / build-report.json) inside
       it is unparseable, structurally invalid, or stale relative to the
       current spine -- see A/C 6. Never a traceback; always a clean,
       documented stderr message instructing the caller to rebuild.

This module lives directly under the generic Layer A code home and carries no
project-specific vocabulary of its own -- every project value (scenario JSON
path, spine manifest path, carrier names, ...) is resolved at runtime from the
active project's `.claude/project-command-injections.yaml` localization
anchors, never hard-coded here.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    _CODE_OVERVIEW_HOME = Path(__file__).resolve().parent
    if str(_CODE_OVERVIEW_HOME) not in sys.path:
        sys.path.insert(0, str(_CODE_OVERVIEW_HOME))
    _REPO_ROOT_FOR_BOOTSTRAP = _CODE_OVERVIEW_HOME.parent.parent
    if str(_REPO_ROOT_FOR_BOOTSTRAP) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT_FOR_BOOTSTRAP))

from audits.anchor_freshness import verify_anchors  # noqa: E402
from audits.changed_scope import run_changed_scope_audit  # noqa: E402
from audits.registry import AUDIT_NAMES, run_all_audits, run_audit  # noqa: E402
from benchmark.runner import run_fixture, run_spine  # noqa: E402
from debt import classify_debt  # noqa: E402
from impact import answer_impact_question  # noqa: E402
from lifecycle.close_ticket_audit import (  # noqa: E402
    build_close_ticket_audit_packet,
    describe_integration,
)
from lifecycle.merge_validation import validate_merge  # noqa: E402
from query.build import (  # noqa: E402
    BUILD_REPORT_RELATIVE_PATH,
    GeneratedIndexUnavailable,
    load_full_index_for_query,
    render_persisted_artifacts,
    resolve_generated_index_dir,
    resolve_ranking_candidate_ids,
)
from query.index import build_index  # noqa: E402
from query.render import DEFAULT_BUDGET_TOKENS, render_query_result  # noqa: E402
from render_views import (  # noqa: E402
    StructurizrNotAdopted,
    render_mermaid,
    render_structurizr,
    render_summary,
)
from schema import load_spine, validate_spine  # noqa: E402
from jswarm.devops_command_injection import load_manifest  # noqa: E402
from trace_distiller import distill  # noqa: E402

__all__ = ["main"]

MANAGED_COMMAND_NAME = "code-overview.md"

# A/C 6: `query --lod 4` fails closed with this exit code when the
# precomputed `generated/` index a prior `build` wrote is missing,
# unparseable, structurally invalid, or stale -- see the module docstring's
# "Exit codes" table. Distinct from every other exit code this CLI uses
# (0/1/2/3/4/5/6/7 are all already spoken for).
EXIT_GENERATED_INDEX_UNAVAILABLE = 8

# Legacy (v1) query tool this command's back-compatible paths shell out to,
# unmodified, so their stdout stays byte-stable with the pre-existing global
# command by construction (no re-serialization risk).
QUERY_SCENARIOS_TOOL_RELATIVE = "jswarm/uat-scenarios/query_uat_scenarios.py"
QUERY_SCENARIOS_SCHEMA_RELATIVE = "jswarm/uat-scenarios/schema/uat-scenarios.schema.json"
V1_SCENARIO_SOURCE_ANCHOR_NAME = "code-overview-scenario-source"

V1_ANCHOR_NAMES: tuple[str, ...] = (
    "code-overview-required-reading",
    "code-overview-scenario-source",
    "code-overview-smoke-config",
)

V2_ANCHOR_NAMES: tuple[str, ...] = (
    "code-overview-v2-spine-manifest",
    "code-overview-v2-carrier-vocabulary",
    "code-overview-v2-source-roots",
    "code-overview-v2-trace-sources",
    "code-overview-v2-debt-sources",
    "code-overview-v2-freeze-ledger",
    "code-overview-v2-close-ticket-policy",
    "code-overview-v2-generated-views-policy",
    "code-overview-v2-benchmark-config",
    "code-overview-v2-live-test-case",
)

SPINE_MANIFEST_ANCHOR_NAME = "code-overview-v2-spine-manifest"
DEFAULT_SPINE_RELATIVE_PATH = "jswarm/code-overview/fixtures/pilot_min/spine.jsonl"

BENCHMARK_RUNNER_RELATIVE = "jswarm/code-overview/benchmark/runner.py"
LEGACY_BENCHMARK_RUNNER_RELATIVE = "jswarm/uat-scenarios/benchmark_code_overview.py"

RESERVED_SUBCOMMANDS = frozenset(
    {"menu", "build", "audit", "query", "trace", "debt", "impact", "render", "benchmark"}
)
FUNCTIONAL_SUBCOMMANDS_SO_FAR = (
    "menu/<grouping_id>",
    "--localization-smoke",
    "query",
    "query --lod 4",
    "build",
    "benchmark",
    "audit --anchors",
    "audit --ci",
    "audit --changed-scope",
    "audit --close-ticket",
    "trace ingest",
    "trace diff",
    "debt classify",
    "impact",
    "render --format mermaid|structurizr|summary",
    "merge validate",
)

_BACKTICK_JSON_PATH_PATTERN = re.compile(r"`([^`]+\.json)`")


class LocalizationError(RuntimeError):
    """Raised when a required localization anchor is missing or unresolved."""


# --------------------------------------------------------------------------
# Localization-manifest helpers (project root -> resolved anchor content).
# --------------------------------------------------------------------------


def _anchors_config(manifest: dict[str, Any]) -> dict[str, Any]:
    command_config = (manifest.get("managed_commands") or {}).get(MANAGED_COMMAND_NAME) or {}
    anchors_config = command_config.get("anchors") if isinstance(command_config, dict) else None
    return anchors_config if isinstance(anchors_config, dict) else {}


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    stripped = "\n".join(lines).strip("\n")
    return f"{stripped}\n" if stripped else ""


def _resolve_anchor_content(project_root: Path, anchor_config: dict[str, Any]) -> str:
    has_content = "content" in anchor_config
    has_snippet = "snippet_path" in anchor_config
    if has_content and has_snippet:
        raise ValueError("anchor config must not declare both 'content' and 'snippet_path'")
    if has_content:
        raw_text = str(anchor_config["content"])
    elif has_snippet:
        snippet_path = project_root / str(anchor_config["snippet_path"])
        raw_text = snippet_path.read_text(encoding="utf-8")
    else:
        raise ValueError("anchor config must declare 'content' or 'snippet_path'")
    return _normalize_text(raw_text)


def _first_non_empty_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip():
            return line
    return None


def _anchor_report(project_root: Path, anchors_config: dict[str, Any], name: str) -> dict[str, Any]:
    config = anchors_config.get(name)
    required = bool(config.get("required")) if isinstance(config, dict) else False
    resolved = False
    first_line: str | None = None
    if isinstance(config, dict):
        try:
            content = _resolve_anchor_content(project_root, config)
        except (OSError, ValueError):
            content = ""
        if content.strip():
            resolved = True
            first_line = _first_non_empty_line(content)
    return {"name": name, "required": required, "resolved": resolved, "first_non_empty_line": first_line}


def _resolve_scenarios_path(project_root: Path) -> Path:
    manifest = load_manifest(project_root)
    anchors_config = _anchors_config(manifest)
    config = anchors_config.get(V1_SCENARIO_SOURCE_ANCHOR_NAME)
    if not isinstance(config, dict):
        raise LocalizationError(
            f"required anchor {V1_SCENARIO_SOURCE_ANCHOR_NAME!r} is not configured in "
            f".claude/project-command-injections.yaml under managed_commands[{MANAGED_COMMAND_NAME!r}]."
        )
    content = _resolve_anchor_content(project_root, config)
    first_line = _first_non_empty_line(content)
    match = _BACKTICK_JSON_PATH_PATTERN.search(first_line or "")
    if not match:
        raise LocalizationError(
            f"anchor {V1_SCENARIO_SOURCE_ANCHOR_NAME!r} resolved but its first non-empty line did "
            "not contain a backtick-quoted .json path."
        )
    return project_root / match.group(1)


def _resolve_spine_path(project_root: Path) -> tuple[Path, str]:
    """Resolve the active project's spine path and how it was resolved.

    Returns ``(path, resolution_mode)`` where ``resolution_mode`` is one of
    ``"project_anchor"`` (a configured ``SPINE_MANIFEST_ANCHOR_NAME`` anchor
    resolved to an existing file) or ``"fixture_pilot"`` (the anchor is not
    configured at all, so the packaged pilot fixture is used).

    A *configured-but-invalid* anchor (empty content, or content naming a
    file that does not exist) never silently falls back to the pilot
    fixture -- it raises :class:`LocalizationError` instead, so a project
    that has committed to its own spine never gets a query answered from
    unrelated fixture data.
    """

    manifest = load_manifest(project_root)
    anchors_config = _anchors_config(manifest)
    config = anchors_config.get(SPINE_MANIFEST_ANCHOR_NAME)
    if isinstance(config, dict):
        try:
            content = _resolve_anchor_content(project_root, config)
        except (OSError, ValueError):
            content = ""
        first_line = _first_non_empty_line(content)
        if not first_line:
            raise LocalizationError(
                f"anchor {SPINE_MANIFEST_ANCHOR_NAME!r} resolved to empty content; configure a "
                "spine path (a single non-empty line) via 'content' or 'snippet_path' in "
                ".claude/project-command-injections.yaml, or remove the anchor entirely to use "
                "the packaged pilot fixture."
            )
        candidate = project_root / first_line.strip()
        if not candidate.is_file():
            raise LocalizationError(
                f"anchor {SPINE_MANIFEST_ANCHOR_NAME!r} names {first_line.strip()!r}, but no file "
                f"exists at {candidate}."
            )
        return candidate, "project_anchor"
    # Phase 2 dev-mode fallback: the packaged pilot fixture, used whenever a
    # project has not (yet) configured its own spine-manifest anchor at all.
    return project_root / DEFAULT_SPINE_RELATIVE_PATH, "fixture_pilot"


# --------------------------------------------------------------------------
# Output helpers.
# --------------------------------------------------------------------------


def _emit_json(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


# --------------------------------------------------------------------------
# v1 back-compatible subcommands (byte-stable subprocess passthrough).
# --------------------------------------------------------------------------


def _run_v1_query_tool(project_root: Path, extra_args: list[str]):
    scenarios_path = _resolve_scenarios_path(project_root)
    tool_path = project_root / QUERY_SCENARIOS_TOOL_RELATIVE
    schema_path = project_root / QUERY_SCENARIOS_SCHEMA_RELATIVE
    return subprocess.run(
        [
            sys.executable,
            str(tool_path),
            "--scenarios",
            str(scenarios_path),
            "--schema",
            str(schema_path),
            *extra_args,
        ],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )


def _cmd_v1_menu() -> int:
    project_root = Path.cwd()
    try:
        result = _run_v1_query_tool(project_root, ["--list-groupings"])
    except LocalizationError as error:
        sys.stderr.write(f"LOCALIZATION ERROR: {error}\n")
        return 3
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def _cmd_v1_grouping(grouping_id: str) -> int:
    project_root = Path.cwd()
    try:
        result = _run_v1_query_tool(project_root, ["--list-scenarios", "--grouping", grouping_id])
    except LocalizationError as error:
        sys.stderr.write(f"LOCALIZATION ERROR: {error}\n")
        return 3
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def _cmd_localization_smoke() -> int:
    project_root = Path.cwd()
    manifest = load_manifest(project_root)
    anchors_config = _anchors_config(manifest)

    v1_anchors = [_anchor_report(project_root, anchors_config, name) for name in V1_ANCHOR_NAMES]
    v2_anchors = [_anchor_report(project_root, anchors_config, name) for name in V2_ANCHOR_NAMES]

    runner_path = project_root / BENCHMARK_RUNNER_RELATIVE
    legacy_runner_path = project_root / LEGACY_BENCHMARK_RUNNER_RELATIVE

    report = {
        "mode": "localization-smoke",
        "project_root": str(project_root),
        "v1_anchors": v1_anchors,
        "v2_anchors": v2_anchors,
        "benchmark": {
            "runner_path": BENCHMARK_RUNNER_RELATIVE,
            "legacy_runner_path": LEGACY_BENCHMARK_RUNNER_RELATIVE,
            "available": runner_path.is_file() and legacy_runner_path.is_file(),
        },
    }
    _emit_json(report)
    return 0


# --------------------------------------------------------------------------
# v2 `query` subcommand.
# --------------------------------------------------------------------------


def _build_query_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py query")
    parser.add_argument("query_text", help="free-text description of the pathway to rank")
    parser.add_argument("--lod", type=int, default=2, choices=[0, 1, 2, 3, 4], help="level of detail, 0-4")
    parser.add_argument("--budget-tokens", type=int, default=DEFAULT_BUDGET_TOKENS)
    parser.add_argument("--scenario", default=None, help="narrow to paths whose scenario_ids include this id")
    parser.add_argument("--carrier", default=None, help="narrow to paths whose carrier_scope includes this carrier")
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=None,
        help=(
            "directory containing a prior `build`'s generated/ index "
            "(default: auto-discover <project_root>/generated); consulted at every "
            "--lod 0-4 when present (required at --lod 4) to drive ranking/scoring "
            "from the persisted index instead of the in-memory spine"
        ),
    )
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _cmd_query(argv: list[str]) -> int:
    parser = _build_query_parser()
    args = parser.parse_args(argv)

    project_root = Path.cwd()
    try:
        spine_path, resolution_mode = _resolve_spine_path(project_root)
    except LocalizationError as error:
        sys.stderr.write(f"LOCALIZATION ERROR: {error}\n")
        return 3
    try:
        records = load_spine(spine_path)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load seam-spine at {spine_path}: {error}\n")
        return 2

    resolved_spine_path = spine_path.resolve()
    resolved_project_root = project_root.resolve()
    spine_nested_under_project_root = resolved_spine_path.is_relative_to(resolved_project_root)
    if spine_nested_under_project_root:
        spine_path_for_output = resolved_spine_path.relative_to(resolved_project_root).as_posix()
    else:
        spine_path_for_output = str(spine_path)

    # A/C 6: ranking/filtering at every LOD (0-4) -- not just LOD4's source
    # excerpts -- is driven by a prior `build`'s persisted generated/ index
    # when one is present, never live-recomputed from the spine. --lod 4
    # always requires the index (fails closed if absent); LOD0-3 consult it
    # only when a `generated/` directory already exists (back-compat: no
    # prior `build` at all still ranks fully in-memory). Any part of the
    # triad being missing/unparseable/invalid/stale fails closed with a
    # documented exit code, no traceback, rather than silently recomputing.
    l4_source_excerpts: dict[str, dict[str, Any]] | None = None
    allowed_path_ids: set[str] | None = None
    token_index: dict[str, list[str]] | None = None
    # GAP3 fix: when the resolved spine lives NESTED under the project root
    # (e.g. docs/architecture/pipeline-execution-map/spine.jsonl), default to
    # the spine's OWN parent directory -- where a prior
    # `build --spine <spine> --out <spine.parent>` actually wrote its index --
    # instead of the project root itself; using the project root here
    # previously looked in the wrong directory and produced a false exit 8
    # (or silently ranked in-memory) even when a valid index existed alongside
    # the spine. When the spine is configured OUTSIDE the project root
    # instead (e.g. a shared spine anchored via a `../`-relative manifest
    # entry), this project's own `generated/` output is still expected at
    # this project's own root -- a per-project index cannot be assumed to
    # live inside a shared spine's directory this project doesn't own -- so
    # the pre-GAP3 project-root default is preserved for that case.
    default_index_dir_base = resolved_spine_path.parent if spine_nested_under_project_root else resolved_project_root
    index_dir = resolve_generated_index_dir(default_index_dir_base, index_dir=args.index_dir)
    if args.lod == 4 or index_dir.is_dir():
        try:
            generated_index = load_full_index_for_query(index_dir, spine_path=resolved_spine_path)
        except GeneratedIndexUnavailable as error:
            sys.stderr.write(f"ERROR: --lod {args.lod} requires a valid precomputed generated/ index: {error}\n")
            return EXIT_GENERATED_INDEX_UNAVAILABLE
        allowed_path_ids = resolve_ranking_candidate_ids(generated_index["query_index"])
        token_index = generated_index["query_index"]["token_index"]
        if args.lod == 4:
            l4_source_excerpts = generated_index["source_excerpts"]

    index = build_index(records)
    packet = render_query_result(
        index,
        query_text=args.query_text,
        lod=args.lod,
        spine_path=spine_path_for_output,
        resolution_mode=resolution_mode,
        budget_tokens=args.budget_tokens,
        scenario_id=args.scenario,
        carrier=args.carrier,
        l4_source_excerpts=l4_source_excerpts,
        allowed_path_ids=allowed_path_ids,
        token_index=token_index,
    )
    _emit_json(packet)
    return 0


# --------------------------------------------------------------------------
# `build` subcommand: precompute the persisted summary/full query indexes.
# --------------------------------------------------------------------------


def _build_build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py build")
    parser.add_argument("--spine", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="defaults to the spine file's own parent directory",
    )
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _cmd_build(argv: list[str]) -> int:
    parser = _build_build_parser()
    args = parser.parse_args(argv)

    try:
        records = load_spine(args.spine)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load seam-spine at {args.spine}: {error}\n")
        return 2

    source_root = args.source_root if args.source_root is not None else args.spine.resolve().parent
    artifacts = render_persisted_artifacts(
        records,
        spine_path=str(args.spine),
        source_root=str(source_root),
    )

    for relative_path, payload_bytes in artifacts.items():
        destination = args.out / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload_bytes)

    report = json.loads(artifacts[BUILD_REPORT_RELATIVE_PATH].decode("utf-8"))
    _emit_json(report)
    return 0 if report.get("status") == "pass" else 1


# --------------------------------------------------------------------------
# `aggregate` subcommand: generate the queryable aggregate `spine.jsonl` from
# federated scoped spines + a `spine-registry.json`
# (`code_overview.spine_registry.v1`). Agents author `spines/*.jsonl`; this
# command is the ONLY thing that writes the generated aggregate -- build,
# query, audit, and trace all consume that generated aggregate and it must
# never be hand-edited (see the `do_not_edit` marker this command writes on
# the generated `spine_metadata` record).
# --------------------------------------------------------------------------


def _build_aggregate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py aggregate")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _derive_aggregate_spine_id(scope_metadata: list[tuple[str, dict[str, Any]]]) -> str:
    """Derive the generated aggregate's ``spine_id`` from the scopes' own IDs.

    Generic (Layer A): never hard-codes a project prefix. Each scope's own
    ``spine_metadata.spine_id`` is expected to end with ``.{scope}`` (its own
    scope name); stripping that suffix yields the shared project prefix,
    which is then combined with the literal ``.aggregate`` suffix. Falls
    back to the plain literal ``"aggregate"`` if the scopes disagree, or if
    no scope metadata is available to derive a prefix from.
    """

    prefixes: set[str] = set()
    for scope_name, metadata in scope_metadata:
        spine_id = metadata.get("spine_id")
        if not isinstance(spine_id, str):
            continue
        suffix = f".{scope_name}"
        if spine_id.endswith(suffix):
            prefixes.add(spine_id[: -len(suffix)])
    if len(prefixes) == 1:
        return f"{prefixes.pop()}.aggregate"
    return "aggregate"


def _cmd_aggregate(argv: list[str]) -> int:
    parser = _build_aggregate_parser()
    args = parser.parse_args(argv)

    registry_path: Path = args.registry
    out_path: Path = args.out

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load spine registry at {registry_path}: {error}\n")
        return 2

    registry_home = registry_path.parent
    aggregation_order = list(registry.get("aggregation_order") or [])
    raw_spines = registry.get("spines") or []
    duplicate_id_policy = (registry.get("aggregate") or {}).get("duplicate_id_policy", "fail")

    errors: list[str] = []
    # jCritic MEDIUM fix (fold 1): the only implemented duplicate-id policy is
    # "fail" -- an unsupported value (typo, unimplemented policy name, wrong
    # case, ...) must be a loud registry error, never a silent "no dedup
    # enforcement" fallthrough that would let colliding record_ids merge.
    _SUPPORTED_DUPLICATE_ID_POLICIES = frozenset({"fail"})
    if duplicate_id_policy not in _SUPPORTED_DUPLICATE_ID_POLICIES:
        errors.append(
            f"aggregate.duplicate_id_policy {duplicate_id_policy!r} is not supported; the only "
            f"implemented policy is {sorted(_SUPPORTED_DUPLICATE_ID_POLICIES)!r}."
        )
    # jCritic LOW fix (fold 1): a malformed spines[] entry (not a mapping, or
    # missing 'scope') must surface as a registry error, not a raw
    # TypeError/KeyError traceback bypassing the JSON failure contract.
    spines_by_scope: dict[str, dict[str, Any]] = {}
    for spine_entry in raw_spines:
        if not isinstance(spine_entry, dict) or not spine_entry.get("scope"):
            errors.append(f"spines[] entry {spine_entry!r} is malformed: expected an object with a 'scope' key.")
            continue
        spines_by_scope[spine_entry["scope"]] = spine_entry

    if errors:
        _emit_json(
            {
                "schema_version": "code_overview.aggregate.v1",
                "status": "fail",
                "source_registry": str(registry_path),
                "out": str(out_path),
                "scope_order": aggregation_order,
                "errors": errors,
            }
        )
        return 1

    merged_records: list[dict[str, Any]] = []
    seen_ids: dict[str, str] = {}
    scope_metadata: list[tuple[str, dict[str, Any]]] = []
    generated_from: list[str] = [registry_path.name]

    for scope in aggregation_order:
        entry = spines_by_scope.get(scope)
        scope_relative_path = entry.get("path") if isinstance(entry, dict) else None
        if not scope_relative_path:
            errors.append(
                f"aggregation_order references scope {scope!r}, but spines[] has no matching entry "
                "with a 'path'."
            )
            continue
        generated_from.append(scope_relative_path)
        scope_path = registry_home / scope_relative_path
        try:
            scope_records = load_spine(scope_path)
        except (OSError, ValueError) as error:
            errors.append(f"failed to load scoped spine for scope {scope!r} at {scope_path}: {error}")
            continue

        for record in scope_records:
            if record.get("record_type") == "spine_metadata":
                scope_metadata.append((scope, record))
                continue
            record_id = record.get("record_id")
            if record_id:
                if record_id in seen_ids:
                    if duplicate_id_policy == "fail":
                        errors.append(
                            f"duplicate record_id {record_id!r}: declared in scope "
                            f"{seen_ids[record_id]!r} and scope {scope!r} (duplicate_id_policy="
                            f"{duplicate_id_policy!r})."
                        )
                        continue
                else:
                    seen_ids[record_id] = scope
            merged_records.append(record)

    if errors:
        _emit_json(
            {
                "schema_version": "code_overview.aggregate.v1",
                "status": "fail",
                "source_registry": str(registry_path),
                "out": str(out_path),
                "scope_order": aggregation_order,
                "errors": errors,
            }
        )
        return 1

    last_refresh_values = sorted(
        entry.get("last_refresh") for entry in spines_by_scope.values() if entry.get("last_refresh")
    )
    template_metadata: dict[str, Any] = scope_metadata[0][1] if scope_metadata else {}

    metadata_record: dict[str, Any] = {
        "record_type": "spine_metadata",
        "schema_version": template_metadata.get("schema_version", "1.0"),
        "spine_id": _derive_aggregate_spine_id(scope_metadata),
        "project": template_metadata.get("project", "aggregate"),
        "feature_parent": template_metadata.get("feature_parent", "aggregate"),
        "last_updated": last_refresh_values[-1] if last_refresh_values else template_metadata.get(
            "last_updated", "unknown"
        ),
        "last_updated_by": "code-overview aggregate",
        "generated_from": generated_from,
        "coverage_summary": {"scopes": aggregation_order},
        "gap_summary": {},
        "generated": True,
        "generated_by": "code-overview aggregate",
        "do_not_edit": (
            "generated from spine-registry.json and spines/*.jsonl -- hand-edit the scoped "
            "spines/*.jsonl instead and re-run `aggregate` to regenerate this file."
        ),
    }

    all_records: list[dict[str, Any]] = [metadata_record, *merged_records]
    validation = validate_spine(all_records)
    if not validation.ok:
        _emit_json(
            {
                "schema_version": "code_overview.aggregate.v1",
                "status": "fail",
                "source_registry": str(registry_path),
                "out": str(out_path),
                "scope_order": aggregation_order,
                "errors": [error.message for error in validation.errors],
            }
        )
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in all_records),
        encoding="utf-8",
    )

    _emit_json(
        {
            "schema_version": "code_overview.aggregate.v1",
            "status": "pass",
            "source_registry": str(registry_path),
            "out": str(out_path),
            "scope_order": aggregation_order,
            "record_count": len(all_records),
        }
    )
    return 0


# --------------------------------------------------------------------------
# v2 `benchmark` subcommand.
# --------------------------------------------------------------------------


def _build_benchmark_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py benchmark")
    parser.add_argument("--fixture", type=Path, default=None, help="path to a benchmark_fixture.v1 JSON file")
    parser.add_argument(
        "--v1-compat",
        action="store_true",
        help="defer to the legacy runner instead of the reconciled v2 runner",
    )
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _cmd_benchmark(argv: list[str]) -> int:
    parser = _build_benchmark_parser()
    args = parser.parse_args(argv)

    if args.v1_compat:
        sys.stderr.write(
            "NOT IMPLEMENTED: benchmark --v1-compat does not yet delegate to the legacy runner "
            f"from this subcommand. Invoke {LEGACY_BENCHMARK_RUNNER_RELATIVE} directly, or omit "
            "--v1-compat to run the reconciled v2 benchmark.\n"
        )
        return 1

    project_root = Path.cwd()
    try:
        if args.fixture is not None:
            report = run_fixture(args.fixture)
        else:
            spine_path, _resolution_mode = _resolve_spine_path(project_root)
            report = run_spine(spine_path)
    except Exception as error:  # noqa: BLE001 - report, never crash silently
        sys.stderr.write(f"ERROR: {error}\n")
        return 2

    _emit_json(report)
    return 0 if report.get("pass") is True else 1


# --------------------------------------------------------------------------
# v2 `audit --anchors` subcommand (Phase 3 of the `audit` rollout; every
# other `audit` mode -- distiller/debt/lifecycle/etc. -- stays a non-silent
# stub until its own phase).
# --------------------------------------------------------------------------


def _build_audit_anchors_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py audit --anchors")
    parser.add_argument("--spine", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--changed-anchors", type=Path, default=None)
    parser.add_argument("--scope", choices=["all", "changed"], default="all")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _load_changed_anchor_ids(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    anchor_ids = payload.get("anchor_ids") if isinstance(payload, dict) else None
    if not isinstance(anchor_ids, list):
        raise ValueError(f"--changed-anchors file {path} must contain an object with an 'anchor_ids' list")
    return [str(anchor_id) for anchor_id in anchor_ids]


def _cmd_audit_anchors(argv: list[str]) -> int:
    parser = _build_audit_anchors_parser()
    args = parser.parse_args(argv)

    try:
        records = load_spine(args.spine)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load seam-spine at {args.spine}: {error}\n")
        return 2

    changed_anchor_ids: list[str] | None = None
    if args.scope == "changed":
        if args.changed_anchors is None:
            sys.stderr.write("ERROR: --scope changed requires --changed-anchors <path>.\n")
            return 2
        try:
            changed_anchor_ids = _load_changed_anchor_ids(args.changed_anchors)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            sys.stderr.write(f"ERROR: failed to load --changed-anchors at {args.changed_anchors}: {error}\n")
            return 2

    report = verify_anchors(
        records,
        source_root=args.source_root,
        strict=args.strict,
        changed_anchor_ids=changed_anchor_ids,
    )
    packet = report.to_dict()
    _emit_json(packet)
    sys.stderr.write(
        f"anchor audit: scope={packet['scope']} checked={len(packet['checked_anchor_ids'])} "
        f"skipped={len(packet['skipped_anchor_ids'])} exit_code={packet['exit_code']}\n"
    )
    return int(packet["exit_code"])


def _build_audit_ci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py audit --ci")
    parser.add_argument("--spine", required=True, type=Path)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _cmd_audit_ci(argv: list[str]) -> int:
    parser = _build_audit_ci_parser()
    args = parser.parse_args(argv)

    try:
        records = load_spine(args.spine)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load seam-spine at {args.spine}: {error}\n")
        return 2

    validation = validate_spine(records)
    if not validation.ok:
        # ADV-4: preserve the validator's own exit_code (e.g. 7 for an
        # unsupported schema major) instead of collapsing every schema
        # validation failure to 2.
        packet = {
            "schema_version": "code_overview.audit.ci.v1",
            "status": "fail",
            "exit_code": validation.exit_code,
            "spine_path": str(args.spine),
            "source_root": str(args.source_root) if args.source_root else None,
            "audits": [],
            "summary": {
                "passed": 0,
                "failed": 0,
                "warned": 0,
                "new_violation_count": 0,
                "frozen_violation_count": 0,
            },
            "schema_errors": [
                {"code": error.code, "message": error.message, "field": error.field}
                for error in validation.errors
            ],
        }
        _emit_json(packet)
        for error in validation.errors:
            sys.stderr.write(f"FAIL schema_validation: {error.message}\n")
        return validation.exit_code

    results = run_all_audits(records, source_root=args.source_root, strict=True)
    audits_payload = [result.to_dict() for result in results]
    passed = sum(1 for result in results if result.status == "pass")
    failed = sum(1 for result in results if result.status == "fail")
    warned = sum(1 for result in results if result.status == "warn")
    new_violation_count = sum(len(result.new_violations) for result in results)
    frozen_violation_count = sum(len(result.frozen_violations) for result in results)

    anchor_result = next(
        (result for result in results if result.audit_name == "anchors_verified_or_relocated"),
        None,
    )
    if failed == 0 and warned == 0:
        exit_code, status = 0, "pass"
    elif anchor_result is not None and anchor_result.status != "pass":
        exit_code, status = 4, "fail"
    elif failed > 0:
        exit_code, status = 1, "fail"
    else:
        exit_code, status = 0, "warn"

    packet = {
        "schema_version": "code_overview.audit.ci.v1",
        "status": status,
        "exit_code": exit_code,
        "spine_path": str(args.spine),
        "source_root": str(args.source_root) if args.source_root else None,
        "audits": audits_payload,
        "summary": {
            "passed": passed,
            "failed": failed,
            "warned": warned,
            "new_violation_count": new_violation_count,
            "frozen_violation_count": frozen_violation_count,
        },
    }
    _emit_json(packet)
    for result in results:
        line_status = "PASS" if result.status == "pass" else "FAIL" if result.status == "fail" else "WARN"
        sys.stderr.write(f"{line_status} {result.audit_name}\n")
    return exit_code


def _build_audit_changed_scope_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py audit --changed-scope")
    parser.add_argument("--spine", required=True, type=Path)
    parser.add_argument("--changed-records", required=True, type=Path)
    parser.add_argument("--strict", action="store_true", default=True)
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _load_changed_records_request(path: Path) -> tuple[list[str], list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"--changed-records file {path} must contain a JSON object")
    changed_record_ids = payload.get("changed_record_ids")
    if not isinstance(changed_record_ids, list):
        raise ValueError(f"--changed-records file {path} must contain a 'changed_record_ids' list")
    audit_names = payload.get("audit_names")
    if audit_names is None:
        audit_names = list(AUDIT_NAMES)
    if not isinstance(audit_names, list):
        raise ValueError(f"--changed-records file {path} 'audit_names', if present, must be a list")
    return [str(record_id) for record_id in changed_record_ids], [str(name) for name in audit_names]


def _cmd_audit_changed_scope(argv: list[str]) -> int:
    parser = _build_audit_changed_scope_parser()
    args = parser.parse_args(argv)

    try:
        records = load_spine(args.spine)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load seam-spine at {args.spine}: {error}\n")
        return 2

    try:
        changed_record_ids, audit_names = _load_changed_records_request(args.changed_records)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"ERROR: failed to load --changed-records at {args.changed_records}: {error}\n")
        return 2

    try:
        packet = run_changed_scope_audit(
            records,
            changed_record_ids=changed_record_ids,
            audit_names=audit_names,
            strict=args.strict,
        )
    except ValueError as error:
        # A valid CLI request must never stack-trace: any residual
        # unsupported-audit-name (or other request-shape) ValueError from
        # run_changed_scope_audit is a documented usage/config failure, not
        # an uncaught exception (Phase 7 GATE B4).
        sys.stderr.write(f"ERROR: audit --changed-scope request rejected: {error}\n")
        return 2
    _emit_json(packet)
    return int(packet["exit_code"])


def _build_audit_close_ticket_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py audit --close-ticket")
    parser.add_argument("--fixture", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--describe-integration", action="store_true")
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _cmd_audit_close_ticket(argv: list[str]) -> int:
    parser = _build_audit_close_ticket_parser()
    args = parser.parse_args(argv)

    if args.describe_integration:
        _emit_json(describe_integration())
        return 0

    if args.fixture is None:
        sys.stderr.write("ERROR: 'audit --close-ticket' requires --fixture (or --describe-integration).\n")
        return 2

    try:
        fixture = _load_json_object_fixture(args.fixture)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"ERROR: failed to load --fixture at {args.fixture}: {error}\n")
        return 2

    fixture_dir = args.fixture.parent
    try:
        spine_path = fixture_dir / fixture["spine"]
        source_root = fixture_dir / fixture["source_root"]
        records = load_spine(spine_path)
    except (OSError, ValueError, KeyError) as error:
        sys.stderr.write(f"ERROR: failed to resolve close-ticket fixture at {args.fixture}: {error}\n")
        return 2

    packet = build_close_ticket_audit_packet(
        ticket=str(fixture.get("ticket", "")),
        changed_anchor_ids=fixture.get("changed_anchor_ids") or [],
        records=records,
        source_root=source_root,
        strict=args.strict,
    )
    _emit_json(packet)
    return int(packet["exit_code"])


def _cmd_audit(argv: list[str]) -> int:
    if "--ci" in argv:
        return _cmd_audit_ci([arg for arg in argv if arg != "--ci"])
    if "--changed-scope" in argv:
        return _cmd_audit_changed_scope([arg for arg in argv if arg != "--changed-scope"])
    if "--close-ticket" in argv:
        return _cmd_audit_close_ticket([arg for arg in argv if arg != "--close-ticket"])
    if "--anchors" not in argv:
        return _cmd_stub("audit")
    return _cmd_audit_anchors([arg for arg in argv if arg != "--anchors"])


# --------------------------------------------------------------------------
# trace ingest / trace diff (Phase 5 trace distiller).
# --------------------------------------------------------------------------


def _build_trace_ingest_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py trace ingest")
    parser.add_argument(
        "--source",
        required=True,
        choices=("sse", "outbox", "workflow_outbox", "preview_outbox", "temporal_history", "ui_evidence"),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--spine", required=True, type=Path)
    parser.add_argument("--rules", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def _infer_source_shape(source_type: str, input_path: Path) -> tuple[str, str | None]:
    if source_type == "temporal_history":
        return "json", None
    if input_path.suffix.lower() == ".json":
        return "json", "events"
    return "ndjson", None


def _load_trace_rules(rules_path: Path | None) -> list[dict[str, Any]]:
    if rules_path is None:
        return []
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "rules" in payload:
        payload = payload["rules"]
    if not isinstance(payload, list):
        raise ValueError(f"--rules file {rules_path} must contain a JSON list of rule objects")
    return [rule for rule in payload if isinstance(rule, dict)]


def _persist_trace_overlay(result: dict[str, Any], *, spine_path: Path, run_id: str) -> None:
    """Persist the #296 S1.1 trace-overlay artifacts as siblings of the spine.

    ``distill()`` already computed ``overlay_records`` and the run-level
    summary fields on ``result`` (also emitted to stdout); this only writes
    that already-computed data to disk so agents/tools can discover it
    without re-running ``trace ingest``:

    - ``<spine.parent>/trace-overlays/<run_id>.jsonl`` -- one JSON object per
      line, exactly ``result["overlay_records"]``, no data loss.
    - ``<spine.parent>/trace-summary.json`` -- the run-level summary packet
      (excludes the bulky ``events``/``overlay_records`` lists).
    """

    artifact_home = spine_path.parent

    overlay_dir = artifact_home / "trace-overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = overlay_dir / f"{run_id}.jsonl"
    with overlay_path.open("w", encoding="utf-8") as handle:
        for record in result["overlay_records"]:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    summary = {
        "schema_version": result["schema_version"],
        "run_id": result["run_id"],
        "overlay_scope": result["overlay_scope"],
        "exit_code": result["exit_code"],
        "source_statuses": result["source_statuses"],
        "raw_event_count": result["raw_event_count"],
        "normalized_event_count": result["normalized_event_count"],
        "summary": result["summary"],
    }
    summary_path = artifact_home / "trace-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _cmd_trace_ingest(argv: list[str]) -> int:
    parser = _build_trace_ingest_parser()
    args = parser.parse_args(argv)

    try:
        rules = _load_trace_rules(args.rules)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"ERROR: failed to load --rules at {args.rules}: {error}\n")
        return 2

    fmt, json_path = _infer_source_shape(args.source, args.input)
    raw_source: dict[str, Any] = {
        "source_type": args.source,
        "path": str(args.input),
        "format": fmt,
    }
    if json_path:
        raw_source["json_path"] = json_path

    try:
        result = distill(
            raw_sources=[raw_source],
            spine_path=args.spine,
            rules=rules,
            run_id=args.run_id,
            overlay_scope="branch",
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"ERROR: trace ingest failed: {error}\n")
        return 2

    if result["exit_code"] != 5:
        # exit_code 5 means every source was unavailable and nothing was
        # computed (raw_event_count == 0, overlay_records == []) -- skip
        # writing empty overlay/summary artifacts for a run that produced
        # no observations at all.
        _persist_trace_overlay(result, spine_path=args.spine, run_id=args.run_id)

    if args.json:
        _emit_json(result)
    else:
        sys.stdout.write(
            f"trace ingest: raw={result['raw_event_count']} "
            f"normalized={result['normalized_event_count']} exit_code={result['exit_code']}\n"
        )
    for status in result["source_statuses"]:
        sys.stderr.write(f"source {status['source_type']} ({status['path']}): {status['status']}\n")
    return int(result["exit_code"])


def _build_trace_diff_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py trace diff")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--spine", required=True, type=Path)
    parser.add_argument("--overlay", required=True, type=Path)
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _load_overlay_records(overlay_path: Path) -> list[dict[str, Any]]:
    stripped = overlay_path.read_text(encoding="utf-8").strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        payload = json.loads(stripped)
        if isinstance(payload, dict) and isinstance(payload.get("overlay_records"), list):
            return [record for record in payload["overlay_records"] if isinstance(record, dict)]
        if isinstance(payload, list):
            return [record for record in payload if isinstance(record, dict)]
        raise ValueError(f"--overlay file {overlay_path} did not contain overlay_records[] or a JSON list")
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def _source_unavailable_diff_packet(run_id: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": "code_overview.trace_diff.v1",
        "run_id": run_id,
        "exit_code": 5,
        "status": "source_unavailable",
        "message": message,
    }


def _cmd_trace_diff(argv: list[str]) -> int:
    parser = _build_trace_diff_parser()
    args = parser.parse_args(argv)

    if not args.spine.is_file():
        _emit_json(_source_unavailable_diff_packet(args.run_id, f"spine not found: {args.spine}"))
        return 5
    if not args.overlay.is_file():
        _emit_json(_source_unavailable_diff_packet(args.run_id, f"overlay not found: {args.overlay}"))
        return 5

    try:
        spine_records = load_spine(args.spine)
        overlay_records = _load_overlay_records(args.overlay)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"ERROR: failed to load trace diff inputs: {error}\n")
        return 2

    combined_records = list(spine_records) + list(overlay_records)
    audit_result = run_audit("observed_edges_declared", combined_records, strict=True)
    audit_payload = audit_result.to_dict()

    observed_events = [
        record
        for record in overlay_records
        if record.get("record_type") == "trace_event" and record.get("record_status") == "declared"
    ]
    unmapped_events = [event for event in observed_events if event.get("resolution_status") == "unmapped"]
    ambiguous_events = [event for event in observed_events if event.get("resolution_status") == "ambiguous"]

    exit_code = 1 if audit_payload["status"] == "fail" else 0
    packet = {
        "schema_version": "code_overview.trace_diff.v1",
        "run_id": args.run_id,
        "spine_path": str(args.spine),
        "overlay_path": str(args.overlay),
        "exit_code": exit_code,
        "declared_edge_count": sum(1 for record in spine_records if record.get("record_type") == "edge"),
        "observed_event_count": len(observed_events),
        "unmapped_events": unmapped_events,
        "ambiguous_events": ambiguous_events,
        "audit": audit_payload,
    }
    _emit_json(packet)
    return exit_code


def _cmd_trace(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("ERROR: 'trace' requires a subcommand: ingest|diff.\n")
        return 2
    subcommand, rest = argv[0], argv[1:]
    if subcommand == "ingest":
        return _cmd_trace_ingest(rest)
    if subcommand == "diff":
        return _cmd_trace_diff(rest)
    sys.stderr.write(f"ERROR: unknown 'trace' subcommand {subcommand!r}; expected ingest|diff.\n")
    return 2


# --------------------------------------------------------------------------
# `debt classify` (Phase 6 debt-overlay classifier).
# --------------------------------------------------------------------------


def _build_debt_classify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py debt classify")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _load_json_object_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"fixture {path} must contain a JSON object")
    return payload


def _cmd_debt_classify(argv: list[str]) -> int:
    parser = _build_debt_classify_parser()
    args = parser.parse_args(argv)

    try:
        fixture = _load_json_object_fixture(args.fixture)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"ERROR: failed to load --fixture at {args.fixture}: {error}\n")
        return 2

    packet = classify_debt(fixture, strict=args.strict)
    _emit_json(packet)
    return int(packet.get("exit_code", 0))


def _cmd_debt(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("ERROR: 'debt' requires a subcommand: classify.\n")
        return 2
    subcommand, rest = argv[0], argv[1:]
    if subcommand == "classify":
        return _cmd_debt_classify(rest)
    sys.stderr.write(f"ERROR: unknown 'debt' subcommand {subcommand!r}; expected classify.\n")
    return 2


# --------------------------------------------------------------------------
# `impact <question>` (Phase 6 PM impact-question surface).
# --------------------------------------------------------------------------


def _build_impact_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py impact")
    parser.add_argument("question", help="free-text PM-style impact question")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _cmd_impact(argv: list[str]) -> int:
    parser = _build_impact_parser()
    args = parser.parse_args(argv)

    try:
        fixture = _load_json_object_fixture(args.fixture)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"ERROR: failed to load --fixture at {args.fixture}: {error}\n")
        return 2

    packet = answer_impact_question(fixture, args.question)
    _emit_json(packet)
    return 0


# --------------------------------------------------------------------------
# `render --format mermaid|structurizr|summary` (Phase 7A generated views).
# --------------------------------------------------------------------------


def _build_render_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py render")
    parser.add_argument("--format", required=True, choices=["mermaid", "structurizr", "summary"])
    parser.add_argument("--spine", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _cmd_render(argv: list[str]) -> int:
    parser = _build_render_parser()
    args = parser.parse_args(argv)

    if args.spine is not None:
        spine_path = args.spine
    else:
        try:
            spine_path, _resolution_mode = _resolve_spine_path(Path.cwd())
        except LocalizationError as error:
            sys.stderr.write(f"LOCALIZATION ERROR: {error}\n")
            return 3
    try:
        records = load_spine(spine_path)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load seam-spine at {spine_path}: {error}\n")
        return 2

    if args.format == "mermaid":
        sys.stdout.write(render_mermaid(records))
        return 0

    if args.format == "structurizr":
        try:
            packet = render_structurizr(records)
        except StructurizrNotAdopted as error:
            sys.stderr.write(f"NOT ADOPTED: {error}\n")
            return 6
        _emit_json(packet)
        return 0

    _emit_json(render_summary(records))
    return 0


# --------------------------------------------------------------------------
# `merge validate` (Phase 7B branch-vs-main lifecycle-integration audit).
# --------------------------------------------------------------------------


def _build_merge_validate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py merge validate")
    parser.add_argument("--main-spine", required=True, type=Path)
    parser.add_argument("--branch-spine", required=True, type=Path)
    parser.add_argument("--main-source-root", type=Path, default=None)
    parser.add_argument("--branch-source-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="accepted for grammar parity; output is always JSON")
    return parser


def _cmd_merge_validate(argv: list[str]) -> int:
    parser = _build_merge_validate_parser()
    args = parser.parse_args(argv)

    try:
        main_records = load_spine(args.main_spine)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load --main-spine at {args.main_spine}: {error}\n")
        return 2
    try:
        branch_records = load_spine(args.branch_spine)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"ERROR: failed to load --branch-spine at {args.branch_spine}: {error}\n")
        return 2

    packet = validate_merge(
        main_records=main_records,
        branch_records=branch_records,
        main_spine_path=str(args.main_spine),
        branch_spine_path=str(args.branch_spine),
        main_source_root=args.main_source_root,
        branch_source_root=args.branch_source_root,
    )
    _emit_json(packet)
    return int(packet["exit_code"])


def _cmd_merge(argv: list[str]) -> int:
    """`merge validate` is new; every other `merge ...` shape stays the legacy grouping path."""
    if argv and argv[0] == "validate":
        return _cmd_merge_validate(argv[1:])
    return _cmd_v1_grouping("merge")


# --------------------------------------------------------------------------
# Later-phase subcommands: non-silent stubs.
# --------------------------------------------------------------------------


def _cmd_stub(name: str) -> int:
    sys.stderr.write(
        f"NOT IMPLEMENTED: the '{name}' subcommand ships in a later rollout phase of this "
        "command. This phase implements only: "
        f"{', '.join(FUNCTIONAL_SUBCOMMANDS_SO_FAR)}.\n"
    )
    return 1


# --------------------------------------------------------------------------
# Dispatch.
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    if not args:
        return _cmd_v1_menu()

    if args[0] == "--localization-smoke":
        return _cmd_localization_smoke()

    first, rest = args[0], args[1:]

    if first == "menu":
        return _cmd_v1_menu()
    if first == "query":
        return _cmd_query(rest)
    if first == "build":
        return _cmd_build(rest)
    if first == "aggregate":
        return _cmd_aggregate(rest)
    if first == "benchmark":
        return _cmd_benchmark(rest)
    if first == "audit":
        return _cmd_audit(rest)
    if first == "trace":
        return _cmd_trace(rest)
    if first == "debt":
        return _cmd_debt(rest)
    if first == "impact":
        return _cmd_impact(rest)
    if first == "render":
        return _cmd_render(rest)
    if first == "merge":
        return _cmd_merge(rest)
    if first in RESERVED_SUBCOMMANDS:
        return _cmd_stub(first)
    if first.startswith("-"):
        sys.stderr.write(f"ERROR: unrecognized option {first!r}.\n")
        return 2

    # Anything else is treated as a legacy grouping id (back-compatible flow).
    return _cmd_v1_grouping(first)


if __name__ == "__main__":
    raise SystemExit(main())
