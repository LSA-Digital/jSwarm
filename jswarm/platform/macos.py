"""macOS: the only platform supported in v0.1.0.

This module is the one place in the public core allowed to know that
Homebrew, Xcode command line tools, launchd, and the interactive shell's
profile file exist. Everything specific to macOS lives here.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jswarm.platform.base import Check


@dataclass(frozen=True)
class MacOSPlatform:
    name: str = "macos"

    def is_supported(self) -> bool:
        return True

    def unsupported_message(self) -> str:
        return "macOS is supported; there is no unsupported-platform message here."

    def check_prerequisites(self) -> list[Check]:
        return [
            self._xcode_command_line_tools_check(),
            self._homebrew_check(),
            self._python_check(),
            self._git_check(),
            self._gh_check(),
            self._agent_host_check(),
            self._rust_toolchain_check(),
        ]

    def _xcode_command_line_tools_check(self) -> Check:
        ok = False
        if shutil.which("xcode-select") is not None:
            result = subprocess.run(["xcode-select", "-p"], capture_output=True)
            ok = result.returncode == 0
        return Check("Xcode command line tools", ok, "xcode-select --install")

    def _homebrew_check(self) -> Check:
        ok = shutil.which("brew") is not None
        remedy = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        return Check("Homebrew", ok, remedy)

    def _python_check(self) -> Check:
        ok = shutil.which("python3.12") is not None
        return Check("Python 3.12", ok, "brew install python@3.12")

    def _git_check(self) -> Check:
        ok = shutil.which("git") is not None
        return Check("git", ok, "brew install git")

    def _gh_check(self) -> Check:
        ok = shutil.which("gh") is not None
        return Check("gh", ok, "brew install gh")

    def _agent_host_check(self) -> Check:
        from jswarm.host import current as _current_host

        host = _current_host()
        return Check(host.name, host.is_present(), "npm install -g @anthropic-ai/claude-code")

    def _rust_toolchain_check(self) -> Check:
        # Only `--with-colgrep` needs this (`cargo install colgrep`); the core
        # loop does not, so this is optional -- see Check.optional.
        ok = shutil.which("cargo") is not None
        remedy = "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"
        return Check("Rust toolchain (for --with-colgrep)", ok, remedy, optional=True)

    def daemon_install(self, plist: Path) -> None:
        target = Path.home() / "Library" / "LaunchAgents" / plist.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plist, target)
        subprocess.run(["launchctl", "load", str(target)], check=True)

    def daemon_uninstall(self, label: str) -> None:
        target = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        subprocess.run(["launchctl", "unload", str(target)], check=False)
        target.unlink(missing_ok=True)

    def shell_profile(self) -> Path:
        shell = os.environ.get("SHELL", "")
        if shell.endswith("bash"):
            return Path.home() / ".bash_profile"
        return Path.home() / ".zshrc"


def list_launchd_jobs() -> str:
    """Raw ``launchctl list`` stdout, for callers that only need to check
    whether a job is loaded. Timeouts and non-zero exits are the caller's
    concern; this is a thin boundary wrapper, not a parser.
    """
    completed = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
    return completed.stdout
