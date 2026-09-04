"""Generic (Layer A) in-memory spine index and guided-pathway reconstruction.

This module builds a small in-memory index over a loaded seam-spine (a list
of typed JSONL record dicts, see ``schema``) and reconstructs the guided
"pathway" a ``path`` record traces through the graph, purely by walking
generic record-type fields (``path.step_record_ids``, ``path_step.seam_id``,
``seam.channel_ids``/``reason_code_ids``, ``edge.from_record_id``/
``to_record_id``/``anchor_ids``). No record ID, carrier name, or other
project vocabulary is hard-coded anywhere in this module: the same code must
work over any spine that conforms to ``schema/spine-record.schema.json``,
not only the pilot fixture.

Edge ordering matters: edges are visited in spine file-declaration order (the
order ``schema.load_spine`` already preserves), so the reconstructed pathway
follows the order the spine's author declared, without needing any
additional ordering field on ``edge`` records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SpineIndex",
    "build_full_pathway_records",
    "build_index",
    "build_ranked_path",
    "rank_paths",
]


@dataclass
class SpineIndex:
    """A small, generic, read-only index over one loaded spine's records."""

    records: list[dict[str, Any]]
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Alias of `by_id` (same population, same values) kept under this more
    # explicit name for external callers (e.g. `aggregate` output
    # verification) that want a self-describing attribute rather than the
    # terser `by_id`.
    records_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_type: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    edges_from: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def build_index(records: list[dict[str, Any]]) -> SpineIndex:
    """Build a :class:`SpineIndex` over ``records``, preserving file order."""

    index = SpineIndex(records=records)
    for record in records:
        record_type = record.get("record_type")
        record_id = record.get("record_id")
        if record_type:
            index.by_type.setdefault(record_type, []).append(record)
        if record_id:
            index.by_id[record_id] = record
            index.records_by_id[record_id] = record
        if record_type == "edge":
            from_id = record.get("from_record_id")
            if from_id:
                index.edges_from.setdefault(from_id, []).append(record)
    return index


def build_ranked_path(index: SpineIndex, path_record: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the guided pathway for one ``path`` record.

    Walks the path's ordered ``path_step`` records; for each step's seam,
    walks every ``edge`` declared ``from_record_id == seam_id`` (in spine file
    order) to collect anchors, then does the same for every channel the seam
    (or step) declares, collecting further anchors and any ``ui_surface`` an
    edge terminates on. Anchor order and de-duplication are both derived
    purely from edge declaration order -- nothing here is specific to any one
    project's pathway.
    """

    seam_ids: list[str] = []
    channel_ids: list[str] = []
    ui_surface_ids: list[str] = []
    reason_code_ids: list[str] = []
    anchor_ids_in_order: list[str] = []

    step_ids = path_record.get("step_record_ids") or []
    steps = [index.by_id[step_id] for step_id in step_ids if step_id in index.by_id]
    steps.sort(key=lambda step: step.get("order", 0))

    for step in steps:
        seam_id = step.get("seam_id")
        seam = index.by_id.get(seam_id) if seam_id else None
        if seam_id and seam_id not in seam_ids:
            seam_ids.append(seam_id)

        step_channel_id = step.get("channel_id")
        seam_channel_ids = list((seam or {}).get("channel_ids") or [])
        ordered_channel_ids = [cid for cid in [step_channel_id] if cid]
        ordered_channel_ids.extend(cid for cid in seam_channel_ids if cid not in ordered_channel_ids)

        if seam_id:
            for edge in index.edges_from.get(seam_id, []):
                for anchor_id in edge.get("anchor_ids") or []:
                    if anchor_id not in anchor_ids_in_order:
                        anchor_ids_in_order.append(anchor_id)

        for channel_id in ordered_channel_ids:
            if channel_id not in channel_ids:
                channel_ids.append(channel_id)
            for edge in index.edges_from.get(channel_id, []):
                for anchor_id in edge.get("anchor_ids") or []:
                    if anchor_id not in anchor_ids_in_order:
                        anchor_ids_in_order.append(anchor_id)
                to_id = edge.get("to_record_id")
                to_record = index.by_id.get(to_id) if to_id else None
                if (
                    to_record is not None
                    and to_record.get("record_type") == "ui_surface"
                    and to_id not in ui_surface_ids
                ):
                    ui_surface_ids.append(to_id)

        if seam:
            for reason_code_id in seam.get("reason_code_ids") or []:
                if reason_code_id not in reason_code_ids:
                    reason_code_ids.append(reason_code_id)

    pathway = []
    for anchor_id in anchor_ids_in_order:
        anchor = index.by_id.get(anchor_id, {})
        pathway.append({"record_id": anchor_id, "label": anchor.get("symbol_name") or anchor_id})

    return {
        "path_id": path_record.get("record_id"),
        "seam_ids": seam_ids,
        "channel_ids": channel_ids,
        "ui_surface_ids": ui_surface_ids,
        "reason_code_ids": reason_code_ids,
        "pathway": pathway,
    }


_FULL_PATHWAY_RECORD_PROJECTIONS: dict[str, tuple[str, ...]] = {
    "seam": ("record_id", "record_type", "name"),
    "channel": ("record_id", "record_type", "name"),
    "ui_surface": ("record_id", "record_type", "surface_name"),
    "reason_code": ("record_id", "record_type", "code"),
}
_DEFAULT_FULL_PATHWAY_RECORD_PROJECTION = ("record_id", "record_type", "name")


def _project_full_pathway_record(record: dict[str, Any]) -> dict[str, Any]:
    """Trim one expanded record down to its identifying/summary fields.

    LOD4's ``full_pathway_records[]`` is meant to let an agent see *which*
    seam/channel/ui_surface/reason_code records back a pathway, not to
    re-serialize every administrative schema field (tags, legacy ids,
    lifecycle timestamps, ...) a second time -- that bulk would blow the
    NFR-234-002 token budget for no reader benefit.
    """

    fields = _FULL_PATHWAY_RECORD_PROJECTIONS.get(
        record.get("record_type"), _DEFAULT_FULL_PATHWAY_RECORD_PROJECTION
    )
    return {field_name: record[field_name] for field_name in fields if field_name in record}


def build_full_pathway_records(index: SpineIndex, ranked_path: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a ranked pathway's declared seam/channel/ui/reason ids into their full records.

    LOD4 exposes this so an agent can inspect the seam/channel/ui_surface/
    reason_code records behind a pathway, not just their bare ids --
    trimmed to each record type's identifying/summary fields (see
    :func:`_project_full_pathway_record`). The ordered pathway *anchors*
    are deliberately not repeated here: they are already fully identified
    by ``pathway[]`` (record_id + label) and, at LOD4, by the sibling
    ``source_excerpts[]`` entry for the same anchor_id -- repeating them a
    third time here would only spend token budget on a duplicate. Order is
    the same declaration order :func:`build_ranked_path` already produced:
    seams, then channels, then ui_surfaces, then reason_codes.
    """

    record_ids: list[str] = []
    for key in ("seam_ids", "channel_ids", "ui_surface_ids", "reason_code_ids"):
        for record_id in ranked_path.get(key) or []:
            if record_id not in record_ids:
                record_ids.append(record_id)

    return [
        _project_full_pathway_record(index.by_id[record_id])
        for record_id in record_ids
        if record_id in index.by_id
    ]


_WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _WORD_PATTERN.findall(text)}


