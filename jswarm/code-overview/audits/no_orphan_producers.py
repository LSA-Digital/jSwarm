"""`no_orphan_producers` audit predicate (S3 #1).

Every active producer seam (one that declares at least one
``producer_anchor_ids`` entry) must have at least one ACTIVE, ``required``
outgoing edge to a valid channel or consumer -- otherwise the seam produces
data that never reaches a channel or consumer. An optional-only outgoing
edge (``required: false``) does not satisfy this predicate: #296 S3.1
requires a *required* edge, not merely the presence of any edge record.
Two escape hatches keep this from over-firing on legitimately inert seams:

* a legacy/frozen seam (``record_status == "legacy_frozen"``) is out of
  scope entirely; and
* a seam whose every declared carrier-matrix cell is ``n_a`` has nothing
  left to require an outgoing edge for.

Layer A generic tooling: this module reads only the generic seam-spine
record shapes (``seam``, ``edge``, ``carrier_matrix_cell``) and carries no
project-specific vocabulary.
"""

from __future__ import annotations

from typing import Any, Sequence

from audits._active import is_active_edge

__all__ = ["check"]


def check(
    records: Sequence[dict[str, Any]],
    *,
    source_root: Any = None,
    strict: bool = True,
) -> list[dict[str, Any]]:
    seams = [record for record in records if record.get("record_type") == "seam"]
    edges = [record for record in records if record.get("record_type") == "edge"]
    known_record_ids = {record["record_id"] for record in records if record.get("record_id")}
    cells_by_id = {
        record["record_id"]: record
        for record in records
        if record.get("record_type") == "carrier_matrix_cell"
    }

    # D-3 set operation: the set of seam_ids that have at least one ACTIVE,
    # required==true outgoing edge whose target resolves to a real record
    # (a channel or consumer) declared elsewhere in the spine. Optional-only
    # edges and retired edges are excluded from this set entirely -- a seam
    # with only such edges lands in the violation branch below, exactly as
    # an orphan producer with zero edges would.
    seams_with_required_outgoing_edge = {
        edge.get("from_record_id")
        for edge in edges
        if is_active_edge(edge)
        and edge.get("required") is True
        and edge.get("to_record_id") in known_record_ids
    }

    violations: list[dict[str, Any]] = []
    for seam in seams:
        if seam.get("lifecycle_state", "active") != "active":
            continue
        if seam.get("record_status") == "legacy_frozen":
            continue
        if not seam.get("producer_anchor_ids"):
            continue  # not a producer seam; out of scope for this predicate

        cell_ids = seam.get("carrier_cell_ids") or []
        cell_states = [
            cells_by_id[cell_id].get("state")
            for cell_id in cell_ids
            if cell_id in cells_by_id
        ]
        if cell_states and all(state == "n_a" for state in cell_states):
            continue  # nothing required of this seam; every carrier is n/a

        seam_id = seam["record_id"]
        if seam_id in seams_with_required_outgoing_edge:
            continue

        violations.append(
            {
                "record_id": seam_id,
                "record_type": "seam",
                "violation_kind": "orphan_producer",
                "message": (
                    f"seam {seam_id!r} declares producer_anchor_ids but has no active, "
                    "required outgoing edge to a channel or consumer."
                ),
            }
        )
    return violations
