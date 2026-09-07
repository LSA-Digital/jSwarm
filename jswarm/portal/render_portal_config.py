# jswarm/portal/render_portal_config.py
"""Render the decision-review portal config for this machine.

Templates carry ``__HOME__``, ``__JSWARM_COMMON__``, ``__PORT_UI__``, ``__PORT_BACKEND__``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from jswarm.paths import jswarm_home

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_TEMPLATE = REPO_ROOT / "templates" / "decision-review" / "config.template.json"


def render(template: str, *, home: Path, common: Path, port_ui: int = 8765, port_backend: int = 8766) -> str:
    out = (
        template.replace("__JSWARM_COMMON__", str(common))
        .replace("__HOME__", str(home))
        .replace("__PORT_UI__", str(port_ui))
        .replace("__PORT_BACKEND__", str(port_backend))
    )
    if "__" in out:
        raise ValueError("unrendered placeholder remains in template")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="where to write config.json")
    parser.add_argument("--port-ui", type=int, default=8765)
    parser.add_argument("--port-backend", type=int, default=8766)
    args = parser.parse_args(argv)

    home = Path(os.environ.get("HOME", str(Path.home())))
    common = Path(os.environ.get("JSWARM_COMMON") or jswarm_home())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        render(CONFIG_TEMPLATE.read_text(encoding="utf-8"), home=home, common=common,
               port_ui=args.port_ui, port_backend=args.port_backend),
        encoding="utf-8",
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
