from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

from jswarm.installer.preflight import (
    _candidate_python_interpreters,
    _repo_venv_python,
)

Runner = Callable[..., object]

_MIN_PYTHON = (3, 11)
_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s\\]+)(?:\s*\\)?$")
_SOURCE_REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?\s*(?:===|~=|==|!=|<=|>=|<|>)")
_VERSION_RE = re.compile(r"(?:Python\s+)?(\d+)\.(\d+)(?:\.(\d+))?")
_DIRECT_IMPORTS: dict[str, str | None] = {
    "httpx": "httpx",
    "Jinja2": "jinja2",
    "openpyxl": "openpyxl",
    "PyMuPDF": "fitz",
    "PyYAML": "yaml",
    "ruamel.yaml": "ruamel.yaml",
}
_DEV_IMPORTS: dict[str, str | None] = {"pytest": "pytest"}


class BootstrapError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DepSourceStatus:
    repo_root: str
    has_requirements: bool
    has_lock: bool
    dev: bool
    source_path: str | None
    lock_path: str | None
    present: bool


@dataclass(frozen=True)
class VenvIdentity:
    path: str
    exists: bool
    is_venv: bool
    prefix_under_repo: bool
    version: str | None
    supported: bool
    valid: bool


@dataclass(frozen=True)
class LockSourceConsistency:
    consistent: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class BootstrapReport:
    repo_root: str
    action: str
    venv_present: bool
    venv_python: str | None
    interpreter_selected: str | None
    dep_source: DepSourceStatus
    deps_installed: bool
    healthy: bool
    mutated: bool
    steps: tuple[dict[str, str], ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "repo_root": self.repo_root,
            "action": self.action,
            "venv_present": self.venv_present,
            "venv_python": self.venv_python,
            "interpreter_selected": self.interpreter_selected,
            "dep_source": asdict(self.dep_source),
            "deps_installed": self.deps_installed,
            "healthy": self.healthy,
            "mutated": self.mutated,
            "steps": [dict(step) for step in self.steps],
        }


def _default_runner(cmd: Sequence[str], *, cwd: Path | None = None, timeout: float | None = None) -> object:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _step(step: str, status: str, detail: str) -> dict[str, str]:
    return {"step": step, "status": status, "detail": detail}


def _as_repo_root(repo_root: Path) -> Path:
    return Path(repo_root).resolve()


def _dep_paths(repo_root: Path, *, dev: bool) -> tuple[Path, Path]:
    if dev:
        return repo_root / "requirements-dev.txt", repo_root / "requirements-dev.lock"
    return repo_root / "requirements.txt", repo_root / "requirements.lock"


def dep_source_status(repo_root: Path, *, dev: bool = False) -> DepSourceStatus:
    root = _as_repo_root(repo_root)
    source_path, lock_path = _dep_paths(root, dev=dev)
    has_requirements = source_path.is_file()
    has_lock = lock_path.is_file()
    return DepSourceStatus(
        repo_root=str(root),
        has_requirements=has_requirements,
        has_lock=has_lock,
        dev=dev,
        source_path=str(source_path) if has_requirements else None,
        lock_path=str(lock_path) if has_lock else None,
        present=has_requirements and has_lock,
    )


def _venv_python(repo_root: Path) -> Path:
    return _repo_venv_python(repo_root)


