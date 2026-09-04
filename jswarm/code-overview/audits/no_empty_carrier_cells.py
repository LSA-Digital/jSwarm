"""`no_empty_carrier_cells` audit predicate (S3 #4).

Every ACTIVE seam -- per #296 S3.4, this is not scoped to producer seams
only -- must carry a `carrier_matrix_cell` record for every
project-required carrier named in ``spine_metadata.required_carriers``. A
carrier cell can legitimately be classified ``n_a``, ``legacy``, etc. --
what it cannot be is *missing entirely*, since a missing cell hides
whether the carrier was ever considered at all. This applies equally to
consumer, projecting, and any other active seam role: the carrier matrix
must account for every active seam, not just producers.

Layer A generic tooling: ``required_carriers`` is read from
``spine_metadata`` at runtime, never hard-coded, so this predicate carries
no project-specific carrier vocabulary of its own.
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = ["check"]


def check(
    records: Sequence[dict[str, Any]],
    *,
    source_root: Any = None,
    strict: bool = True,
) -> list[dict[str, Any]]:
    metadata = next(
        (record for record in records if record.get("record_type") == "spine_metadata"),
        {},
    )
    required_carriers: list[str] = metadata.get("required_carriers") or []
    if not required_carriers:
        return []  # nothing declared as required; vacuously satisfied

    seams = [
        record
        for record in records
        if record.get("record_type") == "seam"
        and record.get("lifecycle_state", "active") == "active"
        and record.get("record_status") != "legacy_frozen"
    ]
    cells_by_id = {
        record["record_id"]: record
        for record in records
        if record.get("record_type") == "carrier_matrix_cell"
    }

    violations: list[dict[str, Any]] = []
    for seam in seams:
        seam_id = seam["record_id"]
        cell_ids = seam.get("carrier_cell_ids") or []
        carriers_present = {
            cells_by_id[cell_id].get("carrier") for cell_id in cell_ids if cell_id in cells_by_id
        }
        for carrier in required_carriers:
            if carrier in carriers_present:
                continue
            violations.append(
                {
                    "record_id": seam_id,
                    "record_type": "seam",
                    "violation_kind": "empty_carrier_cell",
                    "message": (
                        f"seam {seam_id!r} is missing a carrier_matrix_cell for required "
                        f"carrier {carrier!r}."
                    ),
                }
            )
    return violations
