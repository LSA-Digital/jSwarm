from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Protocol, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jswarm.uat_feedback import OwnerWalkGenerationError, ValidationError as LedgerValidationError, render_feedback_document, render_owner_walk_document, validate_ledger
from jswarm.uat_round_materialize import (
    PrewalkGateReceipt,
    StackGateReceipt,
    WalkScriptGateReceipt,
    _package_from_region,
    _package_region_bounds,
    build_canonical_package,
    build_round_render_mapping,
    validate_normalized_package,
)


class _ChainResult(Protocol):
    gaps: tuple[str, ...]
    journey_count: int
    verified_gwt_sha256_by_journey: Mapping[str, str]
    owner_walk: Mapping[str, object] | None
    owner_walk_token: str | None


class _ChainVerifier(Protocol):
    def verify_chain(self, *, scenarios_path: Path, schema_path: Path, ticket: str, project_root: Path, walk_script_path: Path | None = ...) -> _ChainResult: ...


_query_spec = importlib.util.spec_from_file_location("query_uat_scenarios", Path(__file__).with_name("uat-scenarios") / "query_uat_scenarios.py")
assert _query_spec is not None and _query_spec.loader is not None
_query_module = importlib.util.module_from_spec(_query_spec)
assert isinstance(_query_module, ModuleType)
sys.modules[_query_spec.name] = _query_module
_query_spec.loader.exec_module(_query_module)
verify_chain = cast(_ChainVerifier, cast(Any, _query_module)).verify_chain

PrepState = Literal["PENDING", "PARTIAL", "FAILED", "BLOCKED", "UNVERIFIED", "QA_VERIFIED", "ISSUED"]
PackageState = Literal["DRAFT_SEALED", "QA_VERIFIED", "ISSUED"]
LayerAvailabilityToken = Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNVERIFIED"]
_SEALED_PACKAGE_LAYER = "sealed-package"
_PROJECT_SUPPORTED_LAYER = "project-supported"
_LAYER_STDERR_PREFIX = "prepare.layer-availability"
_GWT_DEPENDENCY_TICKET = "COM-346-JINFRA-BOSS"
_GWT_CORPUS_GAP_TOKENS = frozenset({
    "SCENARIO-GWT-MISSING", "SCENARIO-GWT-SHA256-MISSING", "SCENARIO-GWT-SHA256-INVALID", "SCENARIO-GWT-SHA256-MISMATCH",
})
_REPORT_KEYS = frozenset({
    "schema_version", "ticket", "handoff_path", "handoff_sha256", "current_round_path", "current_round_sha256",
    "round_id", "package_id", "package_hash", "sealed_payload_sha256", "script_id", "script_hash",
    "certified_build_hash", "started_at", "finished_at", "executed_steps", "atom_results", "skipped_steps",
    "substituted_steps", "reordered_steps", "known_check_receipt", "recovery_use", "recovery_evidence_paths",
    "observer_status", "observer_evidence_paths", "canary_path", "canary_sha256", "evidence_paths",
    "tool_exit_code", "tool_verdict_line", "overall_verdict",
})


@dataclass(frozen=True, slots=True)
class PreparePaths:
    project_root: Path; scenarios_path: Path; schema_path: Path; current_round_path: Path; round_template_path: Path
    jinfra_receipt_path: Path; dispatch_path: Path; report_path: Path; prewalk_receipt_path: Path; feedback_path: Path; prep_receipt_path: Path
    local_process_receipt_path: Path | None = None
    owner_walk_path: Path | None = None

    def __post_init__(self) -> None:
        if self.owner_walk_path is None:
            ticket_prefix = self.current_round_path.name.split(".", 1)[0]
            object.__setattr__(self, "owner_walk_path", self.current_round_path.with_name(f"{ticket_prefix}.UAT-OWNER-WALK.md"))


def _owner_walk_target(request: PrepareRequest) -> Path:
    target = request.paths.owner_walk_path
    assert target is not None
    return target


@dataclass(frozen=True, slots=True)
class PrepareRequest:
    ticket: str; paths: PreparePaths; canonical_manifest: Mapping[str, object]
    behavior_lock: Literal["YES", "NO", "UNKNOWN"]
    selected_instrument: Literal["CURRENT_SCRIPT_JQATESTER_WALK", "DETERMINISTIC_REPLAY"]
    canary_path: str; generated_at: str
    certification_source: str = "jinfra-docker-recreate-v1"


CERTIFICATION_SOURCES = frozenset({"jinfra-docker-recreate-v1", "local-process-v1"})


@dataclass(frozen=True, slots=True)
class PrepareResult:
    exit_code: int; prep_state: PrepState; package_state: PackageState | None; outcome_token: str
    dispatch_required: bool; owner_invitation_ready: bool; receipt_path: Path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _layer_availability_rows(chain: _ChainResult) -> dict[str, dict[str, object]]:
    """Derive the per-project layer-availability declaration from chain state
    run_prepare already computed (HAS-687 F4: loud self-skip instead of per-lane
    hard-failure rediscovery). Observational ONLY — the declaration never gates,
    blocks, or changes any prepare state transition.

    ``sealed-package`` availability is the fraction of the round's hot-journey
    scenarios carrying a chain-verified ``gwt_sha256`` (the COM-376 sealing
    input); ``project-supported`` covers the project-local derivation chain
    (scenario corpus, runbook, round lineage) excluding the governed corpus
    field. The ``gwt_sha256`` corpus writer/backfill and the ``jInfra
    --currency`` hard-gate are owned by the COM-346-JINFRA-BOSS lane —
    referenced here, NOT implemented (COM-383 work-stream C interim).
    """
    total = chain.journey_count
    carrying = len(chain.verified_gwt_sha256_by_journey)
    if total > 0:
        availability: LayerAvailabilityToken = "AVAILABLE" if carrying == total else "UNAVAILABLE" if carrying == 0 else "PARTIAL"
        sealed: dict[str, object] = {"availability": availability, "carrying_gwt_sha256": carrying, "total_scenarios": total}
        if availability in {"PARTIAL", "UNAVAILABLE"}:
            sealed["dependency"] = _GWT_DEPENDENCY_TICKET
    else:
        sealed = {"availability": "UNVERIFIED", "chain_gaps": len(chain.gaps)}
    project_gaps = sum(1 for gap in chain.gaps if gap.split(":", 1)[0] not in _GWT_CORPUS_GAP_TOKENS)
    project: dict[str, object] = {"availability": "AVAILABLE"} if project_gaps == 0 else {"availability": "UNAVAILABLE", "chain_gaps": project_gaps}
    return {_SEALED_PACKAGE_LAYER: sealed, _PROJECT_SUPPORTED_LAYER: project}


