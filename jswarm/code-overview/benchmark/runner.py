#!/usr/bin/env python3
"""Reconciled `/code-overview` v2 benchmark: guided query vs. unguided baseline.

NFR-234-004: falsifiable speedup benchmark. Two modes:

- ``--spine <path>``: loads a real seam-spine JSONL file, reconstructs every
  ``path`` record's guided pathway (via ``query.index.build_ranked_path``),
  and computes a genuine source-line reduction ratio from the anchors' own
  ``line_start``/``line_end`` fields -- no project vocabulary, no fabricated
  numbers, no hard-coded record IDs.
- ``--fixture <path>``: reads a precomputed ``code_overview.benchmark_fixture.v1``
  JSON object (``unguided_source_lines``, ``guided_source_lines``,
  ``pathway_nodes``) and applies the same pass/fail gate directly -- this is
  the mode the degenerate falsifier fixture exercises.

Metric definition (real ``--spine`` mode): for every anchor touched by a
guided pathway, "guided" cost is the anchor's own pinpointed span
(``line_end - line_start + 1``) -- reading exactly the located lines.
"Unguided" cost is that same anchor's ``line_end`` -- the cost of a brute
force top-of-file linear scan needed to *reach* that line without a guide.
The ratio of the two totals is the reported ``speedup_lines``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

if __package__ in (None, ""):
    _CODE_OVERVIEW_HOME = Path(__file__).resolve().parent.parent
    if str(_CODE_OVERVIEW_HOME) not in sys.path:
        sys.path.insert(0, str(_CODE_OVERVIEW_HOME))

from query.index import build_index, build_ranked_path  # noqa: E402
from schema import load_spine  # noqa: E402

BENCHMARK_HOME = "jswarm/code-overview/benchmark/runner.py"
SCHEMA_VERSION = "code_overview.benchmark.v1"
SPEEDUP_THRESHOLD = 3.0


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Benchmark input does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], data)


def _report(speedup_lines: float, pathway_nodes: int) -> dict[str, Any]:
    passed = speedup_lines >= SPEEDUP_THRESHOLD and pathway_nodes > 0
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark_home": BENCHMARK_HOME,
        "pass": passed,
        "speedup_lines": speedup_lines,
        "pathway_nodes": pathway_nodes,
    }


def run_fixture(fixture_path: Path) -> dict[str, Any]:
    """Evaluate the pass/fail gate directly from a precomputed fixture's numbers."""

    fixture = _load_json_object(fixture_path)
    unguided_lines = fixture.get("unguided_source_lines")
    guided_lines = fixture.get("guided_source_lines")
    pathway_nodes = fixture.get("pathway_nodes")
    if not isinstance(unguided_lines, (int, float)):
        raise ValueError(f"{fixture_path} is missing numeric unguided_source_lines")
    if not isinstance(guided_lines, (int, float)):
        raise ValueError(f"{fixture_path} is missing numeric guided_source_lines")
    if not isinstance(pathway_nodes, int):
        raise ValueError(f"{fixture_path} is missing an integer pathway_nodes")

    speedup_lines = (unguided_lines / guided_lines) if guided_lines > 0 else 0.0
    return _report(speedup_lines, pathway_nodes)


def run_spine(spine_path: Path) -> dict[str, Any]:
    """Evaluate the gate for real by reconstructing every path's guided pathway."""

    records = load_spine(spine_path)
    index = build_index(records)

    guided_anchor_ids: list[str] = []
    for path_record in index.by_type.get("path", []):
        ranked = build_ranked_path(index, path_record)
        for step in ranked["pathway"]:
            record_id = step.get("record_id")
            if record_id and record_id not in guided_anchor_ids:
                guided_anchor_ids.append(record_id)

    guided_lines_total = 0
    unguided_lines_total = 0
    for anchor_id in guided_anchor_ids:
        anchor = index.by_id.get(anchor_id, {})
        line_start = anchor.get("line_start")
        line_end = anchor.get("line_end")
        if not isinstance(line_start, int) or not isinstance(line_end, int):
            continue
        guided_lines_total += max(line_end - line_start + 1, 0)
        unguided_lines_total += max(line_end, 0)

    speedup_lines = (unguided_lines_total / guided_lines_total) if guided_lines_total > 0 else 0.0
    return _report(speedup_lines, len(guided_anchor_ids))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    _ = mode.add_argument("--spine", type=Path, help="path to a real seam-spine JSONL file")
    _ = mode.add_argument("--fixture", type=Path, help="path to a code_overview.benchmark_fixture.v1 JSON file")
    _ = parser.add_argument("--json", action="store_true", help="emit JSON report (the default and only stable output)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.spine is not None:
            report = run_spine(cast(Path, args.spine))
        else:
            report = run_fixture(cast(Path, args.fixture))
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        _ = sys.stdout.write("\n")
        return 0 if report["pass"] is True else 1
    except Exception as error:
        _ = sys.stderr.write(f"ERROR: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
