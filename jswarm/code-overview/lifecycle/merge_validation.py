"""`/jMerge` branch-vs-main lifecycle validation (Phase 7B).

Answers one question before a branch lands on main: did this branch
introduce a new lifecycle regression that main did not already have? A
regression that already existed on main is not the branch's fault and must
not block the merge; only a regression the branch itself introduces does.

Every check here is a thin diff over machinery that already exists and is
never re-derived:

* branch schema validity comes straight from ``schema.validate_spine()``;
* every other rejection reason comes from diffing two
  ``audits.registry.run_all_audits()`` runs (main vs branch) by
  ``new_violations`` record_id, walking the registry's own deterministic
  ``AUDIT_NAMES`` order so the first audit the branch newly fails is the
  one reported.

That single diff loop is enough to name all three non-schema rejection
kinds this ceremony recognizes, because the registry already runs both the
anchor-freshness audit (``anchors_verified_or_relocated``) and the
observed-event audit (``observed_edges_declared``) alongside the other
seven fitness-function audits:

* ``anchors_verified_or_relocated`` differing -> ``stale_anchor``;
* ``observed_edges_declared`` differing -> ``new_unclassified_observed_event``;
* any other audit differing -> the generic ``new_non_frozen_violation``.

At most one rejection is ever reported: the first differing check, in a
fixed precedence order (schema validity first, then the registry audits in
their own deterministic order), is the merge-blocking regression. A branch
that introduces nothing new relative to main passes with an empty
``introduced_rejections`` list.

Layer A generic tooling: every value here (spine records, source roots,
spine paths) is caller-supplied; nothing project-specific is hard-coded in
this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from audits.registry import AUDIT_NAMES, AuditResult, run_all_audits
from schema import validate_spine

__all__ = ["validate_merge"]

SCHEMA_VERSION = "code_overview.merge_validation.v1"

_EXIT_PASS = 0
_EXIT_AUDIT_FAILURE = 1
_EXIT_ANCHOR_DRIFT_MANUAL_RESELECT = 4
_EXIT_SCHEMA_MAJOR_UNSUPPORTED = 7

_KIND_BY_AUDIT_NAME: dict[str, str] = {
    "anchors_verified_or_relocated": "stale_anchor",
    "observed_edges_declared": "new_unclassified_observed_event",
}
_DEFAULT_KIND = "new_non_frozen_violation"

_EXIT_BY_KIND: dict[str, int] = {
    "stale_anchor": _EXIT_ANCHOR_DRIFT_MANUAL_RESELECT,
    "new_unclassified_observed_event": _EXIT_AUDIT_FAILURE,
    _DEFAULT_KIND: _EXIT_AUDIT_FAILURE,
}


def _violation_record_ids(result: AuditResult) -> set[str]:
    return {
        violation.get("record_id")
        for violation in result.new_violations
        if violation.get("record_id")
    }


def _packet(
    *,
    main_spine_path: str,
    branch_spine_path: str,
    status: str,
    exit_code: int,
    introduced_rejections: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ceremony": "merge",
        "main_spine": str(main_spine_path),
        "branch_spine": str(branch_spine_path),
        "status": status,
        "exit_code": exit_code,
        "introduced_rejections": introduced_rejections,
    }


def validate_merge(
    *,
    main_records: Sequence[dict[str, Any]],
    branch_records: Sequence[dict[str, Any]],
    main_spine_path: str,
    branch_spine_path: str,
    main_source_root: Path | str | None,
    branch_source_root: Path | str | None,
) -> dict[str, Any]:
    """Reject only the first lifecycle regression the branch introduces vs main."""

    branch_validation = validate_spine(branch_records)
    if not branch_validation.ok:
        schema_errors = [
            error for error in branch_validation.errors if error.code == "unsupported_schema_version"
        ]
        primary_error = schema_errors[0] if schema_errors else branch_validation.errors[0]
        return _packet(
            main_spine_path=main_spine_path,
            branch_spine_path=branch_spine_path,
            status="rejected",
            exit_code=branch_validation.exit_code,
            introduced_rejections=[
                {
                    "kind": "schema_invalidity",
                    "record_id": primary_error.record_id,
                    "introduced_by_branch": True,
                }
            ],
        )

    main_results = {
        result.audit_name: result
        for result in run_all_audits(main_records, source_root=main_source_root, strict=True)
    }
    branch_results = {
        result.audit_name: result
        for result in run_all_audits(branch_records, source_root=branch_source_root, strict=True)
    }

    for audit_name in AUDIT_NAMES:
        branch_ids = _violation_record_ids(branch_results[audit_name])
        main_ids = _violation_record_ids(main_results[audit_name])
        introduced_ids = sorted(branch_ids - main_ids)
        if not introduced_ids:
            continue
        record_id = introduced_ids[0]
        kind = _KIND_BY_AUDIT_NAME.get(audit_name, _DEFAULT_KIND)
        return _packet(
            main_spine_path=main_spine_path,
            branch_spine_path=branch_spine_path,
            status="rejected",
            exit_code=_EXIT_BY_KIND[kind],
            introduced_rejections=[
                {"kind": kind, "record_id": record_id, "introduced_by_branch": True}
            ],
        )

    return _packet(
        main_spine_path=main_spine_path,
        branch_spine_path=branch_spine_path,
        status="pass",
        exit_code=_EXIT_PASS,
        introduced_rejections=[],
    )
