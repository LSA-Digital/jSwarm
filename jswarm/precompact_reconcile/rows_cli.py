"""Script entrypoint: rebuild a plan's count-bearing matrix ROWS from local working slices.

    python jswarm/precompact_reconcile/rows_cli.py --ticket KEY --repo-root .

Always exits 0 (fail-open). Run by /jPrecompact Surface 2 BEFORE the AC-10 status reconcile
(``cli.py``) so the rebuilt rows are what the reconcile then sets statuses on and update-plan
counts (COM-167 AC-11 → AC-10 → AC-9).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Insert the repository root (parents[2]: <pkg>/ -> jswarm/ -> repo root), not
# jswarm/ itself (parents[1]). jswarm/ on sys.path would shadow the stdlib for
# anything under jswarm/ sharing a name with it (e.g. jswarm/platform/ vs the
# stdlib platform module).
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from jswarm.precompact_reconcile.rows import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
