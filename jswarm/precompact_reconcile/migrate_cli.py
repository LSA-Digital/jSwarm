"""Script entrypoint: auto-migrate a ticket's lifecycle assets to current standards.

    python jswarm/precompact_reconcile/migrate_cli.py --ticket KEY --repo-root .

Always exits 0 (fail-open). Run by /jPrecompact Surface 2 FIRST — before the AC-11 row-rebuild
(``rows_cli.py``) — so a legacy ticket self-heals (seeds slice indexes from hand-authored
matrices; adds missing matrix sections) and the rest of the chain runs as if the assets were
present at ticket creation (COM-167 AC-16).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from precompact_reconcile.migrate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
