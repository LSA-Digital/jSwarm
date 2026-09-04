"""`/jClose` lifecycle audit for a fixture ticket (COM-234 Phase 7B).

This module answers one question for a ticket about to close: does any
anchor the ticket touched drift into a manual-reselect state
(``needs_reselect``, ``deleted_or_unresolvable``, ``ambiguous``)? A drifted
anchor blocks a strict close (exit 4) and only warns in an advisory close
(exit 0); a ticket with no drifted anchors always passes.

The drift check itself is never re-derived here -- it is a thin, generic
wrapper over the existing ``audits.anchor_freshness.verify_anchors()``
scan, scoped to the ticket's own ``changed_anchor_ids`` so a close-ticket
audit only ever judges what the ticket itself touched, not the whole
seam-spine.

A second, unrelated function -- ``describe_integration()`` -- proves the
whole lifecycle integration (this module plus its sibling
``merge_validation``) is additive and reversible: two new CLI entrypoints
plus two opt-in template localization anchors, never an edit to a live
``/jClose`` or ``/jMerge`` command body.

Layer A generic tooling: every value here (ticket id, anchor ids, source
root, spine records) is caller-supplied; nothing project-specific is
hard-coded in this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from audits.anchor_freshness import _MANUAL_RESELECT_STATES, verify_anchors

__all__ = ["build_close_ticket_audit_packet", "describe_integration"]

SCHEMA_VERSION = "code_overview.close_ticket_audit.v1"
INTEGRATION_SCHEMA_VERSION = "code_overview.lifecycle_integration.v1"

_EXIT_PASS = 0
_EXIT_ANCHOR_DRIFT_MANUAL_RESELECT = 4

CLOSE_TICKET_ANCHOR_NAME = "code-overview-v2-close-ticket-audit-integration"
MERGE_VALIDATION_ANCHOR_NAME = "code-overview-v2-merge-validation-rejections"


def build_close_ticket_audit_packet(
    *,
    ticket: str,
    changed_anchor_ids: Sequence[str],
    records: Sequence[dict[str, Any]],
    source_root: Path | str,
    strict: bool,
) -> dict[str, Any]:
    """Audit only the anchors ``changed_anchor_ids`` names against `source_root`.

    Reuses ``verify_anchors(..., changed_anchor_ids=...)`` for the live
    re-scan; this function only reshapes that report into the
    close-ticket-ceremony packet and decides the pass/advisory/blocked
    status and exit code.
    """

    report = verify_anchors(
        records,
        source_root=source_root,
        strict=strict,
        changed_anchor_ids=list(changed_anchor_ids),
    )

    stale_anchors = [
        {
            "record_id": anchor.get("record_id"),
            "state": anchor.get("state"),
            "message": anchor.get("message"),
        }
        for anchor in report.anchors
        if anchor.get("state") in _MANUAL_RESELECT_STATES
    ]

    blocking = strict and bool(stale_anchors)
    if not stale_anchors:
        status = "pass"
    elif blocking:
        status = "blocked"
    else:
        status = "advisory"

    exit_code = _EXIT_ANCHOR_DRIFT_MANUAL_RESELECT if blocking else _EXIT_PASS
    blocking_reasons = ["stale_anchor"] if blocking else []
    warnings = ["stale_anchor"] if stale_anchors and not blocking else []

    return {
        "schema_version": SCHEMA_VERSION,
        "ceremony": "close-ticket",
        "fixture_ticket": ticket,
        "strict": strict,
        "status": status,
        "exit_code": exit_code,
        "blocking": blocking,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "anchor_freshness": {
            "scope": report.scope,
            "anchors": stale_anchors,
        },
    }


def describe_integration() -> dict[str, Any]:
    """A reversible-integration proof: CLI entrypoints + anchors, no live-command edits.

    Both new lifecycle behaviors are wired entirely through new `cli.py`
    entrypoints and opt-in `PROJECT_COMMAND_INJECTIONS_TEMPLATE.yaml`
    anchors; ``live_command_body_paths`` is always empty because neither
    the global ``/jClose`` nor ``/jMerge`` command body is ever
    touched to enable this behavior -- a project opts in purely by filling
    in the two additive anchors.
    """

    return {
        "schema_version": INTEGRATION_SCHEMA_VERSION,
        "integration_mode": "additive_opt_in",
        "live_command_body_edits_required": False,
        "entrypoints": {
            "close_ticket": "jswarm/code-overview/cli.py audit --close-ticket",
            "merge": "jswarm/code-overview/cli.py merge validate",
        },
        "template_anchors": [
            CLOSE_TICKET_ANCHOR_NAME,
            MERGE_VALIDATION_ANCHOR_NAME,
        ],
        "live_command_body_paths": [],
    }
