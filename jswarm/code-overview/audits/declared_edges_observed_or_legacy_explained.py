"""`declared_edges_observed_or_legacy_explained` audit predicate (S3 #6).

Every active, declared ``produces`` edge that is marked ``trace_required``
must be backed by at least one accepted observation -- at EDGE level, not
seam level. A ``trace_observation_summary`` that declares
``observed_edge_ids`` is precise: it names exactly which declared edges it
observed, and an edge from the same seam that is absent from that set does
NOT count as observed, even though some other edge from that seam was
seen. Only when none of a seam's summaries declare ``observed_edge_ids``
(the older, coarser shape) does this predicate fall back to the legacy
seam-level signal (``observed_count > 0``) -- and even then, only when the
seam has exactly one trace-required edge in scope. Once a seam declares
more than one trace-required edge, a coarse count that cannot say which
edge(s) it covers must not bless any of them: two required edges with
``observed_count == 1`` and no ``observed_edge_ids`` leaves both edges
provably unproven, not one arbitrarily chosen winner. This keeps the
legacy fallback scoped to truly single-edge/no-edge-level legacy spines,
exactly as it was meant to be, instead of being exploitable as a blanket
per-seam pass for every sibling edge. Edges that are not marked
``trace_required`` are out of this predicate's scope entirely (they are
not claimed to be traceable in the first place); consuming/projecting/
emitting edges are covered by the reverse mapping in
`observed_edges_declared`.

Layer A generic tooling: reads only the generic ``edge`` and
``trace_observation_summary`` record shapes and carries no
project-specific vocabulary.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["check"]


def _edge_is_observed(
    edge_id: str,
    summaries: list[dict[str, Any]],
    sibling_edge_count: int,
) -> bool:
    precise_summaries = [summary for summary in summaries if "observed_edge_ids" in summary]
    if precise_summaries:
        observed_edge_ids: set[str] = set()
        for summary in precise_summaries:
            observed_edge_ids.update(summary.get("observed_edge_ids") or [])
        return edge_id in observed_edge_ids
    if sibling_edge_count > 1:
        # B2-2: a coarse seam-level observed_count cannot say which of two
        # (or more) trace-required sibling edges it covers, so it must not
        # bless any of them. Only a truly single-edge seam may fall back to
        # the coarser signal below.
        return False
    # Legacy fallback: this seam has exactly one trace-required edge and no
    # summary for it carries edge-level linkage, so trust the coarser
    # seam-level observed_count signal -- unambiguous in this case since
    # there is only one edge it could possibly refer to.
    return any((summary.get("observed_count") or 0) > 0 for summary in summaries)


def check(
    records: Sequence[dict[str, Any]],
    *,
    source_root: Any = None,
    strict: bool = True,
) -> list[dict[str, Any]]:
    edges = [
        record
        for record in records
        if record.get("record_type") == "edge"
        and record.get("record_status") == "declared"
        and record.get("edge_kind") == "produces"
        and record.get("trace_required")
    ]

    summaries_by_seam: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("record_type") != "trace_observation_summary":
            continue
        summaries_by_seam.setdefault(record.get("seam_id"), []).append(record)

    # D-3 set operation: how many trace-required edges (in this predicate's
    # scope) share a given seam_id -- used to decide whether the coarse
    # seam-level fallback in `_edge_is_observed` is even eligible to apply.
    edge_count_by_seam: dict[str, int] = {}
    for edge in edges:
        seam_id = edge.get("from_record_id")
        edge_count_by_seam[seam_id] = edge_count_by_seam.get(seam_id, 0) + 1

    violations: list[dict[str, Any]] = []
    for edge in edges:
        seam_id = edge.get("from_record_id")
        edge_id = edge["record_id"]
        summaries = summaries_by_seam.get(seam_id, [])
        sibling_edge_count = edge_count_by_seam.get(seam_id, 0)
        if _edge_is_observed(edge_id, summaries, sibling_edge_count):
            continue
        violations.append(
            {
                "record_id": edge_id,
                "record_type": "edge",
                "violation_kind": "edge_not_observed",
                "message": (
                    f"edge {edge_id!r} from seam {seam_id!r} is trace_required but has no "
                    "accepted observed trace and no legacy/freeze explanation."
                ),
            }
        )
    return violations
