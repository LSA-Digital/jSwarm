"""Backups before global or project writes.

Global writes back up to `<home>/.jswarm/backups/<utc-timestamp>/`. Project
writes back up to `<repo>/.jswarm/backups/<utc-timestamp>/`. Both use the
same shape, one timestamped session directory per run, so `unadopt` can find
"the oldest backup" (see `oldest_session`) the same way regardless of which
root it is under -- that is the session taken before jswarm ever wrote to
the repo, which is what a true undo needs, regardless of how many `adopt`
calls happened after it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jswarm.installer.fsops import WriteContext


def utc_timestamp() -> str:
    """A session id for a backup run. Microsecond resolution (not just
    seconds) matters here: `adopt` can run twice in the same second (a
    script re-running it, or a test), and second-resolution timestamps
    would collide two calls into the same session directory -- silently
    overwriting an earlier, still-needed backup with a later one that
    already carries jswarm's own managed content. `unadopt` relies on
    each `adopt` call getting its own distinct session (see
    `oldest_session` in this module), so collisions here are not cosmetic.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


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


def _sessions(root: Path) -> list[Path]:
    root_backups = backups_root(root)
    if not root_backups.is_dir():
        return []
    return sorted(p for p in root_backups.iterdir() if p.is_dir())


def newest_session(root: Path) -> Path | None:
    """The most recently created backup session directory under `root`, or
    None when there are no backups at all.
    """
    sessions = _sessions(root)
    return sessions[-1] if sessions else None


def oldest_session(root: Path) -> Path | None:
    """The first backup session directory ever created under `root`, or
    None when there are no backups at all.

    Every `adopt` call backs up each target's *current* state before
    writing to it, timestamped into its own session directory. On a repo's
    first adoption that current state is the user's true pre-jswarm
    content; on every adoption after that, it is already jswarm's own
    managed content from the previous call. The oldest session is
    therefore the only one that ever holds the genuine pre-adoption
    state -- `unadopt` must restore from this one, not the newest, no
    matter how many times `adopt` has run since.
    """
    sessions = _sessions(root)
    return sessions[0] if sessions else None
