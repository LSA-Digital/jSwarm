"""Revisioned, field-owned defect backlog with read-only projections."""

from __future__ import annotations

import fcntl
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fix_localization import verify_effective_digest

STAGES: Final[tuple[str, ...]] = ("observation", "conviction", "assignment", "repair", "proof", "disposition")
FIELD_OWNERS: Final[dict[str, str]] = {
    "observation": "test",
    "scenario_provenance": "test",
    "conviction": "fix",
    "defect_class": "fix",
    "catch_stage": "fix",
    "cycle": "fix",
    "repair": "fix",
    "proof": "fix",
    "disposition": "fix",
    "authority_sha256": "fix",
    "closure_validation": "jclose",
}


@dataclass(frozen=True, slots=True)
class UpdateResult:
    status: str
    reason_code: str
    revision: int


def _temporary_path(backlog_path: Path) -> Path:
    return backlog_path.with_suffix(backlog_path.suffix + ".tmp")


def _unresolved_temporary_paths(backlog_path: Path) -> list[Path]:
    """Return legacy and per-writer CAS temporaries without attempting recovery."""
    candidates = [_temporary_path(backlog_path)]
    candidates.extend(backlog_path.parent.glob(f"{backlog_path.name}.*.tmp"))
    return sorted({path for path in candidates if path.exists()}, key=str)