def _carried_layer_availability(previous: dict[str, Any] | None) -> Mapping[str, Mapping[str, object]] | None:
    """Re-read the declaration a prior invocation sealed into its receipt.

    Sealed reinvocations never re-verify the chain, so the declaration of the
    same sealed round is carried forward from the receipt already read for
    drift detection; receipts predating the declaration degrade to None.
    """
    if previous is None:
        return None
    raw = previous.get("layer_availability")
    if not isinstance(raw, dict) or not all(isinstance(row, Mapping) for row in raw.values()):
        return None
    return cast(Mapping[str, Mapping[str, object]], raw)


def _layer_stderr_lines(rows: Mapping[str, Mapping[str, object]]) -> list[str]:
    lines: list[str] = []
    for layer in (_SEALED_PACKAGE_LAYER, _PROJECT_SUPPORTED_LAYER):
        row = rows.get(layer)
        if row is None:
            continue
        availability = str(row.get("availability", ""))
        if layer == _SEALED_PACKAGE_LAYER and "carrying_gwt_sha256" in row:
            detail = f" ({row['carrying_gwt_sha256']}/{row['total_scenarios']} scenarios carry gwt_sha256)"
        elif "chain_gaps" in row:
            qualifier = "gwt coverage not derived; " if layer == _SEALED_PACKAGE_LAYER else ""
            detail = f" ({qualifier}chain gaps: {row['chain_gaps']})"
        else:
            detail = ""
        lines.append(f"{_LAYER_STDERR_PREFIX}: {layer}: {availability}{detail}")
    return lines


def _result(request: PrepareRequest, state: PrepState, token: str, package_state: PackageState | None, *, dispatch: bool = False, invitation: bool = False, package: Mapping[str, object] | None = None, writes: list[str] | None = None, issued_sha: str | None = None, walk_script_path: str | None = None, layer_availability: Mapping[str, Mapping[str, object]] | None = None) -> PrepareResult:
    paths = request.paths
    owner_walk = _owner_walk_target(request)
    receipt = {
        "schema_version": "uat-prep-receipt@1", "run_id": f"prep-{request.ticket.lower()}", "timestamp": request.generated_at,
        "ticket": request.ticket, "prep_state": state, "outcome_token": token,
        "current_round_path": str(paths.current_round_path), "current_round_sha256": _sha(paths.current_round_path.read_bytes()) if paths.current_round_path.exists() else None,
        "issued_round_sha256": issued_sha, "package_state": package_state,
        "round_id": package.get("round_id") if package else None, "package_id": package.get("package_id") if package else None,
        "package_hash": package.get("package_hash") if package else None, "script_id": package.get("script_id") if package else None,
        "script_hash": package.get("script_hash") if package else None, "certified_build_hash": package.get("certified_build_hash") if package else None,
        "certification_source": request.certification_source,
        "certification_receipt_path": str(_selected_receipt_path(request)) if _selected_receipt_path(request) and _selected_receipt_path(request).exists() else None,
        "certification_receipt_sha256": _sha(_selected_receipt_path(request).read_bytes()) if _selected_receipt_path(request) and _selected_receipt_path(request).exists() else None,
        "jinfra_receipt_path": str(paths.jinfra_receipt_path) if request.certification_source == "jinfra-docker-recreate-v1" and paths.jinfra_receipt_path.exists() else None,
        "jinfra_receipt_sha256": _sha(paths.jinfra_receipt_path.read_bytes()) if request.certification_source == "jinfra-docker-recreate-v1" and paths.jinfra_receipt_path.exists() else None,
        "dispatch_path": str(paths.dispatch_path) if paths.dispatch_path.exists() else None, "dispatch_sha256": _sha(paths.dispatch_path.read_bytes()) if paths.dispatch_path.exists() else None,
        "report_path": str(paths.report_path) if paths.report_path.exists() else None, "report_sha256": _sha(paths.report_path.read_bytes()) if paths.report_path.exists() else None,
        "prewalk_receipt_path": str(paths.prewalk_receipt_path) if paths.prewalk_receipt_path.exists() else None, "prewalk_receipt_sha256": _sha(paths.prewalk_receipt_path.read_bytes()) if paths.prewalk_receipt_path.exists() else None,
        "feedback_path": str(paths.feedback_path) if paths.feedback_path.exists() else None, "feedback_sha256": _sha(paths.feedback_path.read_bytes()) if paths.feedback_path.exists() else None,
        "owner_walk_path": str(owner_walk) if owner_walk.exists() else None, "owner_walk_sha256": _sha(owner_walk.read_bytes()) if owner_walk.exists() else None,
        "dispatch_required": dispatch, "owner_invitation_ready": invitation, "writes": writes or [],
    }
    if layer_availability is not None:
        receipt["layer_availability"] = {layer: dict(row) for layer, row in layer_availability.items()}
        for line in _layer_stderr_lines(layer_availability):
            print(line, file=sys.stderr)
    if walk_script_path is not None:
        receipt["walk_script_path"] = walk_script_path
    _atomic(paths.prep_receipt_path, json.dumps(receipt, sort_keys=True).encode() + b"\n")
    return PrepareResult(0, state, package_state, token, dispatch, invitation, paths.prep_receipt_path)


def _selected_receipt_path(request: PrepareRequest) -> Path | None:
    if request.certification_source == "jinfra-docker-recreate-v1":
        return request.paths.jinfra_receipt_path
    if request.certification_source == "local-process-v1":
        return request.paths.local_process_receipt_path
    return None


def _certification(request: PrepareRequest) -> tuple[dict[str, Any] | None, str | None]:
    if request.certification_source not in CERTIFICATION_SOURCES:
        return None, "unknown-source"
    path = _selected_receipt_path(request)
    if path is None:
        return None, "receipt missing"
    receipt = _read_json(path)
    if receipt is None:
        return None, "receipt missing"
    if request.certification_source == "local-process-v1":
        try:
            from jswarm.uat_prepare_local_process import LocalCertificationError, SOURCE, SCHEMA, validate_receipt, verify_receipt
            if receipt.get("schema") != SCHEMA or receipt.get("source") != SOURCE:
                return None, "cross-source"
            validate_receipt(receipt)
            verify_receipt(receipt)
        except (ImportError, LocalCertificationError, OSError, ValueError):
            return None, "local-validation-failed"
        build = receipt["certification"]["hash"]
        if build != request.canonical_manifest.get("certified_build_hash"):
            return None, "wrong-hash"
        return receipt, None
    # Docker remains byte-for-byte the established effective predicate branch.
    if receipt.get("source") == "local-process-v1" or receipt.get("schema") == "jswarm.uat.local-process-certification/v1":
        return None, "cross-source"
    defect = receipt.get("defect")
    if isinstance(defect, str): return None, defect
    certification = receipt.get("certification")
    if not isinstance(certification, dict): return None, "certification missing"
    if certification.get("verdict") != "certified": return None, "non-certified"
    if receipt.get("force_applied") or receipt.get("force_requested"): return None, "forced"
    if receipt.get("exit_code") != 0: return None, "nonzero"
    build = certification.get("hash")
    if not isinstance(build, str) or build != request.canonical_manifest.get("certified_build_hash"): return None, "wrong-hash"
    return receipt, None


