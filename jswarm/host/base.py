"""The agent-host interface. Codex, Cursor, and ChatGPT are not supported
and are not claimed to be; `current()` in `jswarm.host` raises
`UnsupportedHostError` for anything but Claude Code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class UnsupportedHostError(Exception):
    """Raised by `jswarm.host.current()` for an agent host other than Claude Code."""


class Host(Protocol):
    name: str

    def is_present(self) -> bool: ...

    def skills_dir(self) -> Path:
        """Where this host's global skills are installed: `~/.claude/skills`."""
        ...

    def settings_path(self, repo: Path) -> Path:
        """This host's project-level settings file: `<repo>/.claude/settings.json`."""
        ...

    def memory_path(self, repo: Path) -> Path:
        """This host's project-level memory file: `<repo>/CLAUDE.md`."""
        ...

    def hook_interpreter(self) -> str:
        """The interpreter this host's hooks are invoked with."""
        ...

    def register_mcp_hint(self) -> str:
        """The command line that registers an MCP server with this host, for messages."""
        ...

    def mcp_add_argv(self, name: str, command: str, args: list[str], *, scope: str = "user") -> list[str]:
        """The argv that registers a stdio MCP server named `name`, running
        `command args...`, with this host at the given scope.
        """
        ...

    def mcp_remove_argv(self, name: str, *, scope: str = "user") -> list[str]:
        """The argv that removes the MCP server registration named `name`
        from this host at the given scope -- the undo for `mcp_add_argv`.
        `uninstall` uses this so it removes exactly what `install` (with
        `--with-colgrep`) registered, not leave a dangling registration
        pointing at a binary/server `uninstall` just deleted.
        """
        ...
