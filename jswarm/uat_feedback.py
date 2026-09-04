from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence, TypeAlias, cast

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jswarm.uat_round_materialize import (
    ValidationError as PackageValidationError,
    render_normalized_package_annex_bytes,
    render_owner_journey_bytes,
    validate_normalized_package,
)


SCHEMA_VERSION = "acceptance-quality@1"
ROUND_STATUSES = frozenset({"NOT_STARTED", "IN_PROGRESS", "COMPLETE", "FAILED"})
CORE_FIELDS = frozenset({"schema_version", "ticket", "generated_at", "latest_test_report_path", "overall_verdict", "processing_state", "coverage", "uat_package", "processing_log", "legacy_import_receipt"})
COVERAGE_FIELDS = frozenset({"functional_total", "functional_evidenced", "nfr_total", "nfr_evidenced", "structural_total", "structural_evidenced"})
UAT_PACKAGE_FIELDS = frozenset({"current_round_path", "package_id", "package_hash", "script_id", "script_hash", "certified_build_hash", "round_id", "latest_prewalk_receipt", "latest_feedback_path", "package_state"})
PROCESSING_LOG_FIELDS = frozenset({"run_id", "timestamp", "operation", "requirement_refs", "row_ids", "test_refs", "source_paths", "evidence_paths", "derived_verdict", "validator_verdict", "affected_row_ids", "round_id", "journey_ids", "package_hash", "script_hash", "certified_build_hash"})
UAT_PROCESSING_LOG_FIELDS = PROCESSING_LOG_FIELDS | frozenset({"scenario_outcomes", "finding_severities", "finding_dispositions"})
COLUMNS = ("Trace ID", "Type", "Source / requirement ref", "Atomic obligation", "Scenario/GWT or metric", "Expected outcome or threshold", "Primary collection or profile", "Executable test refs", "Scenario outcome / actual verdict", "Finding severity", "Evidence paths", "RED proof", "Implementation", "Disposition / next action")
ROW_TYPES = frozenset({"UAT", "NFR", "STRUCTURAL"})
VERDICTS = frozenset({"PASS", "FAIL", "BLOCKED", "NOT_RUN", "N/A", "INCOMPLETE", "PASS_WITH_FINDINGS"})
DERIVED_VERDICTS = frozenset({"PASS", "PASS_WITH_FINDINGS", "FAIL", "BLOCKED", "INCOMPLETE"})
VALIDATOR_VERDICTS = frozenset({"PASS", "BLOCKED"})
HEX = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
IMPLEMENTATION = re.compile(r"^(?:task#\d+|PR#\d+|[A-Z][A-Z0-9]+-\d+|[0-9a-f]{7,64})-[a-z0-9]+(?:-[a-z0-9]+)*$")
EXTRACTION_IDENTITY_RULES = (
    "feedback_id: MINT only for a genuinely new atomic concern.",
    "ticket, round_id, package_id, package_hash, sealed_payload_sha256, script_id, script_hash, certified_build_hash, journey_id, requirement_ref, source, scenario_id, uat_test_anchor, and atom_ids: COPY only from the inspected normalized package.",
    "primary-PE2E, RED-test, task, and implementation identities: COPY only when present in an inspected asset; otherwise OMIT WITH REASON.",
    "any optional unresolved identity: OMIT WITH REASON; never fabricate a placeholder.",
    "tool-owned receipt, processing-log row, ledger row, run ID, or success status: NEVER MINT OR WRITE; deterministic tooling owns them.",
    "never invent IDs/hashes/references; preserve exact journey/atom linkage; emit independent atomic candidates rather than an umbrella item.",
    "requirement_ref: COPY from the package; scenario_outcome, finding_severity, and finding_disposition: COPY only from structured scenario_results; record outcome and finding independently, never infer axes from prose; for N/A copy the exact structured reason into summary.",
    "for primary_pe2e, red_test, task, and implementation: use optional_identities only when inspected; otherwise put a non-empty reason under omissions.",
    "for every optional identity key, exactly one of optional_identities or omissions contains it; the maps are disjoint and their union is primary_pe2e, red_test, task, and implementation.",
    "tool-owned receipt, processing-log row, ledger row, run ID, success status, and processing decision: NEVER output.",
    "output exactly one JSON object matching the schema, with no Markdown fence or prose.",
)

_CANDIDATE_OPTIONAL_IDENTITIES = (
    "primary_pe2e", "red_test", "task", "implementation",
)
_CANDIDATE_REQUIRED = (
    "feedback_id", "ticket", "round_id", "package_id", "package_hash",
    "sealed_payload_sha256", "script_id", "script_hash", "certified_build_hash",
    "journey_id", "source", "scenario_id", "uat_test_anchor", "atom_ids",
    "requirement_ref", "scenario_outcome", "finding_severity", "finding_disposition", "summary", "observed", "expected", "optional_identities", "omissions",
)
_CANDIDATE_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "uat-feedback-candidates@1",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "candidates"],
    "properties": {
        "schema_version": {"const": "uat-feedback-candidates@1"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_CANDIDATE_REQUIRED),
                "properties": {
                    **{key: {"type": "string", "minLength": 1} for key in (
                        "feedback_id", "ticket", "round_id", "package_id", "script_id",
                        "journey_id", "source", "scenario_id", "uat_test_anchor", "requirement_ref", "summary",
                        "observed", "expected",
                    )},
                    "package_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "sealed_payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "script_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "certified_build_hash": {"type": "string", "pattern": "^[0-9a-f]{40}([0-9a-f]{24})?$"},
                    "atom_ids": {
                        "type": "array", "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "scenario_outcome": {"enum": ["PASS", "FAIL", "BLOCKED", "NOT_RUN", "N/A"]},
                    "finding_severity": {"enum": ["NONE", "MINOR", "MAJOR"]},
                    "finding_disposition": {"oneOf": [{"enum": ["SATISFIED", "OPEN", "ACCEPTED-BY-OWNER", "ANSWERED-NO-CHANGE", "REJECTED-BY-OWNER", "BLOCKED", "N/A"]}, {"type": "string", "pattern": "^DEFERRED→[A-Z][A-Z0-9_]*-\\d+-[a-z0-9]+(?:-[a-z0-9]+)*$"}]},
                    "optional_identities": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            key: {"type": "string", "minLength": 1}
                            for key in _CANDIDATE_OPTIONAL_IDENTITIES
                        },
                    },
                    "omissions": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            key: {"type": "string", "minLength": 1}
                            for key in _CANDIDATE_OPTIONAL_IDENTITIES
                        },
                    },
                },
            },
        },
    },
}

JsonValue: TypeAlias = str | int | float | bool | None | list[str] | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
_ANNEX_START = "BEGIN NORMALIZED PACKAGE\n```json\n"
_ANNEX_END = "\n```\nEND NORMALIZED PACKAGE"
_FEEDBACK_FIELDS = frozenset({
    "schema_version", "canonical_schema_version", "ticket", "round_id", "package_id",
    "package_hash", "sealed_payload_sha256", "script_id", "script_hash",
    "certified_build_hash", "folder_path", "app_url", "login", "observer_available",
    "observer_capture", "observer_fallback", "recovery_policy", "known_sources_checked",
    "generated_at", "processing_state", "feedback_verdict", "round_status", "scenario_results", "scenario_result_reasons",
})
_LEGACY_FEEDBACK_FIELDS = _FEEDBACK_FIELDS - {"round_status"}
_PARTIAL_PREFIX = "PARTIAL ASSET CHANGES: "


@dataclass(frozen=True)
class ValidationError(Exception):
    code: str

    def __str__(self) -> str:
        return f"ledger.invalid.{self.code}"


def _mapping(value, code: str):
    if not isinstance(value, dict):
        raise ValidationError(code)
    return value


def _nonempty(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(code)
    return value


def _frontmatter(text: str):
    if not text.startswith("---\n"):
        raise ValidationError("frontmatter")
    try:
        _empty, raw, body = text.split("---", 2)
        return _mapping(yaml.safe_load(raw), "frontmatter"), body
    except (ValueError, yaml.YAMLError) as error:
        raise ValidationError("frontmatter") from error


def _exact_keys(value, expected: frozenset[str], code: str):
    mapping = _mapping(value, code)
    if set(mapping) != expected:
        raise ValidationError(code)
    return mapping


def _table(body: str) -> list[list[str]]:
    lines = [line for line in body.splitlines() if line.startswith("|")]
    if len(lines) < 3:
        raise ValidationError("table")
    columns = [cell.strip() for cell in lines[0].strip("|").split("|")]
    if tuple(columns) != COLUMNS:
        raise ValidationError("columns")
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]
    if any(len(row) != len(COLUMNS) for row in rows):
        if any("N/A reason:" in line and len(row) > 1 and row[1] == "UAT" for line, row in zip(lines[2:], rows, strict=True)):
            raise ValidationError("na-reason")
        raise ValidationError("rows")
    if not rows:
        raise ValidationError("rows")
    return rows


def _validate_coverage(value) -> None:
    coverage = _exact_keys(value, COVERAGE_FIELDS, "coverage")
    for prefix in ("functional", "nfr", "structural"):
        total = coverage[f"{prefix}_total"]
        evidenced = coverage[f"{prefix}_evidenced"]
        if not isinstance(total, int) or not isinstance(evidenced, int) or total < 0 or evidenced < 0 or evidenced > total:
            raise ValidationError("coverage")


def _validate_package(value, ticket: str) -> None:
    package = _exact_keys(value, UAT_PACKAGE_FIELDS, "uat-package")
    if package["package_state"] not in {"DRAFT", "ISSUED", "EXPIRED", "N/A"}:
        raise ValidationError("uat-package-state")
    for key in UAT_PACKAGE_FIELDS - {"package_state"}:
        text = _nonempty(package[key], "uat-package")
        if package["package_state"] == "ISSUED" and key.endswith("hash") and not HEX.fullmatch(text):
            raise ValidationError("uat-package-hash")
    if ticket not in _nonempty(package["current_round_path"], "uat-package"):
        raise ValidationError("ticket-path")


def _validate_log(value, row_types: Mapping[str, str], *, allow_empty_pre_round: bool = False) -> None:
    if not isinstance(value, list) or (not value and not allow_empty_pre_round):
        raise ValidationError("processing-log")
    for entry in value:
        if not isinstance(entry, dict):
            raise ValidationError("processing-log")
        row_ids = entry.get("row_ids") if isinstance(entry, dict) else None
        if not isinstance(row_ids, list) or not all(isinstance(row_id, str) and row_id in row_types for row_id in row_ids):
            raise ValidationError("processing-log")
        is_uat = all(row_types[row_id] == "UAT" for row_id in row_ids)
        expected = UAT_PROCESSING_LOG_FIELDS if is_uat else PROCESSING_LOG_FIELDS
        record = _exact_keys(entry, expected, "processing-log")
        if record["derived_verdict"] not in DERIVED_VERDICTS or record["validator_verdict"] not in VALIDATOR_VERDICTS:
            raise ValidationError("processing-log-verdict")
        for key in ("run_id", "timestamp", "operation", "round_id", "package_hash", "script_hash", "certified_build_hash"):
            _nonempty(record[key], "processing-log")
        for key in ("requirement_refs", "row_ids", "test_refs", "source_paths", "evidence_paths", "affected_row_ids", "journey_ids"):
            if not isinstance(record[key], list) or not all(isinstance(item, str) and item for item in record[key]):
                raise ValidationError("processing-log")
        if expected == UAT_PROCESSING_LOG_FIELDS:
            aligned = ("requirement_refs", "row_ids", "affected_row_ids", "journey_ids", "scenario_outcomes", "finding_severities", "finding_dispositions")
            if not all(isinstance(record[key], list) and len(record[key]) == len(record["row_ids"]) for key in aligned):
                raise ValidationError("processing-log")


RESULT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/jTest/uat/feedback-result-contract.json"
)


