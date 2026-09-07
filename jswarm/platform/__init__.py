"""Inspect tools rather than rejecting a machine because it is not a Mac.

The documented shell environments are macOS, Linux, and Windows via WSL2.
Native Windows shells are not covered by the Bash installer and skills.
"""
import sys

from jswarm.platform.base import Check, ToolPlatform


def current() -> ToolPlatform:
    return ToolPlatform("macos" if sys.platform == "darwin" else sys.platform)


__all__ = ["Check", "ToolPlatform", "current"]
