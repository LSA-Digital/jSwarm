"""`legacy_paths_frozen_no_new_violations` audit predicate (S3 #9).

Unlike `no_unconsumed_channels` (which scopes to declared-only channels),
this predicate looks at *every* channel regardless of ``record_status`` --
including legacy/frozen ones -- and reports every channel with no
consuming seam as a raw violation. The registry's uniform freeze-partition
mechanism (`registry._partition_by_freeze`) then tolerates any raw
violation whose record is covered by a `freeze_entry`; only a genuinely
*new*, unfrozen violation fails this audit. This is what lets a known,
already-frozen legacy channel stay green while a brand-new unconsumed
channel is caught immediately.

Layer A generic tooling: reads only the generic ``channel`` record shape
and carries no project-specific vocabulary. Freeze-ledger interpretation
lives centrally in `registry.py`, not here.
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
    channels = [record for record in records if record.get("record_type") == "channel"]

    violations: list[dict[str, Any]] = []
    for channel in channels:
        if channel.get("consumer_seam_ids"):
            continue
        channel_id = channel["record_id"]
        violations.append(
            {
                "record_id": channel_id,
                "record_type": "channel",
                "violation_kind": "unconsumed_channel_path",
                "message": (
                    f"channel {channel_id!r} has no consuming seam; new unfrozen violations of "
                    "this kind are not tolerated."
                ),
            }
        )
    return violations
