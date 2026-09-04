"""The one extension point. Enterprise adds steps to a core skill by dropping Markdown here."""
from __future__ import annotations
import sys
from pathlib import Path

def ext_steps(skill: str) -> list[Path]:
    d = Path.home() / ".jswarm" / "ext" / f"{skill}.d"
    return sorted(d.glob("*.md")) if d.is_dir() else []

if __name__ == "__main__":
    for p in ext_steps(sys.argv[1]):
        print(p)
