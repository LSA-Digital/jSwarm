"""The platform interface. macOS is the only platform supported in v0.1.0;
`current()` in `jswarm.platform` returns `UnsupportedPlatform` for anything
else, honestly and without pretending any of its operations work.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

SUPPORTED_PLATFORM_NAME = "macOS"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    remedy: str


class UnsupportedPlatformError(Exception):
    """Raised by an `UnsupportedPlatform`'s mutating operations."""


class Platform(Protocol):
    name: str

    def is_supported(self) -> bool: ...

    def unsupported_message(self) -> str: ...

    def check_prerequisites(self) -> list[Check]: ...

    def daemon_install(self, plist: Path) -> None: ...

    def daemon_uninstall(self, label: str) -> None: ...

    def shell_profile(self) -> Path: ...


@dataclass(frozen=True)
class UnsupportedPlatform:
    """Anything but macOS. Every operation is honest about being unable to
    run rather than silently doing nothing or guessing at a macOS-shaped
    answer.
    """

    name: str

    def is_supported(self) -> bool:
        return False

    def unsupported_message(self) -> str:
        return f"{self.name} is not supported. JarviSWARM v0.1.0 supports {SUPPORTED_PLATFORM_NAME} only."

    def check_prerequisites(self) -> list[Check]:
        return [Check("platform", False, self.unsupported_message())]

    def daemon_install(self, plist: Path) -> None:
        raise UnsupportedPlatformError(self.unsupported_message())

    def daemon_uninstall(self, label: str) -> None:
        raise UnsupportedPlatformError(self.unsupported_message())

    def shell_profile(self) -> Path:
        raise UnsupportedPlatformError(self.unsupported_message())
