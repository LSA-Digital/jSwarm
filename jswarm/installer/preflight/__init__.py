"""Layer-0 environment preflight for JarviSWARM onboarding."""

from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jswarm.compliance.sanitizer import contains_sensitive, sanitize
from jswarm.platform import Platform, current as _current_platform

_REQUIRED_PORTS = (9100, 9101)
_SKIPPED_DOCKER_CAPABILITIES = ("stack start", "health check", "Compose validation", "Docker cleanup")
# macOS is the only supported platform (v0.1.0). An unsupported platform's
# guidance always comes from `Platform.unsupported_message()` instead -- see
# `_guidance` below.
_MACOS_GUIDANCE: dict[str, str] = {
    "python": "Install a current Python 3.11+ runtime with your macOS package manager, then create the repository .venv.",
    "docker": "Install Docker Desktop for macOS and confirm Docker Compose v2 with docker compose version.",
    "git": "Install Git with Xcode Command Line Tools or your macOS package manager.",
}


@dataclass(frozen=True)
class PreflightError:
    code: str
    message: str
    guidance: str

    def to_json_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "guidance": self.guidance}


@dataclass(frozen=True)
class PreflightReport:
    schema_version: str
    operation: str
    status: str
    mode: str
    platform: dict[str, str]
    checks: dict[str, dict[str, object]]
    reduced_mode: dict[str, object]
    errors: tuple[PreflightError, ...]
    host_mutation: dict[str, bool]
    layer1_doctor: dict[str, object]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "status": self.status,
            "mode": self.mode,
            "platform": dict(self.platform),
            "checks": {
                "docker": dict(self.checks["docker"]),
                "python": dict(self.checks["python"]),
                "git": dict(self.checks["git"]),
                "ports": dict(self.checks["ports"]),
            },
            "reduced_mode": {
                "enabled": bool(self.reduced_mode["enabled"]),
                "skipped_capabilities": list(self.reduced_mode["skipped_capabilities"]),
                "enable_full_mode_steps": list(self.reduced_mode["enable_full_mode_steps"]),
            },
            "errors": [error.to_json_dict() for error in self.errors],
            "host_mutation": dict(self.host_mutation),
            "layer1_doctor": dict(self.layer1_doctor),
        }


def _guidance(platform: Platform, topic: str) -> str:
    if not platform.is_supported():
        return platform.unsupported_message()
    return _MACOS_GUIDANCE[topic]


def _default_probes(repo_root: Path) -> SimpleNamespace:
    docker_path = shutil.which("docker")
    git_path = shutil.which("git")
    venv_python = _repo_venv_python(repo_root)
    selected_venv_python = str(venv_python) if venv_python.exists() else None
    candidates = _candidate_python_interpreters()
    selected_interpreter = selected_venv_python or _select_interpreter(candidates)
    return SimpleNamespace(
        docker_available=docker_path is not None,
        docker_version=_read_version(("docker", "--version")) if docker_path is not None else None,
        compose_v2_available=_docker_compose_v2_available() if docker_path is not None else False,
        compose_version=_read_version(("docker", "compose", "version")) if docker_path is not None else None,
        git_available=git_path is not None,
        git_version=_read_version(("git", "--version")) if git_path is not None else None,
        candidate_python_interpreters=candidates,
        selected_interpreter=selected_interpreter,
        selected_venv_python=selected_venv_python,
        venv_python=selected_venv_python,
        ports=_probe_ports(),
    )


def _repo_venv_python(repo_root: Path) -> Path:
    return repo_root / ".venv" / "bin" / "python"


