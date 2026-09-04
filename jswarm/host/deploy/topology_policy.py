#!/usr/bin/env python3
"""Catalog-driven topology protection policy for controlled config artifacts.

The resolver intentionally fails closed: only cataloged artifacts with an
``install`` block and without ``install.protect`` are eligible for destructive
"track this runtime master by replacing the repo path with an up-symlink"
actions. Protected artifacts and unknown paths are refused with observable
reasons that name both the path and topology class.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only in broken local envs
    yaml = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPONENTS_REL = Path("docs") / "_JarviSWARM" / "components"


ProtectIndex = dict[str, dict[str, Any]]

CONFLICT_KEY = "__conflict__"
REASON_KEY = "reason"


def _normalize_repo_path(path: str) -> str:
    """Return a stable repo-relative POSIX path key for catalog lookup."""
    return PurePosixPath(path).as_posix()


def _conflict_marker(path: str, existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    reason = (
        f"ambiguous duplicate install conflict for {path}: component catalog declares "
        f"multiple non-identical install blocks ({existing!r} vs {incoming!r})"
    )
    print(reason, file=sys.stderr)
    return {CONFLICT_KEY: True, REASON_KEY: reason}


def load_protect_index(repo_root: Path) -> ProtectIndex:
    """Load per-artifact install metadata from component catalog YAML files.

    Returns a mapping of repo-relative artifact path to that artifact's
    ``install`` mapping, including only artifacts that declare an install block.
    Identical duplicate install blocks collapse harmlessly. Conflicting duplicate
    install blocks are recorded as ambiguous conflict markers so callers classify
    the path as ``unknown`` and refuse destructive actions fail-closed. Any
    component read/parse problem is treated as unavailable catalog data and
    therefore yields an empty index; callers will classify paths as ``unknown``
    and refuse destructive actions fail-closed.
    """
    if yaml is None:
        return {}

    components_dir = repo_root / COMPONENTS_REL
    protect_index: ProtectIndex = {}
    try:
        component_paths = sorted(components_dir.glob("*.component.yaml"))
        for component_path in component_paths:
            data = yaml.safe_load(component_path.read_text(encoding="utf-8")) or {}
            artifacts = data.get("artifacts") or []
            if not isinstance(artifacts, list):
                continue
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                raw_path = artifact.get("path")
                install = artifact.get("install")
                if not isinstance(raw_path, str) or not isinstance(install, dict):
                    continue
                normalized_path = _normalize_repo_path(raw_path)
                incoming = dict(install)
                existing = protect_index.get(normalized_path)
                if existing is None:
                    protect_index[normalized_path] = incoming
                    continue
                if existing.get(CONFLICT_KEY):
                    continue
                if existing == incoming:
                    continue
                protect_index[normalized_path] = _conflict_marker(normalized_path, existing, incoming)
    except Exception:
        return {}

    return protect_index


def load_install_index_strict(repo_root: Path) -> tuple[ProtectIndex, list[str]]:
    """Load install-bearing artifacts while surfacing catalog load failures.

    This reads the same component YAML files as :func:`load_protect_index` but is
    intended for topology verification, where malformed catalog data must fail
    loudly instead of collapsing to an empty fail-closed policy index.
    """
    if yaml is None:
        return {}, ["component catalog load failed: PyYAML is unavailable"]

    components_dir = repo_root / COMPONENTS_REL
    install_index: ProtectIndex = {}
    load_errors: list[str] = []
    conflict_paths: set[str] = set()

    try:
        component_paths = sorted(components_dir.glob("*.component.yaml"))
    except OSError as exc:
        return {}, [f"component catalog load failed for {components_dir}: could not list component YAMLs: {exc}"]

    for component_path in component_paths:
        try:
            raw_text = component_path.read_text(encoding="utf-8")
        except OSError as exc:
            load_errors.append(
                f"component catalog load failed for {component_path.name}: could not read component YAML: {exc}"
            )
            continue

        try:
            data = yaml.safe_load(raw_text)
        except Exception as exc:
            load_errors.append(
                f"component catalog load failed for {component_path.name}: YAML parse error: {exc}"
            )
            continue

        if not isinstance(data, dict):
            load_errors.append(
                f"component catalog load failed for {component_path.name}: non-mapping component document; expected YAML mapping"
            )
            continue

        artifacts = data.get("artifacts") or []
        if not isinstance(artifacts, list):
            continue

        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            raw_path = artifact.get("path")
            install = artifact.get("install")
            if not isinstance(raw_path, str) or not isinstance(install, dict):
                continue

            normalized_path = _normalize_repo_path(raw_path)
            if normalized_path in conflict_paths:
                continue

            incoming = dict(install)
            existing = install_index.get(normalized_path)
            if existing is None:
                install_index[normalized_path] = incoming
                continue
            if existing == incoming:
                continue

            conflict_paths.add(normalized_path)
            install_index.pop(normalized_path, None)
            load_errors.append(
                f"component catalog install conflict for {normalized_path}: duplicate non-identical install blocks ({existing!r} vs {incoming!r})"
            )

    return install_index, load_errors


def classify(path: str, protect_index: ProtectIndex) -> str:
    """Classify a repo-relative path's deployment topology."""
    normalized_path = _normalize_repo_path(path)
    install = protect_index.get(normalized_path)
    if install is None or install.get(CONFLICT_KEY):
        return "unknown"

    # install.type alone is NOT protective; install.protect:true is the protection bit;
    # type only selects the protected class.
    if install.get("protect"):
        install_type = install.get("type")
        if install_type == "managed-copy":
            return "managed-copy"
        return "controlled-master-symlink"

    return "runtime-master"


