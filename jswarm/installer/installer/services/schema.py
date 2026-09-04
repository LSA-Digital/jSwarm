"""Service descriptor and capability schema for COM-162 service templates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

STATUS_TEMPLATE_READY = "template-ready"
STATUS_DEVELOPER_DEPLOYED = "developer-deployed"
STATUS_PARTIAL = "partial"
STATUS_BLOCKED = "blocked"
STATUS_UNSUPPORTED = "unsupported"

SERVICE_TEMPLATE_MATERIALIZATION = "service_template"

_ALLOWED_PLACEHOLDERS = {
    "TARGET_ROOT",
    "JARVISWARM_ROOT",
    "JARVISWARM_LOG_DIR",
    "SERVICE_ID",
    "SERVICE_LABEL",
    "SERVICE_DISPLAY_NAME",
    "SERVICE_USER",
    "WORKING_DIRECTORY",
    "VENV_PYTHON",
    "COMMAND_ARGV",
    "PATH_VALUE",
    "ENV_FILE",
}
_PLACEHOLDER_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")
_STRICT_PLACEHOLDER_RE = re.compile(r"{{(?:[A-Z][A-Z0-9_]*|PORT_[A-Z0-9_]+)}}")
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SECRET_ENV_SUFFIXES = ("_TOKEN", "_KEY", "_SECRET", "_PASSWORD")

_BEHAVIOR_KEYS = {
    "mode",
    "materializer",
    "materialization",
    "materialization_type",
    "install_type",
    "action",
    "lifecycle",
    "operation",
}
_FORBIDDEN_BEHAVIOR_VALUES = {
    "apply",
    "start",
    "load",
    "enable",
    "install",
    "install-service",
    "install_service",
    "teardown",
    "teardown-now",
    "teardown_now",
    "service",
}


class ServiceDescriptorError(Exception):
    """Readable schema error for service descriptor normalization."""

    code = "ServiceDescriptorError"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class CommandDescriptor:
    argv: tuple[str, ...]


@dataclass(frozen=True)
class PortDescriptor:
    name: str
    env: str
    default: str


@dataclass(frozen=True)
class LogDescriptor:
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ServiceDescriptor:
    service_id: str
    label: str
    display_name: str
    command: CommandDescriptor
    working_directory: str
    user: str
    env: Mapping[str, str]
    ports: tuple[PortDescriptor, ...]
    logs: LogDescriptor
    platforms: tuple[str, ...]
    materialization: str = SERVICE_TEMPLATE_MATERIALIZATION
    windows_default: str | None = None


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ServiceDescriptorError(f"{field} must be a mapping.")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceDescriptorError(f"{field} is required and must be a non-empty string.")
    _validate_placeholders(value, field)
    return value


def _require_slug(value: Any, field: str) -> str:
    result = _require_string(value, field)
    if not _SLUG_RE.fullmatch(result) or ".." in result or "/" in result or "\\" in result:
        raise ServiceDescriptorError(f"{field} must be a safe slug without path separators.")
    return result


def _validate_placeholders(value: str, field: str) -> None:
    for match in _PLACEHOLDER_RE.finditer(value):
        token = match.group(1)
        expression = match.group(0)
        if not _STRICT_PLACEHOLDER_RE.fullmatch(expression):
            raise ServiceDescriptorError(f"{field} contains malformed placeholder {expression!r}.")
        if token.startswith("PORT_"):
            if token == "PORT_":
                raise ServiceDescriptorError(f"{field} contains malformed port placeholder {expression!r}.")
            continue
        if token not in _ALLOWED_PLACEHOLDERS:
            raise ServiceDescriptorError(f"{field} contains unknown service template placeholder {token!r}.")


def _validate_placeholders_deep(value: Any, field: str) -> None:
    if isinstance(value, str):
        _validate_placeholders(value, field)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_placeholders_deep(item, f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_placeholders_deep(item, f"{field}[{index}]")


def _is_placeholder_or_env_file(value: str) -> bool:
    stripped = value.strip()
    return bool(_PLACEHOLDER_RE.fullmatch(stripped)) or "{{ENV_FILE}}" in stripped


def _validate_behavior(raw: Mapping[str, Any]) -> None:
    materialization = raw.get("materialization", SERVICE_TEMPLATE_MATERIALIZATION)
    if materialization != SERVICE_TEMPLATE_MATERIALIZATION:
        raise ServiceDescriptorError(
            f"service descriptors are template-only; materialization must be {SERVICE_TEMPLATE_MATERIALIZATION!r}."
        )
    _validate_behavior_deep(raw)


def _validate_behavior_deep(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if normalized_key in _BEHAVIOR_KEYS:
                if isinstance(item, str):
                    normalized_value = item.strip().lower()
                    if normalized_value != SERVICE_TEMPLATE_MATERIALIZATION and normalized_value in _FORBIDDEN_BEHAVIOR_VALUES:
                        raise ServiceDescriptorError(
                            f"service_template descriptors must not declare {key}={item!r}; apply/start/load/enable/install/teardown behavior is out of scope."
                        )
                elif normalized_key != "materialization":
                    raise ServiceDescriptorError(
                        f"service_template descriptors must not declare behavioral field {key!r}."
                    )
            _validate_behavior_deep(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_behavior_deep(item)


def _normalize_command(raw: Mapping[str, Any]) -> CommandDescriptor:
    command = _require_mapping(raw.get("command"), "command")
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv:
        raise ServiceDescriptorError("command.argv is required and must be a non-empty list.")
    normalized = tuple(_require_string(item, f"command.argv[{index}]") for index, item in enumerate(argv))
    if normalized[0] == "python3":
        raise ServiceDescriptorError("command.argv must not use bare python3; use {{VENV_PYTHON}}.")
    return CommandDescriptor(argv=normalized)


def _normalize_env(raw: Mapping[str, Any]) -> Mapping[str, str]:
    env = _require_mapping(raw.get("env"), "env")
    normalized: dict[str, str] = {}
    for key, value in env.items():
        name = _require_string(key, "env key")
        env_value = _require_string(value, f"env.{name}")
        if name.endswith(_SECRET_ENV_SUFFIXES) and not _is_placeholder_or_env_file(env_value):
            raise ServiceDescriptorError(
                f"env.{name} is secret-like and must use an approved placeholder or {{ENV_FILE}} reference."
            )
        normalized[name] = env_value
    return MappingProxyType(normalized)


def _normalize_ports(raw: Mapping[str, Any]) -> tuple[PortDescriptor, ...]:
    ports = raw.get("ports")
    if not isinstance(ports, list):
        raise ServiceDescriptorError("ports is required and must be a list.")
    normalized: list[PortDescriptor] = []
    for index, item in enumerate(ports):
        port = _require_mapping(item, f"ports[{index}]")
        name = _require_slug(port.get("name"), f"ports[{index}].name")
        env = _require_slug(port.get("env"), f"ports[{index}].env")
        default = _require_string(port.get("default"), f"ports[{index}].default")
        if default.isdigit():
            port_number = int(default)
            if port_number < 1 or port_number > 65535:
                raise ServiceDescriptorError(f"ports[{index}].default must be between 1 and 65535.")
        elif "{{PORT_" not in default:
            raise ServiceDescriptorError(f"ports[{index}].default must be numeric or a {{PORT_*}} placeholder.")
        normalized.append(PortDescriptor(name=name, env=env, default=default))
    return tuple(normalized)


def _normalize_logs(raw: Mapping[str, Any]) -> LogDescriptor:
    logs = _require_mapping(raw.get("logs"), "logs")
    return LogDescriptor(
        stdout=_require_string(logs.get("stdout"), "logs.stdout"),
        stderr=_require_string(logs.get("stderr"), "logs.stderr"),
    )


def _normalize_platforms(raw: Mapping[str, Any]) -> tuple[str, ...]:
    from .detect import PLATFORM_UNSUPPORTED, SUPPORTED_PLATFORM_KEYS

    platforms = raw.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        raise ServiceDescriptorError("platforms is required and must be a non-empty list.")
    seen: set[str] = set()
    normalized: list[str] = []
    for index, platform_key in enumerate(platforms):
        key = _require_string(platform_key, f"platforms[{index}]")
        if key == PLATFORM_UNSUPPORTED or key not in SUPPORTED_PLATFORM_KEYS:
            raise ServiceDescriptorError(f"platforms[{index}] is not a supported renderer platform: {key!r}.")
        if key not in seen:
            normalized.append(key)
            seen.add(key)
    return tuple(normalized)


def _normalize_windows_default(raw: Mapping[str, Any], platforms: tuple[str, ...]) -> str | None:
    from .detect import PLATFORM_WINDOWS_SERVICE, PLATFORM_WINDOWS_TASKSCHEDULER

    windows_platforms = {PLATFORM_WINDOWS_SERVICE, PLATFORM_WINDOWS_TASKSCHEDULER}
    supported_windows = windows_platforms.intersection(platforms)
    value = raw.get("windows_default")
    if supported_windows == windows_platforms and value is None:
        raise ServiceDescriptorError(
            "Descriptors supporting both windows-service and windows-taskscheduler must declare windows_default."
        )
    if value is None:
        return None
    default = _require_string(value, "windows_default")
    if default not in supported_windows:
        raise ServiceDescriptorError("windows_default must be one of the descriptor's supported Windows platforms.")
    return default


def normalize_descriptor(raw: dict[str, Any]) -> ServiceDescriptor:
    """Validate and freeze a COM-162 service descriptor."""

    mapping = _require_mapping(raw, "descriptor")
    _validate_behavior(mapping)
    _validate_placeholders_deep(mapping, "descriptor")

    service_id = _require_slug(mapping.get("service_id"), "service_id")
    label = _require_slug(mapping.get("label"), "label")
    display_name = _require_string(mapping.get("display_name"), "display_name")
    command = _normalize_command(mapping)
    working_directory = _require_string(mapping.get("working_directory"), "working_directory")
    user = _require_string(mapping.get("user"), "user")
    env = _normalize_env(mapping)
    ports = _normalize_ports(mapping)
    logs = _normalize_logs(mapping)
    platforms = _normalize_platforms(mapping)
    windows_default = _normalize_windows_default(mapping, platforms)

    return ServiceDescriptor(
        service_id=service_id,
        label=label,
        display_name=display_name,
        command=command,
        working_directory=working_directory,
        user=user,
        env=env,
        ports=ports,
        logs=logs,
        platforms=platforms,
        materialization=SERVICE_TEMPLATE_MATERIALIZATION,
        windows_default=windows_default,
    )
