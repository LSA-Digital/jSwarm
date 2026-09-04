#!/usr/bin/env python3
"""COM-204 Phase 8 — complete in-repo ColGREP activity log + launchd-triggered rotation.

A/C 13: every ColGREP activity class is logged in detail to the JarviSWARM repo's
``<repo_root>/log/colgrep.log`` (for the ``common`` repo: ``${JSWARM_HOME:-$HOME/dev/jswarm}/log/colgrep.log``;
per-repo, resolved from the repo root the tooling runs from). This is the human-readable,
COMPLETE activity log; it is ADDITIVE to — and never weakens — the machine-audit JSONL the
guard/evictor/supervisor already write under ``~/dev/colgrep-idx/log/``.

Rotation is time-based with a default 30-day retention and is TRIGGERED BY THE ColGREP
launchd service — specifically the overlay-fleet-supervisor tick (NOT a separate cron). Each
tick rotates the active log (atomic ``os.replace``) and prunes rotated segments older than the
retention window. Rotation is crash-safe: the active log is only ever appended to and the
rotate is an atomic rename, so a writer holding the old fd keeps writing into the renamed
segment — no in-flight line is lost and the active log is never truncated.

The log directory is gitignored (``*.log`` + ``log/`` + an explicit ``log/colgrep*.log``
entry); logs are never committed.

Rotation decisions are a pure function of (injected ``now``, retention, filesystem state) —
no real wall-clock — so the launchd tick is deterministic and testable.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from jswarm.colgrep_guarded_fs import guarded_unlink

ROOT = Path(__file__).resolve().parents[1]

# The complete set of ColGREP activity classes (A/C 13). log_activity fails LOUD on any
# component outside this registry so no activity class can silently bypass the complete log.
ACTIVITY_COMPONENTS: frozenset[str] = frozenset({
    "fleet-supervisor",   # overlay-fleet-supervisor ticks / discover / start-stop
    "watcher",            # per-worktree watcher builds / refreshes / cutovers
    "delete-guard",       # guarded :3280 delete refusals / deletes
    "evictor",            # lifecycle evictor holds / would-delete / deletes
    "lifecycle",          # classify / coverage / cleanup / check
    "reconcile",          # COM-204 Phase 9 manifest reconcile decisions
    "base-health",        # base-index health checks / auto-restore
    "mcp",                # MCP-facing search/serve diagnostics
})

LOG_BASENAME = "colgrep.log"
_STARTDAY_MARKER = f".{LOG_BASENAME}.startday"     # YYYYMMDD the active log began (rotation anchor)
_SEGMENT_PREFIX = f"{LOG_BASENAME}."               # dated segments: colgrep.log.YYYYMMDD[.N]
DEFAULT_RETENTION_DAYS = 30
_REPO_ROOT_ENV = "COLGREP_REPO_ROOT"
_RETENTION_ENV = "COLGREP_LOG_RETENTION_DAYS"


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
def resolve_repo_root(repo_root: str | Path | None = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).expanduser()
    env = os.environ.get(_REPO_ROOT_ENV)
    if env:
        return Path(env).expanduser()
    return ROOT  # the common checkout that masters the ColGREP tooling


def resolve_repo_log_path(repo_root: str | Path | None = None) -> Path:
    return resolve_repo_root(repo_root) / "log" / LOG_BASENAME


def default_retention_days() -> int:
    raw = os.environ.get(_RETENTION_ENV)
    if raw:
        try:
            val = int(raw)
            if val >= 1:
                return val
        except (TypeError, ValueError):
            pass
    return DEFAULT_RETENTION_DAYS


# --------------------------------------------------------------------------- #
# Activity logging
# --------------------------------------------------------------------------- #
def _now_iso(now: Any) -> str:
    if now is None:
        return "unknown-time"
    if hasattr(now, "isoformat"):
        return now.isoformat()
    return str(now)


def _today_str(now: Any) -> str:
    if hasattr(now, "strftime"):
        return now.strftime("%Y%m%d")
    # fall back to parsing an ISO-ish string's date prefix
    text = str(now or "")
    digits = "".join(ch for ch in text[:10] if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else "00000000"


def _fmt_detail(detail: dict[str, Any]) -> str:
    parts = []
    for key in sorted(detail):
        value = detail[key]
        text = str(value)
        if any(c.isspace() for c in text) or text == "":
            text = f'"{text}"'
        parts.append(f"{key}={text}")
    return " ".join(parts)


def log_activity(
    component: str, event: str, *, level: str = "info",
    repo_root: str | Path | None = None, now: Any = None, **detail: Any,
) -> str:
    """Append one detailed, structured, human-readable activity line to the repo log.

    Format: ``<ISO-ts> [<level>] <component>: <event> | k1=v1 k2=v2 ...``. Crash-safe: opens
    the log in append mode and flushes one line. Fails LOUD on an unregistered component so the
    complete-activity contract cannot be silently bypassed. Stamps the rotation start-day
    marker on first write so the launchd tick has a deterministic rotation anchor.
    """
    if component not in ACTIVITY_COMPONENTS:
        raise ValueError(
            f"unknown ColGREP activity component {component!r}; expected one of "
            f"{sorted(ACTIVITY_COMPONENTS)} — register the class so it routes to the complete log"
        )
    log = resolve_repo_log_path(repo_root)
    log.parent.mkdir(parents=True, exist_ok=True)
    suffix = f" | {_fmt_detail(detail)}" if detail else ""
    line = f"{_now_iso(now)} [{level}] {component}: {event}{suffix}\n"
    # Anchor the rotation start-day on the first write of a fresh active log.
    marker = log.parent / _STARTDAY_MARKER
    if not marker.exists():
        try:
            marker.write_text(_today_str(now), encoding="utf-8")
        except OSError:
            pass
    with log.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
    return line


class ActivityLogger:
    """Convenience wrapper binding a repo_root for a stream of activity from one component."""

    def __init__(self, component: str, *, repo_root: str | Path | None = None):
        if component not in ACTIVITY_COMPONENTS:
            raise ValueError(f"unknown ColGREP activity component {component!r}")
        self.component = component
        self.repo_root = repo_root

    def log(self, event: str, *, level: str = "info", now: Any = None, **detail: Any) -> str:
        return log_activity(self.component, event, level=level, repo_root=self.repo_root, now=now, **detail)


# --------------------------------------------------------------------------- #
# Rotation
# --------------------------------------------------------------------------- #
def _parse_segment_day(path: Path) -> _dt.date | None:
    """Parse a colgrep.log.YYYYMMDD[.N] segment's date, or None if not a colgrep segment."""
    name = path.name
    if not name.startswith(_SEGMENT_PREFIX):
        return None
    rest = name[len(_SEGMENT_PREFIX):]
    daypart = rest.split(".", 1)[0]
    if len(daypart) != 8 or not daypart.isdigit():
        return None
    try:
        return _dt.date(int(daypart[:4]), int(daypart[4:6]), int(daypart[6:8]))
    except ValueError:
        return None