def _load(backlog_path: Path) -> dict[str, object]:
    loaded = json.loads(backlog_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("schema") != "jswarm.fix-defect-backlog/v1":
        raise ValueError("invalid backlog")
    if not isinstance(loaded.get("revision"), int) or not isinstance(loaded.get("rows"), dict) or not isinstance(loaded.get("receipts"), list):
        raise ValueError("invalid backlog")
    return loaded


def _new_row(defect_id: str) -> dict[str, object]:
    return {
        "defect_id": defect_id, "stage": "observation", "receipts": [],
        "observation": None, "scenario_provenance": None, "conviction": None,
        "defect_class": None, "catch_stage": None, "cycle": None, "repair": None,
        "proof": None, "disposition": None, "authority_sha256": None,
        "closure_validation": None,
    }


def _result(status: str, reason_code: str, document: dict[str, object]) -> UpdateResult:
    return UpdateResult(status, reason_code, document["revision"] if isinstance(document.get("revision"), int) else 0)


def _lock_path(backlog_path: Path) -> Path:
    return backlog_path.with_suffix(backlog_path.suffix + ".lock")


def _locked_load(backlog_path: Path) -> dict[str, object]:
    """Read a committed document without the public `_load` seam used by test interleaves."""
    loaded = json.loads(backlog_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("schema") != "jswarm.fix-defect-backlog/v1":
        raise ValueError("invalid backlog")
    if not isinstance(loaded.get("revision"), int) or not isinstance(loaded.get("rows"), dict) or not isinstance(loaded.get("receipts"), list):
        raise ValueError("invalid backlog")
    return loaded


def apply_update(backlog_path: Path, *, owner: str, defect_id: str, fields: dict[str, object], to_stage: str, expected_revision: int, idempotency_key: str, receipt_id: str) -> UpdateResult:
    # Preserve the public load seam before locking so deterministic callers can model two
    # writers reading the same revision. The locked read below is the commit-time CAS check.
    try:
        _ = _load(backlog_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return UpdateResult("rejected", "unresolved_transaction", 0)

    try:
        with _lock_path(backlog_path).open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                document = _locked_load(backlog_path)
                if _unresolved_temporary_paths(backlog_path):
                    return _result("rejected", "unresolved_transaction", document)
                receipts = document["receipts"]
                assert isinstance(receipts, list)
                if any(isinstance(receipt, dict) and receipt.get("idempotency_key") == idempotency_key for receipt in receipts):
                    return _result("replayed", "replayed_idempotent", document)
                if owner not in set(FIELD_OWNERS.values()):
                    return _result("rejected", "unknown_owner", document)
                if any(FIELD_OWNERS.get(field) != owner for field in fields):
                    return _result("rejected", "cross_owner_field", document)
                revision = document["revision"]
                assert isinstance(revision, int)
                if expected_revision != revision:
                    return _result("rejected", "stale_revision", document)
                rows = document["rows"]
                assert isinstance(rows, dict)
                existing = rows.get(defect_id)
                row = existing if isinstance(existing, dict) else _new_row(defect_id)
                current_stage = row.get("stage")
                if current_stage not in STAGES or to_stage not in STAGES or STAGES.index(to_stage) not in {STAGES.index(current_stage), STAGES.index(current_stage) + 1}:
                    return _result("rejected", "illegal_transition", document)
                if not isinstance(row.get("receipts"), list):
                    return _result("rejected", "unresolved_transaction", document)
                rows[defect_id] = row
                row.update(fields)
                row["stage"] = to_stage
                row_receipts = row["receipts"]
                assert isinstance(row_receipts, list)
                row_receipts.append(receipt_id)
                next_revision = revision + 1
                document["revision"] = next_revision
                receipts.append({"receipt_id": receipt_id, "owner": owner, "defect_id": defect_id, "idempotency_key": idempotency_key, "revision": next_revision})
                temporary = backlog_path.with_name(f"{backlog_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
                try:
                    temporary.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                    os.replace(temporary, backlog_path)
                finally:
                    temporary.unlink(missing_ok=True)
                return UpdateResult("applied", "ok", next_revision)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError, json.JSONDecodeError):
        return UpdateResult("rejected", "unresolved_transaction", 0)


def recover(backlog_path: Path) -> dict[str, object]:
    unresolved = [str(path) for path in _unresolved_temporary_paths(backlog_path)]
    return {"schema": "jswarm.fix-backlog-recovery/v1", "status": "unresolved" if unresolved else "clean", "unresolved_paths": unresolved}


def _policy(policy_path: Path | None) -> dict[str, object] | None:
    if policy_path is None:
        return None
    document = json.loads(policy_path.read_text(encoding="utf-8"))
    return document if isinstance(document, dict) else None


def _project_root(backlog_path: Path) -> Path:
    for candidate in backlog_path.parents:
        if (candidate / ".jswarm").is_dir():
            return candidate
    raise ValueError("backlog is outside a project")


def _current_authorities(policy: dict[str, object], project_root: Path) -> set[str]:
    references: list[tuple[str, str]] = []
    envelope = policy.get("envelope_ref")
    if isinstance(envelope, dict) and isinstance(envelope.get("path"), str) and isinstance(envelope.get("sha256"), str):
        references.append((envelope["path"], envelope["sha256"]))
    cycle_refs = policy.get("cycle_refs")
    if isinstance(cycle_refs, list):
        for reference in cycle_refs:
            if isinstance(reference, dict) and isinstance(reference.get("path"), str) and isinstance(reference.get("effective_sha256"), str):
                references.append((reference["path"], reference["effective_sha256"]))
    current: set[str] = set()
    for path, digest in references:
        verdict = verify_effective_digest(manifest_path=path, cited_sha256=digest, project_root=project_root)
        if verdict.status == "verified":
            current.add(verdict.effective_sha256)
    return current


def project_status(backlog_path: Path, policy_path: Path | None) -> dict[str, object]:
    try:
        policy = _policy(policy_path)
    except (OSError, ValueError, json.JSONDecodeError):
        policy = None
    if policy is None or policy.get("status") != "active":
        return {"schema": "jswarm.fix-status-projection/v1", "state": "not_enrolled", "header": "not enrolled", "drift_rows": []}
    try:
        if _unresolved_temporary_paths(backlog_path):
            raise ValueError("unresolved transaction")
        document = _load(backlog_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"schema": "jswarm.fix-status-projection/v1", "state": "unknown", "header": "unknown", "drift_rows": []}
    try:
        current_authorities = _current_authorities(policy, _project_root(backlog_path))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"schema": "jswarm.fix-status-projection/v1", "state": "unknown", "header": "unknown", "drift_rows": []}
    drift: list[str] = []
    rows = document["rows"]
    assert isinstance(rows, dict)
    for defect_id, row in rows.items():
        if isinstance(row, dict) and row.get("stage") in {"repair", "proof"} and row.get("authority_sha256") not in current_authorities:
            drift.append(str(defect_id))
    state, header = ("drift", "FIX-CYCLE DRIFT") if drift else ("ok", "enrolled")
    return {"schema": "jswarm.fix-status-projection/v1", "state": state, "header": header, "drift_rows": drift}


def project_precompact(backlog_path: Path, policy_path: Path | None) -> dict[str, object]:
    policy_ref = str(policy_path) if policy_path is not None else None
    policy: dict[str, object] | None = None
    try:
        policy = _policy(policy_path)
        document = _load(backlog_path)
    except (OSError, ValueError, json.JSONDecodeError):
        document = {"ticket": None, "rows": {}}
    rows = document.get("rows", {})
    open_defects = [str(defect_id) for defect_id, row in rows.items() if isinstance(row, dict) and row.get("stage") != "disposition"] if isinstance(rows, dict) else []
    digests: list[str] = []
    if policy and isinstance(policy.get("envelope_ref"), dict) and isinstance(policy["envelope_ref"].get("sha256"), str):
        digests.append(policy["envelope_ref"]["sha256"])
    return {"schema": "jswarm.fix-precompact-projection/v1", "ticket": document.get("ticket"), "policy_ref": policy_ref, "backlog_ref": str(backlog_path), "contract_digests": digests, "open_defects": open_defects}


def _contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path escape")
    return root.resolve() / path


def main(arguments: list[str]) -> int:
    if not arguments:
        return 2
    command, *tail = arguments
    try:
        values = dict(zip(tail[::2], tail[1::2], strict=True))
        root = Path(values.pop("--project-root"))
        backlog_relative = values.pop("--backlog")
        backlog_path = _contained(root, backlog_relative)
        policy_relative = values.pop("--policy", None)
        policy_path = _contained(root, policy_relative) if policy_relative else None
        if command == "update":
            required = {"--owner", "--defect", "--to-stage", "--expected-revision", "--idempotency-key", "--receipt-id", "--fields"}
            if set(values) != required:
                raise ValueError("arguments")
            fields = json.loads(values["--fields"])
            if not isinstance(fields, dict):
                raise ValueError("fields")
            result = apply_update(backlog_path, owner=values["--owner"], defect_id=values["--defect"], fields=fields, to_stage=values["--to-stage"], expected_revision=int(values["--expected-revision"]), idempotency_key=values["--idempotency-key"], receipt_id=values["--receipt-id"])
            print(json.dumps({"schema": "jswarm.fix-backlog-update/v1", "status": result.status, "reason_code": result.reason_code, "revision": result.revision}, sort_keys=True))
            if result.status == "rejected":
                print(f"deny {result.reason_code}", file=sys.stderr)
                return 3
            return 0
        if values:
            raise ValueError("arguments")
        if command == "recover":
            result = recover(backlog_path)
            print(json.dumps(result, sort_keys=True))
            return 3 if result["status"] == "unresolved" else 0
        if command == "project-status":
            print(json.dumps(project_status(backlog_path, policy_path), sort_keys=True))
            return 0
        if command == "project-precompact":
            result = project_precompact(backlog_path, policy_path)
            result["policy_ref"] = policy_relative
            result["backlog_ref"] = backlog_relative
            print(json.dumps(result, sort_keys=True))
            return 0
        raise ValueError("command")
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
