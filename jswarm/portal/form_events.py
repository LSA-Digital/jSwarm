"""Durable, privacy-safe UAT form-action events and non-consuming notifications."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Literal, Mapping, Sequence

from jswarm.portal import consumer

EVENT_SCHEMA = "jswarm.test-uat.form-action-event/v1"
WATCH_SCHEMA = "jswarm.test-uat.form-watch-arm/v1"
SIGNAL_SCHEMA = "jswarm.event-notify.signal/v1"
PRACTICE_RECEIPT_SCHEMA = "jswarm.test-uat.practice-receipt/v1"
EVENTS_DIRNAME = "form-events"
LEDGER_NAME = "consumed.ndjson"

_NEXT_STEP = {
    "feedback.send": "test-uat-feedback-process",
    "decision.approve": "fix-decision-apply",
}
_ALLOWED_EVENT_FIELDS = frozenset({
    "schema", "schema_version", "event_id", "ticket", "round_review_id",
    "action", "actor", "occurred_at_utc", "object_sha256",
    "prior_object_sha256", "next_lifecycle_step",
})


class FormEventError(RuntimeError):
    """A form-action event could not be armed, committed, or delivered."""


def _slug(round_review_id: str) -> str:
    return hashlib.sha256(round_review_id.encode("utf-8")).hexdigest()[:16]


def _root(state_root: os.PathLike[str] | str, round_review_id: str) -> Path:
    return Path(state_root) / EVENTS_DIRNAME / _slug(round_review_id)


def events_path(state_root, round_review_id: str) -> Path:
    return _root(state_root, round_review_id) / "events.ndjson"


def watch_state_path(state_root, round_review_id: str) -> Path:
    return _root(state_root, round_review_id) / "watch.json"


def consumer_state_dir(state_root, round_review_id: str) -> Path:
    return _root(state_root, round_review_id) / "consumer"


def practice_receipts_path(state_root, round_review_id: str) -> Path:
    """Private receipt ledger for safe practice rounds, never an event source."""
    return _root(state_root, round_review_id) / "practice-receipts.ndjson"


def next_lifecycle_step(action: str) -> str:
    try:
        return _NEXT_STEP[action]
    except KeyError as exc:
        raise FormEventError(f"unmapped form action: {action!r}") from exc


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    try:
        written = 0
        while written < len(line):
            written += os.write(fd, line[written:])
        os.fsync(fd)
    finally:
        os.close(fd)


def _form_watch_command(state_root, round_review_id: str, *, once: bool) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    command = (
        f"cd {shlex.quote(str(repo_root))} && "
        f"exec {shlex.quote(str(repo_root / '.venv/bin/python'))} -u "
        "-m jswarm.portal.form_events watch "
        f"--state-root {shlex.quote(str(Path(state_root).resolve()))} "
        f"--round-review-id {shlex.quote(round_review_id)} "
        "--action feedback.send --poll-seconds 2"
    )
    return f"{command} --once 2>&1" if once else f"{command} 2>&1"


def arm_form_watch(state_root, *, round_review_id: str, ticket: str) -> dict[str, Any]:
    """Durably register an active form-event source; no live task is claimed."""
    path = watch_state_path(state_root, round_review_id)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = None
        if isinstance(existing, dict) and existing.get("status") in {"active", "closed"}:
            return existing
    record = {
        "schema": WATCH_SCHEMA,
        "schema_version": "1.0",
        "ticket": ticket,
        "round_review_id": round_review_id,
        "status": "active",
        "armed_at_utc": _now_utc(),
        "events_path": str(events_path(state_root, round_review_id)),
        "consumer_state_dir": str(consumer_state_dir(state_root, round_review_id)),
        "notify_command": _form_watch_command(state_root, round_review_id, once=False),
        "notify_once_command": _form_watch_command(state_root, round_review_id, once=True),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return record


def read_form_watch(state_root, round_review_id: str) -> dict[str, Any] | None:
    path = watch_state_path(state_root, round_review_id)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return record if isinstance(record, dict) else None


def emit_form_action_event(
    state_root,
    *,
    round_review_id: str,
    ticket: str,
    action: str,
    actor: str,
    object_sha256: str,
    prior_object_sha256: str | None = None,
    **ignored: Any,
) -> dict[str, Any]:
    """Commit one immutable privacy-safe event after canonical feedback saves."""
    watch = read_form_watch(state_root, round_review_id)
    if watch is None:
        raise FormEventError(f"no durable source registration for {round_review_id}; present the form before emitting")
    if not (isinstance(object_sha256, str) and len(object_sha256) == 64):
        raise FormEventError("object_sha256 must be a sha256 hex digest")
    event = {
        "schema": EVENT_SCHEMA,
        "schema_version": "1.0",
        "ticket": ticket,
        "round_review_id": round_review_id,
        "action": action,
        "actor": actor,
        "occurred_at_utc": _now_utc(),
        "object_sha256": object_sha256,
        "prior_object_sha256": prior_object_sha256,
        "next_lifecycle_step": next_lifecycle_step(action),
    }
    event["event_id"] = "fae_" + hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    stray = set(event) - _ALLOWED_EVENT_FIELDS
    if stray:
        raise FormEventError(f"event carries unexpected fields: {sorted(stray)}")
    _append_line(events_path(state_root, round_review_id), event)
    return event


def is_agent_eligible(record: Mapping[str, Any]) -> bool:
    """True only for the closed form-event contract the ticket consumer accepts."""
    return (
        record.get("schema") == EVENT_SCHEMA
        and record.get("schema_version") == "1.0"
        and isinstance(record.get("event_id"), str)
        and record["event_id"].startswith("fae_")
        and record.get("action") == "feedback.send"
        and record.get("next_lifecycle_step") == "test-uat-feedback-process"
    )


def record_practice_receipt(
    state_root, *, round_review_id: str, ticket: str, object_sha256: str,
    prior_object_sha256: str | None,
) -> dict[str, Any]:
    """Commit an immutable, non-routable acknowledgement for a practice submit."""
    existing = read_practice_receipts(state_root, round_review_id)
    for receipt in existing:
        if receipt.get("object_sha256") == object_sha256:
            return receipt
    receipt = {
        "schema": PRACTICE_RECEIPT_SCHEMA,
        "schema_version": "1.0",
        "receipt_id": "practice_" + hashlib.sha256(
            f"{round_review_id}:{object_sha256}".encode("utf-8")
        ).hexdigest()[:32],
        "ticket": ticket,
        "round_review_id": round_review_id,
        "created_at_utc": _now_utc(),
        "object_sha256": object_sha256,
        "prior_object_sha256": prior_object_sha256,
        "purpose": "safe-practice-round",
    }
    _append_line(practice_receipts_path(state_root, round_review_id), receipt)
    return receipt


def read_practice_receipts(state_root, round_review_id: str) -> list[dict[str, Any]]:
    path = practice_receipts_path(state_root, round_review_id)
    if not path.exists():
        return []
    receipts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema") == PRACTICE_RECEIPT_SCHEMA:
            receipts.append(row)
    return receipts


def read_form_events(state_root, round_review_id: str) -> list[dict[str, Any]]:
    path = events_path(state_root, round_review_id)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema") == EVENT_SCHEMA:
            events.append(row)
    return events


def _consumed_keys(state_root, round_review_id: str) -> set[str]:
    ledger = consumer_state_dir(state_root, round_review_id) / LEDGER_NAME
    if not ledger.exists():
        return set()
    keys = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            keys.add(json.loads(line)["event_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return keys


def pending_form_events(state_root, round_review_id: str) -> list[dict[str, Any]]:
    """Events the orchestrator can discover without consuming them."""
    consumed = _consumed_keys(state_root, round_review_id)
    return [row for row in read_form_events(state_root, round_review_id) if is_agent_eligible(row) and row.get("event_id") not in consumed]


def _signal(event: Mapping[str, Any], state_root, round_review_id: str) -> dict[str, Any]:
    return {
        "schema": SIGNAL_SCHEMA,
        "schema_version": "1.0",
        "signal": "pending",
        "notification_id": f"uat:form-action:{event['event_id']}",
        "ticket": event.get("ticket"),
        "kind": event.get("action"),
        "source_path": str(events_path(state_root, round_review_id)),
        "object_sha256": event.get("object_sha256"),
        "next_lifecycle_step": event.get("next_lifecycle_step"),
        "error_code": None,
        "retryable": True,
    }


def pending_form_notification_signals(
    state_root, round_review_id: str, *, actions: Collection[str] = ("feedback.send",),
) -> list[dict[str, Any]]:
    """Map pending events to non-consuming derived notification signals."""
    return [_signal(event, state_root, round_review_id) for event in pending_form_events(state_root, round_review_id)
            if event.get("action") in actions]


def watch_form_events(
    state_root, round_review_id: str, *, actions: Collection[str] = ("feedback.send",),
    poll_seconds: float = 2.0, once: bool = False,
    emit: Callable[[Mapping[str, Any]], None],
) -> int:
    """Emit each newly observed pending id once without touching the source or ledger."""
    seen: set[str] = set()
    while True:
        scanned_records = len(read_form_events(state_root, round_review_id))
        signals = pending_form_notification_signals(state_root, round_review_id, actions=actions)
        for signal in signals:
            notification_id = str(signal["notification_id"])
            if notification_id not in seen:
                seen.add(notification_id)
                emit(signal)
        if once:
            if not signals:
                emit({
                    "schema": SIGNAL_SCHEMA,
                    "schema_version": "1.0",
                    "signal": "none-pending",
                    "source_path": str(events_path(state_root, round_review_id)),
                    "scanned_records": scanned_records,
                })
            return 0
        time.sleep(poll_seconds)


def canonical_feedback_digest_resolver(
    source: Any, *, size_limit: int,
) -> Callable[[dict[str, Any]], str]:
    """Return the canonical production resolver for one round's feedback.

    This hashes the exact descriptor-anchored feedback bytes used by the
    emitter's post-save event. Callers must not hand-roll their own digest
    resolver: even a small difference would classify current feedback as
    superseded. The registered source and its snapshot checks fail closed on
    missing, replaced, symlinked, oversized, or unreadable feedback.
    """
    if isinstance(size_limit, bool) or not isinstance(size_limit, int) or size_limit <= 0:
        raise FormEventError("size_limit must be a positive integer")
    round_review_id = getattr(source, "round_review_id", None)
    snapshot_feedback = getattr(source, "snapshot_feedback", None)
    if not isinstance(round_review_id, str) or not round_review_id or not callable(snapshot_feedback):
        raise FormEventError("resolver requires a validated active round source")

    def resolve_current_feedback(event: dict[str, Any]) -> str:
        if (not isinstance(event, dict)
                or event.get("action") != "feedback.send"
                or event.get("round_review_id") != round_review_id):
            raise FormEventError("event does not identify this round's feedback")
        try:
            with snapshot_feedback(size_limit) as feedback:
                if not feedback.unchanged_since_read() or not feedback.still_current_at_path():
                    raise FormEventError("canonical feedback changed while resolving its digest")
                return feedback.digest
        except FormEventError:
            raise
        except Exception as exc:
            raise FormEventError("canonical feedback digest could not be resolved") from exc

    return resolve_current_feedback


def classify_form_event_currency(
    event: Mapping[str, Any], current_sha256: str | None,
) -> Literal["current", "superseded", "missing"]:
    if current_sha256 is None:
        return "missing"
    return "current" if current_sha256 == event.get("object_sha256") else "superseded"


def drain_form_events(
    state_root, round_review_id: str, on_event: Callable[[dict[str, Any]], None], *,
    current_object_sha256: Callable[[dict[str, Any]], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Lease, classify, deliver and durably acknowledge every pending event.

    Omitting ``current_object_sha256`` disables currency checking for backwards-
    compatible direct tests. Production callers must always supply it; unchecked
    deliveries are durably marked in the acknowledgement ledger.
    """
    delivered: list[dict[str, Any]] = []
    currency_unchecked = current_object_sha256 is None
    with consumer.acquire_lease(consumer_state_dir(state_root, round_review_id)):
        while True:
            pending = pending_form_events(state_root, round_review_id)
            if not pending:
                return delivered
            for event in pending:
                digest = current_object_sha256(event) if current_object_sha256 else event.get("object_sha256")
                disposition = classify_form_event_currency(event, digest)
                if disposition == "current":
                    continuation = {
                        **event,
                        "notification_id": _signal(event, state_root, round_review_id)["notification_id"],
                    }
                    on_event(continuation)
                    delivered.append(continuation)
                    disposition = "delivered"
                acknowledgement = {
                    "event_id": event["event_id"],
                    "delivered_at_utc": _now_utc(),
                    "next_lifecycle_step": event.get("next_lifecycle_step"),
                    "disposition": disposition,
                }
                if currency_unchecked:
                    acknowledgement["currency_unchecked"] = True
                _append_line(
                    consumer_state_dir(state_root, round_review_id) / LEDGER_NAME,
                    acknowledgement,
                )


