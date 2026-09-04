#!/usr/bin/env python3
"""Catalog-driven controlled-config deploy engine for COM-146.

The engine is deliberately root-parameterized: callers must provide the
controlled master, common target, home target, and catalog roots.  No command
falls back to the operator's live runtime config tree.

``apply`` is fail-closed by default and refuses live writes. Two explicit
non-default modes exist:
  * ``--harness`` materializes only non-live fixture roots (used by the test
    suite); it still refuses any target resolving under the real ``~/.claude``.
  * ``--live-home`` (AC-11) is the explicit real-home live mode: it permits a
    write ONLY at the exact catalog-declared ``Path.home()/.claude/<target_relpath>``
    for a ``user`` context artifact, and refuses every non-declared target, parent
    symlink escape, non-user context target resolving under live ``~/.claude``, directory
    target, hardlink alias, symlinked merge-template, and planted temp alias.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    # Insert the repository root (parents[3]: jswarm/host/deploy/ -> jswarm/host/ ->
    # jswarm/ -> repo root), not jswarm/ itself (parents[2]). jswarm/ on sys.path would
    # shadow the stdlib for anything under jswarm/ sharing a name with it (e.g.
    # jswarm/platform/ vs the stdlib platform module), and the jswarm.host.deploy.*
    # imports below need the repo root on the path anyway.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jswarm.host.deploy.layers import LayersError, LayersRegistry, load_layers  # noqa: E402
from jswarm.host.deploy.verify_dotclaude_topology import (  # noqa: E402
    _iter_install_artifacts,
    _validate_target_relpath,
    desired_merge_template_payload,
    merge_template_hook_shape_errors,
)

PLAN_KEYS = (
    "target_relpath",
    "context",
    "type",
    "source",
    "target",
    "action",
    "status",
    "detail",
    "rollback_hint",
)
VALID_TYPES = {"symlink", "managed-copy", "merge-template"}
LIVE_APPLY_REFUSAL = "live apply is COM-148"

# AC-12 live backup/preview/rollback constants.
BACKUP_ROOT_ENV = "COM146_CONTROLLED_CONFIG_BACKUP_ROOT"
LIVE_BACKUP_KIND = "controlled-config-dotclaude-live-backup"
LIVE_PREVIEW_KIND = "controlled-config-dotclaude-live-preview"
LIVE_BACKUP_SCHEMA_VERSION = 1

# (BLOCKER-3) Typed preview-load outcomes. An ABSENT preview is an out-of-band approval token
# (warn + proceed without the plan-hash gate); an INVALID preview is tamper/damage evidence and
# MUST fail closed — it is never silently downgraded to "no gate".
PREVIEW_ABSENT = "absent"
PREVIEW_INVALID = "invalid"
PREVIEW_OK = "ok"

PlanEntry = dict[str, str]


def resolve_python_from_release_root(release_root: str | Path, env: dict[str, str] = os.environ) -> Path:
    """Resolve the Python interpreter for a packaged JarviSWARM release root.

    JARVISWARM_PYTHON is the explicit override; otherwise use the release-local
    .venv/bin/python.  The helper is intentionally additive and does not change
    existing deploy behavior.
    """
    override = env.get("JARVISWARM_PYTHON")
    candidate = Path(override) if override else Path(release_root) / ".venv" / "bin" / "python"
    if override and not candidate.exists():
        raise FileNotFoundError(
            f"JARVISWARM_PYTHON points to missing Python interpreter at {candidate}; "
            "unset JARVISWARM_PYTHON to use <release_root>/.venv/bin/python"
        )
    return candidate


def _as_path(value: str | Path) -> Path:
    return Path(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_sha(left: Path, right: Path) -> bool:
    try:
        return _sha256(left) == _sha256(right)
    except OSError:
        return False


def _same_real_file(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=True) == right.resolve(strict=True)
    except OSError:
        return False


def _mode_str(path: Path) -> str:
    """Octal mode (permission bits) of ``path`` via no-follow lstat, for plan detail text."""
    try:
        return oct(stat.S_IMODE(os.lstat(path).st_mode))
    except OSError:
        return "????"


def _same_mode(left: Path, right: Path) -> bool:
    """(P15 critic MAJOR-1) Compare permission bits of two regular files (no-follow on the
    live ``left``; the master ``right`` is a trusted repo file)."""
    try:
        return stat.S_IMODE(os.lstat(left).st_mode) == stat.S_IMODE(os.stat(right).st_mode)
    except OSError:
        return False


def _entry(
    *,
    target_relpath: str,
    context: str,
    install_type: str,
    source: Path | str,
    target: Path | str,
    action: str,
    status: str,
    detail: str,
    rollback_hint: str,
) -> PlanEntry:
    return {
        "target_relpath": target_relpath,
        "context": context,
        "type": install_type,
        "source": os.fspath(source),
        "target": os.fspath(target),
        "action": action,
        "status": status,
        "detail": detail,
        "rollback_hint": rollback_hint,
    }


def _error_entry(detail: str, *, target_relpath: str = "") -> PlanEntry:
    return _entry(
        target_relpath=target_relpath,
        context="",
        install_type="",
        source="",
        target="",
        action="refuse",
        status="error",
        detail=detail,
        rollback_hint="No filesystem changes were made; fix catalog metadata and rerun.",
    )


def _rollback_hint(install_type: str, target: Path) -> str:
    if install_type == "merge-template":
        return f"git checkout -- {target} / restore backup"
    return f"rm {target}"



def _source_status(source: Path) -> str | None:
    if source.is_symlink():
        return f"master source must be a real file, actual symlink at {source}"
    if not source.exists():
        return f"master source missing at {source}"
    if not source.is_file():
        return f"master source must be a real file, actual non-file at {source}"
    try:
        _sha256(source)
    except OSError as exc:
        return f"master source unreadable at {source}: {exc}"
    return None


def _plan_symlink(*, target_relpath: str, context: str, source: Path, target: Path) -> PlanEntry:
    rollback = _rollback_hint("symlink", target)
    source_error = _source_status(source)
    if source_error is not None:
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="symlink",
            source=source,
            target=target,
            action="refuse",
            status="error",
            detail=source_error,
            rollback_hint=rollback,
        )

    if target.is_symlink():
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            return _entry(
                target_relpath=target_relpath,
                context=context,
                install_type="symlink",
                source=source,
                target=target,
                action="update-symlink",
                status="would-change",
                detail=f"target symlink is not correct and will be replaced: {exc}",
                rollback_hint=rollback,
            )
        if _same_real_file(resolved, source) and _same_sha(resolved, source):
            return _entry(
                target_relpath=target_relpath,
                context=context,
                install_type="symlink",
                source=source,
                target=target,
                action="noop",
                status="ok",
                detail="target symlink already resolves to master with matching sha256",
                rollback_hint=rollback,
            )
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="symlink",
            source=source,
            target=target,
            action="update-symlink",
            status="would-change",
            detail="target symlink does not resolve to the expected master file",
            rollback_hint=rollback,
        )

    if target.exists():
        if target.is_dir():
            return _entry(
                target_relpath=target_relpath,
                context=context,
                install_type="symlink",
                source=source,
                target=target,
                action="refuse",
                status="error",
                detail="target is a directory; refusing to replace it with a symlink",
                rollback_hint=rollback,
            )
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="symlink",
            source=source,
            target=target,
            action="update-symlink",
            status="would-change",
            detail="target exists but is not the expected symlink to master",
            rollback_hint=rollback,
        )

    return _entry(
        target_relpath=target_relpath,
        context=context,
        install_type="symlink",
        source=source,
        target=target,
        action="create-symlink",
        status="would-change",
        detail="target symlink will be created",
        rollback_hint=rollback,
    )


def _plan_managed_copy(*, target_relpath: str, context: str, source: Path, target: Path) -> PlanEntry:
    rollback = _rollback_hint("managed-copy", target)
    source_error = _source_status(source)
    if source_error is not None:
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="managed-copy",
            source=source,
            target=target,
            action="refuse",
            status="error",
            detail=source_error,
            rollback_hint=rollback,
        )

    if target.is_file() and not target.is_symlink() and _same_sha(target, source):
        # (P15 critic MAJOR-1) Byte equality is NOT convergence: a managed-copy target whose
        # bytes match the master but whose MODE differs (e.g. an exec hook drifted 0755->0644)
        # must be repaired, not reported as noop. Otherwise verify/report claim "converged"
        # while a non-executable hook sits live.
        if _same_mode(target, source):
            return _entry(
                target_relpath=target_relpath,
                context=context,
                install_type="managed-copy",
                source=source,
                target=target,
                action="noop",
                status="ok",
                detail="target managed-copy already matches master sha256 and mode",
                rollback_hint=rollback,
            )
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="managed-copy",
            source=source,
            target=target,
            action="update-managed-copy",
            status="would-change",
            detail=(
                f"target managed-copy bytes match but mode differs "
                f"({_mode_str(target)} != master {_mode_str(source)}); will repair mode"
            ),
            rollback_hint=rollback,
        )

    if target.exists() or target.is_symlink():
        if target.is_dir():
            return _entry(
                target_relpath=target_relpath,
                context=context,
                install_type="managed-copy",
                source=source,
                target=target,
                action="refuse",
                status="error",
                detail="target is a directory; refusing to replace it with a managed copy",
                rollback_hint=rollback,
            )
        action = "update-managed-copy"
        detail = "target exists but is not a sha-equal managed-copy real file"
    else:
        action = "write-managed-copy"
        detail = "target managed-copy will be written"

    return _entry(
        target_relpath=target_relpath,
        context=context,
        install_type="managed-copy",
        source=source,
        target=target,
        action=action,
        status="would-change",
        detail=detail,
        rollback_hint=rollback,
    )


def _load_json_mapping(path: Path, *, role: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"{role} JSON unreadable at {path}: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"{role} JSON parse error at {path}: {exc}"
    if not isinstance(data, dict):
        return None, f"{role} JSON must be an object mapping at {path}"
    return data, None


def _plan_merge_template(*, target_relpath: str, context: str, source: Path, target: Path) -> PlanEntry:
    rollback = _rollback_hint("merge-template", target)
    if source.suffix != ".json" or target.suffix != ".json":
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="merge-template",
            source=source,
            target=target,
            action="merge-template",
            status="error",
            detail="merge-template non-JSON not supported in COM-146",
            rollback_hint=rollback,
        )

    source_error = _source_status(source)
    if source_error is not None:
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="merge-template",
            source=source,
            target=target,
            action="merge-template",
            status="error",
            detail=source_error,
            rollback_hint=rollback,
        )

    master_payload, master_error = _load_json_mapping(source, role="master")
    if master_error is not None:
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="merge-template",
            source=source,
            target=target,
            action="merge-template",
            status="error",
            detail=master_error,
            rollback_hint=rollback,
        )
    assert master_payload is not None

    if target.is_symlink() or (target.exists() and not target.is_file()):
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="merge-template",
            source=source,
            target=target,
            action="merge-template",
            status="error",
            detail="target merge-template must be a JSON object file or missing",
            rollback_hint=rollback,
        )

    if not target.exists():
        target_payload = {}
    else:
        target_payload, target_error = _load_json_mapping(target, role="target")
        if target_error is not None:
            return _entry(
                target_relpath=target_relpath,
                context=context,
                install_type="merge-template",
                source=source,
                target=target,
                action="merge-template",
                status="error",
                detail=target_error,
                rollback_hint=rollback,
            )
        assert target_payload is not None
    shape_errors = merge_template_hook_shape_errors(target_payload, master_payload)
    if shape_errors:
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="merge-template",
            source=source,
            target=target,
            action="merge-template",
            status="error",
            detail="; ".join(shape_errors),
            rollback_hint=rollback,
        )
    _, additions = desired_merge_template_payload(target_payload, master_payload)

    if not additions:
        return _entry(
            target_relpath=target_relpath,
            context=context,
            install_type="merge-template",
            source=source,
            target=target,
            action="noop",
            status="ok",
            detail="target JSON already satisfies additive merge-template requirements",
            rollback_hint=rollback,
        )

    return _entry(
        target_relpath=target_relpath,
        context=context,
        install_type="merge-template",
        source=source,
        target=target,
        action="merge-template",
        status="would-change",
        detail=f"target JSON will receive missing master entries: {', '.join(additions)}",
        rollback_hint=rollback,
    )


def _target_path_for_context(
    *,
    context: str,
    target_relpath: str,
    layers: LayersRegistry,
    root_args: dict[str, Path],
    include_disabled_layers: bool = False,
) -> Path:
    return layers.target_path(
        context,
        target_relpath,
        root_args=root_args,
        include_disabled=include_disabled_layers,
    )


def build_plan(
    *,
    master_root: str | Path,
    common_root: str | Path,
    home_root: str | Path,
    catalog_root: str | Path,
    layers_path: str | Path | None = None,
    include_disabled_layers: bool = False,
) -> list[PlanEntry]:
    """Build the dry-run deployment plan for cataloged install artifacts."""

    master_root_path = _as_path(master_root)
    if not master_root_path.is_absolute():
        # (P15 field finding) The plan's source paths become on-disk live symlink dests; a
        # relative master root produced a dest that dangles from ~/.claude/<family>/ and forced
        # every such apply to self-rollback. Absolutize LEXICALLY (cwd join + normpath, never
        # resolve()) so the no-follow discipline on the live side is preserved.
        master_root_path = Path(os.path.abspath(master_root_path))
    common_root_path = Path(os.path.abspath(_as_path(common_root)))
    home_root_path = _as_path(home_root)
    catalog_root_path = _as_path(catalog_root)
    root_args = {"common_root": common_root_path, "home_root": home_root_path}

    artifacts, load_errors = _iter_install_artifacts(catalog_root_path)
    plan: list[PlanEntry] = [_error_entry(error) for error in load_errors]

    try:
        layers = load_layers(
            dotclaude_root=master_root_path,
            layers_path=_as_path(layers_path) if layers_path is not None else master_root_path / "_layers.yaml",
        )
    except LayersError as exc:
        plan.append(_error_entry(f"_layers.yaml: {exc}"))
        return [{key: entry[key] for key in PLAN_KEYS} for entry in plan]

    for target_relpath, install in artifacts:
        target_error = _validate_target_relpath(target_relpath)
        if target_error is not None:
            plan.append(
                _error_entry(
                    f"artifact {target_relpath}: {target_error}",
                    target_relpath=target_relpath,
                )
            )
            continue

        if "scope" in install:
            plan.append(
                _error_entry(
                    f"artifact {target_relpath}: install.scope is legacy and unsupported in COM-176 one-home catalog; use install.context",
                    target_relpath=target_relpath,
                )
            )
            continue
        if "target_contexts" in install:
            plan.append(
                _error_entry(
                    f"artifact {target_relpath}: install.target_contexts is legacy and unsupported in COM-176 one-home catalog; use install.context",
                    target_relpath=target_relpath,
                )
            )
            continue

        context = str(install.get("context") or "")
        install_type = str(install.get("type") or "")
        try:
            source = layers.master_path(context, target_relpath)
            target = _target_path_for_context(
                context=context,
                target_relpath=target_relpath,
                layers=layers,
                root_args=root_args,
                include_disabled_layers=include_disabled_layers,
            )
        except LayersError as exc:
            plan.append(_error_entry(f"artifact {target_relpath}: {exc}", target_relpath=target_relpath))
            continue

        if install_type not in VALID_TYPES:
            plan.append(
                _entry(
                    target_relpath=target_relpath,
                    context=context,
                    install_type=install_type,
                    source=source,
                    target=target,
                    action="refuse",
                    status="error",
                    detail=f"artifact {target_relpath}: unsupported install.type {install_type!r}",
                    rollback_hint="No filesystem changes were made; fix catalog metadata and rerun.",
                )
            )
            continue

        if install_type == "symlink":
            plan.append(_plan_symlink(target_relpath=target_relpath, context=context, source=source, target=target))
        elif install_type == "managed-copy":
            plan.append(_plan_managed_copy(target_relpath=target_relpath, context=context, source=source, target=target))
        elif install_type == "merge-template":
            plan.append(_plan_merge_template(target_relpath=target_relpath, context=context, source=source, target=target))

    return [{key: entry[key] for key in PLAN_KEYS} for entry in plan]

def _is_under_or_equal(path: Path, ancestor: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(ancestor.resolve(strict=False))
        return True
    except ValueError:
        return False


class _LiveApplyRefused(RuntimeError):
    """Backstop raised inside an _apply_* helper if a target resolves under live ~/.claude.

    The pre-apply pass in main() already refuses such plans atomically before any write; this
    is the defense-in-depth layer the apply loop never reaches in practice.
    """


def _live_dotclaude() -> Path:
    return Path.home() / ".claude"


def _target_escapes_to_live(target: Path) -> bool:
    """True if a planned write target, OR its parent, resolved through any symlinked parent
    directories, equals or is under the real ``~/.claude``.

    ``Path.resolve(strict=False)`` follows existing symlinked parents, so a child dir
    symlinked into live runtime (``home_root/commands`` -> ``~/.claude/commands``) is caught
    here even though the supplied root itself is outside live ``.claude``.
    """
    live = _live_dotclaude()
    return _is_under_or_equal(target, live) or _is_under_or_equal(target.parent, live)


def _target_aliases_live(target: Path) -> bool:
    """True if an existing regular-file target has more than one hardlink.

    A hardlink shares an inode but resolves to a safe path, so the path-based guard cannot see
    it; an in-place write would mutate the shared (possibly live ``~/.claude``) inode. We
    cannot cheaply prove the other link is not under live, so we fail closed on any multi-link
    target rather than risk mutating live runtime through an inode alias.
    """
    if target.is_symlink():
        return False
    try:
        st = target.lstat()
    except OSError:
        return False
    return target.is_file() and st.st_nlink > 1


def _target_unsafe_for_apply(target: Path) -> bool:
    """Combined live-apply guard: refuse if the target escapes to live via a symlinked path
    OR aliases a live inode via a hardlink."""
    return _target_escapes_to_live(target) or _target_aliases_live(target)


def _normpath(value: str | Path) -> str:
    """Lexical normalization only (collapse ``.``/``..``/dup-slashes). Never resolves symlinks —
    the live-home declared-target check must be lexical, not filesystem-resolved."""
    return os.path.normpath(os.fspath(value))


def _live_context_user_parent_clean(home_root: Path, target: Path) -> bool:
    """True only if ``target.parent`` is lexically under ``home_root`` AND every existing path
    component strictly between them is a real directory (no symlink in the chain).

    A symlinked parent (``home_root/commands`` -> elsewhere) would make a write through the
    "declared" lexical path land somewhere other than the declared target, so it is refused.
    """
    home_norm = Path(_normpath(home_root))
    # BLOCKER-1: the live root itself must not be a symlink — otherwise a write to the "declared"
    # lexical path lands in the symlink target outside the declared live subtree.
    if home_norm.is_symlink():
        return False
    parent_norm = Path(_normpath(target.parent))
    try:
        rel = parent_norm.relative_to(home_norm)
    except ValueError:
        return False
    cur = home_norm
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            return False
    return True


def _unsafe_temp_sibling(target: Path) -> bool:
    """True if a pre-existing entry in ``target.parent`` matching the atomic-write temp prefix
    ``".{target.name}."`` is a symlink or a hardlink alias.

    The live atomic writers stage through ``tempfile.mkstemp(dir=target.parent,
    prefix=f".{target.name}.")``; a planted symlink/hardlink alias under that prefix is a
    CRITICAL-3-class lure, so live-home refuses before staging any temp."""
    parent = target.parent
    try:
        if not parent.is_dir() or parent.is_symlink():
            return False
        prefix = f".{target.name}."
        for child in parent.iterdir():
            if not child.name.startswith(prefix):
                continue
            if child.is_symlink():
                return True
            try:
                if child.lstat().st_nlink > 1:
                    return True
            except OSError:
                return True
    except OSError:
        return False
    return False


def _classify_live_target(entry: PlanEntry, *, home_root: Path, common_root: Path) -> str | None:
    """Declared-target-only live guard (AC-11). Return a refusal reason if a planned entry is
    unsafe for an explicit ``--live-home`` apply, else ``None``.

    A real ``~/.claude`` write is permitted ONLY when the planned target is exactly the
    catalog-declared ``home_root/<target_relpath>`` for a ``user`` context artifact and the path is
    safe (clean parent chain, not a directory, not a hardlink alias, not a symlinked
    merge-template, no planted temp alias). Every other case — a non-declared/tampered target, a
    symlinked parent escape, any non-user context target resolving under live ``~/.claude``, a hardlink
    alias — is refused. ``context user`` targets are intentionally exempt from
    ``_target_escapes_to_live`` because they are SUPPOSED to live under ``~/.claude``; their safety
    is proven by lexical-declared-equality + a symlink-free parent chain instead.
    """
    target_str = entry.get("target") or ""
    if not target_str:
        return None
    target = Path(target_str)
    context = entry.get("context", "")
    target_relpath = entry.get("target_relpath", "")

    # MAJOR-1: validate target_relpath HERE (not only in build_plan). A synthetic/tampered plan
    # entry with a `..`/absolute/drive target_relpath must never normalize into a permitted live
    # target via the classifier's defense-in-depth layer.
    relpath_error = _validate_target_relpath(target_relpath)
    if relpath_error is not None:
        return f"live-home refused: {relpath_error}"

    if context == "user":
        declared = Path(_normpath(home_root / target_relpath))
        if Path(_normpath(target)) != declared:
            return (
                f"live-home refused: planned target {target} is not the catalog-declared "
                f"context user target {declared}"
            )
        if not _live_context_user_parent_clean(home_root, target):
            return f"live-home refused: declared target {target} has a symlinked/escaping parent chain"
        if target.is_dir() and not target.is_symlink():
            return f"live-home refused: declared target {target} exists as a directory"
        if _target_aliases_live(target):
            return f"live-home refused: declared target {target} is a hardlink alias (st_nlink>1)"
        if entry.get("type") == "merge-template" and target.is_symlink():
            return f"live-home refused: merge-template target {target} is a symlink"
        if _unsafe_temp_sibling(target):
            return f"live-home refused: unsafe pre-existing temp alias near {target}"
        return None

    # Non-user contexts must NEVER resolve under the live ``~/.claude`` tree in live-home mode.
    if context == "repo.common":
        declared = Path(_normpath(common_root / target_relpath))
        if Path(_normpath(target)) != declared:
            return (
                f"live-home refused: planned target {target} is not the catalog-declared "
                f"context repo.common target {declared}"
            )
        if not _live_context_user_parent_clean(common_root, target):
            return f"live-home refused: declared repo.common target {target} has a symlinked/escaping parent chain"
    else:
        return f"live-home refused: unsupported non-user context {context!r}"
    if _target_escapes_to_live(target):
        return f"live-home refused: non-user context target {target} resolves under live ~/.claude"
    if _target_aliases_live(target):
        return f"live-home refused: target {target} is a hardlink alias (st_nlink>1)"
    return None


def _guard_target_for_apply(
    entry: PlanEntry, *, live_home: bool, home_root: Path | None, common_root: Path | None
) -> None:
    """Defense-in-depth guard invoked inside every ``_apply_*`` helper.

    Temp-harness mode keeps the established Phase 5 guard (refuse symlink-escape / hardlink-alias).
    ``--live-home`` mode re-runs the declared-target classifier so an _apply_* helper can never
    write a non-declared live target even if the pre-apply pass were bypassed. Raises
    ``_LiveApplyRefused`` on refusal."""
    if live_home:
        assert home_root is not None and common_root is not None
        reason = _classify_live_target(entry, home_root=home_root, common_root=common_root)
        if reason is not None:
            raise _LiveApplyRefused(reason)
        return
    if _target_unsafe_for_apply(Path(entry["target"])):
        raise _LiveApplyRefused(Path(entry["target"]))


def _controlled_root_for_entry(entry: PlanEntry, *, home_root: Path, common_root: Path) -> Path:
    context = entry.get("context", "")
    if context == "user":
        return home_root
    if context == "repo.common":
        return common_root
    raise _LiveApplyRefused(f"unsupported live apply context {context!r}")


def _open_validated_live_parent_dirfd(home_root: Path, target: Path) -> int:
    """Open ``target.parent`` as a directory fd, anchoring every live write within the real
    controlled-config dir.

    **Trust boundary policy (explicit, MAJOR-R2-1).** The operator's home-directory PATH — every
    component ABOVE ``.claude`` — is trusted system state and is intentionally *resolved*: the real
    ``~/.claude`` is located via the home path exactly as Claude Code itself resolves it. This is
    deliberate so that a symlinked home location (macOS ``/var -> /private/var``, network/enterprise
    or relocated home dirs, and pytest's ``/var/folders`` sandbox) works; rejecting ancestor
    symlinks would break legitimate setups, and an actor who can rewrite the home path already owns
    the account. The CONTROLLED, attacker-relevant no-follow surface is ``.claude`` AND EVERYTHING
    BELOW it: ``.claude`` itself must be a real directory (a symlinked ``.claude`` is refused —
    BLOCKER-1), and every descendant component is opened ``O_NOFOLLOW|O_DIRECTORY`` so a
    symlinked/swapped parent cannot redirect the write (BLOCKER-2). The returned fd anchors all
    writes by basename only, never re-resolving a path string. The caller MUST ``os.close()`` it.
    Raises ``_LiveApplyRefused`` on a symlinked ``.claude``/descendant or out-of-root parent.
    """
    home_norm = Path(_normpath(home_root))
    # Canonical live root: resolve the home-directory ancestor (trusted), keep `.claude` literal and
    # no-follow. `O_NOFOLLOW` on this final component still refuses a symlinked `.claude` race-safely.
    canonical_root = home_norm.parent.resolve(strict=False) / home_norm.name
    parent_norm = Path(_normpath(target.parent))
    try:
        rel = parent_norm.relative_to(home_norm)
    except ValueError:
        raise _LiveApplyRefused(f"live target parent {parent_norm} is not under live root {home_norm}")

    open_flags = os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(str(canonical_root), open_flags)
    except OSError as exc:
        raise _LiveApplyRefused(f"refusing controlled root {canonical_root}: must be a real directory ({exc})")
    try:
        for part in rel.parts:
            if part in (os.curdir, os.pardir) or os.sep in part or (os.altsep and os.altsep in part):
                raise _LiveApplyRefused(f"illegal live parent component {part!r} under {canonical_root}")
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise _LiveApplyRefused(f"cannot create live parent component {part!r}: {exc}")
            try:
                child = os.open(part, open_flags, dir_fd=fd)
            except OSError as exc:
                raise _LiveApplyRefused(f"refusing symlinked/non-dir live parent component {part!r}: {exc}")
            # MINOR-R2-1: close the old fd; if that raises, do not leak the freshly-opened child.
            try:
                os.close(fd)
            except BaseException:
                os.close(child)
                raise
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _unlink_existing_at(fd: int, name: str) -> None:
    """Unlink a non-directory ``name`` in the dir referenced by ``fd`` if present. Refuse a
    directory (a live directory target is never replaced)."""
    try:
        st = os.lstat(name, dir_fd=fd)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        raise _LiveApplyRefused(f"live target {name!r} is a directory; refusing to replace it")
    os.unlink(name, dir_fd=fd)


def _refuse_directory_at(fd: int, name: str) -> None:
    """Refuse a directory live ``name`` BEFORE staging an atomic replace (a directory target is
    never overwritten by the forward apply). Read-only probe: never unlinks."""
    try:
        st = os.lstat(name, dir_fd=fd)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        raise _LiveApplyRefused(f"live target {name!r} is a directory; refusing to replace it")


def _live_temp_name(name: str) -> str:
    # Random suffix + O_EXCL|O_NOFOLLOW on create defeats a planted predictable-temp alias.
    return f".{name}.{os.urandom(8).hex()}.com146-live-tmp"


def _write_symlink_live(home_root: Path, target: Path, link_dest: str) -> None:
    """(BLOCKER-1) Atomically (re)point the fd-anchored live symlink ``target`` at ``link_dest`` by
    staging a temp-sibling symlink and ``os.replace``-ing it into place anchored by ``dir_fd`` —
    NEVER unlink-then-create, so a mid-write failure leaves the prior link intact."""
    fd = _open_validated_live_parent_dirfd(home_root, target)
    try:
        name = target.name
        _refuse_directory_at(fd, name)
        tmp = _live_temp_name(name)
        os.symlink(link_dest, tmp, dir_fd=fd)
        try:
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=fd)
            except OSError:
                pass
            raise
    finally:
        os.close(fd)


def _write_bytes_live(home_root: Path, target: Path, data: bytes, *, mode: int | None = None) -> None:
    """Atomically materialize ``data`` at the fd-anchored live ``target`` via an O_EXCL|O_NOFOLLOW
    temp + ``os.replace`` anchored by ``dir_fd`` (never a path string). Shared by the managed-copy
    writer and the file-state restore path so both inherit the same no-follow guarantees.

    ``mode`` (P15 field finding): when given, the temp file is ``fchmod``-ed BEFORE the rename so
    the target appears atomically with the right bits (fchmod is umask-immune; the os.open mode
    arg is not)."""
    create_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = _open_validated_live_parent_dirfd(home_root, target)
    try:
        name = target.name
        tmp = _live_temp_name(name)
        tfd = os.open(tmp, create_flags, 0o644, dir_fd=fd)
        try:
            with os.fdopen(tfd, "wb") as handle:
                handle.write(data)
                if mode is not None:
                    os.fchmod(handle.fileno(), mode)
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=fd)
            except OSError:
                pass
            raise
    finally:
        os.close(fd)


def _write_managed_copy_live(home_root: Path, target: Path, source: Path) -> None:
    # (P15 field finding) Propagate the master's mode bits so an executable master never lands
    # live as the 0o644 temp default. The master is a trusted repo file; the no-follow
    # discipline applies to the live side, not this read.
    source_mode = stat.S_IMODE(os.stat(source).st_mode)
    _write_bytes_live(home_root, target, source.read_bytes(), mode=source_mode)


def _remove_target_live(home_root: Path, target: Path) -> None:
    """Remove a non-directory live ``target`` fd-anchored (used by absent-state restore)."""
    fd = _open_validated_live_parent_dirfd(home_root, target)
    try:
        _unlink_existing_at(fd, target.name)
    finally:
        os.close(fd)


def _read_bytes_nofollow(home_root: Path, target: Path) -> bytes:
    """(MAJOR-1) Read the bytes of a live regular-file ``target`` through the validated parent
    dirfd with ``O_NOFOLLOW`` on the final component — a final-component symlink swap is REFUSED,
    never followed off the controlled surface. Used by backup capture and restore-verify so both
    inherit the same no-follow guarantee as the writers."""
    fd = _open_validated_live_parent_dirfd(home_root, target)
    try:
        try:
            rfd = os.open(
                target.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=fd
            )
        except OSError as exc:
            raise _LiveApplyRefused(
                f"refusing no-follow read of live target {target.name!r}: {exc}"
            )
        with os.fdopen(rfd, "rb") as handle:
            return handle.read()
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    """fsync a directory so a rename/replace inside it is durable across a crash."""
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_p_fsync(path: Path) -> None:
    """(MAJOR-R2-2) Create ``path`` and any missing ancestors, fsyncing each newly-created
    directory's PARENT so the new directory ENTRY itself is durable across a crash.

    Fsyncing a directory only persists entries created *inside* it — it does NOT make that
    directory's own entry durable in its parent. So a freshly-created ``manifests/<run_id>`` whose
    contents are fsynced can still be lost on power failure unless ``manifests`` (its parent) is also
    fsynced. This walks the missing-ancestor chain top-down and fsyncs each created boundary's
    parent, so the backup run directory is crash-durable before any live mutation runs."""
    path = Path(path)
    missing: list[Path] = []
    cur = path
    while not cur.exists():
        missing.append(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_dir(directory.parent)


def _atomic_write_bytes_fsync(path: Path, data: bytes) -> None:
    """(MAJOR-4) Durably write ``data`` to ``path`` via a securely-created temp sibling + file
    fsync + ``os.replace`` + parent-dir fsync, so a crash never leaves a half-written backup blob
    or manifest that a later rollback would trust. Operates on the machine-local backup root (not
    the live no-follow surface), so a path-anchored mkstemp/replace is appropriate here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".com146-bak-tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _atomic_write_text_fsync(path: Path, text: str) -> None:
    _atomic_write_bytes_fsync(path, text.encode("utf-8"))


def _merge_template_live(home_root: Path, target: Path, master_payload: dict[str, Any]) -> bool:
    """Read the live target NO-FOLLOW, merge missing master keys, and atomically replace via an
    fd-anchored temp. Returns True if the target changed. Refuses a symlinked target."""
    create_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = _open_validated_live_parent_dirfd(home_root, target)
    try:
        name = target.name
        existing: dict[str, Any] = {}
        try:
            rfd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=fd)
        except FileNotFoundError:
            rfd = None
        except OSError as exc:
            raise _LiveApplyRefused(f"live merge-template target {name!r} is a symlink or unreadable: {exc}")
        if rfd is not None:
            with os.fdopen(rfd, "r", encoding="utf-8") as handle:
                loaded = json.loads(handle.read())
            if not isinstance(loaded, dict):
                raise _LiveApplyRefused(f"live merge-template target {name!r} is not a JSON object")
            existing = loaded
        shape_errors = merge_template_hook_shape_errors(existing, master_payload)
        if shape_errors:
            raise _LiveApplyRefused("; ".join(shape_errors))
        merged, additions = desired_merge_template_payload(existing, master_payload)
        changed = bool(additions)
        if not changed:
            return False
        tmp = _live_temp_name(name)
        tfd = os.open(tmp, create_flags, 0o644, dir_fd=fd)
        try:
            with os.fdopen(tfd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(merged, sort_keys=True, indent=2) + "\n")
            os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=fd)
            except OSError:
                pass
            raise
        return True
    finally:
        os.close(fd)


