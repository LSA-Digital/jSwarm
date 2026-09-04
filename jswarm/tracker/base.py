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


class Tracker(Protocol):
    def is_configured(self) -> bool: ...

    def describe(self) -> str: ...

    def resolve(self, work_id: str) -> WorkItem | None: ...

    def comment(self, work_id: str, text: str) -> Result: ...

    def transition(self, work_id: str, target_state: str) -> Result: ...
