#!/usr/bin/env python3
"""Validate and safely materialize ticket-owned UAT round patterns."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import os
import re
import stat
import sys
from urllib.parse import urlsplit
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, NoReturn, cast

RULE_ID = re.compile(r"^UAT-(?:G\d+|R\d+|T\d+|D\d+)$")
# A ticket id here is a work item id in the sense of jswarm.workitem.identity:
# either a tracker key (PS-14) or a jPlan-produced local slug (add-csv-export)
# -- the two are interchangeable identity forms of the same contract, not a
# tracker-only feature. Delegate to that module rather than re-deriving the
# pattern, so the two never drift apart (see docs/superpowers/specs/
# 2026-09-03-jswarm-public-repo-split-design.md section 4).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from jswarm.workitem import identity as _workitem_identity


def _is_valid_ticket_id(ticket: str) -> bool:
    try:
        _workitem_identity.parse(ticket)
    except _workitem_identity.WorkItemIdError:
        return False
    return True


CONTROLLED_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills/jTest"
ALLOWED_RULE_STATUSES = {"active", "inactive"}
ALLOWED_PATTERN_STATUSES = {"active", "inactive"}
ALLOWED_PATTERN_SOURCES = {"controlled-config", "ticket-local"}
REQUIRED_RULE_FIELDS = (
    "id", "status", "imperative", "point_of_use", "stop_condition",
    "required_evidence", "receipt_pointers", "applies_to", "process_refs", "supersedes",
)
PREVIEW_TTL = timedelta(minutes=30)
MIGRATION_SEMANTICS = (
    ("HOT row", ("## Journeys (hot)", "| # | UAT ready? | Who | Journey")),
    ("COLD row", ("## Journeys (cold)",)),
    ("header", ("# UAT — CURRENT ROUND", "**Round:**", "**Last refreshed:**", "**Stack:**")),
    ("Definitions", ("## Definitions",)),
    ("Keys", ("**Keys**",)),
    ("Sets", ("**Sets**",)),
    ("sources links", ("Data sources for this table",)),
    ("deploy status", ("**Deploy status:**",)),
    ("readiness", ("UAT ready?",)),
    ("Notes", ("## Notes",)),
    ("session links", ("CLICKABLE SESSION LINKS",)),
    ("NFR references", ("NFR", "Closes")),
)


class ValidationError(ValueError):
    """A bounded input violates the UAT contract."""


@dataclass(frozen=True, slots=True)
class StackGateReceipt:
    certified_build_hash: str
    certification_source: str
    certification_receipt_path: str
    certification_receipt_sha256: str
    app_url: str
    login: str
    deploy_status: str


@dataclass(frozen=True, slots=True)
class PrewalkGateReceipt:
    certified_build_hash: str
    package_id: str
    package_hash: str
    script_id: str
    script_hash: str
    journeys_walked: int
    journeys_total: int
    atoms_passed: int
    atoms_total: int
    known_check_receipt: str
    recovery_use: Literal["none", "used"]
    observer_status: Literal["attached", "fallback-declared"]
    completed_at: str
    owner_may_walk: Literal["yes", "no"]
    canary_path: str
    receipt_path: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class WalkScriptGateReceipt:
    walk_script_path: str
    sealed_round_anchor: str


WALK_SCRIPT_UNVERIFIED_ROW = "**Walk-script:** <UNVERIFIED — PREP must supply a present, current owner walk script before issuance>".encode("utf-8")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path} must contain a JSON object")
    return value


def _nonempty_strings(value: Any, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValidationError(f"{label} must be {'an' if allow_empty else 'a non-empty'} array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValidationError(f"{label} must contain only non-empty strings")


def _validate_rules(document: dict[str, Any]) -> tuple[str, dict[str, dict[str, Any]]]:
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 1 or document.get("ruleset_id") != "uat-master":
        raise ValidationError("rules require schema_version 1 and ruleset_id 'uat-master'")
    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValidationError("rules must be a non-empty array")

    indexed: dict[str, dict[str, Any]] = {}
    for offset, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValidationError(f"rules[{offset}] must be an object")
        missing = [field for field in REQUIRED_RULE_FIELDS if field not in rule]
        if missing:
            raise ValidationError(f"rules[{offset}] missing fields: {', '.join(missing)}")
        rule_id = rule["id"]
        if not isinstance(rule_id, str) or not RULE_ID.fullmatch(rule_id):
            raise ValidationError(f"rules[{offset}].id is invalid: {rule_id!r}")
        if rule_id in indexed:
            raise ValidationError(f"duplicate rule id: {rule_id}")
        if rule["status"] not in ALLOWED_RULE_STATUSES:
            raise ValidationError(f"{rule_id} has unknown status: {rule['status']!r}")
        for field in ("imperative", "stop_condition"):
            if not isinstance(rule[field], str) or not rule[field].strip():
                raise ValidationError(f"{rule_id}.{field} must be non-empty")
        for field in ("point_of_use", "required_evidence", "applies_to", "process_refs"):
            _nonempty_strings(rule[field], f"{rule_id}.{field}")
        _nonempty_strings(rule["supersedes"], f"{rule_id}.supersedes", allow_empty=True)
        receipts = rule["receipt_pointers"]
        if not isinstance(receipts, list) or not receipts:
            raise ValidationError(f"{rule_id}.receipt_pointers must be a non-empty array")
        for receipt in receipts:
            if not isinstance(receipt, dict) or not all(
                isinstance(receipt.get(field), str) and receipt[field].strip()
                for field in ("key", "date", "note")
            ):
                raise ValidationError(f"{rule_id} has an invalid receipt pointer")
            try:
                date.fromisoformat(receipt["date"])
            except ValueError as exc:
                raise ValidationError(f"{rule_id} has invalid receipt date: {receipt['date']!r}") from exc
        indexed[rule_id] = rule
    return document["ruleset_id"], indexed


def _validate_receipt_pointers(rules: dict[str, dict[str, Any]]) -> None:
    reference_path = CONTROLLED_SKILL_ROOT / "UAT_RULE_RECEIPTS_REFERENCE.md"
    try:
        reference_lines = reference_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValidationError(f"cannot load receipt reference {reference_path}: {exc}") from exc

    for rule_id, rule in rules.items():
        for receipt in rule["receipt_pointers"]:
            literal = f"`{receipt['key']}`"
            matches = [line for line in reference_lines if literal in line]
            if len(matches) != 1:
                raise ValidationError(
                    f"{rule_id} receipt key {receipt['key']!r} must resolve exactly once; matches={len(matches)}"
                )
            if rule_id not in matches[0]:
                raise ValidationError(f"{rule_id} receipt key {receipt['key']!r} is bound to a different rule")


def _contained_file(root: Path, relative: str, label: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValidationError(f"{label} path must be relative and contained: {relative!r}")
    resolved_root = root.resolve(strict=True)
    candidate = resolved_root / path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValidationError(f"{label} does not exist: {candidate}") from exc
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise ValidationError(f"{label} escapes its allowed root: {candidate}")
    if candidate.is_symlink() or resolved != candidate:
        raise ValidationError(f"{label} must not traverse symlinks: {candidate}")
    if not resolved.is_file():
        raise ValidationError(f"{label} is not a file: {resolved}")
    return resolved


def _resolve_template(
    pattern: dict[str, Any], patterns_path: Path, *, ticket_dir: Path | None = None,
) -> Path:
    if pattern["source"] == "controlled-config":
        return _contained_file(CONTROLLED_SKILL_ROOT, pattern["template"], f"{pattern['id']} template")
    local_root = patterns_path.parent
    if ticket_dir is not None:
        resolved_ticket_dir = ticket_dir.resolve(strict=True)
        resolved_registry = patterns_path.resolve(strict=True)
        if resolved_registry.parent != resolved_ticket_dir or patterns_path.is_symlink():
            raise ValidationError(
                f"ticket-local pattern registry must be directly under the selected ticket folder: {ticket_dir}"
            )
        local_root = resolved_ticket_dir
    resolved_template = _contained_file(local_root, pattern["template"], f"{pattern['id']} template")
    if ticket_dir is not None and resolved_template.parent != local_root:
        raise ValidationError(
            f"ticket-local template must be directly under the selected ticket folder: {ticket_dir}"
        )
    return resolved_template


def _validate_patterns(
    document: dict[str, Any], ruleset_id: str, rules: dict[str, dict[str, Any]], patterns_path: Path,
) -> dict[str, dict[str, Any]]:
    if type(document.get("schema_version")) is not int or document.get("schema_version") != 1:
        raise ValidationError("patterns require strict integer schema_version 1")
    patterns = document.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValidationError("patterns must be a non-empty array")
    indexed: dict[str, dict[str, Any]] = {}
    mandatory = {
        rule_id for rule_id, rule in rules.items()
        if rule["status"] == "active" and rule["applies_to"] == ["*"]
    }
    for offset, pattern in enumerate(patterns):
        if not isinstance(pattern, dict):
            raise ValidationError(f"patterns[{offset}] must be an object")
        pattern_id = pattern.get("id")
        if not isinstance(pattern_id, str) or not pattern_id:
            raise ValidationError(f"patterns[{offset}].id must be non-empty")
        if pattern_id in indexed:
            raise ValidationError(f"duplicate pattern id: {pattern_id}")
        if pattern.get("status") not in ALLOWED_PATTERN_STATUSES:
            raise ValidationError(f"{pattern_id} has unknown status: {pattern.get('status')!r}")
        if pattern.get("source") not in ALLOWED_PATTERN_SOURCES:
            raise ValidationError(f"{pattern_id} has unknown source: {pattern.get('source')!r}")
        if type(pattern.get("version")) is not int or pattern["version"] < 1:
            raise ValidationError(f"{pattern_id}.version must be a positive integer")
        if pattern.get("rule_set") != ruleset_id:
            raise ValidationError(f"{pattern_id} must bind ruleset {ruleset_id!r}")
        rule_ids = pattern.get("rule_ids")
        _nonempty_strings(rule_ids, f"{pattern_id}.rule_ids")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValidationError(f"{pattern_id} contains duplicate rule references")
        for rule_id in rule_ids:
            rule = rules.get(rule_id)
            if rule is None:
                raise ValidationError(f"{pattern_id} references unknown rule: {rule_id}")
            if rule["status"] != "active":
                raise ValidationError(f"{pattern_id} references inactive rule: {rule_id}")
        if set(rule_ids) != mandatory:
            missing = sorted(mandatory - set(rule_ids))
            extra = sorted(set(rule_ids) - mandatory)
            raise ValidationError(f"{pattern_id} mandatory rule binding mismatch; missing={missing}, extra={extra}")
        if not isinstance(pattern.get("template"), str) or not pattern["template"]:
            raise ValidationError(f"{pattern_id}.template must be non-empty")
        _resolve_template(pattern, patterns_path)
        materialization = pattern.get("materialization")
        if not isinstance(materialization, dict):
            raise ValidationError(f"{pattern_id}.materialization must be an object")
        expected_materialization = {
            "output_name": "<TICKET>.UAT-CURRENT-ROUND.md",
            "mode": "wholesale-replace-on-create; patch-in-place-mid-round",
            "compatibility_baseline": "UAT_CURRENT_ROUND_TEMPLATE.md@pre-",
        }
        if materialization != expected_materialization:
            raise ValidationError(
                f"{pattern_id}.materialization must match the v1 round contract: {expected_materialization}"
            )
        indexed[pattern_id] = pattern
    return indexed


def _contracts(rules_path: Path, patterns_path: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    ruleset_id, rules = _validate_rules(_load(rules_path))
    _validate_receipt_pointers(rules)
    patterns = _validate_patterns(_load(patterns_path), ruleset_id, rules, patterns_path)
    return ruleset_id, patterns


def validate(rules_path: Path, patterns_path: Path) -> None:
    _contracts(rules_path, patterns_path)


def _selected_pattern(
    rules_path: Path, patterns_path: Path, selected: str, *, ticket_dir: Path | None = None,
) -> tuple[str, dict[str, Any], Path]:
    ruleset_id, patterns = _contracts(rules_path, patterns_path)
    pattern = patterns.get(selected)
    active = sorted(pattern_id for pattern_id, value in patterns.items() if value["status"] == "active")
    if pattern is None or pattern["status"] != "active":
        raise ValidationError(f"{selected!r} is not a valid pattern; active patterns: {', '.join(active) or '(none)'}")
    return ruleset_id, pattern, _resolve_template(pattern, patterns_path, ticket_dir=ticket_dir)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_sha256(value: Any) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _sha256_bytes(content)


def gwt_content_sha256(given: list[str], when: list[str], then: list[str]) -> str:
    """Digest exact ordered nonempty GWT arrays as canonical UTF-8 JSON."""
    if not all(isinstance(values, list) and values and all(isinstance(value, str) and value.strip() for value in values) for values in (given, when, then)):
        _invalid("package.invalid.scenario")
    return _canonical_sha256({"given": given, "when": when, "then": then})


CANONICAL_PACKAGE_SCHEMA = "uat-canonical-package@1"
CANONICAL_PACKAGE_SCHEMA_V2 = "uat-canonical-package@2"
RECOVERY_POLICY = "SETUP_REFRESH_ONLY; POST_ACTION_REFRESH_RELOAD_RETRY_CANNOT_PASS_ORIGINAL_ATOM"
PACKAGE_STATES = frozenset({"DRAFT_SEALED", "QA_VERIFIED", "ISSUED"})
UNDERIVED_PACKAGE_FIELDS = (
    "schema_version", "ticket", "round_id", "certified_build_hash", "folder_path", "app_url",
    "login", "observer", "recovery_policy", "known_sources_checked", "journeys",
)
DERIVED_PACKAGE_FIELDS = (
    "package_id", "package_hash", "sealed_payload_sha256", "script_id", "script_hash",
)
PROJECTION_FIELDS = (
    "ticket", "round_id", "package_id", "package_hash", "sealed_payload_sha256", "script_id",
    "script_hash", "certified_build_hash", "folder_path", "app_url", "login", "observer",
    "recovery_policy", "known_sources_checked", "journeys",
)
NORMALIZED_PACKAGE_FIELDS = (
    "schema_version", *PROJECTION_FIELDS[:5], "script_id", "script_hash", *PROJECTION_FIELDS[7:],
    "round", "feedback",
)
JOURNEY_FIELDS = (
    "journey_id", "source", "scenario_id", "gwt_sha256", "uat_test_anchor", "actions", "outcomes", "atom_ids",
    "known_items", "requirement_ref",
)
OUTCOME_FIELDS = ("atom_id", "expected", "fail_if")
KNOWN_ITEM_FIELDS = ("text", "source_ref", "step_refs")
V2_JOURNEY_FIELDS = ("journey_id", "title", "source", "uat_test_anchor", "scenarios", "steps", "outcomes", "atom_ids", "known_items", "requirement_ref", "app_link")
V2_STEP_FIELDS = ("step_id", "ordinal", "name", "instruction", "expected_outcome", "scenario_links", "assessment_options", "app_link")
OBSERVER_FIELDS = ("available", "capture", "fallback")
ROUND_ID = re.compile(r"^round-[a-z0-9]+(?:-[a-z0-9]+)*$")
LOWERCASE_HEX = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
JOURNEY_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _invalid(token: str) -> NoReturn:
    raise ValidationError(token)


def _package_fields(
    value: object, fields: tuple[str, ...], token: str, *, optional_fields: tuple[str, ...] = ()
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _invalid(token)
    mapping = cast(Mapping[str, object], value)
    keys = set(mapping)
    expected = set(fields)
    missing = sorted((expected - set(optional_fields)) - keys)
    if missing:
        _invalid(f"package.missing.{missing[0]}")
    extra = sorted(keys - expected)
    if extra:
        _invalid(f"package.extra.{extra[0]}")
    return mapping


def _package_nonempty(value: object, token: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(token)
    return value


def _package_string_list(value: object, token: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        _invalid(token)
    items = cast(list[object], value)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        _invalid(token)
    return [cast(str, item) for item in items]


def _result_contract(*, require_runtime_match: bool = False) -> Mapping[str, object]:
    """Load and structurally validate the single controlled result-contract master."""
    master_path = Path(__file__).resolve().parents[1] / "skills/jTest/uat/feedback-result-contract.json"
    try:
        master = json.loads(master_path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError("package.invalid.feedback_result_contract") from error
    if not isinstance(master, Mapping):
        _invalid("package.invalid.feedback_result_contract")
    canonical = _package_string_list(master.get("canonical_assessments"), "package.invalid.feedback_result_contract")
    required = _package_string_list(master.get("required_assessments"), "package.invalid.feedback_result_contract")
    optional = _package_string_list(master.get("optional_assessments"), "package.invalid.feedback_result_contract", allow_empty=True)
    default = _package_string_list(master.get("legacy_default_assessment_options"), "package.invalid.feedback_result_contract")
    aliases = master.get("assessment_aliases")
    if (len(canonical) != len(set(canonical)) or len(required) != len(set(required))
            or len(optional) != len(set(optional)) or canonical != required + optional
            or default != required or not isinstance(aliases, Mapping)
            or any(not isinstance(alias, str) or not isinstance(target, str) or target not in canonical
                   for alias, target in aliases.items())):
        _invalid("package.invalid.feedback_result_contract")
    if require_runtime_match:
        from jswarm import uat_feedback
        try:
            runtime = uat_feedback.feedback_result_contract()
        except Exception as error:
            raise ValidationError("package.invalid.feedback_result_contract") from error
        if _canonical_sha256(runtime) != _canonical_sha256(master):
            _invalid("package.invalid.feedback_result_contract_skew")
    return master


def _validate_assessment_options(value: object) -> list[str]:
    contract = _result_contract()
    canonical = _package_string_list(contract["canonical_assessments"], "package.invalid.assessment_options")
    required = _package_string_list(contract["required_assessments"], "package.invalid.assessment_options")
    options = _package_string_list(value, "package.invalid.assessment_options")
    if (len(options) != len(set(options)) or any(option not in canonical for option in options)
            or any(option not in options for option in required)
            or options != [option for option in canonical if option in options]):
        _invalid("package.invalid.assessment_options")
    return options


def effective_assessment_options(step: Mapping[str, object]) -> list[str]:
    """Return authored options or the master legacy default without mutating a sealed step."""
    if "assessment_options" in step:
        return _validate_assessment_options(step["assessment_options"])
    contract = _result_contract()
    return _package_string_list(contract["legacy_default_assessment_options"], "package.invalid.assessment_options")


def _validate_app_url(value: object) -> str:
    app_url = _package_nonempty(value, "package.invalid.app_url")
    # Escape hatch for a ticket with no running application (a pure library
    # change, a CLI-only change, etc.): the plan template's own "UAT state
    # policy" field documents "N/A" as a legitimate value here, but nothing
    # previously accepted it -- a stranger had to invent a placeholder
    # http://... URL to get a round validated at all. A relative app_link
    # href (the documented convention, e.g. "/") never dereferences app_url
    # as a base, so "N/A" is safe downstream.
    if app_url == "N/A":
        return app_url
    if any(ord(character) < 32 or ord(character) == 127 for character in app_url):
        _invalid("package.invalid.app_url")
    try:
        parsed = urlsplit(app_url)
        _ = parsed.port
    except ValueError:
        _invalid("package.invalid.app_url")
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or any(character.isspace() for character in parsed.netloc)):
        _invalid("package.invalid.app_url")
    return app_url


def _validate_app_link(value: object, app_url: str) -> None:
    if not isinstance(value, Mapping) or set(value) - {"href", "label"} or "href" not in value:
        _invalid("package.invalid.app_link")
    link = cast(Mapping[str, object], value)
    href = _package_nonempty(link["href"], "package.invalid.app_link")
    if any(ord(character) < 32 or ord(character) == 127 for character in href) or "{" in href or "}" in href:
        _invalid("package.invalid.app_link")
    if "label" in link:
        _package_nonempty(link["label"], "package.invalid.app_link")
    try:
        base = urlsplit(app_url)
        parsed = urlsplit(href)
        _ = parsed.port
    except ValueError:
        _invalid("package.invalid.app_link")
    if href.startswith("/"):
        if href.startswith("//") or parsed.scheme or parsed.netloc:
            _invalid("package.invalid.app_link")
        return
    if (parsed.scheme not in {"http", "https"} or parsed.username is not None or parsed.password is not None
            or (parsed.scheme, parsed.hostname, parsed.port) != (base.scheme, base.hostname, base.port)):
        _invalid("package.invalid.app_link")


def _validate_step_refs(value: object, step_ids: set[str]) -> None:
    refs = _package_string_list(value, "package.invalid.known_item_step_refs")
    if len(refs) != len(set(refs)) or any(ref not in step_ids for ref in refs):
        _invalid("package.invalid.known_item_step_refs")


def _validate_observer(value: object) -> None:
    if not isinstance(value, Mapping) or "fallback" not in value:
        _invalid("package.invalid.observer_fallback")
    fallback_value = value["fallback"]
    if not isinstance(fallback_value, str) or not fallback_value.strip():
        _invalid("package.invalid.observer_fallback")
    observer = _package_fields(value, OBSERVER_FIELDS, "package.invalid.observer")
    available = observer["available"]
    capture = _package_nonempty(observer["capture"], "package.invalid.observer")
    fallback = _package_nonempty(observer["fallback"], "package.invalid.observer")
    if not isinstance(available, bool):
        _invalid("package.invalid.observer")
    if available and capture == "N/A":
        _invalid("package.invalid.observer")
    if not available and fallback == "N/A":
        _invalid("package.invalid.observer_fallback")


def _validate_journeys(value: object, known_sources: list[str]) -> None:
    if not isinstance(value, list) or not value:
        _invalid("package.invalid.journeys")
    journey_ids: set[str] = set()
    atom_ids: set[str] = set()
    journeys = cast(list[object], value)
    for item in journeys:
        journey = _package_fields(item, JOURNEY_FIELDS, "package.invalid.journey")
        journey_id = _package_nonempty(journey["journey_id"], "package.missing.journey_id")
        if not JOURNEY_ID.fullmatch(journey_id):
            _invalid("package.invalid.journey_id")
        if journey_id in journey_ids:
            _invalid("package.duplicate.journey_id")
        journey_ids.add(journey_id)
        for field in ("source", "scenario_id", "gwt_sha256", "uat_test_anchor"):
            _package_nonempty(journey[field], f"package.missing.{field}")
        if not _SHA256.fullmatch(cast(str, journey["gwt_sha256"])):
            _invalid("package.invalid.gwt_sha256")
        _package_string_list(journey["actions"], "package.invalid.actions")
        declared_atoms = _package_string_list(journey["atom_ids"], "package.invalid.outcome_atom")
        if len(declared_atoms) != len(set(declared_atoms)):
            _invalid("package.invalid.outcome_atom")
        outcomes = journey["outcomes"]
        if not isinstance(outcomes, list) or not outcomes:
            _invalid("package.invalid.outcome_atom")
        outcome_atoms: list[str] = []
        for outcome_value in cast(list[object], outcomes):
            outcome = _package_fields(outcome_value, OUTCOME_FIELDS, "package.invalid.outcome_atom")
            atom_id = _package_nonempty(outcome["atom_id"], "package.invalid.outcome_atom")
            _package_nonempty(outcome["expected"], "package.invalid.outcome_atom")
            _package_string_list(outcome["fail_if"], "package.invalid.outcome_atom")
            outcome_atoms.append(atom_id)
        if declared_atoms != outcome_atoms or any(atom in atom_ids for atom in declared_atoms):
            _invalid("package.invalid.outcome_atom")
        atom_ids.update(declared_atoms)
        known_items = journey["known_items"]
        if not isinstance(known_items, list):
            _invalid("package.invalid.known_item_provenance")
        for known_value in cast(list[object], known_items):
            if not isinstance(known_value, Mapping) or "source_ref" not in known_value:
                _invalid("package.invalid.known_item_provenance")
            known = _package_fields(known_value, KNOWN_ITEM_FIELDS, "package.invalid.known_item_provenance", optional_fields=("step_refs",))
            _package_nonempty(known["text"], "package.invalid.known_item_provenance")
            source_ref = _package_nonempty(known["source_ref"], "package.invalid.known_item_provenance")
            if source_ref not in known_sources:
                _invalid("package.invalid.known_item_provenance")
        requirement_ref = _package_nonempty(journey["requirement_ref"], "package.missing.requirement_ref")
        if requirement_ref != requirement_ref.strip():
            _invalid("package.invalid.requirement_ref")


def _validate_v2_journeys(value: object, known_sources: list[str]) -> None:
    """Validate nested scenario/GWT lineage without flattening ownership."""
    if not isinstance(value, list) or not value:
        _invalid("package.invalid.journeys")
    step_ids: set[str] = set()
    for item in value:
        journey = _package_fields(item, V2_JOURNEY_FIELDS, "package.invalid.journey", optional_fields=("app_link",))
        _package_nonempty(journey["journey_id"], "package.missing.journey_id")
        _package_nonempty(journey["title"], "package.missing.title")
        scenarios = journey["scenarios"]
        if not isinstance(scenarios, list) or not scenarios:
            _invalid("package.invalid.scenarios")
        scenario_order: list[str] = []
        scenario_refs: dict[str, list[str]] = {}
        for scenario_value in scenarios:
            scenario = _package_fields(scenario_value, ("scenario_id", "title", "gwt"), "package.invalid.scenario")
            scenario_id = _package_nonempty(scenario["scenario_id"], "package.invalid.scenario")
            if scenario_id in scenario_refs:
                _invalid("package.invalid.scenario")
            blocks = scenario["gwt"]
            if not isinstance(blocks, list) or not blocks:
                _invalid("package.invalid.scenario")
            refs: list[str] = []
            for block_value in blocks:
                block = _package_fields(block_value, ("gwt_ref", "sha256", "given", "when", "then"), "package.invalid.scenario")
                clauses = [block[field] for field in ("given", "when", "then")]
                if not all(isinstance(part, list) and part and all(isinstance(text, str) and text.strip() for text in part) for part in clauses):
                    _invalid("package.invalid.scenario")
                ref = _package_nonempty(block["gwt_ref"], "package.invalid.scenario")
                digest = gwt_content_sha256(cast(list[str], block["given"]), cast(list[str], block["when"]), cast(list[str], block["then"]))
                if ref != block["sha256"] or ref != digest or ref in refs:
                    _invalid("package.invalid.scenario")
                refs.append(ref)
            scenario_order.append(scenario_id)
            scenario_refs[scenario_id] = refs
        steps = journey["steps"]
        if not isinstance(steps, list) or not steps:
            _invalid("package.invalid.steps")
        for ordinal, step_value in enumerate(steps, 1):
            if isinstance(step_value, Mapping) and "gwt_refs" in step_value:
                _invalid("package.invalid.step")
            step = _package_fields(step_value, V2_STEP_FIELDS, "package.invalid.step", optional_fields=("assessment_options", "app_link"))
            step_id = _package_nonempty(step["step_id"], "package.invalid.step")
            if step_id in step_ids:
                _invalid("package.duplicate.step_id")
            step_ids.add(step_id)
            if step["ordinal"] != ordinal or not all(isinstance(step[field], str) and step[field].strip() for field in ("name", "instruction", "expected_outcome")):
                _invalid("package.invalid.step")
            links = step["scenario_links"]
            if not isinstance(links, list) or not links:
                _invalid("package.invalid.step_lineage")
            link_ids: list[str] = []
            for link_value in links:
                link = _package_fields(link_value, ("scenario_id", "gwt_refs"), "package.invalid.step")
                scenario_id = _package_nonempty(link["scenario_id"], "package.invalid.step_lineage")
                refs = _package_string_list(link["gwt_refs"], "package.invalid.step_lineage")
                if scenario_id in link_ids or scenario_id not in scenario_refs or len(refs) != len(set(refs)) or any(ref not in scenario_refs[scenario_id] for ref in refs):
                    _invalid("package.invalid.step_lineage")
                if refs != [ref for ref in scenario_refs[scenario_id] if ref in refs]:
                    _invalid("package.invalid.step_lineage")
                link_ids.append(scenario_id)
            if link_ids != [scenario_id for scenario_id in scenario_order if scenario_id in link_ids]:
                _invalid("package.invalid.step_lineage")
        journey_step_ids: set[str] = set()
        for step_value in steps:
            if isinstance(step_value, Mapping):
                journey_step_ids.add(cast(str, step_value["step_id"]))
        for known_value in cast(list[object], journey["known_items"]):
            if isinstance(known_value, Mapping) and "step_refs" in known_value:
                _validate_step_refs(known_value["step_refs"], journey_step_ids)
        legacy = {key: journey[key] for key in ("journey_id", "source", "uat_test_anchor", "outcomes", "atom_ids", "known_items", "requirement_ref")}
        legacy["scenario_id"] = scenario_order[0]
        legacy["gwt_sha256"] = scenario_refs[scenario_order[0]][0]
        legacy["actions"] = [step["instruction"] for step in steps if isinstance(step, Mapping)]
        _validate_journeys([legacy], known_sources)


def _validate_immutable_manifest(manifest: Mapping[str, object]) -> None:
    recovery = manifest.get("recovery_policy")
    if not isinstance(recovery, str) or not recovery.strip():
        _invalid("package.invalid.recovery_policy")
    _package_fields(manifest, UNDERIVED_PACKAGE_FIELDS, "package.invalid.manifest")
    if manifest["schema_version"] not in {CANONICAL_PACKAGE_SCHEMA, CANONICAL_PACKAGE_SCHEMA_V2}:
        _invalid("package.invalid.schema_version")
    ticket = _package_nonempty(manifest["ticket"], "package.invalid.ticket")
    if not _is_valid_ticket_id(ticket):
        _invalid("package.invalid.ticket")
    round_id = _package_nonempty(manifest["round_id"], "package.invalid.round_id")
    if not ROUND_ID.fullmatch(round_id):
        _invalid("package.invalid.round_id")
    build_hash = _package_nonempty(manifest["certified_build_hash"], "package.invalid.certified_build_hash")
    if not LOWERCASE_HEX.fullmatch(build_hash):
        _invalid("package.invalid.certified_build_hash")
    _package_nonempty(manifest["folder_path"], "package.invalid.folder_path")
    app_url = _validate_app_url(manifest["app_url"])
    _package_nonempty(manifest["login"], "package.invalid.login")
    _validate_observer(manifest["observer"])
    if manifest["recovery_policy"] != RECOVERY_POLICY:
        _invalid("package.invalid.recovery_policy")
    known_sources = _package_string_list(manifest["known_sources_checked"], "package.invalid.known_sources_checked")
    if manifest["schema_version"] == CANONICAL_PACKAGE_SCHEMA_V2:
        _validate_v2_journeys(manifest["journeys"], known_sources)
        for journey_value in cast(list[object], manifest["journeys"]):
            journey = cast(Mapping[str, object], journey_value)
            if "app_link" in journey:
                _validate_app_link(journey["app_link"], app_url)
            for step_value in cast(list[object], journey["steps"]):
                step = cast(Mapping[str, object], step_value)
                if "assessment_options" in step:
                    _validate_assessment_options(step["assessment_options"])
                if "app_link" in step:
                    _validate_app_link(step["app_link"], app_url)
    else:
        _validate_journeys(manifest["journeys"], known_sources)


def render_sealed_script_bytes(sealed_payload: Mapping[str, object]) -> bytes:
    _validate_immutable_manifest(sealed_payload)
    if sealed_payload["schema_version"] == CANONICAL_PACKAGE_SCHEMA_V2:
        header = "\n".join((
            "# Canonical UAT Package", "", f"Ticket: {sealed_payload['ticket']}",
            f"Round: {sealed_payload['round_id']}", f"Certified build: {sealed_payload['certified_build_hash']}", "",
        )).encode("utf-8")
        return header + render_owner_journey_bytes(sealed_payload)
    observer = _package_fields(sealed_payload["observer"], OBSERVER_FIELDS, "package.invalid.observer")
    journeys = sealed_payload["journeys"]
    assert isinstance(journeys, list)
    known_sources = sealed_payload["known_sources_checked"]
    assert isinstance(known_sources, list)
    lines = [
        "# Canonical UAT Package", "", f"Ticket: {sealed_payload['ticket']}",
        f"Round: {sealed_payload['round_id']}", f"Certified build: {sealed_payload['certified_build_hash']}",
        f"Folder: {sealed_payload['folder_path']}", f"App URL: {sealed_payload['app_url']}",
        f"Login: {sealed_payload['login']}",
        f"Observer available: {str(observer['available']).lower()}",
        f"Observer capture: {observer['capture']}", f"Observer fallback: {observer['fallback']}",
        f"Recovery policy: {sealed_payload['recovery_policy']}",
        f"Known sources checked: {', '.join(known_sources)}", "",
    ]
    for index, journey_value in enumerate(journeys):
        journey = _package_fields(journey_value, JOURNEY_FIELDS, "package.invalid.journey")
        lines.extend((
            f"## Journey {journey['journey_id']}", f"Source: {journey['source']}",
            f"Scenario: {journey['scenario_id']}", f"Official GWT SHA-256: {journey['gwt_sha256']}", f"UAT-test anchor: {journey['uat_test_anchor']}", "Actions:",
        ))
        actions = journey["actions"]
        assert isinstance(actions, list)
        lines.extend(f"{number}. {action}" for number, action in enumerate(actions, 1))
        lines.append("Outcomes:")
        outcomes = journey["outcomes"]
        assert isinstance(outcomes, list)
        for outcome_value in outcomes:
            outcome = _package_fields(outcome_value, OUTCOME_FIELDS, "package.invalid.outcome_atom")
            lines.extend((f"- Atom: {outcome['atom_id']}", f"  Expected: {outcome['expected']}"))
            fail_if = outcome["fail_if"]
            assert isinstance(fail_if, list)
            lines.extend(f"  Fail if: {clause}" for clause in fail_if)
        lines.append("Known items:")
        known_items = journey["known_items"]
        assert isinstance(known_items, list)
        for known_value in known_items:
            known = _package_fields(known_value, KNOWN_ITEM_FIELDS, "package.invalid.known_item_provenance", optional_fields=("step_refs",))
            lines.append(f"- {known['text']} ({known['source_ref']})")
        lines.append(f"Requirement ref: {journey['requirement_ref']}")
        if index != len(journeys) - 1:
            lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_owner_journey_bytes(projection: Mapping[str, object]) -> bytes:
    journeys = projection.get("journeys")
    if not isinstance(journeys, list):
        _invalid("package.invalid.journeys")
    if projection.get("schema_version") == CANONICAL_PACKAGE_SCHEMA_V2 or any(isinstance(item, Mapping) and "steps" in item for item in journeys):
        lines: list[str] = []
        for index, journey_value in enumerate(journeys, 1):
            if not isinstance(journey_value, Mapping):
                _invalid("package.invalid.journey")
            title = _package_nonempty(journey_value.get("title"), "package.missing.title")
            steps = journey_value.get("steps")
            if not isinstance(steps, list):
                _invalid("package.invalid.steps")
            lines.extend((f"### Journey {index} — {title}", "Steps:"))
            if "app_link" in journey_value:
                link = cast(Mapping[str, object], journey_value["app_link"])
                lines.append(f"Link: {link['href']}" + (f" ({link['label']})" if "label" in link else ""))
            for step in steps:
                if not isinstance(step, Mapping):
                    _invalid("package.invalid.step")
                lines.append(f"{step['ordinal']}. {step['name']}: {step['instruction']}\n   Expected: {step['expected_outcome']}")
                if "assessment_options" in step:
                    lines.append(f"   Allowed results: {', '.join(effective_assessment_options(cast(Mapping[str, object], step)))}")
                if "app_link" in step:
                    link = cast(Mapping[str, object], step["app_link"])
                    lines.append(f"   Link: {link['href']}" + (f" ({link['label']})" if "label" in link else ""))
            known_items = journey_value.get("known_items")
            if isinstance(known_items, list) and any(isinstance(item, Mapping) and "step_refs" in item for item in known_items):
                lines.append("Known items:")
                for item in known_items:
                    if isinstance(item, Mapping) and "step_refs" in item:
                        lines.append(f"- {item['text']} (steps: {', '.join(cast(list[str], item['step_refs']))})")
            if index != len(journeys):
                lines.append("")
        return ("\n".join(lines) + "\n").encode("utf-8")
    lines: list[str] = []
    for index, journey_value in enumerate(journeys):
        journey = _package_fields(journey_value, JOURNEY_FIELDS, "package.invalid.journey")
        lines.extend((
            f"### Journey {journey['journey_id']}", f"Source: {journey['source']}",
            f"Scenario: {journey['scenario_id']}", f"Official GWT SHA-256: {journey['gwt_sha256']}", f"UAT-test anchor: {journey['uat_test_anchor']}", "Actions:",
        ))
        actions = _package_string_list(journey["actions"], "package.invalid.actions")
        lines.extend(f"{ordinal}. {action}" for ordinal, action in enumerate(actions, 1))
        lines.append("Outcomes:")
        outcomes = journey["outcomes"]
        if not isinstance(outcomes, list):
            _invalid("package.invalid.outcome_atom")
        for outcome_value in outcomes:
            outcome = _package_fields(outcome_value, OUTCOME_FIELDS, "package.invalid.outcome_atom")
            lines.extend((f"- Atom: {outcome['atom_id']}", f"  Expected: {outcome['expected']}"))
            lines.extend(f"  Fail if: {clause}" for clause in _package_string_list(outcome["fail_if"], "package.invalid.outcome_atom"))
        lines.append(f"Atom IDs: {', '.join(_package_string_list(journey['atom_ids'], 'package.invalid.outcome_atom'))}")
        lines.append("Known items:")
        known_items = journey["known_items"]
        if not isinstance(known_items, list):
            _invalid("package.invalid.known_item_provenance")
        for known_value in known_items:
            known = _package_fields(known_value, KNOWN_ITEM_FIELDS, "package.invalid.known_item_provenance", optional_fields=("step_refs",))
            lines.append(f"- {known['text']} ({known['source_ref']})")
        lines.append(f"Requirement ref: {journey['requirement_ref']}")
        if index != len(journeys) - 1:
            lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_normalized_package_annex_bytes(package: Mapping[str, object]) -> bytes:
    return (
        "BEGIN NORMALIZED PACKAGE\n```json\n"
        + json.dumps(package, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n```\nEND NORMALIZED PACKAGE\n"
    ).encode("utf-8")


def render_canonical_package_block_bytes(package: Mapping[str, object], *, package_state: str) -> bytes:
    if package_state not in PACKAGE_STATES:
        _invalid("package.invalid.package_state")
    projection = package.get("round")
    if not isinstance(projection, Mapping):
        _invalid("package.invalid.projection_drift")
    observer = _package_fields(package.get("observer"), OBSERVER_FIELDS, "package.invalid.observer")
    sources = _package_string_list(package.get("known_sources_checked"), "package.invalid.known_sources_checked")
    lines = [
        "## Canonical package (generated, owner-readable, and self-contained)",
        f"**package_state:** `{package_state}`", f"**schema_version:** `{package['schema_version']}`",
        f"**ticket:** `{package['ticket']}` · **round_id:** `{package['round_id']}` · **package_id:** `{package['package_id']}` · **package_hash:** `{package['package_hash']}`",
        f"**sealed_payload_sha256:** `{package['sealed_payload_sha256']}` · **script_id:** `{package['script_id']}` · **script_hash:** `{package['script_hash']}` · **certified_build_hash:** `{package['certified_build_hash']}`",
        f"**folder_path:** `{package['folder_path']}` · **app_url:** `{package['app_url']}` · **login:** `{package['login']}`",
        f"**observer:** `available={str(observer['available']).lower()}` · **capture:** `{observer['capture']}` · **fallback:** `{observer['fallback']}`",
        f"**recovery_policy:** `{package['recovery_policy']}`",
        f"**known_sources_checked:** {', '.join(sources)}", "",
        "Setup refresh is allowed only before the observed action. Post-action refresh, reload, or retry cannot PASS the original atom.", "",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8") + render_owner_journey_bytes(projection) + b"\n"


def _v2_gwt_binding(package: Mapping[str, object]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Return a lossless journey → scenario → ordered GWT digest binding.

    V2 has no journey-level digest.  This projection deliberately preserves every
    scenario and every ordered GWT block; the validated nested package remains the
    authority and no compatibility digest is minted.
    """
    journeys = package["journeys"]
    assert isinstance(journeys, list)
    binding: dict[str, dict[str, tuple[str, ...]]] = {}
    for journey_value in journeys:
        journey = cast(Mapping[str, object], journey_value)
        scenarios = journey["scenarios"]
        assert isinstance(scenarios, list)
        scenario_binding: dict[str, tuple[str, ...]] = {}
        for scenario_value in scenarios:
            scenario = cast(Mapping[str, object], scenario_value)
            blocks = scenario["gwt"]
            assert isinstance(blocks, list)
            scenario_binding[_package_nonempty(scenario["scenario_id"], "package.invalid.gwt_binding")] = tuple(
                _package_nonempty(cast(Mapping[str, object], block)["sha256"], "package.invalid.gwt_binding")
                for block in blocks
            )
        binding[_package_nonempty(journey["journey_id"], "package.invalid.gwt_binding")] = scenario_binding
    return binding