def form_event_states(state_root, round_review_id: str, *, projection_sha256: str | None) -> dict[str, bool]:
    events = read_form_events(state_root, round_review_id)
    consumed = _consumed_keys(state_root, round_review_id)
    latest = events[-1] if events else None
    return {
        "event_committed": bool(events),
        "consumer_delivered": bool(latest) and latest.get("event_id") in consumed,
        "projection_current": bool(latest) and projection_sha256 == latest.get("object_sha256"),
    }


def _error_signal(state_root, round_review_id: str, code: str) -> dict[str, Any]:
    return {"schema": SIGNAL_SCHEMA, "schema_version": "1.0", "signal": "source-error",
            "notification_id": None, "ticket": None, "kind": None,
            "source_path": str(events_path(state_root, round_review_id)), "object_sha256": None,
            "next_lifecycle_step": None, "error_code": code, "retryable": True}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jswarm.portal.form_events")
    subparsers = parser.add_subparsers(dest="command", required=True)
    watch = subparsers.add_parser("watch")
    watch.add_argument("--state-root", required=True)
    watch.add_argument("--round-review-id", required=True)
    watch.add_argument("--action", action="append", default=[])
    watch.add_argument("--poll-seconds", type=float, default=2.0)
    watch.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.command != "watch":  # pragma: no cover
        return 2
    registration = read_form_watch(args.state_root, args.round_review_id)
    if registration is not None and registration.get("status") == "closed":
        print(json.dumps({"schema": SIGNAL_SCHEMA, "schema_version": "1.0", "signal": "source-closed",
                          "notification_id": None, "ticket": registration.get("ticket"), "kind": None,
                          "source_path": str(watch_state_path(args.state_root, args.round_review_id)),
                          "object_sha256": None, "next_lifecycle_step": None, "error_code": None,
                          "retryable": False}, sort_keys=True), flush=True)
        return 0
    try:
        return watch_form_events(args.state_root, args.round_review_id,
                                 actions=tuple(args.action or ("feedback.send",)),
                                 poll_seconds=args.poll_seconds, once=args.once,
                                 emit=lambda row: print(json.dumps(row, sort_keys=True), flush=True))
    except (OSError, ValueError) as exc:
        print(json.dumps(_error_signal(args.state_root, args.round_review_id, type(exc).__name__.lower()), sort_keys=True), flush=True)
        return 1


__all__: Iterable[str] = (
    "EVENT_SCHEMA", "WATCH_SCHEMA", "SIGNAL_SCHEMA", "LEDGER_NAME", "FormEventError", "arm_form_watch",
    "read_form_watch", "emit_form_action_event", "read_form_events", "pending_form_events",
    "pending_form_notification_signals", "watch_form_events", "canonical_feedback_digest_resolver",
    "classify_form_event_currency", "drain_form_events",
    "form_event_states", "next_lifecycle_step", "events_path", "watch_state_path", "consumer_state_dir", "main",
)

if __name__ == "__main__":
    sys.exit(main())
