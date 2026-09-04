#!/usr/bin/env python3
"""Test-only, producer-faithful DEMO-391 live-fixture CLI.

This helper is deliberately not service authority: it materializes existing
package/feedback producers and invokes the existing feedback processor.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jswarm import uat_feedback
from jswarm.portal import server as server_mod
from jswarm.uat_round_materialize import (
    build_canonical_package,
    render_canonical_package_block_bytes,
    render_normalized_package_annex_bytes,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER_TEMPLATE = REPO_ROOT / "jswarm/tests/fixtures/uat_round_feedback/healthy-ledger.md"
STATE_SCHEMA = "jswarm.test-uat.live-fixture-state/v1"
_LAST_STDOUT = ""


class FixtureError(ValueError):
    """A state receipt or requested fixture path violates the test contract."""


def last_stdout() -> str:
    return _LAST_STDOUT


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _require_regular_write_target(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _require_regular_write_target(path: Path) -> None:
    if path.is_symlink():
        raise FixtureError(f"refusing symlink target: {path}")
    parent = path.parent
    if not parent.exists() or parent.is_symlink() or not parent.is_dir():
        raise FixtureError(f"output parent must be an existing real directory: {parent}")


def _prepare_root(root: Path) -> Path:
    root = root.absolute()
    existing = root
    while not existing.exists():
        existing = existing.parent
    if existing.is_symlink() or not existing.is_dir():
        raise FixtureError(f"root ancestor must be an existing real directory: {existing}")
    missing: list[Path] = []
    cursor = root
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir()
    if root.is_symlink() or not root.is_dir():
        raise FixtureError(f"root must be a real directory: {root}")
    return root.resolve(strict=True)


def _contained_regular(root: Path, path: Path) -> Path:
    if path.is_symlink():
        raise FixtureError(f"refusing symlink path: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise FixtureError(f"path must be a regular file inside fixture root: {path}")
    return resolved


def _state_path(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise FixtureError(f"state must be a regular JSON file: {path}")
    return path.resolve(strict=True)


def _record_digests(state: dict[str, Any]) -> None:
    root = Path(state["root"])
    current = _contained_regular(root, Path(state["paths"]["current_round"]))
    feedback = _contained_regular(root, Path(state["paths"]["feedback"]))
    state["digests"] = {"current_round": _sha256(current.read_bytes()), "feedback": _sha256(feedback.read_bytes())}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    _write_json(path, state)


def load_state(path: Path) -> dict[str, Any]:
    state_path = _state_path(path)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"state is unreadable JSON: {state_path}") from exc
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
        raise FixtureError("unsupported fixture state schema")
    required = {"schema", "root", "ticket", "round_id", "round_review_id", "paths", "digests", "baseline", "lifecycle", "commands"}
    if set(state) != required or not isinstance(state.get("paths"), dict) or not isinstance(state.get("baseline"), dict):
        raise FixtureError("fixture state has unsupported fields")
    root = Path(state["root"])
    if root.is_symlink() or not root.is_dir():
        raise FixtureError("fixture root is not a real directory")
    root = root.resolve(strict=True)
    state["root"] = str(root)
    for name in ("current_round", "feedback"):
        _contained_regular(root, Path(state["paths"].get(name, "")))
        encoded = state["baseline"].get(name)
        if not isinstance(encoded, str):
            raise FixtureError(f"missing baseline {name}")
        try:
            base64.b64decode(encoded.encode("ascii"), validate=True)
        except ValueError as exc:
            raise FixtureError(f"invalid baseline {name}") from exc
    return state


def preflight_state(state: dict[str, Any]) -> dict[str, Any]:
    root = Path(state["root"])
    current = _contained_regular(root, Path(state["paths"]["current_round"]))
    feedback = _contained_regular(root, Path(state["paths"]["feedback"]))
    registration_path = _state_path(Path(state["paths"]["registration"]))
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    expected_round_review_id = f"{state['ticket']}/{state['round_id']}"
    if (registration["round_review_id"] != expected_round_review_id
            or registration["round_review_id"] != state["round_review_id"]
            or registration["ticket"] != state["ticket"]
            or registration["allowed_repo_root"] != str(root)):
        raise FixtureError("registration identity does not match state")
    if state["lifecycle"] != {"round_state": "ISSUED", "feedback_state": "UNPROCESSED"}:
        raise FixtureError("preflight requires fresh ISSUED/UNPROCESSED state")
    current_projection = server_mod.uat_round_materialize.parse_canonical_round_projection(current.read_bytes())
    feedback_projection = uat_feedback.parse_feedback_projection(feedback.read_bytes())
    package = current_projection["normalized_package"]
    feedback_package = feedback_projection["normalized_package"]
    if not isinstance(package, dict) or not isinstance(feedback_package, dict):
        raise FixtureError("canonical package projection is invalid")
    if package != feedback_package or current_projection["journeys"] != package["journeys"]:
        raise FixtureError("round and feedback package/journey identity mismatch")
    if (package.get("ticket") != registration["ticket"]
            or feedback_package.get("ticket") != registration["ticket"]
            or package.get("round_id") != state["round_id"]
            or feedback_package.get("round_id") != state["round_id"]):
        raise FixtureError("registration ticket/round does not match canonical package")
    if current_projection["package_state"] != "ISSUED" or feedback_projection["processing_state"] != "UNPROCESSED":
        raise FixtureError("canonical lifecycle differs from fresh state")
    current_digest, feedback_digest = _sha256(current.read_bytes()), _sha256(feedback.read_bytes())
    if state["digests"] != {"current_round": current_digest, "feedback": feedback_digest}:
        raise FixtureError("state digests no longer match fixture bytes")
    return state


def _evidence_dir(path: Path) -> Path:
    return _prepare_root(path)


def _copy_evidence(evidence_dir: Path, prefix: str, state: dict[str, Any]) -> dict[str, str]:
    current = Path(state["paths"]["current_round"])
    feedback = Path(state["paths"]["feedback"])
    names = {"before_current": evidence_dir / f"{prefix}-before-current.md", "before_feedback": evidence_dir / f"{prefix}-before-feedback.md"}
    for name, destination in names.items():
        source = current if name.endswith("current") else feedback
        destination.write_bytes(source.read_bytes())
    return {name: str(path) for name, path in names.items()}


def _finalize_evidence(evidence: dict[str, str], evidence_dir: Path, prefix: str, state: dict[str, Any]) -> dict[str, str]:
    current = Path(state["paths"]["current_round"])
    feedback = Path(state["paths"]["feedback"])
    outputs = {"after_current": evidence_dir / f"{prefix}-after-current.md", "after_feedback": evidence_dir / f"{prefix}-after-feedback.md"}
    for name, destination in outputs.items():
        source = current if name.endswith("current") else feedback
        destination.write_bytes(source.read_bytes())
    return {**evidence, **{name: str(path) for name, path in outputs.items()}}


def _emit(value: dict[str, Any]) -> None:
    global _LAST_STDOUT
    _LAST_STDOUT = json.dumps(value, sort_keys=True)
    print(_LAST_STDOUT)


def _materialize_active_round(root: Path, ticket: str, round_id: str) -> dict[str, Any]:
    """Render a fresh active round from canonical producer inputs, not rewrites."""
    manifest_path = REPO_ROOT / "jswarm/tests/fixtures/uat_round_package/healthy-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise FixtureError("canonical producer manifest must be an object")
    manifest.update({"ticket": ticket, "round_id": round_id, "folder_path": f".jswarm/plans/{ticket}"})
    package = build_canonical_package(manifest)
    materialized_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    round_dir = root / ".jswarm" / "plans" / ticket
    round_dir.mkdir(parents=True, exist_ok=True)
    current_round_path = round_dir / f"{ticket}.UAT-CURRENT-ROUND.md"
    feedback_path = round_dir / f"{ticket}.uat-feedback.md"
    current_round = (
        render_canonical_package_block_bytes(package, package_state="ISSUED")
        + b"\n"
        + render_normalized_package_annex_bytes(package)
    )
    feedback = uat_feedback.render_feedback_document(package, generated_at=materialized_at)
    current_round_path.write_bytes(current_round)
    feedback_path.write_bytes(feedback)
    identity = {field: package[field] for field in (
        "round_id", "package_id", "package_hash", "sealed_payload_sha256", "script_id", "script_hash", "certified_build_hash",
    )}
    return {
        "package": package,
        "registration": {
            "schema": "jswarm.test-uat.active-round-source/v1", "schema_version": "1.0",
            "round_review_id": f"{ticket}/{round_id}", "ticket": ticket, "allowed_repo_root": str(root),
            "current_round_path": str(current_round_path.relative_to(root)), "feedback_path": str(feedback_path.relative_to(root)),
            "package_identity": identity, "x_extension": {},
        },
        "current_round_path": current_round_path,
        "feedback_path": feedback_path,
    }


def _seed_ledger_for_package(path: Path, package: dict[str, Any], current_round_path: Path) -> None:
    template = LEDGER_TEMPLATE.read_text(encoding="utf-8")
    _empty, frontmatter_raw, body = template.split("---", 2)
    frontmatter = yaml.safe_load(frontmatter_raw)
    if not isinstance(frontmatter, dict) or not isinstance(frontmatter.get("uat_package"), dict):
        raise FixtureError("canonical ledger template is invalid")
    frontmatter["ticket"] = package["ticket"]
    frontmatter["latest_test_report_path"] = f".jswarm/plans/{package['ticket']}/{package['ticket']}.uat.report.md"
    frontmatter["uat_package"] = {
        "current_round_path": str(current_round_path),
        **{field: package[field] for field in ("package_id", "package_hash", "script_id", "script_hash", "certified_build_hash", "round_id")},
        "latest_prewalk_receipt": "test-only fixture", "latest_feedback_path": "test-only fixture", "package_state": "ISSUED",
    }
    path.write_text("---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True) + "---" + body, encoding="utf-8")


def create(args: argparse.Namespace) -> dict[str, Any]:
    if args.ticket != "DEMO-391":
        raise FixtureError("test fixture is bound to DEMO-391")
    root = _prepare_root(Path(args.root))
    registration_path, config_path, state_path = (Path(args.registration_out).absolute(), Path(args.config_out).absolute(), Path(args.state_out).absolute())
    for path in (registration_path, config_path, state_path):
        _require_regular_write_target(path)
    if any(root.iterdir()):
        raise FixtureError("create requires an empty fixture root; use reset for an existing fixture")
    fixture = _materialize_active_round(root, args.ticket, args.round_id)
    registration = deepcopy(fixture["registration"])
    _write_json(registration_path, registration)
    publications = root / "publications"
    threads = root / "threads"
    service_store_root = root / "decision-service-store"
    publications.mkdir()
    threads.mkdir()
    service_store_root.mkdir()
    if service_store_root.is_symlink() or not service_store_root.is_dir() or (service_store_root / "current.json").exists():
        raise FixtureError("fixture service store must be an empty real directory")
    config = {"approved_roots": [str(root)], "publications_dir": str(publications), "threads_dir": str(threads), "service_store_root": str(service_store_root.resolve(strict=True)), "port": 8765, "bind_host": "127.0.0.1", "active_round_sources": [registration]}
    _write_json(config_path, config)
    current_bytes, feedback_bytes = fixture["current_round_path"].read_bytes(), fixture["feedback_path"].read_bytes()
    state = {"schema": STATE_SCHEMA, "root": str(root), "ticket": args.ticket, "round_id": args.round_id, "round_review_id": registration["round_review_id"], "paths": {"current_round": str(fixture["current_round_path"]), "feedback": str(fixture["feedback_path"]), "registration": str(registration_path), "config": str(config_path)}, "digests": {"current_round": _sha256(current_bytes), "feedback": _sha256(feedback_bytes)}, "baseline": {"current_round": base64.b64encode(current_bytes).decode("ascii"), "feedback": base64.b64encode(feedback_bytes).decode("ascii")}, "lifecycle": {"round_state": "ISSUED", "feedback_state": "UNPROCESSED"}, "commands": {"create": ["create", "--root", str(root), "--ticket", args.ticket, "--round-id", args.round_id], "preflight": ["preflight", "--state", str(state_path)], "reset": ["reset", "--state", str(state_path)], "process": ["process", "--state", str(state_path)]}}
    _save_state(state_path, state)
    return {"operation": "create", "state": str(state_path), "registration": str(registration_path), "config": str(config_path), "round_review_id": registration["round_review_id"], "digests": state["digests"]}


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    state = preflight_state(load_state(Path(args.state)))
    return {"operation": "preflight", "state": str(Path(args.state).absolute()), "round_review_id": state["round_review_id"], "digests": state["digests"], "lifecycle": state["lifecycle"]}


def reset(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(Path(args.state))
    state = load_state(state_path)
    root = Path(state["root"])
    for name in ("current_round", "feedback"):
        target = _contained_regular(root, Path(state["paths"][name]))
        target.write_bytes(base64.b64decode(state["baseline"][name]))
    state["lifecycle"] = {"round_state": "ISSUED", "feedback_state": "UNPROCESSED"}
    _record_digests(state)
    _save_state(state_path, state)
    return {"operation": "reset", "state": str(state_path), "digests": state["digests"]}


def mutate_feedback(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(Path(args.state))
    state = load_state(state_path)
    evidence_dir = _evidence_dir(Path(args.evidence_dir))
    evidence = _copy_evidence(evidence_dir, "feedback", state)
    feedback_path = Path(state["paths"]["feedback"])
    projection = uat_feedback.parse_feedback_projection(feedback_path.read_bytes())
    entries = deepcopy(projection["entries"])
    first = next(iter(entries))
    entries[first]["comment"] = "DEMO-391 deterministic external canonical feedback mutation."
    feedback_path.write_bytes(uat_feedback.render_feedback_update(feedback_path.read_bytes(), entries))
    before = state["digests"]["feedback"]
    _record_digests(state)
    _save_state(state_path, state)
    evidence = _finalize_evidence(evidence, evidence_dir, "feedback", state)
    return {"operation": "mutate-feedback", "before_sha256": before, "after_sha256": state["digests"]["feedback"], "evidence": evidence}


def mutate_round(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(Path(args.state))
    state = load_state(state_path)
    evidence_dir = _evidence_dir(Path(args.evidence_dir))
    evidence = _copy_evidence(evidence_dir, "round", state)
    current_path = Path(state["paths"]["current_round"])
    current_path.write_bytes(current_path.read_bytes() + b"\n")
    before = state["digests"]["current_round"]
    _record_digests(state)
    _save_state(state_path, state)
    evidence = _finalize_evidence(evidence, evidence_dir, "round", state)
    return {"operation": "mutate-round", "before_sha256": before, "after_sha256": state["digests"]["current_round"], "evidence": evidence}


def diff_authorized(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(Path(args.state))
    _ = _evidence_dir(Path(args.evidence_dir))
    paths = {name: Path(getattr(args, name)) for name in ("before_current", "before_feedback", "after_current", "after_feedback")}
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise FixtureError("authorized diff inputs must be regular files")
    current_unchanged = paths["before_current"].read_bytes() == paths["after_current"].read_bytes()
    before = uat_feedback.parse_feedback_projection(paths["before_feedback"].read_bytes())
    after = uat_feedback.parse_feedback_projection(paths["after_feedback"].read_bytes())
    authorized = before["normalized_package"] == after["normalized_package"] and before["processing_state"] == after["processing_state"] == "UNPROCESSED"
    if authorized:
        expected = uat_feedback.render_feedback_update(paths["before_feedback"].read_bytes(), after["entries"])
        authorized = expected == paths["after_feedback"].read_bytes()
    return {"operation": "diff-authorized", "round_review_id": state["round_review_id"], "current_round_unchanged": current_unchanged, "feedback_change_authorized": authorized, "before_feedback_sha256": _sha256(paths["before_feedback"].read_bytes()), "after_feedback_sha256": _sha256(paths["after_feedback"].read_bytes())}


def _candidate_from_feedback(feedback_path: Path) -> dict[str, Any]:
    projection = uat_feedback.parse_feedback_projection(feedback_path.read_bytes())
    package = projection["normalized_package"]
    journey = package["journeys"][0]
    entry = projection["entries"][journey["journey_id"]]
    return {"schema_version": "uat-feedback-candidates@1", "candidates": [{"feedback_id": f"fixture-{journey['journey_id']}", "ticket": package["ticket"], "round_id": package["round_id"], "package_id": package["package_id"], "package_hash": package["package_hash"], "sealed_payload_sha256": package["sealed_payload_sha256"], "script_id": package["script_id"], "script_hash": package["script_hash"], "certified_build_hash": package["certified_build_hash"], "journey_id": journey["journey_id"], "requirement_ref": journey["requirement_ref"], "source": "owner", "scenario_id": journey["scenario_id"], "uat_test_anchor": journey["uat_test_anchor"], "atom_ids": journey["atom_ids"], "scenario_outcome": entry["scenario_outcome"], "finding_severity": entry["finding_severity"], "finding_disposition": entry["finding_disposition"], "summary": "Deterministic DEMO-391 fixture processing proof.", "observed": "Canonical feedback entry is present.", "expected": "Canonical feedback entry is processable.", "optional_identities": {}, "omissions": {"primary_pe2e": "test-only fixture", "red_test": "test-only fixture", "task": "test-only fixture", "implementation": "existing behavior"}}]}


def process(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(Path(args.state))
    state = load_state(state_path)
    evidence_dir = _evidence_dir(Path(args.evidence_dir))
    root = Path(state["root"])
    feedback = _contained_regular(root, Path(state["paths"]["feedback"]))
    candidate = evidence_dir / "feedback-candidates.json"
    ledger = evidence_dir / "uat-feedback-ledger.md"
    _write_json(candidate, _candidate_from_feedback(feedback))
    if not ledger.exists():
        projection_for_ledger = uat_feedback.parse_feedback_projection(feedback.read_bytes())
        package_for_ledger = projection_for_ledger["normalized_package"]
        if not isinstance(package_for_ledger, dict):
            raise FixtureError("canonical feedback package is invalid")
        _seed_ledger_for_package(ledger, package_for_ledger, Path(state["paths"]["current_round"]))
    receipt = feedback.with_suffix(feedback.suffix + ".receipt.json")
    projection = uat_feedback.parse_feedback_projection(feedback.read_bytes())
    if projection["processing_state"] == "PROCESSED" and receipt.is_file():
        persisted = json.loads(receipt.read_text(encoding="utf-8"))
        result = {"status": "PROCESSED", "run_id": persisted["run_id"], "feedback_state": "PROCESSED", "idempotent": True}
    else:
        result = uat_feedback.process_feedback(feedback, candidate, ledger)
    _record_digests(state)
    state["lifecycle"]["feedback_state"] = "PROCESSED" if result.get("status") == "PROCESSED" else state["lifecycle"]["feedback_state"]
    _save_state(state_path, state)
    return {"operation": "process", "result": result, "artifacts": {"feedback": str(feedback), "candidate": str(candidate), "ledger": str(ledger), "receipt": str(receipt)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uat_round_live_fixture")
    commands = parser.add_subparsers(dest="operation", required=True)
    create_parser = commands.add_parser("create")
    for option in ("root", "ticket", "round-id", "registration-out", "config-out", "state-out"):
        create_parser.add_argument(f"--{option}", required=True)
    for name in ("preflight", "reset", "mutate-feedback", "mutate-round", "diff-authorized", "process"):
        child = commands.add_parser(name)
        child.add_argument("--state", required=True)
        if name in {"mutate-feedback", "mutate-round", "diff-authorized", "process"}:
            child.add_argument("--evidence-dir", required=True)
        if name == "diff-authorized":
            for option in ("before-current", "before-feedback", "after-current", "after-feedback"):
                child.add_argument(f"--{option}", required=True)
    args = parser.parse_args(argv)
    operation = {"create": create, "preflight": preflight, "reset": reset, "mutate-feedback": mutate_feedback, "mutate-round": mutate_round, "diff-authorized": diff_authorized, "process": process}[args.operation]
    try:
        _emit(operation(args))
    except FixtureError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
