#!/usr/bin/env python3
"""Verify controlled-config .claude deployment topology.

The verifier is intentionally root-parameterized: all checks operate only under
roots supplied by the caller. This lets tests and maintenance jobs validate a
candidate topology without touching the operator's real home directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jswarm.host.deploy.topology_policy import load_install_index_strict
from jswarm.host.deploy.layers import LayersError, load_layers

MAX_SYMLINK_DEPTH = 40
USER_SCOPES = {"scope-user", "both"}


@dataclass(frozen=True)
class Resolution:
    """Result of a bounded symlink walk."""

    path: Path | None
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.path is not None


def _as_path(value: str | Path) -> Path:
    return Path(value)


def _display(path: Path) -> str:
    return os.fspath(path)


def _artifact_label(target_relpath: str) -> str:
    return f"artifact {target_relpath}"


def _validate_target_relpath(value: Any) -> str | None:
    """Return an error string when an install.target_relpath is unsafe."""
    if not isinstance(value, str) or not value.strip():
        return "target_relpath unsafe: expected a non-empty relative string"
    if os.path.isabs(value):
        return f"target_relpath unsafe: absolute paths are not allowed ({value!r})"
    if PureWindowsPath(value).drive:
        return f"target_relpath unsafe: drive-qualified paths are not allowed ({value!r})"
    parts = Path(value).parts
    if ".." in parts:
        return f"target_relpath unsafe: parent traversal '..' components are not allowed ({value!r})"
    return None


def _path_kind(path: Path) -> str:
    try:
        if path.is_symlink():
            try:
                return f"symlink -> {os.readlink(path)}"
            except OSError as exc:
                return f"symlink with unreadable target ({exc})"
        if path.is_file():
            return "real file"
        if path.is_dir():
            return "directory"
        if path.exists():
            return "non-file filesystem object"
        return "missing"
    except OSError as exc:
        return f"unreadable ({exc})"


def _resolve_symlink_bounded(path: Path, *, max_depth: int = MAX_SYMLINK_DEPTH) -> Resolution:
    """Resolve symlinks without hanging on cycles.

    The walk uses ``lstat``/``readlink`` one hop at a time, records visited link
    paths, and stops on either a revisited link or a fixed hop budget.
    """

    current = path
    visited: set[Path] = set()
    chain: list[str] = []

    for _depth in range(max_depth + 1):
        try:
            stat_result = current.lstat()
        except FileNotFoundError:
            return Resolution(
                None,
                "dangling",
                f"dangling/broken symlink chain at {_display(current)}; chain={' -> '.join(chain) or _display(path)}",
            )
        except OSError as exc:
            return Resolution(
                None,
                "error",
                f"could not inspect {_display(current)} while resolving symlink chain: {exc}",
            )

        if not os.path.islink(current):
            return Resolution(current, "ok", f"resolved to {_display(current)}")

        normalized = current.absolute()
        if normalized in visited:
            chain.append(_display(current))
            return Resolution(
                None,
                "cycle",
                f"symlink loop/cycle detected while resolving {_display(path)}; chain={' -> '.join(chain)}",
            )
        visited.add(normalized)
        chain.append(_display(current))

        try:
            raw_target = os.readlink(current)
        except OSError as exc:
            return Resolution(
                None,
                "error",
                f"could not read symlink target for {_display(current)}: {exc}",
            )
        target = Path(raw_target)
        current = target if target.is_absolute() else current.parent / target

    return Resolution(
        None,
        "depth",
        f"symlink depth limit exceeded resolving {_display(path)} after {max_depth} hops; possible loop/cycle",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_real_file(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=True) == right.resolve(strict=True)
    except OSError:
        return False


def _iter_install_artifacts(catalog_root: Path) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    artifacts: list[tuple[str, dict[str, Any]]] = []
    install_index, errors = load_install_index_strict(catalog_root)
    for catalog_path, install in sorted(install_index.items()):
        if not isinstance(install, dict):
            continue
        target_relpath = install.get("target_relpath")
        if isinstance(target_relpath, str) and target_relpath.strip():
            artifacts.append((target_relpath, install))
            continue
        # Every install-bearing artifact must declare a usable target_relpath. A
        # missing/empty/non-string value is malformed catalog metadata, not a
        # topology to silently skip: fail loud, naming the catalog path, before any
        # filesystem access. (No legacy `.claude/<path>` fallback — that masked
        # malformed deploy metadata that Phase 5 will consume as source of truth.)
        errors.append(
            f"component catalog metadata failure for {catalog_path}: "
            f"install.target_relpath must be a non-empty relative string, "
            f"actual {target_relpath!r}"
        )
    return artifacts, errors


def _check_master(
    *, target_relpath: str, master_path: Path, failures: list[str]
) -> tuple[bool, str | None]:
    label = _artifact_label(target_relpath)
    kind = _path_kind(master_path)
    if not master_path.exists() and not master_path.is_symlink():
        failures.append(
            f"{label}: master must be a real file; expected real file at {_display(master_path)}, actual missing"
        )
        return False, None
    if master_path.is_symlink():
        failures.append(
            f"{label}: master must be a real file; expected real file at {_display(master_path)}, actual {kind}"
        )
        return False, None
    if not master_path.is_file():
        failures.append(
            f"{label}: master must be a real file; expected real file at {_display(master_path)}, actual {kind}"
        )
        return False, None
    try:
        return True, _sha256(master_path)
    except OSError as exc:
        failures.append(
            f"{label}: master sha256 unavailable; expected readable real file at {_display(master_path)}, actual error {exc}"
        )
        return True, None


def _check_symlink_to_master(
    *,
    target_relpath: str,
    role: str,
    path: Path,
    master_path: Path,
    master_sha: str | None,
    failures: list[str],
) -> str | None:
    label = _artifact_label(target_relpath)
    kind = _path_kind(path)
    if not path.is_symlink():
        failures.append(
            f"{label}: {role} must be a symlink to master; expected symlink at {_display(path)} -> {_display(master_path)}, actual {kind}"
        )
        return None

    resolution = _resolve_symlink_bounded(path)
    if not resolution.ok:
        failures.append(
            f"{label}: {role} symlink target invalid; expected {_display(path)} to resolve to master {_display(master_path)}, actual {resolution.status}: {resolution.detail}"
        )
        return None

    resolved_path = resolution.path
    assert resolved_path is not None
    if not _same_real_file(resolved_path, master_path):
        failures.append(
            f"{label}: {role} symlink wrong-target; expected realpath {_display(master_path.resolve(strict=False))}, actual realpath {_display(resolved_path.resolve(strict=False))} from {_display(path)}"
        )

    try:
        actual_sha = _sha256(resolved_path)
    except OSError as exc:
        failures.append(
            f"{label}: {role} sha256 unavailable; expected readable symlink target matching master, actual error {exc} at {_display(resolved_path)}"
        )
        return None
    if master_sha is not None and actual_sha != master_sha:
        failures.append(
            f"{label}: sha-mismatch for {role}; expected sha256 {master_sha} from master {_display(master_path)}, actual sha256 {actual_sha} from {_display(resolved_path)}"
        )
    return actual_sha


def _check_managed_copy(
    *,
    target_relpath: str,
    role: str,
    path: Path,
    master_path: Path,
    master_sha: str | None,
    failures: list[str],
) -> str | None:
    label = _artifact_label(target_relpath)
    kind = _path_kind(path)
    if path.is_symlink() or not path.is_file():
        failures.append(
            f"{label}: {role} must be a managed-copy real file; expected real file at {_display(path)} with sha256 matching master {_display(master_path)}, actual {kind}"
        )
        return None

    try:
        actual_sha = _sha256(path)
    except OSError as exc:
        failures.append(
            f"{label}: {role} sha256 unavailable; expected readable managed-copy at {_display(path)}, actual error {exc}"
        )
        return None
    if master_sha is not None and actual_sha != master_sha:
        failures.append(
            f"{label}: sha-mismatch for {role}; expected sha256 {master_sha} from master {_display(master_path)}, actual sha256 {actual_sha} from managed-copy {_display(path)}"
        )
    return actual_sha


def _load_master_merge_object(
    *,
    target_relpath: str,
    master_path: Path,
    failures: list[str],
) -> dict[str, Any] | None:
    """Load a merge-template master as a JSON object mapping, else record a failure."""
    label = _artifact_label(target_relpath)
    try:
        data = json.loads(master_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(
            f"{label}: master merge-template unreadable/unparseable; expected JSON object mapping at {_display(master_path)}, actual error {exc}"
        )
        return None
    if not isinstance(data, dict):
        failures.append(
            f"{label}: master merge-template must be a JSON object mapping; expected object at {_display(master_path)}, actual top-level {type(data).__name__}"
        )
        return None
    return data


def desired_merge_template_payload(
    target_payload: dict[str, Any], master_payload: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Return the additive merge-template end-state and its missing entries.

    General JSON keys keep COM-146's top-level additive semantics.  Settings
    ``hooks`` is the one structured exception: when both sides are hook maps,
    required command registrations merge by event, matcher, and ``type`` plus
    ``command`` identity without replacing existing command payloads.
    """
    shape_errors = merge_template_hook_shape_errors(target_payload, master_payload)
    if shape_errors:
        raise ValueError("; ".join(shape_errors))

    merged = dict(target_payload)
    additions: list[str] = []
    for key, value in master_payload.items():
        if key not in merged:
            merged[key] = value
            additions.append(f"root key {key!r}")

    master_hooks = master_payload.get("hooks")
    target_hooks = target_payload.get("hooks")
    if not isinstance(master_hooks, dict) or not isinstance(target_hooks, dict):
        return merged, additions

    merged_hooks = dict(target_hooks)
    hooks_changed = False
    for event, master_groups in master_hooks.items():
        if not isinstance(master_groups, list):
            continue
        target_groups = merged_hooks.get(event)
        if target_groups is None:
            merged_hooks[event] = list(master_groups)
            hooks_changed = True
            additions.extend(f"hooks.{event} matcher {str(group.get('matcher', ''))!r}" for group in master_groups if isinstance(group, dict))
            continue
        if not isinstance(target_groups, list):
            continue

        merged_groups = list(target_groups)
        event_changed = False
        for master_group in master_groups:
            if not isinstance(master_group, dict):
                continue
            matcher = str(master_group.get("matcher", ""))
            master_commands = master_group.get("hooks")
            master_identities = {
                (command.get("type"), command.get("command"))
                for command in master_commands
                if isinstance(command, dict)
            } if isinstance(master_commands, list) else set()
            matcher_groups = [
                group
                for group in merged_groups
                if isinstance(group, dict) and str(group.get("matcher", "")) == matcher
            ]
            matching_group = next(
                (
                    group
                    for group in matcher_groups
                    if isinstance(group.get("hooks"), list)
                    and any(
                        isinstance(command, dict)
                        and (command.get("type"), command.get("command")) in master_identities
                        for command in group["hooks"]
                    )
                ),
                matcher_groups[0] if matcher_groups else None,
            )
            if matching_group is None:
                merged_groups.append(dict(master_group))
                event_changed = True
                additions.append(f"hooks.{event} matcher {matcher!r}")
                continue

            target_commands = matching_group.get("hooks")
            if not isinstance(master_commands, list) or not isinstance(target_commands, list):
                continue
            merged_commands = list(target_commands)
            commands_changed = False
            for master_command in master_commands:
                if not isinstance(master_command, dict):
                    continue
                identity = (master_command.get("type"), master_command.get("command"))
                if any(
                    isinstance(command, dict)
                    and (command.get("type"), command.get("command")) == identity
                    for command in merged_commands
                ):
                    continue
                merged_commands.append(dict(master_command))
                commands_changed = True
                additions.append(
                    f"hooks.{event} matcher {matcher!r} command type={identity[0]!r} command={identity[1]!r}"
                )
            if commands_changed:
                updated_group = dict(matching_group)
                updated_group["hooks"] = merged_commands
                merged_groups[merged_groups.index(matching_group)] = updated_group
                event_changed = True
        if event_changed:
            merged_hooks[event] = merged_groups
            hooks_changed = True
    if hooks_changed:
        merged["hooks"] = merged_hooks
    return merged, additions


