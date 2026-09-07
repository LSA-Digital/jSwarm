"""The tracker interface. A tracker is optional synchronisation for a work
item's identity, not its source: `jswarm.workitem` is complete without one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Result:
    ok: bool
    skipped: bool
    message: str


@dataclass(frozen=True)
class WorkItem:
    key: str
    title: str
    description: str
    status: str


@dataclass(frozen=True)
class HostRequest:
    """An operation the authenticated agent host must execute, never a success."""

    action: str
    work_id: str
    request_id: str
    text: str | None = None
    target_state: str | None = None
    server: str = "atlassian"
    status: str = "requires_host"
    ok: bool = False
    skipped: bool = False
    message: str = "Use the authenticated Atlassian MCP tools, then verify and record their response. Read docs/jira-host-bridge.md in the jSwarm clone."


class Tracker(Protocol):
    def is_configured(self) -> bool: ...

    def describe(self) -> str: ...

    def resolve(self, work_id: str) -> WorkItem | HostRequest | None: ...

    def comment(self, work_id: str, text: str) -> Result | HostRequest: ...

    def transition(self, work_id: str, target_state: str) -> Result | HostRequest: ...
