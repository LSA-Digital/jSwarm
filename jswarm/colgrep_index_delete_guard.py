#!/usr/bin/env python3
"""Guarded ColGREP index deletion chokepoint.

All client-side DELETE /indices/{name} calls for ColGREP code indices must route
through this module so active-project base indices cannot be evicted by cleanup,
teardown, reaper, or capacity paths.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTIVE_PROJECTS_PATH = REPO_ROOT / "docs" / "_CONTROLLED_CONFIG" / "active-projects.yaml"
DEFAULT_BASE_ALIASES_PATH = Path.home() / "dev" / "colgrep-idx" / "base-aliases.json"
DEFAULT_EVIDENCE_LOG_PATH = Path.home() / "dev" / "colgrep-idx" / "log" / "index-delete-guard.jsonl"
ACTIVE_BASE_REFUSAL_REASON = "active-project base index is undeletable"


class ActiveProjectsUnavailable(RuntimeError):
    """Raised when the active-project registry cannot be trusted."""


class BaseAliasesUnavailable(RuntimeError):
    """Raised when an existing base-alias registry cannot be trusted."""


def _load_yaml_like(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_active_projects_minimal(path)
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ActiveProjectsUnavailable(f"{path} did not contain a mapping")
    return loaded


def _parse_active_projects_minimal(path: Path) -> dict[str, Any]:
    """Tiny fallback parser for docs/_CONTROLLED_CONFIG/active-projects.yaml."""

    rows: list[dict[str, str]] = []
    in_active = False
    current: dict[str, str] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("active_projects:"):
            in_active = True
            current = None
            continue
        if line and not line.startswith(" ") and not line.startswith("-"):
            if in_active:
                break
        if not in_active:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                rows.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if stripped and ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = value.strip().strip('"\'')
        elif current is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = value.strip().strip('"\'')
    if current:
        rows.append(current)
    return {"active_projects": rows}


def active_project_base_names(active_projects_path: str | Path = DEFAULT_ACTIVE_PROJECTS_PATH) -> set[str]:
    """Return active logical base index names from active-projects.yaml.

    Both each active project's configured `id` and `Path(path).name` are included.
    Raises ActiveProjectsUnavailable when the registry cannot be trusted.
    """

    path = Path(active_projects_path).expanduser()
    try:
        payload = _load_yaml_like(path)
    except Exception as exc:  # noqa: BLE001 - guard must fail closed on any registry uncertainty.
        raise ActiveProjectsUnavailable(f"active projects registry is unreadable: {path}: {exc}") from exc
    projects = payload.get("active_projects")
    if not isinstance(projects, list):
        raise ActiveProjectsUnavailable(f"active projects registry lacks active_projects list: {path}")
    names: set[str] = set()
    for entry in projects:
        if not isinstance(entry, dict):
            raise ActiveProjectsUnavailable(f"active project entry is not a mapping: {path}")
        project_id = entry.get("id")
        if isinstance(project_id, str) and project_id.strip():
            names.add(project_id.strip())
        raw_path = entry.get("path")
        if isinstance(raw_path, str) and raw_path.strip():
            names.add(Path(raw_path).expanduser().name)
    if not names:
        raise ActiveProjectsUnavailable(f"active projects registry produced no names: {path}")
    return names


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _add_string_alias(target: set[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        target.add(value.strip())


def _add_candidate_aliases(target: set[str], candidate: Any) -> None:
    if isinstance(candidate, str):
        _add_string_alias(target, candidate)
    elif isinstance(candidate, dict):
        for key in ("active_index", "index_name", "candidate_index", "search_index", "name", "api_index_name"):
            _add_string_alias(target, candidate.get(key))
        for key in ("active_generation", "generation", "candidate_generation"):
            _add_string_alias(target, candidate.get(key))


def active_project_served_base_aliases(
    base_aliases_path: str | Path = DEFAULT_BASE_ALIASES_PATH,
    *,
    active_projects_path: str | Path = DEFAULT_ACTIVE_PROJECTS_PATH,
    now: datetime | None = None,
) -> set[str]:
    """Return served active-project base alias/generation index names.

    Missing alias registry means no aliases. Existing-but-unreadable or malformed
    registry raises BaseAliasesUnavailable so callers can fail closed.
    """

    path = Path(base_aliases_path).expanduser()
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - existing alias registry uncertainty is safety-critical.
        raise BaseAliasesUnavailable(f"base aliases registry is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BaseAliasesUnavailable(f"base aliases registry is not a mapping: {path}")
    projects = payload.get("projects")
    if not isinstance(projects, dict):
        raise BaseAliasesUnavailable(f"base aliases registry lacks projects mapping: {path}")
    active_names = active_project_base_names(active_projects_path)
    protected: set[str] = set()
    now_dt = now or datetime.now(timezone.utc)
    for project, entry in projects.items():
        if str(project) not in active_names or not isinstance(entry, dict):
            continue
        for key in ("active_index", "active_generation", "search_index"):
            _add_string_alias(protected, entry.get(key))
        previous = entry.get("previous_index")
        grace = _parse_time(entry.get("grace_delete_after"))
        if previous and (grace is None or grace > now_dt):
            _add_string_alias(protected, previous)
            _add_string_alias(protected, entry.get("previous_generation"))
        _add_candidate_aliases(protected, entry.get("candidate"))
    return protected


def _is_worktree_or_overlay_name(name: str) -> bool:
    return "-wt-" in name or name.endswith("-overlay")


def _could_be_base_index(name: str) -> bool:
    return bool(name) and not _is_worktree_or_overlay_name(name)


def _active_generation_pattern(active_names: set[str]) -> re.Pattern[str]:
    prefixes = "|".join(re.escape(name) for name in sorted(active_names))
    return re.compile(rf"^(?:{prefixes})-g[0-9A-Za-z]{{4,}}(?:-.+)?$") if prefixes else re.compile(r"a^")


def is_active_project_base_index(
    name: str,
    *,
    active_projects_path: str | Path = DEFAULT_ACTIVE_PROJECTS_PATH,
    base_aliases_path: str | Path = DEFAULT_BASE_ALIASES_PATH,
) -> bool:
    """Return whether `name` is an undeletable active-project base index.

    Worktree/overlay names are not bases unless they exactly match a served base
    alias in the alias registry. If the active project registry is unreadable,
    fail closed for any name that could be a base index.
    """

    index_name = str(name).strip()
    try:
        active_names = active_project_base_names(active_projects_path)
    except ActiveProjectsUnavailable:
        return _could_be_base_index(index_name)
    try:
        aliases = active_project_served_base_aliases(
            base_aliases_path,
            active_projects_path=active_projects_path,
        )
    except BaseAliasesUnavailable:
        return index_name in active_names or _could_be_base_index(index_name)
    if index_name in aliases:
        return True
    if _is_worktree_or_overlay_name(index_name):
        return False
    if index_name in active_names:
        return True
    return bool(_active_generation_pattern(active_names).match(index_name))


def _is_safe_index_name(name: str) -> bool:
    """Reject path-traversal / unsafe index names before any shape match.

    Legitimate ColGREP index names never contain a path separator, ``..``, or
    whitespace/control chars; such a name reaching a shape-gated DELETE is a
    smuggling attempt and is held fail-closed (Phase-3 BLOCKER-4).
    """
    return not (
        "/" in name or "\\" in name or ".." in name
        or any(ch.isspace() or ord(ch) < 32 for ch in name)
    )


def _shape_allowed(name: str, expected_shape: str | None) -> bool:
    if expected_shape in (None, "non-active-or-policy-allowed", "capacity-policy-target", "chat"):
        return True
    # Shape-gated deletes (overlay / worktree-generation) must be safe names.
    if not _is_safe_index_name(name):
        return False
    if expected_shape == "overlay":
        return name.endswith("-overlay")
    if expected_shape == "worktree-generation-or-overlay":
        return "-wt-" in name and (name.endswith("-overlay") or re.search(r"-g[0-9A-Za-z]{4,}(?:-.+)?$", name) is not None)
    if expected_shape == "base-alias-candidate":
        return "-wt-" not in name and not name.endswith("-overlay") and re.search(r"-g[0-9A-Za-z]{4,}(?:-.+)?$", name) is not None
    return True


def _delete_index_http(api_url: str, name: str, *, timeout: float) -> dict[str, Any]:
    encoded = urllib.parse.quote(name, safe="")
    request = urllib.request.Request(f"{api_url.rstrip('/')}/indices/{encoded}", method="DELETE")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost next-plaid API.
        body_text = response.read().decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            body = body_text
        return {"status_code": int(getattr(response, "status", 200)), "body": body}


def _append_evidence(log_path: Path, event: dict[str, Any]) -> None:
    log_path = log_path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _refusal(
    name: str,
    *,
    role: str,
    api_url: str,
    active_projects_path: str | Path,
    log_path: str | Path | None,
    reason: str = ACTIVE_BASE_REFUSAL_REASON,
) -> dict[str, Any]:
    result = {"status": "refused", "name": name, "reason": reason, "role": role, "active_base_guard": True}
    if log_path is not None:
        _append_evidence(
            Path(log_path),
            {
                "event": "delete-refused-active-base",
                "name": name,
                "role": role,
                "api_url": api_url,
                "active_projects_path": str(Path(active_projects_path).expanduser()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            },
        )
    return result


def guarded_delete_next_plaid_index(
    name: str,
    *,
    api_url: str = "http://localhost:3280",
    role: str,
    active_projects_path: str | Path = DEFAULT_ACTIVE_PROJECTS_PATH,
    base_aliases_path: str | Path = DEFAULT_BASE_ALIASES_PATH,
    expected_shape: str | None = None,
    dry_run: bool = False,
    timeout: float = 30.0,
    namespace: str = "code",
    log_path: str | Path | None = DEFAULT_EVIDENCE_LOG_PATH,
    http_delete: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Delete a next-plaid index unless it is an active-project base.

    Refuses active bases before any HTTP call and emits durable evidence. Shape
    checks are an additional fail-closed guard for callers that know what kind of
    disposable index they intend to remove.
    """

    index_name = str(name).strip()
    if is_active_project_base_index(index_name, active_projects_path=active_projects_path, base_aliases_path=base_aliases_path):
        return _refusal(index_name, role=role, api_url=api_url, active_projects_path=active_projects_path, log_path=log_path)
    if not _shape_allowed(index_name, expected_shape):
        return {
            "status": "held",
            "name": index_name,
            "reason": f"index name does not match expected_shape={expected_shape}",
            "role": role,
            "active_base_guard": False,
        }
    if dry_run:
        return {"status": "would_delete", "name": index_name, "deleted_index": index_name, "dry_run": True, "role": role, "namespace": namespace}
    try:
        if http_delete is None:
            response = _delete_index_http(api_url, index_name, timeout=timeout)
        else:
            response = http_delete(api_url, index_name)
        status_code = int(response.get("status_code", 200)) if isinstance(response, dict) else 200
        status = "missing" if status_code == 404 else "removed"
        return {"status": status, "name": index_name, "deleted_index": index_name, "role": role, "namespace": namespace, "http_status": status_code}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"status": "missing", "name": index_name, "deleted_index": index_name, "role": role, "namespace": namespace, "http_status": 404}
        raise RuntimeError(f"DELETE /indices/{index_name} failed with HTTP {exc.code}: {exc}") from exc