def _normalize_dist_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _parse_version(value: object) -> tuple[int, int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            major = int(value[0])
            minor = int(value[1])
            patch = int(value[2]) if len(value) >= 3 else 0
        except (TypeError, ValueError):
            return None
        return major, minor, patch
    match = _VERSION_RE.search(str(value))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _format_version(version: tuple[int, int, int] | None) -> str | None:
    if version is None:
        return None
    return f"{version[0]}.{version[1]}.{version[2]}"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def venv_identity(repo_root: Path, *, runner: Runner | None = None) -> VenvIdentity:
    root = _as_repo_root(repo_root)
    venv_python = _venv_python(root)
    exists = venv_python.is_file()
    if not exists:
        return VenvIdentity(str(venv_python), False, False, False, None, False, False)

    active_runner = runner or _default_runner
    cfg_present = (root / ".venv" / "pyvenv.cfg").is_file()
    probe_script = """
import json
import sys
print(json.dumps({"prefix": sys.prefix, "base_prefix": getattr(sys, "base_prefix", sys.prefix), "version": list(sys.version_info[:3])}))
""".strip()
    prefix: Path | None = None
    base_prefix: Path | None = None
    version: tuple[int, int, int] | None = None
    try:
        probe = _run(active_runner, (str(venv_python), "-c", probe_script), cwd=root, timeout=10)
        if _returncode(probe) == 0:
            payload = json.loads(_stdout(probe).strip())
            prefix = Path(str(payload.get("prefix", ""))).resolve(strict=False)
            base_prefix = Path(str(payload.get("base_prefix", ""))).resolve(strict=False)
            version = _parse_version(payload.get("version"))
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, json.JSONDecodeError):
        prefix = None
        base_prefix = None
        version = None

    expected_prefix = (root / ".venv").resolve(strict=False)
    is_venv = bool(cfg_present and prefix is not None and base_prefix is not None and prefix != base_prefix)
    prefix_under_repo = bool(prefix is not None and _is_relative_to(prefix, expected_prefix))
    supported = version is not None and version >= (_MIN_PYTHON[0], _MIN_PYTHON[1], 0)
    valid = exists and is_venv and prefix_under_repo and supported
    return VenvIdentity(
        path=str(venv_python),
        exists=exists,
        is_venv=is_venv,
        prefix_under_repo=prefix_under_repo,
        version=_format_version(version),
        supported=supported,
        valid=valid,
    )


def _source_requirements(source_path: Path) -> dict[str, str]:
    requirements: dict[str, str] = {}
    if not source_path.is_file():
        return requirements
    for raw_line in source_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _SOURCE_REQ_RE.match(line)
        if match:
            requirements[_normalize_dist_name(match.group(1))] = raw_line
    return requirements