def feedback_result_contract() -> dict[str, object]:
    """Load the single versioned UAT feedback-result contract master."""
    try:
        contract = json.loads(RESULT_CONTRACT_PATH.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise PackageValidationError("feedback.result_contract") from error
    if not isinstance(contract, dict):
        raise PackageValidationError("feedback.result_contract")
    return contract


def _assessment_outcome(assessment: object) -> str | None:
    """Resolve canonical and legacy input assessments without rewriting evidence."""
    if not isinstance(assessment, str):
        return None
    contract = feedback_result_contract()
    aliases = contract.get("assessment_aliases")
    outcomes = contract.get("assessment_outcomes")
    if not isinstance(aliases, Mapping) or not isinstance(outcomes, Mapping):
        raise PackageValidationError("feedback.result_contract")
    canonical = aliases.get(assessment, assessment)
    outcome = outcomes.get(canonical)
    return outcome if isinstance(outcome, str) else None


def _canonical_assessment(assessment: object) -> str | None:
    if not isinstance(assessment, str):
        return None
    aliases = feedback_result_contract().get("assessment_aliases")
    if not isinstance(aliases, Mapping):
        raise PackageValidationError("feedback.result_contract")
    canonical = aliases.get(assessment, assessment)
    return canonical if isinstance(canonical, str) else None


def _contract_cells(key: str) -> set[tuple[str, str, str]]:
    cells = feedback_result_contract().get(key)
    if not isinstance(cells, list):
        raise PackageValidationError("feedback.result_contract")
    triples = {
        tuple(cell) for cell in cells
        if isinstance(cell, list) and len(cell) == 3 and all(isinstance(value, str) for value in cell)
    }
    if len(triples) != len(cells):
        raise PackageValidationError("feedback.result_contract")
    return cast(set[tuple[str, str, str]], triples)


def _empty_classification_value(value: object) -> object:
    """Treat legacy empty classification strings as absent owner choices."""
    return None if value == "" else value


def project_step_entry_to_ledger_triple(entry: Mapping[str, object]) -> tuple[object, object, object]:
    """Project incomplete owner classifications onto one legal ledger triple."""
    assessment_input = _empty_classification_value(entry.get("assessment"))
    assessment = _canonical_assessment(assessment_input)
    if assessment is None and assessment_input is None:
        assessment = "PASS"
    severity = _empty_classification_value(entry.get("finding_severity"))
    disposition = _empty_classification_value(entry.get("finding_disposition"))
    if severity is None and disposition is None:
        projections = feedback_result_contract().get("no_finding_ledger_projection")
        if not isinstance(projections, Mapping) or assessment is None:
            raise PackageValidationError("feedback.result_contract")
        projected = projections.get(assessment)
        if (not isinstance(projected, list) or len(projected) != 3
                or not all(isinstance(value, str) for value in projected)):
            raise PackageValidationError("feedback.step.triple")
        return tuple(projected)
    outcome = _assessment_outcome(assessment)
    cells = feedback_result_contract().get("cells")
    if outcome is None or not isinstance(cells, list):
        raise PackageValidationError("feedback.step.triple")
    for cell in cells:
        if (isinstance(cell, list) and len(cell) == 3
                and all(isinstance(value, str) for value in cell)
                and cell[0] == outcome
                and (severity is None or cell[1] == severity)
                and (disposition is None or cell[2] == disposition)):
            return tuple(cell)
    raise PackageValidationError("feedback.step.triple")


def _allowed_triple(row_type: str, outcome: str, severity: str, disposition: str) -> bool:
    deferred = bool(re.fullmatch(r"DEFERRED→[A-Z][A-Z0-9_]*-\d+-[a-z0-9]+(?:-[a-z0-9]+)*", disposition))
    if row_type == "UAT":
        return ((outcome, severity, disposition) in _contract_cells("cells")
                or (outcome, severity) == ("PASS", "MINOR") and deferred)
    return (outcome, severity, disposition) in {
        ("PASS", "NONE", "SATISFIED"), ("FAIL", "NONE", "OPEN"),
        ("BLOCKED", "NONE", "BLOCKED"), ("NOT_RUN", "NONE", "OPEN"), ("N/A", "NONE", "N/A"),
    }


def _scenario_result_is_valid(
    row_type: str,
    outcome: object,
    severity: object,
    disposition: object,
    reason: object = None,
    *,
    enforce_na_reason: bool = False,
) -> bool:
    """Apply the canonical result-triple and optional N/A-reason contract once."""
    if not all(isinstance(value, str) for value in (outcome, severity, disposition)):
        return False
    if not _allowed_triple(row_type, outcome, severity, disposition):
        return False
    if not enforce_na_reason:
        return True
    is_na = (outcome, severity, disposition) == ("N/A", "NONE", "N/A")
    if not is_na:
        return reason in {None, ""}
    return (
        isinstance(reason, str)
        and bool(reason.strip())
        and len(reason) <= 500
        and not any(token in reason for token in ("|", "\n", "\r", "\x00"))
    )


def _validate_legacy(value, body: str) -> None:
    if value is None or value == "N/A":
        return
    receipt = _exact_keys(value, frozenset({"source_path", "source_sha256"}), "legacy-import")
    if not _nonempty(receipt["source_path"], "legacy-import.source_path").endswith(".uat-traceability.md"):
        raise ValidationError("legacy-import.source_path")
    source_sha256 = receipt["source_sha256"]
    if not isinstance(source_sha256, str):
        raise ValidationError("legacy-import.source_sha256.non-string")
    if not HEX.fullmatch(_nonempty(source_sha256, "legacy-import.source_sha256")):
        raise ValidationError("legacy-import.source_sha256")
    if body.count(".uat-traceability.md"):
        raise ValidationError("legacy-write")


def _validate_rows(rows: list[list[str]]) -> tuple[str, bool, dict[str, int]]:
    has_uat = False
    groups: dict[str, list[tuple[str, str, str]]] = {"functional": [], "nfr": [], "structural": []}
    for row in rows:
        trace_id, row_type, source, obligation, scenario, expected, profile, tests, actual, severity, evidence, red_proof, implementation, disposition = row
        if row_type not in ROW_TYPES:
            raise ValidationError("row-type")
        if row_type == "NFR" and (not tests or (actual != "N/A" and not evidence)):
            raise ValidationError("nfr-evidence")
        if not all((trace_id, source, obligation, scenario, expected, profile, tests, actual, evidence, red_proof, implementation, disposition)):
            raise ValidationError("row")
        system_incomplete = (
            row_type == "UAT"
            and (actual, severity, disposition) in _contract_cells("system_cells")
            and obligation.startswith("feedback:system:")
        )
        if actual not in {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "N/A", "INCOMPLETE"}:
            if actual in {"PASS-WITH-MINOR-DEFECTS", "PASS-WITH-FINDINGS"}:
                raise ValidationError("row-combination")
            raise ValidationError("verdict")
        if row_type == "NFR" and actual == "N/A" and disposition != "N/A":
            raise ValidationError("nfr-evidence")
        if disposition not in {"SATISFIED", "OPEN", "ACCEPTED-BY-OWNER", "ANSWERED-NO-CHANGE", "REJECTED-BY-OWNER", "BLOCKED", "N/A"} and not disposition.startswith("DEFERRED"):
            raise ValidationError("disposition")
        if not system_incomplete and not _allowed_triple(row_type, actual, severity, disposition):
            raise ValidationError("row-combination")
        if not IMPLEMENTATION.fullmatch(implementation):
            raise ValidationError("implementation")
        if row_type == "UAT":
            na_prefix = "feedback:"
            na_marker = "; N/A reason: "
            if (actual, severity, disposition) == ("N/A", "NONE", "N/A"):
                if not obligation.startswith(na_prefix) or obligation.count(na_marker) != 1:
                    raise ValidationError("na-reason")
                identity, reason = obligation.split(na_marker, 1)
                if not re.fullmatch(r"feedback:[^:\s|\x00\r\n]+:[^;\s|\x00\r\n]+", identity) or not reason or any(char in reason for char in "|\x00\r\n"):
                    raise ValidationError("na-reason")
            elif na_marker in obligation:
                raise ValidationError("na-reason")
        elif actual == "N/A" and disposition == "N/A" and "reason" not in obligation.lower():
            raise ValidationError("na-reason")
        if row_type == "UAT":
            has_uat = True
            if not scenario.startswith("UAT-"):
                raise ValidationError("uat-extension")
        if row_type == "NFR":
            if not (expected.startswith("<=") or expected.startswith(">=") or expected.startswith("<") or expected.startswith(">")):
                raise ValidationError("nfr-threshold")
        prefix = {"UAT": "functional", "NFR": "nfr", "STRUCTURAL": "structural"}[row_type]
        groups[prefix].append((source, actual, disposition))
    evidence_counts: dict[str, int] = {}
    has_fail = has_blocked = has_incomplete = has_findings = False
    for prefix, grouped_rows in groups.items():
        by_ref: dict[str, list[tuple[str, str]]] = {}
        for ref, outcome, disposition in grouped_rows:
            by_ref.setdefault(ref, []).append((outcome, disposition))
            has_fail = has_fail or outcome == "FAIL"
            has_blocked = has_blocked or outcome == "BLOCKED"
            has_incomplete = has_incomplete or outcome == "NOT_RUN" or disposition == "OPEN"
            has_findings = has_findings or (prefix == "functional" and outcome == "PASS" and disposition in {"ACCEPTED-BY-OWNER"} or prefix == "functional" and outcome == "PASS" and disposition.startswith("DEFERRED→"))
        evidence_counts[prefix] = sum(bool(values) and all(outcome == "PASS" and disposition in {"SATISFIED", "ANSWERED-NO-CHANGE", "REJECTED-BY-OWNER", "ACCEPTED-BY-OWNER"} or outcome == "PASS" and disposition.startswith("DEFERRED→") for outcome, disposition in values if outcome != "N/A") and any(outcome != "N/A" for outcome, _ in values) for values in by_ref.values())
    verdict = "FAIL" if has_fail else "BLOCKED" if has_blocked else "INCOMPLETE" if has_incomplete else "PASS_WITH_FINDINGS" if has_findings else "PASS"
    return verdict, has_uat, evidence_counts


def validate_normalized_package_manifest(package: Mapping[str, object]) -> list[str]:
    error = validate_normalized_package(package)
    return [] if error is None else [error]


def _validated_package(package: Mapping[str, object]) -> None:
    errors = validate_normalized_package_manifest(package)
    if errors:
        raise PackageValidationError(errors[0])


def _validate_feedback_envelope(
    generated_at: str,
    processing_state: str,
    feedback_verdict: str,
    tool_owned_receipt: str,
    *,
    allow_terminal: bool = False,
) -> None:
    if not isinstance(generated_at, str) or not generated_at:
        raise PackageValidationError("feedback.envelope.generated_at")
    try:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise PackageValidationError("feedback.envelope.generated_at") from error
    if parsed.tzinfo is None or not generated_at.endswith("Z"):
        raise PackageValidationError("feedback.envelope.generated_at")
    if processing_state != "UNPROCESSED" and not (allow_terminal and processing_state == "PROCESSED"):
        raise PackageValidationError("feedback.envelope.processing_state")
    if feedback_verdict != "N/A" and not (allow_terminal and processing_state == "PROCESSED"):
        raise PackageValidationError("feedback.envelope.feedback_verdict")
    if not isinstance(tool_owned_receipt, str) or not tool_owned_receipt:
        raise PackageValidationError("feedback.envelope.tool_owned_receipt")


def render_feedback_document(
    package: Mapping[str, object],
    *,
    generated_at: str,
    processing_state: str = "UNPROCESSED",
    feedback_verdict: str = "N/A",
    tool_owned_receipt: str = "PENDING",
    round_status: str = "NOT_STARTED",
) -> bytes:
    _validated_package(package)
    if round_status not in ROUND_STATUSES:
        raise PackageValidationError("feedback.envelope.round_status")
    _validate_feedback_envelope(
        generated_at, processing_state, feedback_verdict, tool_owned_receipt,
    )
    projection = package["feedback"]
    if not isinstance(projection, Mapping):
        raise PackageValidationError("package.invalid.projection_drift")
    observer = package["observer"]
    if not isinstance(observer, Mapping):
        raise PackageValidationError("package.invalid.observer")
    sources = package["known_sources_checked"]
    if not isinstance(sources, list):
        raise PackageValidationError("package.invalid.known_sources_checked")
    journeys = package["journeys"]
    if not isinstance(journeys, list):
        raise PackageValidationError("package.invalid.journeys")
    results = {
        str(journey["journey_id"]): {
            "requirement_ref": journey["requirement_ref"], "scenario_outcome": "NOT_RUN",
            "finding_severity": "NONE", "finding_disposition": "OPEN",
        }
        for journey in journeys if isinstance(journey, dict)
    }
    frontmatter = {
        "schema_version": "uat-feedback@1",
        "canonical_schema_version": package["schema_version"],
        "ticket": package["ticket"],
        "round_id": package["round_id"],
        "package_id": package["package_id"],
        "package_hash": package["package_hash"],
        "sealed_payload_sha256": package["sealed_payload_sha256"],
        "script_id": package["script_id"],
        "script_hash": package["script_hash"],
        "certified_build_hash": package["certified_build_hash"],
        "folder_path": package["folder_path"],
        "app_url": package["app_url"],
        "login": package["login"],
        "observer_available": observer["available"],
        "observer_capture": observer["capture"],
        "observer_fallback": observer["fallback"],
        "recovery_policy": package["recovery_policy"],
        # This remains the existing scalar projection, rather than changing the
        # feedback-envelope contract to a list during a serialization repair.
        "known_sources_checked": ", ".join(str(source) for source in sources),
        "generated_at": generated_at,
        "processing_state": processing_state,
        "feedback_verdict": feedback_verdict,
        "round_status": round_status,
        "scenario_results": results,
        "scenario_result_reasons": {},
    }
    document = (
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, width=2 ** 20).rstrip()
        + "\n---\n\n# UAT feedback\n\n"
        + f"Tool-owned receipt: {tool_owned_receipt}\n\n"
        + "## Canonical journey walk\n\n"
    ).encode("utf-8")
    return (
        document
        + render_owner_journey_bytes(projection)
        + _render_feedback_entry_markers(package, results)
        + b"\n"
        + render_normalized_package_annex_bytes(package)
    )


def _render_feedback_entry_markers(package: Mapping[str, object], results: Mapping[str, object]) -> bytes:
    journeys = package.get("journeys")
    if not isinstance(journeys, list):
        raise PackageValidationError("package.invalid.journeys")
    if package.get("schema_version") == "uat-canonical-package@2":
        markers: list[str] = []
        for journey in journeys:
            if not isinstance(journey, Mapping):
                raise PackageValidationError("package.invalid.journey")
            for step in journey.get("steps", []):
                if not isinstance(step, Mapping) or not isinstance(step.get("step_id"), str):
                    raise PackageValidationError("package.invalid.step")
                entry = {"complete": False, "assessment": None, "finding_severity": None, "finding_disposition": None, "observed": "", "comment": ""}
                markers.extend((
                    f'<!-- uat-feedback-entry:v3 step_id="{step["step_id"]}" -->',
                    json.dumps(entry, sort_keys=True, separators=(",", ":")),
                    "<!-- /uat-feedback-entry:v3 -->",
                ))
        return ("\n".join(markers) + "\n").encode("utf-8")
    markers: list[str] = []
    for journey in journeys:
        if not isinstance(journey, Mapping) or not isinstance(journey.get("journey_id"), str):
            raise PackageValidationError("package.invalid.journey")
        journey_id = journey["journey_id"]
        result = results.get(journey_id)
        if not isinstance(result, Mapping):
            raise PackageValidationError("feedback.marker.result")
        entry = {
            "scenario_outcome": result.get("scenario_outcome"),
            "finding_severity": result.get("finding_severity"),
            "finding_disposition": result.get("finding_disposition"),
            "reason": "",
            "comment": "",
        }
        markers.extend((
            f'<!-- uat-feedback-entry:v1 journey_id="{journey_id}" -->',
            json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/"),
            "<!-- /uat-feedback-entry:v1 -->",
        ))
    return ("\n".join(markers) + "\n").encode("utf-8")


class OwnerWalkGenerationError(ValueError):
    """Owner-walk projection defect; ``str(error)`` and ``error.token`` are the exact blocking token."""

    def __init__(self, token: str) -> None:
        super().__init__(token)
        self.token = token


_OWNER_WALK_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_OWNER_WALK_NOTES_LINE = "_" * 72
_OWNER_WALK_KNOWN_HEADING = "## Before you start — known conditions (tick if you see them; none is a defect)"
_OWNER_WALK_SETUP_HEADING = "## Setup / reachability"
_OWNER_WALK_STEPS_TABLE_HEADER = "| # | Do this | You should see | ✓ | Your notes |"
_OWNER_WALK_STEPS_TABLE_DELIMITER = "|---|---|---|---|---|"
_OWNER_WALK_FAIL_HEADING = "**❌ If you see any of these, this journey failed:**"
_OWNER_WALK_RESULT_LINE = "**YOUR RESULT: ☐ PASS ☐ FAIL ☐ BLOCKED**"
_OWNER_WALK_FEEDBACK_HEADING = "**YOUR FEEDBACK:**"