def _now_date(now: Any) -> _dt.date:
    if isinstance(now, _dt.datetime):
        return now.date()
    if isinstance(now, _dt.date):
        return now
    s = _today_str(now)
    return _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _read_startday(marker: Path) -> str | None:
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if len(text) == 8 and text.isdigit() else None


def rotate_repo_log(
    repo_root: str | Path | None = None, *, retention_days: int | None = None,
    now: Any = None, force: bool = False,
) -> dict[str, Any]:
    """Rotate (when due) + prune the repo ColGREP log. Pure function of (now, fs, retention).

    Rotation is DUE when the active log is non-empty and its recorded start-day is strictly
    before ``now``'s day (i.e. a day boundary was crossed) — or ``force``. The active log is
    atomically renamed to ``colgrep.log.<start-day>`` (a numeric suffix is appended if that
    segment already exists), a fresh start-day marker is written, and segments whose date is
    strictly older than ``now - retention_days`` are pruned. Non-colgrep logs are never touched.
    """
    retention = retention_days if (isinstance(retention_days, int) and retention_days >= 1) else default_retention_days()
    logdir = resolve_repo_root(repo_root) / "log"
    log = logdir / LOG_BASENAME
    marker = logdir / _STARTDAY_MARKER
    today = _today_str(now)
    today_date = _now_date(now)

    rotated = False
    segment: Path | None = None
    start_day = _read_startday(marker)

    if log.exists() and log.stat().st_size > 0:
        if start_day is None:
            # No anchor (legacy log): adopt today's day; rotate only when forced.
            start_day = today
            logdir.mkdir(parents=True, exist_ok=True)
            marker.write_text(today, encoding="utf-8")
        if force or start_day < today:
            segment = logdir / f"{_SEGMENT_PREFIX}{start_day}"
            n = 1
            while segment.exists():
                segment = logdir / f"{_SEGMENT_PREFIX}{start_day}.{n}"
                n += 1
            os.replace(log, segment)            # atomic; in-flight appenders follow the inode → no lost lines
            log.touch()                          # fresh, empty active log (never truncates the old inode)
            marker.write_text(today, encoding="utf-8")
            rotated = True
    elif start_day is None and logdir.exists():
        # Keep a deterministic anchor even when the active log is empty/absent.
        marker.write_text(today, encoding="utf-8")

    # Prune segments strictly older than the retention window.
    cutoff = today_date - _dt.timedelta(days=retention)
    pruned: list[str] = []
    retained: list[str] = []
    if logdir.exists():
        for path in sorted(logdir.iterdir()):
            seg_day = _parse_segment_day(path)
            if seg_day is None:
                continue  # not a colgrep segment — never touched
            if seg_day < cutoff:
                try:
                    guarded_unlink(path, reason="activity-log-retention-prune")
                    pruned.append(str(path))
                except OSError:
                    retained.append(str(path))
            else:
                retained.append(str(path))

    return {
        "rotated": rotated,
        "segment": str(segment) if segment is not None else None,
        "start_day": start_day,
        "retention_days": retention,
        "cutoff": cutoff.isoformat(),
        "pruned": pruned,
        "retained": retained,
    }


