"""sealed /fix localization resolver; it never executes configured runners."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, override

SCRIPT_DIR: Final = Path(__file__).resolve().parent
PROJECT_ROOT: Final = Path.cwd().resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

if TYPE_CHECKING:
    from .fix_localization_types import JsonObject, is_object, is_strings, parse_document
else:
    from fix_localization_types import JsonObject, is_object, is_strings, parse_document


SCHEMA: Final = SCRIPT_DIR.parent / "schemas" / "fix-contracts.v1.schema.json"
LAYERS: Final = ("global_floor", "global_profiles", "project", "ticket", "cycle")
STEPS: Final = {"draft": "frozen", "frozen": "implementing", "implementing": "proof_pending", "proof_pending": "verified", "verified": "review_pending", "review_pending": "acceptance_pending", "acceptance_pending": "closed"}
FLOOR: Final = ("Diagnosis conviction before implementation", "Causal RED before production code", "Independently sourced expected values", "Connected real-seam proof for every non-local_logic declared risk", "Counterexample/revert/fault-seed proof", "Selected canary before expensive walk/UAT", "Final rerun of the effective proof set")
CLOSURE_MODES: Final = {"not_required", "test_uat", "owner_manual", "both"}


@dataclass(frozen=True, slots=True)
class Blocked(Exception):
    messages: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return "; ".join(self.messages)


@dataclass(frozen=True, slots=True)
class MembershipFacts:
    second_late_miss_same_class: bool
    failed_repair_same_class: bool
    trigger_six: bool
    issuance_authorizes_class_audit: bool
    conviction_authorized_by_current_issuance: bool
    shares_current_causal_seam: bool
    representable_as_bounded_revision: bool
    adjacent_risk_notation: bool
    deferred_notation: bool


@dataclass(frozen=True, slots=True)
class MembershipDecision:
    decision: str
    rule: int
    promotion_target: str | None = None


@dataclass(frozen=True, slots=True)
class DigestVerdict:
    status: str
    reason_code: str
    effective_sha256: str


@dataclass(frozen=True, slots=True)
class _DigestBlocked(Exception):
    reason_code: str


def _load(path: Path) -> JsonObject:
    text = path.read_text(encoding="utf-8")
    try: return parse_document(text)
    except ValueError as error: raise Blocked((f"blocked source: {path}: {error}",)) from error


def _validate(value: JsonObject, definition: str) -> None:
    errors: list[str] = []
    def check(mapping: JsonObject, required: set[str], allowed: set[str], label: str) -> None:
        errors.extend(f"blocked schema {label} required.{key}" for key in required - mapping.keys())
        errors.extend(f"blocked schema {label}.{key}" for key in mapping.keys() - allowed)
    match definition:
        case "overlay":
            check(value, {"schema", "project"}, {"schema", "project", "defaults", "risk_rules", "proof_profiles", "proof_gates"}, definition)
            profiles = value.get("proof_profiles", {}); gates = value.get("proof_gates", {})
            if not is_object(profiles) or not is_object(gates): errors.append("blocked schema profiles/gates")
            else:
                for name, profile in profiles.items():
                    if not is_object(profile): errors.append(f"blocked schema profile {name}"); continue
                    check(profile, {"outcome", "risk_classes", "seam", "expected_value_authority", "fixture_refs", "gate_refs", "counterexample_mode", "timeout_seconds"}, {"outcome", "risk_classes", "seam", "expected_value_authority", "fixture_refs", "gate_refs", "counterexample_mode", "canary_ref", "timeout_seconds"}, f"profile.{name}")
                for name, gate in gates.items():
                    if not name or not is_object(gate): errors.append(f"blocked schema gate {name}"); continue
                    check(gate, {"kind", "phase", "runner", "evidence_shape"}, {"kind", "phase", "runner", "evidence_shape"}, f"gate.{name}")
                    runner = gate.get("runner", {})
                    if not is_object(runner): errors.append(f"blocked schema runner {name}")
                    else:
                        check(runner, {"kind", "target"}, {"kind", "target", "selector"}, f"runner.{name}")
                        target = runner.get("target")
                        if runner.get("kind") not in {"pytest", "script", "playwright_manifest", "http_canary"} or not isinstance(target, str) or ".." in target: errors.append(f"blocked schema runner {name}")
        case "ticketPolicy":
            if value.get("schema") == "jswarm.fix-policy/v2":
                check(value, {"schema", "ticket", "revision", "status", "mode", "selected_risk_classes", "selected_proof_profiles", "closure_mode", "global_floor_assertion", "scenario_refs", "nfr_refs", "backlog_ref"}, {"schema", "ticket", "revision", "status", "mode", "selected_risk_classes", "selected_proof_profiles", "closure_mode", "global_floor_assertion", "scenario_refs", "nfr_refs", "backlog_ref", "envelope_ref", "cycle_refs"}, definition)
                if value.get("status") not in {"active", "superseded"}: errors.append("blocked schema status")
                if value.get("mode") not in {"quick", "standard"}: errors.append("blocked schema mode")
            else:
                check(value, {"schema", "ticket", "revision", "status", "selected_risk_classes", "selected_proof_profiles", "closure_mode", "global_floor_assertion"}, {"schema", "ticket", "revision", "status", "selected_risk_classes", "selected_proof_profiles", "closure_mode", "global_floor_assertion"}, definition)
        case "cycleContract": check(value, {"schema", "ticket", "cycle_id", "state", "provenance", "effective", "effective_sha256", "proofs", "closure_mode", "final_verification"}, {"schema", "ticket", "cycle_id", "state", "provenance", "effective", "effective_sha256", "boundary_map", "proofs", "closure_mode", "test_uat_refs", "final_verification", "alignment_receipt", "closure_evidence", "real_user_journey", "claims_uat_acceptance"}, definition)
        case _: errors.append("blocked schema definition")
    if definition in {"ticketPolicy", "cycleContract"}:
        closure_mode = value.get("closure_mode")
        if type(closure_mode) is not str or closure_mode not in CLOSURE_MODES:
            errors.append("blocked schema closure_mode")
    if errors: raise Blocked(tuple(errors))


def _digest(value: JsonObject) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def classify_cycle_membership(facts: MembershipFacts) -> MembershipDecision:
    recurrence = facts.second_late_miss_same_class or facts.failed_repair_same_class or facts.trigger_six
    if recurrence and not facts.issuance_authorizes_class_audit:
        return MembershipDecision("promote", 1)
    if recurrence:
        return MembershipDecision("execute", 2)
    if facts.conviction_authorized_by_current_issuance:
        return MembershipDecision("execute", 3)
    if facts.shares_current_causal_seam and facts.representable_as_bounded_revision:
        return MembershipDecision("revise", 4)
    return MembershipDecision("open", 5)


def _ticket_path(project_root: Path, relative: str, *, envelope: bool = False) -> Path:
    path = Path(relative)
    parts = path.parts
    if path.is_absolute() or ".." in parts or len(parts) < 4 or parts[:2] != (".jswarm", "plans"):
        raise _DigestBlocked("path_escape")
    ticket = parts[2]
    if not ticket or not path.name.startswith(f"{ticket}."):
        raise _DigestBlocked("path_escape")
    if envelope and not path.name.startswith(f"{ticket}.fix-envelope."):
        raise _DigestBlocked("path_escape")
    root = project_root.resolve()
    candidate = root / path
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise _DigestBlocked("path_escape") from error
    if root != parent and root not in parent.parents or candidate.is_symlink():
        raise _DigestBlocked("path_escape")
    return candidate


def _ticket_document(project_root: Path, relative: str, *, envelope: bool = False) -> JsonObject:
    candidate = _ticket_path(project_root, relative, envelope=envelope)
    try:
        descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            return json.loads(handle.read())
    except (OSError, ValueError) as error:
        raise _DigestBlocked("parent_missing") from error


def _authority_body(document: JsonObject) -> JsonObject:
    body = document.get("body")
    if is_object(body):
        return body
    # Standard-cycle authorities expose their effective contract under `effective`.
    # The shared verifier therefore validates cycle refs without a second digest law.
    effective = document.get("effective")
    if document.get("schema") == "jswarm.fix-cycle/v1" and is_object(effective):
        return effective
    raise _DigestBlocked("stale_effective_digest")


def _contract_chain(project_root: Path, relative: str, seen: set[str] | None = None) -> list[JsonObject]:
    visited = set() if seen is None else seen
    if relative in visited:
        raise _DigestBlocked("parent_cycle")
    visited.add(relative)
    document = _ticket_document(project_root, relative)
    _ = _authority_body(document)
    parent_ref = document.get("parent_ref")
    chain: list[JsonObject] = []
    if isinstance(parent_ref, str):
        try:
            chain = _contract_chain(project_root, parent_ref, visited)
        except _DigestBlocked as error:
            if error.reason_code == "stale_effective_digest":
                raise _DigestBlocked("parent_bytes_changed") from error
            raise
        parent_digest = hashlib.sha256(_canonical_bytes(chain)).hexdigest()
        if document.get("parent_effective_sha256") != parent_digest:
            raise _DigestBlocked("parent_digest_mismatch")
    chain.append(document)
    actual = hashlib.sha256(_canonical_bytes(chain)).hexdigest()
    if document.get("effective_sha256") != actual:
        raise _DigestBlocked("stale_effective_digest")
    return chain


def _canonical_bytes(chain: list[JsonObject]) -> bytes:
    return "\n".join(json.dumps(_authority_body(member), sort_keys=True, separators=(",", ":")) for member in chain).encode("utf-8")


def compose_effective_bytes(*, manifest_path: str, project_root: Path) -> bytes:
    return _canonical_bytes(_contract_chain(project_root, manifest_path))


def verify_effective_digest(*, manifest_path: str, cited_sha256: str, project_root: Path) -> DigestVerdict:
    try:
        effective = compose_effective_bytes(manifest_path=manifest_path, project_root=project_root)
    except _DigestBlocked as error:
        return DigestVerdict("blocked", error.reason_code, "")
    digest = hashlib.sha256(effective).hexdigest()
    if digest != cited_sha256:
        return DigestVerdict("blocked", "stale_effective_digest", digest)
    return DigestVerdict("verified", "ok", digest)


def _quick_verdict(ticket: str, authority_path: str, authority_sha256: str, bypass_class: str, decision: str, reason_code: str) -> JsonObject:
    return {"schema": "jswarm.fix-quick-authority/v1", "decision": decision, "reason_code": reason_code, "ticket": ticket, "authority_path": authority_path, "authority_sha256": authority_sha256, "bypass_class": bypass_class}


def _quick_authority(project_root: Path, policy_path: str, envelope_path: str, cited_sha256: str, bypass_class: str) -> tuple[JsonObject, bool]:
    ticket = Path(envelope_path).parts[2] if len(Path(envelope_path).parts) > 2 else ""
    try:
        envelope = _ticket_document(project_root, envelope_path, envelope=True)
    except _DigestBlocked:
        return _quick_verdict(ticket, envelope_path, cited_sha256, bypass_class, "deny", "authority_not_ticket_scoped"), False
    try:
        policy = _ticket_document(project_root, policy_path)
        _validate(policy, "ticketPolicy")
    except (_DigestBlocked, Blocked):
        return _quick_verdict(ticket, envelope_path, cited_sha256, bypass_class, "deny", "not_enrolled"), False
    body = envelope.get("body")
    effective = _digest(body) if is_object(body) else ""
    ref = policy.get("envelope_ref")
    if not is_object(ref) or ref.get("path") != envelope_path or ref.get("sha256") != cited_sha256 or effective != cited_sha256 or envelope.get("effective_sha256") != effective:
        return _quick_verdict(ticket, envelope_path, cited_sha256, bypass_class, "deny", "digest_mismatch"), False
    allowed = body.get("allowed_classes") if is_object(body) else []
    if not is_strings(allowed) or bypass_class not in allowed:
        return _quick_verdict(ticket, envelope_path, cited_sha256, bypass_class, "deny", "class_not_allowed"), False
    return _quick_verdict(ticket, envelope_path, effective, bypass_class, "allow", "ok"), True


def _project_policy(ticket: Path) -> JsonObject:
    policy = _load(ticket)
    _validate(policy, "ticketPolicy")
    enrolled = policy.get("schema") == "jswarm.fix-policy/v2" and policy.get("status") == "active"
    return {"schema": "jswarm.fix-projection/v1", "enrolled": enrolled, "ticket": policy.get("ticket"), "mode": policy.get("mode", "standard"), "closure_mode": policy.get("closure_mode"), "scenario_refs": policy.get("scenario_refs", []), "nfr_refs": policy.get("nfr_refs", []), "envelope_ref": policy.get("envelope_ref", {}), "cycle_refs": policy.get("cycle_refs", []), "backlog_ref": policy.get("backlog_ref", "")}


def _write_policy(project_root: Path, policy_relative: str, document_path: Path) -> tuple[JsonObject, bool]:
    """Write a validated policy document; this CLI is the mechanism invoked only by `/fix`."""
    result: JsonObject = {"schema": "jswarm.fix-policy-write/v1"}
    if os.environ.get("JSWARM_FIX_POLICY_WRITER_OFF") == "1":
        return {**result, "status": "refused", "reason_code": "policy_writer_disabled"}, False
    policy = _load(document_path)
    _validate(policy, "ticketPolicy")
    target = _ticket_path(project_root, policy_relative)
    if policy.get("ticket") != Path(policy_relative).parts[2]:
        raise Blocked(("blocked policy ticket mismatch",))
    target.write_bytes(document_path.read_bytes())
    return {**result, "status": "written", "reason_code": "ok"}, True


def _contained(base: Path, candidate: Path) -> Path:
    resolved_base = base.resolve()
    relative = candidate.relative_to(resolved_base) if candidate.is_absolute() and candidate.is_relative_to(resolved_base) else candidate
    if relative.is_absolute() or ".." in relative.parts:
        raise Blocked(("blocked path must be repository-contained",))
    if len(relative.parts) < 4 or relative.parts[:2] != (".jswarm", "plans") or ".fix-cycle." not in relative.name:
        raise Blocked(("blocked path must be an exact ticket-local fix-cycle path",))
    parent = (resolved_base / relative.parent).resolve(strict=True)
    if resolved_base != parent and resolved_base not in parent.parents:
        raise Blocked(("blocked path escape",))
    return parent / relative.name


def _layer(name: str, path: Path | None, status: str, warnings: list[str]) -> JsonObject:
    row: JsonObject = {"layer": name, "status": status, "warnings": list(warnings)}
    if path is not None and path.is_file():
        row["path"] = str(path)
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return row


def _overlay(path: Path | None, optional: bool) -> tuple[JsonObject, str, list[str]]:
    if path is None or not path.is_file():
        return {}, "absent", ["optional overlay unavailable; immutable floor applied"]
    data: JsonObject = {}
    try:
        data = _load(path)
        _validate(data, "overlay")
    except Blocked as error:
        defaults = data.get("defaults", {})
        profiles = data.get("proof_profiles", {})
        selected_raw = defaults.get("select_proof_profiles", []) if is_object(defaults) else []
        selected = selected_raw if is_strings(selected_raw) else []
        selectors = tuple(f"blocked unknown-profile: {profile}" for profile in selected if not is_object(profiles) or profile not in profiles)
        containment = ("blocked runner target must be contained",) if ".." in path.read_text(encoding="utf-8") else ()
        error = Blocked(error.messages + selectors + containment)
        if optional:
            return {}, "rejected", list(error.messages)
        raise error
    return data, "applied", []


def _receipt(project: Path | None, ticket: Path | None, cycle: Path | None, explicit: Path | None) -> JsonObject:
    overlay_path = explicit
    warnings: list[str] = []
    if project is not None and explicit is None:
        overlay_path = project / ".claude" / "fix.overlay.yaml"
    overlay, overlay_status, overlay_warnings = _overlay(overlay_path, explicit is None)
    warnings.extend(overlay_warnings)
    overlay_reject_reasons = overlay_warnings if overlay_status == "rejected" else []
    ticket_data = _load(ticket) if ticket is not None else {}
    cycle_data = _load(cycle) if cycle is not None else {}
    if ticket_data:
        _validate(ticket_data, "ticketPolicy")
    if cycle_data:
        _validate(cycle_data, "cycleContract")
    closure_mode = cycle_data.get("closure_mode", ticket_data.get("closure_mode", "not_required"))
    defaults = overlay.get("defaults", {})
    selected_raw = defaults.get("select_proof_profiles", []) if is_object(defaults) else []
    selected = selected_raw if is_strings(selected_raw) else []
    profiles = overlay.get("proof_profiles", {})
    if not is_strings(selected) or not is_object(profiles) or any(item not in profiles for item in selected):
        raise Blocked(("blocked unknown selected proof profile", *overlay_reject_reasons))
    risks_raw = defaults.get("select_risk_classes", []) if is_object(defaults) else []
    risks = risks_raw if is_strings(risks_raw) else []
    ticket_profiles = ticket_data.get("selected_proof_profiles")
    ticket_risks = ticket_data.get("selected_risk_classes")
    if is_strings(ticket_profiles):
        selected = list(dict.fromkeys([*selected, *ticket_profiles]))
    if is_strings(ticket_risks) and is_strings(risks):
        risks = list(dict.fromkeys([*risks, *ticket_risks]))
    cycle_effective = cycle_data.get("effective")
    if is_object(cycle_effective):
        cycle_profiles = cycle_effective.get("proof_profiles")
        cycle_risks = cycle_effective.get("risk_classes")
        if is_strings(cycle_profiles):
            selected = list(dict.fromkeys([*selected, *cycle_profiles]))
        if is_strings(cycle_risks) and is_strings(risks):
            risks = list(dict.fromkeys([*risks, *cycle_risks]))
    if not is_strings(selected) or any(item not in profiles for item in selected):
        raise Blocked(("blocked unknown selected proof profile", *overlay_reject_reasons))
    readings_raw = defaults.get("add_required_reading", []) if is_object(defaults) else []
    readings = readings_raw if is_strings(readings_raw) else []
    effective: JsonObject = {"floor_assertions": list(FLOOR), "risk_classes": risks, "proof_profiles": selected, "proof_gates": overlay.get("proof_gates", {}), "required_reading": readings, "activation": "jTestEngineer" if any(risk != "local_logic" for risk in risks) else "lean_local_logic"}
    provenance = [_layer("global_floor", None, "applied", []), _layer("global_profiles", None, "applied", []), _layer("project", overlay_path, overlay_status, overlay_warnings), _layer("ticket", ticket, "applied" if ticket else "absent", []), _layer("cycle", cycle, "applied" if cycle else "absent", [])]
    policy_schema = ticket_data.get("schema", "jswarm.fix-policy/v1")
    policy_status = ticket_data.get("status", "active")
    enrolled = policy_schema == "jswarm.fix-policy/v2" and policy_status == "active"
    enrollment_reason = "ok" if enrolled else ("policy_superseded" if policy_schema == "jswarm.fix-policy/v2" else "policy_version_unenrolled")
    receipt: JsonObject = {"schema": "jswarm.fix-resolution/v1", "status": "resolved", "precedence": list(LAYERS), "overlay": {"status": overlay_status}, "provenance": provenance, "warnings": warnings, "closure_mode": closure_mode, "effective": effective, "enrollment": {"enrolled": enrolled, "policy_schema": policy_schema, "status": policy_status, "mode": ticket_data.get("mode", "standard"), "reason_code": enrollment_reason}}
    receipt["effective_sha256"] = _digest(effective)
    return receipt


def _freeze(output: Path, receipt_path: Path) -> JsonObject:
    target = _contained(PROJECT_ROOT, output)
    if target.parent.is_symlink() or target.is_symlink():
        raise Blocked(("blocked symlink freeze target",))
    receipt = _load(receipt_path)
    if receipt.get("schema") != "jswarm.fix-resolution/v1" or receipt.get("status") != "resolved":
        raise Blocked(("blocked unresolved effective receipt",))
    cycle: JsonObject = {"schema": "jswarm.fix-cycle/v1", "ticket": target.parent.name, "cycle_id": target.stem, "state": "frozen", "provenance": receipt.get("provenance"), "effective": receipt.get("effective"), "effective_sha256": receipt.get("effective_sha256"), "proofs": [], "closure_mode": receipt.get("closure_mode", "not_required"), "final_verification": {"status": "pending"}}
    _validate(cycle, "cycleContract")
    payload = json.dumps(cycle, sort_keys=True) + "\n"
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as error:
        raise Blocked(("create-only freeze blocked",)) from error
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        _ = handle.write(payload)
    return cycle


def _transition(previous: str, following: str, cycle: Path | None) -> JsonObject:
    if STEPS.get(previous) != following:
        raise Blocked(("cycle state must move to its legal adjacent forward state",))
    if following == "closed":
        if cycle is None:
            raise Blocked(("blocked closure requires cycle evidence",))
        contract = _load(cycle)
        _validate(contract, "cycleContract")
        final = contract.get("final_verification")
        proofs = contract.get("proofs")
        effective = contract.get("effective")
        risks = effective.get("risk_classes") if is_object(effective) else []
        if not is_object(final) or final.get("status") != "passed":
            raise Blocked(("blocked final verification proof",))
        if not isinstance(proofs, list) or not proofs or any(not is_object(proof) or proof.get("status") != "passed" for proof in proofs):
            raise Blocked(("blocked required proof gates",))
        if is_strings(risks) and any(risk != "local_logic" for risk in risks) and not contract.get("boundary_map"):
            raise Blocked(("blocked cross-boundary map",))
        if contract.get("closure_mode") == "test_uat" and not contract.get("test_uat_refs"):
            raise Blocked(("blocked UAT acceptance evidence",))
        # Legacy cycles predate the typed Phase 2 receipt fields. Once either
        # field exists, alignment is mandatory for closure.
        if "alignment_receipt" in contract or "closure_evidence" in contract:
            alignment = contract.get("alignment_receipt")
            if not is_object(alignment) or alignment.get("schema") != "jswarm.fix-alignment-receipt/v1":
                raise Blocked(("alignment_receipt_missing",))
            if alignment.get("kind") == "no_delta" and not alignment.get("reason"):
                raise Blocked(("alignment_reason_missing",))
    return {"schema": "jswarm.fix-cycle/v1", "status": "validated", "from_state": previous, "to_state": following}


def _sources(tail: list[str]) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    if len(tail) == 1 and tail[0].endswith((".yaml", ".yml")):
        return None, None, None, Path(tail[0])
    if len(tail) % 2:
        raise Blocked(("blocked layer arguments",))
    values = dict(zip(tail[::2], tail[1::2], strict=True))
    if set(values) - {"--project", "--ticket", "--cycle", "--overlay"}:
        raise Blocked(("blocked unknown layer argument",))
    return (Path(values["--project"]) if "--project" in values else None, Path(values["--ticket"]) if "--ticket" in values else None, Path(values["--cycle"]) if "--cycle" in values else None, Path(values["--overlay"]) if "--overlay" in values else None)



def _phase2_path(project_root: Path, relative: str) -> Path:
    try:
        return _ticket_path(project_root, relative)
    except _DigestBlocked as error:
        raise Blocked((error.reason_code,)) from error


def _receipt_kinds(contract: JsonObject) -> set[str]:
    evidence = contract.get("closure_evidence")
    if not isinstance(evidence, list):
        return set()
    return {str(item.get("kind")) for item in evidence if is_object(item) and item.get("schema") == "jswarm.fix-closure-receipt/v1"}


def _alignment_error(contract: JsonObject) -> str | None:
    alignment = contract.get("alignment_receipt")
    if not is_object(alignment) or alignment.get("schema") != "jswarm.fix-alignment-receipt/v1":
        return "alignment_receipt_missing"
    if alignment.get("kind") == "no_delta" and not alignment.get("reason"):
        return "alignment_reason_missing"
    return None


def _closure_requirements(contract: JsonObject) -> set[str]:
    mode = contract.get("closure_mode")
    state = contract.get("state")
    if state == "blocked": return {"blocked_disposition", "alignment"}
    if state == "closed": return {"wrapper_canary", "alignment", "final_proof"}
    required = {"alignment"}
    if mode in {"not_required", "test_uat"}: required.add("wrapper_canary" if mode == "not_required" else "test_uat_round")
    if mode in {"owner_manual", "both"}: required.add("owner_manual_acceptance")
    if mode == "both": required.add("test_uat_round")
    if contract.get("real_user_journey"):
        required.update({"test_uat_round", "wrapper_canary"})
    return required


def _closure_verdict(project_root: Path, cycle_ref: str) -> tuple[JsonObject, bool]:
    contract = _ticket_document(project_root, cycle_ref)
    _validate(contract, "cycleContract")
    required = _closure_requirements(contract)
    claims = bool(contract.get("claims_uat_acceptance"))
    mode = str(contract.get("closure_mode"))
    state = str(contract.get("state"))
    reason = _alignment_error(contract)
    if reason is None and mode == "owner_manual" and claims:
        reason = "contradictory_uat_claim"
    kinds = _receipt_kinds(contract)
    missing = sorted(required - kinds)
    if reason is None and missing:
        order = {"wrapper_canary": "missing_wrapper_evidence", "alignment": "missing_alignment_receipt", "test_uat_round": "missing_uat_round_receipt", "owner_manual_acceptance": "missing_owner_manual_receipt", "final_proof": "missing_final_proof", "blocked_disposition": "missing_blocked_disposition"}
        reason = order[missing[0]]
    accepted = reason is None
    return {"schema": "jswarm.fix-closure/v1", "decision": "accept" if accepted else "reject", "reason_code": "ok" if accepted else reason, "effective_mode": mode, "required_receipts": sorted(required), "missing_receipts": [] if accepted else missing, "claims_uat_acceptance": False if mode == "owner_manual" and not contract.get("real_user_journey") else claims, "reported_as_accepted": state == "closed" and accepted}, accepted


def _quick_receipt(project_root: Path, envelope_ref: str, receipt_ref: str) -> tuple[JsonObject, bool]:
    try:
        envelope = _ticket_document(project_root, envelope_ref, envelope=True)
        receipt = _ticket_document(project_root, receipt_ref)
    except _DigestBlocked as error:
        reason = "path_escape" if error.reason_code == "path_escape" else "envelope_digest_mismatch"
        return {"schema": "jswarm.fix-quick-receipt-verdict/v1", "decision": "flagged", "reason_code": reason, "disposition": "escalate_standard", "violations": [reason]}, False
    body = envelope.get("body")
    if not is_object(body) or receipt.get("schema") != "jswarm.fix-quick-receipt/v1":
        raise Blocked(("invalid receipt",))
    ref = receipt.get("envelope_ref")
    digest = _digest(body)
    reason = "ok"; violations: list[str] = []
    if not is_object(ref) or ref.get("path") != envelope_ref or ref.get("sha256") != digest or envelope.get("effective_sha256") != digest:
        reason = "envelope_digest_mismatch"
    elif receipt.get("class") not in body.get("allowed_classes", []): reason = "class_not_allowed"
    elif isinstance(body.get("caps"), dict) and receipt.get("changed_files", 0) > body["caps"].get("files", 0): reason = "cap_files_exceeded"
    elif isinstance(body.get("caps"), dict) and receipt.get("changed_lines", 0) > body["caps"].get("lines", 0): reason = "cap_lines_exceeded"
    else:
        forbidden = body.get("forbidden_surfaces", [])
        for touched in receipt.get("touched_paths", []):
            if not isinstance(touched, str) or touched.startswith("/") or ".." in Path(touched).parts or any(touched == item or touched.startswith(str(item) + "/") for item in forbidden):
                reason = "path_not_in_authorized_class"; violations.append(str(touched)); break
    allowed = reason == "ok"
    return {"schema": "jswarm.fix-quick-receipt-verdict/v1", "decision": "pass" if allowed else "flagged", "reason_code": reason, "disposition": "quick_closed" if allowed else "escalate_standard", "violations": violations}, allowed


def _close_gate(project_root: Path, policy_ref: str) -> tuple[JsonObject, bool]:
    try:
        policy = _ticket_document(project_root, policy_ref); _validate(policy, "ticketPolicy")
    except (_DigestBlocked, Blocked):
        return {"schema": "jswarm.fix-close-gate/v1", "decision": "allow", "reason_code": "not_enrolled", "blocking_cycles": []}, True
    if policy.get("schema") != "jswarm.fix-policy/v2" or policy.get("status") != "active":
        return {"schema": "jswarm.fix-close-gate/v1", "decision": "allow", "reason_code": "not_enrolled", "blocking_cycles": []}, True
    blocking: list[str] = []
    for cycle_ref in policy.get("cycle_refs", []):
        try:
            verdict, allowed = _closure_verdict(project_root, str(cycle_ref))
            if not allowed or verdict["reported_as_accepted"] is False: blocking.append(str(cycle_ref))
        except (Blocked, _DigestBlocked): blocking.append(str(cycle_ref))
    if blocking: return {"schema": "jswarm.fix-close-gate/v1", "decision": "block", "reason_code": "alignment_receipt_missing", "blocking_cycles": blocking}, False
    return {"schema": "jswarm.fix-close-gate/v1", "decision": "allow", "reason_code": "ok", "blocking_cycles": []}, True


@dataclass(frozen=True, kw_only=True, slots=True)
class IntakeFacts:
    conviction_class: str | None = None
    prior_late_misses: tuple[str, ...] = ()
    failed_repairs: tuple[str, ...] = ()
    declared_surfaces: tuple[str, ...] = ()
    declared_triggers: tuple[int, ...] = ()
    envelope_status: str | None = None
    envelope_allowed_classes: tuple[str, ...] = ()
    envelope_allowed_surfaces: tuple[str, ...] = ()
    envelope_cap_files: int | None = None
    touched_file_count: int | None = None
    unknown_material_facts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IntakeVerdict:
    verdict: str
    trigger: int | None
    reason_code: str


def classify_intake(facts: IntakeFacts) -> IntakeVerdict:
    if facts.unknown_material_facts:
        return IntakeVerdict("trigger_unknown", None, "unknown_material_facts")
    if facts.conviction_class is None:
        return IntakeVerdict("trigger_unknown", None, "no_declared_facts")
    if facts.conviction_class in facts.prior_late_misses:
        return IntakeVerdict("trigger_hit", 6, "prior_late_miss_same_class")
    if facts.conviction_class in facts.failed_repairs:
        return IntakeVerdict("trigger_hit", 6, "failed_repair_same_class")
    if facts.declared_triggers:
        return IntakeVerdict("trigger_hit", min(facts.declared_triggers), "declared_trigger")
    if facts.envelope_status is not None and facts.envelope_status != "active":
        return IntakeVerdict("trigger_unknown", None, "envelope_not_active")
    if facts.envelope_status == "active":
        if facts.conviction_class not in facts.envelope_allowed_classes:
            return IntakeVerdict("trigger_unknown", None, "class_not_in_envelope")
        if any(surface not in facts.envelope_allowed_surfaces for surface in facts.declared_surfaces):
            return IntakeVerdict("trigger_unknown", None, "surface_not_allowed")
        if facts.touched_file_count is None or facts.envelope_cap_files is None or facts.touched_file_count > facts.envelope_cap_files:
            return IntakeVerdict("trigger_unknown", None, "cap_exceeded")
        return IntakeVerdict("envelope_candidate", None, "envelope_class_within_caps")
    return IntakeVerdict("trigger_unknown", None, "no_declared_facts")


def _intake_facts_document(project_root: Path, relative: str) -> IntakeFacts:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts or len(relative_path.parts) < 4 or relative_path.parts[:2] != (".jswarm", "plans"):
        raise ValueError("invalid facts document")
    root = project_root.resolve()
    candidate = root / relative_path
    if candidate.is_symlink() or root not in candidate.parent.resolve().parents and candidate.parent.resolve() != root:
        raise ValueError("invalid facts document")
    raw = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != "jswarm.fix-intake-facts/v1":
        raise ValueError("invalid facts document")
    fields = {
        "conviction_class", "prior_late_misses", "failed_repairs", "declared_surfaces",
        "declared_triggers", "envelope_status", "envelope_allowed_classes",
        "envelope_allowed_surfaces", "envelope_cap_files", "touched_file_count",
        "unknown_material_facts",
    }
    values = {key: raw.get(key) for key in fields}
    for key in ("prior_late_misses", "failed_repairs", "declared_surfaces", "envelope_allowed_classes", "envelope_allowed_surfaces", "unknown_material_facts"):
        if not isinstance(values[key], list) or not all(isinstance(item, str) for item in values[key]):
            raise ValueError("invalid facts document")
    if not isinstance(values["declared_triggers"], list) or not all(type(item) is int and 1 <= item <= 6 for item in values["declared_triggers"]):
        raise ValueError("invalid facts document")
    if values["conviction_class"] is not None and not isinstance(values["conviction_class"], str):
        raise ValueError("invalid facts document")
    if values["envelope_status"] not in {None, "active", "superseded", "expired"}:
        raise ValueError("invalid facts document")
    for key in ("envelope_cap_files", "touched_file_count"):
        if values[key] is not None and (type(values[key]) is not int or values[key] < 0):
            raise ValueError("invalid facts document")
    return IntakeFacts(
        conviction_class=values["conviction_class"],
        prior_late_misses=tuple(values["prior_late_misses"]), failed_repairs=tuple(values["failed_repairs"]),
        declared_surfaces=tuple(values["declared_surfaces"]), declared_triggers=tuple(values["declared_triggers"]),
        envelope_status=values["envelope_status"], envelope_allowed_classes=tuple(values["envelope_allowed_classes"]),
        envelope_allowed_surfaces=tuple(values["envelope_allowed_surfaces"]), envelope_cap_files=values["envelope_cap_files"],
        touched_file_count=values["touched_file_count"], unknown_material_facts=tuple(values["unknown_material_facts"]),
    )

def main(arguments: list[str]) -> int:
    if not arguments:
        print("blocked: blocked invalid command arguments", file=sys.stderr)
        return 2
    command, *tail = arguments
    if command == "quick-authority" and tail == ["--help"]:
        print("usage: fix_localization.py quick-authority --project-root <dir> --policy <rel> --envelope <rel> --sha256 <hex> --bypass-class <class>")
        return 0
    try:
        if command == "classify-intake":
            if len(tail) != 4 or tail[::2] != ["--project-root", "--facts"]:
                raise Blocked(("blocked invalid command arguments",))
            verdict = classify_intake(_intake_facts_document(Path(tail[1]), tail[3]))
            result = {"schema": "jswarm.fix-intake-verdict/v1", "verdict": verdict.verdict, "trigger": verdict.trigger, "reason_code": verdict.reason_code}
            print(json.dumps(result, sort_keys=True))
            if verdict.verdict == "trigger_unknown":
                print(f"unknown {verdict.reason_code}", file=sys.stderr)
                return 4
            return 0
        if command == "quick-receipt" and len(tail) == 6 and tail[::2] == ["--project-root", "--envelope", "--receipt"]:
            result, allowed = _quick_receipt(Path(tail[1]), tail[3], tail[5])
            print(json.dumps(result, sort_keys=True))
            if not allowed: print(f"deny {result['reason_code']}", file=sys.stderr); return 3
            return 0
        if command == "closure-mode" and len(tail) == 4 and tail[:1] == ["--project-root"] and tail[2:3] == ["--cycle"]:
            result, allowed = _closure_verdict(Path(tail[1]), tail[3])
            print(json.dumps(result, sort_keys=True))
            if not allowed: print(f"deny {result['reason_code']}", file=sys.stderr); return 3
            return 0
        if command == "close-gate" and len(tail) == 4 and tail[:1] == ["--project-root"] and tail[2:3] == ["--policy"]:
            result, allowed = _close_gate(Path(tail[1]), tail[3])
            print(json.dumps(result, sort_keys=True))
            if not allowed: print(f"deny {result['reason_code']}", file=sys.stderr); return 3
            return 0
        if command == "write-policy":
            if len(tail) != 6:
                raise Blocked(("blocked invalid command arguments",))
            values = dict(zip(tail[::2], tail[1::2], strict=True))
            if set(values) != {"--project-root", "--policy", "--document"}:
                raise Blocked(("blocked invalid command arguments",))
            result, written = _write_policy(Path(values["--project-root"]), values["--policy"], Path(values["--document"]))
            print(json.dumps(result, sort_keys=True))
            if not written:
                print(f"deny {result['reason_code']}", file=sys.stderr)
                return 3
            return 0
        if command == "quick-authority":
            if len(tail) != 10:
                raise Blocked(("blocked invalid command arguments",))
            values = dict(zip(tail[::2], tail[1::2], strict=True))
            if set(values) != {"--project-root", "--policy", "--envelope", "--sha256", "--bypass-class"}:
                raise Blocked(("blocked invalid command arguments",))
            result, allowed = _quick_authority(Path(values["--project-root"]), values["--policy"], values["--envelope"], values["--sha256"], values["--bypass-class"])
            print(json.dumps(result, sort_keys=True))
            if not allowed:
                print(f"deny {result['reason_code']}", file=sys.stderr)
                return 3
            return 0
        match command:
            case "inspect" if len(tail) == 1:
                result = _receipt(Path(tail[0]), None, None, None)
            case "resolve" | "validate-policy":
                project, ticket, cycle, explicit = _sources(tail)
                result = _receipt(project, ticket, cycle, explicit)
                if command == "validate-policy": result["status"] = "validated"
            case "project-policy" if len(tail) == 2 and tail[0] == "--ticket":
                result = _project_policy(Path(tail[1]))
            case "freeze" if len(tail) == 4 and tail[0] == "--output" and tail[2] == "--receipt":
                result = _freeze(Path(tail[1]), Path(tail[3]))
            case "freeze" if len(tail) == 2 and tail[0] == "--output":
                _ = _contained(PROJECT_ROOT, Path(tail[1]))
                raise Blocked(("blocked freeze receipt required",))
            case "validate-cycle" if len(tail) in {4, 6} and tail[0] == "--from-state" and tail[2] == "--to-state":
                result = _transition(tail[1], tail[3], Path(tail[5]) if len(tail) == 6 and tail[4] == "--cycle" else None)
            case _:
                raise Blocked(("blocked invalid command arguments",))
    except (Blocked, OSError, json.JSONDecodeError) as error:
        print(f"blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