def _searchable_text(index: SpineIndex, path_record: dict[str, Any]) -> str:
    """Build a generic free-text corpus for one path from schema fields only."""

    parts: list[str] = [str(path_record.get("name") or "")]
    parts.extend(str(item) for item in (path_record.get("preconditions") or []))
    parts.extend(str(item) for item in (path_record.get("postconditions") or []))

    seen_seam_ids: set[str] = set()
    for step_id in path_record.get("step_record_ids") or []:
        step = index.by_id.get(step_id)
        if not step:
            continue
        seam_id = step.get("seam_id")
        if not seam_id or seam_id in seen_seam_ids:
            continue
        seen_seam_ids.add(seam_id)
        seam = index.by_id.get(seam_id)
        if not seam:
            continue
        parts.append(str(seam.get("name") or ""))
        parts.append(str(seam.get("notes") or ""))
        parts.append(str(seam.get("subsystem") or ""))
        parts.append(str(seam.get("capability") or ""))
        for reason_code_id in seam.get("reason_code_ids") or []:
            reason_code = index.by_id.get(reason_code_id)
            if reason_code:
                parts.append(str(reason_code.get("meaning") or ""))

    return " ".join(part for part in parts if part)


def _associated_record_ids(index: SpineIndex, path_record: dict[str, Any]) -> set[str]:
    """Structural record ids associated with one ``path`` (A/C 6 scoring).

    Mirrors the exact path -> step -> seam -> reason_code graph walk
    :func:`_searchable_text` uses to assemble its free-text corpus, but
    returns record ids instead of text: the path's own ``record_id``, every
    distinct ``seam_id`` its steps reference (in step order, de-duplicated),
    and every ``reason_code_id`` those seams declare. This walk is purely
    structural (``path_step.seam_id`` / ``seam.reason_code_ids`` schema
    fields) -- never poisoned by a precomputed index -- and is only ever
    used to decide *which record ids* a query token's precomputed
    ``token_index`` membership should be checked against (see
    :func:`_score_from_token_index`), not to read any record's text.
    """

    record_id = path_record.get("record_id")
    associated_ids: set[str] = {record_id} if record_id else set()

    seen_seam_ids: set[str] = set()
    for step_id in path_record.get("step_record_ids") or []:
        step = index.by_id.get(step_id)
        if not step:
            continue
        seam_id = step.get("seam_id")
        if not seam_id or seam_id in seen_seam_ids:
            continue
        seen_seam_ids.add(seam_id)
        associated_ids.add(seam_id)
        seam = index.by_id.get(seam_id)
        if not seam:
            continue
        for reason_code_id in seam.get("reason_code_ids") or []:
            associated_ids.add(reason_code_id)
    return associated_ids


