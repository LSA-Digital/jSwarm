"""`observed_edges_declared` audit predicate (S3 #7).

Every observed ``trace_event`` must map to an active declared edge:

* ``resolution_status == "unmapped"`` is tri-state (COM-234 fold 3 --
  see `_classify_unmapped`): FAILS for a *covered* event source unless a
  ``learning-registry.jsonl`` deferral (with rationale) tolerates it;
  WARNS (never fails) for a *newly-adopted* event source while its trace
  strategy matures; and FAILS for any source that is not classified in
  either list at all (including when no ``spine-registry.json`` policy is
  configured), which is exactly the pre-COM-234 "always fail" behavior --
  every existing caller that never wires up the new registry files keeps
  behaving exactly as before.
* ``resolution_status == "ambiguous"`` warns in non-strict mode and fails
  in strict mode (an event that could resolve to more than one declared
  edge must never be silently auto-picked).
* A resolved event (``resolved_exact``, ``resolved_rule``, ...) must
  declare a ``resolved_edge_id`` that names an edge record which is
  ACTIVE per the centralized `is_active_edge` predicate (current
  record_status, lifecycle_state absent-or-"active", no superseded_by) --
  resolving to a retired/inactive/superseded edge is not "mapped" for this
  predicate's purposes, since the pipeline no longer claims that edge
  exists. A resolved event with NO ``resolved_edge_id`` at all is treated
  the same as resolving to nothing: it does not name an active declared
  edge, so it fails this predicate too -- there is no grandfathering for
  current records; every resolved event must prove edge-level linkage.

Layer A generic tooling: reads only the generic ``trace_event`` and
``edge`` record shapes, plus the generic ``spine-registry.json``
``event_sources`` block and ``learning-registry.jsonl`` deferral records
when a ``source_root`` is supplied -- no project-specific vocabulary
lives here; project source names only ever appear in the registry files
a project itself authors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from audits._active import is_active_edge

__all__ = ["check"]

_UNMAPPED = "unmapped"
_AMBIGUOUS = "ambiguous"

_SPINE_REGISTRY_FILENAME = "spine-registry.json"
_LEARNING_REGISTRY_FILENAME = "learning-registry.jsonl"


def _load_event_source_policy(source_root: Any) -> tuple[frozenset[str], frozenset[str]]:
    """Load ``event_sources.covered``/``.newly_adopted`` from ``<source_root>/spine-registry.json``.

    A missing, unreadable, or malformed registry yields two empty sets,
    which preserves the pre-COM-234 behavior of always failing an
    unmapped event regardless of its ``source`` (see `_classify_unmapped`).
    """

    if source_root is None:
        return frozenset(), frozenset()
    registry_path = Path(source_root) / _SPINE_REGISTRY_FILENAME
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset(), frozenset()
    event_sources = registry.get("event_sources") if isinstance(registry, dict) else None
    if not isinstance(event_sources, dict):
        return frozenset(), frozenset()
    covered = event_sources.get("covered") or []
    newly_adopted = event_sources.get("newly_adopted") or []
    return frozenset(covered), frozenset(newly_adopted)


def _load_deferrals(source_root: Any) -> dict[str, dict[str, Any]]:
    """Load deferral entries from ``<source_root>/learning-registry.jsonl``, keyed by event id.

    Only entries with ``status == "deferred"`` AND a non-empty
    ``rationale`` count as a real deferral -- a deferral with no stated
    reason is silent tolerance, which this predicate never grants.
    """

    if source_root is None:
        return {}
    learning_path = Path(source_root) / _LEARNING_REGISTRY_FILENAME
    try:
        lines = learning_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    deferrals: dict[str, dict[str, Any]] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("status") != "deferred":
            continue
        if not str(entry.get("rationale") or "").strip():
            continue
        for applies_to_id in entry.get("applies_to") or []:
            deferrals[applies_to_id] = entry
    return deferrals


def _classify_unmapped(
    event: dict[str, Any],
    *,
    covered_sources: frozenset[str],
    newly_adopted_sources: frozenset[str],
    deferrals_by_event_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Tri-state disposition for one unmapped ``trace_event`` (COM-234 fold 3)."""

    event_id = event["record_id"]
    source = event.get("source")

    # COM-234 fold 3 / jCritic HIGH fix: classify the event's SOURCE first --
    # a learning-registry deferral only ever tolerates an unmapped cluster
    # from a *covered* source. Checking `deferrals_by_event_id` before source
    # classification would let a deferral entry divert an unclassified-source
    # (or newly-adopted-source) event out of its correct disposition, which
    # would silently regress the legacy "always fail when unclassified"
    # guarantee this predicate must preserve byte-for-byte.
    if source in covered_sources:
        deferral = deferrals_by_event_id.get(event_id)
        if deferral is not None:
            return {
                "record_id": event_id,
                "record_type": "trace_event",
                "violation_kind": "covered_source_unmapped_cluster_deferred",
                "message": (
                    f"trace_event {event_id!r} (source={source!r}) is unmapped but tolerated: "
                    f"{deferral.get('rationale')}"
                ),
                "_disposition": "deferral",
            }
        return {
            "record_id": event_id,
            "record_type": "trace_event",
            "violation_kind": "covered_source_unmapped_cluster",
            "message": (
                f"trace_event {event_id!r} from covered source {source!r} is unmapped: no "
                "declared edge/seam claims this observed event, and no learning-registry "
                "deferral with rationale tolerates it."
            ),
        }
    if source in newly_adopted_sources:
        return {
            "record_id": event_id,
            "record_type": "trace_event",
            "violation_kind": "newly_adopted_source_unmapped_cluster",
            "message": (
                f"trace_event {event_id!r} from newly-adopted source {source!r} is unmapped: "
                "warning only while its trace-rule coverage matures toward ruled or deferred."
            ),
            "_disposition": "warning",
        }
    return {
        "record_id": event_id,
        "record_type": "trace_event",
        "violation_kind": "unmapped_observed_edge",
        "message": (
            f"trace_event {event_id!r} is unmapped: no declared edge/seam claims "
            "this observed event."
        ),
    }


