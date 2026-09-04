"""Atomic renderer for COM-162 service-template artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .detect import SUPPORTED_PLATFORM_KEYS
from .instructions import render_instructions
from .leak_gate import LeakGateResult, run_leak_gate
from .platforms import get_renderer

SCHEMA_VERSION = "1.0"
GENERATOR_ID = "jarviswarm-com-162-phase3b-service-template-renderer"
_DEFAULT_RENDERED_AT = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class RenderContext:
    service_id: str
    platform_key: str
    artifact_name: str
    replacements: Mapping[str, str]


@dataclass(frozen=True)
class RenderResult:
    service_id: str
    platform_key: str
    services_root: Path
    output_dir: Path
    written_paths: tuple[Path, ...]
    metadata: Mapping[str, object]
    leak_gate: LeakGateResult


class ServiceTemplateRenderError(Exception):
    """Readable render error for COM-162 service-template blocking failures."""

    code = "ServiceTemplateRenderError"

    def __init__(self, message: str, *, leak_gate: LeakGateResult | None = None) -> None:
        self.message = message
        self.leak_gate = leak_gate
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _ensure_output_boundary(target_root: Path, service_id: str, platform_key: str) -> tuple[Path, Path, Path]:
    root = target_root.resolve()
    jarviswarm_root = root / ".jarviswarm"
    services_root = jarviswarm_root / "services"
    output_dir = services_root / service_id / platform_key
    if not _under(output_dir, services_root):
        raise ServiceTemplateRenderError("output path resolves outside target .jarviswarm/services")
    if jarviswarm_root.exists() and jarviswarm_root.is_symlink():
        raise ServiceTemplateRenderError("target .jarviswarm must not be a symlink for service-template render")
    if services_root.exists() and services_root.is_symlink():
        raise ServiceTemplateRenderError("target .jarviswarm/services must not be a symlink for service-template render")
    return jarviswarm_root, services_root, output_dir


def _replacements(descriptor) -> Mapping[str, str]:
    replacements: dict[str, str] = {
        "TARGET_ROOT": "{{TARGET_ROOT}}",
        "JARVISWARM_ROOT": "{{JARVISWARM_ROOT}}",
        "JARVISWARM_LOG_DIR": "{{JARVISWARM_LOG_DIR}}",
        "SERVICE_ID": "{{SERVICE_ID}}",
        "SERVICE_LABEL": descriptor.label,
        "SERVICE_DISPLAY_NAME": descriptor.display_name,
        "SERVICE_USER": "{{SERVICE_USER}}",
        "WORKING_DIRECTORY": "{{WORKING_DIRECTORY}}",
        "VENV_PYTHON": "{{VENV_PYTHON}}",
        "PATH_VALUE": "{{PATH_VALUE}}",
        "ENV_FILE": "{{ENV_FILE}}",
    }
    for port in descriptor.ports:
        replacements[f"PORT_{port.name.upper()}"] = "{{PORT_" + port.name.upper() + "}}"
    replacements["COMMAND_ARGV"] = " ".join(descriptor.command.argv)
    return MappingProxyType(replacements)


def _descriptor_fingerprint(descriptor, platform_key: str) -> str:
    payload = {
        "service_id": descriptor.service_id,
        "label": descriptor.label,
        "display_name": descriptor.display_name,
        "command": list(descriptor.command.argv),
        "working_directory": descriptor.working_directory,
        "user": descriptor.user,
        "env": dict(sorted(descriptor.env.items())),
        "ports": [port.__dict__ for port in descriptor.ports],
        "logs": descriptor.logs.__dict__,
        "platforms": list(descriptor.platforms),
        "platform_key": platform_key,
    }
    return _sha256_text(_json_dumps(payload))


def _stage_contents(service_id: str, platform_key: str, files: Mapping[str, str]) -> dict[str, str]:
    return {f"{service_id}/{platform_key}/{name}": text for name, text in files.items()}


def _write_files(directory: Path, files: Mapping[str, str]) -> tuple[Path, ...]:
    written: list[Path] = []
    for name, text in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
            raise ServiceTemplateRenderError("renderer file names must be simple relative paths")
        path = directory / relative
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return tuple(written)


def _atomic_publish(stage_dir: Path, output_dir: Path) -> None:
    output_parent = output_dir.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None
    if output_dir.exists():
        backup_dir = output_parent / f".{output_dir.name}.previous-{next(tempfile._get_candidate_names())}"
        output_dir.replace(backup_dir)
    try:
        stage_dir.replace(output_dir)
    except Exception:
        if backup_dir is not None and backup_dir.exists() and not output_dir.exists():
            backup_dir.replace(output_dir)
        raise
    if backup_dir is not None and backup_dir.exists():
        shutil.rmtree(backup_dir)


def render_service_template(
    descriptor,
    *,
    target_root: Path,
    platform_key: str,
    rendered_at: str | None = None,
    extra_content: Mapping[str, str] | None = None,
) -> RenderResult:
    """Render a service-template platform artifact and instructions atomically."""

    if platform_key not in SUPPORTED_PLATFORM_KEYS or platform_key not in descriptor.platforms:
        raise ServiceTemplateRenderError(f"descriptor does not support platform {platform_key!r}")
    if any(str(value).startswith(("/Users/", "/home/", "/root/")) or "\\Users\\" in str(value) for value in (descriptor.working_directory, descriptor.user)):
        descriptor = replace(descriptor, working_directory="{{WORKING_DIRECTORY}}", user="{{SERVICE_USER}}")

    _, services_root, output_dir = _ensure_output_boundary(target_root, descriptor.service_id, platform_key)
    platform_renderer = get_renderer(platform_key)

    preliminary_ctx = RenderContext(
        service_id=descriptor.service_id,
        platform_key=platform_key,
        artifact_name="service-template.pending",
        replacements=_replacements(descriptor),
    )
    rendered_platform = platform_renderer(descriptor, preliminary_ctx)
    ctx = RenderContext(
        service_id=descriptor.service_id,
        platform_key=platform_key,
        artifact_name=rendered_platform.artifact_name,
        replacements=preliminary_ctx.replacements,
    )
    instructions = render_instructions(descriptor, platform_key, ctx)

    files: dict[str, str] = {
        rendered_platform.artifact_name: rendered_platform.text,
        "deploy.md": instructions["deploy"],
        "verify.md": instructions["verify"],
        "teardown.md": instructions["teardown"],
    }
    if extra_content:
        files.update(extra_content)

    output_hashes = {name: _sha256_text(text) for name, text in sorted(files.items())}
    metadata_without_hash = {
        "schema_version": SCHEMA_VERSION,
        "service_id": descriptor.service_id,
        "platform_key": platform_key,
        "rendered_at": rendered_at or _DEFAULT_RENDERED_AT,
        "generator_id": GENERATOR_ID,
        "template_source_hash": _sha256_text(rendered_platform.text),
        "input_fingerprint": _descriptor_fingerprint(descriptor, platform_key),
        "output_hashes": output_hashes,
        "leak_gate": "pass",
        "instructions": {
            "deploy": "deploy.md",
            "verify": "verify.md",
            "teardown": "teardown.md",
        },
        "service_manager_mutation": False,
    }
    metadata_text = _json_dumps(metadata_without_hash)
    files["render-metadata.json"] = metadata_text
    output_hashes["render-metadata.json"] = _sha256_text(metadata_text)
    metadata = dict(metadata_without_hash)
    metadata["output_hashes"] = output_hashes
    files["render-metadata.json"] = _json_dumps(metadata)

    staged_content = _stage_contents(descriptor.service_id, platform_key, files)
    gate = run_leak_gate(staged_content, target_root=target_root)
    if not gate.passed:
        classes = ", ".join(sorted({failure.failure_class for failure in gate.failures}))
        raise ServiceTemplateRenderError(f"leak gate failed closed: {classes}", leak_gate=gate)

    services_root.mkdir(parents=True, exist_ok=True)
    tmp_parent = services_root / ".tmp"
    tmp_parent.mkdir(parents=True, exist_ok=True)
    stage_path = Path(tempfile.mkdtemp(prefix=f"{descriptor.service_id}-{platform_key}-", dir=tmp_parent))
    try:
        written_stage_paths = _write_files(stage_path, files)
        _atomic_publish(stage_path, output_dir)
    except Exception:
        if stage_path.exists():
            shutil.rmtree(stage_path)
        raise
    finally:
        if tmp_parent.exists():
            for child in tmp_parent.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            try:
                tmp_parent.rmdir()
            except OSError:
                pass

    written_paths = tuple(output_dir / path.name for path in written_stage_paths)
    return RenderResult(
        service_id=descriptor.service_id,
        platform_key=platform_key,
        services_root=services_root,
        output_dir=output_dir,
        written_paths=written_paths,
        metadata=metadata,
        leak_gate=gate,
    )