def _owner_walk_inline(value: str) -> str:
    """Escape a non-table inline value: line breaks become one space, nothing else changes."""
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _owner_walk_cell(value: str) -> str:
    """Escape a Markdown table cell exactly per the frozen owner-walk byte contract."""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "<br>").replace("\\", "\\\\").replace("|", "\\|")


def _owner_walk_sequence(value: object) -> tuple[object, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return tuple(value)


_OWNER_WALK_PACKAGE_ROOT_FIELDS = ("ticket", "certified_build_hash", "app_url", "login", "round_id")


def _owner_walk_string(value: object, token: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackageValidationError(token)
    return value


def _owner_walk_string_list(value: object, token: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PackageValidationError(token)
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise PackageValidationError(token)
    return [item for item in value if isinstance(item, str)]


def _owner_walk_package_check(package: Mapping[str, object]) -> None:
    """Structurally validate only the package fields this projection consumes.

    Sealed-identity/hash verification stays the caller's job (the PREP flow already
    validated the package before issuing); token-table row 1 preserves the caller's
    validator token rather than making the renderer re-verify hashes. Structural
    defects in consumed fields raise the existing ``package.*`` vocabulary — e.g. an
    empty ticket yields exactly ``package.invalid.ticket``, the same token the full
    validator produces.
    """
    for field in _OWNER_WALK_PACKAGE_ROOT_FIELDS:
        _owner_walk_string(package.get(field), f"package.invalid.{field}")
    journeys_value = package.get("journeys")
    if not isinstance(journeys_value, list) or not journeys_value:
        raise PackageValidationError("package.invalid.journeys")
    for journey_value in journeys_value:
        if not isinstance(journey_value, Mapping):
            raise PackageValidationError("package.invalid.journey")
        journey = cast(Mapping[str, object], journey_value)
        for field in ("journey_id", "uat_test_anchor"):
            _owner_walk_string(journey.get(field), f"package.missing.{field}")
        _owner_walk_string_list(journey.get("actions"), "package.invalid.actions")
        outcomes_value = journey.get("outcomes")
        if not isinstance(outcomes_value, list) or not outcomes_value:
            raise PackageValidationError("package.invalid.outcome_atom")
        for outcome_value in outcomes_value:
            if not isinstance(outcome_value, Mapping):
                raise PackageValidationError("package.invalid.outcome_atom")
            outcome = cast(Mapping[str, object], outcome_value)
            _owner_walk_string(outcome.get("expected"), "package.invalid.outcome_atom")
            _owner_walk_string_list(outcome.get("fail_if"), "package.invalid.outcome_atom")
        known_items_value = journey.get("known_items")
        if not isinstance(known_items_value, list):
            raise PackageValidationError("package.invalid.known_item_provenance")
        for item_value in known_items_value:
            if not isinstance(item_value, Mapping):
                raise PackageValidationError("package.invalid.known_item_provenance")
            _owner_walk_string(cast(Mapping[str, object], item_value).get("text"), "package.invalid.known_item_provenance")

def render_owner_walk_document(
    package: Mapping[str, object],
    *,
    generated_at: str,
    owner_walk: Mapping[str, object],
) -> bytes:
    """Render the deterministic human owner-walk projection of a validated canonical package.

    Pure function: package + chain projection + ``generated_at`` are the complete inputs;
    identical inputs produce identical UTF-8 bytes with LF newlines and one final newline.
    Raises :class:`OwnerWalkGenerationError` (``str(error)`` is the exact token) before
    returning bytes when any required element is missing or mismatched.
    """
    _owner_walk_package_check(package)
    if not isinstance(generated_at, str) or _OWNER_WALK_UTC.fullmatch(generated_at) is None:
        raise OwnerWalkGenerationError("walk.generation.blocked.generated_at")
    if not isinstance(owner_walk, Mapping):
        raise OwnerWalkGenerationError("walk.generation.blocked.walk-script")
    source_path = owner_walk.get("source_path")
    if not isinstance(source_path, str) or not source_path.strip():
        raise OwnerWalkGenerationError("walk.generation.blocked.walk-script")
    if "journeys" not in owner_walk or "setup_steps" not in owner_walk:
        raise OwnerWalkGenerationError("walk.generation.blocked.walk-script")
    setup_raw = _owner_walk_sequence(owner_walk["setup_steps"])
    if setup_raw is None or not setup_raw or not all(
        isinstance(step, str) and step.strip() for step in setup_raw
    ):
        raise OwnerWalkGenerationError("walk.generation.blocked.setup-steps")
    setup_steps = tuple(cast(str, step) for step in setup_raw)

    journeys_value = package["journeys"]
    assert isinstance(journeys_value, list)
    package_journeys = [journey for journey in journeys_value if isinstance(journey, Mapping)]
    derived_raw = _owner_walk_sequence(owner_walk["journeys"])
    if derived_raw is None:
        raise OwnerWalkGenerationError("walk.generation.blocked.walk-script")
    derived_by_anchor: dict[str, Mapping[str, object]] = {}
    for entry in derived_raw:
        if not isinstance(entry, Mapping):
            raise OwnerWalkGenerationError("walk.generation.blocked.walk-script")
        anchor = entry.get("uat_test_anchor")
        if not isinstance(anchor, str) or not anchor.strip():
            raise OwnerWalkGenerationError("walk.generation.blocked.journey-set")
        if anchor in derived_by_anchor:
            raise OwnerWalkGenerationError("walk.generation.blocked.journey-set")
        derived_by_anchor[anchor] = entry
    package_anchors = {str(journey["uat_test_anchor"]) for journey in package_journeys}
    if set(derived_by_anchor) != package_anchors:
        raise OwnerWalkGenerationError("walk.generation.blocked.journey-set")

    for journey in package_journeys:
        journey_id = str(journey["journey_id"])
        entry = derived_by_anchor[str(journey["uat_test_anchor"])]
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            raise OwnerWalkGenerationError(f"walk.generation.blocked.title.{journey_id}")
        actions_value = journey["actions"]
        assert isinstance(actions_value, list)
        actions = [str(action) for action in actions_value]
        steps_raw = _owner_walk_sequence(entry.get("steps"))
        if steps_raw is None or not steps_raw or not all(isinstance(step, Mapping) for step in steps_raw):
            raise OwnerWalkGenerationError(f"walk.generation.blocked.steps.{journey_id}")
        ordinals = tuple(step.get("ordinal") for step in steps_raw if isinstance(step, Mapping))
        if ordinals != tuple(range(1, len(steps_raw) + 1)) or len(steps_raw) != len(actions):
            raise OwnerWalkGenerationError(f"walk.generation.blocked.steps.{journey_id}")
        for step, action, ordinal in zip(steps_raw, actions, range(1, len(actions) + 1), strict=True):
            assert isinstance(step, Mapping)
            step_action = step.get("action")
            if not isinstance(step_action, str) or not step_action or step_action != action:
                raise OwnerWalkGenerationError(f"walk.generation.blocked.action.{journey_id}.{ordinal}")
            expected = step.get("expected")
            if not isinstance(expected, str) or not expected.strip():
                raise OwnerWalkGenerationError(f"walk.generation.blocked.expected.{journey_id}.{ordinal}")
        frames_raw = _owner_walk_sequence(entry.get("repeat_frames"))
        if frames_raw is None or not all(isinstance(frame, str) for frame in frames_raw):
            raise OwnerWalkGenerationError(f"walk.generation.blocked.repeat-frames.{journey_id}")

    known_texts: list[str] = []
    seen_known: set[str] = set()
    for journey in package_journeys:
        known_items = journey["known_items"]
        assert isinstance(known_items, list)
        for item in known_items:
            if not isinstance(item, Mapping):
                continue
            text = str(item["text"])
            if text not in seen_known:
                seen_known.add(text)
                known_texts.append(text)

    lines: list[str] = [
        f"# {package['ticket']} — Your UAT walk",
        "",
        f"**Build:** {package['certified_build_hash']} · **App:** {package['app_url']} · **Login:** {package['login']} · **Round:** {package['round_id']} · **Prepared:** {generated_at}",
    ]
    if known_texts:
        lines.extend(("", _OWNER_WALK_KNOWN_HEADING, ""))
        lines.extend(f"- [ ] {_owner_walk_inline(text)}" for text in known_texts)
    lines.extend(("", _OWNER_WALK_SETUP_HEADING, ""))
    lines.extend(f"{ordinal}. {_owner_walk_inline(step)}" for ordinal, step in enumerate(setup_steps, 1))
    total = len(package_journeys)
    for index, journey in enumerate(package_journeys, 1):
        entry = derived_by_anchor[str(journey["uat_test_anchor"])]
        title = cast(str, entry["title"])
        steps_raw = cast(tuple[Mapping[str, object], ...], _owner_walk_sequence(entry["steps"]))
        frames_raw = cast(tuple[str, ...], _owner_walk_sequence(entry["repeat_frames"]))
        outcomes_value = journey["outcomes"]
        assert isinstance(outcomes_value, list)
        proves = " · ".join(_owner_walk_inline(str(outcome["expected"])) for outcome in outcomes_value if isinstance(outcome, Mapping))
        fail_clauses = [
            str(clause)
            for outcome in outcomes_value if isinstance(outcome, Mapping)
            for clause in cast(list[str], outcome["fail_if"]) if isinstance(outcome.get("fail_if"), list)
        ]
        lines.extend((
            "",
            f"## Journey {index} of {total} — {_owner_walk_inline(title)}",
            "",
            f"> **What this proves:** {proves}",
            "",
            _OWNER_WALK_STEPS_TABLE_HEADER,
            _OWNER_WALK_STEPS_TABLE_DELIMITER,
        ))
        for step in steps_raw:
            lines.append(f"| {step['ordinal']} | {_owner_walk_cell(str(step['action']))} | {_owner_walk_cell(str(step['expected']))} | ☐ | |")
        lines.extend(("", _OWNER_WALK_FAIL_HEADING, ""))
        lines.extend(f"- {_owner_walk_inline(clause)}" for clause in fail_clauses)
        if len(frames_raw) == 1:
            lines.extend(("", f"**Repeat frame:** ☐ {_owner_walk_inline(frames_raw[0])}"))
        elif len(frames_raw) > 1:
            lines.extend((
                "",
                "| " + " | ".join(_owner_walk_inline(frame) for frame in frames_raw) + " |",
                "|" + "---|" * len(frames_raw),
                "| " + " | ".join("☐" for _ in frames_raw) + " |",
            ))
        lines.extend((
            "",
            _OWNER_WALK_RESULT_LINE,
            "",
            _OWNER_WALK_FEEDBACK_HEADING,
            "",
            _OWNER_WALK_NOTES_LINE,
            _OWNER_WALK_NOTES_LINE,
        ))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _candidate_schema_for(package: Mapping[str, object]) -> dict[str, object]:
    schema = deepcopy(_CANDIDATE_SCHEMA)
    if package.get("schema_version") == "uat-canonical-package@2":
        candidate = cast(dict[str, object], cast(dict[str, object], schema["properties"])["candidates"])["items"]
        assert isinstance(candidate, dict)
        required = cast(list[str], candidate["required"])
        required[:] = [field for field in required if field not in {"scenario_id"}]
        required.extend(("step_id", "scenario_links"))
        properties = cast(dict[str, object], candidate["properties"])
        properties.pop("scenario_id", None)
        properties.update({
            "step_id": {"type": "string", "minLength": 1},
            "scenario_links": {"type": "array", "minItems": 1, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["scenario_id", "gwt_refs"],
                "properties": {
                    "scenario_id": {"type": "string", "minLength": 1},
                    "gwt_refs": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
                },
            }},
        })
    return schema


def build_extraction_prompt(
    package: Mapping[str, object], *, feedback: bytes | None = None,
    eligible_step_ids: Sequence[str] | None = None,
) -> str:
    _validated_package(package)
    prompt_package = deepcopy(package)
    eligible: set[str] | None = None
    if package.get("schema_version") == "uat-canonical-package@2":
        if feedback is not None:
            projection = parse_feedback_projection(feedback)
            entries = projection.get("step_entries")
            stop = projection.get("walk_stop")
            if not isinstance(entries, Mapping):
                raise PackageValidationError("feedback.step")
            licensed = set(stop.get("unreached_step_ids", [])) if isinstance(stop, Mapping) else set()
            eligible = {step_id for step_id, entry in entries.items() if isinstance(entry, Mapping) and entry.get("complete")} | licensed
        elif eligible_step_ids is not None:
            eligible = set(eligible_step_ids)
        if eligible is not None:
            def _prune_ineligible_steps(value: object) -> None:
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "steps" and isinstance(child, list):
                            value[key] = [step for step in child if isinstance(step, dict) and step.get("step_id") in eligible]
                        else:
                            _prune_ineligible_steps(child)
                elif isinstance(value, list):
                    for child in value:
                        _prune_ineligible_steps(child)
            _prune_ineligible_steps(prompt_package)
    package_json = json.dumps(prompt_package, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    schema_json = json.dumps(_candidate_schema_for(package), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    lineage = (
        "validate exact step → journey → scenario → GWT lineage",
        "step_id: COPY exactly from the eligible canonical step; NEVER MINT, infer, merge, or omit.",
        "scenario_links: DEEP-COPY the complete ordered array of {scenario_id, gwt_refs[]} objects from that exact step; NEVER MINT, infer, flatten, merge, reorder, or omit.",
        "Do not output singular scenario_id or top-level gwt_refs for a v2 candidate.",
    ) if package.get("schema_version") == "uat-canonical-package@2" else ()
    eligibility_rule = (
        "Emit exactly one candidate for each eligible step_id: " + json.dumps(sorted(eligible)),
        "For every ineligible step, OMIT it: do not mint, copy, or infer a candidate.",
    ) if eligible is not None else ()
    stop_rules = (
        "uat-walk-stop:v1: the feedback document may carry one round-level stopped-walk marker naming a halting step and an exact unreached_step_ids list.",
        "A stop-licensed unreached step is NOT_OBSERVED and was never exercised: emit NOT_RUN / NONE / OPEN and COPY its observed text exactly.",
        "NEVER MINT a pass, skip, N/A, SATISFIED, or any behavior description for an unreached step, and never invent a stop that the marker does not declare.",
        "Every other step, including the halting step, must already be complete; copy its assessment-derived triple and observed text exactly.",
    ) if package.get("schema_version") == "uat-canonical-package@2" else ()
    return "\n".join((
        "Extract feedback only from this inspected canonical UAT package:",
        package_json,
        "BEGIN UAT FEEDBACK CANDIDATE SCHEMA\n```json\n" + schema_json + "\n```\nEND UAT FEEDBACK CANDIDATE SCHEMA",
        *EXTRACTION_IDENTITY_RULES,
        *lineage,
        *eligibility_rule,
        *stop_rules,
    ))


def _empty_ledger_table(body: str) -> bool:
    lines = [line for line in body.splitlines() if line.startswith("|")]
    return len(lines) == 2 and tuple(cell.strip() for cell in lines[0].strip("|").split("|")) == COLUMNS


def _first_processing_ledger(ledger: Mapping[str, JsonValue], package: JsonObject) -> dict[str, JsonValue] | None:
    """Normalize the documented two-field first-round seed; never mint a receipt."""
    if set(ledger) != {"ticket", "uat_package"}:
        return None
    ticket = ledger.get("ticket")
    package_state = ledger.get("uat_package")
    if not isinstance(ticket, str) or not isinstance(package_state, dict):
        return None
    try:
        _validate_package(package_state, ticket)
    except ValidationError:
        return None
    identities = ("ticket", "package_id", "package_hash", "script_id", "script_hash", "certified_build_hash", "round_id")
    if any((ticket if key == "ticket" else package_state.get(key)) != package.get(key) for key in identities):
        return None
    if package_state["package_state"] not in {"DRAFT", "ISSUED"}:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "ticket": ticket,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_test_report_path": f".jswarm/plans/{ticket}/{ticket}.uat.report.md",
        "overall_verdict": "DRAFT",
        "processing_state": "PENDING",
        "coverage": {
            "functional_total": 0, "functional_evidenced": 0,
            "nfr_total": 0, "nfr_evidenced": 0,
            "structural_total": 0, "structural_evidenced": 0,
        },
        "uat_package": dict(package_state),
        "processing_log": [],
        "legacy_import_receipt": "N/A",
    }


def _bootstrap_error(ledger: Mapping[str, JsonValue], error: ValidationError) -> str:
    if error.code != "core-fields":
        return "feedback.ledger.invalid"
    missing = ", ".join(sorted(CORE_FIELDS - set(ledger)))
    return (
        "feedback.ledger.bootstrap-required: missing core fields: "
        f"{missing}; rerun practical cutover to seed the ledger; do not hand-mint rows or processing_log"
    )


def validate_ledger(
    path: Path, *, allow_pre_round: bool = False, allow_first_processing: bool = False,
) -> None:
    frontmatter, body = _frontmatter(path.read_text(encoding="utf-8"))
    ledger = _mapping(frontmatter, "core-fields")
    fields = set(ledger)
    legacy_omitted_pre_round = fields == CORE_FIELDS - {"legacy_import_receipt"} and isinstance(ledger.get("uat_package"), dict) and ledger["uat_package"].get("package_state") == "DRAFT"
    if fields != CORE_FIELDS and not legacy_omitted_pre_round:
        raise ValidationError("core-fields")
    if ledger["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("schema-version")
    ticket = _nonempty(ledger["ticket"], "ticket")
    _nonempty(ledger["generated_at"], "generated-at")
    if ticket not in _nonempty(ledger["latest_test_report_path"], "ticket-path"):
        raise ValidationError("ticket-path")
    _validate_coverage(ledger["coverage"])
    _validate_package(ledger["uat_package"], ticket)
    _validate_legacy(ledger.get("legacy_import_receipt"), body)
    coverage = ledger["coverage"]
    empty_first_processing = (
        (allow_pre_round and ledger["uat_package"]["package_state"] == "DRAFT")
        or (allow_first_processing and ledger["uat_package"]["package_state"] in {"DRAFT", "ISSUED"})
    ) and (
        ledger["processing_log"] == []
        and ledger["overall_verdict"] == "DRAFT"
        and ledger["processing_state"] == "PENDING"
        and all(coverage[f"{prefix}_{field}"] == 0 for prefix in ("functional", "nfr", "structural") for field in ("total", "evidenced"))
    )
    if empty_first_processing:
        if _empty_ledger_table(body):
            return
        raise ValidationError("pre-round-table")
    rows = _table(body)
    verdict, has_uat, evidenced = _validate_rows(rows)
    _validate_log(ledger["processing_log"], {row[0]: row[1] for row in rows})
    if any(coverage[f"{prefix}_evidenced"] < coverage[f"{prefix}_total"] for prefix in ("functional", "nfr", "structural")) and verdict not in {"FAIL", "BLOCKED"}:
        verdict = "INCOMPLETE"
    if ledger["overall_verdict"] != verdict or ledger["processing_state"] != ("COMPLETE" if verdict in {"PASS", "PASS_WITH_FINDINGS"} else "PENDING"):
        raise ValidationError("derived-achievement")
    for prefix in ("functional", "nfr", "structural"):
        if coverage[f"{prefix}_evidenced"] != evidenced[prefix]:
            raise ValidationError("coverage")
    if coverage["functional_total"] < len({row[2] for row in _table(body) if row[1] == "UAT"}):
        raise ValidationError("coverage")


def _json_object(content: str, code: str) -> JsonObject:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise PackageValidationError(code) from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PackageValidationError(code)
    return value


def _feedback_package_content(content: str) -> tuple[JsonObject, JsonObject, str]:
    frontmatter, document = _frontmatter(content)
    if set(frontmatter) not in {_FEEDBACK_FIELDS, _LEGACY_FEEDBACK_FIELDS}:
        raise PackageValidationError("feedback-envelope")
    envelope = frontmatter
    start = document.find(_ANNEX_START)
    end = document.find(_ANNEX_END, start + len(_ANNEX_START))
    if start < 0 or end < 0:
        raise PackageValidationError("feedback.annex")
    package = _json_object(document[start + len(_ANNEX_START):end], "feedback.annex")
    _validated_package(package)
    results = envelope["scenario_results"]
    reasons = envelope["scenario_result_reasons"]
    journeys = package.get("journeys")
    if not isinstance(results, dict) or not isinstance(reasons, dict) or not isinstance(journeys, list):
        raise ValidationError("feedback-envelope")
    expected = {journey.get("journey_id"): journey for journey in journeys if isinstance(journey, dict) and isinstance(journey.get("journey_id"), str)}
    if set(results) != set(expected):
        raise ValidationError("feedback-envelope")
    for journey_id, journey in expected.items():
        result = results.get(journey_id)
        if not isinstance(result, dict) or set(result) != {"requirement_ref", "scenario_outcome", "finding_severity", "finding_disposition"} or result.get("requirement_ref") != journey.get("requirement_ref"):
            raise ValidationError("feedback-envelope")
    return envelope, package, document


def _feedback_package(path: Path) -> tuple[JsonObject, JsonObject, str]:
    return _feedback_package_content(path.read_text(encoding="utf-8"))


_ENTRY_FIELDS = frozenset({"scenario_outcome", "finding_severity", "finding_disposition", "reason", "comment"})
_STEP_ENTRY_FIELDS = frozenset({"complete", "assessment", "finding_severity", "finding_disposition", "observed", "comment"})
_ENTRY_MARKER = re.compile(
    r'^<!-- uat-feedback-entry:v1 journey_id="(?P<journey_id>[^"]+)" -->\n'
    r'(?P<entry>.*?)\n<!-- /uat-feedback-entry:v1 -->$',
    re.MULTILINE | re.DOTALL,
)
_STEP_ENTRY_MARKER = re.compile(
    r'^<!-- uat-feedback-entry:v(?P<version>[23]) step_id="(?P<step_id>[^"]+)" -->\n'
    r'(?P<entry>.*?)\n<!-- /uat-feedback-entry:v(?P=version) -->$',
    re.MULTILINE | re.DOTALL,
)
_WALK_STOP_FIELDS = frozenset({"state", "halting_step_id", "unreached_step_ids", "trigger", "recorded_by", "note"})
_WALK_STOP_RECORDERS = frozenset({"OWNER", "ORCHESTRATOR_TRANSCRIPTION"})
_TRANSCRIPTION_PREFIX = "TRANSCRIPTION: "
_WALK_STOP_MARKER = re.compile(
    r'^<!-- uat-walk-stop:v1 -->\n(?P<stop>.*?)\n<!-- /uat-walk-stop:v1 -->$',
    re.MULTILINE | re.DOTALL,
)
_WALK_STOP_BLOCK = re.compile(
    r'(?m)^<!-- uat-walk-stop:v1 -->\n.*?\n<!-- /uat-walk-stop:v1 -->\n*',
    re.DOTALL,
)


def _stopped_step_observed(halting_step_id: str) -> str:
    """The one deterministic non-observation text an unreached step may carry."""
    return f"Walk stopped at {halting_step_id}; this step was not reached."


def _walk_stop_from_document(document: str) -> JsonObject | None:
    """Extract the single optional stop marker; duplicates or damage reject."""
    matches = list(_WALK_STOP_MARKER.finditer(document))
    if not matches:
        if "uat-walk-stop" in document:
            raise PackageValidationError("feedback.walk_stop.marker")
        return None
    if (len(matches) != 1
            or document.count("<!-- uat-walk-stop:v1 -->") != 1
            or document.count("<!-- /uat-walk-stop:v1 -->") != 1):
        raise PackageValidationError("feedback.walk_stop.marker")
    try:
        decoded = json.loads(matches[0].group("stop"))
    except json.JSONDecodeError as error:
        raise PackageValidationError("feedback.walk_stop.marker") from error
    if not isinstance(decoded, dict):
        raise PackageValidationError("feedback.walk_stop.marker")
    return decoded


def _walk_stop_marker_text(stop: Mapping[str, object]) -> str:
    payload = json.dumps(stop, sort_keys=True, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return f"<!-- uat-walk-stop:v1 -->\n{payload}\n<!-- /uat-walk-stop:v1 -->"


def _validate_walk_stop(
    stop: object,
    entries: Mapping[str, Mapping[str, object]],
    package: Mapping[str, object],
) -> dict[str, object] | None:
    """Validate the optional round-level stopped-walk marker, fail-closed.

    Canonical step identity is derived only from the sealed package; no
    ordinal, journey-position, or caller-supplied claim is trusted.
    """
    if stop is None:
        return None
    if not isinstance(stop, Mapping) or set(stop) != _WALK_STOP_FIELDS:
        raise PackageValidationError("feedback.walk_stop")
    state, trigger, recorded_by = stop["state"], stop["trigger"], stop["recorded_by"]
    halting_step_id, unreached, note = stop["halting_step_id"], stop["unreached_step_ids"], stop["note"]
    if state != "STOPPED" or trigger != "OBSERVED_FAILURE" or recorded_by not in _WALK_STOP_RECORDERS:
        raise PackageValidationError("feedback.walk_stop")
    if (not isinstance(note, str) or not note.strip() or len(note) > 20_000
            or any(ord(char) < 32 and char not in "\t\n\r" for char in note)):
        raise PackageValidationError("feedback.walk_stop.note")
    journeys = package.get("journeys")
    if not isinstance(journeys, list):
        raise PackageValidationError("package.invalid.journeys")
    canonical = {
        step.get("step_id")
        for journey in journeys if isinstance(journey, Mapping)
        for step in journey.get("steps", []) if isinstance(step, Mapping)
    }
    if not isinstance(halting_step_id, str) or halting_step_id not in canonical:
        raise PackageValidationError("feedback.walk_stop.halting_step")
    if (not isinstance(unreached, list) or not unreached
            or not all(isinstance(step_id, str) for step_id in unreached)
            or len(set(unreached)) != len(unreached)
            or not set(unreached) <= canonical
            or halting_step_id in unreached):
        raise PackageValidationError("feedback.walk_stop.unreached_step_ids")
    transcribed = recorded_by == "ORCHESTRATOR_TRANSCRIPTION"
    halting = entries.get(halting_step_id)
    if (not isinstance(halting, Mapping)
            or halting.get("complete") is not True
            or halting.get("assessment") != "OBSERVED_FAILURE"
            or halting.get("finding_severity") != "MAJOR"
            or halting.get("finding_disposition") != "OPEN"
            or not isinstance(halting.get("observed"), str)
            or not cast(str, halting["observed"]).strip()):
        raise PackageValidationError("feedback.walk_stop.halting_step")
    halting_comment = halting.get("comment")
    if (transcribed and isinstance(halting_comment, str) and halting_comment
            and not halting_comment.startswith(_TRANSCRIPTION_PREFIX)):
        raise PackageValidationError("feedback.walk_stop.transcription")
    expected_observed = _stopped_step_observed(halting_step_id)

    def is_empty_unreached(entry: Mapping[str, object]) -> bool:
        if (entry.get("complete") is not False
                or entry.get("assessment") != "NOT_OBSERVED"
                or entry.get("finding_severity") != "NONE"
                or entry.get("finding_disposition") != "OPEN"
                or entry.get("observed") != expected_observed):
            return False
        comment = entry.get("comment")
        if not isinstance(comment, str):
            return False
        if transcribed:
            return comment.startswith(_TRANSCRIPTION_PREFIX) and bool(comment[len(_TRANSCRIPTION_PREFIX):].strip())
        return comment == ""

    empty_unreached = {
        step_id for step_id, entry in entries.items()
        if isinstance(entry, Mapping) and is_empty_unreached(entry)
    }
    if empty_unreached != set(unreached):
        raise PackageValidationError("feedback.walk_stop.unreached_step_ids")
    return {
        "state": "STOPPED",
        "halting_step_id": halting_step_id,
        "unreached_step_ids": list(unreached),
        "trigger": "OBSERVED_FAILURE",
        "recorded_by": recorded_by,
        "note": note,
    }


def _entry_from_envelope(envelope: Mapping[str, object], journey_id: str) -> dict[str, str]:
    results = envelope["scenario_results"]
    reasons = envelope["scenario_result_reasons"]
    if not isinstance(results, Mapping) or not isinstance(reasons, Mapping):
        raise ValidationError("feedback-envelope")
    result = results.get(journey_id)
    if not isinstance(result, Mapping):
        raise ValidationError("feedback-envelope")
    return {
        "scenario_outcome": str(result.get("scenario_outcome", "")),
        "finding_severity": str(result.get("finding_severity", "")),
        "finding_disposition": str(result.get("finding_disposition", "")),
        "reason": str(reasons.get(journey_id, "")),
        "comment": "",
    }


def _validate_v2_step_entries(
    entries: Mapping[str, object],
    package: Mapping[str, object],
    walk_stop: object = None,
) -> dict[str, dict[str, object]]:
    journeys = package.get("journeys")
    if not isinstance(journeys, list):
        raise PackageValidationError("package.invalid.journeys")
    expected = {
        step.get("step_id")
        for journey in journeys if isinstance(journey, Mapping)
        for step in journey.get("steps", []) if isinstance(step, Mapping) and isinstance(step.get("step_id"), str)
    }
    if not expected or set(entries) != expected:
        raise PackageValidationError("feedback.step_entries")
    validated: dict[str, dict[str, object]] = {}
    for step_id in expected:
        entry = entries.get(step_id)
        if not isinstance(entry, Mapping) or set(entry) != _STEP_ENTRY_FIELDS or not isinstance(entry.get("complete"), bool):
            raise PackageValidationError("feedback.step")
        values = {field: entry[field] for field in _STEP_ENTRY_FIELDS}
        if not isinstance(values["observed"], str) or not isinstance(values["comment"], str):
            raise PackageValidationError("feedback.step")
        classification_fields = ("assessment", "finding_severity", "finding_disposition")
        if any(values[field] is not None and not isinstance(values[field], str) for field in classification_fields):
            raise PackageValidationError("feedback.step")
        string_values = (values["observed"], values["comment"]) + tuple(
            values[field] for field in classification_fields if isinstance(values[field], str)
        )
        if any(len(value) > 20_000 or any(ord(char) < 32 and char not in "\t\n\r" for char in value) for value in string_values):
            raise PackageValidationError("feedback.step")
        assessment = values["assessment"]
        severity = values["finding_severity"]
        disposition = values["finding_disposition"]
        # A partial card may remain blank, but any fully populated legacy or
        # current classification is valid only when its ledger triple is legal.
        if isinstance(assessment, str) and isinstance(severity, str) and isinstance(disposition, str) and assessment and severity and disposition:
            outcome = _assessment_outcome(assessment)
            if outcome is None or not _scenario_result_is_valid("UAT", outcome, severity, disposition):
                raise PackageValidationError("feedback.step.triple")
        if values["complete"]:
            if assessment in (None, ""):
                values["assessment"] = "PASS"
            if severity == "":
                values["finding_severity"] = None
            if disposition == "":
                values["finding_disposition"] = None
            projected = project_step_entry_to_ledger_triple(values)
            if not _scenario_result_is_valid("UAT", *projected):
                raise PackageValidationError("feedback.step.complete")
        validated[cast(str, step_id)] = values
    _validate_walk_stop(walk_stop, validated, package)
    return validated


def _validate_feedback_entries(entries: Mapping[str, object], package: Mapping[str, object]) -> dict[str, dict[str, object]]:
    if package.get("schema_version") == "uat-canonical-package@2":
        return _validate_v2_step_entries(entries, package)
    journeys = package.get("journeys")
    if not isinstance(journeys, list):
        raise PackageValidationError("package.invalid.journeys")
    expected = {
        journey.get("journey_id"): journey
        for journey in journeys
        if isinstance(journey, Mapping) and isinstance(journey.get("journey_id"), str)
    }
    if set(entries) != set(expected):
        raise PackageValidationError("feedback.entries")
    validated: dict[str, dict[str, str]] = {}
    for journey_id, journey in expected.items():
        entry = entries.get(journey_id)
        if not isinstance(entry, Mapping) or set(entry) != _ENTRY_FIELDS:
            raise PackageValidationError("feedback.entry")
        values = {field: entry[field] for field in _ENTRY_FIELDS}
        if not all(isinstance(value, str) for value in values.values()):
            raise PackageValidationError("feedback.entry")
        outcome = cast(str, values["scenario_outcome"])
        severity = cast(str, values["finding_severity"])
        disposition = cast(str, values["finding_disposition"])
        reason = cast(str, values["reason"])
        comment = cast(str, values["comment"])
        if not _scenario_result_is_valid(
            "UAT", outcome, severity, disposition, reason, enforce_na_reason=True
        ):
            raise PackageValidationError("feedback.entry.reason" if outcome == "N/A" or reason else "feedback.entry.triple")
        if len(comment) > 20_000 or any(ord(char) < 32 and char not in "\t\n\r" for char in comment):
            raise PackageValidationError("feedback.entry.comment")
        validated[cast(str, journey_id)] = {
            "scenario_outcome": outcome,
            "finding_severity": severity,
            "finding_disposition": disposition,
            "reason": reason,
            "comment": comment,
        }
    return validated


def _marker_entries(document: str, envelope: Mapping[str, object], package: Mapping[str, object]) -> tuple[dict[str, dict[str, str]], bool]:
    matches = list(_ENTRY_MARKER.finditer(document))
    if not matches:
        if "uat-feedback-entry:v1" in document:
            raise PackageValidationError("feedback.marker")
        entries = {
            journey_id: _entry_from_envelope(envelope, journey_id)
            for journey_id in cast(Mapping[str, object], envelope["scenario_results"])
        }
        return _validate_feedback_entries(entries, package), False
    decoded: dict[str, object] = {}
    for match in matches:
        journey_id = match.group("journey_id")
        if journey_id in decoded:
            raise PackageValidationError("feedback.marker")
        try:
            decoded[journey_id] = json.loads(match.group("entry"))
        except json.JSONDecodeError as error:
            raise PackageValidationError("feedback.marker") from error
    entries = _validate_feedback_entries(decoded, package)
    envelope_entries = {
        journey_id: _entry_from_envelope(envelope, journey_id)
        for journey_id in cast(Mapping[str, object], envelope["scenario_results"])
    }
    if any(
        entries[journey_id][field] != envelope_entries[journey_id][field]
        for journey_id in entries
        for field in ("scenario_outcome", "finding_severity", "finding_disposition", "reason")
    ):
        raise PackageValidationError("feedback.marker.drift")
    return entries, True


def _step_marker_entries(
    document: str, package: Mapping[str, object],
) -> tuple[dict[str, dict[str, object]], bool, dict[str, object] | None]:
    matches = list(_STEP_ENTRY_MARKER.finditer(document))
    if not matches or "uat-feedback-entry:" in document and not matches:
        raise PackageValidationError("feedback.marker")
    decoded: dict[str, object] = {}
    for match in matches:
        step_id = match.group("step_id")
        if step_id in decoded:
            raise PackageValidationError("feedback.marker")
        try:
            decoded[step_id] = json.loads(match.group("entry"))
        except json.JSONDecodeError as error:
            raise PackageValidationError("feedback.marker") from error
    stop = _walk_stop_from_document(document)
    validated = _validate_v2_step_entries(decoded, package, stop)
    return validated, True, _validate_walk_stop(stop, validated, package)


def parse_feedback_projection(content: bytes) -> dict[str, object]:
    """Parse the complete editable feedback projection without changing its authority bytes."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageValidationError("feedback.encoding") from error
    envelope, package, document = _feedback_package_content(text)
    generated_at = _generated_at(envelope, document, allow_terminal=True)
    if package.get("schema_version") == "uat-canonical-package@2":
        step_entries, markerized, walk_stop = _step_marker_entries(document, package)
        entries = {
            journey_id: _entry_from_envelope(envelope, journey_id)
            for journey_id in cast(Mapping[str, object], envelope["scenario_results"])
        }
    else:
        entries, markerized = _marker_entries(document, envelope, package)
        step_entries = None
        if _walk_stop_from_document(document) is not None:
            raise PackageValidationError("feedback.walk_stop.marker")
        walk_stop = None
    stored_round_status = envelope.get("round_status", "NOT_STARTED")
    if stored_round_status not in ROUND_STATUSES:
        raise PackageValidationError("feedback.envelope.round_status")
    if envelope["processing_state"] == "PROCESSED":
        round_status = "FAILED" if stored_round_status == "FAILED" or envelope["feedback_verdict"] in {"FAIL", "BLOCKED", "INCOMPLETE"} else "COMPLETE"
    else:
        round_status = stored_round_status
    projection = {
        "processing_state": envelope["processing_state"],
        "round_status": round_status,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tool_owned_receipt": _tool_owned_receipt(document),
        "normalized_package": deepcopy(package),
        "entries": entries,
        "markerized": markerized,
        "walk_stop": walk_stop,
    }
    if step_entries is not None:
        projection["step_entries"] = step_entries
    return projection


def _entry_marker_text(journey_id: str, entry: Mapping[str, str]) -> str:
    payload = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return f'<!-- uat-feedback-entry:v1 journey_id="{journey_id}" -->\n{payload}\n<!-- /uat-feedback-entry:v1 -->'


def _render_feedback_envelope(text: str, entries: Mapping[str, Mapping[str, str]], package: Mapping[str, object]) -> str:
    match = re.match(r"\A---\n(?P<frontmatter>.*?)---\n", text, re.DOTALL)
    if match is None:
        raise PackageValidationError("feedback-envelope")
    results: dict[str, dict[str, str]] = {}
    reasons: dict[str, str] = {}
    journeys = package.get("journeys")
    assert isinstance(journeys, list)
    for journey in journeys:
        assert isinstance(journey, Mapping)
        journey_id = cast(str, journey["journey_id"])
        entry = entries[journey_id]
        results[journey_id] = {
            "requirement_ref": cast(str, journey["requirement_ref"]),
            "scenario_outcome": entry["scenario_outcome"],
            "finding_severity": entry["finding_severity"],
            "finding_disposition": entry["finding_disposition"],
        }
        if entry["reason"]:
            reasons[journey_id] = entry["reason"]
    replacement = "scenario_results:\n" + "\n".join(
        f"  {line}" for line in yaml.safe_dump(results, sort_keys=False, allow_unicode=True).rstrip().splitlines()
    ) + "\nscenario_result_reasons: " + yaml.safe_dump(reasons, default_flow_style=True, sort_keys=True, allow_unicode=True).strip() + "\n"
    frontmatter, count = re.subn(
        r"(?ms)^scenario_results:\n.*?^scenario_result_reasons:.*\n",
        replacement,
        match.group("frontmatter"),
        count=1,
    )
    if count != 1 or re.search(r"(?m)^scenario_results:", frontmatter) is None or re.search(r"(?m)^scenario_result_reasons:", frontmatter) is None:
        raise PackageValidationError("feedback.legacy.ambiguous")
    return text[:match.start("frontmatter")] + frontmatter + text[match.end("frontmatter"):]


def render_feedback_round_status(original: bytes, round_status: str) -> bytes:
    """Persist a shared owner-facing status without touching sealed package bytes."""
    if round_status not in ROUND_STATUSES:
        raise PackageValidationError("feedback.envelope.round_status")
    projection = parse_feedback_projection(original)
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageValidationError("feedback.encoding") from error
    frontmatter = re.match(r"\A---\n(?P<frontmatter>.*?)---\n", text, re.DOTALL)
    if frontmatter is None:
        raise PackageValidationError("feedback.frontmatter")
    raw = frontmatter.group("frontmatter")
    if re.search(r"(?m)^round_status:", raw):
        rendered, count = re.subn(r"(?m)^round_status:.*$", f"round_status: {round_status}", raw, count=1)
        if count != 1:
            raise PackageValidationError("feedback.envelope.round_status")
    else:
        rendered, count = re.subn(r"(?m)^(processing_state:.*)$", rf"\1\nround_status: {round_status}", raw, count=1)
        if count != 1:
            raise PackageValidationError("feedback.envelope.round_status")
    candidate = text[:frontmatter.start("frontmatter")] + rendered + text[frontmatter.end("frontmatter"):]
    reparsed = parse_feedback_projection(candidate.encode("utf-8"))
    if reparsed["round_status"] != round_status or reparsed["normalized_package"] != projection["normalized_package"]:
        raise PackageValidationError("feedback.round-status-round-trip")
    return candidate.encode("utf-8")


def render_feedback_update(
    original: bytes, entries: Mapping[str, object], *, walk_stop: object = None,
) -> bytes:
    """Render a complete owner-entry update, preserving every non-editable byte."""
    projection = parse_feedback_projection(original)
    package = cast(Mapping[str, object], projection["normalized_package"])
    is_v2 = package.get("schema_version") == "uat-canonical-package@2"
    if walk_stop is not None and not is_v2:
        raise PackageValidationError("feedback.walk_stop.marker")
    validated = _validate_feedback_entries(entries, package)
    validated_stop = _validate_walk_stop(
        walk_stop, cast(Mapping[str, Mapping[str, object]], validated), package,
    ) if is_v2 else None
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PackageValidationError("feedback.encoding") from error
    if not cast(bool, projection["markerized"]):
        frontmatter = re.match(r"\A---\n(?P<frontmatter>.*?)---\n", text, re.DOTALL)
        if frontmatter is None or any(
            len(re.findall(rf"(?m)^{field}:", frontmatter.group("frontmatter"))) != 1
            for field in ("scenario_results", "scenario_result_reasons")
        ) or any(entry["comment"] for entry in validated.values()):
            raise PackageValidationError("feedback.legacy.ambiguous")
    if package.get("schema_version") == "uat-canonical-package@2":
        def replace_step_marker(match: re.Match[str]) -> str:
            step_id = match.group("step_id")
            if step_id not in validated:
                raise PackageValidationError("feedback.marker")
            payload = json.dumps(validated[step_id], sort_keys=True, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
            return f'<!-- uat-feedback-entry:v3 step_id="{step_id}" -->\n{payload}\n<!-- /uat-feedback-entry:v3 -->'
        candidate, count = _STEP_ENTRY_MARKER.subn(replace_step_marker, text)
        if count != len(validated):
            raise PackageValidationError("feedback.marker")
        candidate = _WALK_STOP_BLOCK.sub("", candidate)
        if validated_stop is not None:
            first = _STEP_ENTRY_MARKER.search(candidate)
            if first is None:
                raise PackageValidationError("feedback.marker")
            candidate = (
                candidate[:first.start()]
                + _walk_stop_marker_text(validated_stop)
                + "\n\n"
                + candidate[first.start():]
            )
        reparsed = parse_feedback_projection(candidate.encode("utf-8"))
        if reparsed.get("step_entries") != validated or reparsed.get("walk_stop") != validated_stop:
            raise PackageValidationError("feedback.round-trip")
        return candidate.encode("utf-8")
    candidate = _render_feedback_envelope(text, cast(Mapping[str, Mapping[str, str]], validated), package)
    if cast(bool, projection["markerized"]):
        def replace_marker(match: re.Match[str]) -> str:
            journey_id = match.group("journey_id")
            if journey_id not in validated:
                raise PackageValidationError("feedback.marker")
            return _entry_marker_text(journey_id, cast(Mapping[str, str], validated[journey_id]))
        candidate, count = _ENTRY_MARKER.subn(replace_marker, candidate)
        if count != len(validated):
            raise PackageValidationError("feedback.marker")
    reparsed = parse_feedback_projection(candidate.encode("utf-8"))
    if reparsed["entries"] != validated:
        raise PackageValidationError("feedback.round-trip")
    return candidate.encode("utf-8")


def _tool_owned_receipt(document: str) -> str:
    line = next((item for item in document.splitlines() if item.startswith("Tool-owned receipt: ")), "")
    return line.removeprefix("Tool-owned receipt: ").strip()


def _generated_at(envelope: JsonObject, document: str, *, allow_terminal: bool = False) -> datetime:
    value = envelope.get("generated_at")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise PackageValidationError("feedback.envelope.generated_at")
        stamp = value.isoformat().replace("+00:00", "Z")
    elif isinstance(value, str):
        stamp = value
    else:
        raise PackageValidationError("feedback.envelope.generated_at")
    _validate_feedback_envelope(
        stamp,
        str(envelope.get("processing_state", "")),
        str(envelope.get("feedback_verdict", "")),
        _tool_owned_receipt(document),
        allow_terminal=allow_terminal,
    )
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _selection_skip_reason(error: BaseException) -> str:
    if isinstance(error, OSError):
        name = errno.errorcode.get(error.errno, "UNKNOWN") if isinstance(error.errno, int) else "UNKNOWN"
        return f"oserror.{name}"
    return str(error)


def plan_feedback(inbox: Path) -> JsonObject:
    eligible: list[tuple[datetime, Path]] = []
    skipped: list[str] = []
    for path in sorted(inbox.glob("*.md")):
        try:
            envelope, _package, document = _feedback_package(path)
            generated = _generated_at(envelope, document) if envelope.get("processing_state") == "UNPROCESSED" else None
        except (OSError, PackageValidationError, ValidationError) as error:
            entry = f"feedback.selection.skipped: {path}: {_selection_skip_reason(error)}"
            skipped.append(entry)
            print(entry, file=sys.stderr)
            continue
        if generated is not None:
            eligible.append((generated, path))
    eligible.sort(key=lambda item: (item[0], str(item[1])), reverse=True)
    if not eligible:
        raise PackageValidationError("feedback.selection.empty")
    _timestamp, selected = eligible[0]
    return {
        "selected_path": str(selected),
        "older_unprocessed_paths": [str(path) for _time, path in eligible[1:]],
        "selection_basis": "generated_at",
        "skipped": skipped,
    }


def _candidate_errors(
    candidate: JsonObject,
    package: JsonObject,
    scenario_results: JsonObject,
    scenario_result_reasons: JsonObject,
    *,
    step_entries: Mapping[str, object] | None = None,
    walk_stop: Mapping[str, object] | None = None,
) -> list[str]:
    v2 = package.get("schema_version") == "uat-canonical-package@2"
    required = frozenset((*[field for field in _CANDIDATE_REQUIRED if field != "scenario_id"], "step_id", "scenario_links")) if v2 else frozenset(_CANDIDATE_REQUIRED)
    if set(candidate) != required:
        return ["feedback.invalid.step_lineage"] if v2 else ["feedback.invalid.candidate-or-predecessor"]
    if v2:
        step_id = candidate.get("step_id")
        journeys = package.get("journeys")
        journey = next((item for item in journeys if isinstance(item, dict) and item.get("journey_id") == candidate.get("journey_id")), None) if isinstance(journeys, list) else None
        step = next((item for item in journey.get("steps", []) if isinstance(item, dict) and item.get("step_id") == step_id), None) if isinstance(journey, dict) else None
        if (not isinstance(step, dict)
                or candidate.get("scenario_links") != step.get("scenario_links")
                or candidate.get("expected") != step.get("expected_outcome")):
            return ["feedback.invalid.step_lineage"]
    optional = candidate.get("optional_identities")
    omissions = candidate.get("omissions")
    atom_ids = candidate.get("atom_ids")
    if not isinstance(optional, dict) or not isinstance(omissions, dict) or not isinstance(atom_ids, list):
        return ["feedback.invalid.candidate-or-predecessor"]
    identity_fields = ("feedback_id", "ticket", "round_id", "package_id", "package_hash", "sealed_payload_sha256", "script_id", "script_hash", "certified_build_hash", "journey_id", "source", "uat_test_anchor", "requirement_ref", "summary", "observed", "expected")
    if not all(isinstance(candidate.get(key), str) and candidate[key] for key in identity_fields) or not all(isinstance(atom, str) and atom for atom in atom_ids):
        return ["feedback.invalid.candidate-or-predecessor"]
    for value in (candidate["feedback_id"], candidate["journey_id"], candidate["source"], candidate["uat_test_anchor"]):
        if not isinstance(value, str) or any(token in value for token in ("|", "\n", "\r", "\x00")):
            return ["feedback.invalid.candidate-or-predecessor"]
    if set(optional) | set(omissions) != set(_CANDIDATE_OPTIONAL_IDENTITIES) or set(optional) & set(omissions):
        return ["feedback.invalid.candidate-or-predecessor"]
    if not all(isinstance(value, str) and value.strip() for value in (*optional.values(), *omissions.values())):
        return ["feedback.invalid.candidate-or-predecessor"]
    identities = ("ticket", "round_id", "package_id", "package_hash", "sealed_payload_sha256", "script_id", "script_hash", "certified_build_hash")
    if any(candidate.get(key) != package.get(key) for key in identities):
        return ["feedback.invalid.identity"]
    journeys = package.get("journeys")
    if not isinstance(journeys, list):
        return ["feedback.invalid.missing-predecessor"]
    journey = next((item for item in journeys if isinstance(item, dict) and item.get("journey_id") == candidate.get("journey_id")), None)
    if not isinstance(journey, dict):
        return ["feedback.invalid.missing-predecessor"]
    journey_id = candidate.get("journey_id")
    if not isinstance(journey_id, str):
        return ["feedback.invalid.candidate-or-predecessor"]
    candidate_outcome = candidate.get("scenario_outcome")
    candidate_severity = candidate.get("finding_severity")
    candidate_disposition = candidate.get("finding_disposition")
    if not _scenario_result_is_valid("UAT", candidate_outcome, candidate_severity, candidate_disposition):
        return ["feedback.invalid.candidate-combination"]
    candidate_triple = (candidate_outcome, candidate_severity, candidate_disposition)
    if v2:
        step = next((item for item in journey.get("steps", []) if isinstance(item, dict) and item.get("step_id") == candidate.get("step_id")), None)
        if not isinstance(step, dict) or candidate.get("requirement_ref") != journey.get("requirement_ref"):
            return ["feedback.invalid.step_lineage"]
        if step_entries is not None:
            entry = step_entries.get(cast(str, candidate["step_id"]))
            if not isinstance(entry, Mapping):
                return ["feedback.invalid.step_lineage"]
            expected_triple = project_step_entry_to_ledger_triple(entry)
            licensed = set(cast(list, walk_stop["unreached_step_ids"])) if isinstance(walk_stop, Mapping) else set()
            settled = bool(entry.get("complete")) or (
                candidate.get("step_id") in licensed
                and candidate_triple == ("NOT_RUN", "NONE", "OPEN")
            )
            if (not settled
                    or not isinstance(entry.get("observed"), str)
                    or candidate_triple != expected_triple
                    or candidate.get("observed") != entry.get("observed")):
                return ["feedback.invalid.candidate-feedback-mismatch"]
    else:
        feedback_result = scenario_results.get(journey_id)
        if not isinstance(feedback_result, dict):
            return ["feedback.invalid.scenario-result-combination"]
        feedback_outcome = feedback_result.get("scenario_outcome")
        feedback_severity = feedback_result.get("finding_severity")
        feedback_disposition = feedback_result.get("finding_disposition")
        if not _scenario_result_is_valid("UAT", feedback_outcome, feedback_severity, feedback_disposition):
            return ["feedback.invalid.scenario-result-combination"]
        triple = (feedback_outcome, feedback_severity, feedback_disposition)
        if candidate.get("requirement_ref") != journey.get("requirement_ref") or candidate.get("requirement_ref") != feedback_result.get("requirement_ref") or candidate_triple != triple:
            return ["feedback.invalid.candidate-feedback-mismatch"]
        na_ids = {journey_id for journey_id, result in scenario_results.items() if isinstance(result, dict) and (result.get("scenario_outcome"), result.get("finding_severity"), result.get("finding_disposition")) == ("N/A", "NONE", "N/A")}
        if set(scenario_result_reasons) != na_ids or any(
            not isinstance(result, dict)
            or not _scenario_result_is_valid("UAT", result.get("scenario_outcome"), result.get("finding_severity"), result.get("finding_disposition"), scenario_result_reasons.get(result_id), enforce_na_reason=True)
            for result_id, result in scenario_results.items()
        ):
            return ["feedback.invalid.na-reason"]
        if candidate_triple == ("N/A", "NONE", "N/A") and candidate.get("summary") != scenario_result_reasons.get(journey_id):
            return ["feedback.invalid.na-reason"]
    errors: list[str] = []
    source = candidate.get("source")
    if source not in {"owner", "qa", "review", "investigation"}:
        errors.append("feedback.invalid.source-eligibility")
    if not all(isinstance(candidate.get(key), str) for key in ("observed", "expected", "summary")):
        return [*errors, "feedback.invalid.candidate-or-predecessor"]
    if ((not v2 and candidate.get("scenario_id") != journey.get("scenario_id"))
            or candidate.get("uat_test_anchor") != journey.get("uat_test_anchor")):
        errors.append("feedback.invalid.scenario-defends-defect")
    if atom_ids != journey.get("atom_ids"):
        errors.append("feedback.invalid.watched-outcome-atom")
    if not optional and any(isinstance(value, str) and value.lower() == "missing" for value in omissions.values()):
        errors.append("feedback.invalid.missing-predecessor")
    implementation = optional.get("implementation")
    if implementation is not None and (not isinstance(implementation, str) or not IMPLEMENTATION.fullmatch(implementation)):
        errors.append("feedback.invalid.implementation-slug")
    omitted_implementation = omissions.get("implementation")
    if isinstance(omitted_implementation, str) and omitted_implementation.isdigit():
        errors.append("feedback.invalid.implementation-slug")
    for key in ("primary_pe2e", "red_test"):
        reference = optional.get(key)
        if isinstance(reference, str) and ("/" in reference or reference.endswith(".py")) and not Path(reference).is_file():
            errors.append("feedback.invalid.test-reference")
    return errors


def _candidate_document(path: Path) -> JsonObject:
    candidate_document = _json_object(path.read_text(encoding="utf-8"), "feedback.candidate")
    if set(candidate_document) != {"schema_version", "candidates"} or candidate_document.get("schema_version") != "uat-feedback-candidates@1":
        raise PackageValidationError("feedback.candidate")
    candidates = candidate_document.get("candidates")
    if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
        raise PackageValidationError("feedback.candidate")
    allowed = {
        frozenset(_CANDIDATE_REQUIRED),
        frozenset((*[field for field in _CANDIDATE_REQUIRED if field != "scenario_id"], "step_id", "scenario_links")),
    }
    for item in candidates:
        if not isinstance(item, dict) or frozenset(item.keys()) not in allowed:
            raise PackageValidationError("feedback.candidate")
    return candidate_document


def _validated_ledger(path: Path, package: JsonObject) -> tuple[dict[str, JsonValue], list[list[str]]] | str:
    if not path.is_file():
        return "feedback.ledger.invalid"
    try:
        frontmatter, body = _frontmatter(path.read_text(encoding="utf-8"))
        if isinstance(frontmatter.get("ticket"), str) and frontmatter.get("ticket") != package.get("ticket"):
            return "feedback.ledger.invalid"
        try:
            validate_ledger(path, allow_first_processing=True)
        except ValidationError as error:
            bootstrap = _first_processing_ledger(frontmatter, package)
            if bootstrap is None:
                return _bootstrap_error(frontmatter, error)
            return bootstrap, []
    except OSError:
        return "feedback.ledger.invalid"
    ledger = frontmatter
    package_state = ledger.get("uat_package")
    identities = ("ticket", "package_id", "package_hash", "script_id", "script_hash", "certified_build_hash", "round_id")
    if not isinstance(package_state, dict) or any((ledger.get("ticket") if key == "ticket" else package_state.get(key)) != package.get(key) for key in identities):
        return "feedback.ledger.invalid"
    return ledger, [] if _empty_ledger_table(body) else _table(body)


def validate_feedback(
    feedback_path: Path, candidate_path: Path, ledger_path: Path, *, stale_map: JsonObject | None = None,
) -> JsonObject:
    try:
        envelope, package, _document = _feedback_package(feedback_path)
        feedback_projection = parse_feedback_projection(feedback_path.read_bytes())
    except (OSError, ValidationError, PackageValidationError):
        return {"verdict": "BLOCKED", "errors": ["feedback.invalid.candidate-or-predecessor"], "rows": [], "stale_remaps": []}
    step_entries: Mapping[str, object] | None = None
    walk_stop: Mapping[str, object] | None = None
    eligible_step_ids: set[str] = set()
    untouched_step_ids: list[str] = []
    touched_unsettled_step_ids: list[str] = []
    if package.get("schema_version") == "uat-canonical-package@2":
        step_entries = feedback_projection.get("step_entries")
        raw_stop = feedback_projection.get("walk_stop")
        walk_stop = raw_stop if isinstance(raw_stop, Mapping) else None
        if not isinstance(step_entries, Mapping):
            return {"verdict": "BLOCKED", "errors": ["feedback.invalid.step_lineage"], "rows": [], "stale_remaps": []}
        licensed = set(cast(list, walk_stop["unreached_step_ids"])) if walk_stop is not None else set()
        eligible_step_ids = {
            step_id for step_id, entry in step_entries.items()
            if isinstance(entry, Mapping) and entry.get("complete")
        } | licensed
        for step_id, entry in step_entries.items():
            if step_id in eligible_step_ids or not isinstance(entry, Mapping):
                continue
            if any(str(entry.get(field, "")).strip() for field in _STEP_ENTRY_FIELDS - {"complete"}):
                touched_unsettled_step_ids.append(step_id)
            else:
                untouched_step_ids.append(step_id)
        if not eligible_step_ids and not touched_unsettled_step_ids:
            return {"verdict": "BLOCKED", "errors": ["feedback.no-owner-intent"], "rows": [], "stale_remaps": []}
    try:
        candidate_document = _candidate_document(candidate_path)
    except (OSError, PackageValidationError):
        return {"verdict": "BLOCKED", "errors": ["feedback.invalid.candidate-or-predecessor"], "rows": [], "stale_remaps": []}
    if stale_map is not None:
        for value in stale_map.values():
            if isinstance(value, list):
                return {"verdict": "BLOCKED", "error": "BLOCKED: STALE-FEEDBACK-MAP"}
    ledger_state = _validated_ledger(ledger_path, package)
    if isinstance(ledger_state, str):
        return {"verdict": "BLOCKED", "errors": [ledger_state], "rows": [], "stale_remaps": []}
    _ledger, ledger_rows = ledger_state
    candidates = candidate_document.get("candidates")
    if not isinstance(candidates, list):
        raise PackageValidationError("feedback.candidate")
    if step_entries is not None:
        candidate_step_ids = [candidate.get("step_id") for candidate in candidates if isinstance(candidate, dict)]
        if (len(candidate_step_ids) != len(candidates)
                or set(candidate_step_ids) != eligible_step_ids
                or len(candidate_step_ids) != len(set(candidate_step_ids))):
            return {"verdict": "BLOCKED", "errors": ["feedback.invalid.step_lineage"], "rows": [], "stale_remaps": []}
    errors: list[str] = []
    rows: list[JsonValue] = []
    remaps: list[JsonValue] = []
    for raw_candidate in candidates:
        if not isinstance(raw_candidate, dict):
            errors.append("feedback.invalid.candidate-or-predecessor")
            continue
        candidate = raw_candidate
        results = envelope["scenario_results"]
        reasons = envelope["scenario_result_reasons"]
        if not isinstance(results, dict) or not isinstance(reasons, dict):
            return {"verdict": "BLOCKED", "errors": ["feedback.invalid.scenario-result-combination"], "rows": [], "stale_remaps": []}
        for error in _candidate_errors(candidate, package, results, reasons, step_entries=step_entries, walk_stop=walk_stop):
            if error not in errors:
                errors.append(error)
        if errors:
            continue
        feedback_id = candidate.get("feedback_id")
        if stale_map is not None and isinstance(feedback_id, str) and feedback_id in stale_map:
            mapped = stale_map[feedback_id]
            if isinstance(mapped, str):
                if not any(row[0] == mapped for row in ledger_rows):
                    return {"verdict": "BLOCKED", "error": "BLOCKED: STALE-FEEDBACK-MAP"}
                remaps.append(f"{feedback_id}->{mapped}")
        journey_id = candidate.get("journey_id")
        candidate_result = candidate.get("scenario_outcome")
        optional = candidate.get("optional_identities")
        feedback_id = candidate.get("feedback_id")
        if not isinstance(feedback_id, str):
            continue
        target_id = feedback_id
        if stale_map is not None and isinstance(stale_map.get(feedback_id), str):
            target_id = stale_map[feedback_id]
        if candidate.get("source") not in {"owner", "qa", "review", "investigation"}:
            continue
        if isinstance(journey_id, str) and candidate_result == "PASS" and isinstance(optional, dict):
            implementation = optional.get("implementation")
            rows.append({
                "trace_id": target_id if target_id != feedback_id else (f"PASS-{candidate['step_id']}" if step_entries is not None else f"PASS-{journey_id}"), "type": "UAT", "verdict": "PASS",
                "source": candidate["requirement_ref"],
                "atomic_obligation": f"feedback:{candidate['source']}:{feedback_id}",
                "scenario": candidate["scenario_links"][0]["scenario_id"] if "step_id" in candidate else candidate["scenario_id"],
                "red_proof": "— not applicable — no behavior change; existing GREEN",
                "implementation": implementation if isinstance(implementation, str) else "task#0-existing-green",
                "finding_severity": candidate["finding_severity"],
                "disposition": candidate["finding_disposition"],
            })
        elif isinstance(journey_id, str) and candidate_result in {"FAIL", "BLOCKED", "N/A", "NOT_RUN"}:
            rows.append({
                "trace_id": target_id if target_id != feedback_id else f"FB-{candidate['source']}-{feedback_id}", "type": "UAT", "verdict": candidate_result,
                "source": candidate["requirement_ref"], "atomic_obligation": f"feedback:{candidate['source']}:{feedback_id}" + (f"; N/A reason: {candidate['summary']}" if candidate_result == "N/A" else ""), "scenario": candidate["scenario_links"][0]["scenario_id"] if "step_id" in candidate else candidate["scenario_id"],
                "red_proof": str(optional.get("red_test", "not yet evidenced")) if isinstance(optional, dict) else "not yet evidenced",
                "implementation": str(optional.get("implementation", "task#0-pending")) if isinstance(optional, dict) else "task#0-pending",
                "finding_severity": candidate["finding_severity"], "disposition": candidate["finding_disposition"],
            })
    if step_entries is not None and not errors:
        incomplete_outcome, incomplete_severity, incomplete_disposition = next(iter(_contract_cells("system_cells")))
        step_context = {
            str(step["step_id"]): (journey, step)
            for journey in cast(list[object], package["journeys"])
            if isinstance(journey, dict)
            for step in cast(list[object], journey.get("steps", []))
            if isinstance(step, dict) and isinstance(step.get("step_id"), str)
        }
        for step_id in [*touched_unsettled_step_ids, *untouched_step_ids]:
            journey, step = step_context[step_id]
            assert isinstance(journey, dict) and isinstance(step, dict)
            rows.append({
                "trace_id": f"INCOMPLETE-{step_id}", "type": "UAT", "verdict": incomplete_outcome,
                "source": journey["requirement_ref"], "journey_id": journey["journey_id"],
                "atomic_obligation": f"feedback:system:{step_id}",
                "scenario": step["scenario_links"][0]["scenario_id"],
                "red_proof": "— system-derived incomplete scope —", "implementation": "task#0-pending",
                "finding_severity": incomplete_severity, "disposition": incomplete_disposition,
            })
    result: JsonObject = {
        "verdict": "PASS" if not errors else "BLOCKED", "rows": rows, "errors": errors,
        "stale_remaps": remaps, "untouched_step_ids": untouched_step_ids,
        "touched_unsettled_step_ids": touched_unsettled_step_ids,
    }
    _ = envelope
    return result


def _ledger_bytes(package: JsonObject, rows: list[JsonObject], log: JsonObject, feedback_path: Path, ledger_path: Path) -> bytes:
    frontmatter, body = _frontmatter(ledger_path.read_text(encoding="utf-8"))
    bootstrap = _first_processing_ledger(frontmatter, package)
    if bootstrap is not None:
        frontmatter = bootstrap
        existing_rows: list[list[str]] = []
    else:
        existing_rows = [] if _empty_ledger_table(body) else _table(body)
    identity = package["ticket"]
    if not isinstance(identity, str):
        raise PackageValidationError("feedback.package.ticket")
    replacement_ids = {str(row["trace_id"]) for row in rows}
    retained = [row for row in existing_rows if row[0] not in replacement_ids]
    additions: list[list[str]] = []
    for row in rows:
        disposition = str(row["disposition"])
        source = row.get("source", "owner")
        obligation = row.get("atomic_obligation", f"feedback:{source}:pass")
        scenario = row.get("scenario", "UAT-existing-green")
        additions.append([str(row["trace_id"]), "UAT", str(source), str(obligation), str(scenario), "acceptance outcome", "feedback", "N/A", str(row["verdict"]), str(row["finding_severity"]), str(feedback_path), str(row["red_proof"]), str(row["implementation"]), disposition])
    all_rows = [*retained, *additions]
    logs = frontmatter.get("processing_log")
    if not isinstance(logs, list):
        raise ValidationError("processing-log")
    if not any(isinstance(entry, dict) and entry.get("run_id") == log["run_id"] for entry in logs):
        ledger_log = dict(log)
        row_ids = log.get("row_ids")
        affected_ids = log.get("affected_row_ids")
        if not isinstance(row_ids, list) or not isinstance(affected_ids, list):
            raise ValidationError("processing-log")
        ledger_log["row_ids"] = row_ids
        ledger_log["affected_row_ids"] = affected_ids
        logs.append(ledger_log)
    frontmatter["processing_log"] = logs
    if not isinstance(frontmatter.get("uat_package"), dict):
        raise ValidationError("uat-package")
    derived_verdict, _has_uat, evidence = _validate_rows(all_rows)
    coverage = frontmatter.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != COVERAGE_FIELDS:
        raise ValidationError("coverage")
    for prefix in ("functional", "nfr", "structural"):
        coverage[f"{prefix}_evidenced"] = evidence[prefix]
        if package.get("schema_version") == "uat-canonical-package@2":
            required_total = len({row[2] for row in all_rows if row[1] == "UAT"}) if prefix == "functional" else evidence[prefix]
            coverage[f"{prefix}_total"] = max(cast(int, coverage[f"{prefix}_total"]), required_total)
    frontmatter["overall_verdict"] = derived_verdict
    frontmatter["processing_state"] = "COMPLETE" if derived_verdict in {"PASS", "PASS_WITH_FINDINGS"} else "PENDING"
    text = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, width=2**20).rstrip() + "\n---\n\n"
    before_log, separator, log_text = text.partition("processing_log:\n")
    if separator:
        log_text = re.sub(
            r"(?m)^(  (?:requirement_refs|row_ids|test_refs|source_paths|evidence_paths|affected_row_ids|journey_ids):\n)((?:  - .*(?:\n|$))+)",
            lambda match: match.group(1) + re.sub(r"(?m)^  - ", "    - ", match.group(2)),
            log_text,
        )
        text = before_log + separator + log_text
    text += "| " + " | ".join(COLUMNS) + " |\n| " + " | ".join("---" for _ in COLUMNS) + " |\n"
    text += "".join("| " + " | ".join(row) + " |\n" for row in all_rows)
    return text.encode("utf-8")


def _processed_feedback_bytes(content: bytes, verdict: str) -> bytes:
    original = b"processing_state: UNPROCESSED"
    original_verdict = b"feedback_verdict: N/A"
    if original not in content or original_verdict not in content or verdict not in DERIVED_VERDICTS:
        return content
    content = content.replace(original, b"processing_state: PROCESSED", 1)
    return content.replace(original_verdict, f"feedback_verdict: {verdict}".encode("utf-8"), 1)


def _unprocessed_feedback_bytes(content: bytes) -> bytes:
    content = content.replace(b"processing_state: PROCESSED", b"processing_state: UNPROCESSED", 1)
    return re.sub(rb"(?m)^feedback_verdict: (?:PASS|PASS_WITH_FINDINGS|FAIL|BLOCKED|INCOMPLETE)$", b"feedback_verdict: N/A", content, count=1)


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _cleanup(temporary_paths: tuple[Path, ...]) -> list[str]:
    failed: list[str] = []
    for temporary in temporary_paths:
        if not temporary.exists():
            continue
        try:
            temporary.unlink()
        except OSError:
            failed.append(str(temporary))
    return failed


def _stage(path: Path, payload: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.uat-feedback-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            remaining = payload
            while remaining:
                written = stream.write(remaining)
                if written <= 0:
                    raise OSError(errno.EIO, "stage write made no progress")
                remaining = remaining[written:]
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
    except OSError:
        _cleanup((temporary,))
        raise
    return temporary


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno not in {errno.EINVAL, errno.EOPNOTSUPP}:
            raise
    finally:
        os.close(descriptor)


def _commit(
    label: str, temporary: Path, target: Path, preconditions: dict[Path, str], journal: list[str], callback: Callable[[str, Path], None] | None,
) -> str | None:
    if callback is not None:
        callback(label, target)
    if any((_hash(path.read_bytes()) if path.exists() else "") != expected for path, expected in preconditions.items()):
        return "PRECONDITION-DRIFT"
    try:
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        journal.append(str(target))
        _sync_directory(target)
    except OSError:
        return "ASSET-COMMIT-FAILED"
    return None


def process_feedback(
    feedback_path: Path, candidate_path: Path, ledger_path: Path, *, _before_final_compare: Callable[[str, Path], None] | None = None,
) -> JsonObject:
    envelope, package, _document = _feedback_package(feedback_path)
    candidate_bytes = candidate_path.read_bytes()
    try:
        validated = validate_feedback(feedback_path, candidate_path, ledger_path)
    except PackageValidationError:
        return {"status": "BLOCKED: feedback.invalid.candidate-or-predecessor"}
    if validated.get("verdict") != "PASS":
        errors = validated.get("errors", [])
        if isinstance(errors, list) and errors == ["feedback.invalid.candidate-or-predecessor"]:
            return {"status": "BLOCKED: feedback.invalid.candidate-or-predecessor"}
        if envelope.get("processing_state") == "PROCESSED":
            return {"status": "BLOCKED: PROCESSED-FEEDBACK-INCONSISTENT"}
        if isinstance(errors, list) and errors == ["feedback.ledger.invalid"]:
            return {"status": "BLOCKED: feedback.ledger.invalid"}
        if isinstance(errors, list) and len(errors) == 1 and isinstance(errors[0], str) and errors[0].startswith("feedback.ledger.bootstrap-required:"):
            return {"status": "BLOCKED: " + errors[0]}
        return {"status": "BLOCKED", "errors": errors}
    if candidate_path.read_bytes() != candidate_bytes:
        return {"status": "BLOCKED: PRECONDITION-DRIFT"}
    rows = validated.get("rows")
    if not isinstance(rows, list) or not rows:
        return {"status": "BLOCKED", "errors": ["feedback.invalid.candidate-or-predecessor"]}
    typed_rows = [row for row in rows if isinstance(row, dict)]
    if len(typed_rows) != len(rows):
        return {"status": "BLOCKED", "errors": ["feedback.invalid.candidate-or-predecessor"]}
    untouched_step_ids = validated.get("untouched_step_ids", [])
    touched_unsettled_step_ids = validated.get("touched_unsettled_step_ids", [])
    if not all(isinstance(step_id, str) for step_id in [*untouched_step_ids, *touched_unsettled_step_ids]):
        raise PackageValidationError("feedback.step")
    row_ids = [row.get("trace_id") for row in typed_rows]
    if not all(isinstance(row_id, str) for row_id in row_ids):
        raise PackageValidationError("feedback.row")
    target_ids = [str(row_id) for row_id in row_ids]
    is_processed = envelope.get("processing_state") == "PROCESSED"
    feedback_identity = _unprocessed_feedback_bytes(feedback_path.read_bytes())
    candidate_document = _candidate_document(candidate_path)
    candidate_values = candidate_document.get("candidates")
    if not isinstance(candidate_values, list):
        raise PackageValidationError("feedback.candidate")
    package_journeys = package.get("journeys")
    if not isinstance(package_journeys, list):
        raise PackageValidationError("feedback.package.journeys")
    journey_requirements = {
        str(journey["journey_id"]): str(journey["requirement_ref"])
        for journey in package_journeys
        if isinstance(journey, dict) and isinstance(journey.get("journey_id"), str) and isinstance(journey.get("requirement_ref"), str)
    }
    journeys = [
        str(row["journey_id"]) if isinstance(row.get("journey_id"), str)
        else str(candidate_values[index].get("journey_id"))
        for index, row in enumerate(typed_rows)
        if isinstance(row.get("journey_id"), str)
        or index < len(candidate_values) and isinstance(candidate_values[index], dict)
    ]
    is_pass = all(row.get("verdict") == "PASS" for row in typed_rows)
    package_hash = package.get("package_hash")
    if not isinstance(package_hash, str):
        raise PackageValidationError("feedback.package.hash")
    log: JsonObject = {
        "run_id": _hash(package_hash.encode("utf-8") + _hash(feedback_identity).encode("utf-8") + _hash(candidate_bytes).encode("utf-8") + ":".join(target_ids).encode("utf-8"))[:16],
        "timestamp": datetime.now(timezone.utc).isoformat(), "operation": "process", "requirement_refs": [str(row["source"]) for row in typed_rows],
        "row_ids": list(target_ids), "test_refs": ["N/A — existing behavior"] if is_pass else [str(row["red_proof"]) for row in typed_rows],
        "source_paths": [str(feedback_path)], "evidence_paths": [str(candidate_path)], "derived_verdict": "PASS" if is_pass else "BLOCKED",
        "validator_verdict": "PASS", "affected_row_ids": list(target_ids), "round_id": str(package["round_id"]),
        "journey_ids": journeys, "package_hash": str(package["package_hash"]),
        "script_hash": str(package["script_hash"]), "certified_build_hash": str(package["certified_build_hash"]),
        "scenario_outcomes": [str(row["verdict"]) for row in typed_rows],
        "finding_severities": [str(row["finding_severity"]) for row in typed_rows],
        "finding_dispositions": [str(row["disposition"]) for row in typed_rows],
    }
    receipt_path = feedback_path.with_suffix(feedback_path.suffix + ".receipt.json")
    if is_processed:
        ledger_state = _validated_ledger(ledger_path, package)
        if isinstance(ledger_state, str):
            return {"status": "BLOCKED: PROCESSED-FEEDBACK-INCONSISTENT"}
        _ledger, existing_rows = ledger_state
        if not all(any(existing[0] == target_id for existing in existing_rows) for target_id in target_ids):
            return {"status": "BLOCKED: PROCESSED-FEEDBACK-INCONSISTENT"}
        if not receipt_path.is_file():
            return {"status": "BLOCKED: PROCESSED-FEEDBACK-INCONSISTENT"}
        try:
            receipt = _json_object(receipt_path.read_text(encoding="utf-8"), "feedback.receipt")
        except (OSError, PackageValidationError):
            return {"status": "BLOCKED: PROCESSED-FEEDBACK-INCONSISTENT"}
        required_receipt = {"run_id", "affected_row_ids", "routing_actions", "package_hash", "candidate_sha256", "ledger_sha256", "feedback_sha256", "untouched_step_ids", "touched_unsettled_step_ids"}
        if set(receipt) != required_receipt or receipt.get("run_id") != log["run_id"] or receipt.get("affected_row_ids") != target_ids or receipt.get("routing_actions") != [row["disposition"] for row in typed_rows] or receipt.get("untouched_step_ids") != untouched_step_ids or receipt.get("touched_unsettled_step_ids") != touched_unsettled_step_ids or receipt.get("package_hash") != package_hash or receipt.get("candidate_sha256") != _hash(candidate_bytes) or receipt.get("ledger_sha256") != _hash(ledger_path.read_bytes()) or receipt.get("feedback_sha256") != _hash(feedback_path.read_bytes()):
            return {"status": "BLOCKED: PROCESSED-FEEDBACK-INCONSISTENT"}
        logs = _ledger.get("processing_log")
        persisted = next((entry for entry in logs if isinstance(entry, dict) and entry.get("run_id") == log["run_id"]), None) if isinstance(logs, list) else None
        if persisted is None or {key: value for key, value in persisted.items() if key != "timestamp"} != {key: value for key, value in log.items() if key != "timestamp"}:
            return {"status": "BLOCKED: PROCESSED-FEEDBACK-INCONSISTENT"}
        return {"status": "PROCESSED", "affected_row_ids": target_ids, "created_row_count": 0, "feedback_state": "PROCESSED", "processing_log": persisted, "run_id": persisted["run_id"], "routing_actions": [row["disposition"] for row in typed_rows]}
    ledger_bytes = _ledger_bytes(package, typed_rows, log, feedback_path, ledger_path)
    try:
        rendered_frontmatter, _rendered_body = _frontmatter(ledger_bytes.decode("utf-8"))
    except ValidationError:
        return {"status": "BLOCKED: feedback.ledger.invalid"}
    log["derived_verdict"] = str(rendered_frontmatter["overall_verdict"])
    ledger_bytes = _ledger_bytes(package, typed_rows, log, feedback_path, ledger_path)
    feedback_original = feedback_path.read_bytes()
    if receipt_path.exists():
        return {"status": "BLOCKED: UNPROCESSED-FEEDBACK-RECEIPT-EXISTS"}
    feedback_processed = _processed_feedback_bytes(feedback_original, str(log["derived_verdict"]))
    ledger_bytes = _ledger_bytes(package, typed_rows, log, feedback_path, ledger_path)
    actions = [row["disposition"] for row in typed_rows]
    receipt_bytes = json.dumps({"run_id": log["run_id"], "affected_row_ids": target_ids, "routing_actions": actions, "untouched_step_ids": untouched_step_ids, "touched_unsettled_step_ids": touched_unsettled_step_ids, "package_hash": package_hash, "candidate_sha256": _hash(candidate_bytes), "ledger_sha256": _hash(ledger_bytes), "feedback_sha256": _hash(feedback_processed)}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    staged_paths: list[Path] = []
    try:
        ledger_temp = _stage(ledger_path, ledger_bytes, ledger_path.stat().st_mode & 0o777); staged_paths.append(ledger_temp)
        validate_ledger(ledger_temp)
        feedback_temp = _stage(feedback_path, feedback_processed, feedback_path.stat().st_mode & 0o777); staged_paths.append(feedback_temp)
        receipt_temp = _stage(receipt_path, receipt_bytes, 0o600); staged_paths.append(receipt_temp)
        compensation_temp = _stage(feedback_path, feedback_original, feedback_path.stat().st_mode & 0o777); staged_paths.append(compensation_temp)
        staged = (("ledger", ledger_temp, ledger_path), ("feedback", feedback_temp, feedback_path), ("receipt", receipt_temp, receipt_path), ("compensation", compensation_temp, feedback_path))
    except ValidationError:
        _cleanup(tuple(staged_paths))
        return {"status": "BLOCKED: feedback.ledger.invalid", "changed_paths": ""}
    except OSError:
        _cleanup(tuple(staged_paths))
        return {"status": "BLOCKED: ASSET-STAGE-FAILED", "changed_paths": ""}
    journal: list[str] = []
    preconditions = {ledger_path: _hash(ledger_path.read_bytes()), feedback_path: _hash(feedback_original), candidate_path: _hash(candidate_bytes), receipt_path: ""}
    for label, temporary, target in staged[:3]:
        failure = _commit(label, temporary, target, preconditions, journal, _before_final_compare)
        if failure is None:
            preconditions[target] = _hash(ledger_bytes) if label == "ledger" else _hash(feedback_processed) if label == "feedback" else _hash(receipt_bytes)
            continue
        compensation_status = "NOT-NEEDED"
        receipt_cleanup_status = "NOT_NEEDED"
        receipt_cleanup_failed_paths: list[str] = []
        if receipt_path.exists() and _hash(receipt_path.read_bytes()) == _hash(receipt_bytes):
            try:
                receipt_path.unlink()
                _sync_directory(receipt_path)
                receipt_cleanup_status = "REMOVED"
            except OSError:
                receipt_cleanup_status = "FAILED"
                receipt_cleanup_failed_paths.append(str(receipt_path))
        if label == "receipt" and _hash(feedback_path.read_bytes()) == _hash(feedback_processed):
            restore_failure = _commit("compensation", staged[3][1], feedback_path, {feedback_path: _hash(feedback_processed), candidate_path: _hash(candidate_bytes)}, [], None)
            compensation_status = "RESTORED" if _hash(feedback_path.read_bytes()) == _hash(feedback_original) else "FAILED"
        changed_paths = ",".join(journal)
        if failure == "PRECONDITION-DRIFT" and not journal:
            cleanup_failed = _cleanup(tuple(item[1] for item in staged))
            if cleanup_failed:
                return {"status": _PARTIAL_PREFIX, "changed_paths": "", "failure_reason": "ASSET-CLEANUP-FAILED"}
            return {"status": "BLOCKED: PRECONDITION-DRIFT", "changed_paths": ""}
        cleanup_failed = _cleanup(tuple(item[1] for item in staged))
        if cleanup_failed:
            failure = "ASSET-CLEANUP-FAILED"
        result: JsonObject = {"status": _PARTIAL_PREFIX + changed_paths, "changed_paths": changed_paths, "failure_reason": failure, "run_id": log["run_id"], "compensation_status": compensation_status, "feedback_state": "UNPROCESSED" if compensation_status in {"RESTORED", "NOT-NEEDED"} else "PROCESSED_WITHOUT_RECEIPT"}
        result["receipt_cleanup_status"] = receipt_cleanup_status
        result["receipt_state"] = "PRESENT_TOOL_OWNED" if receipt_path.exists() else "REMOVED_WITH_DIRSYNC_FAILED" if receipt_cleanup_status == "FAILED" else "ABSENT"
        if receipt_cleanup_failed_paths:
            result["receipt_cleanup_failed_paths"] = receipt_cleanup_failed_paths
        if compensation_status == "FAILED":
            result["compensation_failed_paths"] = [str(feedback_path)]
        return result
    cleanup_failed = _cleanup((staged[3][1],))
    if cleanup_failed:
        return {"status": "PROCESSED_WITH_CLEANUP_WARNING", "cleanup_status": "FAILED", "cleanup_residue_paths": [str(feedback_path)], "affected_row_ids": target_ids, "feedback_state": "PROCESSED", "processing_log": log, "routing_actions": actions}
    return {"status": "PROCESSED", "affected_row_ids": target_ids, "created_row_count": len(target_ids), "feedback_state": "PROCESSED", "processing_log": log, "run_id": log["run_id"], "routing_actions": [row["disposition"] for row in typed_rows]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uat_feedback")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validator = subcommands.add_parser("validate-ledger")
    validator.add_argument("path", type=Path)
    planner = subcommands.add_parser("plan")
    planner.add_argument("inbox", type=Path)
    feedback_validator = subcommands.add_parser("validate")
    feedback_validator.add_argument("feedback_path", type=Path)
    feedback_validator.add_argument("candidate_path", type=Path)
    feedback_validator.add_argument("ledger_path", type=Path)
    processor = subcommands.add_parser("process")
    processor.add_argument("feedback_path", type=Path)
    processor.add_argument("candidate_path", type=Path)
    processor.add_argument("ledger_path", type=Path)
    result_contract = subcommands.add_parser("result-contract")
    result_contract.add_argument("--json", action="store_true")
    parsed = parser.parse_args(argv)
    try:
        if parsed.command == "result-contract":
            contract_bytes = RESULT_CONTRACT_PATH.read_bytes()
            contract = feedback_result_contract()
            if parsed.json:
                print(json.dumps({"contract": contract, "sha256": hashlib.sha256(contract_bytes).hexdigest()}))
            else:
                print(json.dumps(contract, indent=2))
            return 0
        if parsed.command == "validate-ledger":
            validate_ledger(parsed.path)
            return 0
        if parsed.command == "plan":
            result = plan_feedback(parsed.inbox)
            print(json.dumps({"operation": "plan", "selection_basis": result["selection_basis"], "selected_path": result["selected_path"], "older_unprocessed_paths": result["older_unprocessed_paths"], "skipped": result["skipped"]}, sort_keys=True))
            return 0
        if parsed.command == "validate":
            try:
                _candidate_document(parsed.candidate_path)
            except (OSError, PackageValidationError):
                print("BLOCKED: feedback.invalid.candidate-or-predecessor", file=sys.stderr)
                return 1
            result = validate_feedback(parsed.feedback_path, parsed.candidate_path, parsed.ledger_path)
            if result.get("verdict") != "PASS":
                errors = result.get("errors")
                if isinstance(errors, list) and errors == ["feedback.ledger.invalid"]:
                    print("BLOCKED: feedback.ledger.invalid", file=sys.stderr)
                else:
                    print("BLOCKED: feedback.invalid.candidate-or-predecessor", file=sys.stderr)
                return 1
            print(json.dumps(result, sort_keys=True))
            return 0
        result = process_feedback(parsed.feedback_path, parsed.candidate_path, parsed.ledger_path)
        if result.get("status") != "PROCESSED":
            if isinstance(result.get("status"), str) and str(result["status"]).startswith("PARTIAL ASSET CHANGES:"):
                print(json.dumps(result, sort_keys=True), file=sys.stderr)
            else:
                print(str(result.get("status")), file=sys.stderr)
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValidationError, PackageValidationError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
