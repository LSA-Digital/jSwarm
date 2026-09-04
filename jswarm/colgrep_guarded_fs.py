#!/usr/bin/env python3
"""Guarded filesystem-destruction primitives for ColGREP tooling (COM-269).

Incident context: a blank / legacy-dot ``physical_colgrep_dir`` registry
field previously normalized to ``Path(".")``, which resolves to the CURRENT
WORKING DIRECTORY -- and was handed straight to ``shutil.rmtree``. Six live
attempts against a real checkout were stopped only by a macOS ACL, not by
any code-level check.

Every destructive filesystem call in ColGREP production code must route
through ``guarded_rmtree`` / ``guarded_unlink`` below, so a blank, relative,
or otherwise unproven path is refused up front instead of executed. This
module is the ONLY place ColGREP production code may call
``shutil.rmtree`` / ``os.rmdir`` / ``os.removedirs`` / ``os.remove`` /
``os.unlink`` / ``Path.unlink`` directly -- enforced by the static tripwire
in ``jswarm/tests/test_colgrep_destructive_call_guard.py``.

Fail-closed by design: any ambiguity about *what* is being deleted or
*where* it lives is a refusal (a raised ``GuardedFsRefusal``), never a
partial or best-effort delete. This is internal tooling -- the bar is
"a destructive op can only target a provably-owned path", not full
security ceremony.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Callable

_MIN_PATH_DEPTH = 3  # refuses "/", "/Users", "/Users/idengrenme"


class GuardedFsRefusal(RuntimeError):
    """Raised when a destructive filesystem call is refused."""


def _owned_prefix_roots() -> tuple[Path, ...]:
    """Roots whose SUBPATHS (not the root itself) are owned."""
    home = Path.home()
    return (
        (home / "dev" / "colgrep-idx").resolve(),
        Path("/private/tmp"),
        Path("/var/folders"),
    )


def _worktrees_entry_root(resolved: Path) -> Path | None:
    """Return the owning `.claude/worktrees/<name>` root if resolved is at or
    below one, else None. The `worktrees` directory itself (the parent that
    holds every `<name>` entry) is deliberately excluded -- only a specific
    named worktree, or something beneath it, is owned.
    """
    parts = resolved.parts
    for idx in range(len(parts) - 2):
        if parts[idx] == ".claude" and parts[idx + 1] == "worktrees" and len(parts) > idx + 2:
            return Path(*parts[: idx + 3])
    return None


def _resolved_is_owned(resolved: Path) -> bool:
    if _worktrees_entry_root(resolved) is not None:
        return True
    for root in _owned_prefix_roots():
        if resolved == root:
            continue  # the root itself is never a valid target
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _is_foreign_git_checkout_root(resolved: Path) -> bool:
    """Refuse a resolved path that is itself the root of a git checkout NOT
    covered by a `.claude/worktrees/<name>` entry. A linked worktree's own
    root carries a `.git` FILE (not directory), so this only fires for a
    real checkout root -- defense in depth against a mis-scoped owned root
    ever containing a full clone.
    """
    if _worktrees_entry_root(resolved) is not None:
        return False
    return (resolved / ".git").is_dir()


def _validate_and_resolve(path: object, *, reason: str) -> tuple[Path, Path]:
    """Validate `path` and return (original_as_path, resolved_realpath).

    Raises GuardedFsRefusal on any ambiguity. Never returns for an unsafe
    input -- callers must not catch and continue past a refusal.
    """
    if not reason or not reason.strip():
        raise GuardedFsRefusal("refused: a non-blank `reason` is required")
    if not isinstance(path, (str, Path)):
        raise GuardedFsRefusal(f"refused ({reason}): path is not str/Path: {type(path)!r}")
    raw = str(path).strip()
    if raw in ("", ".", "..", "/"):
        raise GuardedFsRefusal(f"refused ({reason}): blank or unsafe path literal: {raw!r}")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise GuardedFsRefusal(f"refused ({reason}): non-absolute path: {raw!r}")
    home = Path.home()
    if candidate == home:
        raise GuardedFsRefusal(f"refused ({reason}): refusing to touch $HOME itself: {raw!r}")
    if len(candidate.parts) < _MIN_PATH_DEPTH:
        raise GuardedFsRefusal(f"refused ({reason}): path depth below minimum: {raw!r}")
    resolved = candidate.resolve()
    if resolved == home:
        raise GuardedFsRefusal(f"refused ({reason}): resolved path is $HOME itself: {raw!r}")
    if not _resolved_is_owned(resolved):
        raise GuardedFsRefusal(
            f"refused ({reason}): resolved path {resolved} is outside all owned roots"
        )
    if _is_foreign_git_checkout_root(resolved):
        raise GuardedFsRefusal(
            f"refused ({reason}): resolved path {resolved} is a foreign git checkout root"
        )
    return candidate, resolved


def _log(logger: Callable[[str], None] | None, message: str) -> None:
    line = f"[colgrep_guarded_fs] {message}"
    if logger is not None:
        logger(line)
    else:
        print(line, file=sys.stderr)


def guarded_rmtree(
    path: object,
    *,
    reason: str,
    missing_ok: bool = False,
    logger: Callable[[str], None] | None = None,
) -> None:
    """Recursively remove a directory tree, refusing anything outside the
    owned roots. Raises GuardedFsRefusal instead of partially deleting."""
    candidate, resolved = _validate_and_resolve(path, reason=reason)
    if not candidate.exists():
        if missing_ok:
            return
        raise GuardedFsRefusal(f"refused ({reason}): path does not exist: {resolved}")
    if not resolved.is_dir():
        raise GuardedFsRefusal(f"refused ({reason}): not a directory: {resolved}")
    shutil.rmtree(candidate)
    _log(logger, f"rmtree ok path={resolved} reason={reason}")


def guarded_unlink(
    path: object,
    *,
    reason: str,
    missing_ok: bool = False,
    logger: Callable[[str], None] | None = None,
) -> None:
    """Remove a single file, refusing anything outside the owned roots.

    Operates on the ORIGINAL (unresolved) path -- a symlink is unlinked
    itself and never dereferenced -- once containment of its resolved
    target has been proven.
    """
    candidate, resolved = _validate_and_resolve(path, reason=reason)
    try:
        candidate.unlink()
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    _log(logger, f"unlink ok path={resolved} reason={reason}")
