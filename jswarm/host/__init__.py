"""The agent-host boundary. Claude Code is the only implementation shipped
in v0.1.0; nothing outside this package (and `jswarm.installer`, which is
allowed to know the host exists in order to install it) may reach for
`~/.claude` or `CLAUDE.md` on its own.
"""
from __future__ import annotations

from jswarm.host.base import Host, UnsupportedHostError
from jswarm.host.claude_code import ClaudeCodeHost


def current() -> Host:
    """The configured agent host. Claude Code is the only one in v0.1.0."""
    return ClaudeCodeHost()


__all__ = ["Host", "UnsupportedHostError", "current"]
