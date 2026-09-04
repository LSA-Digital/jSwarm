"""Audit registry for the nine S3 fitness-function audits (COM-234 Phase 4).

This module is the single place that:

* names the nine authoritative audits in their deterministic order
  (`AUDIT_NAMES`, `AUDIT_REGISTRY`);
* wraps each audit's raw-violation predicate in the uniform machine-JSON
  `AuditResult` contract; and
* applies one shared freeze-ledger tolerance mechanism to every audit's
  raw violations, so no individual predicate module has to reimplement
  freeze-entry matching.

Freeze-ledger tolerance model
------------------------------
A raw violation is *frozen* (tolerated, does not fail the audit) only when
ALL FOUR of the following match a single `freeze_entry` record:

* ``freeze_entry.audit_name`` equals the audit currently being evaluated;
* ``freeze_entry.applies_to_record_id`` equals the violation's own
  ``record_id`` (direct match only -- there is no seam-neighborhood or
  other transitive expansion; see `_violation_is_frozen`'s docstring); and
* ``freeze_entry.frozen_violation_signature`` equals the deterministic
  signature computed from ``(audit_name, record_id, violation_kind)`` --
  see `_compute_violation_signature`.

A freeze entry authored for one audit (or one violation_kind, or with a
stale/wrong signature) never tolerates a *different* audit's violation on
the same record_id -- this is what makes the freeze ledger a precise,
per-violation contract rather than a blanket per-record allowlist.
Everything that doesn't match all four fields is a *new* violation and
fails the audit.

Separately -- and regardless of whether a violation is frozen or new --
every violation is annotated with ``related_freeze_entries``: the ids of
any `freeze_entry` records whose ``applies_to_record_id``, ``rationale``,
or ``sunset_condition`` text mentions the violation's ``record_id``. This
is what keeps a known, still-open gap (ADV-2A) explicit rather than
silent: even a violation that still fails the audit (as it should, since
the underlying condition -- e.g. an anchor genuinely needing manual
reselect -- is real) carries a visible citation to the freeze/baseline
record that already documents it, instead of vanishing into an
unexplained failure.

Layer A generic tooling: this module and every predicate module it wires
reads only the generic seam-spine record shapes and the freeze-ledger
record shape. No project-specific vocabulary lives here; project values
only ever appear in tests and fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from audits import (
    declared_edges_observed_or_legacy_explained,
    legacy_paths_frozen,
    no_empty_carrier_cells,
    no_orphan_producers,
    no_reason_code_collisions,
    no_unconsumed_channels,
    observed_edges_declared,
    single_truth_source,
)
from audits.anchor_freshness import _MANUAL_RESELECT_STATES, verify_anchors

__all__ = [
    "AUDIT_NAMES",
    "AUDIT_REGISTRY",
    "Audit",
    "AuditResult",
    "run_audit",
    "run_all_audits",
]

RawViolationFn = Callable[..., list[dict[str, Any]]]

# Authoritative #296 S3 audit order and one-liner predicates. This order is
# part of the CI JSON contract: registry iteration, `run_all_audits()`, and
# `cli.py audit --ci` output must all preserve it so reports diff cleanly.
AUDIT_NAMES: tuple[str, ...] = (
    "no_orphan_producers",
    "no_unconsumed_channels",
    "no_reason_code_collisions",
    "no_empty_carrier_cells",
    "ui_surfaces_have_single_truth_source",
    "declared_edges_observed_or_legacy_explained",
    "observed_edges_declared",
    "anchors_verified_or_relocated",
    "legacy_paths_frozen_no_new_violations",
)

_PREDICATES: dict[str, str] = {
    "no_orphan_producers": (
        "every active producer seam has at least one outgoing required edge to a channel or "
        "consumer, unless all carrier cells are n_a or the seam is legacy/frozen."
    ),
    "no_unconsumed_channels": "every active channel has at least one active consuming edge.",
    "no_reason_code_collisions": (
        "no two active reason-code records share (seam_id, code) with different meaning, "
        "retryable, or terminal."
    ),
    "no_empty_carrier_cells": (
        "every active seam has a cell for every project-required carrier; carrier cell state "
        "cannot be missing."
    ),
    "ui_surfaces_have_single_truth_source": (
        "for each (surface_name, fact_key), exactly one primary truth_source_channel_id; "
        "fallbacks must be explicitly allowed."
    ),
    "declared_edges_observed_or_legacy_explained": (
        "every active declared edge in trace-required scope is either observed in at least one "
        "accepted trace or has a legacy/freeze explanation."
    ),
    "observed_edges_declared": (
        "every resolved observed edge maps to an active declared edge; unmapped events fail "
        "above threshold; ambiguous events warn unless strict."
    ),
    "anchors_verified_or_relocated": (
        "each active anchor is verified or auto_relocated; needs_reselect, "
        "deleted_or_unresolvable, and ambiguous fail strict mode."
    ),
    "legacy_paths_frozen_no_new_violations": (
        "known frozen signatures are allowed; any unmatched new violation fails."
    ),
}


def _single_truth_source_violations(
    records: Sequence[dict[str, Any]],
    *,
    source_root: Any = None,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Thin adapter over the existing `single_truth_source.check()` module."""

    violations = single_truth_source.check(records)
    return [
        {
            "record_id": violation.record_id,
            "record_type": violation.record_type,
            "violation_kind": violation.code,
            "message": violation.message,
            "related_record_ids": list(violation.related_record_ids),
        }
        for violation in violations
    ]