def destructive_action_allowed(path: str, protect_index: ProtectIndex) -> tuple[bool, str]:
    """Return whether destructive re-symlink-up conversion is allowed."""
    normalized_path = _normalize_repo_path(path)
    topology_class = classify(normalized_path, protect_index)

    if topology_class == "runtime-master":
        return True, "runtime-master: safe to track via up-symlink"
    install = protect_index.get(normalized_path)
    if topology_class == "unknown" and isinstance(install, dict) and install.get(CONFLICT_KEY):
        reason = str(install.get(REASON_KEY) or "ambiguous duplicate install conflict")
        return (
            False,
            f"unknown: destructive action refused for {normalized_path}; "
            f"fail-closed because {reason}",
        )
    if topology_class == "unknown":
        return (
            False,
            f"unknown: destructive action refused for {normalized_path}; "
            "fail-closed because no catalog install metadata was found for this path",
        )
    if topology_class == "managed-copy":
        return (
            False,
            f"managed-copy: destructive action refused for {normalized_path}; "
            "catalog install.protect=true marks this artifact as a protected managed copy",
        )
    if topology_class == "controlled-master-symlink":
        return (
            False,
            f"controlled-master-symlink: destructive action refused for {normalized_path}; "
            "catalog install.protect=true marks this artifact as a protected controlled master",
        )

    return (
        False,
        f"{topology_class}: destructive action refused for {normalized_path}; "
        "fail-closed because topology classification is not recognized",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    classify_parser = subparsers.add_parser("classify", help="Print topology class for a path")
    classify_parser.add_argument("path", help="Repo-relative path to classify")
    classify_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)

    check_parser = subparsers.add_parser("check", help="Check whether destructive action is allowed")
    check_parser.add_argument("path", help="Repo-relative path to check")
    check_parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)

    return parser


def main(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    protect_index = load_protect_index(args.repo_root)

    if args.command == "classify":
        print(classify(args.path, protect_index))
        return 0

    if args.command == "check":
        allowed, reason = destructive_action_allowed(args.path, protect_index)
        if allowed:
            return 0
        print(reason, file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised by CLI tests/usage
    raise SystemExit(main(sys.argv[1:]))