def _apply_symlink(
    entry: PlanEntry, *, live_home: bool = False, home_root: Path | None = None, common_root: Path | None = None
) -> PlanEntry:
    source = Path(entry["source"])
    target = Path(entry["target"])
    context = entry.get("context", "")
    _guard_target_for_apply(entry, live_home=live_home, home_root=home_root, common_root=common_root)
    if context == "user":
        # user context (~/.claude): an ABSOLUTE link (machine-local, never committed). Under
        # --live-home it is written fd-anchored at the lexical catalog-declared master path.
        if live_home:
            assert home_root is not None
            _write_symlink_live(Path(home_root), target, os.fspath(source))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(source.resolve(strict=False))
    else:
        # in-repo contexts (repo.* -> e.g. common/.claude): the deploy target is a TRACKED repo
        # file, so write a RELATIVE symlink that resolves in any checkout regardless of the repo's
        # absolute path. The pre-apply live guard already proved this target never escapes to live
        # ~/.claude, so a direct relative symlink (not the home-anchored live writer) is correct.
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        rel = os.path.relpath(source.resolve(strict=False), start=target.parent.resolve(strict=False))
        target.symlink_to(rel)
    entry = dict(entry)
    entry["status"] = "applied"
    entry["detail"] = "target symlink materialized to master"
    return entry