def _read_version(command: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    return output or None


def _docker_compose_v2_available() -> bool:
    try:
        result = subprocess.run(("docker", "compose", "version"), check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _candidate_python_interpreters() -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    for name in ("python3.13", "python3.12", "python3.11", "python3", "python"):
        path = shutil.which(name)
        if path is None or path in seen:
            continue
        seen.add(path)
        version = _read_version((path, "--version")) or "unknown"
        candidates.append(
            {
                "path": path,
                "version": version.replace("Python ", "", 1),
                "can_create_venv": True,
                "quarantined": Path(path).as_posix() == "/usr/bin/python3" and version.startswith("Python 3.9.6"),
            }
        )
    return candidates


def _select_interpreter(candidates: list[dict[str, object]]) -> str | None:
    for candidate in candidates:
        if candidate.get("can_create_venv") and not candidate.get("quarantined"):
            return str(candidate.get("path"))
    return None


def _probe_ports() -> dict[int, str]:
    return {port: _probe_port(port) for port in _REQUIRED_PORTS}


def _probe_port(port: int) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        try:
            connected = probe.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return "unknown"
    if connected:
        return "in_use"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as binder:
        binder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            binder.bind(("127.0.0.1", port))
        except OSError:
            return "in_use"
    return "free"


def _layer1_doctor_available() -> bool:
    return importlib.util.find_spec("jswarm.installer.jconfig") is not None


def run_preflight(*, repo_root: Path, probes: SimpleNamespace | None = None, request_full: bool = True) -> PreflightReport:
    """Build a deterministic, host-read-only Layer-0 preflight report."""

    active_probes = probes if probes is not None else _default_probes(repo_root)
    platform = _current_platform()
    platform_info = {
        "classifier": "jswarm.platform",
        "platform_key": platform.name,
        "guidance_source": f"jswarm.platform.current() selected {platform.name}",
    }

    errors: list[PreflightError] = []
    docker_available = bool(getattr(active_probes, "docker_available", False))
    compose_available = bool(getattr(active_probes, "compose_v2_available", False)) if docker_available else False
    docker_status = "pass" if docker_available else "missing"
    compose_status = "pass" if compose_available else ("missing" if docker_available else "skipped")
    if not docker_available:
        errors.append(
            PreflightError(
                "DOCKER_UNAVAILABLE",
                "Docker is not available, so Docker-backed demo stack operations will run in reduced mode.",
                f"{platform.name}: {_guidance(platform, 'docker')}",
            )
        )
    elif not compose_available:
        errors.append(
            PreflightError(
                "COMPOSE_V2_UNAVAILABLE",
                "Docker Compose v2 is not available, so Compose-backed stack operations will run in reduced mode.",
                f"{platform.name}: Install or enable Docker Compose v2, then verify with docker compose version.",
            )
        )

    candidates = list(getattr(active_probes, "candidate_python_interpreters", []) or [])
    selected_interpreter = getattr(active_probes, "selected_interpreter", None)
    selected_venv_python = getattr(active_probes, "selected_venv_python", None)
    venv_python = getattr(active_probes, "venv_python", selected_venv_python)
    usable_candidates = [c for c in candidates if c.get("can_create_venv") and not c.get("quarantined")]
    quarantined_candidates = [c for c in candidates if c.get("quarantined")]
    if venv_python:
        python_status = "pass"
    elif candidates and quarantined_candidates and not usable_candidates:
        python_status = "quarantined"
        errors.append(
            PreflightError(
                "PYTHON_QUARANTINED",
                "Only quarantined Python candidates were found for the runtime path.",
                f"{platform.name}: {_guidance(platform, 'python')} Do not use the quarantined system Python; create .venv with a supported interpreter.",
            )
        )
    elif not usable_candidates:
        python_status = "missing"
        errors.append(
            PreflightError(
                "PYTHON_UNAVAILABLE",
                "No Python candidate can create the repository virtual environment.",
                f"{platform.name}: {_guidance(platform, 'python')}",
            )
        )
    else:
        python_status = "missing"
        errors.append(
            PreflightError(
                "PYTHON_UNAVAILABLE",
                "The repository .venv Python is not available yet.",
                f"{platform.name}: Create the repository-local .venv with {selected_interpreter or 'a supported Python interpreter'}; preflight did not create it automatically.",
            )
        )

    git_available = bool(getattr(active_probes, "git_available", False))
    git_status = "pass" if git_available else "missing"
    if not git_available:
        errors.append(
            PreflightError(
                "GIT_UNAVAILABLE",
                "Git is not available, so git-baseline operations are degraded or blocked.",
                f"{platform.name}: {_guidance(platform, 'git')}",
            )
        )

    probe_ports = getattr(active_probes, "ports", {}) or {}
    ports = {str(port): str(probe_ports.get(port, probe_ports.get(str(port), "unknown"))) for port in _REQUIRED_PORTS}
    for port, state in list(ports.items()):
        if state not in {"free", "in_use", "unknown"}:
            ports[port] = "unknown"
    in_use_ports = [port for port, state in ports.items() if state == "in_use"]
    if in_use_ports:
        errors.append(
            PreflightError(
                "PORT_IN_USE",
                "One or more default demo ports are already in use.",
                "Free ports 9100 and 9101 before stack start, or provide explicit port overrides for the demo UI/API.",
            )
        )
    unknown_ports = [port for port, state in ports.items() if state == "unknown"]
    if unknown_ports:
        errors.append(
            PreflightError(
                "PORT_UNKNOWN",
                "One or more default demo ports could not be proven free.",
                "Check ports 9100 and 9101 before starting the demo stack, or provide explicit port overrides.",
            )
        )

    reduced_enabled = (not request_full) or bool(errors) or not docker_available or not compose_available or python_status != "pass"
    skipped: list[str] = []
    enable_steps: list[str] = []
    if not docker_available or not compose_available:
        skipped.extend(_SKIPPED_DOCKER_CAPABILITIES)
        enable_steps.append(f"Install Docker and Docker Compose v2 for {platform.name}, then re-run preflight.")
    if python_status != "pass":
        skipped.extend(["Layer 1 doctor", "venv-backed CLI commands", "demo deploy", "tour engine"])
        enable_steps.append("Install a supported Python runtime and create the repository-local .venv before enabling full mode.")
    if not git_available:
        skipped.extend(["git baseline", "baseline commit", "git smoke checks"])
        enable_steps.append("Install Git and confirm git --version before git-baseline operations.")
    if in_use_ports or unknown_ports:
        skipped.extend(["demo UI/API port binding", "stack health"])
        enable_steps.append("Free ports 9100 and 9101 or provide demo port overrides before stack start.")
    skipped = list(dict.fromkeys(skipped))
    enable_steps = list(dict.fromkeys(enable_steps))

    blocking_codes = {"PYTHON_QUARANTINED", "PYTHON_UNAVAILABLE"}
    if request_full and any(error.code in blocking_codes for error in errors) and not docker_available:
        status = "degraded"
    elif request_full and any(error.code in blocking_codes for error in errors):
        status = "blocked"
    elif errors:
        status = "degraded"
    else:
        status = "ready"
    mode = "reduced" if reduced_enabled else "full"

    return PreflightReport(
        schema_version="1",
        operation="environment_preflight",
        status=status,
        mode=mode,
        platform=platform_info,
        checks={
            "docker": {"status": docker_status, "compose_v2": compose_status},
            "python": {
                "status": python_status,
                "selected_interpreter": str(selected_interpreter) if selected_interpreter is not None else None,
                "venv_python": str(venv_python) if venv_python is not None else None,
            },
            "git": {"status": git_status},
            "ports": ports,
        },
        reduced_mode={
            "enabled": mode == "reduced",
            "skipped_capabilities": skipped,
            "enable_full_mode_steps": enable_steps,
        },
        errors=tuple(errors),
        host_mutation={
            "system_packages_auto_installed": False,
            "host_config_changed": False,
            "repo_local_venv_changed": False,
        },
        layer1_doctor={"available": _layer1_doctor_available(), "invoked": False, "gate": "G-JCONFIG-DOCTOR"},
    )


def render_text(report: PreflightReport) -> str:
    data = report.to_json_dict()
    lines = [
        "JarviSWARM Environment Preflight",
        f"Platform: {data['platform']['platform_key']} ({data['platform']['classifier']}; {data['platform']['guidance_source']})",
        f"Mode: {data['mode']}",
        f"Readiness: {data['status']}",
        "Prerequisite checks",
        f"- Docker: {data['checks']['docker']['status']} (Compose v2: {data['checks']['docker']['compose_v2']})",
        f"- Python: {data['checks']['python']['status']} (interpreter: {data['checks']['python']['selected_interpreter']}; venv: {data['checks']['python']['venv_python']})",
        f"- Git: {data['checks']['git']['status']}",
        f"- Ports: 9100={data['checks']['ports']['9100']}; 9101={data['checks']['ports']['9101']}",
        "Missing prerequisites",
    ]
    if report.errors:
        for error in report.errors:
            lines.append(f"- {error.code}: {error.message} Guidance: {error.guidance}")
    else:
        lines.append("- None")
    lines.extend(
        [
            "Reduced mode",
            f"- Enabled: {data['reduced_mode']['enabled']}",
            "- Skipped capabilities: " + (", ".join(data["reduced_mode"]["skipped_capabilities"]) or "none"),
            "Next steps",
        ]
    )
    steps = data["reduced_mode"]["enable_full_mode_steps"]
    if steps:
        for step in steps:
            lines.append(f"- {step}")
    else:
        lines.append("- Full mode is ready; continue with the requested onboarding or demo flow.")
    lines.extend(
        [
            "- Preflight did not auto-install host system packages, did not change host configuration, and did not start Docker.",
            "JSON report:",
            json.dumps(data, indent=2, sort_keys=True),
        ]
    )
    return "\n".join(lines) + "\n"


def prerequisite_docs_text() -> str:
    text = """
# JarviSWARM Environment Prerequisites

JarviSWARM can run a Layer-0 environment preflight before onboarding, demo deploy, or tour commands. The preflight is read-only for host prerequisites: it checks what is available, reports reduced-mode behavior when something is missing, and does not install host packages or start services.

## Required tools for full mode

- Python 3.11 or newer with venv support. Create the repository-local virtual environment at `.venv` before running venv-backed commands.
- Docker Engine or Docker Desktop with Docker Compose v2. Full demo stack start, health checks, Compose validation, and Docker cleanup require Docker and `docker compose`.
- Git for repository baseline and smoke-check operations.
- Default demo ports 9100 and 9101 available, or explicit demo port overrides configured before starting the stack.

## Reduced mode

If Docker, Compose v2, Git, a working `.venv`, or the default demo ports are not available, Layer 0 reports reduced mode with skipped capabilities and the steps needed to enable full mode. File rendering and prerequisite guidance remain available where they do not require the missing host capability.

## Platform guidance

JarviSWARM v0.1.0 supports macOS only. On macOS, the preflight reports macOS-specific prerequisite guidance. On anything else it reports plainly that the platform is not supported, rather than guessing at guidance for it.

## Safety guarantees

The preflight does not auto-install system packages, does not change host configuration, and does not start Docker. Any repo-local bootstrap action must be explicit and limited to project-owned state such as `.venv`.
""".strip() + "\n"
    if contains_sensitive(text) or sanitize(text) != text:
        raise ValueError("prerequisite docs failed sanitizer checks")
    return text


__all__ = ["PreflightReport", "run_preflight", "render_text", "prerequisite_docs_text"]