def merge_template_hook_shape_errors(
    target_payload: dict[str, Any], master_payload: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    for role, payload in (("master", master_payload), ("target", target_payload)):
        if "hooks" not in payload:
            continue
        hooks = payload["hooks"]
        if not isinstance(hooks, dict):
            errors.append(f"{role} hooks must be an object mapping")
            continue
        for event, groups in hooks.items():
            if not isinstance(groups, list):
                errors.append(f"{role} hooks.{event} must be a list")
                continue
            for group in groups:
                if not isinstance(group, dict):
                    errors.append(f"{role} hooks.{event} matcher group must be an object mapping")
                    continue
                matcher = str(group.get("matcher", ""))
                commands = group.get("hooks")
                if not isinstance(commands, list):
                    errors.append(f"{role} hooks.{event} matcher {matcher!r} hooks must be a list")
                    continue
                for command in commands:
                    if not isinstance(command, dict):
                        errors.append(
                            f"{role} hooks.{event} matcher {matcher!r} command entry must be an object mapping"
                        )
                        continue
                    hook_type = command.get("type")
                    hook_command = command.get("command")
                    if not isinstance(hook_type, str) or not hook_type.strip():
                        errors.append(
                            f"{role} hooks.{event} matcher {matcher!r} command entry type must be a nonempty string"
                        )
                    if not isinstance(hook_command, str) or not hook_command.strip():
                        errors.append(
                            f"{role} hooks.{event} matcher {matcher!r} command entry command must be a nonempty string"
                        )
    return errors


def _check_merge_template(
    *,
    target_relpath: str,
    role: str,
    path: Path,
    master_path: Path,
    master_payload: dict[str, Any] | None,
    failures: list[str],
) -> None:
    """Verify a merge-template target against the deploy engine's merge contract end-state.

    The target must satisfy the same additive merge predicate used by the
    planner and materializers. Host-only keys and conflicting host values remain
    valid because merge-template never overwrites them.
    """
    label = _artifact_label(target_relpath)
    kind = _path_kind(path)
    if path.is_symlink():
        failures.append(
            f"{label}: {role} merge-template target must be a real JSON object file, not a symlink; expected real merged file at {_display(path)}, actual {kind}"
        )
        return
    if not path.is_file():
        failures.append(
            f"{label}: {role} merge-template target missing or not a regular file; expected real merged JSON object file at {_display(path)}, actual {kind}"
        )
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(
            f"{label}: {role} merge-template target unreadable/unparseable; expected JSON object mapping at {_display(path)}, actual error {exc}"
        )
        return
    if not isinstance(data, dict):
        failures.append(
            f"{label}: {role} merge-template target must be a JSON object mapping; expected object at {_display(path)}, actual top-level {type(data).__name__}"
        )
        return
    if master_payload is None:
        # Master failure already recorded; structural target checks above still ran fail-closed.
        return
    shape_errors = merge_template_hook_shape_errors(data, master_payload)
    if shape_errors:
        failures.extend(f"{label}: {role} merge-template {error}" for error in shape_errors)
        return
    _, additions = desired_merge_template_payload(data, master_payload)
    if additions:
        failures.append(
            f"{label}: {role} merge-template target missing master entries: {', '.join(additions)}; expected additive merge end-state from {_display(master_path)} at {_display(path)}"
        )


def verify_topology(
    *,
    master_root: str | Path,
    common_root: str | Path,
    home_root: str | Path,
    catalog_root: str | Path,
    layers_path: str | Path | None = None,
    include_disabled_layers: bool = False,
) -> list[str]:
    """Return topology failures for cataloged controlled-config install artifacts.

    COM-176 one-home: each artifact has exactly ONE home context and ONE deploy
    target. The verifier proves the single deployed target resolves to
    ``dotclaude/<context>/<target_relpath>`` — it does NOT check separate common
    and home legs by scope. An empty list means the topology conforms.
    """

    master_root_path = _as_path(master_root)
    common_root_path = _as_path(common_root)
    home_root_path = _as_path(home_root)
    catalog_root_path = _as_path(catalog_root)
    root_args = {"common_root": common_root_path, "home_root": home_root_path}

    artifacts, load_errors = _iter_install_artifacts(catalog_root_path)
    failures: list[str] = list(load_errors)

    try:
        layers = load_layers(
            dotclaude_root=master_root_path,
            layers_path=_as_path(layers_path) if layers_path is not None else master_root_path / "_layers.yaml",
        )
    except LayersError as exc:
        failures.append(f"_layers.yaml: {exc}")
        return failures

    for target_relpath, install in artifacts:
        target_error = _validate_target_relpath(target_relpath)
        if target_error is not None:
            failures.append(f"{_artifact_label(target_relpath)}: {target_error}")
            continue

        # Reject the rejected multi-target model: a legacy scope / target_contexts must never
        # produce a clean verify with silently-skipped legs.
        if "scope" in install:
            failures.append(
                f"{_artifact_label(target_relpath)}: legacy install.scope unsupported in COM-176 one-home; use install.context"
            )
            continue
        if "target_contexts" in install:
            failures.append(
                f"{_artifact_label(target_relpath)}: install.target_contexts is rejected (one-home); use install.context"
            )
            continue

        context = str(install.get("context") or "")
        install_type = install.get("type")
        try:
            master_path = layers.master_path(context, target_relpath)
            target_path = layers.target_path(
                context, target_relpath, root_args=root_args, include_disabled=include_disabled_layers
            )
        except LayersError as exc:
            failures.append(f"{_artifact_label(target_relpath)}: {exc}")
            continue

        _master_ok, master_sha = _check_master(
            target_relpath=target_relpath, master_path=master_path, failures=failures
        )
        role = f"context {context}"

        if install_type == "merge-template":
            # COM-160 merge-template: a real merged JSON file (never a whole-file symlink).
            if Path(target_relpath).suffix != ".json":
                failures.append(
                    f"{_artifact_label(target_relpath)}: merge-template non-JSON not supported; expected a .json target_relpath, actual {target_relpath!r}"
                )
                continue
            master_payload = (
                _load_master_merge_object(
                    target_relpath=target_relpath, master_path=master_path, failures=failures
                )
                if _master_ok
                else None
            )
            _check_merge_template(
                target_relpath=target_relpath,
                role=role,
                path=target_path,
                master_path=master_path,
                master_payload=master_payload,
                failures=failures,
            )
            continue

        if install_type == "symlink":
            _check_symlink_to_master(
                target_relpath=target_relpath,
                role=role,
                path=target_path,
                master_path=master_path,
                master_sha=master_sha,
                failures=failures,
            )
        elif install_type == "managed-copy":
            _check_managed_copy(
                target_relpath=target_relpath,
                role=role,
                path=target_path,
                master_path=master_path,
                master_sha=master_sha,
                failures=failures,
            )
        else:
            failures.append(
                f"{_artifact_label(target_relpath)}: COM-176 expects install.type symlink, actual {install_type!r}"
            )

    return failures


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-root", required=True, type=Path, help="dotclaude master root (holds _layers.yaml + <context>/ dirs)")
    parser.add_argument("--layers-path", type=Path, default=None, help="layer registry (defaults to <master-root>/_layers.yaml)")
    parser.add_argument("--common-root", required=True, type=Path, help="repo.common .claude deploy target root")
    parser.add_argument("--home-root", required=True, type=Path, help="user .claude deploy target root (~/.claude)")
    parser.add_argument("--catalog-root", required=True, type=Path, help="repository root containing docs/_JarviSWARM/components")
    parser.add_argument("--include-disabled-layers", action="store_true", help="diagnostics only: resolve targets for disabled contexts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    failures = verify_topology(
        master_root=args.master_root,
        common_root=args.common_root,
        home_root=args.home_root,
        catalog_root=args.catalog_root,
        layers_path=args.layers_path,
        include_disabled_layers=args.include_disabled_layers,
    )
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main(sys.argv[1:]))
