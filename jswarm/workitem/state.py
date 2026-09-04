"""Local state location for a work item, identical for tracker keys and slugs."""
from __future__ import annotations

from pathlib import Path

from jswarm.workitem.identity import WorkItemId


def work_dir(repo: Path, wid: WorkItemId) -> Path:
    """Where this work item's local state lives, regardless of ``wid.kind``."""
    return Path(repo) / ".jswarm" / "work" / wid.value