def _is_active_anchor(record: dict[str, Any]) -> bool:
    if record.get("lifecycle_state", "active") != "active":
        return False
    if record.get("superseded_by"):
        return False
    return True


def _anchor_freshness_violations(
    records: Sequence[dict[str, Any]],
    *,
    source_root: Any = None,
    strict: bool = True,
) -> list[dict[str, Any]]:
    """Predicate for audit #8: verified-or-relocated over each active anchor.

    ADV-2: when a ``source_root`` is supplied, this predicate performs a
    LIVE re-scan via `anchor_freshness.verify_anchors()` against the real
    source tree and uses its freshly-computed per-anchor ``state`` -- never
    the anchor's stale, previously-recorded ``anchor_state`` field. This is
    what makes `audit --ci --source-root ...` an honest gate: a
    ``verified`` anchor whose backing file has since been deleted must
    fail, not silently pass because its last-known declared state says
    ``verified``. Only when no ``source_root`` is supplied (the predicate
    is invoked without a source tree to check against) does this fall back
    to trusting the anchor's own declared ``anchor_state`` field, exactly
    as a prior `cli.py audit --anchors` pass recorded it.

    Any anchor left in a manual-reselect state (``needs_reselect``,
    ``deleted_or_unresolvable``, ``ambiguous``) fails this audit;
    ``verified``/``auto_relocated`` pass.
    """

    anchors = [
        record
        for record in records
        if record.get("record_type") == "anchor" and _is_active_anchor(record)
    ]

    live_states: dict[str, str] | None = None
    if source_root is not None:
        report = verify_anchors(records, source_root=source_root, strict=strict)
        live_states = {
            entry.get("record_id"): entry.get("state")
            for entry in report.anchors
            if entry.get("record_id")
        }

    violations: list[dict[str, Any]] = []
    for anchor in anchors:
        anchor_id = anchor["record_id"]
        if live_states is not None and anchor_id in live_states:
            state = live_states[anchor_id]
        else:
            state = anchor.get("anchor_state", "verified")
        if state not in _MANUAL_RESELECT_STATES:
            continue
        violations.append(
            {
                "record_id": anchor_id,
                "record_type": "anchor",
                "violation_kind": "anchor_drift",
                "state": state,
                "message": (
                    f"anchor {anchor_id!r} verification state is {state!r}; manual "
                    "reselect required."
                ),
            }
        )
    return violations


# Every raw-violation predicate, keyed by audit name. Each callable returns
# a list of raw violation dicts (pre freeze-partition, pre related-freeze
# annotation) -- `run_audit()` applies both uniformly below.
_RAW_VIOLATION_FNS: dict[str, RawViolationFn] = {
    "no_orphan_producers": no_orphan_producers.check,
    "no_unconsumed_channels": no_unconsumed_channels.check,
    "no_reason_code_collisions": no_reason_code_collisions.check,
    "no_empty_carrier_cells": no_empty_carrier_cells.check,
    "ui_surfaces_have_single_truth_source": _single_truth_source_violations,
    "declared_edges_observed_or_legacy_explained": declared_edges_observed_or_legacy_explained.check,
    "observed_edges_declared": observed_edges_declared.check,
    "anchors_verified_or_relocated": _anchor_freshness_violations,
    "legacy_paths_frozen_no_new_violations": legacy_paths_frozen.check,
}


