"""The null adapter: a repo with no tracker still runs the full lifecycle.

Every write is skipped, every skip says so, and nothing is ever invented.
A caller can run `comment`/`transition`/`resolve` unconditionally, whether or
not a tracker is configured, and never has to branch on it.
"""
from __future__ import annotations

from jswarm.tracker.base import Result, WorkItem

SKIP_MESSAGE = "no tracker configured, keeping local state only"


class NullTracker:
    def is_configured(self) -> bool:
        return False

    def describe(self) -> str:
        return "no tracker"

    def resolve(self, work_id: str) -> WorkItem | None:
        return None

    def comment(self, work_id: str, text: str) -> Result:
        return Result(ok=True, skipped=True, message=SKIP_MESSAGE)

    def transition(self, work_id: str, target_state: str) -> Result:
        return Result(ok=True, skipped=True, message=SKIP_MESSAGE)