def _apply_managed_copy(
    entry: PlanEntry, *, live_home: bool = False, home_root: Path | None = None, common_root: Path | None = None
) -> PlanEntry:
    source = Path(entry["source"])
    target = Path(entry["target"])
    _guard_target_for_apply(entry, live_home=live_home, home_root=home_root, common_root=common_root)
    if live_home:
        assert home_root is not None and common_root is not None
        _write_managed_copy_live(_controlled_root_for_entry(entry, home_root=home_root, common_root=common_root), target, source)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            target.unlink()
        shutil.copyfile(source, target)
    entry = dict(entry)
    entry["status"] = "applied"
    entry["detail"] = "target managed-copy materialized from master"
    return entry


def _apply_merge_template(
    entry: PlanEntry, *, live_home: bool = False, home_root: Path | None = None, common_root: Path | None = None
) -> PlanEntry:
    source = Path(entry["source"])
    target = Path(entry["target"])
    _guard_target_for_apply(entry, live_home=live_home, home_root=home_root, common_root=common_root)
    master_payload, master_error = _load_json_mapping(source, role="master")
    if master_error is not None:
        entry = dict(entry)
        entry["status"] = "error"
        entry["detail"] = master_error
        return entry
    assert master_payload is not None

    if target.exists():
        target_payload, target_error = _load_json_mapping(target, role="target")
        if target_error is not None:
            entry = dict(entry)
            entry["status"] = "error"
            entry["detail"] = target_error
            return entry
        assert target_payload is not None
    else:
        target_payload = {}

    shape_errors = merge_template_hook_shape_errors(target_payload, master_payload)
    if shape_errors:
        entry = dict(entry)
        entry["status"] = "error"
        entry["detail"] = "; ".join(shape_errors)
        return entry

    if live_home:
        assert home_root is not None and common_root is not None
        changed = _merge_template_live(
            _controlled_root_for_entry(entry, home_root=home_root, common_root=common_root), target, master_payload
        )
        entry = dict(entry)
        if changed:
            entry["status"] = "applied"
            entry["detail"] = "target JSON received missing master keys without overwriting existing keys"
        else:
            entry["action"] = "noop"
            entry["status"] = "ok"
            entry["detail"] = "target JSON already contains all master keys"
        return entry

    merged, additions = desired_merge_template_payload(target_payload, master_payload)
    changed = bool(additions)

    entry = dict(entry)
    if changed:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via a SECURELY-created unique temp file, then os.replace into target.
        # tempfile.mkstemp uses O_CREAT|O_EXCL|O_WRONLY with a random name: it never follows a
        # symlink and never opens a pre-existing file, so a predictable/pre-created temp-path
        # symlink/hardlink alias of a live file cannot be written through (CRITICAL-3). The
        # freshly-created temp is a single-link regular file; os.replace swaps the directory
        # entry rather than mutating the (possibly live-aliased) target inode. target.parent is
        # already proven safe by the planned-target guard above.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".com146-merge-tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(merged, sort_keys=True, indent=2) + "\n")
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        entry["status"] = "applied"
        entry["detail"] = "target JSON received missing additive master entries without overwriting existing values"
    else:
        entry["action"] = "noop"
        entry["status"] = "ok"
        entry["detail"] = "target JSON already satisfies additive merge-template requirements"
    return entry


