"""Claude Code: the only agent host supported in v0.1.0.

This module is the one place in the public core allowed to know that
`~/.claude` and `CLAUDE.md` exist. Everything specific to Claude Code lives
here: the global skills install path, project-level settings and memory
file locations, the hook interpreter, and MCP registration.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

_HOOK_INTERPRETER = "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python"
_MCP_HINT = "claude mcp add --scope user --transport http atlassian https://mcp.atlassian.com/v2/mcp"


@dataclass(frozen=True)
class ClaudeCodeHost:
    name: str = "claude-code"

    def is_present(self) -> bool:
        return shutil.which("claude") is not None or self.claude_home().is_dir()

    def claude_home(self) -> Path:
        """The global Claude Code config directory: `~/.claude`.

        The one place a bare `~/.claude` literal appears in the public core.
        Anything elsewhere that needs a path under it (agents, commands,
        session state, and so on) builds it from here rather than
        hardcoding `.claude` itself.
        """
        return Path.home() / ".claude"

    def skills_dir(self) -> Path:
        return self.claude_home() / "skills"

    def agents_dir(self) -> Path:
        return self.claude_home() / "agents"

    def commands_dir(self) -> Path:
        return self.claude_home() / "commands"

    def project_dir(self, repo: Path) -> Path:
        """This host's project-level config directory: `<repo>/.claude`."""
        return Path(repo) / ".claude"

    def settings_path(self, repo: Path) -> Path:
        return self.project_dir(repo) / "settings.json"

    def memory_path(self, repo: Path) -> Path:
        return Path(repo) / "CLAUDE.md"

    def hook_interpreter(self) -> str:
        return _HOOK_INTERPRETER

    def register_mcp_hint(self) -> str:
        return _MCP_HINT

    def mcp_add_argv(self, name: str, command: str, args: list[str], *, scope: str = "user") -> list[str]:
        return ["claude", "mcp", "add", "--scope", scope, name, command, *args]
