"""Lean, owner-authorized practical cutover for a prepared UAT v2 round."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from jswarm import uat_feedback, uat_round_materialize
from jswarm.portal import server as decision_server


_IDENTITY_FIELDS = (
    "round_id", "package_id", "package_hash", "sealed_payload_sha256",
    "script_id", "script_hash", "certified_build_hash",
)
# The unified ledger's uat_package block has no sealed_payload_sha256 member
# (uat_feedback.UAT_PACKAGE_FIELDS), and uat_feedback._validated_ledger — the
# identity-equality gate that enforces this — compares exactly these
# six plus the ledger's top-level ticket.
_LEDGER_IDENTITY_FIELDS = tuple(
    field for field in _IDENTITY_FIELDS if field != "sealed_payload_sha256"
)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label}")
    return value


def _running_service(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, ValueError):
        return False
    return True


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _canonical_sha256(value: object) -> str:
    """Exact canonical-JSON digest used to bind acceptance evidence to its request."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_evidence_binds_request(evidence: Mapping[str, Any], request: Mapping[str, Any]) -> None:
    """Reject acceptance evidence that does not bind the exact request and step counts.

    Acceptance is only meaningful against the exact bytes reviewed. Any post-acceptance
    drift in wording, GWT clauses, or step counts must fail before anything is archived
    or written.
    """
    expected_digest = _canonical_sha256(request)
    if evidence.get("request_sha256") != expected_digest:
        raise ValueError(
            "acceptance evidence digest does not bind this request: "
            f"expected {expected_digest}, evidence recorded {evidence.get('request_sha256')!r}"
        )
    journeys = request.get("canonical_manifest", {}).get("journeys")
    if not isinstance(journeys, list) or not journeys:
        raise ValueError("acceptance evidence cannot bind a request without journeys")
    actual_counts = {
        str(journey.get("journey_id")): len(journey.get("steps") or [])
        for journey in journeys
    }
    if evidence.get("journey_step_counts") != actual_counts:
        raise ValueError(
            "acceptance evidence step counts do not match the request: "
            f"expected {actual_counts}, evidence recorded {evidence.get('journey_step_counts')!r}"
        )
    declared_total = (evidence.get("step_counts") or {}).get("total")
    if declared_total != sum(actual_counts.values()):
        raise ValueError(
            "acceptance evidence total step count does not match the request: "
            f"expected {sum(actual_counts.values())}, evidence recorded {declared_total!r}"
        )


def _seed_ledger_frontmatter(
    package: Mapping[str, Any], *, current_round_path: Path, feedback_path: Path, generated_at: str,
) -> dict[str, Any]:
    """Create the documented pre-round ledger shape from the accepted request."""
    ticket = str(package["ticket"])
    return {
        "schema_version": uat_feedback.SCHEMA_VERSION,
        "ticket": ticket,
        "generated_at": generated_at,
        "latest_test_report_path": f".jswarm/plans/{ticket}/{ticket}.uat.report.md",
        "overall_verdict": "DRAFT",
        "processing_state": "PENDING",
        "coverage": {
            "functional_total": 0, "functional_evidenced": 0,
            "nfr_total": 0, "nfr_evidenced": 0,
            "structural_total": 0, "structural_evidenced": 0,
        },
        "uat_package": {
            "current_round_path": str(current_round_path),
            **{field: package[field] for field in _LEDGER_IDENTITY_FIELDS},
            "latest_prewalk_receipt": "N/A",
            "latest_feedback_path": str(feedback_path),
            "package_state": "DRAFT",
        },
        "processing_log": [],
        "legacy_import_receipt": "N/A",
    }


def _initial_ledger_body() -> str:
    return "\n\n| " + " | ".join(uat_feedback.COLUMNS) + " |\n| " + " | ".join("---" for _ in uat_feedback.COLUMNS) + " |\n"


