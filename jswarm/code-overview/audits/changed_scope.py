"""Changed-scope audit: bounds a check to changed records + their neighborhood.

(COM-234 Phase 7A, spec #295 S7.11; hardened Phase 7 fix-GREEN cycle 1.) A
full audit run (`audit --ci`, `run_all_audits`) walks the whole seam-spine.
Some workflows -- a small follow-up edit to one seam, for example -- only
want to know: did *this* change introduce a new violation, without being
blocked by an unrelated, pre-existing violation somewhere else in the spine
that nobody touched.

``run_changed_scope_audit`` answers exactly that narrower question, in two
steps that both deliberately REUSE existing canonical machinery rather than
reimplementing a laxer, narrower copy of it:

1. **Bidirectional neighborhood expansion.** Starting from the caller's
   ``changed_record_ids``, expand to the *changed neighborhood*: the
   connected component reachable from the changed ids in the bidirectional
   reference graph over every ``PIPE-``-prefixed id found in any record's
   own field values (see ``_expand_neighborhood``). "Bidirectional" is the
   fix for a jCritic-xhigh finding (Phase 7 GATE B2): a purely *forward*
   walk from a changed producer seam never reaches an inbound channel that
   names the producer only in the CHANNEL's own ``producer_seam_ids`` field
   (the producer itself carries no ``channel_ids`` back-reference). Walking
   both directions -- what a record references, AND what other records
   reference it -- reaches those multi-hop dependents (changed producer ←
   channel → consumer) without hard-coding any particular schema field
   name: this stays Layer-A generic by construction, not by enumerating a
   fixed field list.
2. **Canonical audit reuse, then scope-filter.** For every requested audit
   name, run the SAME canonical predicate the full-spine audit runs
   (`audits.registry.run_audit`, freeze-ledger tolerance included) over the
   *whole* records set -- never a re-derived, narrower local copy of the
   predicate -- and then keep only the violations that *touch* the changed
   neighborhood: either the violation's own ``record_id`` falls inside it,
   or any of the violation's ``related_record_ids`` does (see
   ``_violation_touches_scope``). This is the fix for the other
   jCritic-xhigh finding (Phase 7 GATE B1): a changed-scope predicate that
   silently reimplements canonical semantics can drift laxer than the
   canonical audit and let a real violation through. Because this module
   now always asks the registry for the canonical answer and only narrows
   the *set of records considered*, changed-scope can never be laxer than
   `audit --ci` for the records it does check. Touching via
   ``related_record_ids`` is also what keeps a cross-record, group-by
   canonical violation (e.g. ``ui_surfaces_have_single_truth_source``,
   reported under one representative contributor's ``record_id``) from
   being missed when the *changed* record is only a non-representative
   contributor (Phase 7 fix-GREEN cycle 2, jCritic-xhigh B5).

Anything checked-and-violating is a *new* violation; anything outside the
neighborhood is *skipped* -- a pre-existing violation there can never fail
this run, by construction.

The whole computation is deterministic: expansion order does not affect the
final (sorted) checked/skipped sets, and violation order follows each
canonical audit's own deterministic iteration order over the full records
list (audit_names order, then within each audit its own violation order).

Layer A generic tooling: this module carries no project-specific
vocabulary. Record ids, audit names, and carrier names are all read from
the caller-supplied records/request; nothing project-specific is
hard-coded here.
"""

from __future__ import annotations

from typing import Any

from audits._active import is_active_record
from audits.registry import AUDIT_NAMES, run_audit

__all__ = ["run_changed_scope_audit"]

SCHEMA_VERSION = "code_overview.audit.changed_scope.v1"
_ID_PREFIX = "PIPE-"

# Every audit name the registry knows about is now a supported changed-scope
# audit, since changed-scope always defers to the canonical `run_audit` for
# the actual predicate and only narrows the record set the result is
# attributed against.
_KNOWN_AUDIT_NAMES = frozenset(AUDIT_NAMES)


def _referenced_ids(record: dict[str, Any]) -> list[str]:
    """Every ``PIPE-``-prefixed string value reachable from ``record``'s fields."""
    found: list[str] = []
    for value in record.values():
        if isinstance(value, str) and value.startswith(_ID_PREFIX):
            found.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.startswith(_ID_PREFIX):
                    found.append(item)
    return found