@dataclass(frozen=True)
class AuditResult:
    """Stable machine-JSON result for one audit run."""

    audit_name: str
    predicate: str
    status: str  # "pass" | "fail" | "warn"
    new_violations: list[dict[str, Any]]
    frozen_violations: list[dict[str, Any]]
    summary: str
    deferrals: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def violations(self) -> list[dict[str, Any]]:
        return self.new_violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_name": self.audit_name,
            "status": self.status,
            "predicate": self.predicate,
            "violations": self.new_violations,
            "frozen_violations": self.frozen_violations,
            "new_violations": self.new_violations,
            "summary": self.summary,
            "deferrals": self.deferrals,
            "warnings": self.warnings,
        }


def _freeze_entries(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record.get("record_type") == "freeze_entry"]


def _compute_violation_signature(audit_name: str, record_id: str, violation_kind: str) -> str:
    """Deterministic, non-cryptographic freeze-ledger signature.

    Format: ``sha256:{audit_name}:{record_id}:{violation_kind}``. This is a
    flat, human-readable identity string (the ``sha256:`` prefix is a
    stable label, not an actual hash) -- it exists so a `freeze_entry`
    names the exact (audit, record, kind) triple it tolerates, rather than
    tolerating anything that happens to touch a given record_id.
    """

    return f"sha256:{audit_name}:{record_id}:{violation_kind}"


def _violation_is_frozen(
    audit_name: str,
    violation: dict[str, Any],
    freeze_entries: Sequence[dict[str, Any]],
) -> bool:
    """A violation is frozen iff ALL FOUR fields match a single `freeze_entry`.

    Direct-id match only: a `freeze_entry` targeting a seam tolerates only
    that seam record itself, never the seam's linked
    producers/consumers/channels/reason codes/carrier cells/paths
    (jCritic Phase 4 review finding: seam-neighborhood expansion let a
    freeze_entry on one seam mask an unrelated `no_empty_carrier_cells`
    violation on a linked carrier cell). On top of that direct-id match,
    the freeze entry must also declare the SAME ``audit_name`` and the
    SAME ``frozen_violation_signature`` computed from
    ``(audit_name, record_id, violation_kind)`` -- a freeze entry authored
    against the wrong audit, or carrying a stale/mistyped signature, must
    not tolerate this violation (jCritic Phase 4 review finding: a
    freeze_entry whose audit_name/signature belonged to a different audit
    was silently treated as covering any violation on the same
    record_id). Every real freeze_entry fixture in this suite already
    supplies a correct ``audit_name``/``frozen_violation_signature`` for
    the violation it is meant to tolerate; no fixture (RED or pilot)
    depends on the looser record-id-only match this replaces.
    """

    record_id = violation.get("record_id", "")
    violation_kind = violation.get("violation_kind", "")
    expected_signature = _compute_violation_signature(audit_name, record_id, violation_kind)
    for entry in freeze_entries:
        if (
            entry.get("audit_name") == audit_name
            and entry.get("applies_to_record_id") == record_id
            and entry.get("frozen_violation_signature") == expected_signature
        ):
            return True
    return False


def _related_freeze_entry_ids(records: Sequence[dict[str, Any]], record_id: str) -> list[str]:
    """`freeze_entry` ids whose own fields mention `record_id`, direct or textual."""

    related: list[str] = []
    for entry in _freeze_entries(records):
        haystack = " ".join(
            str(entry.get(field_name, "")) for field_name in ("applies_to_record_id", "rationale", "sunset_condition")
        )
        if record_id in haystack:
            related.append(entry["record_id"])
    return related


