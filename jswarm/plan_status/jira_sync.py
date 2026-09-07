"""Plan-status transition intents and optional host-backed tracker sync.

Project-specific mappings are advisory. The lifecycle executes any requires_host
request using the active host's authenticated tools; Python never borrows OAuth.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from jswarm.plan_status import state as S

# Per-project Jira transition NAMES keyed by plan_status (or prefix for IMPL).
# COM verified live via jira_get_transitions 2026-05-20.
# HAS verified live via jira_get_transitions 2026-05-20 — IDENTICAL to COM.
_COM_TRANSITIONS = {
    S.STATE_LITE_INIT: "Backlog",
    S.STATE_LITE_REFINE: "Backlog",
    S.STATE_DETAILED: "Selected->Dev",
    "3.implementation": "In Progress",  # prefix match
    S.STATE_ALL_AC_MET: "In Progress",
    S.STATE_READY_FOR_MERGE: "In Progress",
    S.STATE_MERGED: "Done",
    # Spec §1 (lines 188-189): BOTH terminals map to Jira "Done", retry-safe.
    S.STATE_WONT_DO: "Done",
    S.STATE_DEFERRED: "Done",
}
TRANSITION_NAMES_BY_PROJECT: dict[str, dict[str, str]] = {
    "COM": _COM_TRANSITIONS,
    "HAS": dict(_COM_TRANSITIONS),  # verified identical 2026-05-20
}

# Closeout (retry-safe) target states.
RETRY_SAFE_STATES = frozenset({S.STATE_MERGED, S.STATE_WONT_DO, S.STATE_DEFERRED})



@dataclass
class JiraIntent:
    transition_name: Optional[str]
    criticality: str  # 'fire-and-forget' | 'retry-safe'


def intended_jira_transition(plan_status: str, project_key: str = "COM") -> JiraIntent:
    """Resolve the target Jira transition name + criticality for a plan_status."""
    mapping = TRANSITION_NAMES_BY_PROJECT.get(project_key, {})
    if plan_status.startswith(S.STATE_IMPLEMENTATION_PREFIX):
        name = mapping.get("3.implementation")
    else:
        name = mapping.get(plan_status)
    criticality = "retry-safe" if plan_status in RETRY_SAFE_STATES else "fire-and-forget"
    return JiraIntent(transition_name=name, criticality=criticality)


def is_retry_safe(plan_status: str) -> bool:
    return plan_status in RETRY_SAFE_STATES


# Injectable runner so tests never hit Jira/subprocess.
Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _default_runner(cmd: list[str]) -> "subprocess.CompletedProcess":
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def sync_closeout(
    repo_root: Path,
    *,
    ticket: str,
    plan_status: str,
    venv_python: Optional[str] = None,
    runner: Optional[Runner] = None,
) -> dict:
    """Return the configured tracker's result, including a pending host request.

    Kept for plan-status --sync-jira compatibility. No shell process can borrow
    Claude's OAuth session. The lifecycle must execute and validate requires_host.
    """
    from dataclasses import asdict
    from jswarm.tracker.resolve import load

    if not is_retry_safe(plan_status):
        return {"ok": False, "action": "not-retry-safe",
                "detail": f"{plan_status} is not a retry-safe closeout state"}
    try:
        return asdict(load(Path(repo_root)).transition(ticket, "Done"))
    except Exception as exc:
        return {"ok": False, "skipped": False, "action": "transition",
                "detail": f"error: {exc}"}
