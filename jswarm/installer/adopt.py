"""`adopt`: bring an existing git checkout under JarviSWARM.

Never clobbers. An existing `CLAUDE.md` keeps every byte the user wrote; the
managed content goes into a block marked `<!-- jswarm:begin -->` /
`<!-- jswarm:end -->`. An existing `.claude/settings.json` is deep-merged so
every existing key survives, including nested ones. Both are backed up,
under `<repo>/.jswarm/backups/<utc-timestamp>/`, before anything is written.

Re-running `adopt` on an already-adopted repository replaces the managed
CLAUDE.md block in place (never appends a second one) and re-merges the
settings hooks the same way, so `adopt` is safe to run again to pick up a
new `--jira-key` or a newer jSwarm checkout.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from jswarm.host import current as current_host
from jswarm.installer import backup, registry
from jswarm.installer.fsops import WriteContext

MARK_BEGIN = "<!-- jswarm:begin -->"
MARK_END = "<!-- jswarm:end -->"

# Any hook whose command contains this is ours; unadopt uses the same test
# to remove only jswarm's own entries and leave everything else alone. The
# hook interpreter path itself always contains it, so this is never missed.
_HOOK_TAG = "jswarm"


class AdoptError(RuntimeError):
    pass


@dataclass
class AdoptResult:
    repo: Path
    dry_run: bool
    jira_key: str | None
    backed_up: list[Path] = field(default_factory=list)
    report_lines: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_git_repo(repo: Path) -> bool:
    return (repo / ".git").exists()


def _claude_md_block(jira_key: str | None) -> str:
    tracker_line = f"Jira project: {jira_key}" if jira_key else "Tracker: none configured (local-only work items)"
    lines = [
        MARK_BEGIN,
        "# JarviSWARM",
        "This repository is adopted by JarviSWARM. Start work with /jPlan, run in an agent",
        "session opened here. See the jSwarm clone's docs for the full lifecycle.",
        tracker_line,
        MARK_END,
        "",
    ]
    return "\n".join(lines)


def merge_claude_md(existing: str, block: str) -> str:
    """Replace the managed block in place if one is present; otherwise
    append the block after whatever the user already wrote.
    """
    start = existing.find(MARK_BEGIN)
    end = existing.find(MARK_END)
    if start != -1 and end != -1:
        end += len(MARK_END)
        return existing[:start] + block + existing[end:]
    if not existing:
        return block
    stripped = existing.rstrip("\n")
    return stripped + "\n\n" + block


def _is_jswarm_hook_entry(entry: dict) -> bool:
    for h in entry.get("hooks", []) or []:
        if _HOOK_TAG in str(h.get("command", "")).lower():
            return True
    return False


def _jswarm_hooks_payload() -> dict:
    interpreter = current_host().hook_interpreter()
    command = f"{interpreter} -m jswarm.installer.hooks precompact_reminder"
    return {
        "PreCompact": [
            {"matcher": "", "hooks": [{"type": "command", "command": command}]},
        ],
    }


def _merge_hooks(existing_hooks: dict, additions: dict) -> dict:
    merged = {event: list(entries) for event, entries in existing_hooks.items()}
    for event, entries in additions.items():
        kept = [e for e in merged.get(event, []) if not _is_jswarm_hook_entry(e)]
        merged[event] = kept + list(entries)
    return merged


def deep_merge_settings(existing: dict, additions: dict) -> dict:
    """Merge `additions` into `existing`, recursively. Every existing key
    survives, including nested ones: a scalar collision keeps the existing
    value, and a dict collision recurses. `hooks` merges by event, dropping
    only jswarm's own previous entries for that event before re-adding its
    current ones, so a rerun never duplicates them and never touches a hook
    the user added.
    """
    result = dict(existing)
    for key, value in additions.items():
        if key == "hooks" and isinstance(value, dict):
            current = result.get("hooks")
            result["hooks"] = _merge_hooks(current if isinstance(current, dict) else {}, value)
        elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_settings(result[key], value)
        elif key not in result:
            result[key] = value
        # else: existing wins, nothing to do
    return result


def _tracker_config(jira_key: str | None) -> dict:
    if jira_key:
        return {"adapter": "jira", "key_prefix": jira_key.strip().upper()}
    return {"adapter": "none"}


def _render_config(existing: dict, jira_key: str | None) -> dict:
    """`.jswarm/config.yaml`'s content. `tracker`, `merge`, and `pr` are
    installer-owned and always rewritten to the current adopt call's
    values; anything else already in the file (a user's own addition)
    survives untouched.

    `merge.strategy` defaults to `rebase`: `/jMerge` reads it and falls
    back to rebase itself when the key is absent, so this just makes the
    default explicit. `pr.enabled` defaults to `false`: opening a pull
    request needs a configured host CLI and is a judgment call for someone
    else's repository, so adopt never turns it on without being asked.
    """
    config = dict(existing)
    config["tracker"] = _tracker_config(jira_key)
    config.setdefault("merge", {})
    if not isinstance(config["merge"], dict):
        config["merge"] = {}
    config["merge"].setdefault("strategy", "rebase")
    config.setdefault("pr", {})
    if not isinstance(config["pr"], dict):
        config["pr"] = {}
    config["pr"].setdefault("enabled", False)
    return config


def adopt(
    repo: Path,
    *,
    jira_key: str | None = None,
    hooks: bool = True,
    dry_run: bool = False,
    home: Path | None = None,
) -> AdoptResult:
    repo = Path(repo).resolve()
    home = Path(home) if home is not None else Path.home()

    if not _is_git_repo(repo):
        raise AdoptError(
            f"{repo} is not a git repository (no .git found). "
            f"adopt <repo-path> only works on an existing git checkout; "
            f"run 'git init' there first if it is meant to become one."
        )

    key = jira_key.strip().upper() if jira_key else None
    ctx = WriteContext(dry_run=dry_run, home=home)
    result = AdoptResult(repo=repo, dry_run=dry_run, jira_key=key)

    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=False, env=ctx.env(),
    )
    if status.returncode == 0 and status.stdout.strip():
        n = len(status.stdout.strip().splitlines())
        result.report_lines.append(
            f"adopt: warning: {repo} has {n} uncommitted change(s); "
            f"adopt continues, but commit or stash first so the adopt layer lands in a commit of its own"
        )

    host = current_host()
    claude_md = host.memory_path(repo)
    settings_path = host.settings_path(repo)
    timestamp = backup.utc_timestamp()

    for target in (claude_md, settings_path):
        backed_up = backup.backup_path(ctx, repo, target, timestamp)
        if backed_up is not None:
            result.backed_up.append(backed_up)

    existing_md = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    new_md = merge_claude_md(existing_md, _claude_md_block(key))
    ctx.write_text(claude_md, new_md)
    result.report_lines.append(f"adopt: merged {claude_md}")

    if hooks:
        existing_settings: dict = {}
        if settings_path.exists():
            try:
                existing_settings = json.loads(settings_path.read_text(encoding="utf-8")) or {}
            except json.JSONDecodeError as exc:
                raise AdoptError(f"{settings_path}: not valid JSON ({exc}); fix or move the file, then re-run adopt") from exc
            if not isinstance(existing_settings, dict):
                raise AdoptError(f"{settings_path}: top level must be a JSON object")
        merged_settings = deep_merge_settings(existing_settings, {"hooks": _jswarm_hooks_payload()})
        ctx.write_json(settings_path, merged_settings)
        result.report_lines.append(f"adopt: merged {settings_path}")
    else:
        result.report_lines.append(f"adopt: --no-hooks, leaving {settings_path} untouched")

    jswarm_dir = repo / ".jswarm"
    config_path = jswarm_dir / "config.yaml"
    existing_config: dict = {}
    if config_path.exists():
        existing_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(existing_config, dict):
            existing_config = {}
    ctx.write_yaml(config_path, _render_config(existing_config, key))
    result.report_lines.append(f"adopt: wrote {config_path}")

    ctx.mkdir(jswarm_dir / "work")

    marker = jswarm_dir / ".adopted"
    ctx.write_text(marker, _now_iso() + "\n")
    result.report_lines.append(f"adopt: wrote {marker}")

    registry.add(ctx, home, repo)

    return result