def _partition_by_freeze(
    records: Sequence[dict[str, Any]],
    raw_violations: list[dict[str, Any]],
    audit_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split raw violations into (frozen, new), annotating each with related freeze entries.

    A violation is frozen only when a `freeze_entry` matches its
    ``audit_name``, ``record_id``, and ``frozen_violation_signature`` all
    at once -- see `_violation_is_frozen`.

    ADV-2A: annotation happens regardless of which bucket a violation lands
    in, so a still-failing (new) violation that a freeze_entry already
    documents (e.g. its `sunset_condition` names the exact record) stays
    explicit rather than silent.
    """

    freeze_entries = _freeze_entries(records)
    frozen: list[dict[str, Any]] = []
    new: list[dict[str, Any]] = []

    for violation in raw_violations:
        record_id = violation.get("record_id", "")
        related = _related_freeze_entry_ids(records, record_id)
        annotated = {**violation, "related_freeze_entries": related} if related else dict(violation)
        if _violation_is_frozen(audit_name, violation, freeze_entries):
            frozen.append(annotated)
        else:
            new.append(annotated)

    return frozen, new


def _summarize(
    audit_name: str,
    status: str,
    new_count: int,
    frozen_count: int,
    warning_count: int = 0,
) -> str:
    if status == "warn":
        return f"{audit_name}: 0 new violations, {warning_count} warning(s) ({frozen_count} frozen, tolerated)."
    if status == "pass" and frozen_count == 0:
        return f"{audit_name}: 0 violations."
    if status == "pass":
        return f"{audit_name}: 0 new violations ({frozen_count} frozen, tolerated)."
    return f"{audit_name}: {new_count} new violation(s) ({frozen_count} frozen, tolerated)."


@dataclass(frozen=True)
class Audit:
    """One registry entry: an audit's identity plus its executable check."""

    audit_name: str
    predicate: str
    _raw_violation_fn: RawViolationFn = field(repr=False)

    def check(
        self,
        records: Sequence[dict[str, Any]],
        *,
        source_root: Path | str | None = None,
        strict: bool = True,
    ) -> AuditResult:
        raw_items = self._raw_violation_fn(records, source_root=source_root, strict=strict)

        # COM-234 fold 3: a raw item may carry an internal "_disposition"
        # marker ("warning" | "deferral") diverting it away from the
        # ordinary violation path entirely -- freeze-ledger tolerance never
        # applies to it, since it was never a violation to begin with. Every
        # pre-existing predicate never sets this marker, so `disposition`
        # defaults to "violation" and this loop is a no-op passthrough for
        # all eight other audits' raw item lists.
        raw_violations: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        deferrals: list[dict[str, Any]] = []
        for item in raw_items:
            disposition = item.get("_disposition", "violation")
            cleaned = {key: value for key, value in item.items() if key != "_disposition"}
            if disposition == "warning":
                warnings.append(cleaned)
            elif disposition == "deferral":
                deferrals.append(cleaned)
            else:
                raw_violations.append(cleaned)

        frozen, new = _partition_by_freeze(records, raw_violations, self.audit_name)
        if new:
            status = "fail"
        elif warnings:
            status = "warn"
        else:
            status = "pass"
        summary = _summarize(self.audit_name, status, len(new), len(frozen), len(warnings))
        return AuditResult(
            audit_name=self.audit_name,
            predicate=self.predicate,
            status=status,
            new_violations=new,
            frozen_violations=frozen,
            summary=summary,
            deferrals=deferrals,
            warnings=warnings,
        )


AUDIT_REGISTRY: dict[str, Audit] = {
    name: Audit(audit_name=name, predicate=_PREDICATES[name], _raw_violation_fn=_RAW_VIOLATION_FNS[name])
    for name in AUDIT_NAMES
}


def run_audit(
    audit_name: str,
    records: Sequence[dict[str, Any]],
    *,
    source_root: Path | str | None = None,
    strict: bool = True,
) -> AuditResult:
    if audit_name not in AUDIT_REGISTRY:
        raise KeyError(f"unknown audit_name {audit_name!r}; known audits: {', '.join(AUDIT_NAMES)}")
    return AUDIT_REGISTRY[audit_name].check(records, source_root=source_root, strict=strict)


def run_all_audits(
    records: Sequence[dict[str, Any]],
    *,
    source_root: Path | str | None = None,
    strict: bool = True,
) -> list[AuditResult]:
    return [
        AUDIT_REGISTRY[audit_name].check(records, source_root=source_root, strict=strict)
        for audit_name in AUDIT_NAMES
    ]
