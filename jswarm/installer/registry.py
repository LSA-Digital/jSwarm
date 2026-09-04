"""The global record of adopted repositories.

`uninstall` needs to list what it knows of without touching any of it
(ruling: uninstall must not delete anything it did not install). Nothing
else currently reads this; it exists so that listing is possible at all.
"""
from __future__ import annotations

import json
from pathlib import Path

from jswarm.installer.fsops import WriteContext

REGISTRY_REL_PATH = Path(".jswarm") / "adopted_repos.json"


def registry_path(home: Path) -> Path:
    return Path(home) / REGISTRY_REL_PATH


def read(home: Path) -> list[str]:
    path = registry_path(home)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def add(ctx: WriteContext, home: Path, repo: Path) -> None:
    repos = read(home)
    repo_str = str(Path(repo).resolve())
    if repo_str not in repos:
        repos.append(repo_str)
    ctx.write_json(registry_path(home), repos)


def remove(ctx: WriteContext, home: Path, repo: Path) -> None:
    repos = read(home)
    repo_str = str(Path(repo).resolve())
    remaining = [r for r in repos if r != repo_str]
    if remaining != repos:
        ctx.write_json(registry_path(home), remaining)