def _apply_plan(
    plan: list[PlanEntry], *, live_home: bool = False, home_root: Path | None = None, common_root: Path | None = None
) -> list[PlanEntry]:
    if any(entry["status"] in {"error", "refused"} for entry in plan):
        return plan

    roots = {"live_home": live_home, "home_root": home_root, "common_root": common_root}
    applied: list[PlanEntry] = []
    for entry in plan:
        if entry["action"] == "noop" and entry["status"] == "ok":
            applied.append(entry)
        elif entry["type"] == "symlink" and entry["action"] in {"create-symlink", "update-symlink"}:
            applied.append(_apply_symlink(entry, **roots))
        elif entry["type"] == "managed-copy" and entry["action"] in {"write-managed-copy", "update-managed-copy"}:
            applied.append(_apply_managed_copy(entry, **roots))
        elif entry["type"] == "merge-template" and entry["action"] == "merge-template":
            applied.append(_apply_merge_template(entry, **roots))
        else:
            failed = dict(entry)
            failed["status"] = "error"
            failed["detail"] = f"unsupported apply action/type combination: {entry['action']} / {entry['type']}"
            applied.append(failed)
    return applied


def _resolve_backup_root(home_root: str | Path) -> Path:
    """Resolve the AC-12 backup/preview/manifest root.

    Honors ``COM146_CONTROLLED_CONFIG_BACKUP_ROOT`` (the test/operator override). Otherwise it
    defaults to ``<home_root>/../.jswarm/backups/controlled-config/dotclaude`` — deliberately a
    SIBLING of the live ``.claude`` (not the repo) so a monkeypatched-home test never writes into
    the real repo or the real ``~/.jswarm``, and a real operator's backups land beside their home
    config tree."""
    override = os.environ.get(BACKUP_ROOT_ENV)
    if override:
        return Path(override)
    return Path(home_root).parent / ".jswarm" / "backups" / "controlled-config" / "dotclaude"


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{os.urandom(4).hex()}"


def _source_content_digest(entry: PlanEntry) -> str:
    """(BLOCKER-2) Digest of the source file bytes backing a plan entry, so the preview plan-hash
    binds the REVIEWED CONTENT, not merely path/action/status. Swapping the reviewed master bytes
    after preview (without changing any plan field) therefore still invalidates the gate. Empty
    string when the entry has no readable source (e.g. a merge-template whose end-state is computed
    at write time)."""
    source = entry.get("source")
    if not source:
        return ""
    try:
        return _sha256(Path(source))
    except OSError:
        return ""


