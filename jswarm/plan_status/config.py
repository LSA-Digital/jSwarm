"""A/C 3: per-project config resolver.

Reads jswarm/config/active-projects.yaml (substrate, absent by default — see
that path's ``.exists()`` guard below) to identify the project, then resolves
the Jira key + ticket regex + plans dir. When the key cannot be resolved,
returns enabled=False so hooks operate advisory-only (no writes).

Resolution order for the Jira key:
  1. Optional `jira_key:` field on the matching active-projects.yaml entry.
  2. Built-in PROJECT_KEY_MAP (common -> COM, hai-sim-engine -> HAS, ...).
  3. git-remote inference (best effort).
  4. None -> enabled=False.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

try:
    import yaml
except Exception:  # pragma: no cover - yaml is a hard dep in this repo
    yaml = None

# Built-in fallback map. active-projects.yaml jira_key field overrides this.
PROJECT_KEY_MAP = {
    "common": "COM",
    "hai-sim-engine": "HAS",
    "epms": "EPMS",
    "lsars-monorepo": "LSARS",
}

ACTIVE_PROJECTS_REL = "jswarm/config/active-projects.yaml"


@dataclass
class ProjectConfig:
    enabled: bool
    project_id: str
    project_key: Optional[str]
    ticket_regex: Optional[str]
    plans_dir: Optional[Path]
    repo_root: Path
    source: str  # how the key was resolved: 'active-projects' | 'builtin' | 'git-remote' | 'unresolved'


def find_repo_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (or cwd) to the nearest dir containing .git."""
    cur = Path(start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _load_active_projects(common_root: Path) -> list[dict[str, Any]]:
    path = common_root / ACTIVE_PROJECTS_REL
    if yaml is None or not path.exists():
        return []
    try:
        data = cast(dict[str, Any], yaml.safe_load(path.read_text()) or {})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for key in ("active_projects", "inactive_projects"):
        for entry in data.get(key, []) or []:
            entry = dict(cast(dict[str, Any], entry))
            entry["_active"] = key == "active_projects"
            out.append(entry)
    return out


def _expand(path_str: str) -> Path:
    return Path(path_str.replace("~", str(Path.home()))).resolve()


def _git_remote_key(repo_root: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = out.stdout.strip()
    except Exception:
        return None
    if not url:
        return None
    # Infer from repo basename in the remote URL.
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git").lower()
    return PROJECT_KEY_MAP.get(name)


def resolve_config(
    project_root: Optional[Path] = None,
    *,
    common_root: Optional[Path] = None,
) -> ProjectConfig:
    """Resolve the plan-status config for ``project_root`` (default: cwd's repo)."""
    repo_root = find_repo_root(project_root)
    if repo_root is None:
        return ProjectConfig(
            enabled=False, project_id="", project_key=None, ticket_regex=None,
            plans_dir=None, repo_root=Path(project_root or Path.cwd()).resolve(),
            source="unresolved",
        )

    # The common repo is wherever active-projects.yaml lives; default to repo_root.
    common_root = Path(common_root).resolve() if common_root else repo_root
    project_id = repo_root.name

    project_key: Optional[str] = None
    source = "unresolved"

    # 1. active-projects.yaml match (by resolved path) with optional jira_key.
    for entry in _load_active_projects(common_root):
        entry_path = _expand(str(entry.get("path", "")))
        if entry_path == repo_root:
            project_id = str(entry.get("id", project_id))
            if entry.get("jira_key"):
                project_key = str(entry["jira_key"])
                source = "active-projects"
            break

    # 2. Built-in map.
    if project_key is None:
        mapped = PROJECT_KEY_MAP.get(project_id)
        if mapped:
            project_key = mapped
            source = "builtin"

    # 3. git-remote inference.
    if project_key is None:
        inferred = _git_remote_key(repo_root)
        if inferred:
            project_key = inferred
            source = "git-remote"

    plans_dir = repo_root / "docs" / "plans"
    ticket_regex = rf"^{project_key}-\d+$" if project_key else None
    enabled = bool(project_key) and plans_dir.exists()

    return ProjectConfig(
        enabled=enabled,
        project_id=project_id,
        project_key=project_key,
        ticket_regex=ticket_regex,
        plans_dir=plans_dir if plans_dir.exists() else None,
        repo_root=repo_root,
        source=source if project_key else "unresolved",
    )


def ticket_matches(config: ProjectConfig, ticket: str) -> bool:
    if not config.ticket_regex:
        return False
    return bool(re.match(config.ticket_regex, ticket))


# A real legacy plan file is docs/plans/<KEY-NNN>-<description>.md.
# A canonical OMO plan file is .jswarm/plans/<KEY-NNN>.plan.<description>.md.
# Suffix artifacts (KEY-NNN.specs.md etc.) and evidence/ subpaths are excluded.
# Shared by the post-edit hook, reconciler, doctor, and backfill.
_LEGACY_PLAN_FILE_RE = re.compile(r"^([A-Z][A-Z0-9_]+-\d+)-[^/]*\.md$")
_JSWARM_PLAN_FILE_RE = re.compile(r"^([A-Z][A-Z0-9_]+-\d+)\.plan\.[^/]*\.md$")
_EXCLUDED_DIR_PARTS = {"evidence", "retros", "templates", "plan-templates"}


def ticket_from_plan_filename(filename: str) -> Optional[str]:
    m = _LEGACY_PLAN_FILE_RE.match(filename) or _JSWARM_PLAN_FILE_RE.match(filename)
    return m.group(1) if m else None


def _repo_root_from_plans_dir(plans_dir: Path) -> Path:
    if plans_dir.name == "plans" and plans_dir.parent.name in {"docs", ".jswarm"}:
        return plans_dir.parent.parent
    return plans_dir


def _iter_plan_roots(plans_dir: Path) -> list[Path]:
    repo_root = _repo_root_from_plans_dir(plans_dir)
    roots = [repo_root / ".jswarm" / "plans", repo_root / "docs" / "plans", Path(plans_dir)]
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def iter_plan_files(plans_dir: Path):
    """Yield (ticket, path) for real legacy and canonical plan files.

    Excludes suffix artifacts (*.specs.md / *.uat-*.md) and evidence subtrees.
    """
    plans_dir = Path(plans_dir)
    paths: list[Path] = []
    for root in _iter_plan_roots(plans_dir):
        if root.exists():
            paths.extend(root.glob("*.md"))
    for path in sorted(paths):
        if any(part in _EXCLUDED_DIR_PARTS for part in path.parts):
            continue
        ticket = ticket_from_plan_filename(path.name)
        if ticket:
            yield ticket, path


def iter_canonical_plan_files(repo_root: Path):
    """Yield (ticket, path) for CANONICAL master plans only: direct children of
    ``<repo>/.jswarm/plans/`` matching ``KEY-NNN.plan.<descr>.md`` (BLOCK-1).

    Excludes legacy ``docs/plans/``, per-ticket artifact subfolders (``.jswarm/plans/KEY/…``),
    and suffix artifacts. This is the apply set for the normalize backfill and the
    command-driven write resolver — never ``iter_plan_files``.
    """
    root = Path(repo_root) / ".jswarm" / "plans"
    if not root.exists():
        return
    for path in sorted(root.glob("*.md")):
        if path.parent != root:
            continue  # artifact under a ticket subfolder
        m = _JSWARM_PLAN_FILE_RE.match(path.name)
        if m:
            yield m.group(1), path
