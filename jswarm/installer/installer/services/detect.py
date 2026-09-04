"""Read-only service platform detection for JarviSWARM service templates."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any, Iterable

PLATFORM_DARWIN_LAUNCHD = "darwin-launchd"
PLATFORM_LINUX_SYSTEMD = "linux-systemd"
PLATFORM_LINUX_SYSVINIT = "linux-sysvinit"
PLATFORM_LINUX_OPENRC = "linux-openrc"
PLATFORM_WINDOWS_SERVICE = "windows-service"
PLATFORM_WINDOWS_TASKSCHEDULER = "windows-taskscheduler"
PLATFORM_UNSUPPORTED = "unsupported"

SUPPORTED_PLATFORM_KEYS = (
    PLATFORM_DARWIN_LAUNCHD,
    PLATFORM_LINUX_SYSTEMD,
    PLATFORM_LINUX_SYSVINIT,
    PLATFORM_LINUX_OPENRC,
    PLATFORM_WINDOWS_SERVICE,
    PLATFORM_WINDOWS_TASKSCHEDULER,
)

_WINDOWS_MODE_TO_PLATFORM = {
    "service": PLATFORM_WINDOWS_SERVICE,
    "taskscheduler": PLATFORM_WINDOWS_TASKSCHEDULER,
}

_DEFAULT_REMEDIATION = (
    "Render service templates on a supported host or enable the test-harness "
    "platform override for fixture-only generation."
)


class UnsupportedServicePlatform(Exception):
    """Readable, stable error for unsupported service-template platform selection."""

    code = "UnsupportedServicePlatform"

    def __init__(
        self,
        detected_platform: str,
        supported_platforms: Iterable[str],
        remediation: str | None = None,
    ) -> None:
        self.detected_platform = detected_platform
        self.supported_platforms = tuple(supported_platforms)
        self.remediation = remediation or _DEFAULT_REMEDIATION
        super().__init__(str(self))

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "detected_platform": self.detected_platform,
            "supported_platforms": list(self.supported_platforms),
            "remediation": self.remediation,
        }

    def __str__(self) -> str:
        supported = ", ".join(self.supported_platforms) or "none"
        return (
            f"{self.code}: detected platform '{self.detected_platform}' is not supported "
            f"for this service (supported: {supported}). {self.remediation}"
        )


def classify_platform(probe: dict[str, Any]) -> str:
    """Map a read-only probe dictionary to a stable COM-162 platform key."""

    system = str(probe.get("system") or "").strip()
    if system == "Darwin":
        return PLATFORM_DARWIN_LAUNCHD
    if system == "Linux":
        if bool(probe.get("systemd_active_as_init")):
            return PLATFORM_LINUX_SYSTEMD
        if bool(probe.get("openrc_markers")):
            return PLATFORM_LINUX_OPENRC
        if bool(probe.get("sysvinit_service")):
            return PLATFORM_LINUX_SYSVINIT
        return PLATFORM_UNSUPPORTED
    if system == "Windows":
        return _WINDOWS_MODE_TO_PLATFORM.get(str(probe.get("windows_mode") or ""), PLATFORM_UNSUPPORTED)
    return PLATFORM_UNSUPPORTED


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _systemd_active_as_init() -> bool:
    return (
        _read_text(Path("/proc/1/comm")) == "systemd"
        or Path("/run/systemd/system").is_dir()
        or os.environ.get("INVOCATION_ID") is not None
        or os.environ.get("SYSTEMD_EXEC_PID") is not None
    )


def _has_openrc_markers() -> bool:
    return any(
        path.exists()
        for path in (
            Path("/run/openrc"),
            Path("/sys/fs/cgroup/openrc"),
            Path("/etc/openrc"),
            Path("/etc/conf.d"),
        )
    ) or shutil.which("rc-service") is not None


def _has_sysvinit_service() -> bool:
    return shutil.which("service") is not None or Path("/etc/init.d").is_dir()


def _windows_mode_from_env() -> str | None:
    value = os.environ.get("JARVISWARM_WINDOWS_SERVICE_MODE", "").strip().lower()
    if value in _WINDOWS_MODE_TO_PLATFORM:
        return value
    return None


def probe_host() -> dict[str, object]:
    """Build a read-only host probe without executing service-manager commands."""

    system = platform.system()
    probe: dict[str, object] = {
        "system": system,
        "systemd_active_as_init": False,
        "has_systemctl": False,
        "openrc_markers": False,
        "sysvinit_service": False,
        "windows_mode": None,
    }
    if system == "Linux":
        probe.update(
            {
                "systemd_active_as_init": _systemd_active_as_init(),
                "has_systemctl": shutil.which("systemctl") is not None,
                "openrc_markers": _has_openrc_markers(),
                "sysvinit_service": _has_sysvinit_service(),
            }
        )
    elif system == "Windows":
        probe["windows_mode"] = _windows_mode_from_env()
    return probe


def _descriptor_platforms(descriptor: Any) -> tuple[str, ...]:
    platforms = getattr(descriptor, "platforms", None)
    if platforms is None and isinstance(descriptor, dict):
        platforms = descriptor.get("platforms")
    return tuple(platforms or ())


def require_windows_default(descriptor: Any) -> str | None:
    """Require a Windows default when both Windows manager variants are supported."""

    from .schema import ServiceDescriptorError

    platforms = _descriptor_platforms(descriptor)
    windows_platforms = {PLATFORM_WINDOWS_SERVICE, PLATFORM_WINDOWS_TASKSCHEDULER}
    supported_windows = windows_platforms.intersection(platforms)
    windows_default = getattr(descriptor, "windows_default", None)
    if windows_default is None and isinstance(descriptor, dict):
        windows_default = descriptor.get("windows_default")

    if supported_windows == windows_platforms and windows_default not in windows_platforms:
        raise ServiceDescriptorError(
            "Descriptors supporting both Windows service managers must declare windows_default "
            "as windows-service or windows-taskscheduler before render."
        )
    if windows_default is not None and windows_default not in supported_windows:
        raise ServiceDescriptorError(
            f"windows_default must be one of the descriptor's supported Windows platforms; got {windows_default!r}."
        )
    return windows_default


def _detect_platform_for_descriptor(descriptor: Any, probe: dict[str, Any]) -> str:
    detected = classify_platform(probe)
    if detected == PLATFORM_UNSUPPORTED and str(probe.get("system") or "").strip() == "Windows":
        windows_default = require_windows_default(descriptor)
        if windows_default is not None:
            return windows_default
        platforms = _descriptor_platforms(descriptor)
        windows_platforms = [p for p in platforms if p in _WINDOWS_MODE_TO_PLATFORM.values()]
        if len(windows_platforms) == 1:
            return windows_platforms[0]
    return detected


def select_platform(
    descriptor: Any,
    *,
    probe: dict[str, Any] | None = None,
    allow_override: bool = False,
    override: str | None = None,
) -> str:
    """Select a supported platform for a descriptor from host probe or gated override."""

    supported_platforms = _descriptor_platforms(descriptor)
    require_windows_default(descriptor)

    if override is not None and allow_override:
        selected = override
        remediation = "Choose one of the descriptor's supported platform keys for the test-harness override."
    else:
        selected = _detect_platform_for_descriptor(descriptor, probe or probe_host())
        remediation = _DEFAULT_REMEDIATION

    if selected == PLATFORM_UNSUPPORTED or selected not in SUPPORTED_PLATFORM_KEYS or selected not in supported_platforms:
        raise UnsupportedServicePlatform(selected, supported_platforms, remediation)
    return selected
