"""Fail-open per-session active-ticket binding writer."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from pathlib import Path


def _session_id(value: str | None) -> str | None:
    """Return a non-path session identifier or ``None`` for a fail-open no-op."""
    if not isinstance(value, str):
        return None
    session_id = value.strip()
    if not session_id or session_id == "unknown":
        return None
    path = Path(session_id)
    if path.is_absolute() or len(path.parts) != 1 or session_id in {".", ".."}:
        return None
    return session_id


def _ticket(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    ticket = value.strip()
    return ticket or None


def _binding_version(data: object) -> int:
    """Return a stored binding version, treating absent/non-positive values as zero."""
    if isinstance(data, dict) and type(data.get("binding_version")) is int:
        version = data["binding_version"]
        if version > 0:
            return version
    return 0


def _read_binding(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _persist_binding(
    binding_path: Path,
    session_id: str,
    ticket: str,
    source: str,
    version: int,
) -> None:
    """Atomically replace a binding and sync both file and containing directory."""
    tmp_path: Path | None = None
    try:
        payload = json.dumps(
            {
                "session_id": session_id,
                "ticket": ticket,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": source,
                "binding_version": version,
            }
        ) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=binding_path.parent, delete=False
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, binding_path)
        tmp_path = None
        directory_fd = os.open(binding_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _write_locked(
    binding_path: Path,
    session_id: str,
    ticket: str,
    source: str,
    expected_version: int | None,
    *,
    require_existing_ticket: bool,
) -> int | None:
    current = _read_binding(binding_path)
    if require_existing_ticket and (
        current is None
        or current.get("session_id") != session_id
        or (current.get("ticket") or "").strip() != ticket
    ):
        return None

    current_version = _binding_version(current)
    if expected_version is not None and expected_version != current_version:
        return None

    new_version = current_version + 1
    _persist_binding(binding_path, session_id, ticket, source, new_version)
    return new_version


def _write(
    repo_root: Path,
    session_id: str | None,
    ticket: str | None,
    source: str,
    expected_version: int | None,
    *,
    require_existing_ticket: bool,
) -> int | None:
    sid = _session_id(session_id)
    key = _ticket(ticket)
    if sid is None or key is None or (
        expected_version is not None and type(expected_version) is not int
    ):
        return None

    try:
        sessions_directory = Path(repo_root) / ".jswarm" / "state" / "sessions"
        directory = sessions_directory / sid
        binding_path = directory / "active-ticket.json"
        # Reject known CAS/refresh no-ops before creating a lock or a session directory.
        current = _read_binding(binding_path)
        current_version = _binding_version(current)
        if (
            (expected_version is not None and expected_version != current_version)
            or (
                require_existing_ticket
                and (
                    current is None
                    or current.get("session_id") != sid
                    or (current.get("ticket") or "").strip() != key
                )
            )
        ):
            return None

        directory_is_new = not directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        if directory_is_new:
            sessions_fd = os.open(sessions_directory, os.O_RDONLY)
            try:
                os.fsync(sessions_fd)
            finally:
                os.close(sessions_fd)
        with (directory / ".active-ticket.lock").open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return _write_locked(
                    binding_path,
                    sid,
                    key,
                    source,
                    expected_version,
                    require_existing_ticket=require_existing_ticket,
                )
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except Exception:
        return None


def write(
    repo_root: Path,
    session_id: str | None,
    ticket: str | None,
    source: str,
    expected_version: int | None = None,
) -> int | None:
    """Atomically bind a session to a ticket and return its next version.

    The statusline and hooks are fail-open paths. Invalid/blank session ids, the
    sentinel ``unknown``, blank tickets, I/O failures, and stale CAS writes return
    ``None`` without raising.
    """
    return _write(
        repo_root,
        session_id,
        ticket,
        source,
        expected_version,
        require_existing_ticket=False,
    )


def refresh_if_bound_to(
    repo_root: Path,
    session_id: str | None,
    ticket: str | None,
    source: str,
    expected_version: int | None = None,
) -> int | None:
    """Refresh an existing same-ticket binding and return its next version.

    The binding check and CAS update share one session lock, so this operation can
    neither create a binding nor overwrite a lifecycle ticket switch. Errors and
    no-op guards fail open with ``None``.
    """
    return _write(
        repo_root,
        session_id,
        ticket,
        source,
        expected_version,
        require_existing_ticket=True,
    )
