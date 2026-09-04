"""Generic (Layer A) persisted-index builder for `cli.py build`.

This module renders the byte-stable artifact set the `build` subcommand
writes under an output directory's `generated/` folder:

- ``summary-index.json`` (``code_overview.summary_index.v1``) -- the
  precomputed *query index*: an inverted token index over the spine's
  seam/channel/ui_surface/reason_code/path records, a scenario-to-path join,
  seam graph centrality (in-degree + out-degree over ``edge`` records), a
  file-to-anchor index, per-seam trace-observation counters, and a list of
  audit-divergence flags (records whose generic lifecycle/anchor state
  fields have drifted from their declared baseline).
- ``full-index.json`` (``code_overview.full_index.v1``) -- persisted source
  excerpts (file/line/text) for every ``anchor`` record whose source file is
  reachable under the resolved source root. This is what LOD4 queries read
  their source excerpts from.
- ``build-report.json`` (``code_overview.build_report.v1``) -- a manifest of
  the two artifacts above (byte length + sha256), for downstream freshness
  checks.

Every function here is a pure function of the loaded spine records (plus the
two provenance strings ``spine_path``/``source_root``): given the same
inputs, the same bytes come back every run -- no timestamps, random ids, or
process/environment state leak into the persisted output. Only generic
schema-field names (``record_type``, ``name``, ``notes``, ``scenario_ids``,
``file_path``, ``line_start``, ``line_end``, ...) are used; no project
vocabulary is hard-coded here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .index import SpineIndex, build_index

__all__ = [
    "BUILD_REPORT_RELATIVE_PATH",
    "BUILD_REPORT_SCHEMA_VERSION",
    "FULL_INDEX_RELATIVE_PATH",
    "FULL_INDEX_SCHEMA_VERSION",
    "GeneratedIndexUnavailable",
    "SUMMARY_INDEX_RELATIVE_PATH",
    "SUMMARY_INDEX_SCHEMA_VERSION",
    "build_full_index",
    "build_summary_index",
    "compute_spine_sha256",
    "load_full_index_for_query",
    "render_persisted_artifacts",
    "resolve_generated_index_dir",
    "resolve_ranking_candidate_ids",
    "resolve_source_excerpt",
]


class GeneratedIndexUnavailable(Exception):
    """Raised when the precomputed ``generated/`` index cannot be used for a query.

    Covers: the ``generated/`` directory (or an explicit ``--index-dir``) is
    missing; any member of the ``generated/`` triad (``build-report.json``,
    ``full-index.json``, ``summary-index.json``) is missing/unparseable/has
    the wrong ``schema_version``; ``build-report.json`` is missing a
    non-empty ``spine_sha256`` (can never disable the staleness check); the
    current spine's sha256 no longer matches the recorded ``spine_sha256``
    (stale relative to the current inputs); any ``full-index.json``
    ``source_excerpts`` entry is structurally invalid; or
    ``summary-index.json``'s ``query_index`` object is structurally invalid
    (A/C 6: ranking at every LOD is driven by this precomputed query index
    when a ``generated/`` index is present, so an unusable ``query_index``
    must fail the whole query closed, not silently fall back to an in-memory
    recompute). Query callers catch this and fail closed with a documented
    exit code instead of silently recomputing excerpts or rankings live from
    source.
    """

SUMMARY_INDEX_SCHEMA_VERSION = "code_overview.summary_index.v1"
FULL_INDEX_SCHEMA_VERSION = "code_overview.full_index.v1"
BUILD_REPORT_SCHEMA_VERSION = "code_overview.build_report.v1"

SUMMARY_INDEX_RELATIVE_PATH = "generated/summary-index.json"
FULL_INDEX_RELATIVE_PATH = "generated/full-index.json"
BUILD_REPORT_RELATIVE_PATH = "generated/build-report.json"

# Record types whose generic text fields feed the token index. This mirrors
# the record types a guided pathway walks (see query.index.build_ranked_path):
# path, seam, channel, ui_surface, reason_code.
_TOKEN_INDEX_RECORD_TYPES = ("seam", "channel", "ui_surface", "reason_code", "path")
_TOKEN_TEXT_FIELDS = ("name", "notes", "subsystem", "capability", "meaning", "surface_name", "semantic_hint")

_WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _WORD_PATTERN.findall(text) if token}


def _record_searchable_text(record: dict[str, Any]) -> str:
    """Generic free-text corpus for one record, built from schema text fields only."""

    parts = [str(record.get(field_name) or "") for field_name in _TOKEN_TEXT_FIELDS]
    parts.extend(str(item) for item in (record.get("preconditions") or []))
    parts.extend(str(item) for item in (record.get("postconditions") or []))
    return " ".join(part for part in parts if part)


def _build_token_index(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    token_to_record_ids: dict[str, set[str]] = {}
    for record in records:
        if record.get("record_type") not in _TOKEN_INDEX_RECORD_TYPES:
            continue
        record_id = record.get("record_id")
        if not record_id:
            continue
        for token in _tokenize(_record_searchable_text(record)):
            token_to_record_ids.setdefault(token, set()).add(record_id)
    return {token: sorted(record_ids) for token, record_ids in sorted(token_to_record_ids.items())}


def _build_scenario_to_path(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    scenario_to_path: dict[str, set[str]] = {}
    for record in records:
        if record.get("record_type") != "path":
            continue
        path_id = record.get("record_id")
        if not path_id:
            continue
        for scenario_id in record.get("scenario_ids") or []:
            scenario_to_path.setdefault(scenario_id, set()).add(path_id)
    return {scenario_id: sorted(path_ids) for scenario_id, path_ids in sorted(scenario_to_path.items())}


def _build_graph_centrality(index: SpineIndex) -> dict[str, int]:
    in_degree: dict[str, int] = {}
    for edge in index.by_type.get("edge", []):
        to_id = edge.get("to_record_id")
        if to_id:
            in_degree[to_id] = in_degree.get(to_id, 0) + 1

    centrality: dict[str, int] = {}
    for seam in index.by_type.get("seam", []):
        seam_id = seam.get("record_id")
        if not seam_id:
            continue
        out_degree = len(index.edges_from.get(seam_id, []))
        centrality[seam_id] = out_degree + in_degree.get(seam_id, 0)
    return dict(sorted(centrality.items()))


def _build_file_to_record_anchors(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    file_to_anchors: dict[str, set[str]] = {}
    for record in records:
        if record.get("record_type") != "anchor":
            continue
        file_path = record.get("file_path")
        anchor_id = record.get("record_id")
        if not file_path or not anchor_id:
            continue
        file_to_anchors.setdefault(file_path, set()).add(anchor_id)
    return {file_path: sorted(anchor_ids) for file_path, anchor_ids in sorted(file_to_anchors.items())}


def _build_trace_observation_counters(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counters: dict[str, dict[str, int]] = {}
    for record in records:
        if record.get("record_type") != "trace_observation_summary":
            continue
        seam_id = record.get("seam_id")
        if not seam_id:
            continue
        counters[seam_id] = {
            "observed_count": int(record.get("observed_count") or 0),
            "failed_count": int(record.get("failed_count") or 0),
            "dropped_count": int(record.get("dropped_count") or 0),
            "unpaired_count": int(record.get("unpaired_count") or 0),
        }
    return dict(sorted(counters.items()))


def _build_audit_divergence_flags(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag records whose generic lifecycle/anchor-state fields have drifted.

    Purely schema-field-based (``anchor_state``, ``lifecycle_state``): a
    record is flagged when an ``anchor`` record's ``anchor_state`` is present
    and not ``"verified"``, or any record's ``lifecycle_state`` is present
    and not ``"active"``. No project-specific vocabulary is involved.
    """

    flags: list[dict[str, Any]] = []
    for record in records:
        record_id = record.get("record_id")
        record_type = record.get("record_type")
        if record_type == "anchor":
            anchor_state = record.get("anchor_state")
            if anchor_state not in (None, "verified"):
                flags.append(
                    {
                        "record_id": record_id,
                        "record_type": record_type,
                        "kind": "anchor_state_divergence",
                        "detail": f"anchor_state={anchor_state!r} has not been verified",
                    }
                )
        lifecycle_state = record.get("lifecycle_state")
        if lifecycle_state not in (None, "active"):
            flags.append(
                {
                    "record_id": record_id,
                    "record_type": record_type,
                    "kind": "lifecycle_state_divergence",
                    "detail": f"lifecycle_state={lifecycle_state!r} is not active",
                }
            )
    return flags