def _build_reverse_reference_index(
    records_by_id: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """``id -> ids of OTHER records whose own fields name it`` (inbound refs).

    This is the "backward" half of the bidirectional walk: it lets
    ``_expand_neighborhood`` discover, for example, a ``channel`` record
    that names a changed producer seam in its own ``producer_seam_ids``
    field, even though the producer seam's own fields never point at the
    channel.
    """
    reverse: dict[str, list[str]] = {}
    for record_id, record in records_by_id.items():
        for referenced_id in _referenced_ids(record):
            if referenced_id in records_by_id and referenced_id != record_id:
                reverse.setdefault(referenced_id, []).append(record_id)
    return reverse


def _expand_neighborhood(
    records_by_id: dict[str, dict[str, Any]],
    changed_record_ids: list[str],
) -> set[str]:
    """The connected component of ``changed_record_ids`` in the bidirectional
    ``PIPE-`` id reference graph.

    For every visited record, BOTH the ids it references (forward) and the
    ids of other records that reference it (backward, via the reverse
    index) are added to the frontier. Iterating until the frontier is
    exhausted reaches multi-hop dependents -- a changed producer's inbound
    channel, then that channel's forward consumer -- without hard-coding any
    schema-specific field name.
    """
    reverse_index = _build_reverse_reference_index(records_by_id)
    visited: set[str] = set()
    frontier: list[str] = [record_id for record_id in changed_record_ids if record_id in records_by_id]
    while frontier:
        current = frontier.pop()
        if current in visited:
            continue
        visited.add(current)
        record = records_by_id[current]
        neighbor_ids = list(_referenced_ids(record)) + list(reverse_index.get(current, []))
        for neighbor_id in neighbor_ids:
            if neighbor_id in records_by_id and neighbor_id not in visited:
                frontier.append(neighbor_id)
    return visited


def _legacy_empty_carrier_cell_signals(
    records_by_id: dict[str, dict[str, Any]],
    checked_ids: set[str],
    ordered_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Supplementary changed-scope-only signals, IN ADDITION to canonical reuse.

    These catch two conditions the canonical ``no_empty_carrier_cells``
    predicate does not check at all (it only checks
    ``spine_metadata.required_carriers`` presence, not per-cell state):

    * a seam's ``carrier_cell_ids`` names a cell record that does not exist
      (a dangling reference); or
    * a referenced carrier cell's own declared ``state`` is the schema's
      "undetermined" sentinel, ``"unknown"``.

    Neither condition depends on ``required_carriers`` being declared, so
    this remains useful even on a spine that has not (yet) declared any
    required carriers -- it is a strictly additive signal layered on top of
    (never a substitute for) the canonical, ``required_carriers``-based
    reuse in ``run_changed_scope_audit``.
    """
    violations: list[dict[str, Any]] = []
    for record in ordered_records:
        record_id = record.get("record_id")
        if (
            record.get("record_type") != "seam"
            or record_id not in checked_ids
            or not is_active_record(record)
        ):
            continue
        for cell_id in record.get("carrier_cell_ids") or []:
            cell = records_by_id.get(cell_id)
            if cell is None:
                violations.append(
                    {
                        "audit_name": "no_empty_carrier_cells",
                        "record_id": record_id,
                        "violation_kind": "dangling_carrier_cell_ref",
                        "carrier_cell_id": cell_id,
                        "reason": f"seam {record_id!r} references carrier cell {cell_id!r} which does not exist",
                    }
                )
            elif cell.get("state") == "unknown":
                violations.append(
                    {
                        "audit_name": "no_empty_carrier_cells",
                        "record_id": record_id,
                        "violation_kind": "undetermined_carrier_cell",
                        "carrier_cell_id": cell_id,
                        "carrier": cell.get("carrier"),
                        "reason": (
                            f"seam {record_id!r} carrier cell {cell_id!r} "
                            f"(carrier={cell.get('carrier')!r}) has state 'unknown' -- empty carrier"
                        ),
                    }
                )
    return violations


def _violation_touches_scope(violation: dict[str, Any], checked_ids: set[str]) -> bool:
    """True if ``violation`` touches ``checked_ids`` -- directly or via a contributor.

    (COM-234 Phase 7 fix-GREEN cycle 2, jCritic-xhigh B5.) A canonical
    group-by predicate (e.g. ``ui_surfaces_have_single_truth_source``) may
    report a violation under one *representative* contributor's
    ``record_id`` while every other contributor that fed the same group is
    listed only in ``related_record_ids``. Checking ``record_id`` alone
    therefore missed a changed record that participates in the violation
    purely as a non-representative contributor. "Touches scope" is
    ``record_id in checked_ids`` OR any ``related_record_ids`` entry in
    checked_ids -- a disconnected violation (representative AND every
    related id outside the changed neighborhood) still correctly yields an
    empty intersection and is skipped, preserving the changed-scope
    over-correction bound.
    """
    if violation.get("record_id") in checked_ids:
        return True
    related_ids = violation.get("related_record_ids") or ()
    return not checked_ids.isdisjoint(related_ids)


def run_changed_scope_audit(
    records: list[dict[str, Any]],
    changed_record_ids: list[str],
    audit_names: list[str] | None = None,
    *,
    source_root: str | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Audit only ``changed_record_ids`` and their bidirectional reference-reachable neighborhood.

    For every requested audit name, this runs the SAME canonical
    ``audits.registry.run_audit`` predicate the full-spine audit runs (over
    the whole ``records`` set, freeze-ledger tolerance included) and then
    filters its result down to violations whose ``record_id`` falls inside
    the changed neighborhood. Changed-scope can therefore never report a
    weaker (laxer) result than `audit --ci` would for the same records --
    it only ever narrows *which records* are considered, never *how* a
    violation is detected.

    Returns a JSON-serializable packet with ``status``/``exit_code``,
    ``checked_record_ids`` (the strict changed-neighborhood subset actually
    examined), ``skipped_record_ids`` (every other known record id -- a
    pre-existing violation there cannot fail this run), and
    ``new_violations``/``frozen_violations`` (each audit's canonical
    new/frozen violations, filtered to the checked scope).

    ``audit_names`` defaults to every registry audit (``AUDIT_NAMES``) when
    omitted -- now that every audit is reused rather than re-derived, there
    is no narrower "supported" subset to fall back to.

    ``source_root`` is passed through to every canonical audit predicate
    exactly as `audit --ci` would (e.g. it enables the live anchor-freshness
    re-scan); omit it to fall back to each predicate's own declared-field
    default, same as a full audit run without ``--source-root``.
    """

    records_by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        if isinstance(record, dict):
            record_id = record.get("record_id")
            if isinstance(record_id, str):
                records_by_id[record_id] = record

    checked_ids = _expand_neighborhood(records_by_id, list(changed_record_ids))
    skipped_ids = set(records_by_id) - checked_ids

    if audit_names is None:
        audit_names = list(AUDIT_NAMES)

    unknown_audit_names = [name for name in audit_names if name not in _KNOWN_AUDIT_NAMES]
    if unknown_audit_names:
        if strict:
            raise ValueError(
                f"run_changed_scope_audit: unsupported audit_names {unknown_audit_names!r}; "
                f"supported: {sorted(_KNOWN_AUDIT_NAMES)!r}"
            )
        audit_names = [name for name in audit_names if name in _KNOWN_AUDIT_NAMES]

    new_violations: list[dict[str, Any]] = []
    frozen_violations: list[dict[str, Any]] = []
    for audit_name in audit_names:
        result = run_audit(audit_name, records, source_root=source_root, strict=strict)
        new_violations.extend(
            violation for violation in result.new_violations if _violation_touches_scope(violation, checked_ids)
        )
        frozen_violations.extend(
            violation for violation in result.frozen_violations if _violation_touches_scope(violation, checked_ids)
        )
        if audit_name == "no_empty_carrier_cells":
            new_violations.extend(_legacy_empty_carrier_cell_signals(records_by_id, checked_ids, records))

    status = "fail" if new_violations else "pass"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "exit_code": 1 if new_violations else 0,
        "audit_names": list(audit_names),
        "changed_record_ids": sorted(changed_record_ids),
        "checked_record_ids": sorted(checked_ids),
        "skipped_record_ids": sorted(skipped_ids),
        "new_violations": new_violations,
        "frozen_violations": frozen_violations,
    }
