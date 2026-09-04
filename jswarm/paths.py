"""Where JarviSWARM lives on this machine. Nothing else may hardcode it."""
from __future__ import annotations
import os
from pathlib import Path

def jswarm_home() -> Path:
    return Path(os.environ.get("JSWARM_HOME") or Path.home() / "dev" / "jswarm").expanduser()

def python() -> Path:
    return jswarm_home() / ".venv" / "bin" / "python"