def _score_from_token_index(
    index: SpineIndex,
    path_record: dict[str, Any],
    query_tokens: set[str],
    token_index: dict[str, list[str]],
) -> int:
    """Score one ``path`` from the precomputed ``token_index`` (A/C 6).

    For an unpoisoned, freshly built ``token_index`` this is numerically
    equivalent to ``len(query_tokens & corpus_tokens)`` from
    :func:`_searchable_text`: ``token_index`` (see
    ``query.build._build_token_index``) is built from the same per-record
    text fields, tokenized with the same word pattern, for the same record
    types (path/seam/channel/ui_surface/reason_code). A query token counts
    toward the score once whenever any record id :func:`_associated_record_ids`
    considers part of this path (the path's own id, its linked seam ids, or
    those seams' reason-code ids) appears in ``token_index[token]`` --
    reading token membership from the persisted index rather than
    re-tokenizing spine text is what lets a poisoned ``token_index`` change
    the score/order (A/C 6 reorder contract), while an unpoisoned one stays
    equivalent to the original spine score.
    """

    if not query_tokens:
        return 0
    associated_ids = _associated_record_ids(index, path_record)
    score = 0
    for token in query_tokens:
        candidate_ids = token_index.get(token)
        if candidate_ids and associated_ids.intersection(candidate_ids):
            score += 1
    return score


def rank_paths(
    index: SpineIndex,
    query_text: str,
    *,
    scenario_id: str | None = None,
    carrier: str | None = None,
    allowed_path_ids: set[str] | None = None,
    token_index: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Rank every ``path`` record in ``index`` against ``query_text``.

    When ``token_index`` is ``None`` (no ``generated/`` index available),
    score is the size of the intersection between the query's lower-cased
    word tokens and each path's generic searchable-text tokens (name,
    pre/postconditions, linked seam name/notes/subsystem/capability, linked
    reason-code meanings) -- the pre-A/C-6 in-memory back-compat behavior.
    When ``token_index`` is provided (A/C 6: a precomputed
    ``generated/summary-index.json`` query index was found), score/order are
    instead driven entirely by that persisted index via
    :func:`_score_from_token_index`, never by re-tokenizing spine text. Ties
    break by original spine declaration order in both cases, so a spine with
    a single path is always returned deterministically even for a
    zero-overlap query, and a freshly-built (unpoisoned) ``token_index``
    reproduces the exact same order as the no-generated-index fallback.

    ``scenario_id``/``carrier`` are optional generic narrowing filters applied
    before ranking: a ``path`` record is only a candidate if its own
    ``scenario_ids``/``carrier_scope`` schema fields contain the requested
    value. Both are plain schema-field lookups; neither hard-codes any
    project-specific scenario or carrier value.

    ``allowed_path_ids`` (A/C 6) is an additional optional candidate gate: when
    provided (non-``None``), a ``path`` record is only a candidate if its
    ``record_id`` is a member. Callers pass the ids reconstructed from a
    precomputed ``generated/summary-index.json`` query index (see
    ``query.build.resolve_ranking_candidate_ids``) so that ranking/filtering
    is driven by the persisted index rather than every ``path`` record the
    in-memory spine happens to contain. ``None`` (the default) means no
    generated index was available/consulted -- every ``path`` record in
    ``index`` stays a candidate, preserving the pre-A/C-6 in-memory
    back-compat behavior.
    """

    query_tokens = _tokenize(query_text)
    paths = index.by_type.get("path", [])
    if allowed_path_ids is not None:
        paths = [record for record in paths if record.get("record_id") in allowed_path_ids]
    if scenario_id:
        paths = [record for record in paths if scenario_id in (record.get("scenario_ids") or [])]
    if carrier:
        paths = [record for record in paths if carrier in (record.get("carrier_scope") or [])]

    scored: list[tuple[int, int, dict[str, Any]]] = []
    for position, path_record in enumerate(paths):
        if token_index is not None:
            score = _score_from_token_index(index, path_record, query_tokens, token_index)
        else:
            corpus_tokens = _tokenize(_searchable_text(index, path_record))
            score = len(query_tokens & corpus_tokens) if query_tokens else 0
        scored.append((score, position, path_record))

    scored.sort(key=lambda item: (-item[0], item[1]))

    ranked: list[dict[str, Any]] = []
    for score, _position, path_record in scored:
        ranked_path = build_ranked_path(index, path_record)
        ranked_path["score"] = score
        ranked.append(ranked_path)
    return ranked