def _canonical_plan_sha256(plan: list[PlanEntry]) -> str:
    """Stable hash over the semantically-meaningful plan fields PLUS each entry's source-content
    digest. Used as the preview-to-apply plan-hash gate: a preview captured at operator-review time
    must hash-equal the plan recomputed at apply time, else the apply is refused as stale (the
    catalog/master path OR the reviewed source bytes changed underneath)."""
    canonical = []
    for entry in plan:
        row = {key: entry.get(key, "") for key in PLAN_KEYS}
        row["source_sha256"] = _source_content_digest(entry)
        canonical.append(row)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_preview(backup_root: Path, plan: list[PlanEntry], *, live_home: bool, home_root: Path) -> Path:
    """Persist a canonical preview artifact (plan + plan_sha256) for later ``--from-preview`` apply.

    (MINOR-2) Carries an ``metadata`` block (home_root + created_utc) for operator audit/debug; the
    metadata deliberately does NOT participate in ``plan_sha256`` (the hash binds only the plan +
    source bytes), so audit annotations can never weaken or break the staleness gate."""
    run_id = _new_run_id()
    previews_dir = backup_root / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    preview = {
        "schema_version": LIVE_BACKUP_SCHEMA_VERSION,
        "kind": LIVE_PREVIEW_KIND,
        "run_id": run_id,
        "live_home": bool(live_home),
        "plan_sha256": _canonical_plan_sha256(plan),
        "plan_entries": plan,
        "metadata": {
            "home_root": str(home_root),
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }
    path = previews_dir / f"{run_id}.preview.json"
    _atomic_write_text_fsync(path, json.dumps(preview, sort_keys=True, indent=2) + "\n")
    return path


def _is_hex64(value: Any) -> bool:
    """True only for a 64-char lowercase-hex string (a real sha256 hexdigest). Rejects 64 arbitrary
    characters — the R2 gate accepted any ``len == 64`` value (BLOCKER-R2-1)."""
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _load_preview(
    backup_root: Path, preview_id: str, *, expected_live_home: bool
) -> tuple[str, dict[str, Any] | None]:
    """(BLOCKER-3 / BLOCKER-R2-1) Load a named preview, returning a typed (status, payload):

      * ``(PREVIEW_ABSENT, None)`` — NO preview path exists (determined no-follow via ``os.lstat``).
        This is the only out-of-band approval token: the caller warns and proceeds WITHOUT the
        plan-hash gate.
      * ``(PREVIEW_INVALID, None)`` — the preview path is PRESENT but is a symlink / directory /
        non-regular object / unreadable / not JSON / not a strict schema-v1 live-preview for this
        ``preview_id`` and apply mode. A present-but-bad preview is tamper/damage/race evidence; the
        caller MUST fail closed (never downgraded to "no gate").
      * ``(PREVIEW_OK, payload)`` — a strictly schema-valid preview: right kind + schema_version,
        ``run_id == preview_id``, a boolean ``live_home`` equal to ``expected_live_home``, a list
        ``plan_entries``, and a 64-HEX ``plan_sha256``.

    Absence is decided NO-FOLLOW so a dangling preview symlink (present, but ``Path.exists()`` reports
    absent because it follows the broken link) can never masquerade as the absent approval token —
    that was the R2 fail-open bypass.
    """
    path = backup_root / "previews" / f"{preview_id}.preview.json"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return PREVIEW_ABSENT, None
    except OSError:
        return PREVIEW_INVALID, None
    if not stat.S_ISREG(st.st_mode):
        # Present symlink / directory / fifo / device / any non-regular object: a preview MUST be a
        # plain regular file under the backup root. Present-but-not-a-regular-file = fail closed.
        return PREVIEW_INVALID, None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return PREVIEW_INVALID, None
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return PREVIEW_INVALID, None
    if not isinstance(loaded, dict):
        return PREVIEW_INVALID, None
    live_home = loaded.get("live_home")
    if (
        loaded.get("kind") != LIVE_PREVIEW_KIND
        or loaded.get("schema_version") != LIVE_BACKUP_SCHEMA_VERSION
        or loaded.get("run_id") != preview_id
        or not isinstance(live_home, bool)
        or live_home != expected_live_home
        or not isinstance(loaded.get("plan_entries"), list)
        or not _is_hex64(loaded.get("plan_sha256"))
    ):
        return PREVIEW_INVALID, None
    return PREVIEW_OK, loaded


def _capture_before(controlled_root: Path, target: Path, manifest_dir: Path) -> dict[str, Any]:
    """No-follow snapshot of the live ``target`` before mutation. For a real file the exact bytes
    are read through the fd-anchored no-follow reader (MAJOR-1 — a final-component symlink swap is
    refused, not followed off-surface) and durably persisted (MAJOR-4) into
    ``<manifest_dir>/files/<sha>.bin`` so rollback can restore them verbatim."""
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return {"state": "absent"}
    if stat.S_ISLNK(st.st_mode):
        return {"state": "symlink", "symlink_target": os.readlink(target)}
    if stat.S_ISDIR(st.st_mode):
        return {"state": "directory"}
    data = _read_bytes_nofollow(controlled_root, target)
    sha = hashlib.sha256(data).hexdigest()
    backup_relpath = f"files/{sha}.bin"
    backup_path = manifest_dir / backup_relpath
    _atomic_write_bytes_fsync(backup_path, data)
    # (P15 field finding) Capture mode bits too: the first real migration rollback restored a
    # 0o755 hook as 0o644 — bytes alone are not the whole before-state of a real file.
    return {
        "state": "file",
        "sha256": sha,
        "backup_relpath": backup_relpath,
        "mode": stat.S_IMODE(st.st_mode),
    }


def _planned_after(entry: PlanEntry) -> dict[str, Any]:
    install_type = entry.get("type", "")
    # (P12 MINOR-1) A retirement's planned end-state is ABSENCE, not an apply-shaped record.
    if entry.get("action") == "retire":
        return {"state": "absent"}
    source = Path(entry["source"]) if entry.get("source") else None
    if install_type == "symlink" and source is not None:
        return {"state": "symlink", "symlink_target": os.fspath(source)}
    if install_type == "managed-copy" and source is not None:
        # (P15 critic MAJOR-1 follow-on) Record the planned MODE too, so manifest/report evidence
        # captures the expected file mode the apply will set, not only its bytes.
        try:
            return {
                "state": "file",
                "sha256": _sha256(source),
                "mode": stat.S_IMODE(os.stat(source).st_mode),
            }
        except OSError:
            return {"state": "file"}
    # merge-template end-state is computed at write time; record the coarse planned state only.
    return {"state": "file"}


def _write_backup_manifest(
    *, backup_root: Path, run_id: str, home_root: Path, common_root: Path, mutating: list[PlanEntry]
) -> tuple[Path, dict[str, dict[str, Any]]]:
    """Capture before-state for every mutating entry and write the schema-v1 backup manifest BEFORE
    the apply loop touches anything. Returns the manifest path and a map keyed by the rollback key
    so the apply loop can roll back already-applied artifacts in reverse order on a mid-surface
    failure."""
    manifest_dir = backup_root / "manifests" / run_id
    # MAJOR-R2-2: crash-durably create the run directory — every newly-created boundary up to and
    # including <run_id> has its parent fsynced, so the run-dir entry (and thus the whole rollback
    # record) survives a power loss mid-apply. Then create+fsync files/ (so the captured before-blob
    # entries are durable in the run dir) BEFORE the apply loop mutates anything.
    _mkdir_p_fsync(manifest_dir)
    _mkdir_p_fsync(manifest_dir / "files")
    artifacts: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for entry in mutating:
        target = Path(entry["target"])
        controlled_root = _controlled_root_for_entry(entry, home_root=home_root, common_root=common_root)
        record = {
            "target_relpath": entry["target_relpath"],
            "context": entry["context"],
            "install_type": entry["type"],
            "target": str(target),
            "controlled_root": str(controlled_root),
            "before": _capture_before(controlled_root, target, manifest_dir),
            "after_planned": _planned_after(entry),
        }
        artifacts.append(record)
        by_key[_rollback_key(entry)] = record
    manifest = {
        "schema_version": LIVE_BACKUP_SCHEMA_VERSION,
        "kind": LIVE_BACKUP_KIND,
        "run_id": run_id,
        "home_root": str(home_root),
        "artifacts": artifacts,
    }
    manifest_path = manifest_dir / "manifest.json"
    # MAJOR-4: the manifest (and every captured before-blob above) is durably persisted BEFORE the
    # apply loop runs, so a crash mid-apply always leaves a complete, trustworthy rollback record.
    _atomic_write_text_fsync(manifest_path, json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest_path, by_key


def _rollback_key(entry: PlanEntry) -> str:
    return f"{entry.get('context', '')}::{entry.get('target_relpath', '')}::{entry.get('target', '')}"


# --- AC-14/AC-15: live-deployment ledger + lifecycle smoke seams ----------------------------------

LEDGER_PATH_ENV = "COM146_CONTROLLED_CONFIG_LEDGER_PATH"
LEDGER_SCHEMA_VERSION = 2


def _resolve_ledger_path(catalog_root: str | Path, ledger_arg: str | Path | None) -> Path:
    """Resolve the machine-local live-deployment ledger: explicit --ledger arg, then the
    env override, then `<catalog_root>/.jswarm/state/controlled-config/dotclaude-live-state.json`
    (machine-local; gitignored — never committed)."""
    if ledger_arg:
        return Path(ledger_arg)
    override = os.environ.get(LEDGER_PATH_ENV)
    if override:
        return Path(override)
    return Path(catalog_root) / ".jswarm" / "state" / "controlled-config" / "dotclaude-live-state.json"


def _load_ledger(path: Path) -> tuple[dict[str, Any], str | None]:
    """Load the ledger fail-closed: absent → empty ledger; present-but-corrupt → error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"schema_version": LEDGER_SCHEMA_VERSION, "targets": {}}, None
    except OSError as exc:
        return {}, f"ledger unreadable at {path}: {exc}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"ledger JSON parse error at {path}: {exc}"
    if not isinstance(payload, dict) or not isinstance(payload.get("targets"), dict):
        return {}, f"ledger at {path} must be an object with a 'targets' mapping"
    schema_version = payload.get("schema_version")
    if schema_version != LEDGER_SCHEMA_VERSION:
        if schema_version == 1:
            return (
                {},
                "ledger schema_version 1 uses legacy target_relpath::scope keys; run COM-176 "
                "ledger reset/re-apply or migrate_ledger_one_home.py",
            )
        return {}, f"ledger at {path} has unsupported schema_version {schema_version!r}"
    for key, record in payload["targets"].items():
        if not isinstance(record, dict):
            return {}, f"ledger record {key!r} at {path} must be an object"
        expected_key = _ledger_key(str(record.get("context", "")), str(record.get("target_relpath", "")))
        if key != expected_key:
            return {}, f"ledger record {key!r} at {path} must use context::target_relpath key {expected_key!r}"
    return payload, None


def _write_ledger_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text_fsync(path, json.dumps(payload, sort_keys=True, indent=2) + "\n")


def _ledger_key(context: str, target_relpath: str) -> str:
    return f"{context}::{target_relpath}"


def _surface_family(target_relpath: str) -> str:
    """Surface family for smoke routing: 'settings' for the merge-template settings file,
    else the first path component (commands/hooks/skills/rules)."""
    if target_relpath == "settings.json":
        return "settings"
    return target_relpath.split("/", 1)[0]


def _update_ledger_after_apply(
    ledger_path: Path, applied: list[PlanEntry], *, home_root: Path, manifest_path: Path
) -> None:
    """(AC-15 required state) Upsert the last-successful-deployment record per applied target.
    Failures here must not un-succeed the apply — the ledger is advisory machine-local state."""
    ledger, error = _load_ledger(ledger_path)
    if error is not None:
        print(f"warning: ledger not updated ({error})", file=sys.stderr)
        return
    targets = ledger.setdefault("targets", {})
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for entry in applied:
        source = str(entry.get("source", ""))
        try:
            source_sha = _sha256(Path(source)) if source else ""
        except OSError:
            source_sha = ""
        targets[_ledger_key(str(entry["context"]), str(entry["target_relpath"]))] = {
            "context": entry["context"],
            "target_relpath": entry["target_relpath"],
            "install_type": entry["type"],
            "target": entry["target"],
            "source": source,
            "source_sha256": source_sha,
            "last_manifest": str(manifest_path),
            "last_verified_at_utc": stamp,
        }
    _write_ledger_atomic(ledger_path, ledger)


def run_runtime_smoke(*, surface: str, manifest_path: str | Path, home_root: str | Path) -> bool:
    """(AC-14) Post-apply runtime-honor smoke seam, monkeypatchable by tests.

    The real implementation delegates to the AC-13 runner for the automatable surfaces
    (command/rule/skill/command-injection probe deploy + fresh-session smoke + cleanup).
    hook/settings are COUPLED surfaces (a hook only fires with a settings.json registration)
    and fail CLOSED here: their smoke is the operator-driven coupled sequence, never an
    unattended engine call. The probe rides the operator's live acknowledgement: this seam
    only runs after an `apply --live-home` the operator explicitly invoked.
    """
    from jswarm.host.deploy import runtime_smoke_runner as _runner  # lazy: avoids cycle

    family_to_surface = {
        "commands": "command",
        "hooks": "hook",
        "skills": "skill",
        "rules": "rule",
        "settings": "settings",
    }
    runner_surface = family_to_surface.get(surface, surface)
    if runner_surface in {"hook", "settings"}:
        print(
            f"runtime smoke for {surface!r} requires the operator-driven coupled hook+settings "
            f"sequence; refusing the unattended engine path (fail-closed)"
        )
        return False
    repo_root = Path(__file__).resolve().parents[2]
    nonce = f"engine-{_new_run_id()}"
    plan = _runner.build_probe_plan(
        surface=runner_surface,
        master_root=repo_root / ".jswarm" / "local" / "com146-smoke" / "masters",
        common_root=repo_root / ".claude",
        home_root=home_root,
        nonce=nonce,
        # MINOR-2: the ENGINE owns rollback of the apply manifest on smoke failure; the runner's
        # plan deliberately carries no manifest so exactly one party rolls back.
        manifest_path=None,
        log_root=repo_root / ".jswarm" / "logs" / "controlled-config-smoke",
        live_home_ack=_runner._classify_live_root(home_root),
    )
    _runner._write_probe_master(plan)
    if runner_surface == "command-injection":
        _runner._stage_injection_probe_root(plan)
    deployed = _runner._deploy_probe(plan, install_type="symlink")
    try:
        result = _runner.run_surface_smoke(runner_surface, plan)
    finally:
        if deployed:
            _runner._cleanup_probe(plan)
    # MINOR-5: engine-invoked smokes leave the same durable NDJSON trail as CLI smokes.
    _runner._append_evidence(plan, result, run_id=f"engine-apply-smoke-{nonce}")
    return result.passed


def run_negative_smoke(*, surface: str, target_relpath: str, home_root: str | Path) -> bool:
    """(AC-15) Post-retire negative smoke seam, monkeypatchable by tests.

    v1 real proof: the declared target is GONE from the live tree (no-follow lstat). The
    fresh-session negative probes from the spec's table (e.g. `/help` no longer lists the
    command) are operator-driven via the AC-13 runner / mode 37 — this seam records the
    filesystem-gone invariant the engine itself is responsible for."""
    del surface
    target = Path(home_root) / target_relpath
    try:
        os.lstat(target)
    except FileNotFoundError:
        return True
    print(f"negative smoke failed: retired target still present at {target}")
    return False


def _restore_before(record: dict[str, Any], *, controlled_root: Path, manifest_dir: Path) -> None:
    """Restore a single artifact to its captured before-state using the same fd-anchored live
    writers the forward apply uses (no path-string write ever re-resolves a symlinked parent)."""
    target = Path(record["target"])
    before = record["before"]
    state = before.get("state")
    if state == "absent":
        _remove_target_live(controlled_root, target)
        return
    if state == "symlink":
        _write_symlink_live(controlled_root, target, before["symlink_target"])
        return
    if state == "file":
        data = (manifest_dir / before["backup_relpath"]).read_bytes()
        # (P15 field finding) Restore the captured mode bits too; a legacy manifest without
        # a recorded mode falls back to the previous bytes-only behavior.
        _write_bytes_live(controlled_root, target, data, mode=before.get("mode"))
        return
    if state == "directory":
        # MINOR-1: a directory before-state cannot prove contents/mode/owner, so rollback REFUSES it
        # rather than silently claiming success. (The forward apply never replaces a directory, so a
        # directory before-state should never legitimately appear in a manifest we produced.)
        raise _LiveApplyRefused(
            f"directory before-state for {target} is non-restorable; refusing to claim rollback "
            f"success without proving directory contents/mode/owner"
        )
    raise _LiveApplyRefused(f"unknown before-state {state!r} for {target}")


def _verify_restored(record: dict[str, Any], *, controlled_root: Path, manifest_dir: Path) -> bool:
    """Prove the on-disk target now matches the captured before-state (rollback is verified, not
    merely attempted). File reads go through the no-follow reader (MAJOR-1)."""
    target = Path(record["target"])
    before = record["before"]
    state = before.get("state")
    if state == "absent":
        return not (target.exists() or target.is_symlink())
    if state == "symlink":
        try:
            return os.readlink(target) == before["symlink_target"]
        except OSError:
            return False
    if state == "file":
        try:
            data = _read_bytes_nofollow(controlled_root, target)
        except (_LiveApplyRefused, OSError):
            return False
        if hashlib.sha256(data).hexdigest() != before.get("sha256"):
            return False
        # (P15 field finding) Verify restored mode bits when the manifest captured them
        # (legacy manifests without a mode verify on bytes alone, as before).
        recorded_mode = before.get("mode")
        if recorded_mode is not None:
            try:
                return stat.S_IMODE(os.lstat(target).st_mode) == recorded_mode
            except OSError:
                return False
        return True
    # A directory before-state is non-restorable (see _restore_before); never verify it as restored.
    return False


def _controlled_root_for_record(
    record: dict[str, Any], *, home_root: Path, common_root: Path | None
) -> Path:
    context = record.get("context", "")
    if context == "user":
        return home_root
    if context == "repo.common":
        if common_root is None:
            raise _LiveApplyRefused("repo.common rollback requires a trusted --common-root")
        recorded_root = record.get("controlled_root")
        if not isinstance(recorded_root, str) or not recorded_root:
            raise _LiveApplyRefused(f"repo.common rollback record for {record.get('target')!r} has no controlled_root")
        trusted_root = Path(_normpath(common_root))
        if Path(_normpath(recorded_root)) != trusted_root:
            raise _LiveApplyRefused(
                f"repo.common rollback record controlled_root {recorded_root!r} does not match trusted common root {trusted_root}"
            )
        return trusted_root
    raise _LiveApplyRefused(f"unsupported rollback context {context!r}")


def _rollback_records(
    records: list[dict[str, Any]], *, home_root: Path, common_root: Path | None, manifest_dir: Path
) -> tuple[bool, list[str]]:
    """Restore the given records in REVERSE order and verify each. Returns (ok, problems)."""
    problems: list[str] = []
    for record in reversed(records):
        try:
            controlled_root = _controlled_root_for_record(record, home_root=home_root, common_root=common_root)
            _restore_before(record, controlled_root=controlled_root, manifest_dir=manifest_dir)
        except (_LiveApplyRefused, OSError) as exc:
            problems.append(f"restore failed for {record.get('target')}: {exc}")
            continue
        if not _verify_restored(record, controlled_root=controlled_root, manifest_dir=manifest_dir):
            problems.append(f"restore verification failed for {record.get('target')}")
    return (not problems), problems


def _apply_one_live(entry: PlanEntry, *, home_root: Path, common_root: Path) -> PlanEntry:
    """Apply a single mutating entry in live-home mode. Raises on refusal/error so the caller can
    trigger reverse-order rollback."""
    install_type = entry["type"]
    roots = {"live_home": True, "home_root": home_root, "common_root": common_root}
    if install_type == "symlink":
        result = _apply_symlink(entry, **roots)
    elif install_type == "managed-copy":
        result = _apply_managed_copy(entry, **roots)
    elif install_type == "merge-template":
        result = _apply_merge_template(entry, **roots)
    else:
        raise _LiveApplyRefused(f"unsupported live apply type {install_type!r}")
    if result.get("status") == "error":
        raise _LiveApplyRefused(result.get("detail", f"apply error for {entry.get('target')}"))
    return result


def _apply_live_with_backup(
    plan: list[PlanEntry], *, home_root: Path, common_root: Path, backup_root: Path
) -> tuple[list[PlanEntry], int, Path | None]:
    """Live-home apply with a before-mutation backup manifest and reverse-order auto-rollback.

    The plan is assumed already free of error/refused entries and already classified safe by the
    pre-apply declared-target pass. Idempotent no-op plans (every entry already reconciled) write NO
    manifest and mutate nothing. Returns ``(final_plan, rc, manifest_path)`` — ``manifest_path`` is
    None for the no-op path and otherwise the durable manifest the caller surfaces as the
    one-command rollback handle (MAJOR-5)."""
    mutating = [entry for entry in plan if entry["status"] == "would-change"]
    if not mutating:
        return plan, 0, None

    run_id = _new_run_id()
    manifest_path, record_by_key = _write_backup_manifest(
        backup_root=backup_root, run_id=run_id, home_root=home_root, common_root=common_root, mutating=mutating
    )
    manifest_dir = manifest_path.parent

    applied_records: list[dict[str, Any]] = []
    results: dict[str, PlanEntry] = {}
    failure: str | None = None
    for entry in mutating:
        try:
            results[_rollback_key(entry)] = _apply_one_live(entry, home_root=home_root, common_root=common_root)
        except (_LiveApplyRefused, OSError) as exc:
            ok, problems = _rollback_records(
                applied_records, home_root=home_root, common_root=common_root, manifest_dir=manifest_dir
            )
            detail = f"live apply failed at {entry.get('target')}: {exc}"
            if not ok:
                detail += f"; rollback INCOMPLETE: {'; '.join(problems)} (manifest {manifest_path})"
            else:
                detail += f"; already-applied artifacts rolled back from {manifest_path}"
            failure = detail
            break
        applied_records.append(record_by_key[_rollback_key(entry)])

    if failure is not None:
        print(f"{LIVE_APPLY_REFUSAL}: {failure}")
        return plan, 1, manifest_path

    final = [results.get(_rollback_key(entry), entry) if entry["status"] == "would-change" else entry for entry in plan]
    return final, 0, manifest_path


def _prevalidate_rollback_records(records: Any, *, manifest_dir: Path) -> str | None:
    """(MAJOR-R3-1) Refuse-all-before-any-write for standalone rollback: fully validate the manifest
    record list and EVERY before-state payload — including reading and SHA-verifying each file backup
    blob — BEFORE any restore mutates the live surface. A damaged/truncated/hand-edited manifest must
    be refused whole, never partially applied (a partial restore would itself create mixed live state,
    e.g. a valid later record restored before an earlier record fails on a missing blob). Returns an
    error string on the first defect, else None."""
    if not isinstance(records, list):
        return "manifest 'artifacts' must be a list"
    for record in records:
        if not isinstance(record, dict):
            return f"manifest artifact record is not an object: {record!r}"
        before = record.get("before")
        if not isinstance(before, dict):
            return f"artifact {record.get('target')!r} has no before-state object"
        state = before.get("state")
        if state == "absent":
            if before.get("backup_relpath"):
                return f"absent before-state for {record.get('target')!r} must carry no backup blob"
            continue
        if state == "symlink":
            link = before.get("symlink_target")
            if not isinstance(link, str) or not link:
                return f"symlink before-state for {record.get('target')!r} needs a string symlink_target"
            continue
        if state == "directory":
            # A directory before-state is non-restorable (cannot prove contents/mode/owner). Refuse
            # the WHOLE rollback before any write, not just-in-time mid-restore.
            return (
                f"directory before-state for {record.get('target')!r} is non-restorable; refusing "
                f"rollback rather than partially applying"
            )
        if state == "file":
            rel = before.get("backup_relpath", "")
            rel_error = _validate_target_relpath(rel)
            if rel_error is not None:
                return f"file backup_relpath unsafe for {record.get('target')!r}: {rel_error}"
            blob = manifest_dir / rel
            try:
                st = os.lstat(blob)
            except OSError as exc:
                return f"file backup blob missing for {record.get('target')!r} at {blob}: {exc}"
            if not stat.S_ISREG(st.st_mode):
                return f"file backup blob for {record.get('target')!r} is not a regular file at {blob}"
            try:
                data = blob.read_bytes()
            except OSError as exc:
                return f"file backup blob unreadable for {record.get('target')!r} at {blob}: {exc}"
            if hashlib.sha256(data).hexdigest() != before.get("sha256"):
                return f"file backup blob sha mismatch for {record.get('target')!r} at {blob}"
            continue
        return f"unknown before-state {state!r} for {record.get('target')!r}"
    return None


def _rollback_from_manifest(
    manifest_path: Path, *, live_home_ack: bool, common_root: Path | None = None
) -> int:
    """``rollback --manifest`` entrypoint: restore + verify every artifact in reverse order.

    Pre-restore guards, all BEFORE anything is touched (refuse-all-before-any-write):
      * (MAJOR-2 / MAJOR-R2-1) If the manifest's ``home_root`` IS the live ``~/.claude`` — lexically
        OR by symlink-resolved identity — an explicit ``--live-home`` acknowledgement is required
        (parity with ``apply --live-home``; a manifest is a write primitive over the live surface).
      * (MAJOR-2) Every artifact ``target`` must equal ``home_root/target_relpath`` (with a safe
        relpath); an out-of-root or traversal target is a write-primitive escape.
      * (MAJOR-R3-1) The full record list and every before-state payload — including each file backup
        blob's existence, regular-file-ness, and SHA — is validated, so a damaged/incomplete manifest
        is refused whole rather than partially restored.
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"refusing rollback: manifest unreadable at {manifest_path}: {exc}")
        return 1
    if not isinstance(manifest, dict) or manifest.get("kind") != LIVE_BACKUP_KIND or manifest.get(
        "schema_version"
    ) != LIVE_BACKUP_SCHEMA_VERSION:
        print(
            f"refusing rollback: manifest {manifest_path} is not a schema_version="
            f"{LIVE_BACKUP_SCHEMA_VERSION} {LIVE_BACKUP_KIND}"
        )
        return 1
    home_raw = manifest.get("home_root", "")
    if not home_raw:
        print(f"refusing rollback: manifest {manifest_path} has no home_root")
        return 1
    home_root = Path(home_raw)

    # MAJOR-2 / MAJOR-R2-1: a manifest whose home_root IS the live ~/.claude — lexically OR by
    # symlink-RESOLVED identity — requires explicit --live-home. The forward live writer resolves
    # trusted home ancestors (_open_validated_live_parent_dirfd), so a symlink-alias home_root that
    # resolves to the live root would otherwise mutate the real runtime without the live opt-in. A
    # lexical-only check (R2) missed that alias; classify live by both.
    live_dotclaude = Path.home() / ".claude"
    lexical_live = _normpath(home_root) == _normpath(live_dotclaude)
    try:
        resolved_live = home_root.resolve(strict=False) == live_dotclaude.resolve(strict=False)
    except OSError:
        resolved_live = False
    if (lexical_live or resolved_live) and not live_home_ack:
        print(
            f"refusing rollback: manifest {manifest_path} targets the live ~/.claude "
            f"(lexically or by resolved identity); pass --live-home to acknowledge a real-home rollback"
        )
        return 1

    records = manifest.get("artifacts", [])
    # MAJOR-R3-1: validate the full record list + every before-state payload (incl. each file backup
    # blob's existence + SHA) BEFORE any restore, and type-guard `records` for the target loop below.
    prevalidation_error = _prevalidate_rollback_records(records, manifest_dir=manifest_path.parent)
    if prevalidation_error is not None:
        print(f"refusing rollback: {prevalidation_error} in {manifest_path}")
        return 1

    # MAJOR-2: validate every artifact target equals its declared controlled root / target_relpath
    # BEFORE any restore. Refuse-all-before-any-write parity with the forward declared-target guard.
    for record in records:
        relpath = record.get("target_relpath", "")
        relpath_error = _validate_target_relpath(relpath)
        if relpath_error is not None:
            print(f"refusing rollback: {relpath_error} in {manifest_path}")
            return 1
        try:
            controlled_root = _controlled_root_for_record(record, home_root=home_root, common_root=common_root)
        except _LiveApplyRefused as exc:
            print(f"refusing rollback: {exc} in {manifest_path}")
            return 1
        declared = _normpath(controlled_root / relpath)
        actual = _normpath(Path(record.get("target", "")))
        if actual != declared:
            print(
                f"refusing rollback: artifact target {record.get('target')!r} is not the declared "
                f"{declared!r} in {manifest_path}"
            )
            return 1

    ok, problems = _rollback_records(
        records, home_root=home_root, common_root=common_root, manifest_dir=manifest_path.parent
    )
    if not ok:
        for problem in problems:
            print(problem)
        return 1
    print(f"rollback restored and verified {len(records)} artifact(s) from {manifest_path}")
    return 0


