"""A/C 2: per-project plan-status registry/read model.

- Atomic writes (tmp + os.replace) within an advisory fcntl.flock.
- transition_event_id dedupe (A/C 5 / Oracle Concern #4).
- Registry is a per-checkout CACHE; plan-file frontmatter is canonical
  (spec §3 / Oracle Concern #3). Reconciler rebuilds this from frontmatter.

Schema is additive-compatible: unknown top-level / per-ticket keys are preserved
on read+write so older readers tolerate newer writers (A/C 3 / Oracle low-pri rec).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, cast

from . import state as S

SCHEMA_URI = "https://lsa.dev/schemas/plan-status/v1.json"
SCHEMA_VERSION = 1
REGISTRY_FILENAME = ".jPlanStatus.json"
CANONICAL_READ_MODEL_REL = Path(".jswarm/ops/read-models/plan-status.json")
HISTORY_CAP = 50
LOCK_TIMEOUT_SECONDS = 5.0

__all__ = [
    "SCHEMA_URI",
    "SCHEMA_VERSION",
    "REGISTRY_FILENAME",
    "CANONICAL_READ_MODEL_REL",
    "canonical_read_model_path",
    "registry_path",
    "load_registry",
    "save_registry",
    "fold_plan_status_events",
    "transition_event_id",
    "record_transition",
    "set_cache",
    "LockTimeout",
]


class LockTimeout(RuntimeError):
    """Raised when the advisory registry lock cannot be acquired in time."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def registry_path(plans_dir: Path) -> Path:
    plans_dir = Path(plans_dir)
    if plans_dir.name == "plans" and plans_dir.parent.name in {"docs", ".jswarm"}:
        return _repo_root_from_plans_dir(plans_dir) / "docs" / "plans" / REGISTRY_FILENAME
    return plans_dir / REGISTRY_FILENAME


def _repo_root_from_plans_dir(plans_dir: Path) -> Path:
    plans_dir = Path(plans_dir)
    if plans_dir.name == "plans" and plans_dir.parent.name in {"docs", ".jswarm"}:
        return plans_dir.parent.parent
    return plans_dir


def canonical_read_model_path(plans_dir: Path) -> Path:
    return _repo_root_from_plans_dir(plans_dir) / CANONICAL_READ_MODEL_REL


def plan_status_events_path(repo_root_or_plans_dir: Path) -> Path:
    repo_root = _repo_root_from_plans_dir(Path(repo_root_or_plans_dir))
    return repo_root / ".jswarm" / "ops" / "plan-status-events.ndjson"


def _registry_load_paths(plans_dir: Path) -> list[Path]:
    canonical = canonical_read_model_path(plans_dir)
    legacy = registry_path(plans_dir)
    return [canonical] if canonical == legacy else [canonical, legacy]


def _direct_registry_storage_paths(plans_dir: Path) -> list[Path]:
    return [registry_path(plans_dir)]


def _empty_registry(project_key: str) -> dict[str, Any]:
    return {
        "$schema": SCHEMA_URI,
        "schema_version": SCHEMA_VERSION,
        "project_key": project_key,
        "generated_by": "jswarm/plan_status",
        "last_updated": _utc_now_iso(),
        "tickets": {},
        "jira_retry_queue": [],
    }


def load_registry(plans_dir: Path, *, project_key: str = "") -> dict[str, Any]:
    """Load the registry, returning an empty skeleton if the file is absent.

    Unknown keys are preserved (additive compatibility).
    """
    data: dict[str, Any] | None = None
    for path in _registry_load_paths(plans_dir):
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = cast(dict[str, Any], json.load(fh))
            break
        except (json.JSONDecodeError, OSError):
            # Corrupted projection: try the next compatibility surface before
            # healing from frontmatter via the caller.
            continue
    if data is None:
        return _empty_registry(project_key)
    data.setdefault("tickets", {})
    data.setdefault("jira_retry_queue", [])
    data.setdefault("schema_version", SCHEMA_VERSION)
    if project_key and not data.get("project_key"):
        data["project_key"] = project_key
    return data


