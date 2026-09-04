"""A/C 9: hybrid Jira sync — planning fire-and-forget, closeout retry-safe.

Per-project transition NAMES are recorded here as a constant (T1.5). COM names were
verified live via atlassian.jira_get_transitions(COM-84) on 2026-05-20:
    Backlog(11), Selected->Dev(21), In Progress(31), Done(41), Canceled(5).

Criticality (Oracle Concern #5 + user-confirmed hybrid):
  - Planning-stage transitions (Backlog / Selected->Dev / In Progress) are
    FIRE-AND-FORGET: best effort, tolerate MCP timeouts, never block, never queue.
  - Closeout transitions (Done at 6.closed.merged; Canceled/Backlog at terminals)
    are RETRY-SAFE: delegate to jswarm/jira_mcp_closeout.py and, on failure, the
    caller appends to the registry jira_retry_queue for /devops-maint resync.

This module does not import the Atlassian MCP directly (hooks/commands run agent-side
or shell out). The closeout path shells to the existing retry-safe helper; the planning
path is advisory and is pushed by the slash-command agent context (Phase 3) or by
/devops-maint plan-status-resync-jira (Phase 4).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from plan_status import state as S

# Per-project Jira transition NAMES keyed by plan_status (or prefix for IMPL).
# COM verified live via jira_get_transitions(COM-84) 2026-05-20.
# HAS verified live via jira_get_transitions(HAS-381) 2026-05-20 — IDENTICAL to COM.
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

CLOSEOUT_HELPER = "jswarm/jira_mcp_closeout.py"


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
    """Retry-safe closeout sync. Returns {ok, action, detail}.

    All retry-safe states (6.closed.merged, terminal.wont_do, terminal.deferred)
    map to Jira "Done" (spec §1) and are transitioned via the existing
    `transition-done` helper subcommand. Never raises — returns ok=False on any
    failure so callers can append to the registry jira_retry_queue.
    """
    runner = runner or _default_runner
    repo_root = Path(repo_root)
    py = venv_python or str(repo_root / ".venv" / "bin" / "python")
    helper = str(repo_root / CLOSEOUT_HELPER)

    if not is_retry_safe(plan_status):
        return {
            "ok": False,
            "action": "not-retry-safe",
            "detail": f"{plan_status} is not a retry-safe closeout state",
        }

    # 6.closed.merged + both terminals all map to Jira "Done" (spec §1).
    cmd = [py, helper, "--ticket-context", ticket, "transition-done", ticket]
    try:
        proc = runner(cmd)
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "action": "transition-done",
            "detail": (proc.stdout or proc.stderr or "").strip()[:500],
        }
    except Exception as exc:  # never raise into a closeout flow
        return {"ok": False, "action": "transition-done", "detail": f"error: {exc}"}