def _rollback_command(manifest_path: Path, *, common_root: Path) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("artifacts", [])
    needs_common_root = any(isinstance(record, dict) and record.get("context") == "repo.common" for record in records)
    command = ["deploy.py", "rollback", "--manifest", str(manifest_path), "--live-home"]
    if needs_common_root:
        command[:1] = [str(Path(sys.executable)), str(Path(__file__).resolve())]
        command.extend(["--common-root", str(common_root)])
    return " ".join(shlex.quote(part) for part in command)


def _print_json(plan: list[PlanEntry]) -> None:
    print(json.dumps(plan, indent=2, sort_keys=True))


def _print_text(plan: list[PlanEntry]) -> None:
    print("target_relpath | context | type | action | status | target | rollback_hint | detail")
    print("--- | --- | --- | --- | --- | --- | --- | ---")
    for entry in plan:
        print(
            " | ".join(
                [
                    entry["target_relpath"],
                    entry["context"],
                    entry["type"],
                    entry["action"],
                    entry["status"],
                    entry["target"],
                    entry["rollback_hint"],
                    entry["detail"],
                ]
            )
        )


def _emit_plan(plan: list[PlanEntry], output_format: str) -> None:
    if output_format == "json":
        _print_json(plan)
    else:
        _print_text(plan)


def _is_live_home_root(home_root: str | Path) -> bool:
    """Live-root classification: lexical OR resolved (P9 MAJOR-R2-1 dual) OR same-file identity
    (P12 MINOR-6: parity with the smoke runner's triple — covers case-aliasing filesystems)."""
    live = Path.home() / ".claude"
    candidate = Path(home_root)
    if _normpath(candidate) == _normpath(live):
        return True
    try:
        if candidate.resolve(strict=False) == live.resolve(strict=False):
            return True
    except OSError:
        pass
    try:
        return candidate.exists() and live.exists() and os.path.samefile(candidate, live)
    except OSError:
        return False


RETIRE_PREVIEW_KIND = "controlled-config-dotclaude-retire-preview"


