"""`no_unconsumed_channels` audit predicate (S3 #2).

Every active (``record_status == "declared"``) channel must have at least
one ACTIVE, ``required`` outgoing ``consumes`` edge (``from_record_id`` ==
the channel, ``edge_kind == "consumes"``) to a consumer seam. The
denormalized ``channel.consumer_seam_ids`` list is NOT trusted as proof of
consumption -- it can be populated while the corresponding edge is
missing, retired, or optional-only, so this predicate is derived strictly
from the ACTIVE consuming-edge set over the edge graph. A legacy/frozen
channel (``record_status == "legacy_frozen"``) is out of scope for this
predicate -- that case is instead the concern of
`legacy_paths_frozen_no_new_violations`, which audits every channel
regardless of status and tolerates known-frozen signatures via the freeze
ledger.

Layer A generic tooling: reads only the generic ``channel``/``edge``
record shapes and carries no project-specific vocabulary.
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
    channels = [
        record
        for record in records
        if record.get("record_type") == "channel" and record.get("record_status") == "declared"
    ]
    edges = [record for record in records if record.get("record_type") == "edge"]

    # D-3 set operation: derive the set of channel_ids that have at least
    # one ACTIVE, required==true outgoing "consumes" edge, from the edge
    # graph directly -- never from the denormalized consumer_seam_ids list.
    channels_with_active_consuming_edge = {
        edge.get("from_record_id")
        for edge in edges
        if edge.get("edge_kind") == "consumes"
        and edge.get("required") is True
        and is_active_edge(edge)
    }

    violations: list[dict[str, Any]] = []
    for channel in channels:
        channel_id = channel["record_id"]
        if channel_id in channels_with_active_consuming_edge:
            continue
        violations.append(
            {
                "record_id": channel_id,
                "record_type": "channel",
                "violation_kind": "unconsumed_channel",
                "message": (
                    f"channel {channel_id!r} has no active consuming edge to a "
                    "consumer seam."
                ),
            }
        )
    return violations
