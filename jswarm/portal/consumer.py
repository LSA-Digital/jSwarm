"""COM-389 Phase 3 — orchestrator receipt/Q&A consumer (at-least-once bridge).

Watches a receipt directory (and optionally a Q&A threads directory) with a
stdlib polling loop (``os.listdir`` + mtime; no watchdog dependency) and
records successful callback effects in an append-only consumed-keys ledger
(``consumed-ids.ndjson``) in a state directory.

Delivery contract (HIGH 4 ruling, jCritic round 2): **at-least-once delivery,
EXACTLY-ONCE EFFECT via consumer idempotency.** The ledger line is appended
(atomic ``O_APPEND`` write + fsync) AFTER the callback returns without
raising, so a crash between callback and ledger append replays that event on
restart; the consumer must therefore tolerate duplicates, deduping by event
key (``receipt:<id>`` / ``thread:<thread_id>:<message_id>``).

Single-consumer lease: an exclusive ``flock`` on ``<state-dir>/consumer.lock``
is held for the process lifetime. A second consumer on the same state dir
fails LOUD (:class:`ConsumerLockedError`; CLI exit code 3) instead of
double-delivering.

Gate mapping: approve+digest-match unlocks; deny+digest-match blocks; comment
is informational; a receipt whose ``contract_sha256`` differs from the
expected digest NEVER unlocks OR blocks (stale receipts authorize nothing).
An OPEN Q&A thread anchored to the expected digest is ``waiting``; an
answered/closed or stale-anchored thread never waits and is never authority.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import uuid
from pathlib import Path
from threading import Event
from typing import Any, Callable, Collection, Mapping

from jswarm.portal import receipts as receipts_mod

LEDGER_NAME = "consumed-ids.ndjson"
LOCK_NAME = "consumer.lock"


class ConsumerError(Exception):
    pass


class ConsumerLockedError(ConsumerError):
    """Another consumer holds the lease on this state directory."""


# ---------------------------------------------------------------------------
# Single-consumer lease


class ConsumerLease:
    """Exclusive flock on <state-dir>/consumer.lock, held for process lifetime."""

    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self._fd = None

    def __enter__(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.state_dir / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self._fd)
            self._fd = None
            raise ConsumerLockedError(
                f"another consumer already holds the lease on {self.state_dir}"
            ) from error
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
        return False


def acquire_lease(state_dir) -> ConsumerLease:
    """Acquire the single-consumer lease (use as a context manager)."""
    return ConsumerLease(state_dir)


# ---------------------------------------------------------------------------
# Append-only consumed-keys ledger


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_ledger(state_dir: Path, key: str) -> None:
    """Durably append one consumed-key line: single O_APPEND write + fsync.

    Append-only by construction: no read-modify-replace, so concurrent
    appenders can never lose each other's lines.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"consumed_key": key, "consumed_at_utc": _now_utc()}) + "\n"
    fd = os.open(state_dir / LEDGER_NAME, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    try:
        written = 0
        payload = line.encode("utf-8")
        while written < len(payload):
            written += os.write(fd, payload[written:])
        os.fsync(fd)
    finally:
        os.close(fd)


def load_consumed(state_dir: Path) -> set[str]:
    ledger = Path(state_dir) / LEDGER_NAME
    if not ledger.exists():
        return set()
    consumed = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            consumed.add(json.loads(line)["consumed_key"])
        except (json.JSONDecodeError, KeyError):
            continue  # a torn tail line never blocks consumption
    return consumed


# ---------------------------------------------------------------------------
# Event scanning


def receipt_key(receipt: dict) -> str:
    return f"receipt:{receipt.get('receipt_id')}"


def thread_message_key(thread_id: str, message_id: str) -> str:
    return f"thread:{thread_id}:{message_id}"


def thread_status_key(thread: dict) -> str:
    """Stable status snapshot key; changes only when status/message revision changes."""
    payload = json.dumps({"status": thread.get("status"), "messages": len(thread.get("messages", []))}, sort_keys=True)
    import hashlib
    return f"thread-status:{thread.get('thread_id')}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def scan_receipt_files(receipt_dir: Path) -> list[Path]:
    """Candidate receipt files, sorted by name; skips temps and foreign files."""
    receipt_dir = Path(receipt_dir)
    if not receipt_dir.is_dir():
        return []
    return sorted(p for p in receipt_dir.iterdir() if receipts_mod._FILENAME_RE.match(p.name))


def scan_thread_messages(threads_dir: Path) -> list[dict]:
    """All (thread_id, message) pairs from thread files, deterministic order."""
    threads_dir = Path(threads_dir)
    out = []
    if not threads_dir.is_dir():
        return out
    for path in sorted(threads_dir.glob("*.json")):
        try:
            thread = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        thread_id = thread.get("thread_id")
        if not thread_id:
            continue
        for message in thread.get("messages", []):
            if message.get("message_id"):
                out.append({"thread_id": thread_id, "message": message, "thread": thread})
    return out


def _pending_receipt_events(receipt_dir, consumed, seen):
    events = []
    for path in scan_receipt_files(receipt_dir):
        try:
            receipt = receipts_mod.read_receipt(path)
        except (json.JSONDecodeError, OSError, receipts_mod.ReceiptError):
            continue  # unreadable/invalid files are skipped, never block the loop
        key = receipt_key(receipt)
        if key in consumed or key in seen:
            continue
        seen.add(key)
        events.append({"kind": "receipt", "key": key, "receipt": receipt})
    return events


def _pending_thread_events(threads_dir, consumed, seen):
    events = []
    # Status snapshots deliberately include empty threads so an unanswered
    # question is observable before anybody has posted a message.
    threads_dir = Path(threads_dir)
    if threads_dir.is_dir():
        for path in sorted(threads_dir.glob("*.json")):
            try:
                thread = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if thread.get("thread_id") and thread.get("status"):
                key = thread_status_key(thread)
                if key not in consumed and key not in seen:
                    seen.add(key)
                    events.append({"kind": "thread_status", "key": key, "thread_id": thread["thread_id"], "thread_status": thread["status"], "anchor": thread.get("anchor", {})})
    for item in scan_thread_messages(threads_dir):
        key = thread_message_key(item["thread_id"], item["message"]["message_id"])
        if key in consumed or key in seen:
            continue
        seen.add(key)
        events.append(
            {
                "kind": "thread_message",
                "key": key,
                "thread_id": item["thread_id"],
                "message": item["message"],
                "thread_status": item["thread"].get("status"),
                "anchor": item["thread"].get("anchor", {}),
            }
        )
    return events



def _receipt_signal(receipt: Mapping[str, Any], receipt_dir: os.PathLike[str] | str) -> dict[str, Any]:
    action = receipt.get("action")
    return {
        "schema": "jswarm.event-notify.signal/v1", "schema_version": "1.0", "signal": "pending",
        "notification_id": f"fix:receipt:{receipt.get('receipt_id')}", "ticket": receipt.get("ticket"),
        "kind": f"decision.{action}", "source_path": str(Path(receipt_dir)),
        "object_sha256": receipt.get("contract_sha256"), "next_lifecycle_step": "fix-decision-apply",
        "error_code": None, "retryable": True,
    }


def pending_notification_signals(
    receipt_dir: os.PathLike[str] | str, state_dir: os.PathLike[str] | str, *,
    expected_digest: str | None = None, kinds: Collection[str] = ("decision.approve",),
) -> list[dict[str, Any]]:
    """Return non-consuming signals for unconsumed matching FIX receipts."""
    consumed = load_consumed(Path(state_dir))
    signals = []
    for path in scan_receipt_files(Path(receipt_dir)):
        receipt = receipts_mod.read_receipt(path)
        if receipt_key(receipt) in consumed:
            continue
        signal = _receipt_signal(receipt, receipt_dir)
        if expected_digest is not None and receipt.get("contract_sha256") != expected_digest:
            continue
        if signal["kind"] in kinds:
            signals.append(signal)
    return signals


def watch_pending_notifications(
    receipt_dir: os.PathLike[str] | str, state_dir: os.PathLike[str] | str, *,
    expected_digest: str | None = None, kinds: Collection[str] = ("decision.approve",),
    poll_seconds: float = 2.0, once: bool = False, emit: Callable[[Mapping[str, Any]], None],
) -> int:
    """Emit each newly observed pending receipt once without leasing or consuming."""
    seen: set[str] = set()
    while True:
        for signal in pending_notification_signals(receipt_dir, state_dir, expected_digest=expected_digest, kinds=kinds):
            notification_id = str(signal["notification_id"])
            if notification_id not in seen:
                seen.add(notification_id)
                emit(signal)
        if once:
            return 0
        time.sleep(poll_seconds)

def drain(receipt_dir, state_dir, on_receipt, threads_dir=None, on_message=None) -> list[dict]:
    """Deliver every unconsumed event once; returns the events delivered.

    Marks consumed only after the callback succeeds (at-least-once). Re-checks
    the directories after each delivery batch so events landing mid-scan are
    not missed (missed-event safety). Dedupe is by event key, including
    duplicate receipt files on disk and within a single scan. The caller is
    responsible for holding the single-consumer lease (see acquire_lease).
    """
    receipt_dir = Path(receipt_dir)
    threads_dir = Path(threads_dir) if threads_dir else None
    delivered = []
    while True:
        consumed = load_consumed(state_dir)
        seen: set[str] = set()
        events = _pending_receipt_events(receipt_dir, consumed, seen)
        if threads_dir is not None:
            events.extend(_pending_thread_events(threads_dir, consumed, seen))
        if not events:
            return delivered
        for event in events:
            if event["kind"] == "receipt":
                on_receipt(event["receipt"])
            elif on_message is not None:
                on_message(event)
            _append_ledger(state_dir, event["key"])
            delivered.append(event)
        # loop again: new events may have arrived during delivery


def watch_receipts(
    receipt_dir,
    on_receipt,
    state_dir,
    poll_seconds: float = 2.0,
    threads_dir=None,
    on_message=None,
    stop_event: Event | None = None,
):
    """Polling watch loop; acquires the single-consumer lease and delivers
    events at least once until stop_event is set (or forever)."""
    stop = stop_event or Event()
    # The caller (main / test) is responsible for holding the single-consumer
    # lease; watch_receipts does not re-acquire it (flock on a second fd of
    # the same file within one process self-deadlocks).
    while not stop.is_set():
        drain(receipt_dir, state_dir, on_receipt, threads_dir=threads_dir, on_message=on_message)
        stop.wait(poll_seconds)


# ---------------------------------------------------------------------------
# Gate-state mapping


def map_receipt_to_gate_state(receipt: dict, expected_digest: str) -> dict:
    """Map one receipt to orchestrator gate state.

    - approve + exact digest match -> ``unlocked``
    - deny   + exact digest match -> ``blocked``
    - comment -> ``informational``
    - a receipt whose contract_sha256 differs from the expected digest makes
      NO state change (stale receipts neither unlock nor block — HIGH 6)
    - anything else -> no state change
    """
    digest_ok = receipt.get("contract_sha256") == expected_digest
    action = receipt.get("action")
    if not digest_ok:
        return {"unlocked": False, "blocked": False, "waiting": False, "informational": False}
    if action == "approve":
        return {"unlocked": True, "blocked": False, "waiting": False, "informational": False}
    if action == "deny":
        return {"unlocked": False, "blocked": True, "waiting": False, "informational": False}
    if action == "comment":
        return {"unlocked": False, "blocked": False, "waiting": False, "informational": True}
    return {"unlocked": False, "blocked": False, "waiting": False, "informational": False}


def map_thread_to_gate_state(thread: dict, expected_digest: str) -> dict:
    """Map one Q&A thread to orchestrator gate state (HIGH 3 ruling).

    ``waiting`` is true ONLY for an OPEN thread anchored to the expected
    digest. Answered/closed threads resume; stale-anchored or free-floating
    threads never wait. A thread is never authority: unlocked/blocked stay
    false unconditionally.
    """
    anchor = thread.get("anchor", {}) or {}
    anchored_to_expected = (
        bool(anchor.get("contract_sha256"))
        and anchor.get("contract_sha256") == expected_digest
    )
    waiting = thread.get("status") == "open" and anchored_to_expected
    return {"unlocked": False, "blocked": False, "waiting": waiting, "informational": not waiting}


# ---------------------------------------------------------------------------
# CLI



def main(argv=None):
    parser = argparse.ArgumentParser(prog="jswarm.portal.consumer", description="Consume immutable fix-decision receipts and Q&A thread events with at-least-once delivery and exactly-once effect.")
    parser.add_argument("--receipts", required=True, help="receipt directory to watch")
    parser.add_argument("--state", required=True, help="state directory for the lease and consumed-keys ledger")
    parser.add_argument("--threads", default=None, help="optional Q&A threads directory to watch")
    parser.add_argument("--once", action="store_true", help="drain unconsumed events and exit")
    parser.add_argument("--notify-only", action="store_true", help="emit non-consuming pending notification signals")
    parser.add_argument("--kind", action="append", default=[], help="notification kind to include")
    parser.add_argument("--expected-digest", default=None, help="only report receipts with this contract digest")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)

    if args.notify_only:
        try:
            return watch_pending_notifications(args.receipts, args.state, expected_digest=args.expected_digest,
                kinds=tuple(args.kind or ("decision.approve",)), poll_seconds=args.poll_seconds, once=args.once,
                emit=lambda row: print(json.dumps(row, sort_keys=True), flush=True))
        except (OSError, ValueError, json.JSONDecodeError, receipts_mod.ReceiptError) as exc:
            print(json.dumps({"schema": "jswarm.event-notify.signal/v1", "schema_version": "1.0", "signal": "source-error",
                "notification_id": None, "ticket": None, "kind": None, "source_path": str(Path(args.receipts)),
                "object_sha256": None, "next_lifecycle_step": None, "error_code": type(exc).__name__.lower(), "retryable": True}, sort_keys=True), flush=True)
            return 1

    def _print_receipt(receipt):
        print(json.dumps({k: receipt.get(k) for k in ("receipt_id", "action", "publication_id", "contract_sha256")}, sort_keys=True))

    def _print_message(event):
        print(json.dumps({"thread_id": event["thread_id"], "message_id": event["message"]["message_id"]}, sort_keys=True))

    lease = None
    try:
        lease = acquire_lease(args.state)
        lease.__enter__()
        if args.once:
            delivered = drain(Path(args.receipts), Path(args.state), _print_receipt,
                threads_dir=Path(args.threads) if args.threads else None, on_message=_print_message)
            print(f"delivered={len(delivered)}")
            return 0
        watch_receipts(Path(args.receipts), _print_receipt, Path(args.state), poll_seconds=args.poll_seconds,
            threads_dir=Path(args.threads) if args.threads else None, on_message=_print_message)
    except ConsumerLockedError as exc:
        print(f"error: consumer_locked: {exc}", file=sys.stderr)
        return 3
    finally:
        if lease is not None:
            try:
                lease.__exit__(None, None, None)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
