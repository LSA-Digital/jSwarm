"""Validate the plan §Machine-readable UAT trigger contract.

Phase 4 seams are `/jPlan` composition and `/jGo` phase-exit evaluation.
"""

from __future__ import annotations

import json
import re
from typing import Mapping, NamedTuple


SCHEMA_VERSION: str = "uat-execution-trigger@1"
AUTOMATED_UAT_VALUES = ("yes", "no")
APPLICABILITY_VALUES = ("REQUIRED", "SKIP")
IMPACT_VALUES = ("USER_VISIBLE", "QUALITY_ONLY", "NONE")
LOWER_LEVEL_GATE_VALUES = ("GREEN_REQUIRED",)
FRESHNESS_FIELDS = (
    "package_hash",
    "script_hash",
    "certified_build_hash",
    "package_state",
    "latest_prewalk_receipt",
    "overall_verdict",
)
CANONICAL_ROUTE = "/test uat prepare"
MANDATORY_TOP_LEVEL_FIELDS = (
    "schema_version",
    "automated_uat",
    "canonical_route",
    "receipt_source",
    "freshness_fields",
    "phases",
)
_PHASE_FIELDS = (
    "phase_id",
    "applicability",
    "impact",
    "obligation_refs",
    "required_assets",
    "lower_level_gate",
    "skip_reason",
)


class TriggerContractError(ValueError):
    """Raised when a plan has duplicate UAT Execution Trigger sections."""


class PhaseExitDecision(NamedTuple):
    """Closed phase-exit action: PROCEED, DISPATCH, BLOCK, or SKIP.

    Route is empty or `/test uat prepare <TICKET>`; reason is a typed token.
    """

    action: str
    route: str
    reason: str


def extract_trigger_section(plan_bytes: bytes) -> bytes | None:
    """Return exact fenced JSON bytes from one UAT Execution Trigger section.

    Return ``None`` when absent and raise ``TriggerContractError`` for duplicate
    sections.
    """
    headings = list(re.finditer(br"(?m)^## UAT Execution Trigger\r?$", plan_bytes))
    if len(headings) > 1:
        raise TriggerContractError("duplicate UAT Execution Trigger sections")
    if not headings:
        return None
    section_start = headings[0].end()
    next_heading = re.search(br"(?m)^## ", plan_bytes[section_start:])
    section_end = section_start + next_heading.start() if next_heading is not None else len(plan_bytes)
    if len(re.findall(br"(?m)^```json\r?$", plan_bytes[section_start:section_end])) != 1:
        raise TriggerContractError("UAT Execution Trigger must contain one JSON object")
    while plan_bytes[section_start:section_start + 2] == b"\r\n":
        section_start += 2
    while plan_bytes[section_start:section_start + 1] == b"\n":
        section_start += 1
    if not plan_bytes.startswith(b"```json", section_start):
        return None
    payload_start = section_start + len(b"```json")
    if plan_bytes[payload_start:payload_start + 2] == b"\r\n":
        payload_start += 2
    elif plan_bytes[payload_start:payload_start + 1] == b"\n":
        payload_start += 1
    closing_fence = re.search(br"\r?\n```", plan_bytes[payload_start:])
    if closing_fence is None:
        return None
    return plan_bytes[payload_start:payload_start + closing_fence.start()]


def validate_trigger(document: Mapping[str, object]) -> list[str]:
    """Return typed trigger errors once implemented.

    Planned tokens include ``trigger.invalid.top-level``,
    ``trigger.invalid.schema-version``, ``trigger.invalid.canonical-route``,
    ``trigger.invalid.automated-uat``, ``trigger.invalid.phase``,
    ``trigger.invalid.phase-id``, ``trigger.invalid.freshness-fields``, and
    ``trigger.invalid.cross-field`` for mandatory keys, exact constants,
    allowlists, unique phase IDs, required arrays, and cross-field truth.
    """
    errors: list[str] = []
    if set(document) != set(MANDATORY_TOP_LEVEL_FIELDS):
        errors.append("trigger.invalid.top-level")
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append("trigger.invalid.schema-version")
    if document.get("canonical_route") != CANONICAL_ROUTE:
        errors.append("trigger.invalid.canonical-route")
    automated_uat = document.get("automated_uat")
    if automated_uat not in AUTOMATED_UAT_VALUES:
        errors.append("trigger.invalid.automated-uat")
    receipt_source = document.get("receipt_source")
    if not isinstance(receipt_source, str) or not receipt_source:
        errors.append("trigger.invalid.top-level")
    freshness_fields = document.get("freshness_fields")
    if freshness_fields != list(FRESHNESS_FIELDS):
        errors.append("trigger.invalid.freshness-fields")
    phases = document.get("phases")
    if not isinstance(phases, list) or not phases:
        return [*errors, "trigger.invalid.phase"]

    phase_ids: set[str] = set()
    for phase in phases:
        if not isinstance(phase, Mapping) or set(phase) != set(_PHASE_FIELDS):
            errors.append("trigger.invalid.phase")
            continue
        phase_id = phase.get("phase_id")
        if not isinstance(phase_id, str) or not phase_id or phase_id in phase_ids:
            errors.append("trigger.invalid.phase-id")
        else:
            phase_ids.add(phase_id)
        applicability = phase.get("applicability")
        impact = phase.get("impact")
        lower_level_gate = phase.get("lower_level_gate")
        if applicability not in APPLICABILITY_VALUES or impact not in IMPACT_VALUES or lower_level_gate not in LOWER_LEVEL_GATE_VALUES:
            errors.append("trigger.invalid.phase")
            continue
        obligation_refs = phase.get("obligation_refs")
        required_assets = phase.get("required_assets")
        skip_reason = phase.get("skip_reason")
        arrays_are_strings = all(
            isinstance(values, list) and all(isinstance(value, str) and value for value in values)
            for values in (obligation_refs, required_assets)
        )
        if not arrays_are_strings or not isinstance(skip_reason, str):
            errors.append("trigger.invalid.cross-field")
            continue
        if automated_uat == "no":
            if applicability != "SKIP" or impact != "NONE":
                errors.append("trigger.invalid.cross-field")
        elif applicability == "REQUIRED":
            if impact not in {"USER_VISIBLE", "QUALITY_ONLY"} or not obligation_refs or not required_assets or skip_reason:
                errors.append("trigger.invalid.cross-field")
        elif applicability == "SKIP":
            if impact != "NONE" or obligation_refs or required_assets or not skip_reason:
                errors.append("trigger.invalid.cross-field")
    return errors