def _stamped_ledger_bytes(
    path: Path,
    package: Mapping[str, Any],
    *,
    current_round_path: Path,
    feedback_path: Path,
    generated_at: str,
) -> bytes:
    """Re-stamp an existing ledger, or seed the first round from its request."""
    if path.exists():
        try:
            frontmatter, body = uat_feedback._frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, uat_feedback.ValidationError) as exc:
            raise ValueError("invalid traceability ledger") from exc
    else:
        frontmatter = _seed_ledger_frontmatter(
            package,
            current_round_path=current_round_path,
            feedback_path=feedback_path,
            generated_at=generated_at,
        )
        body = _initial_ledger_body()
    ledger_package = frontmatter.get("uat_package")
    if (not isinstance(ledger_package, dict)
            or set(ledger_package) != uat_feedback.UAT_PACKAGE_FIELDS
            or frontmatter.get("ticket") != package["ticket"]):
        raise ValueError("invalid traceability ledger")
    stamped = dict(ledger_package)
    stamped.update({field: package[field] for field in _LEDGER_IDENTITY_FIELDS})
    stamped["package_state"] = "ISSUED"
    frontmatter["uat_package"] = stamped
    rendered = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, width=2 ** 20).rstrip()
    return ("---\n" + rendered + "\n---" + body).encode("utf-8")


