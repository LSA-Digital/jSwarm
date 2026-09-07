"""Tool prerequisites shared by the supported shell environments."""
from __future__ import annotations

from dataclasses import dataclass
import shutil
import sys


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    remedy: str
    optional: bool = False


@dataclass(frozen=True)
class ToolPlatform:
    name: str

    def check_prerequisites(self) -> list[Check]:
        from jswarm.host import current as current_host
        host = current_host()
        return [
            Check("Python 3.12+", sys.version_info >= (3, 12),
                  "Install Python 3.12+ with venv support: https://www.python.org/downloads/"),
            Check("git", shutil.which("git") is not None, "Install Git: https://git-scm.com/downloads"),
            Check("gh", shutil.which("gh") is not None, "Install GitHub CLI: https://cli.github.com/"),
            Check(host.name, host.is_present(), host.install_hint()),
            self._rust_toolchain_check(),
        ]

    def _rust_toolchain_check(self) -> Check:
        return Check("Rust toolchain (for --with-colgrep)", shutil.which("cargo") is not None,
                     "Install Rust and your platform's build tools: https://rustup.rs/", optional=True)
