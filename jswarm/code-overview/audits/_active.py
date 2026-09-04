"""Centralized active-record/active-edge predicate (COM-234 Phase 4, jCritic B2-1).

Every edge/record audit that needs to know whether a record is currently
"in force" must call `is_active_record` (or its `is_active_edge` alias) --
never re-derive an ad-hoc, local, retired-only check. jCritic cycle-2 found
three separate audits (`no_orphan_producers`, `no_unconsumed_channels`,
`observed_edges_declared`) each carrying their own narrower
`record_status == "retired"` / `lifecycle_state == "retired"` variant that
missed `lifecycle_state == "inactive"` and `superseded_by` entirely, so an
inactive or superseded edge still satisfied a producer/consumer/trace
predicate. Centralizing the definition once here closes that hole for good
and prevents it from recurring in the next audit that needs the same
concept.

Definition (S1.3 record lifecycle fields), authoritative and exact:

    active = record_status is a current/declared status
             AND (lifecycle_state is absent OR lifecycle_state == "active")
             AND superseded_by is empty/absent

`record_status` values observed across the seam-spine schema are
`"declared"` (current), `"retired"`, and `"legacy_frozen"` (both
non-current); only `"declared"` (or an absent field, defensively) counts as
current here -- any other value is treated as not-current rather than
enumerating every possible non-current string.

Layer A generic tooling: this module reads only the generic record-lifecycle
fields shared by every seam-spine record shape and carries no
project-specific vocabulary.
"""

from __future__ import annotations

from typing import Any

__all__ = ["is_active_record", "is_active_edge"]

_CURRENT_RECORD_STATUSES = frozenset({"declared", None})


def is_active_record(record: dict[str, Any]) -> bool:
    """True iff `record` is active per the S1.3 lifecycle contract.

    active = current record_status AND lifecycle_state absent-or-"active"
    AND no superseded_by. All three conditions are checked independently --
    an inactive or superseded record is never rescued by a merely-current
    record_status, and vice versa.
    """

    if record.get("record_status") not in _CURRENT_RECORD_STATUSES:
        return False
    if record.get("lifecycle_state", "active") != "active":
        return False
    if record.get("superseded_by"):
        return False
    return True


# Every edge audit imports this predicate as `is_active_edge` -- same
# function, clearer name at edge-record call sites.
is_active_edge = is_active_record
