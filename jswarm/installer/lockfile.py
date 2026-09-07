"""`~/.jswarm/install.lock.yaml`: the record of what `install` has finished.

Shape:

    public_version: v1.0.0
    installed_at: 2026-09-03T00:00:00Z
    state: complete            # or partial
    steps_completed: [venv, skills, portal_config]

`state: partial` plus the steps present is what lets `install` resume at the
first missing step, and what lets `verify` name the exact command that fixes
a half-finished install.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from jswarm.installer.fsops import WriteContext

LOCK_REL_PATH = Path(".jswarm") / "install.lock.yaml"


def lock_path(home: Path) -> Path:
    return Path(home) / LOCK_REL_PATH


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Lock:
    public_version: str
    installed_at: str
    state: str = "partial"
    steps_completed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "public_version": self.public_version,
            "installed_at": self.installed_at,
            "state": self.state,
            "steps_completed": list(self.steps_completed),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Lock":
        return cls(
            public_version=str(data.get("public_version", "")),
            installed_at=str(data.get("installed_at", "")),
            state=str(data.get("state", "partial")),
            steps_completed=[str(s) for s in (data.get("steps_completed") or [])],
        )


def read(home: Path) -> Lock | None:
    """The current lock, or None when no install has ever run."""
    path = lock_path(home)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return None
    return Lock.from_dict(data)


def write(ctx: WriteContext, home: Path, lock: Lock) -> None:
    ctx.write_yaml(lock_path(home), lock.to_dict())
