"""The platform boundary. macOS is the only platform supported in v0.1.0;
nothing outside this package may branch on the operating system itself.
"""
from __future__ import annotations

import sys

from jswarm.platform.base import Check, Platform, UnsupportedPlatform, UnsupportedPlatformError
from jswarm.platform.macos import MacOSPlatform


def current() -> Platform:
    """This machine's platform. MacOSPlatform on darwin, UnsupportedPlatform elsewhere."""
    if sys.platform == "darwin":
        return MacOSPlatform()
    return UnsupportedPlatform(sys.platform)


__all__ = ["Check", "Platform", "UnsupportedPlatform", "UnsupportedPlatformError", "MacOSPlatform", "current"]
