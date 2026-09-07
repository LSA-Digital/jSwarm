"""Read-only host hooks. A reminder is not an executed checkpoint."""
from __future__ import annotations

import argparse


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["precompact_reminder"])
    parser.parse_args(argv)
    print("jSwarm: /jPrecompact records a work-item checkpoint. This reminder does not run it or save state; run it explicitly before planned compaction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