def _acquire_lock(lock_fh, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"Could not acquire registry lock within {timeout}s"
                )
            time.sleep(0.05)


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    """Serialize ``data`` to ``path`` via tmp + os.replace. Caller holds the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


def _write_registry_files(plans_dir: Path, data: dict[str, Any]) -> None:
    data["last_updated"] = _utc_now_iso()
    for path in _direct_registry_storage_paths(plans_dir):
        _write_atomic(path, data)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


def _event_value(event: dict[str, Any], key: str) -> Any:
    payload = event.get("payload")
    if isinstance(payload, dict) and key in payload:
        return payload[key]
    return event.get(key)


def _reduce_plan_status_events(events: list[dict[str, Any]], project_key: str) -> dict[str, Any]:
    data = _empty_registry(project_key)
    data["generated_by"] = "jswarm/plan_status/registry.py:fold_plan_status_events"
    tickets = cast(dict[str, dict[str, Any]], data["tickets"])
    seen_event_ids: set[str] = set()
    pending_events: list[dict[str, Any]] = []
    latest_timestamp = ""
    for event in events:
        outcome = _event_value(event, "outcome")
        if outcome != "applied":
            continue
        event_id = _event_value(event, "transition_event_id") or _event_value(event, "event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen_event_ids:
            continue
        ticket = _event_value(event, "ticket_key")
        to_status = _event_value(event, "to_status")
        if not isinstance(ticket, str) or not isinstance(to_status, str) or not S.is_valid_state(to_status):
            continue
        seen_event_ids.add(event_id)
        pending_events.append(event)

    progress = True
    while progress:
        progress = False
        next_pending: list[dict[str, Any]] = []
        for event in pending_events:
            ticket = cast(str, _event_value(event, "ticket_key"))
            entry = tickets.get(ticket)
            current_status = entry.get("plan_status", S.NULL_STATE) if entry else S.NULL_STATE
            from_status = _event_value(event, "from_status")
            if not isinstance(from_status, str):
                from_status = S.NULL_STATE
            has_predecessor_pending = any(
                other is not event
                and _event_value(other, "ticket_key") == ticket
                and _event_value(other, "to_status") == from_status
                for other in pending_events
            )
            has_asserted_initial_state = (
                entry is None
                and from_status != S.NULL_STATE
                and S.is_valid_state(from_status)
                and not has_predecessor_pending
            )
            if from_status != current_status and not has_asserted_initial_state:
                next_pending.append(event)
                continue
            progress = True
            to_status = cast(str, _event_value(event, "to_status"))
            event_id = cast(str, _event_value(event, "transition_event_id") or _event_value(event, "event_id"))
            entry = tickets.setdefault(ticket, {"history": []})
            actor = event.get("actor")
            proof_source = _event_value(event, "proof_source")
            ts = event.get("timestamp_utc")
            if isinstance(ts, str) and ts > latest_timestamp:
                latest_timestamp = ts
            history_item: dict[str, Any] = {
                "ts": ts if isinstance(ts, str) else "",
                "from": from_status,
                "to": to_status,
                "actor": actor if isinstance(actor, str) else "",
                "proof_source": proof_source if isinstance(proof_source, str) else "",
                "transition_event_id": event_id,
            }
            entry.setdefault("history", []).append(history_item)
            if len(entry["history"]) > HISTORY_CAP:
                entry["history"] = entry["history"][-HISTORY_CAP:]
            entry["plan_status"] = to_status
            entry["status"] = S.derive_merge_status(to_status)
            entry["actor"] = history_item["actor"]
            entry["last_updated"] = history_item["ts"]
            plan_file = _event_value(event, "plan_file")
            if isinstance(plan_file, str) and plan_file:
                entry["plan_file"] = plan_file
        pending_events = next_pending

    if latest_timestamp:
        data["last_updated"] = latest_timestamp
    return data


def _fact_events_fold() -> Callable[..., dict[str, Any]]:
    return cast(Callable[..., dict[str, Any]], import_module("fact_events").fold_events)


def _fact_events_canonical_root() -> Callable[[Path], Path]:
    return cast(Callable[[Path], Path], import_module("fact_events").canonical_repo_root)


def fold_plan_status_events(
    repo_root_or_plans_dir: Path,
    *,
    project_key: str = "",
    fail_open: bool = True,
) -> dict[str, Any]:
    repo_root = _repo_root_from_plans_dir(Path(repo_root_or_plans_dir))
    canonical_root = _fact_events_canonical_root()(repo_root)
    plans_dir = canonical_root / "docs" / "plans"
    event_path = canonical_root / ".jswarm" / "ops" / "plan-status-events.ndjson"
    read_model_path = canonical_read_model_path(plans_dir)

    result = _fact_events_fold()(
        event_path,
        read_model_path,
        lambda events: _reduce_plan_status_events(events, project_key),
        event_class="plan_status",
        fail_open=fail_open,
    )
    if result.get("ok"):
        legacy = registry_path(plans_dir)
        legacy.parent.mkdir(parents=True, exist_ok=True)
        try:
            _write_bytes_atomic(legacy, read_model_path.read_bytes())
        except OSError:
            if not fail_open:
                raise
            result = {**result, "legacy_write_error": True}
    return result


def save_registry(plans_dir: Path, data: dict[str, Any], *, timeout: float = LOCK_TIMEOUT_SECONDS) -> Path:
    """Atomically write the registry under an advisory exclusive lock.

    Standalone save (e.g. reconciler full rebuild). For read-modify-write under a
    single lock, use ``record_transition`` which holds the lock across load+save.
    """
    plans_dir = Path(plans_dir)
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = registry_path(plans_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with open(lock_path, "w") as lock_fh:
        _acquire_lock(lock_fh, timeout)
        try:
            _write_registry_files(plans_dir, data)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
    return path


def set_cache(
    plans_dir: Path,
    *,
    ticket: str,
    plan_status: str,
    plan_file: str = "",
    source: str = "reconcile",
    project_key: str = "",
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Reconcile the registry CACHE to a frontmatter-canonical value.

    Unlike ``record_transition`` this performs NO transition validation — the
    plan-file frontmatter is canonical (Oracle Concern #3), so the registry cache
    simply mirrors it. Used by the post-edit reconcile hook and the reconciler.
    Idempotent when the cache already matches. Validates that ``plan_status`` is a
    recognized value (skips silently otherwise — frontmatter may be mid-edit).
    """
    if not S.is_valid_state(plan_status) or plan_status == S.NULL_STATE:
        return {"action": "skipped", "reason": "invalid-or-null-state", "to": plan_status}

    plans_dir = Path(plans_dir)
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = registry_path(plans_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with open(lock_path, "w") as lock_fh:
        _acquire_lock(lock_fh, timeout)
        try:
            data = load_registry(plans_dir, project_key=project_key)
            tickets = cast(dict[str, dict[str, Any]], data["tickets"])
            entry = tickets.get(ticket)
            if entry is not None and entry.get("plan_status") == plan_status:
                return {"action": "idempotent", "to": plan_status}

            now = _utc_now_iso()
            if entry is None:
                entry = cast(dict[str, Any], {"history": []})
                tickets[ticket] = entry
            prev = entry.get("plan_status", S.NULL_STATE)
            entry["plan_status"] = plan_status
            entry["status"] = S.derive_merge_status(plan_status)
            entry["actor"] = source
            entry["last_updated"] = now
            # Cache reconcile is NOT a fact transition: record only a reconcile
            # marker, never append to `history` (history holds fact transitions
            # from record_transition only — Critic M3). NDJSON is not emitted here.
            entry["last_reconciled"] = now
            entry["reconciled_from"] = prev
            if plan_file:
                entry["plan_file"] = plan_file
            _write_registry_files(plans_dir, data)
            return {"action": "synced", "from": prev, "to": plan_status}
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def transition_event_id(
    ticket: str,
    from_status: str,
    to_status: str,
    actor: str,
    proof_source: str,
    proof_sha: str = "",
) -> str:
    """Deterministic dedupe id for a logical transition (Oracle Concern #4)."""
    payload = "|".join([ticket, from_status, to_status, actor, proof_source, proof_sha])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _recent_event_ids(ticket_entry: dict[str, Any]) -> set[str]:
    return {
        h.get("transition_event_id", "")
        for h in ticket_entry.get("history", [])
    }


def record_transition(
    plans_dir: Path,
    *,
    ticket: str,
    to_status: str,
    actor: str,
    proof_source: str,
    plan_file: str = "",
    proof_sha: str = "",
    manual: bool = False,
    reason: str = "",
    project_key: str = "",
    from_status_override: str | None = None,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Apply a fact transition to the registry with validation + dedupe.

    Returns a result dict: {action: 'applied'|'noop'|'idempotent', from, to,
    transition_event_id, status}. Raises InvalidTransitionError for illegal /
    manual-without-override transitions (the registry refuses them).

    ``from_status_override`` lets a caller assert the canonical pre-state when the
    registry cache lags behind frontmatter (e.g. the post-edit hook reads
    frontmatter=0.lite_init but the registry entry is absent), so the recorded
    transition is a single honest fact rather than a synthetic cache-seed plus a
    transition (Critic M3 / m1).

    SAFETY (Critic re-review): the override is honored ONLY when it cannot contradict
    a real recorded state — i.e. when there is no existing entry, OR the override
    equals the existing entry's plan_status. If an existing entry disagrees with the
    override, the override is IGNORED and the registry's real current state is used,
    so the override can never be used to launder an otherwise-illegal transition past
    validation. validate_transition still runs on the resolved from_status either way.

    The advisory lock is held across the full load -> modify -> write cycle so
    concurrent writers on different tickets do not lose updates (Oracle Concern #4).
    """
    plans_dir = Path(plans_dir)
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = registry_path(plans_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with open(lock_path, "w") as lock_fh:
        _acquire_lock(lock_fh, timeout)
        try:
            data = load_registry(plans_dir, project_key=project_key)
            tickets = cast(dict[str, dict[str, Any]], data["tickets"])
            entry = tickets.get(ticket)
            real_current = entry["plan_status"] if entry else S.NULL_STATE
            if (
                from_status_override is not None
                and (entry is None or real_current == from_status_override)
            ):
                from_status = from_status_override
            else:
                # Override absent, or contradicts a real recorded state -> ignore it.
                from_status = real_current

            cls = S.validate_transition(from_status, to_status, manual=manual)

            event_id = transition_event_id(
                ticket, from_status, to_status, actor, proof_source, proof_sha
            )

            # No-op short-circuits (no write, no history growth):
            #  - idempotent: from == to (same canonical state).
            #  - dedupe: already at target AND this exact event already recorded
            #    (defends against a logical transition replayed across the lock window).
            if cls == "idempotent":
                return {
                    "action": "idempotent",
                    "from": from_status,
                    "to": to_status,
                    "transition_event_id": event_id,
                    "status": S.derive_merge_status(to_status),
                }
            if entry and entry.get("plan_status") == to_status and event_id in _recent_event_ids(entry):
                return {
                    "action": "noop",
                    "from": from_status,
                    "to": to_status,
                    "transition_event_id": event_id,
                    "status": S.derive_merge_status(to_status),
                }

            derived_status = S.derive_merge_status(to_status)
            now = _utc_now_iso()

            if entry is None:
                entry = cast(dict[str, Any], {"history": []})
                tickets[ticket] = entry

            history_item = {
                "ts": now,
                "from": from_status,
                "to": to_status,
                "actor": actor,
                "proof_source": proof_source,
                "transition_event_id": event_id,
            }
            if manual and reason:
                history_item["reason"] = reason
            entry.setdefault("history", []).append(history_item)
            if len(entry["history"]) > HISTORY_CAP:
                entry["history"] = entry["history"][-HISTORY_CAP:]

            entry["plan_status"] = to_status
            entry["status"] = derived_status
            entry["actor"] = actor
            entry["last_updated"] = now
            if plan_file:
                entry["plan_file"] = plan_file

            _write_registry_files(plans_dir, data)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)

    return {
        "action": "idempotent" if cls == "idempotent" else "applied",
        "from": from_status,
        "to": to_status,
        "transition_event_id": event_id,
        "status": derived_status,
    }