def run_launchd_rotation_tick(
    repo_root: str | Path | None = None, *, retention_days: int | None = None, now: Any = None,
) -> dict[str, Any]:
    """The hook the ColGREP overlay-fleet-supervisor launchd tick calls each scan.

    Idempotent within a day (rotation only fires across a day boundary); prunes expired
    segments every tick. This is the rotation TRIGGER — there is no separate cron.
    """
    return rotate_repo_log(repo_root=repo_root, retention_days=retention_days, now=now)


# --------------------------------------------------------------------------- #
# CLI (diagnostics / manual tick)
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="colgrep-activity-log")
    sub = parser.add_subparsers(dest="cmd")
    rot = sub.add_parser("rotate", help="Run the launchd rotation tick once.")
    rot.add_argument("--repo-root", default=None)
    rot.add_argument("--retention-days", type=int, default=None)
    pth = sub.add_parser("path", help="Print the resolved repo activity-log path.")
    pth.add_argument("--repo-root", default=None)

    args = parser.parse_args(argv)
    if args.cmd == "path":
        print(resolve_repo_log_path(args.repo_root))
        return 0
    if args.cmd == "rotate":
        now = _dt.datetime.now(_dt.timezone.utc)
        result = run_launchd_rotation_tick(
            repo_root=args.repo_root, retention_days=args.retention_days, now=now)
        print(json.dumps(result, sort_keys=True))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
