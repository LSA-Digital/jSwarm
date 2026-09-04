"""A/C 17: append plan-status fact transitions to common/.jswarm/ops/ as NDJSON.

Schema-compatible with common/logs/command-usage.ndjson (COM-50 substrate).
One JSON object per line. Fail-open: never raises to the caller.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from . import registry as R


class _AppendEvent(Protocol):
    def __call__(
        self,
        repo_root: Path,
        event_class: str,
        record_or_payload: Mapping[str, object],
        *,
        event_file: str | Path | None = None,
        runtime: str = "claude",
        actor: str = "",
        timestamp_utc: str | None = None,
        fail_open: bool = True,
    ) -> Mapping[str, object]: ...


append_event = cast(_AppendEvent, import_module("jswarm.fact_events").append_event)

EVENTS_FILENAME = "plan-status-events.ndjson"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def events_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".jswarm" / "ops" / EVENTS_FILENAME


def emit_event(
    repo_root: Path,
    *,
    ticket_key: str,
    from_status: str,
    to_status: str,
    actor: str,
    proof_source: str,
    transition_event_id: str,
    outcome: str,
    runtime: str = "claude",
    timestamp_utc: str | None = None,
) -> bool:
    """Append one NDJSON record. Returns True on success, False on any failure.

    Fails open — a logging failure must never break a transition.
    """
    record = {
        "ticket_key": ticket_key,
        "from_status": from_status,
        "to_status": to_status,
        "proof_source": proof_source,
        "transition_event_id": transition_event_id,
        "outcome": outcome,
    }
    try:
        result = append_event(
            repo_root,
            "plan_status",
            record,
            runtime=runtime,
            actor=actor,
            timestamp_utc=timestamp_utc or _utc_now_iso(),
            fail_open=True,
        )
        ok = bool(result.get("ok"))
        if ok:
            R.fold_plan_status_events(repo_root, fail_open=True)
        return ok
    except Exception:
        return False