def compose_trigger_section(document: Mapping[str, object], *, ticket: str) -> bytes:
    """Validate and render the one fenced UAT Execution Trigger section.

    The composer defaults an omitted receipt source to the ticket's
    traceability ledger and preserves the embedded JSON bytes for extraction.
    """
    rendered_document = dict(document)
    rendered_document.setdefault(
        "receipt_source",
        f".jswarm/plans/{ticket}/{ticket}.test-traceability.md",
    )
    errors = validate_trigger(rendered_document)
    if errors:
        raise TriggerContractError(",".join(errors))
    payload = json.dumps(rendered_document, indent=2, sort_keys=False).encode("utf-8")
    return b"## UAT Execution Trigger\n\n```json\n" + payload + b"\n```\n"


def evaluate_phase_exit(
    trigger_document: Mapping[str, object],
    phase_id: str,
    ledger_state: Mapping[str, object],
    *,
    lower_level_green: bool,
    canonical_phase_ids: tuple[str, ...],
    active_package_hash: str,
    active_script_hash: str,
    active_build_hash: str,
    ticket: str,
) -> PhaseExitDecision:
    """Evaluate one phase as PROCEED, DISPATCH, BLOCK, or SKIP.

    Invalid or ambiguous phase state blocks; required phases require lower-level
    GREEN and exact current ledger freshness before proceeding. Otherwise the
    result dispatches `/test uat prepare <TICKET>` with a typed stale token.
    """
    if validate_trigger(trigger_document):
        return PhaseExitDecision("BLOCK", "", "trigger.invalid")
    phases = trigger_document.get("phases")
    if not isinstance(phases, list) or phase_id not in canonical_phase_ids:
        return PhaseExitDecision("BLOCK", "", "phase-exit.phase-id")
    matches = [
        phase for phase in phases
        if isinstance(phase, Mapping) and phase.get("phase_id") == phase_id
    ]
    if len(matches) != 1:
        return PhaseExitDecision("BLOCK", "", "phase-exit.phase-id")
    phase = matches[0]
    if phase.get("applicability") == "SKIP":
        skip_reason = phase.get("skip_reason")
        return PhaseExitDecision("SKIP", "", skip_reason if isinstance(skip_reason, str) else "phase-exit.phase-id")
    if not lower_level_green:
        return PhaseExitDecision("BLOCK", "", "phase-exit.lower-level")

    route = f"{CANONICAL_ROUTE} {ticket}"
    freshness = (
        ("package_hash", active_package_hash, "phase-exit.stale.package-hash"),
        ("script_hash", active_script_hash, "phase-exit.stale.script-hash"),
        ("certified_build_hash", active_build_hash, "phase-exit.stale.certified-build-hash"),
    )
    for field, active_value, token in freshness:
        value = ledger_state.get(field)
        if not isinstance(value, str) or not value or value != active_value:
            return PhaseExitDecision("DISPATCH", route, token)
    package_state = ledger_state.get("package_state")
    if package_state not in {"QA_VERIFIED", "ISSUED"}:
        return PhaseExitDecision("DISPATCH", route, "phase-exit.stale.package-state")
    latest_prewalk_receipt = ledger_state.get("latest_prewalk_receipt")
    if not isinstance(latest_prewalk_receipt, str) or not latest_prewalk_receipt:
        return PhaseExitDecision("DISPATCH", route, "phase-exit.stale.latest-prewalk-receipt")
    overall_verdict = ledger_state.get("overall_verdict")
    if overall_verdict not in {"PASS", "PASS_WITH_FINDINGS"}:
        return PhaseExitDecision("DISPATCH", route, "phase-exit.stale.overall-verdict")
    return PhaseExitDecision("PROCEED", "", "phase-exit.current")