def _locked_versions(lock_path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    if not lock_path.is_file():
        return versions
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-r ") or line.startswith("-c ") or line.startswith("--hash="):
            continue
        match = _PIN_RE.match(line)
        if match:
            name = _normalize_dist_name(match.group(1))
            versions[name] = match.group(2).rstrip("\\")
    return versions


def lock_source_consistency(repo_root: Path, *, dev: bool) -> LockSourceConsistency:
    root = _as_repo_root(repo_root)
    source_path, lock_path = _dep_paths(root, dev=dev)
    reasons: list[str] = []
    source_requirements = _source_requirements(source_path)
    pinned: dict[str, str] = {}

    if lock_path.is_file():
        for line_number, raw_line in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-r ") or line.startswith("-c ") or line.startswith("--hash="):
                continue
            match = _PIN_RE.match(line)
            if match:
                pinned[_normalize_dist_name(match.group(1))] = match.group(2).rstrip("\\")
                continue
            reasons.append(f"malformed or unpinned lock line {line_number}: {raw_line}")

    if source_requirements and not pinned:
        reasons.append("lock has zero pinned distributions while dependency source has requirements")

    missing = sorted(name for name in source_requirements if name not in pinned)
    if missing:
        reasons.append("direct source requirements missing from lock: " + ", ".join(missing))

    return LockSourceConsistency(consistent=not reasons, reasons=tuple(reasons))


def _lock_has_hashes(lock_path: Path) -> bool:
    if not lock_path.is_file():
        return False
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        if "--hash=" in raw_line:
            return True
    return False


def _direct_import_map(*, dev: bool) -> dict[str, str | None]:
    imports = dict(_DIRECT_IMPORTS)
    if dev:
        imports.update(_DEV_IMPORTS)
    return imports


def _run(runner: Runner, cmd: Sequence[str], *, cwd: Path | None = None, timeout: float | None = None) -> object:
    return runner(tuple(str(part) for part in cmd), cwd=cwd, timeout=timeout)


def _returncode(result: object) -> int:
    return int(getattr(result, "returncode", 1))


def _stdout(result: object) -> str:
    return str(getattr(result, "stdout", "") or "")


def _stderr(result: object) -> str:
    return str(getattr(result, "stderr", "") or "")


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_state_marker(repo_root: Path, *, dev: bool) -> None:
    venv_python = _venv_python(repo_root)
    marker = venv_python.parent.parent / "jarviswarm-bootstrap.json"
    _, lock_path = _dep_paths(repo_root, dev=dev)
    state = {
        "python": str(venv_python),
        "dev": dev,
        "lock_path": str(lock_path),
        "lock_sha256": _hash_file(lock_path),
    }
    marker.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _verify_with_steps(repo_root: Path, *, dev: bool, runner: Runner) -> tuple[bool, tuple[dict[str, str], ...]]:
    steps: list[dict[str, str]] = []
    venv_python = _venv_python(repo_root)
    status = dep_source_status(repo_root, dev=dev)
    if not status.present:
        steps.append(_step("dep-source", "fail", "requirements source and lock are required"))
        return False, tuple(steps)

    identity = venv_identity(repo_root, runner=runner)
    if not identity.valid:
        detail = f"invalid venv identity at {venv_python}"
        if identity.exists and not identity.is_venv:
            detail = "venv python exists but is not a real virtual environment"
        elif identity.exists and not identity.prefix_under_repo:
            detail = "venv prefix is outside the repository .venv"
        elif identity.exists and not identity.supported:
            detail = "venv Python must be 3.11 or newer"
        steps.append(_step("venv", "fail", detail))
        return False, tuple(steps)
    steps.append(_step("venv", "ok", f"{identity.path} ({identity.version})"))

    consistency = lock_source_consistency(repo_root, dev=dev)
    if not consistency.consistent:
        steps.append(_step("lock-source", "fail", "; ".join(consistency.reasons)))
        return False, tuple(steps)
    steps.append(_step("lock-source", "ok", "dependency source and lock are consistent"))

    _, lock_path = _dep_paths(repo_root, dev=dev)
    locked = _locked_versions(lock_path)
    relevant_imports = {
        name: import_name for name, import_name in _direct_import_map(dev=dev).items() if _normalize_dist_name(name) in locked
    }
    probe_script = """
import importlib
import importlib.metadata
import json
import sys
locked = json.loads(sys.argv[1])
imports = json.loads(sys.argv[2])
missing = []
for name, expected in locked.items():
    try:
        installed = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        missing.append(f"{name}: missing")
        continue
    if installed != expected:
        missing.append(f"{name}: {installed} != {expected}")
for dist_name, import_name in imports.items():
    if import_name is None:
        continue
    try:
        importlib.import_module(import_name)
    except Exception as exc:
        missing.append(f"{dist_name}: import {import_name} failed: {exc}")
if missing:
    print("; ".join(missing))
    raise SystemExit(1)
print("locked distributions ok")
""".strip()
    probe = _run(
        runner,
        (
            str(venv_python),
            "-c",
            probe_script,
            json.dumps(locked, sort_keys=True),
            json.dumps(relevant_imports, sort_keys=True),
        ),
        cwd=repo_root,
        timeout=30,
    )
    if _returncode(probe) != 0:
        detail = (_stdout(probe) or _stderr(probe)).strip() or "locked dependency probe failed"
        steps.append(_step("deps", "fail", detail))
        return False, tuple(steps)
    steps.append(_step("deps", "ok", _stdout(probe).strip() or "locked distributions ok"))

    pip_check = _run(runner, (str(venv_python), "-m", "pip", "check"), cwd=repo_root, timeout=30)
    if _returncode(pip_check) != 0:
        detail = (_stdout(pip_check) or _stderr(pip_check)).strip() or "pip check failed"
        steps.append(_step("pip-check", "fail", detail))
        return False, tuple(steps)
    steps.append(_step("pip-check", "ok", (_stdout(pip_check) or _stderr(pip_check)).strip() or "pip check ok"))
    return True, tuple(steps)


def select_host_interpreter(candidates: list[dict[str, object]]) -> str | None:
    usable: list[tuple[tuple[int, int, int], str]] = []
    for candidate in candidates:
        if not candidate.get("can_create_venv") or candidate.get("quarantined"):
            continue
        path = candidate.get("path")
        if not path:
            continue
        version = _parse_version(candidate.get("version"))
        if version is None or version < (_MIN_PYTHON[0], _MIN_PYTHON[1], 0):
            continue
        usable.append((version, str(path)))
    if not usable:
        return None
    return sorted(usable, key=lambda item: (-item[0][0], -item[0][1], -item[0][2], item[1]))[0][1]


def _select_host_interpreter() -> str:
    # Deterministic host selection only: the highest supported, non-quarantined
    # candidate on the host. The caller's current interpreter (sys.executable)
    # MUST NOT override the selected host candidate, so venv creation stays
    # reproducible regardless of which environment invoked the bootstrap.
    selected = select_host_interpreter(_candidate_python_interpreters())
    if not selected:
        raise BootstrapError("NO_INTERPRETER", "No supported Python interpreter is available to create .venv")
    return selected


def check_bootstrap(repo_root: Path, *, dev: bool = False, runner: Runner | None = None) -> BootstrapReport:
    root = _as_repo_root(repo_root)
    active_runner = runner or _default_runner
    status = dep_source_status(root, dev=dev)
    identity = venv_identity(root, runner=active_runner)
    consistency = lock_source_consistency(root, dev=dev)
    steps: list[dict[str, str]] = [
        _step("dep-source", "ok" if status.present else "fail", "dependency source present" if status.present else "dependency source missing"),
        _step("venv", "ok" if identity.valid else "fail", str(_venv_python(root))),
        _step("lock-source", "ok" if consistency.consistent else "fail", "dependency source and lock are consistent" if consistency.consistent else "; ".join(consistency.reasons)),
    ]
    deps_installed = False
    if identity.valid and status.present and consistency.consistent:
        deps_installed, verify_steps = _verify_with_steps(root, dev=dev, runner=active_runner)
        steps.extend(verify_steps)
    else:
        steps.append(_step("deps", "skip", "dependency verification requires valid venv and consistent lock"))
    return BootstrapReport(
        repo_root=str(root),
        action="check",
        venv_present=identity.valid,
        venv_python=identity.path if identity.valid else None,
        interpreter_selected=None,
        dep_source=status,
        deps_installed=deps_installed,
        healthy=identity.valid and status.present and consistency.consistent and deps_installed,
        mutated=False,
        steps=tuple(steps),
    )


def _remove_repo_venv(repo_root: Path) -> None:
    venv_dir = repo_root / ".venv"
    if not venv_dir.exists() and not venv_dir.is_symlink():
        return
    if venv_dir.is_symlink() or venv_dir.is_file():
        venv_dir.unlink()
        return
    if venv_dir.resolve(strict=False) != venv_dir:
        raise BootstrapError("UNSAFE_VENV_PATH", f"Refusing to remove non-local venv path: {venv_dir}")
    shutil.rmtree(venv_dir)


def repair_bootstrap(
    repo_root: Path,
    *,
    dev: bool = False,
    runner: Runner | None = None,
    allow_create: bool = True,
    replace_invalid: bool = False,
) -> BootstrapReport:
    root = _as_repo_root(repo_root)
    active_runner = runner or _default_runner
    status = dep_source_status(root, dev=dev)
    venv_python = _venv_python(root)
    identity = venv_identity(root, runner=active_runner)
    consistency = lock_source_consistency(root, dev=dev)
    steps: list[dict[str, str]] = []
    if not status.present:
        steps.append(_step("dep-source", "fail", "dependency source missing"))
        return BootstrapReport(str(root), "repair", identity.valid, identity.path if identity.valid else None, None, status, False, False, False, tuple(steps))
    if not consistency.consistent:
        steps.append(_step("lock-source", "fail", "; ".join(consistency.reasons)))
        return BootstrapReport(str(root), "repair", identity.valid, identity.path if identity.valid else None, None, status, False, False, False, tuple(steps))

    selected: str | None = None
    mutated = False
    created_venv = False
    invalid_existing = identity.exists and not identity.valid
    if invalid_existing and not replace_invalid:
        steps.append(_step("venv", "fail", "existing .venv is invalid; rerun with replace_invalid to recreate it"))
        return BootstrapReport(str(root), "repair", False, None, None, status, False, False, False, tuple(steps))
    if (not identity.valid and not identity.exists) or (invalid_existing and replace_invalid):
        if not allow_create:
            steps.append(_step("venv-create", "skip", "venv creation disabled"))
            return BootstrapReport(str(root), "repair", False, None, None, status, False, False, False, tuple(steps))
        if invalid_existing:
            _remove_repo_venv(root)
            mutated = True
            steps.append(_step("venv-replace", "ok", str(root / ".venv")))
        selected = _select_host_interpreter()
        create = _run(active_runner, (selected, "-m", "venv", str(root / ".venv")), cwd=root, timeout=120)
        if _returncode(create) != 0:
            detail = (_stdout(create) or _stderr(create)).strip() or "venv creation failed"
            steps.append(_step("venv-create", "fail", detail))
            now_exists = venv_python.is_file()
            return BootstrapReport(str(root), "repair", False, str(venv_python) if now_exists else None, selected, status, False, False, mutated, tuple(steps))
        mutated = True
        created_venv = True
        steps.append(_step("venv-create", "ok", str(root / ".venv")))

    if created_venv:
        deps_installed = False
        verify_steps: tuple[dict[str, str], ...] = ()
    else:
        deps_installed, verify_steps = _verify_with_steps(root, dev=dev, runner=active_runner)
    steps.extend(verify_steps)
    if not deps_installed:
        _, lock_path = _dep_paths(root, dev=dev)
        install_cmd: tuple[str, ...]
        if _lock_has_hashes(lock_path):
            install_cmd = (str(venv_python), "-m", "pip", "install", "--require-hashes", "-r", str(lock_path))
        else:
            install_cmd = (str(venv_python), "-m", "pip", "install", "-r", str(lock_path))
        install = _run(active_runner, install_cmd, cwd=root, timeout=300)
        if _returncode(install) != 0:
            detail = (_stdout(install) or _stderr(install)).strip() or "pip install failed"
            steps.append(_step("pip-install", "fail", detail))
            return BootstrapReport(str(root), "repair", venv_python.is_file(), str(venv_python) if venv_python.is_file() else None, selected, status, False, False, True, tuple(steps))
        mutated = True
        steps.append(_step("pip-install", "ok", str(lock_path)))
        deps_installed, verify_steps = _verify_with_steps(root, dev=dev, runner=active_runner)
        steps.extend(verify_steps)

    final_identity = venv_identity(root, runner=active_runner)
    healthy = final_identity.valid and status.present and consistency.consistent and deps_installed
    if healthy and mutated:
        try:
            _write_state_marker(root, dev=dev)
            steps.append(_step("state-marker", "ok", "bootstrap state marker written"))
        except OSError as exc:
            steps.append(_step("state-marker", "warn", str(exc)))
    return BootstrapReport(
        repo_root=str(root),
        action="repair",
        venv_present=final_identity.valid,
        venv_python=final_identity.path if final_identity.valid else None,
        interpreter_selected=selected,
        dep_source=status,
        deps_installed=deps_installed,
        healthy=healthy,
        mutated=mutated,
        steps=tuple(steps),
    )


def verify_env(repo_root: Path, *, dev: bool = False, runner: Runner | None = None) -> BootstrapReport:
    root = _as_repo_root(repo_root)
    active_runner = runner or _default_runner
    status = dep_source_status(root, dev=dev)
    identity = venv_identity(root, runner=active_runner)
    consistency = lock_source_consistency(root, dev=dev)
    deps_installed, steps = _verify_with_steps(root, dev=dev, runner=active_runner)
    healthy = identity.valid and status.present and consistency.consistent and deps_installed
    return BootstrapReport(
        repo_root=str(root),
        action="check",
        venv_present=identity.valid,
        venv_python=identity.path if identity.valid else None,
        interpreter_selected=None,
        dep_source=status,
        deps_installed=deps_installed,
        healthy=healthy,
        mutated=False,
        steps=steps,
    )
