"""Script entrypoint: reconcile a plan's count-bearing matrices from local result docs.

    python jswarm/precompact_reconcile/cli.py --ticket KEY --repo-root .

Always exits 0 (fail-open). Run by /jPrecompact Surface 2 BEFORE the update-plan count
(AC-10) so the freshly-reconciled matrix statuses are what update-plan counts.
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

from jswarm.precompact_reconcile.matrices import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