def check(
    records: Sequence[dict[str, Any]],
    *,
    source_root: Any = None,
    strict: bool = True,
) -> list[dict[str, Any]]:
    events = [
        record
        for record in records
        if record.get("record_type") == "trace_event" and record.get("record_status") == "declared"
    ]
    edges_by_id = {
        record["record_id"]: record
        for record in records
        if record.get("record_type") == "edge" and record.get("record_id")
    }
    covered_sources, newly_adopted_sources = _load_event_source_policy(source_root)
    deferrals_by_event_id = _load_deferrals(source_root)

    violations: list[dict[str, Any]] = []
    for event in events:
        status = event.get("resolution_status")
        event_id = event["record_id"]
        if status == _UNMAPPED:
            violations.append(
                _classify_unmapped(
                    event,
                    covered_sources=covered_sources,
                    newly_adopted_sources=newly_adopted_sources,
                    deferrals_by_event_id=deferrals_by_event_id,
                )
            )
            continue
        if status == _AMBIGUOUS and strict:
            violations.append(
                {
                    "record_id": event_id,
                    "record_type": "trace_event",
                    "violation_kind": "ambiguous_observed_edge",
                    "message": (
                        f"trace_event {event_id!r} is ambiguous across multiple declared edges; "
                        "never auto-picked, and strict mode treats this as a failure."
                    ),
                }
            )
            continue

        resolved_edge_id = event.get("resolved_edge_id")
        edge = edges_by_id.get(resolved_edge_id) if resolved_edge_id is not None else None
        if edge is not None and is_active_edge(edge):
            continue
        violations.append(
            {
                "record_id": event_id,
                "record_type": "trace_event",
                "violation_kind": "resolved_edge_not_declared_active",
                "message": (
                    f"trace_event {event_id!r} resolved_edge_id {resolved_edge_id!r} does not "
                    "name an active declared edge."
                ),
            }
        )
    return violations