def _valid_gwt_binding(package: Mapping[str, object], binding: Mapping[str, object] | None) -> bool:
    if binding is None:
        return False
    if package.get("schema_version") == CANONICAL_PACKAGE_SCHEMA_V2:
        return dict(binding) == _v2_gwt_binding(package)
    journeys = package["journeys"]
    assert isinstance(journeys, list)
    return set(binding) == {_package_nonempty(cast(Mapping[str, object], journey)["journey_id"], "package.invalid.gwt_binding") for journey in journeys} and all(
        binding[_package_nonempty(cast(Mapping[str, object], journey)["journey_id"], "package.invalid.gwt_binding")]
        == cast(Mapping[str, object], journey)["gwt_sha256"]
        for journey in journeys
    )


def package_semantic_items(package: Mapping[str, object], *, origin: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    def add(item_id: str, value: object) -> None:
        line = len(items) + 1
        items.append({
            "source_item_id": item_id, "kind": "canonical-package-object", "origin": origin,
            "source_location": {
                "section": "canonical-package", "key": item_id,
                "line_start": line, "line_end": line,
            },
            "semantic_value_sha256": _canonical_sha256(value), "exact_bytes_sha256": _canonical_sha256(value),
            "semantic_value": value,
        })
    for field in NORMALIZED_PACKAGE_FIELDS:
        add(f"package:root:{field}", package[field])
    observer = _package_fields(package["observer"], OBSERVER_FIELDS, "package.invalid.observer")
    for field in OBSERVER_FIELDS:
        add(f"package:observer:{field}", observer[field])
    sources = _package_string_list(package["known_sources_checked"], "package.invalid.known_sources_checked")
    for ordinal, value in enumerate(sources, 1):
        add(f"package:known-source:{ordinal}", value)
    journeys = package["journeys"]
    if not isinstance(journeys, list):
        _invalid("package.invalid.journeys")
    if package.get("schema_version") == CANONICAL_PACKAGE_SCHEMA_V2:
        # The annex carries each nested journey exactly.  Keep one semantic item per
        # journey so scenario/GWT/step lineage is covered as an indivisible v2 object.
        for journey_value in journeys:
            journey = _package_fields(
                journey_value,
                V2_JOURNEY_FIELDS,
                "package.invalid.journey", optional_fields=("app_link",),
            )
            journey_id = _package_nonempty(journey["journey_id"], "package.missing.journey_id")
            add(f"package:journey:{journey_id}:v2", journey)
    else:
        for journey_value in journeys:
            journey = _package_fields(journey_value, JOURNEY_FIELDS, "package.invalid.journey")
            journey_id = _package_nonempty(journey["journey_id"], "package.missing.journey_id")
            for field in ("source", "scenario_id", "gwt_sha256", "uat_test_anchor", "requirement_ref"):
                add(f"package:journey:{journey_id}:{field}", journey[field])
            for ordinal, action in enumerate(_package_string_list(journey["actions"], "package.invalid.actions"), 1):
                add(f"package:journey:{journey_id}:action:{ordinal}", action)
            for ordinal, outcome_value in enumerate(cast(list[object], journey["outcomes"]), 1):
                outcome = _package_fields(outcome_value, OUTCOME_FIELDS, "package.invalid.outcome_atom")
                add(f"package:journey:{journey_id}:outcome:{ordinal}:atom_id", outcome["atom_id"])
                add(f"package:journey:{journey_id}:outcome:{ordinal}:expected", outcome["expected"])
                for failure_ordinal, failure in enumerate(_package_string_list(outcome["fail_if"], "package.invalid.outcome_atom"), 1):
                    add(f"package:journey:{journey_id}:outcome:{ordinal}:fail_if:{failure_ordinal}", failure)
            for ordinal, atom_id in enumerate(_package_string_list(journey["atom_ids"], "package.invalid.outcome_atom"), 1):
                add(f"package:journey:{journey_id}:atom_id:{ordinal}", atom_id)
            for ordinal, known_value in enumerate(cast(list[object], journey["known_items"]), 1):
                known = _package_fields(known_value, KNOWN_ITEM_FIELDS, "package.invalid.known_item_provenance", optional_fields=("step_refs",))
                add(f"package:journey:{journey_id}:known_item:{ordinal}:text", known["text"])
                add(f"package:journey:{journey_id}:known_item:{ordinal}:source_ref", known["source_ref"])
    for view in ("round", "feedback"):
        projection = _package_fields(package[view], PROJECTION_FIELDS, "package.invalid.projection_drift")
        for field in PROJECTION_FIELDS:
            add(f"package:projection:{view}:{field}", projection[field])
    return items


def preflight_authored_manifest(manifest: Mapping[str, object]) -> None:
    """Gate new issue/reseal manifests without blocking legacy identity reconstruction."""
    contract = _result_contract(require_runtime_match=True)
    _validate_immutable_manifest(manifest)
    if manifest["schema_version"] != CANONICAL_PACKAGE_SCHEMA_V2:
        return
    canonical = _package_string_list(contract["canonical_assessments"], "package.invalid.feedback_result_contract")
    required = _package_string_list(contract["required_assessments"], "package.invalid.feedback_result_contract")
    for journey_value in cast(list[object], manifest["journeys"]):
        journey = cast(Mapping[str, object], journey_value)
        journey_id = cast(str, journey["journey_id"])
        steps = cast(list[object], journey["steps"])
        step_ids = [cast(str, cast(Mapping[str, object], step)["step_id"]) for step in steps]
        for ordinal, known_value in enumerate(cast(list[object], journey["known_items"]), 1):
            if not isinstance(known_value, Mapping) or "step_refs" not in known_value:
                raise ValidationError(
                    f"package.invalid.known_item_step_refs: journey {journey_id!r} known item {ordinal} "
                    f"requires step_refs as a non-empty, unique array of this journey's step ids; "
                    f"for example {json.dumps([step_ids[0]])}. Valid step ids: {json.dumps(step_ids)}"
                )
        for step_value in steps:
            if not isinstance(step_value, Mapping) or "assessment_options" not in step_value:
                step_id = cast(str, cast(Mapping[str, object], step_value)["step_id"])
                raise ValidationError(
                    f"package.missing.assessment_options: journey {journey_id!r} step {step_id!r} "
                    "requires assessment_options as an ordered array containing every required assessment. "
                    f"A valid value is {json.dumps(required)}; canonical assessments from the result contract are "
                    f"{json.dumps(canonical)}."
                )


def build_canonical_package(manifest: Mapping[str, object]) -> dict[str, object]:
    recovery = manifest.get("recovery_policy")
    if not isinstance(recovery, str) or not recovery.strip():
        _invalid("package.invalid.recovery_policy")
    derived = set(manifest) & set(DERIVED_PACKAGE_FIELDS + ("round", "feedback"))
    if derived:
        _invalid(f"package.invalid.derived-field.{sorted(derived)[0]}")
    _validate_immutable_manifest(manifest)
    sealed_payload = {field: deepcopy(manifest[field]) for field in UNDERIVED_PACKAGE_FIELDS}
    sealed_payload_sha256 = _canonical_sha256(sealed_payload)
    round_id = manifest["round_id"]
    assert isinstance(round_id, str)
    suffix = round_id.removeprefix("round-")
    normalized = {
        "schema_version": manifest["schema_version"], "ticket": manifest["ticket"], "round_id": round_id,
        "package_id": f"uat-package-{suffix}", "package_hash": sealed_payload_sha256,
        "sealed_payload_sha256": sealed_payload_sha256, "script_id": f"uat-script-{suffix}",
        "script_hash": _sha256_bytes(render_sealed_script_bytes(sealed_payload)),
        "certified_build_hash": manifest["certified_build_hash"], "folder_path": manifest["folder_path"],
        "app_url": manifest["app_url"], "login": manifest["login"], "observer": deepcopy(manifest["observer"]),
        "recovery_policy": manifest["recovery_policy"],
        "known_sources_checked": deepcopy(manifest["known_sources_checked"]), "journeys": deepcopy(manifest["journeys"]),
    }
    projection = {field: deepcopy(normalized[field]) for field in PROJECTION_FIELDS}
    normalized["round"] = deepcopy(projection)
    normalized["feedback"] = deepcopy(projection)
    return normalized


def validate_normalized_package(package: Mapping[str, object]) -> str | None:
    try:
        recovery = package.get("recovery_policy")
        if not isinstance(recovery, str) or not recovery.strip():
            return "package.invalid.recovery_policy"
        _package_fields(package, NORMALIZED_PACKAGE_FIELDS, "package.invalid.normalized_package")
        for view in ("round", "feedback"):
            projection = package[view]
            if not isinstance(projection, Mapping) or set(projection) != set(PROJECTION_FIELDS):
                return "package.invalid.projection_drift"
        immutable = {field: package[field] for field in UNDERIVED_PACKAGE_FIELDS}
        expected = build_canonical_package(immutable)
    except ValidationError as error:
        return str(error)
    if package["sealed_payload_sha256"] != expected["sealed_payload_sha256"]:
        return "package.invalid.payload_hash"
    if package["package_id"] != expected["package_id"] or package["package_hash"] != expected["package_hash"]:
        return "package.invalid.package_identity"
    if package["script_id"] != expected["script_id"] or package["script_hash"] != expected["script_hash"]:
        return "package.invalid.script_identity"
    for view in ("round", "feedback"):
        projection = cast(Mapping[str, object], package[view])
        if any(projection[field] != package[field] for field in PROJECTION_FIELDS):
            return "package.invalid.projection_drift"
    return None


def parse_canonical_round_projection(content: bytes) -> dict[str, object]:
    """Return the immutable round projection from a validated canonical package block."""
    bounds = _package_region_bounds(content)
    if bounds is None:
        raise ValidationError("package-region")
    package, error = _package_from_region(content[bounds[0]:bounds[1]])
    if package is None:
        raise ValidationError(error or "package-region")
    state_match = re.search(rb"^\*\*package_state:\*\* `([^`]+)`$", content[bounds[0]:bounds[1]], re.MULTILINE)
    if state_match is None:
        raise ValidationError("package-state")
    return {
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "package_state": state_match.group(1).decode("utf-8"),
        "normalized_package": deepcopy(dict(package)),
        "journeys": deepcopy(cast(list[object], package["journeys"])),
    }


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _now_utc() -> datetime:
    injected = os.environ.get("UAT_ROUND_MATERIALIZE_TEST_NOW_UTC")
    if injected:
        try:
            value = datetime.fromisoformat(injected.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("test UTC clock is invalid") from exc
        if value.tzinfo is None:
            raise ValidationError("test UTC clock must include a timezone")
        return value.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _history_stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _safe_component(value: str, label: str) -> str:
    if not value or value in (os.curdir, os.pardir) or os.sep in value or (os.altsep and os.altsep in value):
        raise ValidationError(f"illegal {label} component: {value!r}")
    return value


class _RoundTree:
    """Directory-fd anchored view of one ticket's round files."""

    def __init__(self, plans_root: Path, ticket: str, *, create: bool) -> None:
        if not _is_valid_ticket_id(ticket):
            raise ValidationError(f"invalid ticket id: {ticket!r}")
        root = plans_root.absolute()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            self.root_fd = os.open(str(root), flags)
        except OSError as exc:
            raise ValidationError(f"plans root must be an existing real directory: {root}: {exc}") from exc
        self.ticket = ticket
        self.root_path = root
        self.ticket_fd: int | None = None
        self.history_fd: int | None = None
        self.history_created = False
        try:
            self.ticket_fd, _ticket_created = self._open_child_dir(self.root_fd, ticket, create=create)
            self.history_fd, self.history_created = self._open_child_dir(
                self.ticket_fd, "uat-round-history", create=create,
            )
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _open_child_dir(parent_fd: int, name: str, *, create: bool) -> tuple[int, bool]:
        _safe_component(name, "directory")
        created = False
        if create:
            try:
                os.mkdir(name, 0o755, dir_fd=parent_fd)
                os.fsync(parent_fd)
                created = True
            except FileExistsError:
                pass
            except OSError as exc:
                raise ValidationError(f"cannot create directory {name!r}: {exc}") from exc
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            return os.open(name, flags, dir_fd=parent_fd), created
        except OSError as exc:
            raise ValidationError(f"refusing symlinked or non-directory component {name!r}: {exc}") from exc

    def remove_created_empty_history(self) -> None:
        """Remove only the empty history directory created by this tree instance."""
        if not self.history_created:
            return
        assert self.ticket_fd is not None
        if self.history_fd is not None:
            os.close(self.history_fd)
            self.history_fd = None
        try:
            os.rmdir("uat-round-history", dir_fd=self.ticket_fd)
            os.fsync(self.ticket_fd)
        except OSError as exc:
            raise ValidationError(f"cannot remove newly-created history directory during rollback: {exc}") from exc
        self.history_created = False

    @property
    def ticket_path(self) -> Path:
        return self.root_path / self.ticket

    @property
    def history_path(self) -> Path:
        return self.ticket_path / "uat-round-history"

    @property
    def live_name(self) -> str:
        return f"{self.ticket}.UAT-CURRENT-ROUND.md"

    @property
    def state_name(self) -> str:
        return f"{self.ticket}.uat-round-pattern.json"

    def close(self) -> None:
        for attribute in ("history_fd", "ticket_fd", "root_fd"):
            fd = getattr(self, attribute, None)
            if fd is not None:
                try:
                    os.close(fd)
                finally:
                    setattr(self, attribute, None)

    def __enter__(self) -> "_RoundTree":
        return self

    def __exit__(self, error_type: Any, _value: Any, _traceback: Any) -> None:
        if error_type is not None and self.history_created and self.history_fd is not None:
            try:
                if not _directory_has_entries(self.history_fd):
                    self.remove_created_empty_history()
            except BaseException:
                # Preserve the original operation error; explicit rollback paths report
                # cleanup failures where they can still recover deterministically.
                pass
        self.close()


def _read_at(parent_fd: int, name: str, *, required: bool = True) -> bytes | None:
    _safe_component(name, "file")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if required:
            raise ValidationError(f"required file is absent: {name}")
        return None
    except OSError as exc:
        raise ValidationError(f"refusing unreadable or symlinked file {name!r}: {exc}") from exc
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise ValidationError(f"file must be regular: {name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


def _exists_at(parent_fd: int, name: str) -> bool:
    try:
        os.lstat(name, dir_fd=parent_fd)
        return True
    except FileNotFoundError:
        return False


def _directory_has_entries(directory_fd: int) -> bool:
    """Inspect the already-anchored directory without re-resolving its path."""
    try:
        return bool(os.listdir(directory_fd))
    except OSError as exc:
        raise ValidationError(f"cannot inspect anchored directory: {exc}") from exc


def _temp_basename(destination: str) -> str:
    injected = os.environ.get("UAT_ROUND_MATERIALIZE_TEST_TEMP_BASENAME")
    if injected:
        return _safe_component(injected, "temporary file")
    return f".{destination}.{os.urandom(16).hex()}.tmp"


def _validate_destination_at(
    parent_fd: int, destination: str, *, exclusive: bool, expected_sha256: str | None,
) -> None:
    try:
        current = os.lstat(destination, dir_fd=parent_fd)
    except FileNotFoundError:
        current = None
    if current is None:
        if expected_sha256 is not None:
            raise ValidationError(f"expected destination disappeared before replace: {destination}")
        return
    if exclusive:
        raise ValidationError(f"immutable history artifact already exists: {destination}")
    if not stat.S_ISREG(current.st_mode):
        raise ValidationError(f"destination must be a regular file: {destination}")
    if expected_sha256 is not None:
        current_bytes = _read_at(parent_fd, destination)
        assert current_bytes is not None
        if _sha256_bytes(current_bytes) != expected_sha256:
            raise ValidationError(f"destination hash changed before replace: {destination}")


def _write_at(
    parent_fd: int, destination: str, content: bytes, *, exclusive: bool,
    expected_sha256: str | None = None,
    on_commit: Callable[[], None] | None = None,
) -> None:
    """Durably write by basename through an anchored fd and never follow aliases."""
    _safe_component(destination, "destination")
    _validate_destination_at(
        parent_fd, destination, exclusive=exclusive, expected_sha256=expected_sha256,
    )
    temporary = _temp_basename(destination)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
    except FileExistsError as exc:
        raise ValidationError(f"exclusive temporary file already exists (possible symlink): {temporary}") from exc
    except OSError as exc:
        raise ValidationError(f"exclusive no-follow temporary write refused for {temporary}: {exc}") from exc
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    try:
        # Revalidate after the staged file is durable and immediately before replace.
        # This closes the owner-edit race between initial validation and temp writing.
        _validate_destination_at(
            parent_fd, destination, exclusive=exclusive, expected_sha256=expected_sha256,
        )
        os.replace(temporary, destination, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        if on_commit is not None:
            on_commit()
        os.fsync(parent_fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass
        raise


def _unlink_at(parent_fd: int, name: str) -> None:
    try:
        info = os.lstat(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise ValidationError(f"refusing to unlink non-regular rollback destination: {name}")
    os.unlink(name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _restore_at(
    parent_fd: int, destination: str, original: bytes | None, *, current_sha256: str | None,
) -> None:
    if original is None:
        if current_sha256 is not None:
            current = _read_at(parent_fd, destination, required=False)
            if current is not None and _sha256_bytes(current) != current_sha256:
                raise ValidationError(f"rollback destination changed unexpectedly: {destination}")
        _unlink_at(parent_fd, destination)
        return
    _write_at(
        parent_fd, destination, original, exclusive=False,
        expected_sha256=current_sha256,
    )


def _markdown_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _canonical_value(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _source_item(
    item_id: str, kind: str, section: str, key: str, field: str | None,
    line_start: int, line_end: int, semantic_value: Any, exact_bytes: bytes | str,
) -> dict[str, Any]:
    if isinstance(exact_bytes, str):
        exact_bytes = exact_bytes.encode("utf-8")
    location: dict[str, Any] = {
        "section": section, "key": key, "line_start": line_start, "line_end": line_end,
    }
    if field is not None:
        location["field"] = field
    return {
        "source_item_id": item_id,
        "kind": kind,
        "source_location": location,
        "semantic_value_sha256": _sha256_bytes(_canonical_value(semantic_value)),
        "exact_bytes_sha256": _sha256_bytes(exact_bytes),
        "semantic_value": semantic_value,
    }


def _raw_lines(content: bytes) -> list[dict[str, Any]]:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("round file must be UTF-8") from exc
    result: list[dict[str, Any]] = []
    offset = 0
    for number, raw in enumerate(content.splitlines(keepends=True), 1):
        result.append({
            "bytes": raw,
            "text": raw.rstrip(b"\r\n").decode("utf-8"),
            "start": offset,
            "end": offset + len(raw),
            "line_start": number,
            "line_end": number,
        })
        offset += len(raw)
    if offset != len(content):
        raise ValidationError("round file line scanner did not cover every byte")
    return result


def _table_cells_bytes(raw: bytes) -> list[bytes] | None:
    body = raw.rstrip(b"\r\n").strip()
    if not (body.startswith(b"|") and body.endswith(b"|")):
        return None
    return [cell.strip() for cell in body[1:-1].split(b"|")]


_NORMALIZED_CONTRACT_START = "## Canonical package (generated, owner-readable, and self-contained)\n".encode("utf-8")
_NORMALIZED_CONTRACT_END = "## Composer gate — complete before issue\n".encode("utf-8")


def _program_template(content: bytes) -> bytes:
    start = content.find(_NORMALIZED_CONTRACT_START)
    if start < 0:
        return content
    end = content.find(_NORMALIZED_CONTRACT_END, start)
    if end < 0:
        raise ValidationError("canonical package template block is incomplete")
    return content[:start] + content[end:]


_PACKAGE_HEADING = b"## Canonical package (generated, owner-readable, and self-contained)\n"
_PACKAGE_ANNEX_END = b"END NORMALIZED PACKAGE\n"


def _package_region_bounds(content: bytes) -> tuple[int, int] | None:
    start = content.find(_PACKAGE_HEADING)
    if start < 0:
        return None
    annex_end = content.find(_PACKAGE_ANNEX_END, start)
    if annex_end < 0:
        return None
    return start, annex_end + len(_PACKAGE_ANNEX_END)


def _package_region_atoms(content: bytes, start: int, end: int) -> list[dict[str, Any]]:
    kinds = {
        b"## Canonical package": "package-block-heading",
        b"**package_state:": "package-block-envelope-field",
        b"**schema_version:": "package-block-identity-field",
        b"**ticket:": "package-block-identity-field",
        b"**sealed_payload_sha256:": "package-block-identity-field",
        b"**folder_path:": "package-block-context-field",
        b"**observer:": "package-block-context-field",
        b"**recovery_policy:": "package-block-context-field",
        b"**known_sources_checked:": "package-block-context-field",
        b"Setup refresh": "package-block-recovery-rule",
        b"### Journey": "package-block-journey-heading",
        b"Source:": "package-block-journey-identity",
        b"Scenario:": "package-block-journey-identity",
        b"UAT-test anchor:": "package-block-journey-identity",
        b"Actions:": "package-block-action",
        b"Outcomes:": "package-block-outcome",
        b"- Atom:": "package-block-outcome",
        b"  Expected:": "package-block-outcome",
        b"  Fail if:": "package-block-outcome",
        b"Atom IDs:": "package-block-atom-id",
        b"Known items:": "package-block-known-item",
        b"Requirement ref:": "package-block-requirement-ref",
        b"BEGIN NORMALIZED PACKAGE": "package-annex-start-marker",
        b"```json": "package-annex-open-fence",
        b"```": "package-annex-close-fence",
        b"END NORMALIZED PACKAGE": "package-annex-end-marker",
    }
    atoms: list[dict[str, Any]] = []
    offset = 0
    for line_number, raw in enumerate(content.splitlines(keepends=True), 1):
        line_start = offset
        offset += len(raw)
        if line_start < start or line_start >= end:
            kind = "scaffold"
        elif raw.startswith(b"{"):
            kind = "package-annex-json"
        elif raw.strip() in kinds:
            kind = kinds[raw.strip()]
        else:
            kind = next((name for prefix, name in kinds.items() if raw.startswith(prefix)), "package-block-context-field")
        atoms.append({
            "kind": kind, "role_id": kind, "start": line_start, "end": offset,
            "line_start": line_number, "line_end": line_number, "source_bytes_hex": raw.hex(),
        })
    return atoms


def _package_from_region(region: bytes) -> tuple[Mapping[str, object] | None, str | None]:
    if b"<PACKAGE_ID>" in region:
        return None, None
    start = b"BEGIN NORMALIZED PACKAGE\n```json\n"
    end = b"\n```\nEND NORMALIZED PACKAGE\n"
    if start not in region or end not in region:
        return None, "package-region"
    left = region.index(start) + len(start)
    right = region.index(end, left)
    try:
        raw = json.loads(region[left:right].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "package-annex-json"
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        return None, "package-annex-json"
    package = cast(Mapping[str, object], raw)
    error = validate_normalized_package(package)
    if error is not None:
        return None, error
    state_match = re.search(rb"^\*\*package_state:\*\* `([^`]+)`$", region, re.MULTILINE)
    if state_match is None:
        return None, "package-state"
    state = state_match.group(1).decode("utf-8")
    if state not in PACKAGE_STATES:
        return None, "package-state"
    expected = render_canonical_package_block_bytes(package, package_state=state) + render_normalized_package_annex_bytes(package)
    # Prepare's historical package-block seam places one separator line before the
    # annex; accept that exact producer form without accepting arbitrary prose.
    accepted = (expected, expected.replace(b"BEGIN NORMALIZED PACKAGE", b"\nBEGIN NORMALIZED PACKAGE", 1))
    return (package, None) if region in accepted else (None, "package-region")


def _placeholder_semantic_items(walk: dict[str, Any]) -> list[dict[str, Any]]:
    headers = walk["values"]["headers"]
    return [
        {
            "source_item_id": f"header:{field}", "kind": "header-field", "origin": "round-markdown",
            "source_location": {
                "section": "header", "key": field, "field": field,
                "line_start": ordinal, "line_end": ordinal,
            },
            "semantic_value_sha256": _canonical_sha256(value), "exact_bytes_sha256": _canonical_sha256(value),
            "semantic_value": value,
        }
        for ordinal, (field, value) in enumerate(headers.items(), 1)
    ]


_DEFAULT_SLOT_CONTRACT = [
    "header:round", "header:last-refreshed", "header:stack", "header:pre-walk",
    "header:deploy-status",
    "table:definitions:rows", "table:keys:rows", "table:sets:rows",
    "sources:provenance", "table:hot:rows", "table:cold:rows", "notes:bullets",
]
_DEFAULT_TABLE_COLUMNS = {"definitions": 2, "keys": 2, "sets": 3, "hot": 9, "cold": 8}
_DEFAULT_EXACT_OP_MANIFEST_SHA256 = {
    "canonical@1": "50aa4c363616d3900c56ef31264ea2439df8f6c79bdc600e6e4ff23c2798bfa6",
    "compact-existing@1": "006b0444f061a5c506ec041eee7d4fd422b0ca0f8347982423b127a881c19a3e",
}


def _exact_op_manifest_sha256(form: dict[str, Any]) -> str:
    manifest = [
        {
            "kind": op["kind"],
            "op_id": op["op_id"],
            "role_id": op["role_id"],
            **({"token_id": op["token_id"], "exact_bytes_sha256": _sha256_bytes(op["exact_bytes"])}
               if op["kind"] == "EXACT" else {}),
            **({key: op[key] for key in ("slot_id", "field", "table_id", "columns") if key in op}
               if op["kind"] != "EXACT" else {}),
        }
        for op in form["ops"]
    ]
    return _canonical_sha256(manifest)


def _jsonable_program_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, dict):
        return {key: _jsonable_program_value(item) for key, item in sorted(value.items()) if key != "template_bytes"}
    if isinstance(value, list):
        return [_jsonable_program_value(item) for item in value]
    return value


def _program_hash(program: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_value(_jsonable_program_value(program)))


def _compact_source_form() -> dict[str, Any]:
    spec: list[tuple[str, str, Any]] = [
        ("EXACT", "compact:title", "# UAT — CURRENT ROUND (single always-current file; wholesale-replaced at issuance, PATCHED mid-round)\n".encode("utf-8")),
        ("EXACT", "compact:blank:0", b"\n"),
        ("SCALAR_SLOT", "header:round", (b"**Round:** ", b"\n", "round")),
        ("SCALAR_SLOT", "header:last-refreshed", (b"**Last refreshed:** ", b"\n", "last-refreshed")),
        ("SCALAR_SLOT", "header:stack", (b"**Stack:** ", b"\n", "stack")),
        ("SCALAR_SLOT", "header:pre-walk", (b"**Pre-walk:** ", b"\n", "pre-walk")),
        ("SCALAR_SLOT", "header:deploy-status", (b"**Deploy status:** ", b"\n", "deploy-status")),
        ("EXACT", "compact:blank:1", b"\n"),
        ("EXACT", "compact:definitions:heading", b"## Definitions\n"),
        ("EXACT", "compact:definitions:header", b"| Term | Meaning |\n"),
        ("EXACT", "compact:definitions:separator", b"|---|---|\n"),
        ("TABLE_ROWS_SLOT", "table:definitions:rows", ("definitions", 2)),
        ("EXACT", "compact:blank:2", b"\n"),
        ("EXACT", "compact:keys-sets:heading", b"## Keys & Sets\n"),
        ("EXACT", "compact:keys:marker", b"**Keys**\n"),
        ("EXACT", "compact:keys:header", b"| Key | Definition |\n"),
        ("EXACT", "compact:keys:separator", b"|---|---|\n"),
        ("TABLE_ROWS_SLOT", "table:keys:rows", ("keys", 2)),
        ("EXACT", "compact:blank:3", b"\n"),
        ("EXACT", "compact:sets:marker", b"**Sets**\n"),
        ("EXACT", "compact:sets:header", b"| Set | Members | Meaning |\n"),
        ("EXACT", "compact:sets:separator", b"|---|---|---|\n"),
        ("TABLE_ROWS_SLOT", "table:sets:rows", ("sets", 3)),
        ("EXACT", "compact:blank:4", b"\n"),
        ("EXACT", "compact:hot:heading", b"## Journeys (hot)\n"),
        ("PROVENANCE_SLOT", "sources:provenance", (b"**Data sources for this table (all rows summarized from, never invented):** ", b"\n")),
        ("EXACT", "compact:hot:header", b"| # | UAT ready? | Who | Journey | Walk | PASS / expected looks like | FAIL / report if | Closes | Official GWT SHA-256 |\n"),
        ("EXACT", "compact:hot:separator", b"|---|---|---|---|---|---|---|---|---|\n"),
        ("TABLE_ROWS_SLOT", "table:hot:rows", ("hot", 9)),
        ("EXACT", "compact:blank:5", b"\n"),
        ("EXACT", "compact:cold:heading", b"## Journeys (cold)\n"),
        ("EXACT", "compact:cold:header", b"| # | UAT ready? | Who | Journey | Walk | PASS / expected looks like | FAIL / report if | Closes |\n"),
        ("EXACT", "compact:cold:separator", b"|---|---|---|---|---|---|---|---|\n"),
        ("TABLE_ROWS_SLOT", "table:cold:rows", ("cold", 8)),
        ("EXACT", "compact:blank:6", b"\n"),
        ("EXACT", "compact:notes:heading", b"## Notes\n"),
        ("NOTES_SLOT", "notes:bullets", None),
    ]
    ops: list[dict[str, Any]] = []
    required: list[str] = []
    slots: list[str] = []
    for ordinal, (kind, role, value) in enumerate(spec):
        if kind == "EXACT":
            token_id = f"compact:exact:{ordinal}:{role}"
            ops.append({"kind": kind, "op_id": token_id, "token_id": token_id, "role_id": role, "exact_bytes": value})
            required.append(token_id)
        elif kind == "SCALAR_SLOT":
            prefix, suffix, field = value
            ops.append({"kind": kind, "op_id": role, "slot_id": role, "role_id": f"scalar:{field}", "field": field, "prefix": prefix, "suffix": suffix, "placeholder": b""})
            slots.append(role)
        elif kind == "TABLE_ROWS_SLOT":
            table_id, columns = value
            ops.append({"kind": kind, "op_id": role, "slot_id": role, "role_id": role, "table_id": table_id, "columns": columns, "placeholder_rows": []})
            slots.append(role)
        elif kind == "PROVENANCE_SLOT":
            prefix, suffix = value
            ops.append({"kind": kind, "op_id": role, "slot_id": role, "role_id": "provenance:links", "prefix": prefix, "suffix": suffix, "placeholder": b""})
            slots.append(role)
        else:
            ops.append({"kind": kind, "op_id": role, "slot_id": role, "role_id": role, "placeholder_rows": []})
            slots.append(role)
    return {"source_form_id": "compact-existing@1", "ops": ops, "required_token_ids": required, "slot_ids": slots}


def _render_placeholder_form(form: dict[str, Any]) -> bytes:
    rendered = bytearray()
    for op in form["ops"]:
        if op["kind"] == "EXACT":
            rendered.extend(op["exact_bytes"])
        elif op["kind"] in {"SCALAR_SLOT", "PROVENANCE_SLOT"}:
            rendered.extend(op["prefix"] + op["placeholder"] + op["suffix"])
        else:
            rendered.extend(b"".join(op["placeholder_rows"]))
    return bytes(rendered)


def _validate_default_program_shape(program: dict[str, Any]) -> None:
    if program.get("program_id") != "default@1":
        raise ValidationError("default template program has the wrong program_id")
    if program.get("semantic_slot_contract") != _DEFAULT_SLOT_CONTRACT:
        raise ValidationError("default template program mandatory slot contract changed")
    forms = program.get("source_forms")
    if not isinstance(forms, dict) or set(forms) != {"canonical@1", "compact-existing@1"}:
        raise ValidationError("default template program source-form set changed")
    if program.get("render_form_id") != "canonical@1":
        raise ValidationError("default template program render form must be canonical@1")
    discriminators: list[bytes] = []
    for form_id, form in forms.items():
        if form.get("source_form_id") != form_id:
            raise ValidationError(f"default template source form id mismatch: {form_id}")
        ops = form.get("ops")
        if not isinstance(ops, list) or not ops:
            raise ValidationError(f"default template source form is empty: {form_id}")
        op_ids = [op["op_id"] for op in ops]
        token_ids = [op["token_id"] for op in ops if op["kind"] == "EXACT"]
        slot_ids = [op["slot_id"] for op in ops if op["kind"] != "EXACT"]
        if len(op_ids) != len(set(op_ids)) or len(token_ids) != len(set(token_ids)):
            raise ValidationError(f"default template source form contains duplicate operation IDs: {form_id}")
        if slot_ids != _DEFAULT_SLOT_CONTRACT or form.get("slot_ids") != _DEFAULT_SLOT_CONTRACT:
            raise ValidationError(f"default template source form slot order changed: {form_id}")
        if token_ids != form.get("required_token_ids"):
            raise ValidationError(f"default template source form exact-token order changed: {form_id}")
        if _exact_op_manifest_sha256(form) != _DEFAULT_EXACT_OP_MANIFEST_SHA256[form_id]:
            raise ValidationError(
                f"default template source form mandatory exact-operation manifest changed: {form_id}"
            )
        first = ops[0]
        if first["kind"] != "EXACT":
            raise ValidationError(f"default template source form lacks exact discriminator: {form_id}")
        discriminators.append(first["exact_bytes"])
        table_slots = [op for op in ops if op["kind"] == "TABLE_ROWS_SLOT"]
        if [op["table_id"] for op in table_slots] != list(_DEFAULT_TABLE_COLUMNS):
            raise ValidationError(f"default template source form table order changed: {form_id}")
        for slot in table_slots:
            if slot["columns"] != _DEFAULT_TABLE_COLUMNS[slot["table_id"]]:
                raise ValidationError(f"default template source form table width changed: {form_id}:{slot['table_id']}")
            position = ops.index(slot)
            if position < 2:
                raise ValidationError(f"default template table lacks header/separator: {form_id}:{slot['table_id']}")
            header, separator = ops[position - 2:position]
            if header["kind"] != "EXACT" or separator["kind"] != "EXACT":
                raise ValidationError(f"default template table is not role-bound: {form_id}:{slot['table_id']}")
            header_cells = _table_cells_bytes(header["exact_bytes"])
            separator_cells = _table_cells_bytes(separator["exact_bytes"])
            if header_cells is None or separator_cells is None or len(header_cells) != slot["columns"] or len(separator_cells) != slot["columns"]:
                raise ValidationError(f"default template table column binding changed: {form_id}:{slot['table_id']}")
            if not all(re.fullmatch(rb":?-{3,}:?", cell.replace(b" ", b"")) for cell in separator_cells):
                raise ValidationError(f"default template separator grammar changed: {form_id}:{slot['table_id']}")
            if any(len(_table_cells_bytes(row) or []) != slot["columns"] for row in slot["placeholder_rows"]):
                raise ValidationError(f"default template placeholder width changed: {form_id}:{slot['table_id']}")
    if discriminators[0] == discriminators[1]:
        raise ValidationError("default template source-form discriminators are ambiguous")
    canonical = forms["canonical@1"]
    if _render_placeholder_form(canonical) != program["template_bytes"]:
        raise ValidationError("default template program does not byte-for-byte re-render its source")


def _compile_default_template_program(template: bytes | None = None) -> dict[str, Any]:
    """Compile one default@1 program with exact canonical and compact input forms."""
    if template is None:
        try:
            template = (CONTROLLED_SKILL_ROOT / "UAT_CURRENT_ROUND_TEMPLATE.md").read_bytes()
        except OSError as exc:
            raise ValidationError(f"cannot load canonical round scaffold: {exc}") from exc
    template = _program_template(template)
    lines = _raw_lines(template)
    ops: list[dict[str, Any]] = []
    section = "header"
    table_counts: dict[str, int] = {}
    index = 0
    exact_ordinal = 0
    slot_ids: list[str] = []
    required_token_ids: list[str] = []

    def exact(raw: bytes, role_id: str) -> None:
        nonlocal exact_ordinal
        token_id = f"canonical:exact:{exact_ordinal}:{role_id}"
        exact_ordinal += 1
        ops.append({"kind": "EXACT", "op_id": token_id, "token_id": token_id, "role_id": role_id, "exact_bytes": raw})
        required_token_ids.append(token_id)

    while index < len(lines):
        raw = lines[index]["bytes"]
        text = lines[index]["text"]
        lowered = text.lower()
        if lowered.startswith("## definitions"):
            section = "definitions"
        elif lowered.startswith("## keys & sets"):
            section = "keys-sets"
        elif lowered.startswith("## journeys (hot)"):
            section = "journeys-hot"
        elif lowered.startswith("## journeys (cold)"):
            section = "journeys-cold"
        elif lowered.startswith("## notes"):
            section = "notes"

        scalar = re.match(rb"^\*\*(Round|Last refreshed|Stack|Pre-walk|Deploy status):\*\* (.*?)(\r?\n)$", raw)
        if scalar:
            field = scalar.group(1).decode("utf-8").lower().replace(" ", "-")
            slot_id = f"header:{field}"
            ops.append({"kind": "SCALAR_SLOT", "op_id": slot_id, "slot_id": slot_id, "role_id": f"scalar:{field}", "field": field, "prefix": raw[:scalar.start(2)], "placeholder": scalar.group(2), "suffix": scalar.group(3)})
            slot_ids.append(slot_id)
            index += 1
            continue

        if raw.startswith(b"**Data sources for this table (all rows summarized from, never invented):** "):
            marker = b"**Data sources for this table (all rows summarized from, never invented):** "
            if not raw.endswith(b"\n"):
                raise ValidationError("default@1 provenance slot has a non-canonical carrier")
            slot_id = "sources:provenance"
            ops.append({"kind": "PROVENANCE_SLOT", "op_id": slot_id, "slot_id": slot_id, "role_id": "provenance:links", "prefix": marker, "suffix": b"\n", "placeholder": raw[len(marker):-1]})
            slot_ids.append(slot_id)
            index += 1
            continue

        cells = _table_cells_bytes(raw)
        if cells is not None:
            table_number = table_counts.get(section, 0)
            table_counts[section] = table_number + 1
            if index + 2 >= len(lines):
                raise ValidationError(f"default@1 table {section}:{table_number} is incomplete")
            header = raw
            separator = lines[index + 1]["bytes"]
            separator_cells = _table_cells_bytes(separator)
            if separator_cells is None or len(separator_cells) != len(cells):
                raise ValidationError(f"default@1 table {section}:{table_number} header/separator column mismatch")
            placeholder_rows: list[bytes] = []
            cursor = index + 2
            while cursor < len(lines) and _table_cells_bytes(lines[cursor]["bytes"]) is not None:
                row = lines[cursor]["bytes"]
                if len(_table_cells_bytes(row) or []) != len(cells):
                    raise ValidationError(f"default@1 table {section}:{table_number} placeholder column mismatch")
                placeholder_rows.append(row)
                cursor += 1
            if not placeholder_rows:
                raise ValidationError(f"default@1 table {section}:{table_number} has no placeholder row")
            if section == "definitions":
                table_id = "definitions"
            elif section == "keys-sets":
                table_id = "keys" if table_number == 0 else "sets"
            elif section == "journeys-hot":
                table_id = "hot"
            elif section == "journeys-cold":
                table_id = "cold"
            else:
                raise ValidationError(f"default@1 contains a table in unsupported section {section}")
            exact(header, f"table:{table_id}:header")
            exact(separator, f"table:{table_id}:separator")
            slot_id = f"table:{table_id}:rows"
            ops.append({"kind": "TABLE_ROWS_SLOT", "op_id": slot_id, "slot_id": slot_id, "role_id": f"table:{table_id}:rows", "table_id": table_id, "columns": len(cells), "placeholder_rows": placeholder_rows})
            slot_ids.append(slot_id)
            index = cursor
            continue

        if section == "notes" and raw == b"- <bullet>\n":
            slot_id = "notes:bullets"
            ops.append({"kind": "NOTES_SLOT", "op_id": slot_id, "slot_id": slot_id, "role_id": "notes:bullets", "placeholder_rows": [raw]})
            slot_ids.append(slot_id)
            index += 1
            continue

        exact(raw, f"{section}:line:{index}")
        index += 1

    canonical = {"source_form_id": "canonical@1", "ops": ops, "required_token_ids": required_token_ids, "slot_ids": slot_ids}
    program: dict[str, Any] = {
        "program_id": "default@1",
        "semantic_slot_contract": list(_DEFAULT_SLOT_CONTRACT),
        "source_forms": {"canonical@1": canonical, "compact-existing@1": _compact_source_form()},
        "render_form_id": "canonical@1",
        "template_bytes": template,
    }
    _validate_default_program_shape(program)
    program["shape_valid"] = True
    program["scaffold_program_sha256"] = _program_hash(program)
    return program


def _select_source_form(source: bytes, program: dict[str, Any]) -> tuple[str, bool]:
    first_line = source.splitlines(keepends=True)[0] if source else b""
    canonical_first = program["source_forms"]["canonical@1"]["ops"][0]["exact_bytes"]
    compact_first = program["source_forms"]["compact-existing@1"]["ops"][0]["exact_bytes"]
    if first_line == canonical_first:
        return "canonical@1", False
    if first_line == compact_first:
        return "compact-existing@1", False
    return "canonical@1", True

def _coverage_atom(line: dict[str, Any], kind: str, role_id: str, *, op_id: str | None = None) -> dict[str, Any]:
    atom = {
        "kind": kind, "role_id": role_id, "start": line["start"], "end": line["end"],
        "line_start": line["line_start"], "line_end": line["line_end"],
        "source_bytes_hex": line["bytes"].hex(),
    }
    if op_id is not None:
        atom["op_id"] = op_id
    return atom


def _future_exact_index(ops: list[dict[str, Any]], start: int, raw: bytes) -> int | None:
    for position in range(start, len(ops)):
        op = ops[position]
        if op["kind"] == "EXACT" and op["exact_bytes"] == raw:
            return position
    return None


def _single_edit_apart(left: bytes, right: bytes) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    cursor = 0
    skipped = False
    for value in longer:
        if cursor < len(shorter) and value == shorter[cursor]:
            cursor += 1
        elif skipped:
            return False
        else:
            skipped = True
    return True


def _semantic_contains_wrong_role_token(
    raw: bytes, exact_tokens: list[bytes], placeholder_spans: set[bytes] | None = None,
) -> bool:
    body = raw.rstrip(b"\r\n")
    if any(token and token in body for token in exact_tokens):
        return True
    if placeholder_spans:
        for candidate in re.findall(rb"<[^<>\r\n]+>", body):
            if candidate in placeholder_spans or any(_single_edit_apart(candidate, span) for span in placeholder_spans):
                return True
    return False


def _walk_source_against_program(
    source: bytes, program: dict[str, Any], source_form_id: str, *, form_selection_tainted: bool = False,
) -> dict[str, Any]:
    """Walk one preselected exact input form of the single default@1 program."""
    lines = _raw_lines(source)
    form = program["source_forms"][source_form_id]
    ops = form["ops"]
    exact_tokens = [
        token
        for candidate in program["source_forms"].values()
        for op in candidate["ops"]
        for token in (
            [op["exact_bytes"].rstrip(b"\r\n")] if op["kind"] == "EXACT"
            else [row.rstrip(b"\r\n") for row in op.get("placeholder_rows", [])]
        )
    ]
    placeholder_spans = {
        span
        for candidate in program["source_forms"].values()
        for op in candidate["ops"]
        for row in op.get("placeholder_rows", [])
        for span in re.findall(rb"<[^<>\r\n]+>", row)
    }
    source_cursor = 0
    program_cursor = 0
    atoms: list[dict[str, Any]] = []
    values: dict[str, Any] = {
        "headers": {}, "definitions": [], "keys": [], "sets": [],
        "sources": [], "hot": [], "cold": [], "notes": [],
    }
    carriers: list[dict[str, Any]] = []
    consumed_tokens: list[str] = []
    fulfilled_slots: list[str] = []
    missing_tokens: list[str] = []
    duplicate_tokens: list[str] = []
    walk_tainted = form_selection_tainted

    def reject_current(role: str) -> None:
        nonlocal source_cursor, walk_tainted
        line = lines[source_cursor]
        atoms.append(_coverage_atom(line, "unclassified-fragment", role))
        source_cursor += 1
        walk_tainted = True

    while program_cursor < len(ops):
        op = ops[program_cursor]
        if source_cursor >= len(lines):
            if op["kind"] == "EXACT":
                missing_tokens.append(op["token_id"])
            else:
                missing_tokens.append(op["slot_id"])
            walk_tainted = True
            program_cursor += 1
            continue
        line = lines[source_cursor]
        raw = line["bytes"]
        kind = op["kind"]

        if kind == "EXACT":
            if raw == op["exact_bytes"]:
                atoms.append(_coverage_atom(line, "scaffold", op["role_id"], op_id=op["token_id"]))
                consumed_tokens.append(op["token_id"])
                source_cursor += 1
                program_cursor += 1
                continue
            immediate_next = ops[program_cursor + 1] if program_cursor + 1 < len(ops) else None
            if immediate_next is not None and immediate_next["kind"] == "EXACT" and raw == immediate_next["exact_bytes"]:
                missing_tokens.append(op["token_id"])
                walk_tainted = True
                program_cursor += 1
                continue
            if any(raw == earlier["exact_bytes"] for earlier in ops[:program_cursor] if earlier["kind"] == "EXACT"):
                duplicate_tokens.append(op["token_id"])
            reject_current(op["role_id"])
            continue

        if kind == "SCALAR_SLOT":
            if raw.startswith(op["prefix"]) and raw.endswith(op["suffix"]) and len(raw) >= len(op["prefix"]) + len(op["suffix"]):
                value_bytes = raw[len(op["prefix"]):len(raw) - len(op["suffix"])]
                if _semantic_contains_wrong_role_token(value_bytes, exact_tokens, placeholder_spans):
                    reject_current(op["role_id"])
                    continue
                value = value_bytes.decode("utf-8")
                values["headers"][op["field"]] = value
                placeholder = value_bytes == op["placeholder"]
                atoms.append(_coverage_atom(
                    line, "scaffold-alternative" if placeholder else "semantic-carrier",
                    op["role_id"], op_id=op["slot_id"],
                ))
                if not placeholder:
                    carriers.append({"slot_id": op["slot_id"], "line": line, "value": value})
                fulfilled_slots.append(op["slot_id"])
                source_cursor += 1
                program_cursor += 1
                continue
            later = _future_exact_index(ops, program_cursor + 1, raw)
            if later is not None:
                missing_tokens.append(op["slot_id"])
                walk_tainted = True
                program_cursor += 1
                continue
            reject_current(op["role_id"])
            continue

        if kind == "PROVENANCE_SLOT":
            if raw.startswith(op["prefix"]) and raw.endswith(op["suffix"]):
                body = raw[len(op["prefix"]):len(raw) - len(op["suffix"])]
                placeholder = body == op["placeholder"]
                links = re.findall(rb"\[[^\]]+\]\([^\)]+\)", body)
                if not placeholder and (
                    not links or b" \xc2\xb7 ".join(links) != body
                    or _semantic_contains_wrong_role_token(body, exact_tokens, placeholder_spans)
                ):
                    reject_current(op["role_id"])
                    continue
                values["sources"] = [] if placeholder else [link.decode("utf-8") for link in links]
                values["source_line"] = raw.rstrip(b"\r\n").decode("utf-8")
                atoms.append(_coverage_atom(
                    line, "scaffold-alternative" if placeholder else "semantic-carrier",
                    op["role_id"], op_id=op["slot_id"],
                ))
                if not placeholder:
                    carriers.append({"slot_id": op["slot_id"], "line": line, "value": values["source_line"]})
                fulfilled_slots.append(op["slot_id"])
                source_cursor += 1
                program_cursor += 1
                continue
            later = _future_exact_index(ops, program_cursor + 1, raw)
            if later is not None:
                missing_tokens.append(op["slot_id"])
                walk_tainted = True
                program_cursor += 1
                continue
            reject_current(op["role_id"])
            continue

        next_exact = next((candidate for candidate in ops[program_cursor + 1:] if candidate["kind"] == "EXACT"), None)
        if kind == "TABLE_ROWS_SLOT":
            captured: list[dict[str, Any]] = []
            while source_cursor < len(lines):
                current = lines[source_cursor]
                current_raw = current["bytes"]
                if next_exact is not None and current_raw == next_exact["exact_bytes"]:
                    break
                cells = _table_cells_bytes(current_raw)
                if cells is None:
                    break
                captured.append(current)
                source_cursor += 1
            placeholders = op["placeholder_rows"]
            captured_bytes = [entry["bytes"] for entry in captured]
            if placeholders and captured_bytes == placeholders:
                for entry in captured:
                    atoms.append(_coverage_atom(entry, "scaffold-alternative", op["role_id"], op_id=op["slot_id"]))
                fulfilled_slots.append(op["slot_id"])
                program_cursor += 1
                continue
            if not captured:
                missing_tokens.append(op["slot_id"])
                walk_tainted = True
                program_cursor += 1
                continue
            valid_rows: list[list[str]] = []
            valid = True
            canonical_placeholder_tokens = {
                placeholder
                for source_form in program["source_forms"].values()
                for candidate in source_form["ops"]
                if candidate["kind"] in {"TABLE_ROWS_SLOT", "NOTES_SLOT"}
                for placeholder in candidate.get("placeholder_rows", [])
            }
            for entry in captured:
                row_cells = _table_cells_bytes(entry["bytes"]) or []
                if (
                    len(row_cells) != op["columns"]
                    or all(re.fullmatch(rb":?-{3,}:?", cell.replace(b" ", b"")) for cell in row_cells)
                    or entry["bytes"] in canonical_placeholder_tokens
                    or _semantic_contains_wrong_role_token(entry["bytes"], exact_tokens, placeholder_spans)
                ):
                    valid = False
                    atoms.append(_coverage_atom(entry, "unclassified-fragment", op["role_id"]))
                    walk_tainted = True
                else:
                    atoms.append(_coverage_atom(entry, "semantic-carrier", op["role_id"], op_id=op["slot_id"]))
                    cells_text = [cell.decode("utf-8") for cell in row_cells]
                    valid_rows.append(cells_text)
                    carriers.append({"slot_id": op["slot_id"], "line": entry, "value": cells_text})
            if valid:
                values[op["table_id"]] = valid_rows
                fulfilled_slots.append(op["slot_id"])
            else:
                missing_tokens.append(op["slot_id"])
            program_cursor += 1
            continue

        if kind == "NOTES_SLOT":
            captured = []
            while source_cursor < len(lines):
                current = lines[source_cursor]
                if next_exact is not None and current["bytes"] == next_exact["exact_bytes"]:
                    break
                captured.append(current)
                source_cursor += 1
            placeholders = op["placeholder_rows"]
            if placeholders and [entry["bytes"] for entry in captured] == placeholders:
                for entry in captured:
                    atoms.append(_coverage_atom(entry, "scaffold-alternative", op["role_id"], op_id=op["slot_id"]))
                fulfilled_slots.append(op["slot_id"])
                program_cursor += 1
                continue
            valid = bool(captured)
            notes: list[str] = []
            for entry in captured:
                body = entry["bytes"].rstrip(b"\r\n")
                if not body.startswith(b"- ") or _semantic_contains_wrong_role_token(body[2:], exact_tokens, placeholder_spans):
                    valid = False
                    atoms.append(_coverage_atom(entry, "unclassified-fragment", op["role_id"]))
                    walk_tainted = True
                else:
                    value = body[2:].decode("utf-8")
                    notes.append(value)
                    atoms.append(_coverage_atom(entry, "semantic-carrier", op["role_id"], op_id=op["slot_id"]))
                    carriers.append({"slot_id": op["slot_id"], "line": entry, "value": value})
            if valid:
                values["notes"] = notes
                fulfilled_slots.append(op["slot_id"])
            else:
                missing_tokens.append(op["slot_id"])
                walk_tainted = True
            program_cursor += 1
            continue

        raise ValidationError(f"unknown template program operation: {kind}")

    while source_cursor < len(lines):
        reject_current("source:residue")
    return {
        "source": source, "coverage_atoms": atoms, "values": values, "carriers": carriers,
        "walk_tainted": walk_tainted, "missing_program_tokens": missing_tokens,
        "duplicate_program_tokens": duplicate_tokens,
        "consumed_required_token_ids": consumed_tokens, "fulfilled_slot_ids": fulfilled_slots,
        "source_form_id": source_form_id, "form_selection_tainted": form_selection_tainted,
        "program": program,
    }


def _prove_byte_coverage(source: bytes, atoms: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(atoms, key=lambda atom: (atom["start"], atom["end"]))
    reconstructed = b"".join(bytes.fromhex(atom["source_bytes_hex"]) for atom in ordered)
    gapless = (
        (not ordered and not source)
        or (
            bool(ordered) and ordered[0]["start"] == 0 and ordered[-1]["end"] == len(source)
            and all(left["end"] == right["start"] for left, right in zip(ordered, ordered[1:]))
            and sum(atom["end"] - atom["start"] for atom in ordered) == len(source)
        )
    )
    valid = gapless and reconstructed == source
    return {
        "valid": valid, "byte_count": len(source),
        "covered_byte_count": sum(atom["end"] - atom["start"] for atom in ordered),
        "coverage_sha256": _canonical_sha256(ordered),
        "reconstructed_sha256": _sha256_bytes(reconstructed),
        "atoms": ordered,
    }


def _inventory_from_walk(walk: dict[str, Any]) -> list[dict[str, Any]]:
    values = walk["values"]
    carriers_by_slot: dict[str, list[dict[str, Any]]] = {}
    for carrier in walk["carriers"]:
        carriers_by_slot.setdefault(carrier["slot_id"], []).append(carrier)
    items: list[dict[str, Any]] = []
    for field in ("round", "last-refreshed", "stack", "pre-walk", "deploy-status"):
        carriers = carriers_by_slot.get(f"header:{field}", [])
        if field in values["headers"] and carriers:
            carrier = carriers[0]
            line = carrier["line"]
            items.append(_source_item(
                f"header:{field}", "header-field", "header", field, field,
                line["line_start"], line["line_end"], values["headers"][field], line["bytes"],
            ))
    for table_id, kind, section in (
        ("definitions", "definition-row", "definitions"),
        ("keys", "key-row", "keys"),
        ("sets", "set-row", "sets"),
    ):
        for ordinal, (cells, carrier) in enumerate(zip(values[table_id], carriers_by_slot.get(f"table:{table_id}:rows", [])), 1):
            key = str(ordinal) if table_id == "definitions" else cells[0].strip("`")
            items.append(_source_item(
                f"{table_id}:row:{key}", kind, section, key, None,
                carrier["line"]["line_start"], carrier["line"]["line_end"], cells, carrier["line"]["bytes"],
            ))
    source_carriers = carriers_by_slot.get("sources:provenance", [])
    if source_carriers:
        line = source_carriers[0]["line"]
        for ordinal, link in enumerate(values["sources"], 1):
            items.append(_source_item(
                f"sources:link:{ordinal}", "provenance-link", "journeys.hot", str(ordinal), "source-link",
                line["line_start"], line["line_end"], link, link.encode("utf-8"),
            ))
    field_names = ("number", "readiness", "who", "journey", "walk", "pass", "fail", "closes", "gwt_sha256")
    identities: set[str] = set()
    for temperature in ("hot", "cold"):
        for cells, carrier in zip(values[temperature], carriers_by_slot.get(f"table:{temperature}:rows", [])):
            row_id = cells[0]
            if row_id in identities:
                raise ValidationError(f"duplicate journey row identity: {row_id}")
            identities.add(row_id)
            line = carrier["line"]
            section = f"journeys.{temperature}"
            row_base = f"journeys:{temperature}:row:{row_id}"
            semantic_cells = list(cells)
            items.append(_source_item(row_base, "journey-row", section, row_id, None, line["line_start"], line["line_end"], semantic_cells, line["bytes"]))
            for field, value in zip(field_names, semantic_cells):
                child_id = f"{row_base}:field:{field}"
                items.append(_source_item(child_id, "journey-field", section, row_id, field, line["line_start"], line["line_end"], value, value.encode("utf-8")))
                for ordinal, link in enumerate(re.findall(r"\[[^\]]+\]\([^\)]+\)", value), 1):
                    if "/session/" in link:
                        items.append(_source_item(f"{child_id}:session-link:{ordinal}", "session-link", section, row_id, f"{field}.session-link", line["line_start"], line["line_end"], link, link.encode("utf-8")))
                if field == "closes":
                    for ordinal, nfr in enumerate(re.findall(r"\bNFR-[A-Z0-9-]+\b", value), 1):
                        items.append(_source_item(f"{child_id}:nfr:{ordinal}", "nfr-reference", section, row_id, "closes.nfr", line["line_start"], line["line_end"], nfr, nfr.encode("utf-8")))
    for ordinal, (note, carrier) in enumerate(zip(values["notes"], carriers_by_slot.get("notes:bullets", [])), 1):
        line = carrier["line"]
        items.append(_source_item(f"notes:bullet:{ordinal}", "note", "notes", str(ordinal), None, line["line_start"], line["line_end"], note, line["bytes"]))

    unclassified = [atom for atom in walk["coverage_atoms"] if atom["kind"] == "unclassified-fragment"]
    groups: list[list[dict[str, Any]]] = []
    comment_open = False
    for atom in unclassified:
        captured = bytes.fromhex(atom["source_bytes_hex"])
        if comment_open and groups and groups[-1][-1]["end"] == atom["start"]:
            groups[-1].append(atom)
        else:
            groups.append([atom])
        if not comment_open and b"<!--" in captured and b"-->" not in captured:
            comment_open = True
        if comment_open and b"-->" in captured:
            comment_open = False
    for group in groups:
        start = group[0]["line_start"]
        end = group[-1]["line_end"]
        exact = b"".join(bytes.fromhex(atom["source_bytes_hex"]) for atom in group)
        semantic = exact.decode("utf-8").rstrip("\r\n")
        if not semantic.strip():
            continue
        items.append(_source_item(
            f"unclassified-fragment:{start}-{end}", "unclassified-fragment", "unclassified", str(start), None,
            start, end, semantic, exact,
        ))
    ids = [item["source_item_id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValidationError("source inventory contains duplicate item identities")
    return items


def _parse_round(content: bytes, *, scaffold_template: bytes | None = None) -> dict[str, Any]:
    bounds = _package_region_bounds(content)
    base_content = content
    region: bytes | None = None
    if bounds is not None:
        start, end = bounds
        region = content[start:end]
        base_content = content[:start] + content[end:]
    program = _compile_default_template_program(scaffold_template)
    source_form_id, selection_tainted = _select_source_form(base_content, program)
    walk = _walk_source_against_program(
        base_content, program, source_form_id, form_selection_tainted=selection_tainted,
    )
    package: Mapping[str, object] | None = None
    package_error: str | None = None
    if region is not None:
        assert bounds is not None
        package, package_error = _package_from_region(region)
        if package is None and package_error is None and scaffold_template is not None:
            expected_bounds = _package_region_bounds(scaffold_template)
            if expected_bounds is not None and region != scaffold_template[expected_bounds[0]:expected_bounds[1]]:
                package_error = "package-placeholder"
        if package_error is not None:
            walk["walk_tainted"] = True
            walk["missing_program_tokens"].append(package_error)
        walk["coverage_atoms"] = _package_region_atoms(content, bounds[0], bounds[1])
    elif scaffold_template is not None:
        expected_bounds = _package_region_bounds(scaffold_template)
        if expected_bounds is not None and content.startswith(scaffold_template[:expected_bounds[0]]):
            walk["walk_tainted"] = True
            walk["missing_program_tokens"].append("package-region")
    walk["coverage_proof"] = _prove_byte_coverage(content, walk["coverage_atoms"])
    walk["items"] = _inventory_from_walk(walk)
    if region is not None and package_error is None:
        walk["walk_tainted"] = False
        walk["duplicate_program_tokens"] = []
        if not walk["items"]:
            walk["items"] = _placeholder_semantic_items(walk)
    for item in walk["items"]:
        item["origin"] = "round-markdown"
    if package is not None:
        walk["package"] = package
        walk["items"].extend(package_semantic_items(package, origin="canonical-package-object"))
    return walk


def _validate_hot_closes_requirement_refs(parsed_round: Mapping[str, Any], package: Mapping[str, object]) -> None:
    values = parsed_round.get("values")
    journeys = package.get("journeys")
    if not isinstance(values, Mapping) or not isinstance(journeys, list):
        raise ValidationError("package.invalid.requirement_ref")
    hot_rows = values.get("hot")
    if not isinstance(hot_rows, list):
        raise ValidationError("package.invalid.requirement_ref")
    if len(hot_rows) > len(journeys):
        raise ValidationError("package.invalid.requirement_ref")
    for cells, journey in zip(hot_rows, journeys, strict=False):
        if not isinstance(cells, list) or len(cells) != 9 or not isinstance(journey, Mapping):
            raise ValidationError("package.invalid.requirement_ref")
        markers = re.findall(r"`AC:([^`]+)`", cells[7])
        if len(markers) != 1:
            raise ValidationError("package.invalid.requirement_ref")
        requirement_ref = markers[0]
        if requirement_ref != requirement_ref.strip() or not requirement_ref or len(requirement_ref) > 128 or any(character in requirement_ref for character in "|\r\n`"):
            raise ValidationError("package.invalid.requirement_ref")
        if journey.get("requirement_ref") != requirement_ref:
            raise ValidationError("package.invalid.requirement_ref")


def _render_default_from_program(program: dict[str, Any], parsed_source: dict[str, Any]) -> bytes:
    values = parsed_source["values"]
    output = bytearray()
    for op in program["source_forms"][program["render_form_id"]]["ops"]:
        kind = op["kind"]
        if kind == "EXACT":
            output.extend(op["exact_bytes"])
        elif kind == "SCALAR_SLOT":
            if op["field"] not in values["headers"]:
                raise ValidationError(f"source is missing required populated header: {op['field']}")
            output.extend(op["prefix"] + values["headers"][op["field"]].encode("utf-8") + op["suffix"])
        elif kind == "PROVENANCE_SLOT":
            source_line = values.get("source_line")
            if not isinstance(source_line, str):
                raise ValidationError("source is missing the required provenance links line")
            output.extend(source_line.encode("utf-8") + op["suffix"])
        elif kind == "TABLE_ROWS_SLOT":
            rows = values[op["table_id"]]
            if rows:
                output.extend(b"".join(("| " + " | ".join(row) + " |\n").encode("utf-8") for row in rows))
            else:
                output.extend(b"".join(op["placeholder_rows"]))
        elif kind == "NOTES_SLOT":
            notes = values["notes"]
            if notes:
                output.extend(b"".join(f"- {note}\n".encode("utf-8") for note in notes))
            else:
                output.extend(b"".join(op["placeholder_rows"]))
        else:
            raise ValidationError(f"unknown template program operation: {kind}")
    return bytes(output)


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "semantic_value"}


def _semantic_location(item: dict[str, Any]) -> dict[str, Any]:
    source = item["source_location"]
    result = {"section": source["section"], "key": source["key"]}
    if "field" in source:
        result["field"] = source["field"]
    if item["kind"] in {"session-link", "nfr-reference"}:
        match = re.fullmatch(r".+:(?:session-link|nfr):([1-9]\d*)", item["source_item_id"])
        if match is None:
            raise ValidationError(f"nested semantic item lacks a positive ordinal: {item['source_item_id']}")
        result["kind"] = item["kind"]
        result["ordinal"] = int(match.group(1))
    return result


def _walk_is_closed(walk: dict[str, Any]) -> bool:
    program = walk["program"]
    form = program["source_forms"][walk["source_form_id"]]
    return (
        program.get("shape_valid") is True
        and walk["source_form_id"] in program["source_forms"]
        and not walk["form_selection_tainted"]
        and walk["coverage_proof"]["valid"]
        and not walk["walk_tainted"]
        and not walk["missing_program_tokens"]
        and not walk["duplicate_program_tokens"]
        and walk["consumed_required_token_ids"] == form["required_token_ids"]
        and walk["fulfilled_slot_ids"] == program["semantic_slot_contract"]
        and not any(item["kind"] == "unclassified-fragment" for item in walk["items"])
    )


def _prove_complete_mapping(
    program: dict[str, Any], source_walk: dict[str, Any], target: bytes | None,
    target_walk: dict[str, Any] | None, dispositions: list[dict[str, Any]],
) -> tuple[bool, list[str], list[str]]:
    source_by_id = {item["source_item_id"]: item for item in source_walk["items"]}
    target_by_id = {item["source_item_id"]: item for item in target_walk["items"]} if target_walk else {}
    for item_id, item in target_by_id.items():
        if item.get("origin") == "canonical-package-object" and item_id not in source_by_id:
            source_by_id[item_id] = item
    source_ids = list(source_by_id)
    disposition_ids = [item["source_item_id"] for item in dispositions]
    missing = sorted(set(source_ids) - set(disposition_ids))
    unexpected = sorted(set(disposition_ids) - set(source_ids))
    if target is None or target_walk is None:
        structural_reasons = source_walk["missing_program_tokens"] + source_walk["duplicate_program_tokens"]
        return False, sorted(set(missing + structural_reasons)), unexpected
    disposition_by_id = {item["source_item_id"]: item for item in dispositions}
    target_locations = [_semantic_location(item) for item in target_by_id.values()]
    semantic_equal = (
        set(source_by_id) == set(target_by_id) == set(disposition_by_id)
        and len(target_locations) == len({json.dumps(location, sort_keys=True) for location in target_locations})
        and all(
            (disposition_by_id[item_id]["disposition"] == (
                "materialize" if source_item.get("origin") == "canonical-package-object" else "retain"
            ))
            and disposition_by_id[item_id]["target_location"] == _semantic_location(target_by_id[item_id])
            and disposition_by_id[item_id]["target_semantic_value_sha256"] == target_by_id[item_id]["semantic_value_sha256"]
            and target_by_id[item_id]["semantic_value_sha256"] == source_item["semantic_value_sha256"]
            for item_id, source_item in source_by_id.items()
        )
    )
    complete = (
        bool(program["scaffold_program_sha256"])
        and _walk_is_closed(source_walk)
        and _walk_is_closed(target_walk)
        and len(source_ids) == len(set(source_ids))
        and len(disposition_ids) == len(set(disposition_ids))
        and set(source_ids) == set(disposition_ids)
        and semantic_equal
        and _sha256_bytes(target) == target_walk["coverage_proof"]["reconstructed_sha256"]
    )
    if not complete and not missing:
        structural_reasons = (
            source_walk["missing_program_tokens"]
            + source_walk["duplicate_program_tokens"]
        )
        missing = sorted(set(structural_reasons or ["source-structure"]))
    return complete, missing, unexpected


def _coverage_fields(prefix: str, proof: dict[str, Any] | None) -> dict[str, Any]:
    if proof is None:
        return {
            f"{prefix}_byte_count": None, f"{prefix}_covered_byte_count": None,
            f"{prefix}_coverage_sha256": None, f"{prefix}_reconstructed_sha256": None,
            f"{prefix}_coverage_atoms": [],
        }
    return {
        f"{prefix}_byte_count": proof["byte_count"],
        f"{prefix}_covered_byte_count": proof["covered_byte_count"],
        f"{prefix}_coverage_sha256": proof["coverage_sha256"],
        f"{prefix}_reconstructed_sha256": proof["reconstructed_sha256"],
        f"{prefix}_coverage_atoms": proof["atoms"],
    }


def _legacy_build_mapping(source: bytes, template: bytes) -> tuple[dict[str, Any], bytes | None, list[str]]:
    program = _compile_default_template_program(template)
    parsed_source = _parse_round(source, scaffold_template=template)
    source_items = parsed_source["items"]
    generated: bytes | None = None
    parsed_target: dict[str, Any] | None = None
    if _walk_is_closed(parsed_source):
        generated = _render_default_from_program(program, parsed_source)
        if source == template:
            generated = source
        parsed_target = _parse_round(generated, scaffold_template=template)
    target_items = {item["source_item_id"]: item for item in parsed_target["items"]} if parsed_target else {}
    dispositions: list[dict[str, Any]] = []
    for item in source_items:
        item_id = item["source_item_id"]
        target_item = target_items.get(item_id)
        if item["kind"] == "unclassified-fragment" or target_item is None:
            continue
        if target_item["semantic_value_sha256"] != item["semantic_value_sha256"]:
            continue
        dispositions.append({
            "source_item_id": item_id, "disposition": "retain",
            "target_location": _semantic_location(target_item),
            "target_semantic_value_sha256": target_item["semantic_value_sha256"],
            "merge_group_id": None, "retirement_reason": None, "retirement_authority": None,
        })
    omitted = os.environ.get("UAT_ROUND_MATERIALIZE_TEST_OMIT_DISPOSITION")
    if omitted:
        dispositions = [entry for entry in dispositions if entry["source_item_id"] != omitted]
    complete, missing, unexpected = _prove_complete_mapping(program, parsed_source, generated, parsed_target, dispositions)
    public_source = [_public_item(item) for item in source_items]
    public_target = [_public_item(item) for item in parsed_target["items"]] if parsed_target else []
    unclassified = [item for item in source_items if item["kind"] == "unclassified-fragment"]
    envelope = {
        "schema_version": 1,
        "source_form_id": parsed_source["source_form_id"],
        "scaffold_program_sha256": program["scaffold_program_sha256"],
        "source_inventory_sha256": _canonical_sha256(public_source),
        "source_item_count": len(public_source), "source_items": public_source,
        "disposition_count": len(dispositions), "dispositions": dispositions,
        "missing_source_item_ids": missing,
        "unexpected_disposition_item_ids": unexpected,
        "unclassified_fragment_count": len(unclassified),
        "generated_target_sha256": _sha256_bytes(generated) if complete and generated is not None else None,
        "generated_target_inventory_sha256": _canonical_sha256(public_target) if complete else None,
        **_coverage_fields("source", parsed_source["coverage_proof"]),
        **_coverage_fields("target", parsed_target["coverage_proof"] if parsed_target else None),
    }
    return envelope, generated if complete else None, missing


def _materialized_round_bytes(source: bytes, template: bytes, package: Mapping[str, object], package_state: str) -> bytes:
    region = render_canonical_package_block_bytes(package, package_state=package_state) + render_normalized_package_annex_bytes(package)
    bounds = _package_region_bounds(source)
    if bounds is not None:
        return source[:bounds[0]] + region + source[bounds[1]:]
    insertion = source.find(b"## Definitions\n")
    if insertion < 0:
        _invalid("package.invalid.round_template")
    return source[:insertion] + region + b"\n" + source[insertion:]


def _complete_package_walk(walk: dict[str, Any]) -> None:
    if any(item["kind"] == "unclassified-fragment" for item in walk["items"]):
        return
    walk["walk_tainted"] = False
    walk["missing_program_tokens"] = []
    walk["duplicate_program_tokens"] = []


def _has_only_missing_package_region(walk: dict[str, Any]) -> bool:
    return (
        walk["missing_program_tokens"] == ["package-region"]
        and not walk["duplicate_program_tokens"]
        and not any(item["kind"] == "unclassified-fragment" for item in walk["items"])
    )


def _is_legacy_package_compat_walk(walk: dict[str, Any]) -> bool:
    return (
        walk["source_form_id"] == "compact-existing@1"
        and not walk["missing_program_tokens"]
        and walk["duplicate_program_tokens"] == ["compact:exact:6:compact:definitions:heading"]
        and not any(item["kind"] == "unclassified-fragment" for item in walk["items"])
    )


def _package_mapping(
    source: bytes, template: bytes, package: Mapping[str, object] | None, package_state: str,
) -> tuple[dict[str, Any], bytes | None, list[str]]:
    program = _compile_default_template_program(template)
    parsed_source = _parse_round(source, scaffold_template=template)
    if package is not None:
        _validate_hot_closes_requirement_refs(parsed_source, package)
        parsed_source["items"].extend(package_semantic_items(package, origin="canonical-package-object"))
        _complete_package_walk(parsed_source)
    elif _has_only_missing_package_region(parsed_source) or _is_legacy_package_compat_walk(parsed_source):
        _complete_package_walk(parsed_source)
    generated = _materialized_round_bytes(source, template, package, package_state) if package is not None else source
    parsed_target = _parse_round(generated, scaffold_template=template)
    if package is not None:
        _complete_package_walk(parsed_target)
    elif _has_only_missing_package_region(parsed_target) or _is_legacy_package_compat_walk(parsed_target):
        _complete_package_walk(parsed_target)
    source_items = parsed_source["items"]
    target_items = {item["source_item_id"]: item for item in parsed_target["items"]}
    dispositions: list[dict[str, Any]] = []
    for item in source_items:
        target_item = target_items.get(item["source_item_id"])
        if target_item is None or target_item["semantic_value_sha256"] != item["semantic_value_sha256"]:
            continue
        dispositions.append({
            "source_item_id": item["source_item_id"],
            "disposition": "materialize" if item.get("origin") == "canonical-package-object" else "retain",
            "target_location": _semantic_location(target_item),
            "target_semantic_value_sha256": target_item["semantic_value_sha256"],
            "merge_group_id": None, "retirement_reason": None, "retirement_authority": None,
        })
    omitted = os.environ.get("UAT_ROUND_MATERIALIZE_TEST_OMIT_DISPOSITION")
    if omitted:
        dispositions = [entry for entry in dispositions if entry["source_item_id"] != omitted]
    complete, missing, unexpected = _prove_complete_mapping(program, parsed_source, generated, parsed_target, dispositions)
    public_source = [_public_item(item) for item in source_items]
    public_target = [_public_item(item) for item in parsed_target["items"]]
    envelope = {
        "schema_version": 1, "source_form_id": parsed_source["source_form_id"],
        "scaffold_program_sha256": program["scaffold_program_sha256"],
        "source_inventory_sha256": _canonical_sha256(public_source), "source_item_count": len(public_source),
        "source_items": public_source, "target_items": public_target,
        "disposition_count": len(dispositions), "dispositions": dispositions,
        "missing_source_item_ids": missing, "unexpected_disposition_item_ids": unexpected,
        "unclassified_fragment_count": sum(
            item["kind"] == "unclassified-fragment" for item in source_items
        ),
        "generated_target_sha256": _sha256_bytes(generated) if complete else None,
        "generated_target_inventory_sha256": _canonical_sha256(public_target) if complete else None,
        **_coverage_fields("source", parsed_source["coverage_proof"]),
        **_coverage_fields("target", parsed_target["coverage_proof"]),
    }
    return envelope, generated if complete else None, missing


def _gate_header_source(
    source: bytes,
    stack_gate_receipt: StackGateReceipt | None,
    prewalk_gate_receipt: PrewalkGateReceipt | None,
    walk_script_gate_receipt: WalkScriptGateReceipt | None = None,
    *,
    walk_script_unverified_row: bool = False,
) -> bytes:
    headers: dict[bytes, str] = {}
    if stack_gate_receipt is not None:
        headers[b"**Stack:** "] = (
            f"CERTIFIED @ {stack_gate_receipt.certified_build_hash} · source {stack_gate_receipt.certification_source} · receipt "
            f"{stack_gate_receipt.certification_receipt_path}#sha256={stack_gate_receipt.certification_receipt_sha256} · "
            f"app {stack_gate_receipt.app_url} · login {stack_gate_receipt.login}"
        )
    if prewalk_gate_receipt is not None:
        headers[b"**Pre-walk:** "] = (
            f"build {prewalk_gate_receipt.certified_build_hash} · package "
            f"{prewalk_gate_receipt.package_id}@{prewalk_gate_receipt.package_hash} · script "
            f"{prewalk_gate_receipt.script_id}@{prewalk_gate_receipt.script_hash} · journeys "
            f"{prewalk_gate_receipt.journeys_walked}/{prewalk_gate_receipt.journeys_total} · atoms "
            f"{prewalk_gate_receipt.atoms_passed}/{prewalk_gate_receipt.atoms_total} · known-check "
            f"{prewalk_gate_receipt.known_check_receipt} · recovery {prewalk_gate_receipt.recovery_use} · "
            f"observer {prewalk_gate_receipt.observer_status} · {prewalk_gate_receipt.completed_at} · verdict "
            f"OWNER-MAY-WALK: {prewalk_gate_receipt.owner_may_walk} · canary {prewalk_gate_receipt.canary_path} · receipt "
            f"{prewalk_gate_receipt.receipt_path}#sha256={prewalk_gate_receipt.receipt_sha256}"
        )
    for prefix, value in headers.items():
        line = next((item for item in source.splitlines(keepends=True) if item.startswith(prefix)), None)
        if line is None:
            _invalid("package.invalid.gate_header")
        source = source.replace(line, prefix + value.encode("utf-8") + b"\n", 1)
    walk_script_row: bytes | None = None
    if walk_script_gate_receipt is not None:
        walk_script_row = (
            f"**Walk-script:** PRESENT and current @ {walk_script_gate_receipt.walk_script_path} · "
            f"sealed-round anchor {walk_script_gate_receipt.sealed_round_anchor}"
        ).encode("utf-8")
    elif walk_script_unverified_row:
        walk_script_row = WALK_SCRIPT_UNVERIFIED_ROW
    if walk_script_row is not None:
        prewalk_line = next((item for item in source.splitlines(keepends=True) if item.startswith(b"**Pre-walk:** ")), None)
        if prewalk_line is None:
            _invalid("package.invalid.gate_header")
        source = source.replace(prewalk_line, prewalk_line + walk_script_row + b"\n", 1)
    if prewalk_gate_receipt is not None:
        source = source.replace(b"<UNVERIFIED", b"GATE-VERIFIED")
    return source


def build_round_render_mapping(
    source: bytes, template: bytes, *, canonical_package: Mapping[str, object] | None = None,
    package_state: str = "DRAFT_SEALED", verified_gwt_sha256_by_journey: Mapping[str, object] | None = None,
    stack_gate_receipt: StackGateReceipt | None = None,
    prewalk_gate_receipt: PrewalkGateReceipt | None = None,
    walk_script_gate_receipt: WalkScriptGateReceipt | None = None,
    walk_script_unverified_row: bool = False,
) -> tuple[dict[str, Any], bytes | None, list[str]]:
    if canonical_package is None:
        if package_state != "DRAFT_SEALED":
            _invalid("package.invalid.package_state_without_package")
        envelope, target, missing = _legacy_build_mapping(source, template)
        if target is not None:
            return envelope, target, missing
        template_region = _package_region_bounds(template)
        if template_region is not None and source.startswith(template[:template_region[0]]):
            return envelope, target, missing
        envelope, target, missing = _package_mapping(source, template, None, package_state)
        return envelope, _gate_header_source(target, stack_gate_receipt, prewalk_gate_receipt, walk_script_gate_receipt, walk_script_unverified_row=walk_script_unverified_row) if target is not None else None, missing
    error = validate_normalized_package(canonical_package)
    if error is not None:
        _invalid(error)
    if not _valid_gwt_binding(canonical_package, verified_gwt_sha256_by_journey):
        _invalid("package.invalid.gwt_binding")
    if package_state not in PACKAGE_STATES:
        _invalid("package.invalid.package_state")
    envelope, target, missing = _package_mapping(source, template, canonical_package, package_state)
    return envelope, _gate_header_source(target, stack_gate_receipt, prewalk_gate_receipt, walk_script_gate_receipt, walk_script_unverified_row=walk_script_unverified_row) if target is not None else None, missing


def _build_mapping(
    source: bytes, template: bytes, *, canonical_package: Mapping[str, object] | None = None,
    package_state: str = "DRAFT_SEALED", verified_gwt_sha256_by_journey: Mapping[str, object] | None = None,
    stack_gate_receipt: StackGateReceipt | None = None,
    prewalk_gate_receipt: PrewalkGateReceipt | None = None,
) -> tuple[dict[str, Any], bytes | None, list[str]]:
    return build_round_render_mapping(
        source, template, canonical_package=canonical_package, package_state=package_state,
        verified_gwt_sha256_by_journey=verified_gwt_sha256_by_journey,
        stack_gate_receipt=stack_gate_receipt,
        prewalk_gate_receipt=prewalk_gate_receipt,
    )

def _state(
    *, pattern: dict[str, Any], template_sha: str, ruleset_id: str,
    receipt_path: Path, lifecycle_state: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pattern_id": pattern["id"],
        "pattern_version": pattern["version"],
        "template_sha256": template_sha,
        "ruleset_id": ruleset_id,
        "last_materialization_receipt": str(receipt_path),
        "lifecycle_state": lifecycle_state,
    }


def _switch_target_guard(pattern: dict[str, Any]) -> None:
    if pattern["id"] != "default" or pattern["version"] != 1 or pattern["source"] != "controlled-config":
        raise ValidationError(
            "unsupported-pattern-deferred: Phase-2 switch target must be controlled-config default@1; "
            f"requested={pattern['id']}@{pattern['version']} source={pattern['source']}; "
            "follow_on=general-non-default-uat-round-switching"
        )


def _initialization_write(
    ordinal: int, parent_fd: int, destination: str, content: bytes, *, exclusive: bool,
    on_commit: Callable[[], None],
) -> None:
    """Create/adopt-only deterministic fault seam; raise before selected write."""
    injected = os.environ.get("UAT_ROUND_MATERIALIZE_TEST_FAIL_WRITE_AT")
    if injected:
        if not re.fullmatch(r"[1-9]\d*", injected):
            raise ValidationError("test initialization write ordinal must be a positive decimal integer")
        fail_at = int(injected)
        if fail_at == ordinal:
            raise ValidationError(f"injected initialization write failure before ordinal {ordinal}")
    _write_at(
        parent_fd, destination, content, exclusive=exclusive, on_commit=on_commit,
    )


_SEMANTIC_VERDICT_HEADER = (
    "| Scenario | Canonical GWT | Paraphrase probes | L1 invariants | L2 semantic vote | "
    "L3 continuation | Overall | Key evidence |"
)
_SEMANTIC_VERDICT_LAYER_DISPOSITIONS = {"PASS", "FAIL", "BLOCKED"}
_SEMANTIC_VERDICT_L2_CELL = re.compile(
    r"^(?P<passes>\d+)/(?P<completed>\d+);\s*threshold\s+(?P<threshold>\d+);\s*"
    r"(?P<disposition>PASS|FAIL|BLOCKED)$"
)
_SEMANTIC_VERDICT_PROBE_HEADING = re.compile(r"^###\s+Probe\s+\d+\s+—\s+(?:canonical|paraphrase)\s*$")
_SEMANTIC_VERDICT_PROBE_DISPOSITION = re.compile(r"^\*\*Disposition:\*\*\s+(PASS|FAIL|BLOCKED)\s*$")


def _semantic_verdict_probe_dispositions(lines: list[str]) -> list[str]:
    """Return each per-probe block's recorded disposition, in document order."""
    dispositions: list[str] = []
    awaiting_disposition = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if _SEMANTIC_VERDICT_PROBE_HEADING.match(stripped):
            awaiting_disposition = True
            continue
        if awaiting_disposition:
            match = _SEMANTIC_VERDICT_PROBE_DISPOSITION.match(stripped)
            if match is None:
                continue
            dispositions.append(match.group(1))
            awaiting_disposition = False
    return dispositions


def _validate_semantic_verdict(markdown: str, *, expected_scenario_id: str) -> None:
    """Deterministically refuse a malformed semantic verdict before a round is materialized.

    Enforces the Phase 3 consumption gate (AC-3): the frozen eight-column aggregate
    row (exactly one row per scenario) must be internally consistent — all three layer
    dispositions present and parseable, an Overall PASS only when L1, L2, and L3 are all
    PASS, an L2 cell matching the frozen compact grammar with a formula-correct threshold
    and a numerator that does not exceed its denominator, a completed-probe denominator that
    matches the per-probe detail blocks actually recorded, and any BLOCKED probe forcing a
    BLOCKED Overall — and must be bound to the scenario under materialization.
    """
    lines = markdown.splitlines()
    header_index = next((index for index, line in enumerate(lines) if line.strip() == _SEMANTIC_VERDICT_HEADER), None)
    if header_index is None:
        raise ValidationError("semantic verdict is missing the frozen eight-column aggregate header")

    data_rows: list[str] = []
    for line in lines[header_index + 2:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        data_rows.append(stripped)
    if len(data_rows) != 1:
        raise ValidationError(
            f"semantic verdict aggregate must contain exactly one row per scenario; found {len(data_rows)} rows"
        )

    cells = [cell.strip() for cell in data_rows[0].strip("|").split("|")]
    if len(cells) != 8:
        raise ValidationError("semantic verdict aggregate row must have exactly eight columns")
    row_scenario_id, _canonical_gwt, _paraphrase_probes, l1, l2_cell, l3, overall, _key_evidence = cells

    if row_scenario_id != expected_scenario_id:
        raise ValidationError(
            f"semantic verdict scenario ID does not match: expected {expected_scenario_id!r}, got {row_scenario_id!r}"
        )

    for label, value in (("L1", l1), ("L3", l3)):
        if value not in _SEMANTIC_VERDICT_LAYER_DISPOSITIONS:
            raise ValidationError(
                f"{label} layer disposition is missing or unparseable; all three layer dispositions are required"
            )

    l2_match = _SEMANTIC_VERDICT_L2_CELL.match(l2_cell)
    if l2_match is None:
        raise ValidationError("L2 semantic vote cell does not match the frozen compact shape")

    semantic_passes = int(l2_match.group("passes"))
    claimed_completed_probes = int(l2_match.group("completed"))
    claimed_threshold = int(l2_match.group("threshold"))
    l2_disposition = l2_match.group("disposition")

    if overall not in _SEMANTIC_VERDICT_LAYER_DISPOSITIONS:
        raise ValidationError("Overall disposition is missing or unparseable")

    if overall == "PASS":
        failing_layer = next(
            (label for label, value in (("L1", l1), ("L2", l2_disposition), ("L3", l3)) if value != "PASS"),
            None,
        )
        if failing_layer is not None:
            raise ValidationError(
                f"overall verdict is PASS but layer {failing_layer} is not PASS; "
                "overall PASS requires L1, L2, and L3 to all be PASS"
            )

    if semantic_passes > claimed_completed_probes:
        raise ValidationError(
            f"L2 semantic_passes ({semantic_passes}) exceeds completed_probes ({claimed_completed_probes})"
        )

    expected_threshold = claimed_completed_probes // 2 + 1
    if claimed_threshold != expected_threshold:
        raise ValidationError(
            f"L2 threshold {claimed_threshold} does not equal floor(completed_probes/2)+1={expected_threshold}"
        )

    probe_dispositions = _semantic_verdict_probe_dispositions(lines)
    completed_probe_blocks = sum(1 for disposition in probe_dispositions if disposition in {"PASS", "FAIL"})
    if claimed_completed_probes != completed_probe_blocks:
        raise ValidationError(
            f"L2 completed_probes ({claimed_completed_probes}) does not match the {completed_probe_blocks} "
            "completed probe rows recorded in the per-probe detail blocks"
        )

    if any(disposition == "BLOCKED" for disposition in probe_dispositions) and overall != "BLOCKED":
        raise ValidationError("a BLOCKED probe disposition requires the scenario Overall to be BLOCKED")


def _create_or_adopt(args: argparse.Namespace, *, adopt: bool) -> dict[str, Any]:
    semantic_verdict = getattr(args, "semantic_verdict", None)
    if semantic_verdict is not None:
        scenario_id = getattr(args, "scenario_id", None)
        if not scenario_id:
            raise ValidationError("scenario_id is required when semantic_verdict is supplied")
        _validate_semantic_verdict(semantic_verdict, expected_scenario_id=scenario_id)
    ticket_dir = args.plans_root.absolute() / args.ticket
    ruleset_id, pattern, template_path = _selected_pattern(args.rules, args.patterns, args.pattern, ticket_dir=ticket_dir)
    template = template_path.read_bytes()
    template_sha = _sha256_bytes(template)
    rules_sha = _sha256_bytes(args.rules.read_bytes())
    mode = "absent-only-adopt" if adopt else "create"
    decision = "adopt" if adopt else "create"
    receipt_name = f"{args.ticket}.{'adopt' if adopt else 'create'}.receipt.json"
    with _RoundTree(args.plans_root, args.ticket, create=True) as tree:
        assert tree.ticket_fd is not None and tree.history_fd is not None
        live_exists = _exists_at(tree.ticket_fd, tree.live_name)
        if live_exists:
            if adopt:
                raise ValidationError("absent-only adoption refused: existing round requires switch")
            if not _exists_at(tree.ticket_fd, tree.state_name):
                raise ValidationError("legacy round has no pattern metadata; use receipt-gated switch")
            raise ValidationError("round already exists; use receipt-gated switch")
        if _exists_at(tree.ticket_fd, tree.state_name) or _directory_has_entries(tree.history_fd):
            raise ValidationError("partial materialization state exists while round is absent")
        receipt = {
            "command_mode": mode,
            "mapping_decision": decision,
            "mapping_envelope": [],
            "mapping_sha256": _canonical_sha256([]),
            "pattern_record_sha256": _canonical_sha256(pattern),
            "rules_sha256": rules_sha,
            "selected_pattern": {"id": pattern["id"], "version": pattern["version"], "source": pattern["source"]},
            "source_sha256": None,
            "target_sha256": template_sha,
            "template_sha256": template_sha,
            "ticket": args.ticket,
        }
        receipt_path = tree.history_path / receipt_name
        state_value = _state(pattern=pattern, template_sha=template_sha, ruleset_id=ruleset_id, receipt_path=receipt_path, lifecycle_state="INITIALIZED_NOT_READY")
        writes = (
            (tree.ticket_fd, tree.live_name, template),
            (tree.history_fd, receipt_name, _json_bytes(receipt)),
            (tree.ticket_fd, tree.state_name, _json_bytes(state_value)),
        )
        committed: list[tuple[int, str]] = []
        try:
            for ordinal, (parent_fd, destination, content) in enumerate(writes, 1):
                _initialization_write(
                    ordinal, parent_fd, destination, content, exclusive=True,
                    on_commit=lambda parent_fd=parent_fd, destination=destination: committed.append(
                        (parent_fd, destination)
                    ),
                )
        except BaseException as initialization_error:
            rollback_errors: list[str] = []
            for parent_fd, destination in reversed(committed):
                try:
                    _unlink_at(parent_fd, destination)
                except BaseException as exc:
                    rollback_errors.append(f"{destination} rollback failed: {exc}")
            try:
                tree.remove_created_empty_history()
            except BaseException as exc:
                rollback_errors.append(f"history rollback failed: {exc}")
            if rollback_errors:
                raise ValidationError(
                    f"{mode} failed ({initialization_error}); {'; '.join(rollback_errors)}"
                ) from initialization_error
            raise
        return receipt


def _preview_receipt_name(ticket: str, now: datetime, receipt_id: str) -> str:
    return f"{ticket}.switch-preview.{_history_stamp(now)}.{receipt_id}.receipt.json"


def _is_identified_default(state_raw: bytes | None, pattern: dict[str, Any], template_sha: str) -> bool:
    if state_raw is None:
        return False
    state = _parse_json_bytes(state_raw, "pattern state")
    return (
        state.get("pattern_id") == pattern["id"]
        and state.get("pattern_version") == pattern["version"]
        and state.get("template_sha256") == template_sha
    )


def _switch_preview(args: argparse.Namespace) -> dict[str, Any]:
    ticket_dir = args.plans_root.absolute() / args.ticket
    _ruleset_id, pattern, template_path = _selected_pattern(args.rules, args.patterns, args.pattern, ticket_dir=ticket_dir)
    _switch_target_guard(pattern)
    template = template_path.read_bytes()
    now = _now_utc()
    receipt_id = os.urandom(16).hex()
    receipt_name = _preview_receipt_name(args.ticket, now, receipt_id)
    with _RoundTree(args.plans_root, args.ticket, create=True) as tree:
        assert tree.ticket_fd is not None and tree.history_fd is not None
        source = _read_at(tree.ticket_fd, tree.live_name)
        assert source is not None
        state_raw = _read_at(tree.ticket_fd, tree.state_name, required=False)
        template_sha = _sha256_bytes(template)
        envelope, target, missing = _build_mapping(source, template)
        complete = target is not None and envelope["generated_target_sha256"] is not None
        selected = {"id": pattern["id"], "version": pattern["version"], "source": pattern["source"]}
        receipt = {
            "schema_version": 1,
            "receipt_kind": "uat-round-switch-preview",
            "receipt_id": receipt_id,
            "command_mode": "switch-preview",
            "ticket": args.ticket,
            "history_child": receipt_name,
            "issued_at": _utc_text(now),
            "expires_at": _utc_text(now + PREVIEW_TTL),
            "apply_capable": complete,
            "selected_pattern": selected,
            "source_sha256": _sha256_bytes(source),
            "source_state_sha256": _sha256_bytes(state_raw) if state_raw is not None else None,
            "rules_sha256": _sha256_bytes(args.rules.read_bytes()),
            "pattern_record_sha256": _canonical_sha256(pattern),
            "template_sha256": template_sha,
            "target_sha256": _sha256_bytes(target) if target is not None else None,
            "mapping_decision": "complete" if complete else "incomplete",
            "mapping_envelope": envelope,
            "mapping_sha256": _canonical_sha256(envelope),
            "missing_source_item_ids": missing,
        }
        _write_at(tree.history_fd, receipt_name, _json_bytes(receipt), exclusive=True)
    if not complete:
        raise ValidationError(f"incomplete mapping; missing source-item IDs: {', '.join(missing)}")
    return receipt


def _parse_json_bytes(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return value


_PREVIEW_FIELDS = {
    "schema_version", "receipt_kind", "receipt_id", "command_mode", "ticket",
    "history_child", "issued_at", "expires_at", "apply_capable", "selected_pattern",
    "source_sha256", "source_state_sha256", "rules_sha256", "pattern_record_sha256",
    "template_sha256", "target_sha256", "mapping_decision", "mapping_envelope",
    "mapping_sha256", "missing_source_item_ids",
}
_ENVELOPE_FIELDS = {
    "schema_version", "source_form_id", "scaffold_program_sha256",
    "source_inventory_sha256", "source_item_count", "source_items",
    "disposition_count", "dispositions", "missing_source_item_ids",
    "unexpected_disposition_item_ids", "unclassified_fragment_count",
    "generated_target_sha256", "generated_target_inventory_sha256", "target_items",
    "source_byte_count", "source_covered_byte_count", "source_coverage_sha256",
    "source_reconstructed_sha256", "source_coverage_atoms",
    "target_byte_count", "target_covered_byte_count", "target_coverage_sha256",
    "target_reconstructed_sha256", "target_coverage_atoms",
}
_COVERAGE_ATOM_FIELDS = {
    "kind", "role_id", "start", "end", "line_start", "line_end", "source_bytes_hex",
}
_SOURCE_ITEM_FIELDS = {
    "source_item_id", "kind", "source_location", "semantic_value_sha256", "exact_bytes_sha256",
    "origin",
}
_DISPOSITION_FIELDS = {
    "source_item_id", "disposition", "target_location", "target_semantic_value_sha256",
    "merge_group_id", "retirement_reason", "retirement_authority",
}
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def _strict_int(value: Any, label: str, *, minimum: int | None = None) -> None:
    if type(value) is not int or (minimum is not None and value < minimum):
        qualifier = f" at least {minimum}" if minimum is not None else ""
        raise ValidationError(f"{label} must be a strict integer{qualifier}")


def _exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise ValidationError(f"{label} fields are invalid; missing={missing}, unknown={unknown}")


def _require_sha256(value: Any, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValidationError(f"{label} must be lowercase SHA-256 hex")


def _strict_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TEXT.fullmatch(value):
        raise ValidationError(f"{label} must be strict microsecond UTC Z text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValidationError(f"{label} is not a valid UTC timestamp") from exc
    if _utc_text(parsed) != value:
        raise ValidationError(f"{label} is not canonical UTC text")
    return parsed


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValidationError(f"{label} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValidationError(f"{label} must not contain duplicates")
    return value


def _validate_mapping_envelope(envelope: Any, preview: dict[str, Any]) -> None:
    if not isinstance(envelope, dict):
        raise ValidationError("preview mapping_envelope must be an object")
    _exact_fields(envelope, _ENVELOPE_FIELDS, "preview mapping_envelope")
    if envelope["schema_version"] != 1 or type(envelope["schema_version"]) is not int:
        raise ValidationError("preview mapping envelope requires strict schema_version 1")
    if envelope["source_form_id"] not in {"canonical@1", "compact-existing@1"}:
        raise ValidationError("preview mapping envelope has an invalid source_form_id")
    for field in ("source_item_count", "disposition_count", "unclassified_fragment_count"):
        _strict_int(envelope[field], f"preview mapping {field}", minimum=0)
    source_items = envelope["source_items"]
    dispositions = envelope["dispositions"]
    if not isinstance(source_items, list) or not isinstance(dispositions, list):
        raise ValidationError("preview mapping source_items and dispositions must be arrays")
    if envelope["source_item_count"] != len(source_items):
        raise ValidationError("preview mapping source_item_count is inconsistent")
    if envelope["disposition_count"] != len(dispositions):
        raise ValidationError("preview mapping disposition_count is inconsistent")

    source_ids: list[str] = []
    for offset, item in enumerate(source_items):
        if not isinstance(item, dict):
            raise ValidationError(f"preview source_items[{offset}] must be an object")
        _exact_fields(item, _SOURCE_ITEM_FIELDS, f"preview source_items[{offset}]")
        item_id = item["source_item_id"]
        if not isinstance(item_id, str) or not item_id:
            raise ValidationError(f"preview source_items[{offset}].source_item_id is invalid")
        if not isinstance(item["kind"], str) or not item["kind"]:
            raise ValidationError(f"preview source_items[{offset}].kind is invalid")
        if item["origin"] not in {"round-markdown", "canonical-package-object"}:
            raise ValidationError(f"preview source_items[{offset}].origin is invalid")
        location = item["source_location"]
        if not isinstance(location, dict) or set(location) not in (
            {"section", "key", "line_start", "line_end"},
            {"section", "key", "field", "line_start", "line_end"},
        ):
            raise ValidationError(f"preview source_items[{offset}].source_location is invalid")
        if any(not isinstance(location[field], str) or not location[field] for field in ("section", "key")):
            raise ValidationError(f"preview source_items[{offset}].source_location strings are invalid")
        if "field" in location and (not isinstance(location["field"], str) or not location["field"]):
            raise ValidationError(f"preview source_items[{offset}].source_location.field is invalid")
        for field in ("line_start", "line_end"):
            _strict_int(
                location[field], f"preview source_items[{offset}].source_location.{field}", minimum=1,
            )
        if location["line_end"] < location["line_start"]:
            raise ValidationError(f"preview source_items[{offset}].source line range is reversed")
        _require_sha256(item["semantic_value_sha256"], f"preview source_items[{offset}].semantic_value_sha256")
        _require_sha256(item["exact_bytes_sha256"], f"preview source_items[{offset}].exact_bytes_sha256")
        source_ids.append(item_id)
    if len(source_ids) != len(set(source_ids)):
        raise ValidationError("preview mapping source item IDs are duplicated")

    target_items = envelope["target_items"]
    if not isinstance(target_items, list):
        raise ValidationError("preview mapping target_items must be an array")
    for offset, item in enumerate(target_items):
        if not isinstance(item, dict):
            raise ValidationError(f"preview target_items[{offset}] must be an object")
        item_id = item.get("source_item_id")
        if not isinstance(item_id, str) or not item_id:
            raise ValidationError(f"preview target_items[{offset}].source_item_id is invalid")
    if envelope["generated_target_inventory_sha256"] is not None and _canonical_sha256(
        target_items
    ) != envelope["generated_target_inventory_sha256"]:
        raise ValidationError(
            "preview target_items do not match generated_target_inventory_sha256"
        )

    disposition_ids: list[str] = []
    for offset, disposition in enumerate(dispositions):
        if not isinstance(disposition, dict):
            raise ValidationError(f"preview dispositions[{offset}] must be an object")
        _exact_fields(disposition, _DISPOSITION_FIELDS, f"preview dispositions[{offset}]")
        item_id = disposition["source_item_id"]
        if not isinstance(item_id, str) or not item_id:
            raise ValidationError(f"preview dispositions[{offset}].source_item_id is invalid")
        if disposition["disposition"] != "retain":
            raise ValidationError("default@1 preview dispositions must all be retain")
        target_location = disposition["target_location"]
        aggregate_location_fields = ({"section", "key"}, {"section", "key", "field"})
        nested_location_fields = {"section", "key", "field", "kind", "ordinal"}
        if not isinstance(target_location, dict) or set(target_location) not in (
            *aggregate_location_fields, nested_location_fields,
        ):
            raise ValidationError(f"preview dispositions[{offset}].target_location is invalid")
        string_fields = set(target_location) - {"ordinal"}
        if any(not isinstance(target_location[field], str) or not target_location[field] for field in string_fields):
            raise ValidationError(f"preview dispositions[{offset}].target_location strings are invalid")
        if set(target_location) == nested_location_fields:
            if target_location["kind"] not in {"session-link", "nfr-reference"}:
                raise ValidationError(f"preview dispositions[{offset}].target_location.kind is invalid")
            _strict_int(
                target_location["ordinal"],
                f"preview dispositions[{offset}].target_location.ordinal",
                minimum=1,
            )
        _require_sha256(
            disposition["target_semantic_value_sha256"],
            f"preview dispositions[{offset}].target_semantic_value_sha256",
        )
        source_item = source_items[source_ids.index(item_id)] if item_id in source_ids else None
        if source_item is None or (
            disposition["target_semantic_value_sha256"] != source_item["semantic_value_sha256"]
        ):
            raise ValidationError("preview retain disposition semantic hash differs from its source item")
        expected_location = _semantic_location(source_item)
        if target_location != expected_location:
            raise ValidationError("preview retain disposition target location differs from its semantic source location")
        if any(disposition[field] is not None for field in (
            "merge_group_id", "retirement_reason", "retirement_authority",
        )):
            raise ValidationError("retain disposition metadata must be null")
        disposition_ids.append(item_id)
    if len(disposition_ids) != len(set(disposition_ids)):
        raise ValidationError("preview mapping disposition IDs are duplicated")

    missing = _string_list(envelope["missing_source_item_ids"], "preview envelope missing_source_item_ids")
    unexpected = _string_list(
        envelope["unexpected_disposition_item_ids"],
        "preview envelope unexpected_disposition_item_ids",
    )
    calculated_missing = sorted(set(source_ids) - set(disposition_ids))
    calculated_unexpected = sorted(set(disposition_ids) - set(source_ids))
    if missing != calculated_missing or unexpected != calculated_unexpected:
        raise ValidationError("preview mapping missing/unexpected IDs are inconsistent")
    if preview["missing_source_item_ids"] != missing:
        raise ValidationError("preview top-level missing source IDs differ from mapping envelope")
    unclassified = sum(item["kind"] == "unclassified-fragment" for item in source_items)
    if envelope["unclassified_fragment_count"] != unclassified:
        raise ValidationError("preview mapping unclassified fragment count is inconsistent")
    if set(source_ids) != set(disposition_ids) or missing or unexpected or unclassified:
        raise ValidationError("preview complete mapping envelope is not complete")
    if envelope["source_inventory_sha256"] != _canonical_sha256(source_items):
        raise ValidationError("preview source inventory hash is inconsistent")
    _require_sha256(envelope["source_inventory_sha256"], "preview source_inventory_sha256")
    _require_sha256(envelope["generated_target_sha256"], "preview generated_target_sha256")
    _require_sha256(
        envelope["generated_target_inventory_sha256"],
        "preview generated_target_inventory_sha256",
    )
    if envelope["generated_target_sha256"] != preview["target_sha256"]:
        raise ValidationError("preview generated target hash differs from top-level target hash")
    _require_sha256(envelope["scaffold_program_sha256"], "preview scaffold_program_sha256")
    for prefix in ("source", "target"):
        byte_count = envelope[f"{prefix}_byte_count"]
        covered = envelope[f"{prefix}_covered_byte_count"]
        _strict_int(byte_count, f"preview {prefix}_byte_count", minimum=0)
        _strict_int(covered, f"preview {prefix}_covered_byte_count", minimum=0)
        if byte_count != covered:
            raise ValidationError(f"preview {prefix} byte coverage is incomplete")
        _require_sha256(envelope[f"{prefix}_coverage_sha256"], f"preview {prefix}_coverage_sha256")
        _require_sha256(envelope[f"{prefix}_reconstructed_sha256"], f"preview {prefix}_reconstructed_sha256")
        atoms = envelope[f"{prefix}_coverage_atoms"]
        if not isinstance(atoms, list):
            raise ValidationError(f"preview {prefix}_coverage_atoms must be an array")
        cursor = 0
        reconstructed = bytearray()
        for offset, atom in enumerate(atoms):
            if not isinstance(atom, dict):
                raise ValidationError(f"preview {prefix}_coverage_atoms[{offset}] must be an object")
            fields = set(atom)
            if fields not in (_COVERAGE_ATOM_FIELDS, _COVERAGE_ATOM_FIELDS | {"op_id"}):
                raise ValidationError(f"preview {prefix}_coverage_atoms[{offset}] fields are invalid")
            for field in ("kind", "role_id"):
                if not isinstance(atom[field], str) or not atom[field]:
                    raise ValidationError(f"preview {prefix}_coverage_atoms[{offset}].{field} is invalid")
            if "op_id" in atom and (not isinstance(atom["op_id"], str) or not atom["op_id"]):
                raise ValidationError(f"preview {prefix}_coverage_atoms[{offset}].op_id is invalid")
            for field in ("start", "end"):
                _strict_int(atom[field], f"preview {prefix}_coverage_atoms[{offset}].{field}", minimum=0)
            for field in ("line_start", "line_end"):
                _strict_int(atom[field], f"preview {prefix}_coverage_atoms[{offset}].{field}", minimum=1)
            if atom["start"] != cursor or atom["end"] < atom["start"]:
                raise ValidationError(f"preview {prefix} coverage atoms are not a gapless ordered partition")
            try:
                captured = bytes.fromhex(atom["source_bytes_hex"])
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"preview {prefix}_coverage_atoms[{offset}].source_bytes_hex is invalid") from exc
            if len(captured) != atom["end"] - atom["start"]:
                raise ValidationError(f"preview {prefix} coverage atom byte range is inconsistent")
            reconstructed.extend(captured)
            cursor = atom["end"]
        if cursor != byte_count or len(reconstructed) != byte_count:
            raise ValidationError(f"preview {prefix} coverage atom cardinality is inconsistent")
        if envelope[f"{prefix}_coverage_sha256"] != _canonical_sha256(atoms):
            raise ValidationError(f"preview {prefix} coverage ledger hash is inconsistent")
        if envelope[f"{prefix}_reconstructed_sha256"] != _sha256_bytes(bytes(reconstructed)):
            raise ValidationError(f"preview {prefix} reconstructed hash is inconsistent")
    if envelope["source_reconstructed_sha256"] != preview["source_sha256"]:
        raise ValidationError("preview source coverage does not reconstruct the bound source")
    if envelope["target_reconstructed_sha256"] != preview["target_sha256"]:
        raise ValidationError("preview target coverage does not reconstruct the generated target")


def _validate_typed_preview(name: str, preview: dict[str, Any], pattern: dict[str, Any], ticket: str) -> None:
    _exact_fields(preview, _PREVIEW_FIELDS, "preview receipt")
    if preview["schema_version"] != 1 or type(preview["schema_version"]) is not int:
        raise ValidationError("preview receipt requires strict schema_version 1")
    if preview["receipt_kind"] != "uat-round-switch-preview" or preview["command_mode"] != "switch-preview":
        raise ValidationError("preview receipt has the wrong kind")
    if preview["ticket"] != ticket:
        raise ValidationError("preview receipt ticket does not match switch-apply")
    receipt_id = preview["receipt_id"]
    if not isinstance(receipt_id, str) or not _HEX_32.fullmatch(receipt_id):
        raise ValidationError("preview receipt_id must be 32 lowercase hex characters")
    issued = _strict_utc(preview["issued_at"], "preview issued_at")
    expiry = _strict_utc(preview["expires_at"], "preview expires_at")
    if expiry != issued + PREVIEW_TTL:
        raise ValidationError("preview expires_at must equal issued_at plus the preview TTL")
    expected_name = _preview_receipt_name(ticket, issued, receipt_id)
    if preview["history_child"] != name or name != expected_name:
        raise ValidationError("preview receipt basename is not bound to issued_at and receipt_id")
    if preview["apply_capable"] is not True or preview["mapping_decision"] != "complete":
        raise ValidationError("preview receipt is not apply-capable")
    selection = preview["selected_pattern"]
    if not isinstance(selection, dict):
        raise ValidationError("preview selected_pattern must be an object")
    _exact_fields(selection, {"id", "version", "source"}, "preview selected_pattern")
    if not isinstance(selection["id"], str) or not selection["id"]:
        raise ValidationError("preview selected_pattern.id is invalid")
    _strict_int(selection["version"], "preview selected_pattern.version", minimum=1)
    if not isinstance(selection["source"], str) or not selection["source"]:
        raise ValidationError("preview selected_pattern.source is invalid")
    expected_selection = {"id": pattern["id"], "version": pattern["version"], "source": pattern["source"]}
    if selection != expected_selection:
        raise ValidationError("preview receipt selected pattern does not match switch-apply")
    for field in (
        "source_sha256", "rules_sha256", "pattern_record_sha256", "template_sha256",
        "target_sha256", "mapping_sha256",
    ):
        _require_sha256(preview[field], f"preview {field}")
    _require_sha256(preview["source_state_sha256"], "preview source_state_sha256", nullable=True)
    _string_list(preview["missing_source_item_ids"], "preview missing_source_item_ids")
    _validate_mapping_envelope(preview["mapping_envelope"], preview)
    if preview["mapping_sha256"] != _canonical_sha256(preview["mapping_envelope"]):
        raise ValidationError("preview migration mapping is absent or changed")


def _validated_preview(tree: _RoundTree, supplied: Path, pattern: dict[str, Any]) -> tuple[str, bytes, dict[str, Any]]:
    assert tree.history_fd is not None
    expected_parent = tree.history_path.absolute()
    supplied_absolute = supplied.absolute()
    if supplied_absolute.parent != expected_parent or supplied.name != supplied_absolute.name:
        raise ValidationError("preview receipt must be an exact direct ticket-history child")
    name = _safe_component(supplied.name, "preview receipt")
    try:
        info = os.lstat(name, dir_fd=tree.history_fd)
    except FileNotFoundError as exc:
        raise ValidationError("preview receipt does not exist") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ValidationError("preview receipt must be a direct regular history child, not a symlink")
    raw = _read_at(tree.history_fd, name)
    assert raw is not None
    preview = _parse_json_bytes(raw, "preview receipt")
    _validate_typed_preview(name, preview, pattern, tree.ticket)
    return name, raw, preview


def _switch_apply(args: argparse.Namespace) -> dict[str, Any]:
    ticket_dir = args.plans_root.absolute() / args.ticket
    ruleset_id, pattern, template_path = _selected_pattern(args.rules, args.patterns, args.pattern, ticket_dir=ticket_dir)
    _switch_target_guard(pattern)
    template = template_path.read_bytes()
    rules_bytes = args.rules.read_bytes()
    now = _now_utc()
    with _RoundTree(args.plans_root, args.ticket, create=False) as tree:
        assert tree.ticket_fd is not None and tree.history_fd is not None
        preview_name, preview_raw, preview = _validated_preview(tree, args.preview_receipt, pattern)
        issued = _strict_utc(preview["issued_at"], "preview issued_at")
        expiry = _strict_utc(preview["expires_at"], "preview expires_at")
        if not (issued <= now < expiry):
            raise ValidationError("preview receipt has expired or is not yet valid")
        source = _read_at(tree.ticket_fd, tree.live_name)
        assert source is not None
        source_sha = _sha256_bytes(source)
        if preview.get("source_sha256") != source_sha:
            raise ValidationError("source hash changed after switch-preview")
        state_raw = _read_at(tree.ticket_fd, tree.state_name, required=False)
        state_sha = _sha256_bytes(state_raw) if state_raw is not None else None
        if preview.get("source_state_sha256") != state_sha:
            raise ValidationError("source pattern state changed after switch-preview")
        if preview.get("rules_sha256") != _sha256_bytes(rules_bytes):
            raise ValidationError("rules changed after switch-preview")
        if preview.get("template_sha256") != _sha256_bytes(template):
            raise ValidationError("target template changed after switch-preview")
        if preview.get("pattern_record_sha256") != _canonical_sha256(pattern):
            raise ValidationError("selected pattern record changed after switch-preview")
        envelope, target, missing = _build_mapping(source, template)
        if target is None or envelope["generated_target_sha256"] is None:
            raise ValidationError("regenerated migration mapping is incomplete")
        if _canonical_sha256(envelope) != preview.get("mapping_sha256"):
            raise ValidationError("regenerated migration mapping differs from preview")
        target_sha = _sha256_bytes(target)
        if target_sha != preview.get("target_sha256"):
            raise ValidationError("regenerated target hash differs from preview")

        previous_lifecycle: str | None = None
        if state_raw is not None:
            previous_state = _parse_json_bytes(state_raw, "pattern state")
            lifecycle = previous_state.get("lifecycle_state")
            if not isinstance(lifecycle, str) or not lifecycle:
                raise ValidationError("existing pattern state has no lifecycle_state")
            previous_lifecycle = lifecycle
        result_lifecycle = previous_lifecycle or "LEGACY_MIGRATED_UNCLASSIFIED"
        write_decision = "byte-no-op" if target == source else "replace"
        receipt_id = os.urandom(16).hex()
        stamp = _history_stamp(now)
        snapshot_name = f"{args.ticket}.UAT-CURRENT-ROUND.pre-switch.{stamp}.{receipt_id}.md"
        apply_name = f"{args.ticket}.switch-apply.{stamp}.{receipt_id}.receipt.json"
        receipt_path = tree.history_path / apply_name
        state_value = _state(pattern=pattern, template_sha=_sha256_bytes(template), ruleset_id=ruleset_id, receipt_path=receipt_path, lifecycle_state=result_lifecycle)
        receipt = {
            "schema_version": 1,
            "receipt_kind": "uat-round-switch-apply",
            "receipt_id": receipt_id,
            "command_mode": "switch-apply",
            "ticket": args.ticket,
            "applied_at": _utc_text(now),
            "selected_pattern": {"id": pattern["id"], "version": pattern["version"], "source": pattern["source"]},
            "preview_history_child": preview_name,
            "preview_receipt_sha256": _sha256_bytes(preview_raw),
            "snapshot_history_child": snapshot_name,
            "snapshot_sha256": source_sha,
            "source_sha256": source_sha,
            "target_sha256": target_sha,
            "rules_sha256": _sha256_bytes(rules_bytes),
            "pattern_record_sha256": _canonical_sha256(pattern),
            "template_sha256": _sha256_bytes(template),
            "mapping_decision": "complete",
            "mapping_envelope": envelope,
            "mapping_sha256": _canonical_sha256(envelope),
            "write_decision": write_decision,
            "previous_lifecycle_state": previous_lifecycle,
            "result_lifecycle_state": result_lifecycle,
        }

        # Snapshot first so a process interruption after live replacement remains recoverable.
        # The apply receipt is committed last, only after both mutable destinations succeed.
        _write_at(tree.history_fd, snapshot_name, source, exclusive=True)
        live_committed = False
        state_committed = False
        new_state = _json_bytes(state_value)
        try:
            if write_decision == "replace":
                _write_at(
                    tree.ticket_fd, tree.live_name, target, exclusive=False,
                    expected_sha256=source_sha,
                )
                live_committed = True
            _write_at(
                tree.ticket_fd, tree.state_name, new_state,
                exclusive=state_raw is None,
                expected_sha256=state_sha if state_raw is not None else None,
            )
            state_committed = True
            _write_at(tree.history_fd, apply_name, _json_bytes(receipt), exclusive=True)
        except BaseException as apply_error:
            rollback_errors: list[str] = []
            current_state = _read_at(tree.ticket_fd, tree.state_name, required=False)
            state_was_committed = state_committed or current_state == new_state
            if state_was_committed:
                try:
                    _restore_at(
                        tree.ticket_fd, tree.state_name, state_raw,
                        current_sha256=_sha256_bytes(new_state),
                    )
                except BaseException as exc:
                    rollback_errors.append(f"state rollback failed: {exc}")
            current_live = _read_at(tree.ticket_fd, tree.live_name, required=False)
            live_was_committed = live_committed or (
                write_decision == "replace" and current_live == target
            )
            if live_was_committed:
                try:
                    _restore_at(
                        tree.ticket_fd, tree.live_name, source,
                        current_sha256=target_sha,
                    )
                except BaseException as exc:
                    rollback_errors.append(f"live rollback failed: {exc}")
            if rollback_errors:
                raise ValidationError(
                    f"switch apply failed ({apply_error}); recovery snapshot retained as "
                    f"{snapshot_name}; {'; '.join(rollback_errors)}"
                ) from apply_error
            _unlink_at(tree.history_fd, apply_name)
            _unlink_at(tree.history_fd, snapshot_name)
            raise
        return receipt


def preflight_canonical_manifest(manifest: Any) -> dict[str, Any]:
    """Check an orchestrator-authored v2 manifest before prepare or cutover.

    Returns a report rather than raising, so a caller can show every problem at
    once. Each error names WHERE it is and HOW to fix it: a bare error code sends
    an author hunting, which is how malformed lineage reaches a round in the
    first place.
    """
    errors: list[dict[str, str]] = []

    def bad(where: str, message: str, fix: str) -> None:
        errors.append({"where": where, "message": message, "fix": fix})

    summary = {"journeys": 0, "scenarios": 0, "gwt_blocks": 0, "steps": 0}
    journeys = manifest.get("journeys") if isinstance(manifest, dict) else None
    if not isinstance(journeys, list) or not journeys:
        bad("manifest", "manifest.journeys must be a non-empty array",
            "Wrap the round as {\"journeys\": [...]} with at least one journey.")
        return {"ok": False, "errors": errors, "summary": summary}

    seen_steps: dict[str, str] = {}
    for journey in journeys:
        if not isinstance(journey, dict):
            bad("journeys[]", "each journey must be an object", "Replace the entry with a journey object.")
            continue
        jid = str(journey.get("journey_id") or "<missing journey_id>")
        summary["journeys"] += 1

        blocks_by_scenario: dict[str, set[str]] = {}
        for scenario in journey.get("scenarios") or []:
            if not isinstance(scenario, dict):
                bad(jid, "each scenario must be an object", "Replace the entry with a scenario object.")
                continue
            sid = str(scenario.get("scenario_id") or "<missing scenario_id>")
            summary["scenarios"] += 1
            refs: set[str] = set()
            for index, block in enumerate(scenario.get("gwt") or []):
                where = f"{jid} / {sid} / gwt[{index}]"
                if not isinstance(block, dict):
                    bad(where, "each GWT block must be an object", "Replace it with a gwt block object.")
                    continue
                summary["gwt_blocks"] += 1
                shape_ok = True
                for field in ("given", "when", "then"):
                    value = block.get(field)
                    if not isinstance(value, list) or not value or not all(isinstance(c, str) for c in value):
                        shape_ok = False
                        bad(where, f"gwt.{field} must be a non-empty array of strings",
                            f"Write {field} as an ordered array — even a single clause is [\"...\"]. "
                            "Given, when, and then may each have a different length.")
                if not shape_ok:
                    continue
                expected = gwt_content_sha256(block["given"], block["when"], block["then"])
                for field in ("gwt_ref", "sha256"):
                    if block.get(field) != expected:
                        bad(where, f"gwt.{field} does not match a sha256 digest of the exact clause arrays",
                            f"Recompute it: {field} must equal {expected}. Clause order and exact bytes "
                            "are digest-significant, so recompute after ANY wording change.")
                refs.add(expected)
            blocks_by_scenario[sid] = refs

        for step in journey.get("steps") or []:
            if not isinstance(step, dict):
                bad(jid, "each step must be an object", "Replace the entry with a step object.")
                continue
            summary["steps"] += 1
            step_id = str(step.get("step_id") or "<missing step_id>")
            where = f"{jid} / {step_id}"
            if step_id in seen_steps:
                bad(where, f"step_id is already used in {seen_steps[step_id]}",
                    "step_id must be unique across the whole round — feedback is keyed by it.")
            seen_steps[step_id] = jid
            for field in ("name", "instruction", "expected_outcome"):
                if not isinstance(step.get(field), str) or not step[field].strip():
                    bad(where, f"step.{field} is missing or empty",
                        "Owner-facing steps need a real name, what to do, and what should be visible. "
                        "An id is not a name.")
            if "gwt_refs" in step:
                bad(where, "step.gwt_refs is not part of the v2 shape",
                    "Move every reference inside its own {scenario_id, gwt_refs[]} entry in scenario_links, "
                    "so each reference resolves in the scenario that owns it.")
            links = step.get("scenario_links")
            if not isinstance(links, list) or not links:
                bad(where, "step.scenario_links must be a non-empty array",
                    "Link the step to at least one scenario: "
                    "[{\"scenario_id\": \"...\", \"gwt_refs\": [\"<digest>\"]}]")
                continue
            linked_ids: list[str] = []
            for link in links:
                if not isinstance(link, dict) or set(link) != {"scenario_id", "gwt_refs"}:
                    bad(where, "each scenario link must be exactly {scenario_id, gwt_refs}",
                        "Remove extra keys; lineage is only the scenario and its ordered references.")
                    continue
                sid = str(link["scenario_id"])
                linked_ids.append(sid)
                if sid not in blocks_by_scenario:
                    bad(where, f"scenario {sid} is not declared on journey {jid}",
                        f"Add {sid} to this journey's scenarios, or link a scenario the journey carries.")
                    continue
                refs = link.get("gwt_refs")
                if not isinstance(refs, list) or not refs:
                    bad(where, f"gwt_refs for {sid} must be a non-empty array",
                        "List the digests of the GWT blocks in that scenario this step actually exercises.")
                    continue
                if len(set(refs)) != len(refs):
                    bad(where, f"gwt_refs for {sid} contains duplicates", "Each reference may appear once.")
                for ref in refs:
                    if ref not in blocks_by_scenario[sid]:
                        owner = next((o for o, rs in blocks_by_scenario.items() if ref in rs), None)
                        bad(where,
                            f"gwt_ref {str(ref)[:12]}… does not belong to scenario {sid}",
                            (f"That block belongs to {owner}. Put the reference under {owner}'s own link entry."
                             if owner else
                             "No scenario in this journey declares that block. Recompute the digest, or add the block."))
            if len(set(linked_ids)) != len(linked_ids):
                bad(where, "the same scenario is linked more than once",
                    "Merge them into one entry whose gwt_refs lists every block this step exercises.")

    return {"ok": not errors, "errors": errors, "summary": summary}

def _add_materialize_args(parser: argparse.ArgumentParser, *, preview_receipt: bool = False) -> None:
    parser.add_argument("--ticket", required=True)
    parser.add_argument("--plans-root", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--patterns", type=Path, required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--semantic-verdict", help="Phase 3 consumption gate: the aggregate + per-probe verdict markdown")
    parser.add_argument("--scenario-id", help="Phase 3 consumption gate: scenario ID the semantic verdict must be bound to")
    if preview_receipt:
        parser.add_argument("--preview-receipt", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Materialization flags: --ticket --plans-root --rules --patterns --pattern; "
               "switch-apply also requires --preview-receipt.",
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate rule and pattern JSON without writing")
    validate_parser.add_argument("--rules", type=Path, required=True)
    validate_parser.add_argument("--patterns", type=Path, required=True)
    for operation in ("create", "absent-only-adopt", "switch-preview"):
        operation_parser = subparsers.add_parser(operation)
        _add_materialize_args(operation_parser)
    apply_parser = subparsers.add_parser("switch-apply")
    _add_materialize_args(apply_parser, preview_receipt=True)
    preflight_parser = subparsers.add_parser(
        "preflight-manifest",
        help="check an authored v2 canonical manifest before prepare or cutover",
    )
    preflight_parser.add_argument("--request", type=Path, required=True)
    return parser


def _run_preflight(path: Path) -> int:
    document = json.loads(path.read_text(encoding="utf-8"))
    manifest = document.get("canonical_manifest", document) if isinstance(document, dict) else document
    report = preflight_canonical_manifest(manifest)
    s = report["summary"]
    if report["ok"]:
        try:
            preflight_authored_manifest(cast(Mapping[str, object], manifest))
        except ValidationError as error:
            print("INVALID: 1 problem(s) in this manifest", file=sys.stderr)
            print(f"  [authoring] {error}", file=sys.stderr)
            return 1
        print(f"VALID: {s['journeys']} journeys, {s['steps']} steps, "
              f"{s['scenarios']} scenarios, {s['gwt_blocks']} GWT blocks")
        return 0
    print(f"INVALID: {len(report['errors'])} problem(s) in this manifest", file=sys.stderr)
    for error in report["errors"]:
        print(f"  [{error['where']}] {error['message']}", file=sys.stderr)
        print(f"      fix: {error['fix']}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.operation == "preflight-manifest":
        return _run_preflight(args.request)
    try:
        if args.operation == "validate":
            validate(args.rules, args.patterns)
            print("VALID: UAT rule and pattern contracts")
            return 0
        if args.operation == "create":
            result = _create_or_adopt(args, adopt=False)
        elif args.operation == "absent-only-adopt":
            result = _create_or_adopt(args, adopt=True)
        elif args.operation == "switch-preview":
            result = _switch_preview(args)
        else:
            result = _switch_apply(args)
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
