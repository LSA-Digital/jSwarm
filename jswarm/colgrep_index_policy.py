#!/usr/bin/env python3
"""Phase 2 — ColGREP active-project ``colgrep:`` lifecycle policy + fleet coverage.

Extends the active-projects registry with a per-project ``colgrep:`` policy block and
provides the fleet *coverage planner* that decides whether an active project's
worktree index should be built/repaired, reported, or left alone — but **never
deleted**. Deletion is owned solely by the guarded-delete chokepoint
(``jswarm.colgrep_index_delete_guard.guarded_delete_next_plaid_index``) plus the
Phase 3 evictor; this module proposes repair, not cleanup.

Policy shape (layered over safe defaults; unspecified fields keep the default):

    active_projects:
      - id: hai-sim-engine
        path: ~/dev/hai-sim-engine
        default: true
        colgrep:
          auto_index_base: true
          auto_index_worktrees: true
          max_live_generations: 1
          cleanup_tier: active
          ambiguous_prompt: true

Authorization rule: a worktree authorizes as an active-project worktree by
its RESOLVED ROOT PATH (path containment), not by a basename match. Two repos that
share a basename must never cross-authorize. Logical index *families* (used by the
classifier's per-family generation cap) resolve by project-id prefix, since family
names (``{project_id}-wt-{ticket}``) are allocator-controlled.

Fail-closed contract:
  * Unreadable / non-mapping / no-active-projects YAML  -> ``trusted=False`` and
    ``resolve_for_path`` returns ``None`` (authorize nothing; coverage = ambiguous).
  * A malformed ``colgrep:`` block  -> that project falls back to the full safe
    ``DEFAULT_POLICY`` and records ``policy_error`` (never trust partial/garbage
    eviction-permissive values).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

# Run both as `python -m jswarm.colgrep_index_policy` and as a direct script.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_ACTIVE_PROJECTS_PATH = (
    Path(__file__).resolve().parents[1] / "jswarm" / "config" / "active-projects.yaml"
)

CLEANUP_TIER_DEFAULT = "active"
VALID_CLEANUP_TIERS = ("active", "conservative", "protected")

# Safe upper bound for the per-family retained-generation cap. A value above this is
# almost certainly a config error (typo / merge conflict / bad operator input) that
# would disable superseded-generation cleanup, so it fails closed to DEFAULT_POLICY
# rather than being trusted (Phase-2 critic MAJOR-1). The plan default is 1.
MAX_LIVE_GENERATIONS_CAP = 5

# `-wt-` is the reserved structural delimiter in worktree family names
# ({project_id}-wt-{ticket}). A project id or path-derived base name that contains it
# is ambiguous and would let family<->project resolution cross-bleed or mis-compute a
# base project, so it is rejected at load time (Phase-2 critic MAJOR-3).
RESERVED_FAMILY_DELIMITER = "-wt-"

# Coverage decision vocabulary. NONE of these is ever a delete/cleanup decision —
# that is the A/C 3 safety invariant: an active worktree missing its index is
# repaired/enqueued or reported, never proposed for eviction.
COVERAGE_REPAIR = "repair-enqueue"
COVERAGE_REPORT = "report-missing"
COVERAGE_OK = "indexed-ok"
COVERAGE_SKIP_INACTIVE = "skip-inactive"
COVERAGE_SKIP_UNCONFIGURED = "skip-unconfigured"
COVERAGE_AMBIGUOUS = "ambiguous-untrusted"
COVERAGE_DECISIONS = (
    COVERAGE_REPAIR, COVERAGE_REPORT, COVERAGE_OK,
    COVERAGE_SKIP_INACTIVE, COVERAGE_SKIP_UNCONFIGURED, COVERAGE_AMBIGUOUS,
)


# --------------------------------------------------------------------------- #
# Policy model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ColgrepPolicy:
    """Per-project ColGREP lifecycle policy. Defaults are the SAFE defaults:
    worktrees are repaired (indexed), not left to look like orphans; the
    generation cap matches NFR-204-STALE-GENERATION-CAP (default 1); ambiguous
    cases still prompt."""

    auto_index_base: bool = True
    auto_index_worktrees: bool = True
    max_live_generations: int = 1
    cleanup_tier: str = CLEANUP_TIER_DEFAULT
    ambiguous_prompt: bool = True


DEFAULT_POLICY = ColgrepPolicy()


@dataclass(frozen=True)
class ResolvedProject:
    project_id: str
    root_path: Path            # resolved, absolute
    is_active: bool
    policy: ColgrepPolicy
    policy_error: str | None = None


@dataclass(frozen=True)
class IndexPolicySet:
    projects: tuple[ResolvedProject, ...]
    trusted: bool
    error: str | None = None

    @property
    def active(self) -> tuple[ResolvedProject, ...]:
        return tuple(p for p in self.projects if p.is_active)

    @property
    def inactive(self) -> tuple[ResolvedProject, ...]:
        return tuple(p for p in self.projects if not p.is_active)

    def resolve_for_path(self, path: str | Path) -> ResolvedProject | None:
        """Most-specific project whose resolved root contains ``path`` (active OR
        inactive). Returns ``None`` if untrusted or no root contains the path."""
        if not self.trusted:
            return None
        target = _resolve_abs(path)
        best: ResolvedProject | None = None
        for proj in self.projects:
            if _is_within(target, proj.root_path):
                if best is None or len(str(proj.root_path)) > len(str(best.root_path)):
                    best = proj
        return best

    def policy_for_path(self, path: str | Path) -> ColgrepPolicy:
        owner = self.resolve_for_path(path)
        return owner.policy if owner is not None else DEFAULT_POLICY

    def resolve_for_family(self, family: str | None) -> ResolvedProject | None:
        """Resolve a logical index family (``{project_id}-wt-{ticket}`` or a bare
        base ``{project_id}``) to its project by id prefix. Family names are
        allocator-controlled, so prefix matching on the ``-wt-`` boundary is safe."""
        if not self.trusted or not family:
            return None
        fam = family.strip().lower()
        best: ResolvedProject | None = None
        for proj in self.projects:
            pid = proj.project_id.strip().lower()
            if not pid:
                continue
            if fam == pid or fam.startswith(pid + "-wt-"):
                if best is None or len(proj.project_id) > len(best.project_id):
                    best = proj
        return best

    def resolve_policy_for_family(self, family: str | None) -> ColgrepPolicy | None:
        owner = self.resolve_for_family(family)
        return owner.policy if owner is not None else None


# --------------------------------------------------------------------------- #
# Parsing (fail-closed)
# --------------------------------------------------------------------------- #
def _resolve_abs(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _is_within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _parse_colgrep_block(raw: Any) -> tuple[ColgrepPolicy, str | None]:
    """Layered merge over ``DEFAULT_POLICY``. ANY malformed field collapses the whole
    block to the safe default (fail-closed): partial/garbage eviction-permissive
    values are never trusted."""
    if raw is None:
        return DEFAULT_POLICY, None
    if not isinstance(raw, dict):
        return DEFAULT_POLICY, "colgrep block is not a mapping"

    errors: list[str] = []
    fields: dict[str, Any] = {}

    for key in ("auto_index_base", "auto_index_worktrees", "ambiguous_prompt"):
        if key in raw:
            value = raw[key]
            if isinstance(value, bool):
                fields[key] = value
            else:
                errors.append(f"{key} must be a bool")

    if "max_live_generations" in raw:
        value = raw["max_live_generations"]
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append("max_live_generations must be an int")
        elif value < 1:
            errors.append("max_live_generations must be >= 1")
        elif value > MAX_LIVE_GENERATIONS_CAP:
            # Fail closed on an unbounded/huge cap (would disable superseded cleanup).
            errors.append(f"max_live_generations must be <= {MAX_LIVE_GENERATIONS_CAP}")
        else:
            fields["max_live_generations"] = value

    if "cleanup_tier" in raw:
        value = raw["cleanup_tier"]
        if isinstance(value, str) and value in VALID_CLEANUP_TIERS:
            fields["cleanup_tier"] = value
        else:
            errors.append("cleanup_tier must be one of " + ", ".join(VALID_CLEANUP_TIERS))

    if errors:
        return DEFAULT_POLICY, "; ".join(errors)
    return replace(DEFAULT_POLICY, **fields), None


def _load_yaml(path: Path) -> Any:
    import yaml  # PyYAML is provided by the project .venv; a missing parser fails closed.

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_index_policies(
    active_projects_path: str | Path = DEFAULT_ACTIVE_PROJECTS_PATH,
) -> IndexPolicySet:
    """Load active-projects.yaml into a trusted/untrusted policy set (fail-closed)."""
    path = Path(active_projects_path).expanduser()
    try:
        payload = _load_yaml(path)
    except Exception as exc:  # noqa: BLE001 - any read/parse failure must fail closed.
        return IndexPolicySet(projects=(), trusted=False, error=f"unreadable: {path}: {exc}")

    if not isinstance(payload, dict):
        return IndexPolicySet(projects=(), trusted=False, error=f"not a mapping: {path}")

    projects: list[ResolvedProject] = []
    for is_active, key in ((True, "active_projects"), (False, "inactive_projects")):
        rows = payload.get(key)
        if rows is None:
            continue
        if not isinstance(rows, list):
            return IndexPolicySet(projects=(), trusted=False, error=f"{key} is not a list: {path}")
        for row in rows:
            if not isinstance(row, dict):
                return IndexPolicySet(projects=(), trusted=False, error=f"{key} row is not a mapping: {path}")
            pid = row.get("id")
            raw_path = row.get("path")
            if not (isinstance(pid, str) and pid.strip()):
                return IndexPolicySet(projects=(), trusted=False, error=f"{key} row missing id: {path}")
            if not (isinstance(raw_path, str) and raw_path.strip()):
                return IndexPolicySet(projects=(), trusted=False, error=f"{key} row missing path: {path}")
            # Fail closed: a project id or base name containing the reserved family
            # delimiter `-wt-` is ambiguous and would cross-bleed family<->project
            # resolution / mis-compute base projects (critic MAJOR-3).
            base_name = Path(raw_path).expanduser().name
            if RESERVED_FAMILY_DELIMITER in pid.strip() or RESERVED_FAMILY_DELIMITER in base_name:
                return IndexPolicySet(
                    projects=(), trusted=False,
                    error=f"{key} row id/base name contains reserved delimiter "
                          f"'{RESERVED_FAMILY_DELIMITER}' (ambiguous family ownership): {path}")
            policy, policy_error = _parse_colgrep_block(row.get("colgrep"))
            projects.append(ResolvedProject(
                project_id=pid.strip(),
                root_path=_resolve_abs(raw_path),
                is_active=is_active,
                policy=policy,
                policy_error=policy_error,
            ))

    if not any(p.is_active for p in projects):
        return IndexPolicySet(projects=(), trusted=False, error=f"no active projects: {path}")

    return IndexPolicySet(projects=tuple(projects), trusted=True, error=None)


# --------------------------------------------------------------------------- #
# Fleet coverage planner (proposes repair, NEVER deletion)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WorktreeCandidate:
    path: Path
    family: str | None = None
    has_loaded_index: bool = False
    has_registry_row: bool = False


@dataclass(frozen=True)
class CoverageDecision:
    worktree_path: str
    project_id: str | None
    decision: str
    reasons: tuple[str, ...]
    has_loaded_index: bool
    has_registry_row: bool
    family: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "worktree_path": self.worktree_path,
            "project_id": self.project_id,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "has_loaded_index": self.has_loaded_index,
            "has_registry_row": self.has_registry_row,
            "family": self.family,
        }


def _coverage_one(policy_set: IndexPolicySet, cand: WorktreeCandidate) -> CoverageDecision:
    path_str = str(cand.path)

    def decision(value: str, reason: str, project_id: str | None) -> CoverageDecision:
        return CoverageDecision(
            worktree_path=path_str, project_id=project_id, decision=value,
            reasons=(reason,), has_loaded_index=cand.has_loaded_index,
            has_registry_row=cand.has_registry_row, family=cand.family,
        )

    if not policy_set.trusted:
        return decision(
            COVERAGE_AMBIGUOUS,
            f"active-projects policy untrusted/unreadable ({policy_set.error}) — "
            "cannot authorize repair; held ambiguous, no action",
            None,
        )

    owner = policy_set.resolve_for_path(cand.path)
    if owner is None:
        return decision(
            COVERAGE_SKIP_UNCONFIGURED,
            "path is not within any configured project root — not an active worktree; no action",
            None,
        )
    if not owner.is_active:
        return decision(
            COVERAGE_SKIP_INACTIVE,
            f"path belongs to inactive project '{owner.project_id}' — coverage planner takes no "
            "repair action; deletion (if any) is handled only by the separate certain-only "
            "lifecycle cleanup path and never for bases",
            owner.project_id,
        )
    if cand.has_loaded_index:
        return decision(
            COVERAGE_OK,
            f"active project '{owner.project_id}' worktree has a loaded :3280 index",
            owner.project_id,
        )
    if not owner.policy.auto_index_worktrees:
        return decision(
            COVERAGE_REPORT,
            f"active project '{owner.project_id}' worktree is missing its index but "
            "colgrep.auto_index_worktrees=false — reported, not built, never deleted",
            owner.project_id,
        )
    return decision(
        COVERAGE_REPAIR,
        f"active project '{owner.project_id}' worktree is missing its index with "
        "auto_index_worktrees=true — enqueue build/repair (NOT orphan cleanup)",
        owner.project_id,
    )


def plan_active_worktree_index_coverage(
    *,
    policy_set: IndexPolicySet,
    candidates: Iterable[WorktreeCandidate],
) -> list[CoverageDecision]:
    """For each candidate worktree, decide index-coverage action. The planner never
    returns a delete/cleanup decision — an active worktree missing its index is
    repaired/enqueued or reported; inactive/unconfigured/untrusted are left alone."""
    return [_coverage_one(policy_set, cand) for cand in candidates]


# --------------------------------------------------------------------------- #
# CLI helpers (consumed by colgrep_index_lifecycle.py `coverage` subcommand)
# --------------------------------------------------------------------------- #
def _strict_bool(row: dict[str, Any], key: str) -> bool:
    """Require an actual JSON boolean. A present-but-non-bool value (e.g. the string
    "false", "0", "no", or a number) is REJECTED rather than truthiness-coerced —
    coercing "false" -> True would false-green a missing index as `indexed-ok`
    (critic MAJOR-2). Absent defaults to False (the safe "not indexed" direction).
    """
    if key not in row:
        return False
    value = row[key]
    if not isinstance(value, bool):
        raise ValueError(f"candidate field '{key}' must be a JSON boolean, got {value!r}")
    return value


def _candidates_from_json(data: Any) -> list[WorktreeCandidate]:
    if not isinstance(data, list):
        raise ValueError("candidates JSON must be a list of objects")
    out: list[WorktreeCandidate] = []
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("each candidate needs a string 'path'")
        family = row.get("family")
        if family is not None and not isinstance(family, str):
            raise ValueError(f"candidate field 'family' must be a string or absent, got {family!r}")
        out.append(WorktreeCandidate(
            path=Path(row["path"]),
            family=family,
            has_loaded_index=_strict_bool(row, "has_loaded_index"),
            has_registry_row=_strict_bool(row, "has_registry_row"),
        ))
    return out


def coverage_report(
    *,
    active_projects_path: str | Path,
    candidates: Iterable[WorktreeCandidate],
) -> dict[str, Any]:
    policy_set = load_index_policies(active_projects_path)
    decisions = plan_active_worktree_index_coverage(policy_set=policy_set, candidates=candidates)
    counts: dict[str, int] = {d: 0 for d in COVERAGE_DECISIONS}
    for decision in decisions:
        counts[decision.decision] = counts.get(decision.decision, 0) + 1
    return {
        "dry_run": True,  # the planner never mutates; coverage is always read-only.
        "policy_trusted": policy_set.trusted,
        "policy_error": policy_set.error,
        "counts": counts,
        "coverage": [d.to_dict() for d in decisions],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ColGREP active-project policy + fleet coverage planner (read-only).")
    parser.add_argument("--active-projects", default=str(DEFAULT_ACTIVE_PROJECTS_PATH))
    parser.add_argument("--candidates", required=True, help="JSON file: list of worktree candidates.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="No-op: coverage planning never mutates. Accepted for operator consistency.")
    args = parser.parse_args(argv)

    candidates = _candidates_from_json(json.loads(Path(args.candidates).read_text(encoding="utf-8")))
    report = coverage_report(active_projects_path=args.active_projects, candidates=candidates)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
