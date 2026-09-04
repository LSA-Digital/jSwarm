#!/usr/bin/env python3
"""Benchmark JSON-guided /code-overview against brute-force source reading.

The benchmark is intentionally falsifiable: it passes only when the selected
UAT grouping's scenario evidence narrows source reads by at least 3x on the
primary metric (lines) and the guided trace includes pathway evidence.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, cast

# When invoked as ``.venv/bin/python jswarm/uat-scenarios/benchmark_code_overview.py``,
# sys.path contains only this script directory. Bootstrap the repository root so
# shared dashboard helpers import the same way they do under module execution.
if __package__ in (None, ""):
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from jswarm.dashboard_ui import schema_validate  # noqa: E402
from jswarm.dashboard_ui.schema_validate import SchemaValidationError  # noqa: E402

JsonObject = dict[str, object]


def load_json_object(path: Path) -> JsonObject:
    """Load a JSON object from ``path`` with explicit operator-facing errors."""
    try:
        with path.open(encoding="utf-8") as input_file:
            data = json.load(input_file)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"JSON input does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(JsonObject, data)


def validate_scenarios_for_code_overview(data: JsonObject, schema: JsonObject) -> None:
    """Validate scenarios with the Phase 6 local ``type=code`` evidence extension.

    The checked-in schema remains the Phase 1-5 canonical schema where
    evidence link types are limited to rendered-document references. Phase 6
    fixtures add ``evidence_links[].type == \"code\"`` as a benchmark-only
    touchpoint contract, so this benchmark validates against a deep-copied schema
    with that one local enum extension rather than mutating the shared schema.
    """
    extended_schema = copy.deepcopy(schema)
    definitions = extended_schema.get("definitions")
    if isinstance(definitions, dict):
        evidence_link = definitions.get("evidenceLink")
        if isinstance(evidence_link, dict):
            properties = evidence_link.get("properties")
            if isinstance(properties, dict):
                type_property = properties.get("type")
                if isinstance(type_property, dict):
                    enum = type_property.get("enum")
                    if isinstance(enum, list) and "code" not in enum:
                        enum.append("code")

    errors = schema_validate.validate(data, extended_schema, path="data")
    if errors:
        raise SchemaValidationError(errors)


def brute_force_metrics(codebase_root: Path) -> JsonObject:
    """Read every regular file under ``codebase_root`` in deterministic order."""
    files = _regular_files(codebase_root)
    return _read_metrics(codebase_root, files, tool_calls=len(files), pathway_nodes=0)


def guided_metrics(data: JsonObject, codebase_root: Path, grouping_id: str) -> tuple[JsonObject, list[JsonObject]]:
    """Read only code evidence files reachable from grouping -> scenario links."""
    grouping = _find_grouping(data, grouping_id)
    scenario_ids = _string_list(grouping.get("scenario_ids"), f"Grouping {grouping_id} scenario_ids")
    scenarios_by_id = _scenarios_by_id(data)
    trace: list[JsonObject] = [
        {
            "kind": "grouping",
            "grouping_id": grouping_id,
            "scenario_ids": scenario_ids,
        }
    ]

    refs: list[str] = []
    pathway_nodes = 0
    for scenario_id in scenario_ids:
        scenario = scenarios_by_id.get(scenario_id)
        if scenario is None:
            raise LookupError(f"Grouping {grouping_id} references unknown scenario id: {scenario_id}")
        pathway_nodes += 1  # grouping -> scenario edge
        trace.append({"kind": "scenario", "scenario_id": scenario_id, "grouping_id": grouping_id})
        for evidence_link in _object_list(scenario.get("evidence_links")):
            if evidence_link.get("type") != "code":
                continue
            ref = evidence_link.get("ref")
            if not isinstance(ref, str) or not ref:
                raise ValueError(f"Scenario {scenario_id} contains a code evidence link without a string ref")
            refs.append(ref)
            pathway_nodes += 1  # scenario -> touchpoint edge
            trace.append({"kind": "touchpoint", "ref": ref, "scenario_id": scenario_id})

    unique_refs = sorted(set(refs))
    files = [_resolve_code_ref(codebase_root, ref) for ref in unique_refs]
    metrics = _read_metrics(codebase_root, files, tool_calls=1 + len(files), pathway_nodes=pathway_nodes)
    return metrics, trace


def build_report(fixture_root: Path, schema_path: Path, grouping_id: str) -> JsonObject:
    """Build the benchmark report object. Raises before output only on invalid inputs."""
    start = time.perf_counter()
    scenarios_path = fixture_root / "scenarios.json"
    codebase_root = fixture_root / "codebase"
    if not codebase_root.is_dir():
        raise NotADirectoryError(f"Fixture codebase directory does not exist: {codebase_root}")

    scenarios = load_json_object(scenarios_path)
    schema = load_json_object(schema_path)
    validate_scenarios_for_code_overview(scenarios, schema)

    brute_force = brute_force_metrics(codebase_root)
    guided, trace = guided_metrics(scenarios, codebase_root, grouping_id)

    brute_lines = _int_metric(brute_force, "lines")
    guided_lines = _int_metric(guided, "lines")
    guided_files = _int_metric(guided, "files")
    code_touchpoints = guided_files
    speedup = (brute_lines / guided_lines) if guided_lines > 0 else 0.0
    passed = (
        guided_files > 0
        and guided_lines > 0
        and code_touchpoints > 0
        and math.isfinite(speedup)
        and speedup >= 3.0
    )

    return {
        "fixture": str(fixture_root),
        "grouping": grouping_id,
        "strategies": {
            "brute_force": brute_force,
            "guided": guided,
        },
        "code_touchpoints": code_touchpoints,
        "speedup": speedup,
        "pass": passed,
        "trace": trace,
        "wall_time": round(time.perf_counter() - start, 6),
    }


def _read_metrics(codebase_root: Path, files: list[Path], *, tool_calls: int, pathway_nodes: int) -> JsonObject:
    total_lines = 0
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        total_lines += len(text.splitlines())
    return {
        "files": len(files),
        "lines": total_lines,
        "tool_calls": tool_calls,
        "pathway_nodes": pathway_nodes,
    }


def _regular_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _resolve_code_ref(codebase_root: Path, ref: str) -> Path:
    candidate = (codebase_root / ref).resolve()
    root = codebase_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Code evidence ref escapes fixture codebase: {ref}") from error
    if not candidate.is_file():
        raise FileNotFoundError(f"Code evidence ref does not exist under codebase: {ref}")
    return candidate


def _find_grouping(data: JsonObject, grouping_id: str) -> JsonObject:
    for grouping in _object_list(data.get("groupings")):
        if grouping.get("id") == grouping_id:
            return grouping
    smoke = data.get("smoke")
    if isinstance(smoke, dict):
        for grouping in _object_list(smoke.get("smoke_groupings")):
            if grouping.get("id") == grouping_id:
                return grouping
    raise LookupError(f"Grouping id not found: {grouping_id}")


def _scenarios_by_id(data: JsonObject) -> dict[str, JsonObject]:
    scenarios: dict[str, JsonObject] = {}
    for scenario in _object_list(data.get("scenarios")):
        scenario_id = scenario.get("id")
        if isinstance(scenario_id, str):
            scenarios[scenario_id] = scenario
    return scenarios


def _object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [cast(JsonObject, item) for item in value if isinstance(item, dict)]


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} contains a non-string value: {item!r}")
        result.append(item)
    return result


def _int_metric(metrics: JsonObject, key: str) -> int:
    value = metrics.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"Metric {key} is not an integer: {value!r}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--fixture", required=True, type=Path, help="fixture root containing scenarios.json and codebase/")
    _ = parser.add_argument("--schema", required=True, type=Path, help="UAT scenarios schema JSON path")
    _ = parser.add_argument("--grouping", required=True, help="grouping id to benchmark")
    _ = parser.add_argument("--json", action="store_true", help="emit JSON report (the default and only stable output)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        report = build_report(cast(Path, args.fixture), cast(Path, args.schema), cast(str, args.grouping))
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        _ = sys.stdout.write("\n")
        return 0 if report["pass"] is True else 1
    except Exception as error:
        _ = sys.stderr.write(f"ERROR: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
