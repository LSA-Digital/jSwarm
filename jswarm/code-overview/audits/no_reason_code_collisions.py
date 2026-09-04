"""`no_reason_code_collisions` audit predicate (S3 #3).

No two active ``reason_code`` records may share the same ``(seam_id,
code)`` pair while disagreeing on ``meaning``, ``retryable``, or
``terminal`` -- that combination is meant to identify one unambiguous
failure/outcome signature per seam, and a same-code-different-meaning
collision means callers can no longer trust what the code means.

Layer A generic tooling: reads only the generic ``reason_code`` record
shape and carries no project-specific vocabulary.
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
    reason_codes = [
        record
        for record in records
        if record.get("record_type") == "reason_code" and record.get("record_status") == "declared"
    ]

    groups: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for reason_code in reason_codes:
        key = (reason_code.get("seam_id"), reason_code.get("code"))
        groups.setdefault(key, []).append(reason_code)

    violations: list[dict[str, Any]] = []
    for (seam_id, code), group in groups.items():
        if len(group) < 2:
            continue
        signatures = {
            (record.get("meaning"), record.get("retryable"), record.get("terminal")) for record in group
        }
        if len(signatures) <= 1:
            continue  # duplicate records but they agree; not a collision
        for record in group:
            violations.append(
                {
                    "record_id": record["record_id"],
                    "record_type": "reason_code",
                    "violation_kind": "reason_code_collision",
                    "message": (
                        f"reason_code {record['record_id']!r} collides with another active "
                        f"reason_code sharing seam {seam_id!r} code {code!r}: meaning/retryable/"
                        "terminal disagree between the records."
                    ),
                }
            )
    return violations
