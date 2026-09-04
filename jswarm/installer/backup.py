"""Backups before global or project writes.

Global writes back up to `<home>/.jswarm/backups/<utc-timestamp>/`. Project
writes back up to `<repo>/.jswarm/backups/<utc-timestamp>/`. Both use the
same shape, one timestamped session directory per run, so `unadopt` can find
"the newest backup" the same way regardless of which root it is under.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jswarm.installer.fsops import WriteContext


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backups_root(root: Path) -> Path:
    return Path(root) / ".jswarm" / "backups"


def session_dir(root: Path, timestamp: str) -> Path:
    return backups_root(root) / timestamp


def backup_path(ctx: WriteContext, root: Path, target: Path, timestamp: str) -> Path | None:
    """Copy `target` (file or directory), if it exists, into this run's
    backup session directory under `root/.jswarm/backups/<timestamp>/`,
    preserving its own name. Returns the backup location, or None when
    there was nothing to back up.
    """
    target = Path(target)
    if not target.exists():
        return None
    dest = session_dir(root, timestamp) / target.name
    if target.is_dir():
        ctx.copy_tree(target, dest)
    else:
        ctx.copy_file(target, dest)
    return dest


def newest_session(root: Path) -> Path | None:
    """The most recently created backup session directory under `root`, or
    None when there are no backups at all.
    """
    root_backups = backups_root(root)
    if not root_backups.is_dir():
        return None
    sessions = sorted((p for p in root_backups.iterdir() if p.is_dir()), reverse=True)
    return sessions[0] if sessions else None