def _retire_candidates_sha(candidates: dict[str, dict[str, Any]]) -> str:
    """Canonical hash binding a retire approval to the EXACT candidate set previewed
    (P12 MAJOR-4 — parity with the AC-12 plan-hash gate)."""
    canonical = [
        {
            "key": key,
            "install_type": str(record.get("install_type", "")),
            "target": str(record.get("target", "")),
            "source": str(record.get("source", "")),
            "source_sha256": str(record.get("source_sha256", "")),
        }
        for key, record in sorted(candidates.items())
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_retire_preview(backup_root: Path, *, candidates_sha: str, live_home: bool) -> str:
    previews_dir = backup_root / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    run_id = _new_run_id()
    payload = {
        "kind": RETIRE_PREVIEW_KIND,
        "schema_version": 1,
        "run_id": run_id,
        "live_home": live_home,
        "candidates_sha256": candidates_sha,
    }
    _atomic_write_text_fsync(
        previews_dir / f"{run_id}.retire-preview.json", json.dumps(payload, sort_keys=True, indent=2) + "\n"
    )
    return run_id


def _load_retire_preview(backup_root: Path, preview_id: str) -> tuple[str, dict[str, Any]]:
    """Typed retire-preview load (AC-12 parity): ABSENT only via no-follow lstat
    FileNotFoundError; present-but-bad is INVALID (fail closed, never downgraded)."""
    path = backup_root / "previews" / f"{preview_id}.retire-preview.json"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return PREVIEW_ABSENT, {}
    except OSError:
        return PREVIEW_INVALID, {}
    if not stat.S_ISREG(st.st_mode):
        return PREVIEW_INVALID, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PREVIEW_INVALID, {}
    if (
        not isinstance(payload, dict)
        or payload.get("kind") != RETIRE_PREVIEW_KIND
        or payload.get("schema_version") != 1
        or payload.get("run_id") != preview_id
        or not _is_hex64(payload.get("candidates_sha256"))
    ):
        return PREVIEW_INVALID, {}
    return PREVIEW_OK, payload


def _retire(args: argparse.Namespace) -> int:
    """(AC-15) Retirement lifecycle: candidates = ledger (previously deployed) minus the current
    install index. Preview is report-only; apply removes ONLY targets still provably in their
    recorded controlled state (refuse-all-before-any-write on drift → manual review), backed up
    first, with optional negative smoke, and prunes the ledger. merge-template (settings) is
    ALWAYS report-only by default — stale keys are reported, never auto-pruned."""
    ledger_path = _resolve_ledger_path(args.catalog_root, getattr(args, "ledger", None))
    ledger, ledger_error = _load_ledger(ledger_path)
    if ledger_error is not None:
        print(f"retire refused: {ledger_error}")
        return 1

    home_root_path = _as_path(args.home_root)
    common_root_path = _as_path(args.common_root)
    master_root_path = _as_path(args.master_root)
    root_args = {"common_root": common_root_path, "home_root": home_root_path}
    try:
        layers = load_layers(
            dotclaude_root=master_root_path,
            layers_path=_as_path(args.layers_path) if getattr(args, "layers_path", None) is not None else master_root_path / "_layers.yaml",
        )
    except LayersError as exc:
        print(f"retire refused: _layers.yaml: {exc}")
        return 1
    live_home = bool(getattr(args, "live_home", False))
    if _is_live_home_root(home_root_path) and not live_home:
        print("retire refused: retiring from the real ~/.claude requires the explicit --live-home acknowledgement")
        return 1

    # (P12 MAJOR-1) A nonexistent/typo'd catalog root yields an EMPTY install index, which would
    # turn the ENTIRE ledger into retirement candidates — refuse loudly instead. An intentionally
    # emptied catalog still has its components directory.
    components_dir = _as_path(args.catalog_root) / "docs" / "_JarviSWARM" / "components"
    if not components_dir.is_dir():
        print(
            f"retire refused: catalog components directory not found at {components_dir}; a missing "
            f"catalog root must never convert the whole ledger into retirement candidates"
        )
        return 1
    artifacts, load_errors = _iter_install_artifacts(_as_path(args.catalog_root))
    if load_errors:
        for error in load_errors:
            print(f"retire refused: {error}")
        return 1
    current_keys: set[str] = set()
    for target_relpath, install in artifacts:
        if "scope" in install or "target_contexts" in install:
            print(f"retire refused: artifact {target_relpath} uses legacy install scope/target_contexts metadata")
            return 1
        context = str(install.get("context") or "")
        current_keys.add(_ledger_key(context, target_relpath))

    candidates = {}
    for key, record in sorted(ledger.get("targets", {}).items()):
        if not isinstance(record, dict):
            # MINOR-8b: a malformed record shape is corrupt state worth surfacing, not skipping.
            print(f"warning: ledger record {key!r} has a non-object shape and was ignored", file=sys.stderr)
            continue
        if key not in current_keys:
            candidates[key] = record

    apply_mode = getattr(args, "from_preview", None) is not None and not getattr(args, "write_preview", False)

    # (P12 MAJOR-4) The retire approval is CONTENT-BOUND: the preview records a canonical hash of
    # the candidate set; apply recomputes it and refuses a stale approval. A named-but-absent
    # preview id stays the explicit out-of-band token (AC-12 parity); present-but-corrupt or
    # hash-mismatched previews fail closed.
    candidates_sha = _retire_candidates_sha(candidates)
    backup_root = _resolve_backup_root(args.home_root)
    if apply_mode:
        preview_status, preview_payload = _load_retire_preview(backup_root, str(args.from_preview))
        if preview_status == PREVIEW_INVALID:
            print(
                f"retire refused: preview {args.from_preview!r} is present but unreadable/corrupt — "
                f"refusing rather than retiring without the gate"
            )
            return 1
        if preview_status == PREVIEW_ABSENT:
            print(
                f"warning: no stored retire preview {args.from_preview!r}; proceeding on the "
                f"out-of-band operator approval token",
                file=sys.stderr,
            )
        elif preview_payload.get("candidates_sha256") != candidates_sha:
            print(
                f"retire refused: the candidate set changed since preview {args.from_preview!r} — "
                f"re-run the retire preview and approve the current set (zero removals performed)"
            )
            return 1

    # Classify every candidate BEFORE any write (refuse-all-before-any-write).
    report: list[dict[str, Any]] = []
    removals: list[tuple[str, dict[str, Any], Path]] = []
    drifts: list[str] = []
    for key, record in candidates.items():
        relpath = str(record.get("target_relpath", ""))
        context = str(record.get("context", ""))
        install_type = str(record.get("install_type", ""))
        target = Path(str(record.get("target", "")))
        relpath_error = _validate_target_relpath(relpath)
        if relpath_error is not None:
            drifts.append(f"{relpath}: {relpath_error} — manual review required")
            continue

        if install_type == "merge-template":
            stale = _stale_settings_keys(record, target)
            report.append(
                {
                    "target_relpath": relpath,
                    "context": context,
                    "action": "report-only",
                    "detail": (
                        f"merge-template retirement is report-only by default; stale keys not "
                        f"auto-pruned: {', '.join(stale) if stale else '(none detected)'}"
                    ),
                }
            )
            continue

        try:
            declared_root = layers.target_root(
                context,
                root_args=root_args,
                include_disabled=bool(getattr(args, "include_disabled_layers", False)),
            )
        except LayersError as exc:
            drifts.append(f"{relpath}: {exc} — manual review required")
            continue
        declared = Path(_normpath(declared_root / relpath))
        if Path(_normpath(target)) != declared:
            drifts.append(
                f"{relpath}: ledger target {target} is not the declared {declared} — manual review required"
            )
            continue
        if not _live_context_user_parent_clean(declared_root, target):
            drifts.append(
                f"{relpath}: symlinked/escaping parent chain at {target.parent} — manual review required"
            )
            continue
        try:
            st = os.lstat(target)
        except FileNotFoundError:
            # already-absent: nothing to remove; the apply pass still prunes it from the ledger
            report.append(
                {"target_relpath": relpath, "context": context, "action": "already-absent", "detail": "nothing to remove"}
            )
            continue
        except OSError as exc:
            drifts.append(f"{relpath}: target unreadable ({exc}) — manual review required")
            continue
        if stat.S_ISLNK(st.st_mode):
            link_target = os.readlink(target)
            if install_type == "symlink" and str(link_target) == str(record.get("source", "")):
                removals.append((key, record, target))
                report.append({"target_relpath": relpath, "context": context, "action": "remove", "detail": "controlled symlink matches recorded master"})
            else:
                drifts.append(
                    f"{relpath}: live symlink points at {link_target!r}, not the recorded controlled "
                    f"master — manual review required"
                )
        elif stat.S_ISREG(st.st_mode):
            try:
                actual_sha = _sha256(target)
            except OSError as exc:
                drifts.append(f"{relpath}: target unreadable ({exc}) — manual review required")
                continue
            if install_type == "managed-copy" and actual_sha == str(record.get("source_sha256", "")):
                removals.append((key, record, target))
                report.append({"target_relpath": relpath, "context": context, "action": "remove", "detail": "controlled managed-copy sha matches recorded source"})
            else:
                drifts.append(
                    f"{relpath}: live bytes drifted from the recorded controlled state — manual review required"
                )
        else:
            drifts.append(f"{relpath}: unexpected live file type — manual review required")

    if not apply_mode:
        if getattr(args, "write_preview", False):
            preview_id = _write_retire_preview(
                backup_root, candidates_sha=candidates_sha, live_home=live_home
            )
            print(f"retire-preview id: {preview_id}")
        print(json.dumps({"retire_preview": report + [{"drift": d} for d in drifts]}, indent=2, sort_keys=True))
        return 0

    if drifts:
        for drift in drifts:
            print(f"retire refused: {drift}")
        print("retire refused: drifted candidates require manual review; no removal was performed")
        return 1

    removed_keys: list[str] = []
    if removals:
        entries: list[PlanEntry] = [
            _entry(
                target_relpath=str(record.get("target_relpath", "")),
                context=str(record.get("context", "")),
                install_type=str(record.get("install_type", "")),
                source=str(record.get("source", "")),
                target=target,
                action="retire",
                status="would-change",
                detail="retire candidate",
                rollback_hint="restore from retire backup manifest",
            )
            for _key, record, target in removals
        ]
        manifest_path, _by_key = _write_backup_manifest(
            backup_root=backup_root,
            run_id=_new_run_id(),
            home_root=home_root_path,
            common_root=common_root_path,
            mutating=entries,
        )
        for key, record, target in removals:
            # (P12 MAJOR-3) fd-anchored removal (dir_fd + basename, O_NOFOLLOW parent walk) —
            # never a bare path-string unlink that could re-resolve a swapped parent.
            context = str(record.get("context", ""))
            declared_root = layers.target_root(
                context,
                root_args=root_args,
                include_disabled=bool(getattr(args, "include_disabled_layers", False)),
            )
            _remove_target_live(declared_root, target)
            removed_keys.append(key)
        print(f"retire backup manifest: {manifest_path}")

        if getattr(args, "smoke", False):
            for key, record, target in removals:
                relpath = str(record.get("target_relpath", ""))
                if not run_negative_smoke(
                    surface=_surface_family(relpath), target_relpath=relpath, home_root=home_root_path
                ):
                    print(
                        f"retire failed: negative smoke for {relpath} did not confirm removal; "
                        f"restore from {manifest_path} if needed"
                    )
                    return 1

    # Prune retired + already-absent candidates from the ledger (atomic).
    targets_map = ledger.setdefault("targets", {})
    for key in removed_keys:
        targets_map.pop(key, None)
    for item in report:
        if item.get("action") == "already-absent":
            targets_map.pop(_ledger_key(str(item["context"]), str(item["target_relpath"])), None)
    _write_ledger_atomic(ledger_path, ledger)

    print(json.dumps({"retired": removed_keys, "report": report}, indent=2, sort_keys=True))
    return 0


def _stale_settings_keys(record: dict[str, Any], target: Path) -> list[str]:
    """Top-level keys the retired merge-template master deployed that are still present in the
    live settings target (report-only: candidates for an explicit, separately-policied prune)."""
    try:
        master_payload = json.loads(Path(str(record.get("source", ""))).read_text(encoding="utf-8"))
        target_payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(master_payload, dict) or not isinstance(target_payload, dict):
        return []
    return sorted(key for key in master_payload if key in target_payload)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("dry-run", "apply", "verify", "report", "retire"):
        subparser = subparsers.add_parser(command, help=f"{command} controlled-config deployment topology")
        subparser.add_argument("--master-root", required=True, type=Path, help="controlled master .claude root")
        subparser.add_argument(
            "--layers-path",
            type=Path,
            default=None,
            help="controlled-config layers registry path (default: <master-root>/_layers.yaml)",
        )
        subparser.add_argument(
            "--include-disabled-layers",
            action="store_true",
            help="diagnostics only: include disabled layer contexts in plan construction; apply refuses this flag",
        )
        subparser.add_argument("--common-root", required=True, type=Path, help="common checkout .claude deploy target root")
        subparser.add_argument("--home-root", required=True, type=Path, help="user-context .claude deploy target root")
        subparser.add_argument("--catalog-root", required=True, type=Path, help="repository root containing docs/_JarviSWARM/components")
        subparser.add_argument("--only-context", type=str, default=None, help="restrict the plan to ONE context's entries (e.g. user); other contexts are skipped (e.g. to deploy ~/.claude without touching common/.claude)")
        subparser.add_argument("--format", choices=("text", "json"), default="text", help="output format for plan/report commands")
        if command in ("dry-run", "apply", "retire", "verify"):
            subparser.add_argument(
                "--live-home",
                action="store_true",
                help=(
                    "explicit real-home live mode (AC-11): permit ONLY catalog-declared context user "
                    "targets at Path.home()/.claude/<target_relpath>; refuse every escape/alias "
                    "(accepted by verify for invocation parity; verify is read-only)"
                ),
            )
        if command in ("dry-run", "retire"):
            subparser.add_argument(
                "--write-preview",
                action="store_true",
                help=(
                    "(AC-12/15) persist or emit a preview; for retire this forces report-only "
                    "preview mode (candidates = ledger minus current catalog)"
                ),
            )
        if command in ("apply", "retire"):
            subparser.add_argument(
                "--from-preview",
                default=None,
                help=(
                    "(AC-12/15) operator-approved preview id; apply recomputes and hash-gates the "
                    "plan; retire uses it as the explicit mutate-mode acknowledgement"
                ),
            )
            subparser.add_argument(
                "--smoke",
                action="store_true",
                help=(
                    "(AC-13/14/15) run the runtime-honor smoke after a successful live apply "
                    "(per applied surface family) / the negative filesystem-gone proof after "
                    "retire removals (fresh-session negative probes are operator-driven)"
                ),
            )
            subparser.add_argument(
                "--ledger",
                type=Path,
                default=None,
                help=(
                    "(AC-15) live-deployment ledger path (default: env "
                    f"{LEDGER_PATH_ENV} or <catalog-root>/.jswarm/state/controlled-config/"
                    "dotclaude-live-state.json)"
                ),
            )
        if command == "apply":
            subparser.add_argument("--harness", action="store_true", help="allow apply against non-live fixture roots only")
    rollback = subparsers.add_parser("rollback", help="restore + verify a live apply from its backup manifest (AC-12)")
    rollback.add_argument("--manifest", required=True, type=Path, help="path to the schema-v1 backup manifest to roll back")
    rollback.add_argument(
        "--common-root",
        type=Path,
        default=None,
        help="trusted repo.common deploy root; required when the manifest contains repo.common records",
    )
    rollback.add_argument(
        "--live-home",
        action="store_true",
        help="explicit real-home live rollback acknowledgement (parity with apply --live-home)",
    )
    rollback.add_argument("--format", choices=("text", "json"), default="text", help="reserved; rollback emits a text status line")
    return parser


def _has_blocking_entry(plan: list[PlanEntry]) -> bool:
    return any(entry["status"] in {"error", "refused"} for entry in plan)


def _guard_apply(*, harness: bool, live_home: bool = False, common_root: Path, home_root: Path) -> str | None:
    live_dotclaude = Path.home() / ".claude"
    if live_home:
        # Explicit real-home live mode (AC-11). --live-home is NOT an interpretation of --harness:
        # it is the only mode that may write live targets, and the two are mutually exclusive.
        if harness:
            # MAJOR-2: refuse mode confusion — --harness + --live-home is never a weaker-of-two apply.
            return LIVE_APPLY_REFUSAL
        # BLOCKER-1: the live root must be a real directory, never a symlink (a symlinked ~/.claude
        # would redirect "declared" writes outside the declared subtree).
        if live_dotclaude.is_symlink():
            return LIVE_APPLY_REFUSAL
        # Valid ONLY when home_root IS the live ~/.claude; per-target declared-context user
        # classification + fd-anchored writes enforce the rest in the apply pass.
        if _normpath(home_root) != _normpath(live_dotclaude):
            return LIVE_APPLY_REFUSAL
        return None
    if not harness:
        return LIVE_APPLY_REFUSAL
    if _is_under_or_equal(common_root, live_dotclaude) or _is_under_or_equal(home_root, live_dotclaude):
        return LIVE_APPLY_REFUSAL
    return None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.common_root is not None:
        args.common_root = Path(os.path.abspath(args.common_root))

    if args.command == "rollback":
        # (AC-12) Restore + verify a prior live apply from its backup manifest. No plan build; the
        # manifest declares its own home_root and the captured before-state for every artifact. A
        # live-home manifest requires the explicit --live-home acknowledgement (MAJOR-2).
        return _rollback_from_manifest(
            args.manifest,
            live_home_ack=bool(getattr(args, "live_home", False)),
            common_root=args.common_root,
        )

    if getattr(args, "command", None) == "apply" and getattr(args, "include_disabled_layers", False):
        print("apply refused: --include-disabled-layers is diagnostics only")
        return 1

    if args.command == "retire":
        # (AC-15) Retirement lifecycle: ledger-driven candidates, report-only preview, drift →
        # manual review with NO removal, backup-before-remove, optional negative smoke.
        return _retire(args)

    if False:
        print("apply refused: --include-disabled-layers is diagnostics only")
        return 1

    if args.command == "verify":
        # Plan-based verify: consistent with apply by construction. Fail on any unreconciled
        # (non-noop) entry, using the same context->target + type semantics as build_plan. This
        # does NOT delegate to the AC-5 verify_dotclaude_topology (that models the full
        # post-inversion topology — common symlink universal — a COM-148 concern).
        plan = build_plan(
            master_root=args.master_root,
            common_root=args.common_root,
            home_root=args.home_root,
            catalog_root=args.catalog_root,
            layers_path=args.layers_path,
            include_disabled_layers=bool(getattr(args, "include_disabled_layers", False)),
        )
        verify_only_context = getattr(args, "only_context", None)
        if verify_only_context:
            plan = [e for e in plan if e.get("context") == verify_only_context]
        unreconciled = [entry for entry in plan if not (entry["action"] == "noop" and entry["status"] == "ok")]
        for entry in unreconciled:
            print(
                f"{entry['target_relpath']}: {entry['status']} {entry['action']} "
                f"at {entry['target']} — {entry['detail']}"
            )
        return 1 if unreconciled else 0

    live_home = bool(getattr(args, "live_home", False))

    if args.command == "apply":
        refusal = _guard_apply(
            harness=bool(args.harness),
            live_home=live_home,
            common_root=args.common_root,
            home_root=args.home_root,
        )
        if refusal is not None:
            print(refusal)
            return 1

    plan = build_plan(
        master_root=args.master_root,
        common_root=args.common_root,
        home_root=args.home_root,
        catalog_root=args.catalog_root,
        layers_path=args.layers_path,
        include_disabled_layers=bool(getattr(args, "include_disabled_layers", False)),
    )

    only_context = getattr(args, "only_context", None)
    if only_context:
        # Restrict to one context (keep error/refused entries so a malformed catalog still fails).
        plan = [e for e in plan if e.get("context") == only_context or e.get("status") in {"error", "refused"}]

    if args.command == "dry-run" and getattr(args, "write_preview", False):
        # (AC-12) Persist a canonical preview (plan + plan_sha256) for a later operator-approved
        # --from-preview apply. Lands under the backup root (env-overridable; defaults to a sibling
        # of the live .claude so a monkeypatched-home test never writes the real repo or home tree).
        _write_preview(_resolve_backup_root(args.home_root), plan, live_home=live_home, home_root=args.home_root)

    if args.command == "apply" and live_home:
        # (BLOCKER-R3-1) Preview-FIRST gate: a live apply may NEVER build-and-execute a fresh plan in
        # one step. Every `apply --live-home` MUST name a preview id via `--from-preview <id>` before
        # any declared-target guard, backup manifest, or mutation runs. (A named-but-ABSENT id remains
        # the explicit out-of-band approval token; omitting the id entirely is refused.)
        from_preview = getattr(args, "from_preview", None)
        if not from_preview:
            print(
                f"{LIVE_APPLY_REFUSAL}: apply --live-home requires --from-preview <id> "
                f"(preview-first gate, AC-12) — refusing a one-step build-and-apply"
            )
            return 1
        # Declared-target-only live guard (AC-11): refuse the WHOLE apply before any write if any
        # planned entry is not the exact catalog-declared context user target (or is otherwise
        # unsafe). Refuse-all-before-any-write; the in-helper guard is the defense-in-depth layer.
        for entry in plan:
            if entry["status"] in {"error", "refused"}:
                continue
            reason = _classify_live_target(entry, home_root=args.home_root, common_root=args.common_root)
            if reason is not None:
                print(reason)
                return 1
        if not _has_blocking_entry(plan):
            # No build_plan error/refused entry → safe to apply. (Mirrors _apply_plan's all-or-
            # nothing guard: a blocking entry means NO live writes; fall through, emit, return 1.)
            backup_root = _resolve_backup_root(args.home_root)
            # (AC-12) Preview plan-hash gate (BLOCKER-3 typed outcomes):
            #   * PREVIEW_OK    → the stored hash must equal the plan recomputed now (binding the
            #     source bytes too, BLOCKER-2), else the catalog/master changed underneath and the
            #     apply is refused as stale BEFORE any mutation.
            #   * PREVIEW_ABSENT → a named-but-absent preview is an out-of-band approval token;
            #     proceed with a stderr warning.
            #   * PREVIEW_INVALID → a present-but-corrupt preview is tamper/damage evidence; fail
            #     closed (never downgrade to "no gate").
            # from_preview is guaranteed non-empty by the preview-first gate above.
            status, stored = _load_preview(backup_root, from_preview, expected_live_home=live_home)
            if status == PREVIEW_INVALID:
                print(
                    f"{LIVE_APPLY_REFUSAL}: preview {from_preview!r} is present but "
                    f"unreadable/corrupt — refusing rather than applying without the gate"
                )
                return 1
            if status == PREVIEW_ABSENT:
                print(
                    f"warning: no stored preview {from_preview!r}; proceeding without plan-hash gate",
                    file=sys.stderr,
                )
            elif stored.get("plan_sha256") != _canonical_plan_sha256(plan):
                print(
                    f"{LIVE_APPLY_REFUSAL}: plan changed since preview {from_preview!r} — "
                    f"refusing stale apply"
                )
                return 1
            plan, rc, manifest_path = _apply_live_with_backup(
                plan, home_root=args.home_root, common_root=args.common_root, backup_root=backup_root
            )
            if rc != 0:
                # _apply_live_with_backup already printed the failure (and rolled back in reverse).
                return rc
            if manifest_path is not None:
                # MAJOR-5: post-apply verification. Rebuild the plan and prove it converged to
                # all-noop; if not, the live surface is not in the intended end-state, so roll back
                # from the just-written manifest and exit nonzero rather than report success.
                verify_plan = build_plan(
                    master_root=args.master_root,
                    common_root=args.common_root,
                    home_root=args.home_root,
                    catalog_root=args.catalog_root,
                    layers_path=args.layers_path,
                    include_disabled_layers=bool(getattr(args, "include_disabled_layers", False)),
                )
                if only_context:
                    # A scoped apply only deployed one context, so convergence is asserted over that
                    # context's entries only; other contexts' targets are intentionally untouched.
                    verify_plan = [e for e in verify_plan if e.get("context") == only_context]
                unreconciled = [
                    entry
                    for entry in verify_plan
                    if not (entry["action"] == "noop" and entry["status"] == "ok")
                ]
                if unreconciled:
                    roll_rc = _rollback_from_manifest(
                        manifest_path, live_home_ack=True, common_root=args.common_root
                    )
                    outcome = (
                        "rolled back from"
                        if roll_rc == 0
                        else "ROLLBACK INCOMPLETE — manual intervention required for"
                    )
                    print(
                        f"{LIVE_APPLY_REFUSAL}: post-apply verify did not converge to all-noop "
                        f"({len(unreconciled)} unreconciled entr"
                        f"{'y' if len(unreconciled) == 1 else 'ies'}); {outcome} {manifest_path}"
                    )
                    return 1
                # MAJOR-5: surface the exact one-command rollback handle for the operator.
                print(f"rollback command: {_rollback_command(manifest_path, common_root=args.common_root)}")
                applied_entries = [entry for entry in plan if entry["status"] == "applied"]
                if getattr(args, "smoke", False) and applied_entries:
                    # (AC-13/AC-14) Post-apply runtime-honor smoke, once per applied surface
                    # family. ANY failed smoke rolls the whole apply back and exits nonzero —
                    # an artifact is not "added" until the runtime demonstrably honors it.
                    families = sorted({_surface_family(str(e["target_relpath"])) for e in applied_entries})
                    for family in families:
                        # (P12 MAJOR-2) ANY raise out of the smoke seam (env-gate refusal,
                        # plan-build refusal, adapter crash) takes the SAME fail path as a
                        # returned False — otherwise the mutation persists live-but-not-added.
                        try:
                            smoke_ok = run_runtime_smoke(
                                surface=family, manifest_path=manifest_path, home_root=args.home_root
                            )
                            smoke_detail = "runtime-honor smoke failed"
                        except Exception as exc:  # noqa: BLE001 — fail-closed by contract
                            smoke_ok = False
                            smoke_detail = f"runtime-honor smoke raised {type(exc).__name__}: {exc}"
                        if not smoke_ok:
                            roll_rc = _rollback_from_manifest(
                                manifest_path, live_home_ack=True, common_root=args.common_root
                            )
                            outcome = (
                                "rolled back from"
                                if roll_rc == 0
                                else "ROLLBACK INCOMPLETE — manual intervention required for"
                            )
                            print(
                                f"{LIVE_APPLY_REFUSAL}: {smoke_detail} for surface "
                                f"{family!r}; {outcome} {manifest_path}"
                            )
                            return 1
                if applied_entries:
                    # (AC-15 required state) Record last successful deployment per target in the
                    # machine-local ledger — retirement candidates are computed from this.
                    _update_ledger_after_apply(
                        _resolve_ledger_path(args.catalog_root, getattr(args, "ledger", None)),
                        applied_entries,
                        home_root=args.home_root,
                        manifest_path=manifest_path,
                    )
    elif args.command == "apply":
        if getattr(args, "smoke", False) or getattr(args, "ledger", None):
            # MINOR-8a: these flags only act on the live branch; silent no-ops mislead operators.
            print("warning: --smoke/--ledger have no effect on a non-live apply", file=sys.stderr)
        # Temp-harness apply: refuse the WHOLE apply before any write if any planned target is
        # unsafe — it (or its parent) resolves into live ~/.claude through a symlinked path, or it
        # aliases a live inode through a hardlink. Refuse-all-before-any-write.
        for entry in plan:
            target_str = entry["target"]
            if target_str and _target_unsafe_for_apply(Path(target_str)):
                print(LIVE_APPLY_REFUSAL)
                return 1
        try:
            plan = _apply_plan(plan)
        except _LiveApplyRefused:
            print(LIVE_APPLY_REFUSAL)
            return 1

    _emit_plan(plan, args.format)

    if args.command == "apply":
        return 0 if all(entry["status"] in {"applied", "ok"} for entry in plan) else 1
    return 1 if _has_blocking_entry(plan) else 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main(sys.argv[1:]))