def _stack_receipt(request: PrepareRequest, package: Mapping[str, object]) -> StackGateReceipt:
    path = _selected_receipt_path(request)
    if path is None:
        raise ValueError("selected receipt path is missing")
    rendered_path = f".jswarm/plans/{request.ticket}/jinfra.json" if request.certification_source == "jinfra-docker-recreate-v1" else str(path)
    return StackGateReceipt(
        str(package["certified_build_hash"]), request.certification_source, rendered_path, _sha(path.read_bytes()),
        str(package["app_url"]), str(package["login"]), "CERTIFIED",
    )


def _handoff(request: PrepareRequest, package: Mapping[str, object], round_sha: str, receipt_sha: str) -> bytes:
    journeys = cast(list[Mapping[str, object]], package["journeys"])
    if package.get("schema_version") == "uat-canonical-package@2":
        steps = [
            {
                "journey_id": journey["journey_id"], "step_id": step["step_id"],
                "step_ordinal": step["ordinal"], "action": step["instruction"],
                "action_sha256": _sha(str(step["instruction"]).encode()),
            }
            for journey in journeys
            for step in cast(list[Mapping[str, object]], journey["steps"])
        ]
    else:
        steps = [
            {"journey_id": journey["journey_id"], "step_ordinal": index, "action": action, "action_sha256": _sha(str(action).encode())}
            for journey in journeys for index, action in enumerate(cast(list[object], journey["actions"]), 1)
        ]
    root = f".jswarm/plans/{request.ticket}"
    receipt_path = f"{root}/jinfra.json" if request.certification_source == "jinfra-docker-recreate-v1" else str(_selected_receipt_path(request))
    annex: dict[str, object] = {"schema_version":"uat-prep-handoff@1", "ticket":request.ticket, "generated_at":request.generated_at, "current_round_path":f"{root}/{request.ticket}.UAT-CURRENT-ROUND.md", "current_round_sha256":round_sha, "round_id":package["round_id"], "package_id":package["package_id"], "package_hash":package["package_hash"], "sealed_payload_sha256":package["sealed_payload_sha256"], "script_id":package["script_id"], "script_hash":package["script_hash"], "certified_build_hash":package["certified_build_hash"], "certification_source":request.certification_source, "certification_receipt_path":receipt_path, "certification_receipt_sha256":receipt_sha, "jinfra_receipt_path":f"{root}/jinfra.json" if request.certification_source == "jinfra-docker-recreate-v1" else None, "jinfra_receipt_sha256":receipt_sha if request.certification_source == "jinfra-docker-recreate-v1" else None, "behavior_lock":request.behavior_lock, "selected_instrument":request.selected_instrument, "journey_order":[j["journey_id"] for j in journeys], "journeys":journeys, "step_contract":steps, "atom_verdict_slots":[], "known_check_receipt":package["known_sources_checked"], "canary_path":request.canary_path, "skipped_steps":[], "recovery_policy":package["recovery_policy"], "recovery_use":"none", "observer":package["observer"], "observer_status":"attached", "report_path":f"{root}/{request.ticket}.uat.prewalk-report.md", "evidence_root":f"{root}/evidence", "tool_verdict_contract":"OWNER-MAY-WALK: yes|no"}
    raw = json.dumps(annex, sort_keys=True, separators=(",", ":")).encode(); annex["handoff_sha256"] = _sha(raw)
    return ("# QA dispatch handoff\n\nBEGIN UAT PREP HANDOFF\n```json\n" + json.dumps(annex, sort_keys=True) + "\n```\nEND UAT PREP HANDOFF\n").encode()


