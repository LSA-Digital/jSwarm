"""Pure platform renderers for COM-162 service-template artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..detect import (
    PLATFORM_DARWIN_LAUNCHD,
    PLATFORM_LINUX_OPENRC,
    PLATFORM_LINUX_SYSTEMD,
    PLATFORM_LINUX_SYSVINIT,
    PLATFORM_WINDOWS_SERVICE,
    PLATFORM_WINDOWS_TASKSCHEDULER,
)


@dataclass(frozen=True)
class RenderedPlatform:
    """Rendered platform artifact text and its deterministic file name."""

    artifact_name: str
    text: str


Renderer = Callable[[Any, Any], RenderedPlatform]


def get_renderer(platform_key: str) -> Renderer:
    if platform_key == PLATFORM_DARWIN_LAUNCHD:
        from . import macos_launchd

        return macos_launchd.render
    if platform_key == PLATFORM_LINUX_SYSTEMD:
        from . import linux_systemd

        return linux_systemd.render
    if platform_key == PLATFORM_LINUX_SYSVINIT:
        from . import linux_sysvinit

        return linux_sysvinit.render
    if platform_key == PLATFORM_LINUX_OPENRC:
        from . import linux_openrc

        return linux_openrc.render
    if platform_key == PLATFORM_WINDOWS_SERVICE:
        from . import windows_service

        return windows_service.render
    if platform_key == PLATFORM_WINDOWS_TASKSCHEDULER:
        from . import windows_taskscheduler

        return windows_taskscheduler.render
    raise ValueError(f"unsupported service-template platform: {platform_key}")


__all__ = ["RenderedPlatform", "get_renderer"]