def resolve_source_excerpt(source_root: Path, anchor: dict[str, Any]) -> dict[str, Any] | None:
    """Read the source lines an ``anchor`` record points at, if reachable.

    Returns ``None`` (never raises) when the anchor's ``file_path``/
    ``line_start``/``line_end`` fields are missing or the file cannot be
    read, so a stale/relocated anchor degrades a single excerpt instead of
    failing the whole build/query.
    """

    file_path = anchor.get("file_path")
    line_start = anchor.get("line_start")
    line_end = anchor.get("line_end")
    if not file_path or not isinstance(line_start, int) or not isinstance(line_end, int):
        return None

    try:
        lines = (source_root / file_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    start_index = max(line_start - 1, 0)
    if start_index >= len(lines):
        # ADV-1: an anchor whose declared range starts past the end of the
        # (now shorter, or relocated) source file is stale -- omit it
        # entirely rather than emit an excerpt with empty ``"text"``.
        return None
    end_index = min(line_end, len(lines))
    if end_index <= start_index:
        return None
    text = "\n".join(lines[start_index:end_index])
    return {
        "anchor_id": anchor.get("record_id"),
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "text": text,
    }


def _build_source_excerpts(records: list[dict[str, Any]], source_root: Path) -> dict[str, dict[str, Any]]:
    excerpts: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("record_type") != "anchor":
            continue
        excerpt = resolve_source_excerpt(source_root, record)
        record_id = record.get("record_id")
        if excerpt is not None and record_id:
            excerpts[record_id] = excerpt
    return dict(sorted(excerpts.items()))


def build_summary_index(records: list[dict[str, Any]], *, spine_path: str) -> dict[str, Any]:
    """Precompute the ``code_overview.summary_index.v1`` query index."""

    index = build_index(records)
    return {
        "schema_version": SUMMARY_INDEX_SCHEMA_VERSION,
        "spine_path": spine_path,
        "record_count": len(records),
        "query_index": {
            "token_index": _build_token_index(records),
            "scenario_to_path": _build_scenario_to_path(records),
            "graph_centrality": _build_graph_centrality(index),
            "file_to_record_anchors": _build_file_to_record_anchors(records),
            "trace_observation_counters": _build_trace_observation_counters(records),
            "audit_divergence_flags": _build_audit_divergence_flags(records),
        },
    }


def build_full_index(records: list[dict[str, Any]], *, spine_path: str, source_root: str) -> dict[str, Any]:
    """Precompute the ``code_overview.full_index.v1`` persisted source excerpts."""

    return {
        "schema_version": FULL_INDEX_SCHEMA_VERSION,
        "spine_path": spine_path,
        "source_root": source_root,
        "source_excerpts": _build_source_excerpts(records, Path(source_root)),
    }


def _serialize_json(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_spine_sha256(spine_path: Path) -> str:
    """Hash of the raw spine file's bytes -- the *input* fingerprint.

    Used (only) to detect staleness: whether the spine has changed since a
    ``generated/`` index was last built. Deliberately never used to validate
    ``full-index.json``'s own excerpt content -- a structurally valid
    full-index with hand-edited excerpt text must still be consumable as-is
    (see :func:`load_full_index_for_query`).
    """

    return _sha256_hex(Path(spine_path).read_bytes())


def render_persisted_artifacts(
    records: list[dict[str, Any]],
    *,
    spine_path: str,
    source_root: str,
) -> dict[str, bytes]:
    """Render the byte-stable `build` artifact set for one loaded spine.

    Returns a mapping of the three out-dir-relative artifact paths
    (``generated/summary-index.json``, ``generated/full-index.json``,
    ``generated/build-report.json``) to the exact serialized bytes the
    ``build`` subcommand must write. Everything here is derived purely from
    ``records`` (plus the two provenance strings): the same spine always
    renders the same bytes.
    """

    summary_index = build_summary_index(records, spine_path=spine_path)
    full_index = build_full_index(records, spine_path=spine_path, source_root=source_root)

    summary_bytes = _serialize_json(summary_index)
    full_bytes = _serialize_json(full_index)

    try:
        spine_sha256: str | None = compute_spine_sha256(Path(spine_path))
    except OSError:
        # Should not happen in practice -- `load_spine` already succeeded on
        # this path -- but if the spine can no longer be read at build time,
        # `spine_sha256` is recorded as `None`. `load_full_index_for_query`
        # requires a non-empty `spine_sha256` and always recomputes and
        # compares it, so a generated index with no recorded hash is
        # rejected fail-closed at query time (LOD4 exit 8).
        spine_sha256 = None

    build_report = {
        "schema_version": BUILD_REPORT_SCHEMA_VERSION,
        "status": "pass",
        "spine_path": spine_path,
        "spine_sha256": spine_sha256,
        "source_root": source_root,
        "record_count": len(records),
        "artifacts": [
            {
                "path": SUMMARY_INDEX_RELATIVE_PATH,
                "bytes": len(summary_bytes),
                "sha256": _sha256_hex(summary_bytes),
            },
            {
                "path": FULL_INDEX_RELATIVE_PATH,
                "bytes": len(full_bytes),
                "sha256": _sha256_hex(full_bytes),
            },
        ],
        "warnings": [],
    }
    report_bytes = _serialize_json(build_report)

    return {
        SUMMARY_INDEX_RELATIVE_PATH: summary_bytes,
        FULL_INDEX_RELATIVE_PATH: full_bytes,
        BUILD_REPORT_RELATIVE_PATH: report_bytes,
    }


# --------------------------------------------------------------------------
# Query-side (A/C 6): consuming the persisted `generated/` index for LOD4.
# --------------------------------------------------------------------------

_FULL_INDEX_FILENAME = Path(FULL_INDEX_RELATIVE_PATH).name
_BUILD_REPORT_FILENAME = Path(BUILD_REPORT_RELATIVE_PATH).name
_SUMMARY_INDEX_FILENAME = Path(SUMMARY_INDEX_RELATIVE_PATH).name


def resolve_generated_index_dir(project_root: Path, *, index_dir: Path | None = None) -> Path:
    """Resolve the directory LOD4 queries read the persisted index from.

    Defaults to ``<project_root>/generated`` -- exactly where `build --out
    <dir>` always writes -- so any existing project that has already been
    built needs no new flag. ``index_dir`` overrides the default when a
    caller explicitly wants to point at a different built index.
    """

    if index_dir is not None:
        return index_dir
    return project_root / "generated"


def _load_generated_json_object(path: Path) -> dict[str, Any]:
    """Read+parse one ``generated/`` artifact as a JSON object.

    Raises :class:`GeneratedIndexUnavailable` -- never a bare exception/
    traceback -- when the file is missing, unreadable, not valid JSON, or
    not a JSON object.
    """

    if not path.is_file():
        raise GeneratedIndexUnavailable(
            f"{path!s} not found -- run `cli.py build --spine <spine.jsonl> --out <dir>` before querying"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise GeneratedIndexUnavailable(f"{path!s} could not be read: {error}") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise GeneratedIndexUnavailable(f"{path!s} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise GeneratedIndexUnavailable(f"{path!s} must contain one JSON object")
    return value


def _require_schema_version(payload: dict[str, Any], *, expected: str, path: Path) -> None:
    actual = payload.get("schema_version")
    if actual != expected:
        raise GeneratedIndexUnavailable(
            f"{path!s} schema_version {actual!r} != expected {expected!r}"
        )


def _require_fresh_build_report(
    build_report: dict[str, Any], *, build_report_path: Path, spine_path: Path, index_dir: Path
) -> None:
    """Enforce the build-report's spine fingerprint (B4 C2 staleness gate).

    The recorded ``spine_sha256`` must be present as a non-empty string --
    its absence can never disable the staleness check, unlike the prior
    "only checked when present" behavior -- and the *current* spine bytes'
    sha256 is computed unconditionally on every call and compared against
    it. A mismatch (or an unreadable/missing current spine) fails closed.
    """

    spine_sha256 = build_report.get("spine_sha256")
    if not isinstance(spine_sha256, str) or not spine_sha256:
        raise GeneratedIndexUnavailable(
            f"{build_report_path!s} is missing a non-empty spine_sha256 -- "
            "cannot verify generated/ freshness at --lod 4"
        )
    if not spine_path.is_file():
        raise GeneratedIndexUnavailable(
            f"spine {spine_path!s} not found -- cannot verify generated index freshness at {index_dir!s}"
        )
    try:
        current_spine_sha256 = compute_spine_sha256(spine_path)
    except OSError as error:
        raise GeneratedIndexUnavailable(
            f"spine {spine_path!s} could not be read to verify generated index freshness: {error}"
        ) from error
    if current_spine_sha256 != spine_sha256:
        raise GeneratedIndexUnavailable(
            f"generated index at {index_dir!s} is stale: spine {spine_path!s} has changed "
            "since the last `cli.py build` run -- rebuild before querying"
        )


def _require_valid_source_excerpt_entry(
    anchor_id: str, excerpt: Any, *, full_index_path: Path
) -> dict[str, Any]:
    """Validate one ``source_excerpts`` entry's shape (B4 C2 hardening).

    Every entry must be a dict with a string ``anchor_id``, a string
    ``file_path``, an int ``line_start`` >= 1, an int ``line_end`` >=
    ``line_start``, and ``text`` present as a string. A structurally valid
    but hand-edited/"poisoned" string ``text`` is deliberately still
    accepted here -- only the *shape* is enforced, never the content.
    """

    if not isinstance(excerpt, dict):
        raise GeneratedIndexUnavailable(
            f"{full_index_path!s} source_excerpts[{anchor_id!r}] must be an object, "
            f"got {type(excerpt).__name__}"
        )

    excerpt_anchor_id = excerpt.get("anchor_id")
    file_path = excerpt.get("file_path")
    line_start = excerpt.get("line_start")
    line_end = excerpt.get("line_end")
    text = excerpt.get("text")

    if not isinstance(excerpt_anchor_id, str):
        raise GeneratedIndexUnavailable(
            f"{full_index_path!s} source_excerpts[{anchor_id!r}].anchor_id must be a string"
        )
    if not isinstance(file_path, str):
        raise GeneratedIndexUnavailable(
            f"{full_index_path!s} source_excerpts[{anchor_id!r}].file_path must be a string"
        )
    if not isinstance(line_start, int) or isinstance(line_start, bool) or line_start < 1:
        raise GeneratedIndexUnavailable(
            f"{full_index_path!s} source_excerpts[{anchor_id!r}].line_start must be an int >= 1"
        )
    if not isinstance(line_end, int) or isinstance(line_end, bool) or line_end < line_start:
        raise GeneratedIndexUnavailable(
            f"{full_index_path!s} source_excerpts[{anchor_id!r}].line_end must be an int >= line_start"
        )
    if not isinstance(text, str):
        raise GeneratedIndexUnavailable(
            f"{full_index_path!s} source_excerpts[{anchor_id!r}].text must be a string"
        )
    return excerpt


_QUERY_INDEX_REQUIRED_FIELDS: tuple[str, ...] = (
    "token_index",
    "scenario_to_path",
    "graph_centrality",
    "file_to_record_anchors",
    "trace_observation_counters",
    "audit_divergence_flags",
)


def _require_valid_query_index(query_index: Any, *, summary_index_path: Path) -> dict[str, Any]:
    """Validate ``summary-index.json``'s ``query_index`` object's shape (A/C 6).

    A schema-valid but semantically **empty** ``query_index`` (every required
    field present, but empty ``dict``/``list`` values) is valid -- it simply
    yields zero ranking candidates downstream (see
    :func:`resolve_ranking_candidate_ids`). Only the *shape* is enforced
    here, never the content, mirroring
    :func:`_require_valid_source_excerpt_entry`'s poisoned-but-valid-text
    contract for ``full-index.json``.
    """

    if not isinstance(query_index, dict):
        raise GeneratedIndexUnavailable(
            f"{summary_index_path!s} query_index must be an object, got {type(query_index).__name__}"
        )
    for field_name in _QUERY_INDEX_REQUIRED_FIELDS:
        if field_name not in query_index:
            raise GeneratedIndexUnavailable(
                f"{summary_index_path!s} query_index is missing required field {field_name!r}"
            )

    token_index = query_index["token_index"]
    if not isinstance(token_index, dict) or not all(
        isinstance(token, str) and isinstance(record_ids, list) and all(isinstance(rid, str) for rid in record_ids)
        for token, record_ids in token_index.items()
    ):
        raise GeneratedIndexUnavailable(
            f"{summary_index_path!s} query_index.token_index must be an object mapping token -> [record_id, ...]"
        )

    scenario_to_path = query_index["scenario_to_path"]
    if not isinstance(scenario_to_path, dict) or not all(
        isinstance(scenario_id, str) and isinstance(path_ids, list) and all(isinstance(pid, str) for pid in path_ids)
        for scenario_id, path_ids in scenario_to_path.items()
    ):
        raise GeneratedIndexUnavailable(
            f"{summary_index_path!s} query_index.scenario_to_path must be an object mapping scenario_id -> [path_id, ...]"
        )

    graph_centrality = query_index["graph_centrality"]
    if not isinstance(graph_centrality, dict) or not all(
        isinstance(seam_id, str) and isinstance(score, int) and not isinstance(score, bool)
        for seam_id, score in graph_centrality.items()
    ):
        raise GeneratedIndexUnavailable(
            f"{summary_index_path!s} query_index.graph_centrality must be an object mapping seam_id -> int"
        )

    file_to_record_anchors = query_index["file_to_record_anchors"]
    if not isinstance(file_to_record_anchors, dict) or not all(
        isinstance(file_path, str) and isinstance(anchor_ids, list) and all(isinstance(aid, str) for aid in anchor_ids)
        for file_path, anchor_ids in file_to_record_anchors.items()
    ):
        raise GeneratedIndexUnavailable(
            f"{summary_index_path!s} query_index.file_to_record_anchors must be an object mapping "
            "file_path -> [anchor_id, ...]"
        )

    trace_observation_counters = query_index["trace_observation_counters"]
    if not isinstance(trace_observation_counters, dict) or not all(
        isinstance(seam_id, str) and isinstance(counters, dict)
        for seam_id, counters in trace_observation_counters.items()
    ):
        raise GeneratedIndexUnavailable(
            f"{summary_index_path!s} query_index.trace_observation_counters must be an object mapping "
            "seam_id -> {{...}}"
        )

    audit_divergence_flags = query_index["audit_divergence_flags"]
    if not isinstance(audit_divergence_flags, list) or not all(
        isinstance(flag, dict) for flag in audit_divergence_flags
    ):
        raise GeneratedIndexUnavailable(
            f"{summary_index_path!s} query_index.audit_divergence_flags must be a list of objects"
        )

    return {
        "token_index": token_index,
        "scenario_to_path": scenario_to_path,
        "graph_centrality": graph_centrality,
        "file_to_record_anchors": file_to_record_anchors,
        "trace_observation_counters": trace_observation_counters,
        "audit_divergence_flags": audit_divergence_flags,
    }


def resolve_ranking_candidate_ids(query_index: dict[str, Any]) -> set[str]:
    """Derive the set of ``path`` record ids the precomputed query index knows about.

    A/C 6: ranking/filtering (:func:`query.index.rank_paths`) must be driven
    by ``generated/summary-index.json``'s ``query_index`` when a precomputed
    index is present, instead of live-recomputing over every ``path`` record
    in the loaded spine. This reconstructs the candidate set the ranker is
    allowed to consider from the two precomputed structures that reference
    path ids: ``token_index`` (an inverted token index whose values include
    every record -- including ``path`` records -- that contributed a token,
    see ``build.py``'s ``_build_token_index``) and ``scenario_to_path`` (a
    direct scenario -> ``[path_id, ...]`` join). Ids from other record types
    that also land in ``token_index`` (seam/channel/ui_surface/reason_code)
    are harmless here -- :func:`query.index.rank_paths` only ever checks
    candidacy against actual ``path`` record ids, so a stray non-path id in
    this set never becomes a spurious ranked path.

    An empty (but structurally valid) ``query_index`` -- every field present
    with no entries -- yields an empty set, which in turn makes
    :func:`query.index.rank_paths` return no ranked paths at all: this is
    the precomputed-index-driven behavior the A/C 6 poisoned-index RED test
    pins, distinguishing "ranking really consumed the precomputed index"
    from "ranking silently recomputed from the spine and ignored it."
    """

    candidate_ids: set[str] = set()
    for record_ids in query_index["token_index"].values():
        candidate_ids.update(record_ids)
    for path_ids in query_index["scenario_to_path"].values():
        candidate_ids.update(path_ids)
    return candidate_ids


def load_full_index_for_query(index_dir: Path, *, spine_path: Path) -> dict[str, Any]:
    """Load+verify the persisted ``generated/`` triad for a query.

    Returns a dict with two keys:

    - ``"source_excerpts"``: the validated ``full-index.json``
      ``source_excerpts{}`` mapping (anchor_id -> excerpt), consumed only at
      ``--lod 4``;
    - ``"query_index"``: the validated ``summary-index.json`` ``query_index``
      object (A/C 6), consumed by :func:`resolve_ranking_candidate_ids` to
      drive ranking/filtering at every LOD when a ``generated/`` index is
      present.

    Fails closed with :class:`GeneratedIndexUnavailable` -- never a bare
    exception/traceback -- when any part of the ``generated/`` triad cannot
    be verified:

    - ``index_dir`` does not exist (no ``build`` has ever been run there);
    - ``build-report.json``, ``full-index.json``, or ``summary-index.json``
      is missing, unreadable, not valid JSON, not a JSON object, or has the
      wrong/missing ``schema_version`` (the triad is required+validated as
      one atomic unit);
    - ``build-report.json`` is missing a non-empty string ``spine_sha256``
      (absence can never disable the staleness check);
    - the current spine's sha256 (always computed, never skipped) does not
      match the recorded ``spine_sha256`` (the generated index is stale);
    - ``full-index.json`` is missing its ``source_excerpts`` object, or any
      entry in it is not a dict with valid ``anchor_id``/``file_path``/
      ``line_start``/``line_end``/``text`` fields;
    - ``summary-index.json``'s ``query_index`` object is missing any
      required field or any field has the wrong structural shape (see
      :func:`_require_valid_query_index`).

    Deliberately does **not** hash or otherwise validate ``full-index.json``
    excerpt *content*, or ``summary-index.json`` ``query_index`` *content*,
    against anything -- a structurally valid but hand-edited/poisoned
    excerpt text, or an empty-but-valid ``query_index``, must still be
    consumed as-is. Only the *spine* fingerprint and each field's *shape*
    are enforced.
    """

    if not index_dir.is_dir():
        raise GeneratedIndexUnavailable(
            f"generated index directory not found at {index_dir!s} -- "
            "run `cli.py build --spine <spine.jsonl> --out <dir>` before querying"
        )

    full_index_path = index_dir / _FULL_INDEX_FILENAME
    build_report_path = index_dir / _BUILD_REPORT_FILENAME
    summary_index_path = index_dir / _SUMMARY_INDEX_FILENAME

    build_report = _load_generated_json_object(build_report_path)
    _require_schema_version(build_report, expected=BUILD_REPORT_SCHEMA_VERSION, path=build_report_path)

    full_index = _load_generated_json_object(full_index_path)
    _require_schema_version(full_index, expected=FULL_INDEX_SCHEMA_VERSION, path=full_index_path)

    summary_index = _load_generated_json_object(summary_index_path)
    _require_schema_version(summary_index, expected=SUMMARY_INDEX_SCHEMA_VERSION, path=summary_index_path)

    # Staleness can never be disabled by absence: a non-empty spine_sha256 is
    # required, and it is always compared against a freshly computed hash.
    _require_fresh_build_report(
        build_report, build_report_path=build_report_path, spine_path=spine_path, index_dir=index_dir
    )

    source_excerpts = full_index.get("source_excerpts")
    if not isinstance(source_excerpts, dict):
        raise GeneratedIndexUnavailable(f"{full_index_path!s} is missing a source_excerpts{{}} object")

    validated_source_excerpts = {
        anchor_id: _require_valid_source_excerpt_entry(anchor_id, excerpt, full_index_path=full_index_path)
        for anchor_id, excerpt in source_excerpts.items()
    }

    validated_query_index = _require_valid_query_index(
        summary_index.get("query_index"), summary_index_path=summary_index_path
    )

    return {
        "source_excerpts": validated_source_excerpts,
        "query_index": validated_query_index,
    }