def _preflight_rendered_artifacts(
    *, current_bytes: bytes, feedback_bytes: bytes, ledger_bytes: bytes,
    registration: dict[str, Any], current_relative: Path, feedback_relative: Path,
    staging_parent: Path,
) -> None:
    """Exercise the service's production parsers before touching live artifacts."""
    try:
        uat_feedback.parse_feedback_projection(feedback_bytes)
    except (uat_feedback.PackageValidationError, uat_feedback.ValidationError) as exc:
        raise ValueError(f"preflight feedback document parse failed: {exc}") from exc
    try:
        uat_feedback._frontmatter(ledger_bytes.decode("utf-8"))
    except (UnicodeDecodeError, uat_feedback.ValidationError) as exc:
        raise ValueError(f"preflight traceability ledger parse failed: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix=".uat-cutover-preflight-", dir=staging_parent) as directory:
        root = Path(directory)
        current_path = root / current_relative
        feedback_path = root / feedback_relative
        current_path.parent.mkdir(parents=True, exist_ok=True)
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_bytes(current_bytes)
        feedback_path.write_bytes(feedback_bytes)
        staged_registration = dict(registration)
        staged_registration["allowed_repo_root"] = str(root)
        try:
            decision_server.parse_active_round_source(staged_registration)
        except decision_server.ServiceError as exc:
            raise ValueError(
                f"preflight active round registration failed: {exc.code}: {exc.message}"
            ) from exc


def practical_cutover(command: dict[str, Any], *, after_write: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Atomically replace the six practical round artifacts after preflight.

    The traceability-ledger stamp is a peer of the other writes, not a follow-up an
    operator can forget: it is archived, ordered, and restored with the rest.

    The narrow restoration path is intentionally internal: this command has no rollback
    mode because a failed invocation restores the exact pre-cutover bytes itself.
    """
    if command.get("authorized_by") not in {"owner", "ticket-boss"}:
        raise ValueError("authorized_by must be owner or ticket-boss")
    if not isinstance(command.get("note"), str) or not command["note"].strip():
        raise ValueError("note is required")
    required_paths = (
        "request_path", "acceptance_evidence_path", "current_round_path", "feedback_path",
        "handoff_path", "receipt_path", "ledger_path", "config_path", "service_pid_path",
        "archive_dir",
    )
    if any(not isinstance(command.get(key), Path) for key in required_paths):
        raise ValueError("practical cutover paths are required")
    paths = {key: command[key] for key in required_paths}
    request = _read_json(paths["request_path"], "v2 request")
    evidence = _read_json(paths["acceptance_evidence_path"], "acceptance evidence")
    if (request.get("schema") != "jswarm.test-uat.practical-cutover-request/v1"
            or request.get("schema_version") != "1.0"
            or request.get("ticket") != command.get("round_review_id", "").split("/", 1)[0]
            or not isinstance(request.get("canonical_manifest"), dict)):
        raise ValueError("invalid v2 request")
    if (evidence.get("schema") != "jswarm.test-uat.practical-acceptance-evidence/v1"
            or evidence.get("verdict") != "ACCEPTED"
            or evidence.get("accepted_by") not in {"owner", "ticket-boss"}
            or evidence.get("round_review_id") != command.get("round_review_id")):
        raise ValueError("invalid acceptance evidence")
    _require_evidence_binds_request(evidence, request)
    uat_round_materialize.preflight_authored_manifest(request["canonical_manifest"])
    package = uat_round_materialize.build_canonical_package(request["canonical_manifest"])
    if package.get("schema_version") != "uat-canonical-package@2":
        raise ValueError("request must contain canonical v2 package")
    config = _read_json(paths["config_path"], "config")
    # `active_round_sources` is this config's own registration list. Nothing in
    # the documented /jUAT flow ever seeds it (the rendered config template
    # never includes the key), so a project's first-ever round must default it
    # to empty rather than treat an absent key as a fatal setup error -- an
    # explicitly-present non-list value is still rejected as a corrupt config.
    registrations = config.get("active_round_sources", [])
    if not isinstance(registrations, list):
        raise ValueError("active_round_sources in config must be a list")
    target_id = command.get("round_review_id")
    matches = [row for row in registrations if isinstance(row, dict) and row.get("round_review_id") == target_id]
    if len(matches) > 1:
        raise ValueError("duplicate matching registrations are invalid")
    if matches and _running_service(paths["service_pid_path"]):
        raise RuntimeError(
            f"cannot replace registered round {target_id!r} while the service is running; "
            "its visibility cannot be swapped atomically"
        )
    # A round owns its registration root. When this is the first registration,
    # its artifact directory provides a bounded root for the relative paths and
    # its form-event state, rather than requiring an operator-created row.
    registration_root = (
        Path(matches[0]["allowed_repo_root"]).resolve()
        if matches else paths["current_round_path"].parent.resolve()
    )
    try:
        current_round_relative = paths["current_round_path"].resolve().relative_to(registration_root)
        feedback_relative = paths["feedback_path"].resolve().relative_to(registration_root)
    except ValueError as exc:
        raise ValueError("round artifacts must remain within their registration root") from exc
    requested_artifact_paths = {
        paths["current_round_path"].resolve(),
        paths["feedback_path"].resolve(),
    }
    for row in registrations:
        if not isinstance(row, dict) or row.get("round_review_id") == target_id:
            continue
        claimed_root = row.get("allowed_repo_root")
        claimed_paths = (row.get("current_round_path"), row.get("feedback_path"))
        if (not isinstance(claimed_root, str)
                or not all(isinstance(path, str) for path in claimed_paths)):
            continue
        resolved_claims = {(Path(claimed_root) / path).resolve() for path in claimed_paths}
        if requested_artifact_paths & resolved_claims:
            raise ValueError(
                "round artifacts are already claimed by live round "
                f"{row['round_review_id']!r}; use distinct per-round paths"
            )
    archive = paths["archive_dir"]
    if archive.exists():
        raise ValueError(f"archive directory already exists: {archive}")
    live_names = ("current_round_path", "feedback_path", "handoff_path", "receipt_path", "ledger_path", "config_path")
    originals = {name: paths[name].read_bytes() if paths[name].exists() else None for name in live_names}
    archive_paths = {name.replace("_path", ""): archive / f"{name.replace('_path', '')}.prior" for name in live_names}
    current_bytes = (
        uat_round_materialize.render_canonical_package_block_bytes(package, package_state="ISSUED")
        + b"\n" + uat_round_materialize.render_normalized_package_annex_bytes(package)
    )
    feedback_bytes = uat_feedback.render_feedback_document(package, generated_at=str(evidence["accepted_at"]))
    ledger_bytes = _stamped_ledger_bytes(
        paths["ledger_path"], package,
        current_round_path=paths["current_round_path"],
        feedback_path=paths["feedback_path"],
        generated_at=str(evidence["accepted_at"]),
    )
    registration = {
        "schema": "jswarm.test-uat.active-round-source/v2", "schema_version": "2.0",
        "round_review_id": target_id, "ticket": package["ticket"],
        "allowed_repo_root": str(registration_root),
        "current_round_path": current_round_relative.as_posix(),
        "feedback_path": feedback_relative.as_posix(),
        "package_identity": {field: package[field] for field in _IDENTITY_FIELDS},
    }
    handoff = {
        "schema": "jswarm.test-uat.practical-handoff/v1", "schema_version": "1.0",
        "ticket": package["ticket"], "round_review_id": target_id,
        "accepted_by": evidence["accepted_by"], "accepted_at": evidence["accepted_at"],
        "step_counts": evidence["step_counts"], "package_id": package["package_id"],
    }
    receipt = {
        "schema": "jswarm.test-uat.practical-cutover-receipt/v1", "schema_version": "1.0",
        "ticket": package["ticket"], "round_review_id": target_id,
        "authorized_by": command["authorized_by"], "note": command["note"],
        "accepted_at": evidence["accepted_at"], "package_id": package["package_id"],
    }
    new_config = dict(config)
    new_config["active_round_sources"] = (
        [registration if row is matches[0] else row for row in registrations]
        if matches else [*registrations, registration]
    )
    _preflight_rendered_artifacts(
        current_bytes=current_bytes,
        feedback_bytes=feedback_bytes,
        ledger_bytes=ledger_bytes,
        registration=registration,
        current_relative=current_round_relative,
        feedback_relative=feedback_relative,
        staging_parent=paths["current_round_path"].parent,
    )
    write_order: list[str] = []
    try:
        archive.mkdir(parents=True, exist_ok=False)
        for label, archived in archive_paths.items():
            original = originals[f"{label}_path"]
            archived.write_bytes(original if original is not None else b"NO PRIOR ARTIFACT\n")
        paths["current_round_path"].write_bytes(current_bytes); write_order.append("current_round")
        if after_write: after_write("current_round")
        paths["feedback_path"].write_bytes(feedback_bytes); write_order.append("feedback")
        if after_write: after_write("feedback")
        _write_json(paths["handoff_path"], handoff); write_order.append("handoff")
        if after_write: after_write("handoff")
        _write_json(paths["receipt_path"], receipt); write_order.append("receipt")
        if after_write: after_write("receipt")
        paths["ledger_path"].write_bytes(ledger_bytes); write_order.append("ledger")
        if after_write: after_write("ledger")
        _write_json(paths["config_path"], new_config); write_order.append("config")
        if after_write: after_write("config")
    except BaseException:
        for name, content in originals.items():
            if content is None:
                paths[name].unlink(missing_ok=True)
            else:
                paths[name].write_bytes(content)
        shutil.rmtree(archive, ignore_errors=True)
        raise
    return {"status": "cutover-complete", "write_order": write_order, "archive_paths": {key: str(value) for key, value in archive_paths.items()}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uat-practical-cutover")
    parser.add_argument("practical-cutover", nargs="?")
    parser.add_argument("--authorized-by", choices=("owner", "ticket-boss"), required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--acceptance-evidence", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--current-round", required=True)
    parser.add_argument("--feedback", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--service-pid", required=True)
    parser.add_argument("--round-review-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = practical_cutover({
        "authorized_by": args.authorized_by, "note": args.note,
        "request_path": Path(args.request), "acceptance_evidence_path": Path(args.acceptance_evidence),
        "config_path": Path(args.config), "archive_dir": Path(args.archive_dir),
        "current_round_path": Path(args.current_round), "feedback_path": Path(args.feedback),
        "handoff_path": Path(args.handoff), "receipt_path": Path(args.receipt),
        "ledger_path": Path(args.ledger),
        "service_pid_path": Path(args.service_pid), "round_review_id": args.round_review_id,
    })
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    main()
