"""Generic Shape 1 fact-event append and fold helpers for ."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, cast


JsonObject = dict[str, object]


EVENT_CLASS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_SCHEMA_VERSION = 1
DEFAULT_LINE_SIZE_LIMIT = 4096

_COMPAT_EVENT_FILES = {
    "plan_status": Path(".jswarm/ops/plan-status-events.ndjson"),
    "jinfra_effort": Path(".jswarm/ops/jinfra-effort.ndjson"),  # A/C 9
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _failure(
    *,
    event_class: str,
    event_path: Path | None = None,
    lock_path: Path | None = None,
    canonical_repo_root: Path | None = None,
    error: BaseException,
) -> JsonObject:
    return {
        "ok": False,
        "event_path": str(event_path) if event_path is not None else "",
        "lock_path": str(lock_path) if lock_path is not None else "",
        "canonical_repo_root": str(canonical_repo_root) if canonical_repo_root is not None else "",
        "event_class": event_class,
        "event_id": "",
        "bytes_written": 0,
        "line_count_delta": 0,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return result.stdout.strip()


def canonical_repo_root(repo_root: Path) -> Path:
    """Return the main checkout root for ``repo_root`` when git can identify it."""
    start = Path(repo_root).resolve()
    try:
        common_dir_raw = _run_git(start, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except Exception:
        return start

    common_dir = Path(common_dir_raw).resolve()
    if common_dir.name == ".git":
        return common_dir.parent.resolve()

    try:
        top_level = _run_git(start, "rev-parse", "--show-toplevel")
    except Exception:
        return start
    return Path(top_level).resolve()


def _event_rel_path(event_class: str) -> Path:
    return _COMPAT_EVENT_FILES.get(
        event_class,
        Path(".jswarm/ops") / f"{event_class}-events.ndjson",
    )


def _resolve_event_path(canonical_root: Path, event_class: str, event_file: str | Path | None) -> Path:
    if not EVENT_CLASS_RE.match(event_class):
        raise ValueError(f"Invalid event_class: {event_class!r}")

    ops_root = (canonical_root / ".jswarm" / "ops").resolve()
    if event_file is None:
        return canonical_root / _event_rel_path(event_class)

    registered = _COMPAT_EVENT_FILES.get(event_class)
    if registered is None:
        raise ValueError(f"event_file override is not registered for event_class {event_class!r}")

    candidate = Path(event_file)
    if not candidate.is_absolute():
        candidate = canonical_root / candidate
    candidate = candidate.resolve()
    if not (candidate == ops_root or ops_root in candidate.parents):
        raise ValueError(f"event_file must resolve under {ops_root}")
    registered_path = (canonical_root / registered).resolve()
    if candidate != registered_path:
        raise ValueError(f"event_file override must match registered path {registered}")
    return candidate


def _event_id_from(record: Mapping[str, object]) -> str:
    for key in ("event_id", "transition_event_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    payload = record.get("payload")
    if isinstance(payload, dict):
        payload_map = cast(Mapping[str, object], payload)
        for key in ("event_id", "transition_event_id"):
            value = payload_map.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _event_sort_key(indexed_event: tuple[int, JsonObject]) -> tuple[str, str, int]:
    line_number, event = indexed_event
    timestamp = event.get("timestamp_utc")
    if not isinstance(timestamp, str):
        timestamp = ""
    event_id = _event_id_from(event)
    return timestamp, event_id, line_number


def _metadata(event_path: Path, lines: list[str], events: list[JsonObject], parse_errors: list[JsonObject], event_class: str | None) -> JsonObject:
    raw = event_path.read_bytes() if event_path.exists() else b""
    stat = event_path.stat() if event_path.exists() else None
    latest = ""
    for event in events:
        value = event.get("timestamp_utc")
        if isinstance(value, str) and value > latest:
            latest = value
    source_mtime = ""
    generated_at = ""
    if stat is not None:
        source_mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        generated_at = source_mtime
    return {
        "deterministic_sort": ["timestamp_utc", "event_id_or_transition_event_id", "line_number"],
        "event_class": event_class,
        "events_applied": len(events),
        "events_skipped": len(lines) - len(events),
        "generated_at_utc": generated_at,
        "generated_by": "jswarm/fact_events/__init__.py",
        "latest_event_timestamp_utc": latest,
        "parse_errors": len(parse_errors),
        "parse_error_samples": parse_errors[:5],
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "source_event_mtime_utc": source_mtime,
        "source_event_path": str(event_path),
        "source_event_sha256": hashlib.sha256(raw).hexdigest(),
        "source_event_size_bytes": len(raw),
        "total_lines_read": len(lines),
    }


def append_event(
    repo_root: Path,
    event_class: str,
    record_or_payload: Mapping[str, object],
    *,
    event_file: str | Path | None = None,
    runtime: str = "claude",
    actor: str = "",
    timestamp_utc: str | None = None,
    fail_open: bool = True,
) -> JsonObject:
    canonical_root: Path | None = None
    event_path: Path | None = None
    lock_path: Path | None = None
    try:
        canonical_root = canonical_repo_root(Path(repo_root))
        event_path = _resolve_event_path(canonical_root, event_class, event_file)
        lock_path = event_path.with_suffix(".ndjson.lock")
        payload = dict(record_or_payload)
        event_id = str(payload.get("event_id") or payload.get("transition_event_id") or "")
        record: JsonObject = {
            "actor": actor,
            "event_class": event_class,
            "event_id": event_id,
            "payload": payload,
            "runtime": runtime,
            "schema_version": DEFAULT_SCHEMA_VERSION,
            "timestamp_utc": timestamp_utc or _utc_now_iso(),
        }
        if event_class == "plan_status":
            for key in ("ticket_key", "from_status", "to_status", "proof_source", "transition_event_id", "outcome"):
                if key in payload:
                    record[key] = payload[key]

        line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        if len(encoded) > DEFAULT_LINE_SIZE_LIMIT:
            raise ValueError(f"Event line exceeds {DEFAULT_LINE_SIZE_LIMIT} bytes")

        event_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            try:
                fd = os.open(event_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
                try:
                    bytes_written = os.write(fd, encoded)
                    if bytes_written != len(encoded):
                        raise OSError(f"Short event write: {bytes_written} of {len(encoded)} bytes")
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)

        stat = event_path.stat()
        return {
            "ok": True,
            "event_path": str(event_path),
            "lock_path": str(lock_path),
            "canonical_repo_root": str(canonical_root),
            "event_class": event_class,
            "event_id": event_id,
            "bytes_written": bytes_written,
            "line_count_delta": 1,
            "source_mtime": stat.st_mtime,
            "freshness_hint": {
                "source_event_size_bytes": stat.st_size,
                "source_event_mtime": stat.st_mtime,
            },
        }
    except Exception as exc:
        if not fail_open:
            raise
        return _failure(
            event_class=event_class,
            event_path=event_path,
            lock_path=lock_path,
            canonical_repo_root=canonical_root,
            error=exc,
        )


def fold_events(
    event_path: Path,
    read_model_path: Path,
    reducer: Callable[[list[JsonObject]], JsonObject],
    *,
    event_class: str | None = None,
    debounce_key: str | None = None,
    fail_open: bool = True,
) -> JsonObject:
    del debounce_key
    path = Path(event_path)
    output_path = Path(read_model_path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        parse_errors: list[JsonObject] = []
        indexed_events: list[tuple[int, JsonObject]] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                decoded = cast(object, json.loads(line))
            except json.JSONDecodeError as exc:
                parse_errors.append({"line_number": line_number, "error": str(exc)})
                continue
            if not isinstance(decoded, dict):
                parse_errors.append({"line_number": line_number, "error": "event is not a JSON object"})
                continue
            event = cast(JsonObject, decoded)
            if event_class is not None and event.get("event_class") != event_class:
                continue
            indexed_events.append((line_number, event))

        events = [event for _, event in sorted(indexed_events, key=_event_sort_key)]
        model = dict(reducer(events))
        _ = model.setdefault("_metadata", _metadata(path, lines, events, parse_errors, event_class))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(model, fh, ensure_ascii=False, indent=2, sort_keys=True)
            _ = fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, output_path)
        return {
            "ok": True,
            "event_path": str(path),
            "read_model_path": str(output_path),
            "event_class": event_class or "",
            "events_applied": len(events),
            "parse_errors": len(parse_errors),
        }
    except Exception as exc:
        if not fail_open:
            raise
        return {
            "ok": False,
            "event_path": str(path),
            "read_model_path": str(output_path),
            "event_class": event_class or "",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
