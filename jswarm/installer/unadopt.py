"""`unadopt`: undo `adopt`. Nothing the user wrote is touched.

`CLAUDE.md` and `.claude/settings.json` are restored from the *oldest*
backup session `adopt` ever took (`<repo>/.jswarm/backups/<utc-timestamp>/`)
-- the state right before the *first* `adopt` call, not the most recent
one. Every `adopt` call backs up each target's current state before writing
to it, so on a second or later `adopt` (for instance to change
`--jira-key`), the *newest* backup already contains jswarm's own managed
content, not the user's original. Only the oldest session was taken before
jswarm ever touched the repo, so that is the one restore must use; this
holds no matter how many times `adopt` has run since. When a file has no
backup (because `adopt` found nothing there to back up, meaning it did not
exist before adoption), `unadopt` removes it rather than leaving jswarm's
own content behind. As a fallback, when there is no backup at all to work
from, the managed block and jswarm's own hook entries are stripped out
surgically instead.

`.jswarm/` is removed except `backups/`, which is kept unless it is empty.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from jswarm.host import current as current_host
from jswarm.installer import backup, registry
from jswarm.installer.adopt import (
    GITIGNORE_MARK_BEGIN,
    GITIGNORE_MARK_END,
    MARK_BEGIN,
    MARK_END,
    _is_jswarm_hook_entry,
)
from jswarm.installer.fsops import WriteContext


@dataclass
class UnadoptResult:
    repo: Path
    dry_run: bool
    was_adopted: bool
    report_lines: list[str] = field(default_factory=list)


def _strip_marked_block(text: str, mark_begin: str, mark_end: str) -> str:
    start = text.find(mark_begin)
    end = text.find(mark_end)
    if start == -1 or end == -1:
        return text
    end += len(mark_end)
    before = text[:start].rstrip("\n")
    after = text[end:].lstrip("\n")
    if before and after:
        return before + "\n\n" + after
    if before:
        return before + "\n"
    return after


def _strip_claude_md_block(text: str) -> str:
    return _strip_marked_block(text, MARK_BEGIN, MARK_END)


def _strip_gitignore_block(text: str) -> str:
    return _strip_marked_block(text, GITIGNORE_MARK_BEGIN, GITIGNORE_MARK_END)


def _strip_jswarm_hooks(settings: dict) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    cleaned = {}
    for event, entries in hooks.items():
        kept = [e for e in entries if not _is_jswarm_hook_entry(e)]
        if kept:
            cleaned[event] = kept
    result = dict(settings)
    if cleaned:
        result["hooks"] = cleaned
    else:
        result.pop("hooks", None)
    return result


def unadopt(repo: Path, *, dry_run: bool = False, home: Path | None = None) -> UnadoptResult:
    repo = Path(repo).resolve()
    home = Path(home) if home is not None else Path.home()
    ctx = WriteContext(dry_run=dry_run, home=home)

    jswarm_dir = repo / ".jswarm"
    marker = jswarm_dir / ".adopted"
    was_adopted = marker.exists()
    result = UnadoptResult(repo=repo, dry_run=dry_run, was_adopted=was_adopted)

    if not was_adopted and not jswarm_dir.exists():
        result.report_lines.append(f"unadopt: {repo} is not adopted; nothing to undo")
        return result

    host = current_host()
    claude_md = host.memory_path(repo)
    settings_path = host.settings_path(repo)
    gitignore_path = repo / ".gitignore"
    oldest = backup.oldest_session(repo)

    if oldest is not None:
        backed_up_md = oldest / claude_md.name
        if backed_up_md.exists():
            ctx.copy_file(backed_up_md, claude_md)
            result.report_lines.append(f"unadopt: restored {claude_md} from {oldest}")
        elif claude_md.exists():
            ctx.remove(claude_md)
            result.report_lines.append(f"unadopt: removed {claude_md} (did not exist before adoption)")

        backed_up_settings = oldest / settings_path.name
        if backed_up_settings.exists():
            ctx.copy_file(backed_up_settings, settings_path)
            result.report_lines.append(f"unadopt: restored {settings_path} from {oldest}")
        elif settings_path.exists():
            ctx.remove(settings_path)
            result.report_lines.append(f"unadopt: removed {settings_path} (did not exist before adoption)")

        backed_up_gitignore = oldest / gitignore_path.name
        if backed_up_gitignore.exists():
            ctx.copy_file(backed_up_gitignore, gitignore_path)
            result.report_lines.append(f"unadopt: restored {gitignore_path} from {oldest}")
        elif gitignore_path.exists():
            ctx.remove(gitignore_path)
            result.report_lines.append(f"unadopt: removed {gitignore_path} (did not exist before adoption)")
    else:
        if claude_md.exists():
            stripped = _strip_claude_md_block(claude_md.read_text(encoding="utf-8"))
            ctx.write_text(claude_md, stripped)
            result.report_lines.append(f"unadopt: stripped the managed block from {claude_md} (no backup on record)")
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = None
            if isinstance(existing, dict):
                ctx.write_json(settings_path, _strip_jswarm_hooks(existing))
                result.report_lines.append(f"unadopt: removed jswarm hook entries from {settings_path} (no backup on record)")
        if gitignore_path.exists():
            stripped_gitignore = _strip_gitignore_block(gitignore_path.read_text(encoding="utf-8"))
            ctx.write_text(gitignore_path, stripped_gitignore)
            result.report_lines.append(f"unadopt: stripped the managed block from {gitignore_path} (no backup on record)")

    ctx.remove(marker)
    config_path = jswarm_dir / "config.yaml"
    if config_path.exists():
        ctx.remove(config_path)
    work_dir = jswarm_dir / "work"
    if work_dir.exists():
        ctx.remove_tree(work_dir)

    backups_dir = jswarm_dir / "backups"
    backups_present = backups_dir.is_dir() and any(backups_dir.iterdir())
    if not backups_present:
        if backups_dir.exists():
            ctx.remove_tree(backups_dir)
        if jswarm_dir.exists() and not any(jswarm_dir.iterdir()):
            ctx.remove_tree(jswarm_dir)
    else:
        result.report_lines.append(f"unadopt: kept {backups_dir}")

    registry.remove(ctx, home, repo)
    result.report_lines.append(f"unadopt: {repo} is no longer adopted")
    return result
