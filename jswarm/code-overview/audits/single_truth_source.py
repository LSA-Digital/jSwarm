"""Single-truth-source audit: `ui_surfaces_have_single_truth_source`.

Flags any `(surface_name, fact_key)` group bound to more than one
conflicting authoritative truth-source channel. This is the mechanical
catch for the dual-status bug class: a UI fact -- identified by which
surface displays it and which key it displays -- must resolve to exactly
one authoritative truth source across every `ui_surface`/`ui_fact_binding`
record that contributes to it, never silently pick between two. Grouping
by ``record_id`` alone (a single surface's own binding) would miss the
case this predicate exists for: two *different* `ui_surface` records that
happen to render the same fact under the same surface name but disagree
on which channel is authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

__all__ = ["AuditViolation", "check"]


@dataclass(frozen=True)
class AuditViolation:
    code: str
    message: str
    record_id: str
    record_type: str = "ui_surface"
    severity: str = "error"
    related_record_ids: tuple[str, ...] = ()


def check(spine: Sequence[dict[str, Any]]) -> list[AuditViolation]:
    """Return violations for every `(surface_name, fact_key)` group with >1 truth source.

    Groups contributions across both `ui_surface` records and every
    `ui_fact_binding` whose `ui_surface_id` points at one, keyed by the
    surface's `(surface_name, fact_key)` pair rather than its
    `record_id` -- this is what lets the audit catch two distinct
    `ui_surface` records that share a display identity but disagree on
    truth source. A group with zero or one unique truth source is not
    flagged; more than one is the dual-status class this audit exists to
    catch (after allowed-fallback policy: only ``truth_source_channel_id``
    is considered authoritative, never the ``allowed_*``/``forbidden_*``
    fallback-declaration fields).
    """
    surfaces = [
        record
        for record in spine
        if record.get("record_type") == "ui_surface" and record.get("record_id")
    ]
    surface_by_id = {record["record_id"]: record for record in surfaces}

    def _key_for_surface(surface: dict[str, Any]) -> tuple[Any, Any]:
        return (surface.get("surface_name"), surface.get("fact_key"))

    # D-3 set operation: build, per (surface_name, fact_key) group, the
    # ordered list of (truth_source_channel_id, contributing_record_id)
    # pairs contributed by every ui_surface and ui_fact_binding record that
    # belongs to that group.
    contributions_by_key: dict[tuple[Any, Any], list[tuple[str, str]]] = {}

    for surface in surfaces:
        truth_source = surface.get("truth_source_channel_id")
        if not truth_source:
            continue
        key = _key_for_surface(surface)
        contributions_by_key.setdefault(key, []).append((truth_source, surface["record_id"]))

    for record in spine:
        if record.get("record_type") != "ui_fact_binding":
            continue
        surface = surface_by_id.get(record.get("ui_surface_id"))
        if surface is None:
            continue
        truth_source = record.get("truth_source_channel_id")
        if not truth_source:
            continue
        key = _key_for_surface(surface)
        contributions_by_key.setdefault(key, []).append((truth_source, record["record_id"]))

    violations: list[AuditViolation] = []
    for (surface_name, fact_key), contributions in contributions_by_key.items():
        unique_sources = list(dict.fromkeys(source for source, _ in contributions))
        if len(unique_sources) <= 1:
            continue

        contributing_ids = list(dict.fromkeys(rid for _, rid in contributions))
        representative_surface_ids = sorted(
            rid for rid in contributing_ids if rid in surface_by_id
        )
        record_id = representative_surface_ids[0] if representative_surface_ids else contributing_ids[0]

        violations.append(
            AuditViolation(
                code="ui_fact_multiple_truth_sources",
                message=(
                    f"ui surfaces sharing (surface_name={surface_name!r}, fact_key={fact_key!r}) "
                    f"resolve to {len(unique_sources)} conflicting truth source channels: "
                    f"{', '.join(unique_sources)}. Exactly one primary truth source is required "
                    "per allowed-fallback policy; a UI surface must never silently pick between "
                    "conflicting fallbacks."
                ),
                record_id=record_id,
                record_type="ui_surface",
                related_record_ids=tuple(dict.fromkeys(contributing_ids + unique_sources)),
            )
        )

    return violations