def _handoff_annex(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])
    except (OSError, IndexError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _sealed_package(path: Path) -> Mapping[str, object] | None:
    try:
        content = path.read_bytes()
    except OSError:
        return None
    bounds = _package_region_bounds(content)
    if bounds is None:
        return None
    package, error = _package_from_region(content[bounds[0]:bounds[1]])
    return package if error is None else None


def _journey_gwt_binding(value: object) -> dict[str, object]:
    """Derive the renderer binding from the schema's authoritative GWT fields.

    V1 retains its exact journey-level digest map.  V2 retains every ordered GWT
    digest under its owning scenario; no journey-level compatibility digest is
    invented or accepted.
    """
    if not isinstance(value, list):
        return {}
    bindings: dict[str, object] = {}
    for journey in value:
        if not isinstance(journey, Mapping) or not isinstance(journey.get("journey_id"), str):
            continue
        journey_id = str(journey["journey_id"])
        if isinstance(journey.get("gwt_sha256"), str):
            bindings[journey_id] = str(journey["gwt_sha256"])
            continue
        scenarios = journey.get("scenarios")
        if not isinstance(scenarios, list):
            continue
        scenario_binding: dict[str, tuple[str, ...]] = {}
        for scenario in scenarios:
            if not isinstance(scenario, Mapping) or not isinstance(scenario.get("scenario_id"), str):
                continue
            blocks = scenario.get("gwt")
            if not isinstance(blocks, list):
                continue
            digests = tuple(
                str(block["sha256"])
                for block in blocks
                if isinstance(block, Mapping) and isinstance(block.get("sha256"), str)
            )
            if len(digests) == len(blocks):
                scenario_binding[str(scenario["scenario_id"])] = digests
        steps = journey.get("steps")
        links_are_authoritative = isinstance(steps, list) and all(
            isinstance(step, Mapping)
            and isinstance(step.get("scenario_links"), list)
            and all(
                isinstance(link, Mapping)
                and isinstance(link.get("scenario_id"), str)
                and isinstance(link.get("gwt_refs"), list)
                and all(
                    isinstance(reference, str)
                    and reference in scenario_binding.get(str(link["scenario_id"]), ())
                    for reference in link["gwt_refs"]
                )
                for link in step["scenario_links"]
            )
            for step in steps
        )
        if len(scenario_binding) == len(scenarios) and links_are_authoritative:
            bindings[journey_id] = scenario_binding
    return bindings


def _divergent_journeys(request: PrepareRequest, package: Mapping[str, object]) -> list[str]:
    requested = _journey_gwt_binding(request.canonical_manifest.get("journeys"))
    sealed = _journey_gwt_binding(package.get("journeys"))
    return sorted(journey_id for journey_id in requested.keys() | sealed.keys() if requested.get(journey_id) != sealed.get(journey_id))


def _archive_sealed_package(request: PrepareRequest, package: Mapping[str, object]) -> list[str]:
    sources = (request.paths.current_round_path, request.paths.dispatch_path, request.paths.prep_receipt_path)
    snapshots = [(source.name, source.read_bytes()) for source in sources]
    snapshot_sha = _sha(b"".join(name.encode() + b"\0" + data + b"\0" for name, data in snapshots))
    package_id = str(package.get("package_id", "unknown-package"))
    archive = request.paths.current_round_path.parent / "evidence" / "uat-prep-seal-archive" / f"{package_id}-{snapshot_sha[:16]}"
    writes: list[str] = []
    checksum_lines: list[str] = []
    for name, data in snapshots:
        destination = archive / name
        if destination.exists() and destination.read_bytes() != data:
            raise OSError(f"seal archive collision: {destination}")
        _atomic(destination, data)
        writes.append(str(destination))
        checksum_lines.append(f"{_sha(data)}  {name}")
    checksum_path = archive / "SHA256SUMS"
    _atomic(checksum_path, ("\n".join(checksum_lines) + "\n").encode())
    writes.append(str(checksum_path))
    return writes


def _eligible_ledger(path: Path) -> bool:
    try:
        validate_ledger(path, allow_pre_round=True)
    except (OSError, LedgerValidationError):
        return False
    return True


def _evidence_is_valid(path: str, root: Path, aggregate: set[str]) -> bool:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return path in aggregate and resolved.is_file() and not candidate.is_symlink()


def _report_outcome(report: Mapping[str, Any], request: PrepareRequest, handoff: Mapping[str, Any], package: Mapping[str, object]) -> tuple[PrepState, str, PrewalkGateReceipt | None]:
    if report.get("tool_verdict_line") not in {"OWNER-MAY-WALK: yes", "OWNER-MAY-WALK: no"}:
        return "UNVERIFIED", "UNVERIFIED: TOOL VERDICT NOT READ", None
    if set(report) != _REPORT_KEYS or report.get("schema_version") != "uat-prewalk-report@1":
        return "BLOCKED", "OWNER-MAY-WALK: no", None
    identities = {
        "ticket": request.ticket, "handoff_path": str(request.paths.dispatch_path), "handoff_sha256": _sha(request.paths.dispatch_path.read_bytes()),
        "current_round_path": str(request.paths.current_round_path), "current_round_sha256": handoff.get("current_round_sha256"),
        "round_id": package["round_id"], "package_id": package["package_id"], "package_hash": package["package_hash"],
        "sealed_payload_sha256": package["sealed_payload_sha256"], "script_id": package["script_id"], "script_hash": package["script_hash"],
        "certified_build_hash": package["certified_build_hash"],
    }
    if report.get("certified_build_hash") != identities["certified_build_hash"]:
        return "BLOCKED", "NOT CERTIFIED: wrong-hash", None
    if report.get("current_round_sha256") != identities["current_round_sha256"]:
        return "BLOCKED", "BLOCKED: SEALED PACKAGE DRIFT", None
    if any(report.get(key) != value for key, value in identities.items()):
        return "BLOCKED", "BLOCKED: no owner-script pre-walk", None
    started, finished = report.get("started_at"), report.get("finished_at")
    if not isinstance(started, str) or not isinstance(finished, str) or not started.endswith("Z") or not finished.endswith("Z") or started > finished:
        return "BLOCKED", "OWNER-MAY-WALK: no", None
    steps = report.get("executed_steps")
    contract = handoff.get("step_contract")
    if not isinstance(steps, list) or not isinstance(contract, list) or len(steps) != len(contract):
        return "PARTIAL", "PARTIAL: SHEET NOT WALKED VERBATIM", None
    aggregate = report.get("evidence_paths")
    if not isinstance(aggregate, list) or not all(isinstance(value, str) for value in aggregate) or len(aggregate) != len(set(aggregate)):
        return "BLOCKED", "OWNER-MAY-WALK: no", None
    evidence = set(aggregate); root = request.paths.dispatch_path.parent / "evidence"
    for actual, expected in zip(steps, contract, strict=True):
        if not isinstance(actual, dict) or set(actual) != {"journey_id", "step_ordinal", "action_sha256", "verdict", "evidence_paths"} or any(actual.get(key) != expected.get(key) for key in ("journey_id", "step_ordinal", "action_sha256")):
            return "PARTIAL", "PARTIAL: SHEET NOT WALKED VERBATIM", None
        paths = actual.get("evidence_paths")
        if actual.get("verdict") != "PASS" or not isinstance(paths, list) or not paths:
            return "PARTIAL", "PARTIAL: SHEET NOT WALKED VERBATIM", None
        if not all(isinstance(value, str) and _evidence_is_valid(value, root, evidence) for value in paths):
            return "BLOCKED", "OWNER-MAY-WALK: no", None
    expected_atoms = [(journey["journey_id"], atom) for journey in cast(list[Mapping[str, object]], package["journeys"]) for atom in cast(list[str], journey["atom_ids"])]
    atoms = report.get("atom_results")
    if not isinstance(atoms, list) or [(row.get("journey_id"), row.get("atom_id")) if isinstance(row, dict) else (None, None) for row in atoms] != expected_atoms:
        return "PARTIAL", "PARTIAL: SHEET NOT WALKED VERBATIM", None
    for atom in cast(list[dict[str, Any]], atoms):
        paths = atom.get("evidence_paths")
        if atom.get("verdict") == "NOT_RUN" or not isinstance(paths, list) or not paths:
            return "PARTIAL", "PARTIAL: SHEET NOT WALKED VERBATIM", None
        if atom.get("verdict") in {"FAIL", "BLOCKED"}:
            return "FAILED", "OWNER-MAY-WALK: no", None
        if atom.get("verdict") != "PASS" or not all(isinstance(value, str) and _evidence_is_valid(value, root, evidence) for value in paths):
            return "BLOCKED", "OWNER-MAY-WALK: no", None
    known = report.get("known_check_receipt")
    if not isinstance(known, dict) or known.get("value") != handoff.get("known_check_receipt") or not isinstance(known.get("evidence_paths"), list) or not known["evidence_paths"]:
        return "BLOCKED", "BLOCKED: KNOWN-BEHAVIOR CROSS-CHECK INCOMPLETE", None
    if not all(isinstance(value, str) and _evidence_is_valid(value, root, evidence) for value in known["evidence_paths"]):
        return "BLOCKED", "BLOCKED: KNOWN-BEHAVIOR CROSS-CHECK INCOMPLETE", None
    recovery = report.get("recovery_use")
    recovery_paths = report.get("recovery_evidence_paths")
    if recovery not in {"none", "used"} or not isinstance(recovery_paths, list) or (recovery == "none") != (not recovery_paths):
        return "BLOCKED", "OWNER-MAY-WALK: no", None
    if recovery == "used" and any(atom["verdict"] == "PASS" for atom in atoms):
        return "FAILED", "FAIL: RECOVERY-DEPENDENT LIVE PATH", None
    observer = report.get("observer_status"); observer_paths = report.get("observer_evidence_paths")
    if observer not in {"attached", "fallback-declared"} or not isinstance(observer_paths, list) or not observer_paths or not all(isinstance(value, str) and _evidence_is_valid(value, root, evidence) for value in observer_paths):
        return "BLOCKED", "OWNER OBSERVER UNAVAILABLE: FALLBACK REQUIRED", None
    canary = report.get("canary_path")
    if not isinstance(canary, str) or not _evidence_is_valid(canary, root, evidence) or report.get("canary_sha256") != _sha(Path(canary).read_bytes()):
        return "BLOCKED", "OWNER-MAY-WALK: no", None
    if report.get("skipped_steps") or report.get("substituted_steps") or report.get("reordered_steps") or report.get("overall_verdict") != "PASS" or report.get("tool_verdict_line") != "OWNER-MAY-WALK: yes":
        return "BLOCKED", "OWNER-MAY-WALK: no", None
    journey_count = len(cast(list[Mapping[str, object]], package["journeys"]))
    return "QA_VERIFIED", "OWNER-MAY-WALK: yes", _prewalk_receipt(request, package, report, journey_count, len(atoms))


def _prewalk_receipt(request: PrepareRequest, package: Mapping[str, object], report: Mapping[str, Any], journeys: int, atoms: int) -> PrewalkGateReceipt:
    recovery: Literal["none", "used"] = "none" if report["recovery_use"] == "none" else "used"
    observer: Literal["attached", "fallback-declared"] = "attached" if report["observer_status"] == "attached" else "fallback-declared"
    fields = (
        str(package["certified_build_hash"]), str(package["package_id"]), str(package["package_hash"]),
        str(package["script_id"]), str(package["script_hash"]), journeys, journeys, atoms, atoms,
        str(report["known_check_receipt"]["value"]), recovery, observer, str(report["finished_at"]),
        str(report["canary_path"]), str(request.paths.prewalk_receipt_path),
    )
    receipt_sha = _sha(json.dumps(fields, separators=(",", ":")).encode())
    return PrewalkGateReceipt(
        certified_build_hash=fields[0], package_id=fields[1], package_hash=fields[2], script_id=fields[3], script_hash=fields[4],
        journeys_walked=fields[5], journeys_total=fields[6], atoms_passed=fields[7], atoms_total=fields[8], known_check_receipt=fields[9],
        recovery_use=fields[10], observer_status=fields[11], completed_at=fields[12], owner_may_walk="yes", canary_path=fields[13],
        receipt_path=fields[14], receipt_sha256=receipt_sha,
    )


def _render(request: PrepareRequest, source: bytes, template: bytes, package: Mapping[str, object], state: PackageState, stack: StackGateReceipt, prewalk: PrewalkGateReceipt | None, *, walk_script_gate_receipt: WalkScriptGateReceipt | None = None, walk_script_unverified_row: bool = False) -> bytes | None:
    gwt = _journey_gwt_binding(package.get("journeys"))
    _, rendered, _ = build_round_render_mapping(source, template, canonical_package=package, package_state=state, verified_gwt_sha256_by_journey=gwt, stack_gate_receipt=stack, prewalk_gate_receipt=prewalk, walk_script_gate_receipt=walk_script_gate_receipt, walk_script_unverified_row=walk_script_unverified_row)
    return rendered


def _has_unverified_gate_header(round_bytes: bytes) -> bool:
    return any(line.startswith((b"**Stack:**", b"**Pre-walk:**", b"**Walk-script:**")) and b"<UNVERIFIED" in line for line in round_bytes.splitlines())


def _interrupted_issuance_recovery(request: PrepareRequest, report_path: Path | None, carried: Mapping[str, Mapping[str, object]] | None) -> PrepareResult | None:
    """Retained-receipt interrupted-issuance recovery (COM-376 §Issuance Write
    Ordering steps 5/6; COM-383 design §6). ``_atomic`` is replacement-based, so an
    interrupted ISSUED-round write (step 5) or success-receipt write (step 6) leaves
    the PRIOR DRAFT_SEALED receipt on disk; the sealed-path drift check then fires
    before any recovery could run. This helper evaluates the interrupted-issuance
    hypothesis from on-disk state alone: dispatch/package/report identities still
    anchored (handoff annex + sealed package + report with a QA_VERIFIED outcome)
    and the on-disk round byte-equal to the recomputed QA_VERIFIED or ISSUED render.
    Returns None whenever the hypothesis is disproven — the caller then retains the
    drift refusal; unexplained round bytes are never overwritten."""
    if report_path is None:
        return None
    package = _sealed_package(request.paths.current_round_path)
    handoff = _handoff_annex(request.paths.dispatch_path)
    if package is None or handoff is None:
        return None
    report = _read_json(report_path)
    if report is None:
        return None
    state, _token, prewalk = _report_outcome(report, request, handoff, package)
    if state != "QA_VERIFIED" or prewalk is None:
        return None
    walk_script = request.paths.current_round_path.with_name(f"{request.ticket}.uat-test.md")
    if not walk_script.is_file():
        return None
    try:
        chain_projection = _query_module.verify_chain(scenarios_path=request.paths.scenarios_path, schema_path=request.paths.schema_path, ticket=request.ticket, project_root=request.paths.project_root, walk_script_path=walk_script)
    except OSError:
        return None
    if chain_projection.owner_walk_token is not None or chain_projection.owner_walk is None:
        return None
    walk_receipt = WalkScriptGateReceipt(walk_script_path=str(walk_script), sealed_round_anchor=str(request.paths.current_round_path))
    template = request.paths.round_template_path.read_bytes()
    stack = _stack_receipt(request, package)
    qa_round = _render(request, template, template, package, "QA_VERIFIED", stack, prewalk, walk_script_gate_receipt=walk_receipt)
    if qa_round is None or _has_unverified_gate_header(qa_round):
        return None
    feedback = render_feedback_document(package, generated_at=request.generated_at, tool_owned_receipt=str(request.paths.prewalk_receipt_path))
    owner_walk_target = _owner_walk_target(request)
    try:
        owner_walk_bytes = render_owner_walk_document(package, generated_at=request.generated_at, owner_walk=chain_projection.owner_walk)
    except OwnerWalkGenerationError:
        return None
    issued_round = _render(request, template, template, package, "ISSUED", stack, prewalk, walk_script_gate_receipt=walk_receipt)
    if issued_round is None or _has_unverified_gate_header(issued_round):
        return None
    on_disk = request.paths.current_round_path.read_bytes()
    if on_disk == issued_round:
        round_write_needed = False
    elif on_disk == qa_round:
        round_write_needed = True
    else:
        # Unexplained round bytes (neither recomputed state): never overwritten.
        return None
    # Hypothesis accepted. The mtime staleness gate is not re-applied: byte-equality
    # with the recomputed render proves the original invocation passed the full gate
    # (the walk script predates the interrupted write by construction).
    recovery_writes: list[str] = []
    try:
        if not (request.paths.feedback_path.exists() and request.paths.feedback_path.read_bytes() == feedback):
            _atomic(request.paths.feedback_path, feedback)
            recovery_writes.append(str(request.paths.feedback_path))
    except OSError:
        return _result(request, "PARTIAL", "FEEDBACK-FILE BLOCKED", "QA_VERIFIED", package=package, writes=recovery_writes, layer_availability=carried)
    if owner_walk_target.exists():
        if owner_walk_target.read_bytes() != owner_walk_bytes:
            return _result(request, "BLOCKED", "walk.recovery.blocked.byte-drift", "QA_VERIFIED", package=package, writes=recovery_writes, layer_availability=carried)
    else:
        try:
            _atomic(owner_walk_target, owner_walk_bytes)
        except OSError:
            return _result(request, "PARTIAL", "OWNER-WALK-FILE BLOCKED", "QA_VERIFIED", package=package, writes=recovery_writes, layer_availability=carried)
        recovery_writes.append(str(owner_walk_target))
    if round_write_needed:
        # Step-5 interruption (QA_VERIFIED round on disk): finish the ISSUED write.
        _atomic(request.paths.current_round_path, issued_round)
        recovery_writes.append(str(request.paths.current_round_path))
    return _result(request, "ISSUED", "OWNER-MAY-WALK: yes", "ISSUED", invitation=True, package=package, writes=[*recovery_writes, str(request.paths.prep_receipt_path)], issued_sha=_sha(issued_round), layer_availability=carried)



def _materialize_draft(
    request: PrepareRequest,
    *,
    archive_package: Mapping[str, object] | None = None,
    carried_layers: Mapping[str, Mapping[str, object]] | None = None,
) -> PrepareResult:
    ledger = request.paths.project_root / ".jswarm/plans" / request.ticket / f"{request.ticket}.test-traceability.md"
    if not _eligible_ledger(ledger):
        return _result(request, "BLOCKED", "BLOCKED: no eligible unified ledger; create DRAFT ledger with processing_log: []", None)
    layers = carried_layers
    if archive_package is None:
        try:
            chain = verify_chain(scenarios_path=request.paths.scenarios_path, schema_path=request.paths.schema_path, ticket=request.ticket, project_root=request.paths.project_root)
        except OSError as error:
            artifact = error.filename or str(request.paths.scenarios_path)
            return _result(request, "BLOCKED", f"CHAIN FAIL: OSError: {artifact}", None)
        layers = _layer_availability_rows(chain)
        if chain.gaps:
            return _result(request, "BLOCKED", "CHAIN FAIL: " + "; ".join(chain.gaps), None, layer_availability=layers)
    package = build_canonical_package(request.canonical_manifest)
    if validate_normalized_package(package):
        return _result(request, "BLOCKED", "NOT CERTIFIED: receipt missing", None, layer_availability=layers)
    template = request.paths.round_template_path.read_bytes()
    stack = _stack_receipt(request, package)
    draft = _render(request, template, template, package, "DRAFT_SEALED", stack, None, walk_script_unverified_row=True)
    if draft is None:
        return _result(request, "BLOCKED", "CHAIN FAIL: RUNBOOK-ANCHOR-MISSING: UAT.HAPPY", None, layer_availability=layers)
    selected_receipt = _selected_receipt_path(request)
    if selected_receipt is None:
        return _result(request, "BLOCKED", "NOT CERTIFIED: receipt missing", None, layer_availability=layers)
    handoff = _handoff(request, package, _sha(draft), _sha(selected_receipt.read_bytes()))
    archive_writes = _archive_sealed_package(request, archive_package) if archive_package is not None else []
    _atomic(request.paths.current_round_path, draft)
    _atomic(request.paths.dispatch_path, handoff)
    return _result(request, "PENDING", "BLOCKED: no owner-script pre-walk", "DRAFT_SEALED", dispatch=True, package=package, writes=[*archive_writes, str(request.paths.current_round_path), str(request.paths.dispatch_path)], layer_availability=layers)


def run_prepare(request: PrepareRequest, *, report_path: Path | None = None, force_recreate: bool = False) -> PrepareResult:
    if request.behavior_lock in {"NO", "UNKNOWN"} and request.selected_instrument != "CURRENT_SCRIPT_JQATESTER_WALK":
        return _result(request, "BLOCKED", "BLOCKED: OWNER-OUTCOME WITHOUT CRITERION", None)
    if any(key in request.canonical_manifest for key in ("package_id", "package_hash", "script_id", "script_hash")):
        return _result(request, "BLOCKED", "NOT CERTIFIED: receipt missing", None)
    _jinfra, reason = _certification(request)
    if reason:
        return _result(request, "BLOCKED", f"NOT CERTIFIED: {reason}", None)
    if request.paths.current_round_path.is_file() and request.paths.dispatch_path.is_file() and not request.paths.prep_receipt_path.is_file():
        # COM-383 receipt-less ISSUED recovery (design §6, "Success receipt write fails
        # after ISSUED round"): the last step of the COM-376 issuance ordering was
        # interrupted after the ISSUED round landed. Recompute every expected byte,
        # recognize byte-equal artifacts, finish idempotently, and only then write the
        # success receipt — the invitation becomes true only then.
        if report_path is None:
            # No writes at all, not even this outcome's receipt: writing one would flip
            # the next invocation onto the sealed path, whose staleness gate cannot pass
            # here (the on-disk ISSUED round postdates the walk script). The operator
            # re-runs with the report and re-enters this branch.
            return PrepareResult(0, "BLOCKED", None, "BLOCKED: PREP RECEIPT MISSING", False, False, request.paths.prep_receipt_path)
        package = _sealed_package(request.paths.current_round_path)
        handoff = _handoff_annex(request.paths.dispatch_path)
        if package is None or handoff is None:
            return _result(request, "BLOCKED", "BLOCKED: SEALED PACKAGE DRIFT", "DRAFT_SEALED")
        report = _read_json(report_path)
        if report is None:
            return _result(request, "UNVERIFIED", "UNVERIFIED: TOOL VERDICT NOT READ", "DRAFT_SEALED", package=package)
        state, token, prewalk = _report_outcome(report, request, handoff, package)
        if state != "QA_VERIFIED" or prewalk is None:
            return _result(request, state, token, "DRAFT_SEALED", package=package)
        walk_script = request.paths.current_round_path.with_name(f"{request.ticket}.uat-test.md")
        if not walk_script.is_file():
            return _result(request, "BLOCKED", "BLOCKED: walk script missing", "DRAFT_SEALED", package=package, walk_script_path=str(walk_script))
        try:
            chain_projection = _query_module.verify_chain(scenarios_path=request.paths.scenarios_path, schema_path=request.paths.schema_path, ticket=request.ticket, project_root=request.paths.project_root, walk_script_path=walk_script)
        except OSError:
            return _result(request, "BLOCKED", "BLOCKED: walk script missing", "DRAFT_SEALED", package=package, walk_script_path=str(walk_script))
        if chain_projection.owner_walk_token is not None:
            return _result(request, "BLOCKED", chain_projection.owner_walk_token, "DRAFT_SEALED", package=package, walk_script_path=str(walk_script))
        if chain_projection.owner_walk is None:
            # Defensive fail-closed guard (design §5.3 "require non-None owner_walk"): removable only with the seam contract.
            return _result(request, "BLOCKED", "walk.generation.blocked.walk-script", "DRAFT_SEALED", package=package, walk_script_path=str(walk_script))
        walk_receipt = WalkScriptGateReceipt(walk_script_path=str(walk_script), sealed_round_anchor=str(request.paths.current_round_path))
        template = request.paths.round_template_path.read_bytes()
        stack = _stack_receipt(request, package)
        qa_round = _render(request, template, template, package, "QA_VERIFIED", stack, prewalk, walk_script_gate_receipt=walk_receipt)
        if qa_round is None or _has_unverified_gate_header(qa_round):
            return _result(request, "BLOCKED", "OWNER-MAY-WALK: no", "DRAFT_SEALED", package=package, walk_script_path=str(walk_script))
        feedback = render_feedback_document(package, generated_at=request.generated_at, tool_owned_receipt=str(request.paths.prewalk_receipt_path))
        owner_walk_target = _owner_walk_target(request)
        try:
            owner_walk_bytes = render_owner_walk_document(package, generated_at=request.generated_at, owner_walk=chain_projection.owner_walk)
        except OwnerWalkGenerationError as error:
            return _result(request, "BLOCKED", str(error), "DRAFT_SEALED", package=package, walk_script_path=str(walk_script))
        issued_round = _render(request, template, template, package, "ISSUED", stack, prewalk, walk_script_gate_receipt=walk_receipt)
        if issued_round is None or _has_unverified_gate_header(issued_round):
            return _result(request, "BLOCKED", "OWNER-MAY-WALK: no", "DRAFT_SEALED", package=package, walk_script_path=str(walk_script))
        if request.paths.current_round_path.read_bytes() != issued_round:
            # Bounded limit (design §6): a round that is not byte-equal to the recomputed
            # ISSUED render — e.g. the QA-on-disk intermediate of an interruption between
            # the QA and ISSUED writes — is unexplained bytes and is never overwritten;
            # that step-6-interruption retry variant is not implemented (no frozen test
            # demands it).
            return _result(request, "BLOCKED", "BLOCKED: SEALED PACKAGE DRIFT", "QA_VERIFIED", package=package, walk_script_path=str(walk_script))
        # Byte-equality with the recomputed ISSUED round proves the original invocation
        # passed the full staleness gate — the walk script predates the ISSUED write by
        # construction — so the mtime gate is not re-applied in this branch.
        recovery_writes: list[str] = []
        try:
            if not (request.paths.feedback_path.exists() and request.paths.feedback_path.read_bytes() == feedback):
                _atomic(request.paths.feedback_path, feedback)
                recovery_writes.append(str(request.paths.feedback_path))
        except OSError:
            return _result(request, "PARTIAL", "FEEDBACK-FILE BLOCKED", "QA_VERIFIED", package=package, writes=recovery_writes)
        if owner_walk_target.exists():
            if owner_walk_target.read_bytes() != owner_walk_bytes:
                return _result(request, "BLOCKED", "walk.recovery.blocked.byte-drift", "QA_VERIFIED", package=package, writes=recovery_writes)
        else:
            try:
                _atomic(owner_walk_target, owner_walk_bytes)
            except OSError:
                return _result(request, "PARTIAL", "OWNER-WALK-FILE BLOCKED", "QA_VERIFIED", package=package, writes=recovery_writes)
            recovery_writes.append(str(owner_walk_target))
        return _result(request, "ISSUED", "OWNER-MAY-WALK: yes", "ISSUED", invitation=True, package=package, writes=[*recovery_writes, str(request.paths.prep_receipt_path)], issued_sha=_sha(issued_round))
    sealed = request.paths.current_round_path.is_file() and request.paths.dispatch_path.is_file() and request.paths.prep_receipt_path.is_file()
    if not sealed:
        return _materialize_draft(request)
    previous = _read_json(request.paths.prep_receipt_path)
    carried = _carried_layer_availability(previous)
    if previous is None or previous.get("dispatch_sha256") != _sha(request.paths.dispatch_path.read_bytes()) or previous.get("current_round_sha256") != _sha(request.paths.current_round_path.read_bytes()):
        # An interrupted step-5/step-6 issuance write retains the prior DRAFT_SEALED
        # receipt, so this drift check fires before any byte-equal recovery could
        # run. Evaluate the interrupted-issuance hypothesis before refusing; a None
        # return keeps the drift refusal (unexplained bytes are never overwritten).
        recovered = _interrupted_issuance_recovery(request, report_path, carried)
        if recovered is not None:
            return recovered
        return _result(request, "BLOCKED", "BLOCKED: SEALED PACKAGE DRIFT", "DRAFT_SEALED", layer_availability=carried)
    package = _sealed_package(request.paths.current_round_path)
    handoff = _handoff_annex(request.paths.dispatch_path)
    if package is None or handoff is None:
        return _result(request, "BLOCKED", "BLOCKED: SEALED PACKAGE DRIFT", "DRAFT_SEALED", layer_availability=carried)
    divergent_journeys = _divergent_journeys(request, package)
    if divergent_journeys and not force_recreate:
        token = "BLOCKED: SEALED REQUEST GWT DIVERGENCE: " + ", ".join(divergent_journeys) + "; rerun with --force-recreate"
        print(token, file=sys.stderr)
        return _result(request, "BLOCKED", token, "DRAFT_SEALED", package=package, layer_availability=carried)
    if force_recreate:
        return _materialize_draft(request, archive_package=package, carried_layers=carried)
    if report_path is None:
        prior_writes = previous.get("writes")
        return _result(request, "PENDING", "BLOCKED: no owner-script pre-walk", "DRAFT_SEALED", dispatch=True, package=package, writes=cast(list[str], prior_writes) if isinstance(prior_writes, list) else None, layer_availability=carried)
    report = _read_json(report_path)
    if report is None:
        return _result(request, "UNVERIFIED", "UNVERIFIED: TOOL VERDICT NOT READ", "DRAFT_SEALED", package=package, layer_availability=carried)
    state, token, prewalk = _report_outcome(report, request, handoff, package)
    if state != "QA_VERIFIED" or prewalk is None:
        return _result(request, state, token, "DRAFT_SEALED", package=package, layer_availability=carried)
    walk_script = request.paths.current_round_path.with_name(f"{request.ticket}.uat-test.md")
    if previous.get("package_state") != "ISSUED":
        walk_token = "BLOCKED: walk script missing" if not walk_script.is_file() else "BLOCKED: walk script stale" if walk_script.stat().st_mtime_ns < request.paths.current_round_path.stat().st_mtime_ns else None
        if walk_token is not None:
            return _result(request, "BLOCKED", walk_token, "DRAFT_SEALED", package=package, walk_script_path=str(walk_script), layer_availability=carried)
    walk_receipt = WalkScriptGateReceipt(walk_script_path=str(walk_script), sealed_round_anchor=str(request.paths.current_round_path))
    template = request.paths.round_template_path.read_bytes()
    stack = _stack_receipt(request, package)
    qa_round = _render(request, template, template, package, "QA_VERIFIED", stack, prewalk, walk_script_gate_receipt=walk_receipt)
    if qa_round is None or _has_unverified_gate_header(qa_round):
        return _result(request, "BLOCKED", "OWNER-MAY-WALK: no", "DRAFT_SEALED", package=package, layer_availability=carried)
    feedback = render_feedback_document(package, generated_at=request.generated_at, tool_owned_receipt=str(request.paths.prewalk_receipt_path))
    owner_walk_target = _owner_walk_target(request)
    try:
        chain_projection = _query_module.verify_chain(scenarios_path=request.paths.scenarios_path, schema_path=request.paths.schema_path, ticket=request.ticket, project_root=request.paths.project_root, walk_script_path=walk_script)
    except OSError:
        return _result(request, "BLOCKED", "BLOCKED: walk script missing", "DRAFT_SEALED", package=package, walk_script_path=str(walk_script), layer_availability=carried)
    if chain_projection.owner_walk_token is not None:
        return _result(request, "BLOCKED", chain_projection.owner_walk_token, "DRAFT_SEALED", package=package, walk_script_path=str(walk_script), layer_availability=carried)
    if chain_projection.owner_walk is None:
        # Defensive fail-closed guard (design §5.3 "require non-None owner_walk"): removable only with the seam contract.
        return _result(request, "BLOCKED", "walk.generation.blocked.walk-script", "DRAFT_SEALED", package=package, walk_script_path=str(walk_script), layer_availability=carried)
    try:
        owner_walk_bytes = render_owner_walk_document(package, generated_at=request.generated_at, owner_walk=chain_projection.owner_walk)
    except OwnerWalkGenerationError as error:
        return _result(request, "BLOCKED", str(error), "DRAFT_SEALED", package=package, walk_script_path=str(walk_script), layer_availability=carried)
    issued_round = _render(request, template, template, package, "ISSUED", stack, prewalk, walk_script_gate_receipt=walk_receipt)
    if issued_round is None or _has_unverified_gate_header(issued_round):
        return _result(request, "BLOCKED", "OWNER-MAY-WALK: no", "DRAFT_SEALED", package=package, layer_availability=carried)
    writes: list[str] = []
    _atomic(request.paths.prewalk_receipt_path, json.dumps(asdict(prewalk), sort_keys=True).encode() + b"\n")
    writes.append(str(request.paths.prewalk_receipt_path))
    _atomic(request.paths.current_round_path, qa_round)
    writes.append(str(request.paths.current_round_path))
    try:
        if not (request.paths.feedback_path.exists() and request.paths.feedback_path.read_bytes() == feedback):
            _atomic(request.paths.feedback_path, feedback)
            writes.append(str(request.paths.feedback_path))
    except OSError:
        return _result(request, "PARTIAL", "FEEDBACK-FILE BLOCKED", "QA_VERIFIED", package=package, writes=writes, layer_availability=carried)
    if owner_walk_target.exists():
        if owner_walk_target.read_bytes() != owner_walk_bytes:
            return _result(request, "BLOCKED", "walk.recovery.blocked.byte-drift", "QA_VERIFIED", package=package, writes=writes, layer_availability=carried)
    else:
        try:
            _atomic(owner_walk_target, owner_walk_bytes)
        except OSError:
            return _result(request, "PARTIAL", "OWNER-WALK-FILE BLOCKED", "QA_VERIFIED", package=package, writes=writes, layer_availability=carried)
        writes.append(str(owner_walk_target))
    _atomic(request.paths.current_round_path, issued_round)
    writes.append(str(request.paths.current_round_path))
    return _result(request, "ISSUED", "OWNER-MAY-WALK: yes", "ISSUED", invitation=True, package=package, writes=[*writes, str(request.paths.prep_receipt_path)], issued_sha=_sha(issued_round), layer_availability=carried)


def _request_from_json(value: Mapping[str, Any]) -> PrepareRequest:
    raw_paths = {key: Path(item) if item is not None else None for key, item in value["paths"].items()}
    owner_walk = raw_paths.pop("owner_walk_path", None)
    paths = PreparePaths(**{key: path for key, path in raw_paths.items() if path is not None}, owner_walk_path=owner_walk)
    return PrepareRequest(ticket=value["ticket"], paths=paths, canonical_manifest=value["canonical_manifest"], behavior_lock=value["behavior_lock"], selected_instrument=value["selected_instrument"], canary_path=value["canary_path"], generated_at=value["generated_at"], certification_source=value.get("certification_source", "jinfra-docker-recreate-v1"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--ticket", required=True); parser.add_argument("--request-json", required=True); parser.add_argument("--report", type=Path); parser.add_argument("--json-out", type=Path, required=True); parser.add_argument("--force-recreate", action="store_true", help="archive the existing sealed package and mint a fresh DRAFT_SEALED package from the request")
    args = parser.parse_args(argv); raw = sys.stdin.read() if args.request_json == "-" else Path(args.request_json).read_text(encoding="utf-8")
    request = _request_from_json(json.loads(raw)); result = run_prepare(request, report_path=args.report, force_recreate=args.force_recreate)
    # The receipt-less recovery outcome ("BLOCKED: PREP RECEIPT MISSING") writes no
    # receipt by contract, so json-out can only mirror a receipt that exists.
    if request.paths.prep_receipt_path.is_file():
        _atomic(args.json_out, request.paths.prep_receipt_path.read_bytes())
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
