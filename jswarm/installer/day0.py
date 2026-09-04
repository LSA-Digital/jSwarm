"""COM-398 day-0 helpers behind install.sh.

Everything here is a small, testable file operation. The shell script decides
order and talks to the user; this module never prompts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

JSWARM_START = "<!-- JSWARM:START -->"
JSWARM_END = "<!-- JSWARM:END -->"
# Two separate patterns, searched JSWARM-first: a file may carry an OMC block
# *before* its JSWARM block, and a single alternation regex would match the OMC
# one, delete its content, and leave the file with two JSWARM blocks.
_JSWARM_BLOCK_RE = re.compile(re.escape(JSWARM_START) + r".*?" + re.escape(JSWARM_END), re.DOTALL)
_OMC_BLOCK_RE = re.compile(r"<!-- OMC:START -->.*?<!-- OMC:END -->", re.DOTALL)
CANONICAL_CORES = (
    "jArchitect", "jCoder", "jCritic", "jDebugger", "jExplorer", "jMicroBuildFixer",
    "jMicroCliSmoke", "jOps", "jOracle", "jPlanner", "jQATester", "jResearcher",
    "jSecurityReviewer", "jTestEngineer", "jUIDesigner", "jVerifier", "jWriter",
)
DAY0_SKILLS = ("jPlan", "jGo", "jClose", "jMerge", "fix", "test", "uat", "jPrecompact")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backup(path: Path) -> str | None:
    if not path.exists():
        return None
    bak = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    bak.write_bytes(path.read_bytes())
    return str(bak)


# --------------------------------------------------------------------------- settings.json
def merge_settings(path: Path, *, common_dir: Path, status_line: dict | None = None, dry_run: bool = False) -> dict:
    """Merge ``env.JSWARM_COMMON`` (and optionally ``statusLine``) into a Claude settings file.

    Every other key is preserved byte-for-byte in value. Never touches ``hooks``.
    """
    existed = path.exists()
    try:
        data: dict = json.loads(path.read_text(encoding="utf-8")) if existed else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc}); fix or move the file, then re-run") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    changes: list[str] = []
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
        if "env" in data:
            changes.append("env: replaced non-object value")
    if env.get("JSWARM_COMMON") != str(common_dir):
        changes.append(f"env.JSWARM_COMMON = {common_dir}")
    env = {**env, "JSWARM_COMMON": str(common_dir)}
    data["env"] = env
    if status_line is not None and data.get("statusLine") != status_line:
        data["statusLine"] = status_line
        changes.append("statusLine updated")
    action = "noop" if (existed and not changes) else ("update" if existed else "create")
    backup = None
    if action != "noop" and not dry_run:
        backup = _backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"action": action, "changes": changes, "backup": backup, "path": str(path)}


# --------------------------------------------------------------------------- CLAUDE.md
def extract_block(master_text: str) -> str:
    s, e = master_text.find(JSWARM_START), master_text.find(JSWARM_END)
    if s == -1 or e == -1:
        raise ValueError("master is missing JSWARM:START/END markers")
    return master_text[s : e + len(JSWARM_END)]


def sync_claude_md_block(claude_md: Path, master: Path, *, dry_run: bool = False) -> str:
    try:
        block = extract_block(master.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{master}: {exc}") from exc
    existing = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    if not existing:
        action, new = "create", block + "\n"
    else:
        # JSWARM first: an OMC block is only the replacement target when the
        # file has no JSWARM block at all.
        m = _JSWARM_BLOCK_RE.search(existing) or _OMC_BLOCK_RE.search(existing)
        if m and m.group(0) == block:
            return "noop"
        if m:
            action, new = "replace", existing[: m.start()] + block + existing[m.end() :]
        else:
            action, new = "prepend", block + "\n\n" + existing.lstrip("\n")
    count = new.count(JSWARM_START)
    if count != 1:
        raise ValueError(f"{claude_md}: refusing to write, {count} JSWARM blocks would remain")
    if not dry_run:
        _backup(claude_md)
        claude_md.parent.mkdir(parents=True, exist_ok=True)
        claude_md.write_text(new, encoding="utf-8")
    return action


# --------------------------------------------------------------------------- verify
def verify_report(*, home: Path, common_dir: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    settings = home / ".claude" / "settings.json"
    env_ok = False
    if settings.exists():
        try:
            env_ok = json.loads(settings.read_text(encoding="utf-8")).get("env", {}).get("JSWARM_COMMON") == str(common_dir)
        except json.JSONDecodeError:
            pass
    rows.append(("settings env JSWARM_COMMON", "ON" if env_ok else "OFF", str(settings)))
    py = common_dir / ".venv" / "bin" / "python"
    rows.append(("venv python", "ON" if py.exists() else "OFF", str(py)))
    cm = home / ".claude" / "CLAUDE.md"
    rows.append(("CLAUDE.md JSWARM block", "ON" if cm.exists() and JSWARM_START in cm.read_text(encoding="utf-8") else "OFF", str(cm)))
    for skill in DAY0_SKILLS:
        p = home / ".claude" / "skills" / skill
        rows.append((f"skill /{skill}", "ON" if p.exists() else "OFF", str(p)))
    agents = home / ".claude" / "agents"
    present = [c for c in CANONICAL_CORES if (agents / f"{c}.md").exists()]
    rows.append(("agents (canonical j-cores)", "ON" if len(present) == len(CANONICAL_CORES) else "OFF", f"{len(present)}/{len(CANONICAL_CORES)} in {agents}"))
    return rows


# --------------------------------------------------------------------------- adopt
ADOPT_START = "<!-- JSWARM-ADOPT:START -->"
ADOPT_END = "<!-- JSWARM-ADOPT:END -->"
GITIGNORE_LINES = (
    "# JSWARM runtime state (COM-398 adopt)",
    ".jswarm/state/",
    ".jswarm/logs/",
    ".jswarm/reports/",
    ".jarviswarm/cleanup-ledger-*.json",
    "docs/plans/.jPlanStatus.json",
)


class AdoptError(RuntimeError):
    pass


def render_project_settings(master_settings: Path) -> dict:
    """Project hook registration for a NON-common repo.

    Copies every hook entry from the repo.common master except those that call
    scripts under ``$CLAUDE_PROJECT_DIR/.claude/hooks`` (common-only hooks).
    """
    data = json.loads(master_settings.read_text(encoding="utf-8"))
    hooks_out: dict = {}
    for event, matchers in (data.get("hooks") or {}).items():
        kept = []
        for m in matchers:
            entries = [h for h in m.get("hooks", []) if "$CLAUDE_PROJECT_DIR/.claude/hooks" not in h.get("command", "")]
            if entries:
                kept.append({**m, "hooks": entries})
        if kept:
            hooks_out[event] = kept
    return {"hooks": hooks_out}


def _agents_block(common_dir: Path, jira_key: str | None) -> str:
    return "\n".join([
        ADOPT_START,
        "# JSWARM",
        "This repository is adopted by JarviSWARM. Shared policy, templates, and lifecycle docs live in",
        f"`${JSWARM_HOME:-$HOME/dev/jswarm}` (`$JSWARM_COMMON`, currently `{common_dir}`). Start work with `/jPlan`; read",
        "`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/_JarviSWARM/README.md` for the method.",
        f"Jira project: {jira_key or 'UNRESOLVED (set jira_project_key in .claude/project-command-injections.yaml)'}",
        ADOPT_END,
        "",
    ])


def adopt(project_root: Path, *, common_dir: Path, jira_key: str | None, hooks: bool = True, dry_run: bool = False) -> dict:
    """Adopt an existing git checkout into JSWARM.

    Completion is marked by ``.jswarm/.adopted`` (one line, an ISO timestamp),
    written as the very last step and never under ``dry_run``. The "already
    adopted" guard raises ``AdoptError`` only when that marker exists. A
    ``.jswarm/`` directory without the marker means a previous run failed
    part-way through; ``adopt`` then resumes idempotently (``force=True`` into
    ``bootstrap_project_injections`` so pre-existing manifest/snippet files from
    the failed run are overwritten rather than tripping a collision error)
    instead of refusing.
    """
    from jswarm.devops_command_injection import bootstrap_project_injections  # local import: heavy module

    project_root = Path(project_root).resolve()
    if not (project_root / ".git").exists():
        raise AdoptError(f"{project_root} is not a git checkout")
    marker = project_root / ".jswarm" / ".adopted"
    if marker.exists():
        raise AdoptError(f"{project_root} is already adopted ({marker} exists)")
    resuming = (project_root / ".jswarm").exists()
    key = jira_key.strip().upper() if jira_key else None
    writes: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []

    # 0. Pre-flight, before any write. An unreadable project settings file must
    # abort the whole run, and a dirty checkout warns but continues (spec 7).
    settings_path = project_root / ".claude" / "settings.json"
    existing_settings: dict = {}
    if hooks and settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AdoptError(f"{settings_path}: not valid JSON ({exc}); fix or move the file, then re-run adopt") from exc
        if not isinstance(loaded, dict):
            raise AdoptError(f"{settings_path}: top level must be a JSON object")
        existing_settings = loaded
    status = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    if status.returncode == 0 and status.stdout.strip():
        n = len(status.stdout.strip().splitlines())
        warnings.append(
            f"{project_root} has {n} uncommitted change(s); adopt continues, but commit or stash "
            "first so the adopt layer lands in a commit of its own"
        )

    # 1. command-injection manifest (+ jira key parameter)
    result = bootstrap_project_injections(project_root=project_root, common_root=common_dir, write=not dry_run, force=resuming, jira_key=key)
    writes.append(str(result.manifest_path))
    writes += [str(project_root / rel) for rel in result.snippet_texts]

    # 2. project hook registration. An existing settings file keeps every key it
    # already had (permissions, env, model, statusLine, ...); only "hooks" is
    # replaced, and the original is backed up first.
    if hooks:
        rendered = render_project_settings(common_dir / "docs" / "_CONTROLLED_CONFIG" / "dotclaude" / "repo.common" / "settings.json")
        writes.append(str(settings_path))
        if not dry_run:
            if settings_path.exists():
                _backup(settings_path)
            merged = {**existing_settings, **rendered}
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    else:
        skipped.append(".claude/settings.json")

    # 3. runtime dirs + gitignore
    for sub in ("plans", "state", "reports", "logs"):
        writes.append(str(project_root / ".jswarm" / sub))
        if not dry_run:
            (project_root / ".jswarm" / sub).mkdir(parents=True, exist_ok=True)
            (project_root / ".jswarm" / sub / ".gitkeep").touch()
    gi = project_root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    missing = [l for l in GITIGNORE_LINES if l not in existing.splitlines()]
    if missing:
        writes.append(str(gi))
        if not dry_run:
            gi.write_text(existing.rstrip("\n") + ("\n" if existing else "") + "\n".join(missing) + "\n", encoding="utf-8")

    # 4. AGENTS.md managed block
    am = project_root / "AGENTS.md"
    text = am.read_text(encoding="utf-8") if am.exists() else ""
    if ADOPT_START not in text:
        writes.append(str(am))
        if not dry_run:
            am.write_text((text.rstrip("\n") + "\n\n" if text else "") + _agents_block(common_dir, key), encoding="utf-8")

    # 5. completion marker (must be last: its existence is the "already adopted" guard)
    writes.append(str(marker))
    if not dry_run:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(_now_iso() + "\n", encoding="utf-8")
    return {"writes": writes, "skipped": skipped, "warnings": warnings, "jira_key": key, "project_root": str(project_root)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("settings-merge", "claude-md-sync", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--home", type=Path, default=Path(os.environ.get("HOME", str(Path.home()))))
        sp.add_argument("--common", type=Path, default=None)
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--status-line-json", default=None)
        sp.add_argument("--master", type=Path, default=None)
    sp = sub.add_parser("adopt")
    sp.add_argument("repo", type=Path)
    sp.add_argument("--jira-key")
    sp.add_argument("--no-hooks", action="store_true")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--common", type=Path)
    args = parser.parse_args(argv)
    common = args.common or Path(__file__).resolve().parents[2]
    if args.cmd == "settings-merge":
        sl = json.loads(args.status_line_json) if args.status_line_json else None
        try:
            result = merge_settings(args.home / ".claude" / "settings.json", common_dir=common, status_line=sl, dry_run=args.dry_run)
        except (ValueError, OSError) as exc:
            print(f"settings-merge: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result))
        return 0
    if args.cmd == "claude-md-sync":
        master = args.master or common / "docs" / "_CONTROLLED_CONFIG" / "jswarm-global-claude-bootstrap.md"
        try:
            action = sync_claude_md_block(args.home / ".claude" / "CLAUDE.md", master, dry_run=args.dry_run)
        except (ValueError, OSError) as exc:
            print(f"claude-md-sync: {exc}", file=sys.stderr)
            return 1
        print(action)
        return 0
    if args.cmd == "adopt":
        from jswarm.devops_command_injection import CommandInjectionError  # local import: heavy module

        try:
            result = adopt(args.repo, common_dir=common, jira_key=args.jira_key, hooks=not args.no_hooks, dry_run=args.dry_run)
        except (AdoptError, CommandInjectionError, OSError, ValueError) as exc:
            print(f"adopt: {exc}", file=sys.stderr)
            return 1
        for w in result["warnings"]:
            print(f"adopt: warning: {w}", file=sys.stderr)
        print(json.dumps(result, indent=2))
        return 0
    rows = verify_report(home=args.home, common_dir=common)
    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"{name.ljust(width)}  {status:3}  {detail}")
    return 0 if all(s == "ON" for _, s, _ in rows) else 1


_main = main  # backwards-compatible alias for callers that predate the rename


if __name__ == "__main__":
    sys.exit(main())
