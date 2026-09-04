"""A/C 3: per-project config resolver.

A work item is either a tracker key (``PS-14``) or a local slug
(``add-csv-export``) -- see ``jswarm.workitem.identity`` and section 4 of
docs/superpowers/specs/2026-09-03-jswarm-public-repo-split-design.md. Plan
status must work identically for either: a tracker key is only meaningful
once a tracker is configured, but a project WITHOUT a tracker is the
default, documented path, not a degraded one. ``enabled`` therefore no
longer depends on a Jira key prefix being resolvable at all -- it only
depends on this being a real, plan-tracking repo (a resolved ``.git`` root
with a plans directory). The Jira key, when resolved, is still used to
build ``ticket_regex`` for cross-project ownership checks on tracker-key
tickets (see ``ticket_matches``); a slug ticket never needs one.

Reads jswarm/config/active-projects.yaml (substrate, absent by default; see
that path's ``.exists()`` guard below) to identify the project, then resolves
the Jira key + ticket regex + plans dir.

Resolution order for the Jira key:
  1. Optional `jira_key:` field on the matching active-projects.yaml entry.
  2. The adopted project's own `.jswarm/config.yaml` `tracker.key_prefix`
     (the same config `jswarm.tracker.resolve.load` reads to build the live
     tracker; see `jswarm/installer/adopt.py`, which writes it).
  3. None -> no tracker configured; tracker-key tickets never match, slugs
     always do (see ``ticket_matches``).

There is no built-in table mapping real project names to ticket-key
prefixes. A project's prefix is either recorded in its own committed
`.jswarm/config.yaml` (once it has adopted a tracker) or not resolved at
all; there is nothing left to guess from.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

try:
    import yaml
except Exception:  # pragma: no cover - yaml is a hard dep in this repo
    yaml = None

# Insert the repository root (parents[2]: plan_status/ -> jswarm/ -> repo root), not
# jswarm/ itself (parents[1]), which would shadow the stdlib for anything under
# jswarm/ sharing a name with it (e.g. jswarm/platform/ vs the stdlib platform module).
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from jswarm.workitem import identity as _workitem_identity

ACTIVE_PROJECTS_REL = "jswarm/config/active-projects.yaml"


@dataclass
class ProjectConfig:
    enabled: bool
    project_id: str
    project_key: Optional[str]
    ticket_regex: Optional[str]
    plans_dir: Optional[Path]
    repo_root: Path
    source: str  # how the key was resolved: 'active-projects' | 'tracker-config' | 'unresolved'


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


def _tracker_key_prefix(repo_root: Path) -> Optional[str]:
    """Read the adopted project's own `<repo_root>/.jswarm/config.yaml`.

    Mirrors the config shape `jswarm.tracker.resolve.load` reads (this
    resolver only needs the prefix string, not a live Tracker, so it reads
    the file directly rather than importing that module). A missing file,
    a missing `tracker` key, or an empty `key_prefix` all resolve to None.
    """
    if yaml is None:
        return None
    config_path = repo_root / ".jswarm" / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        data = cast(dict[str, Any], yaml.safe_load(config_path.read_text()) or {})
    except Exception:
        return None
    tracker_cfg = data.get("tracker") or {}
    prefix = tracker_cfg.get("key_prefix")
    return str(prefix) if prefix else None


def _resolve_plans_dir(repo_root: Path) -> Path:
    """Prefer the canonical ``.jswarm/plans`` (all new plan writes -- tracker-key or
    slug alike -- land there per skills/jPlan/CHANGELOG.md); fall back to the legacy
    ``docs/plans`` only when it exists and the canonical dir does not, so a project
    that predates the ``.jswarm/plans`` move keeps resolving without migration.
    Never requires a tracker key: plan tracking is enabled by having plans, not by
    having a Jira prefix (see module docstring)."""
    canonical = repo_root / ".jswarm" / "plans"
    if canonical.exists():
        return canonical
    legacy = repo_root / "docs" / "plans"
    if legacy.exists():
        return legacy
    return canonical


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
    source = "no-tracker"

    # 1. active-projects.yaml match (by resolved path) with optional jira_key.
    for entry in _load_active_projects(common_root):
        entry_path = _expand(str(entry.get("path", "")))
        if entry_path == repo_root:
            project_id = str(entry.get("id", project_id))
            if entry.get("jira_key"):
                project_key = str(entry["jira_key"])
                source = "active-projects"
            break

    # 2. The adopted project's own .jswarm/config.yaml tracker.key_prefix.
    if project_key is None:
        configured = _tracker_key_prefix(repo_root)
        if configured:
            project_key = configured
            source = "tracker-config"

    plans_dir = _resolve_plans_dir(repo_root)
    ticket_regex = rf"^{project_key}-\d+$" if project_key else None
    # Enabled means "this is a real, plan-tracking repo" -- NOT "a tracker is
    # configured". A tracker-free project (tracker: {adapter: none}, the default
    # adopt writes) is the documented default path, not a degraded one: plan status
    # must work for it exactly as it does for a Jira-tracked project. See module
    # docstring and docs/superpowers/specs/2026-09-03-jswarm-public-repo-split-
    # design.md section 4.
    enabled = plans_dir.exists()

    return ProjectConfig(
        enabled=enabled,
        project_id=project_id,
        project_key=project_key,
        ticket_regex=ticket_regex,
        plans_dir=plans_dir if plans_dir.exists() else None,
        repo_root=repo_root,
        source=source if enabled else "unresolved",
    )


def ticket_matches(config: ProjectConfig, ticket: str) -> bool:
    """Whether ``ticket`` is a work item this project's plan-status registry owns.

    Delegates identity recognition to ``jswarm.workitem.identity`` rather than a
    private regex (see module docstring). A slug is always local to the one repo
    ``config`` was resolved from, so it always matches -- there is no tracker
    prefix to disambiguate against, and none is needed. A tracker key only matches
    when it carries THIS project's configured prefix, exactly as before.
    """
    try:
        work_id = _workitem_identity.parse(ticket)
    except _workitem_identity.WorkItemIdError:
        return False
    if work_id.kind == "slug":
        return True
    if not config.ticket_regex:
        return False
    return bool(re.match(config.ticket_regex, ticket))


# A real legacy plan file is docs/plans/<KEY-NNN>-<description>.md. That naming
# scheme predates jswarm.workitem.identity / slug support entirely (it is only
# ever produced by an old Jira-ticket-based project migrated from before the
# tracker-free split), so it is genuinely tracker-key-only -- left as-is, not a
# missed instance of the identity bug. Its "-" separator would also be
# ambiguous against a slug's own "-"-delimited alphabet with no way to tell
# id from description apart.
#
# The canonical plan file, .jswarm/plans/<ID>.plan.<description>.md, is where
# ALL new plan writes land for either identity form (see skills/jPlan/
# CHANGELOG.md) -- its ".plan." separator is unambiguous (slugs never contain
# a literal "."), so it accepts both a tracker key and a slug, delegating to
# jswarm.workitem.identity rather than a private regex.
# Suffix artifacts (KEY-NNN.specs.md etc.) and evidence/ subpaths are excluded.
# Shared by the post-edit hook, reconciler, doctor, and backfill.
_LEGACY_PLAN_FILE_RE = re.compile(r"^([A-Z][A-Z0-9_]+-\d+)-[^/]*\.md$")
_JSWARM_PLAN_FILE_RE = re.compile(
    rf"^((?:{_workitem_identity.TRACKER_KEY[1:-1]}|{_workitem_identity.SLUG[1:-1]}))\.plan\.[^/]*\.md$"
)
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
