from __future__ import annotations

import runpy
from pathlib import Path
from typing import Final

CONTROLLED_RESOLVER: Final = (
    Path(__file__).resolve().parents[1]
    / "skills/fix/scripts/fix_localization.py"
)

if __name__ == "__main__":
    _ = runpy.run_path(str(CONTROLLED_RESOLVER), run_name="__main__")
else:
    globals().update(runpy.run_path(str(CONTROLLED_RESOLVER)))
