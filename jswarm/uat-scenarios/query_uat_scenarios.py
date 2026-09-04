#!/usr/bin/env python3
"""Query a canonical UAT scenario JSON inventory with schema validation first."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping, cast

# When invoked as ``.venv/bin/python jswarm/uat-scenarios/query_uat_scenarios.py``,
# sys.path contains only this script directory. Bootstrap the repository root so
# shared dashboard helpers import the same way they do under module execution.
if __package__ in (None, ""):
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from jswarm.dashboard_ui import schema_validate  # noqa: E402
from jswarm.dashboard_ui.schema_validate import SchemaValidationError  # noqa: E402

JsonObject = dict[str, object]
_MARKDOWN_ID: Final = re.compile(r"`([^`]+)`")
_ATOM_OUTCOME: Final = re.compile(r"→\s*`([^`]+)`")
_PROVENANCE: Final = re.compile(r"`([^`]+)`\s*→\s*([^;]+)")
_RUNBOOK_HEADING: Final = re.compile(r"^## .*?(?: \{(?P<anchor>#[\w-]+)\})?\s*$", re.MULTILINE)
_ROUND_HEADING: Final = re.compile(r"^## Journey (?P<journey_id>\S+)\s*$", re.MULTILINE)
_CANONICAL_ROUND_HEADING: Final = re.compile(r"^### Journey (?P<journey_id>\S+)\s*$", re.MULTILINE)
_RUNBOOK_FIELDS: Final = ("Scenario IDs", "Watched outcomes", "Experiential criterion atoms", "Known items")
_ROUND_FIELDS: Final = ("Journey class", "Scenario ID", "Official GWT SHA-256", "UAT runbook anchor")
_CANONICAL_ROUND_FIELDS: Final = ("Scenario", "Official GWT SHA-256", "UAT-test anchor")
_OWNER_WALK_SETUP_HEADING: Final = "## Owner setup / reachability"
_OWNER_WALK_STEPS_MARKER: Final = "**Owner walk steps:**"
_OWNER_WALK_REPEAT_MARKER: Final = "**Repeat frames:**"
_OWNER_WALK_STEPS_HEADER: Final = "| # | Do this | You should see |"
_OWNER_WALK_STEPS_DELIMITER: Final = "|---|---|---|"
_OWNER_WALK_JOURNEY_HEADING: Final = re.compile(r"^## (.+?) \{#([\w-]+)\}\s*$")
_OWNER_WALK_LIST_ITEM: Final = re.compile(r"^(\d+)\. (.*)$")


@dataclass(frozen=True, slots=True)
class _Section:
    identifier: str | None
    fields: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ChainVerification:
    gaps: tuple[str, ...]
    journey_count: int
    atom_count: int
    known_provenance_count: int
    verified_gwt_sha256_by_journey: Mapping[str, str]
    owner_walk: Mapping[str, object] | None = None
    owner_walk_token: str | None = None


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


def validate_scenarios(data: JsonObject, schema: JsonObject) -> None:
    """Validate scenario data and raise one aggregated error on any defect."""
    errors = schema_validate.validate(data, schema, path="data")
    if errors:
        raise SchemaValidationError(errors)


def list_groupings(data: JsonObject) -> list[JsonObject]:
    """Return canonical groupings followed by smoke groupings with source labels."""
    rows: list[JsonObject] = []
    for grouping in _object_list(data.get("groupings")):
        rows.append(_grouping_row(grouping, source="grouping"))

    smoke = data.get("smoke")
    if isinstance(smoke, dict):
        for grouping in _object_list(smoke.get("smoke_groupings")):
            rows.append(_grouping_row(grouping, source="smoke"))
    return rows


def get_scenario(data: JsonObject, scenario_id: str) -> JsonObject:
    """Return the exact top-level scenario object for ``scenario_id``."""
    for scenario in _object_list(data.get("scenarios")):
        if scenario.get("id") == scenario_id:
            return scenario
    raise LookupError(f"Scenario id not found: {scenario_id}")


def list_scenarios(data: JsonObject, grouping_id: str | None = None) -> list[JsonObject]:
    """Return compact scenario rows, optionally scoped to one grouping's members."""
    scenarios = _object_list(data.get("scenarios"))
    scenarios_by_id = {
        scenario.get("id"): scenario
        for scenario in scenarios
        if isinstance(scenario.get("id"), str)
    }

    if grouping_id is None:
        selected_scenarios = scenarios
    else:
        grouping = _find_grouping(data, grouping_id)
        scenario_ids = grouping.get("scenario_ids")
        if not isinstance(scenario_ids, list):
            raise ValueError(f"Grouping {grouping_id} does not contain scenario_ids")
        selected_scenarios = []
        for scenario_id in scenario_ids:
            if not isinstance(scenario_id, str):
                raise ValueError(f"Grouping {grouping_id} contains a non-string scenario id: {scenario_id!r}")
            scenario = scenarios_by_id.get(scenario_id)
            if scenario is None:
                raise LookupError(f"Grouping {grouping_id} references unknown scenario id: {scenario_id}")
            selected_scenarios.append(scenario)

    return [
        {
            "id": scenario.get("id"),
            "title": scenario.get("title"),
            "status": scenario.get("status"),
        }
        for scenario in selected_scenarios
    ]


def _grouping_row(grouping: JsonObject, *, source: str) -> JsonObject:
    scenario_ids = grouping.get("scenario_ids")
    if not isinstance(scenario_ids, list):
        raise ValueError(f"Grouping {grouping.get('id', '<unknown>')} does not contain scenario_ids")
    return {
        "id": grouping.get("id"),
        "title": grouping.get("title"),
        "scenario_ids": scenario_ids,
        "source": source,
    }


def _find_grouping(data: JsonObject, grouping_id: str) -> JsonObject:
    for row in list_groupings(data):
        if row.get("id") == grouping_id:
            return row
    raise LookupError(f"Grouping id not found: {grouping_id}")


def _object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [cast(JsonObject, item) for item in value if isinstance(item, dict)]


def _field_occurrences(body: str, labels: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return {
        label: tuple(match.group(1).strip() for match in re.compile(rf"^\*\*{re.escape(label)}:\*\*[ \t]*(.*)$", re.MULTILINE).finditer(body))
        for label in labels
    }


def _section_bodies(text: str, heading: re.Pattern[str]) -> list[tuple[re.Match[str], str]]:
    headings = list(heading.finditer(text))
    return [(match, text[match.end() : headings[index + 1].start() if index + 1 < len(headings) else len(text)]) for index, match in enumerate(headings)]


def _canonical_round_sections(text: str) -> list[_Section]:
    sections: list[_Section] = []
    for match, body in _section_bodies(text, _CANONICAL_ROUND_HEADING):
        source_fields = {
            label: tuple(
                occurrence.group(1).strip()
                for occurrence in re.compile(rf"^{re.escape(label)}:[ \t]*(.*)$", re.MULTILINE).finditer(body)
            )
            for label in _CANONICAL_ROUND_FIELDS
        }
        normalized = {
            "Journey class": ("`HOT`",),
            "Scenario ID": tuple(f"`{value}`" for value in source_fields["Scenario"]),
            "Official GWT SHA-256": tuple(f"`{value}`" for value in source_fields["Official GWT SHA-256"]),
            "UAT runbook anchor": tuple(f"`{value}`" for value in source_fields["UAT-test anchor"]),
        }
        sections.append(_Section(match.group("journey_id"), normalized))
    return sections


def _round_sections(text: str) -> list[_Section]:
    canonical = _canonical_round_sections(text)
    if canonical and "## Canonical package (generated, owner-readable, and self-contained)" in text:
        return canonical
    return [
        _Section(match.group("journey_id"), _field_occurrences(body, _ROUND_FIELDS))
        for match, body in _section_bodies(text, _ROUND_HEADING)
    ]


def _ids(text: str, pattern: re.Pattern[str] = _MARKDOWN_ID) -> tuple[str, ...]:
    return tuple(match.group(1) for match in pattern.finditer(text))


def _field_gap(section: _Section, label: str, prefix: str, require_value: bool) -> str | None:
    occurrences = section.fields[label]
    if len(occurrences) != 1:
        return f"{prefix}-{'MISSING' if not occurrences else 'DUPLICATE'}: {section.identifier or '<unanchored>'} {label}"
    if not occurrences[0] and not require_value:
        return f"{prefix}-MISSING: {section.identifier or '<unanchored>'} {label}"
    values = _ids(occurrences[0])
    if require_value and len(values) != 1:
        return f"{prefix}-VALUE-COUNT: {section.identifier or '<unanchored>'} {label} expected=1 actual={len(values)}"


def _verify_chain(
    data: JsonObject,
    ticket: str,
    *,
    project_root: Path | None = None,
) -> tuple[list[str], int, int, int, dict[str, str]]:
    plan_dir = (Path.cwd() if project_root is None else project_root) / ".jswarm" / "plans" / ticket
    canonical_runbook = plan_dir / f"{ticket}.uat-scenario-steps.md"
    runbook_path = canonical_runbook if canonical_runbook.exists() else plan_dir / f"{ticket}.uat-test.md"
    sections = [_Section(match.group("anchor"), _field_occurrences(body, _RUNBOOK_FIELDS + ("Known-register provenance",))) for match, body in _section_bodies(runbook_path.read_text(encoding="utf-8"), _RUNBOOK_HEADING)]
    sections = [section for section in sections if section.identifier is not None or any(section.fields.values())]
    round_path = plan_dir / f"{ticket}.UAT-CURRENT-ROUND.md"
    round_available = round_path.exists()
    round_text = round_path.read_text(encoding="utf-8") if round_available else ""
    journeys = _round_sections(round_text)
    scenarios_by_id = {
        scenario_id: scenario
        for scenario in _object_list(data.get("scenarios"))
        if isinstance(scenario_id := scenario.get("id"), str)
    }
    scenario_ids = set(scenarios_by_id)
    gaps: list[str] = []

    def add_gap(gap: str) -> None:
        if gap not in gaps:
            gaps.append(gap)

    for section in sections:
        for label in _RUNBOOK_FIELDS:
            if gap := _field_gap(section, label, "RUNBOOK-FIELD", False):
                add_gap(gap)
        if len(section.fields["Known-register provenance"]) > 1:
            add_gap(f"RUNBOOK-FIELD-DUPLICATE: {section.identifier or '<unanchored>'} Known-register provenance")
    anchors = [section.identifier for section in sections if section.identifier is not None]
    for anchor in anchors:
        if anchors.count(anchor) > 1:
            add_gap(f"RUNBOOK-ANCHOR-DUPLICATE: {anchor}")
    hot_journeys: list[_Section] = []
    for journey in journeys:
        if gap := _field_gap(journey, "Journey class", "ROUND-FIELD", True):
            add_gap(gap)
            continue
        classes = _ids(journey.fields["Journey class"][0])
        if classes[0] not in {"HOT", "COLD"}:
            add_gap(f"ROUND-FIELD-VALUE-INVALID: {journey.identifier} Journey class {classes[0]}")
            continue
        if classes[0] == "COLD" and not any(journey.fields[label] for label in _ROUND_FIELDS[1:]):
            continue
        required_round_fields = ("Scenario ID", "UAT runbook anchor")
        for label in required_round_fields:
            if gap := _field_gap(journey, label, "ROUND-FIELD", True):
                add_gap(gap)
        if classes[0] == "HOT": hot_journeys.append(journey)
    if round_available and not gaps and not hot_journeys:
        add_gap("ROUND-HOT-SECTION-UNPARSEABLE" if "## Journeys (hot)" in round_text else "ROUND-HOT-JOURNEY-MISSING")
    if gaps:
        return gaps, 0, 0, 0, {}
    cited_scenarios = {scenario_id for section in sections for scenario_id in _ids(section.fields["Scenario IDs"][0])}
    sections_by_anchor = {section.identifier: section for section in sections if section.identifier is not None and anchors.count(section.identifier) == 1}
    atoms = known_provenance = 0
    for section in sections:
        for scenario_id in _ids(section.fields["Scenario IDs"][0]):
            if scenario_id not in scenario_ids:
                add_gap(f"SCENARIO-ID-NOT-FOUND: {scenario_id}")
        atom_outcomes = _ids(section.fields["Experiential criterion atoms"][0], _ATOM_OUTCOME)
        atoms += len(atom_outcomes)
        for outcome in _ids(section.fields["Watched outcomes"][0]):
            if outcome not in atom_outcomes:
                add_gap(f"EXPERIENTIAL-ATOM-MISSING: {outcome}")
        provenance = {match.group(1) for match in _PROVENANCE.finditer(section.fields["Known-register provenance"][0] if section.fields["Known-register provenance"] else "") if match.group(2).strip()}
        for item in _ids(section.fields["Known items"][0]):
            if item not in provenance:
                add_gap(f"KNOWN-REGISTER-PROVENANCE-MISSING: {item}")
            else:
                known_provenance += 1
    for entry in _object_list(data.get("changelog")):
        if entry.get("actor") == ticket:
            raw_ids = entry.get("scenario_ids")
            if isinstance(raw_ids, list):
                for scenario_id in cast(list[object], raw_ids):
                    if isinstance(scenario_id, str) and scenario_id not in cited_scenarios:
                        add_gap(f"TOUCHED-SCENARIO-ORPHAN: {scenario_id}")
    for journey in hot_journeys:
        scenario_id = _ids(journey.fields["Scenario ID"][0])[0]
        anchor = _ids(journey.fields["UAT runbook anchor"][0])[0]
        if scenario_id not in scenario_ids:
            add_gap(f"ROUND-JOURNEY-SCENARIO-NOT-FOUND: {scenario_id}")
        elif (section := sections_by_anchor.get(anchor)) is None:
            add_gap(f"RUNBOOK-ANCHOR-MISSING: {anchor}")
        elif scenario_id not in _ids(section.fields["Scenario IDs"][0]):
            add_gap(f"ROUND-LINEAGE-MISMATCH: {journey.identifier}")
    verified_gwt_sha256_by_journey: dict[str, str] = {}
    for journey in hot_journeys:
        scenario_id = _ids(journey.fields["Scenario ID"][0])[0]
        scenario = scenarios_by_id.get(scenario_id)
        if scenario is None:
            continue
        gwt = scenario.get("gwt")
        if gwt is None:
            add_gap(f"SCENARIO-GWT-MISSING: {scenario_id}")
            continue
        recorded_digest = scenario.get("gwt_sha256")
        if recorded_digest is None:
            add_gap(f"SCENARIO-GWT-SHA256-MISSING: {scenario_id}")
            continue
        if not isinstance(recorded_digest, str) or re.fullmatch(r"[0-9a-f]{64}", recorded_digest) is None:
            add_gap(f"SCENARIO-GWT-SHA256-INVALID: {scenario_id}")
            continue
        recomputed_digest = hashlib.sha256(
            json.dumps(gwt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if recomputed_digest != recorded_digest:
            add_gap(f"SCENARIO-GWT-SHA256-MISMATCH: {scenario_id}")
            continue
        digest_occurrences = journey.fields["Official GWT SHA-256"]
        digest_ids = _ids(digest_occurrences[0]) if len(digest_occurrences) == 1 else ()
        if len(digest_ids) != 1 or digest_ids[0] != recorded_digest:
            add_gap(f"OFFICIAL-GWT-LINEAGE-MISMATCH: {journey.identifier}")
            continue
        verified_gwt_sha256_by_journey[journey.identifier or ""] = recorded_digest
    return gaps, len(hot_journeys), atoms, known_provenance, verified_gwt_sha256_by_journey


def _owner_walk_table_row(line: str) -> list[str] | None:
    """Split one Markdown table row into unescaped cells, or return None if it is not a row."""
    text = line.strip()
    if len(text) < 2 or not text.startswith("|") or not text.endswith("|"):
        return None
    cells: list[str] = []
    current: list[str] = []
    index = 1
    while index < len(text) - 1:
        char = text[index]
        if char == "\\" and index + 1 < len(text) - 1 and text[index + 1] in ("\\", "|"):
            current.append(text[index + 1])
            index += 2
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _owner_walk_repeat_frames(value: str) -> tuple[str, ...] | None:
    """Parse a ``**Repeat frames:**`` value: literal ``none`` or backtick labels joined by ' · '."""
    if value == "none":
        return ()
    labels: list[str] = []
    for part in value.split(" · "):
        if len(part) < 2 or not (part.startswith("`") and part.endswith("`")):
            return None
        label = part[1:-1]
        if not label:
            return None
        labels.append(label)
    return tuple(labels)


def _extract_owner_walk(script_text: str, source_path: str) -> tuple[dict[str, object] | None, str | None]:
    """Extract the structured owner-walk projection from the closed derived-script grammar.

    Returns ``(projection, None)`` on success and ``(None, exact_token)`` otherwise.
    A script carrying no owner-walk grammar at all yields ``walk.generation.blocked.walk-script``.
    A grammar-bearing but malformed script yields the exact frozen ``walk.generation.blocked.*``
    token for its first structural failure: document order across sections, and inside one
    journey the frozen validation order (steps-table structure, then the action cell, then the
    expected cell, then repeat frames). Per-journey tokens carry the authored heading anchor as
    the journey identifier because the chain never sees the sealed package: the package
    ``journey_id`` and every package-relative classification (journey-set membership, empty
    title, action-vs-package mismatch, step-count mismatch) stay renderer-side. Duplicate
    anchors are parser-detectable and yield ``walk.generation.blocked.journey-set``. This
    module remains the sole parser: PREP and the renderer consume only this typed result.
    """
    if (
        _OWNER_WALK_SETUP_HEADING not in script_text
        and _OWNER_WALK_STEPS_MARKER not in script_text
        and _OWNER_WALK_REPEAT_MARKER not in script_text
    ):
        return None, "walk.generation.blocked.walk-script"
    lines = script_text.splitlines()

    setup_indexes = [index for index, line in enumerate(lines) if line.strip() == _OWNER_WALK_SETUP_HEADING]
    if len(setup_indexes) != 1:
        return None, "walk.generation.blocked.setup-steps"
    setup_steps: list[str] = []
    index = setup_indexes[0] + 1
    while index < len(lines):
        current = lines[index].strip()
        if not current:
            index += 1
            continue
        if current.startswith("## "):
            break
        match = _OWNER_WALK_LIST_ITEM.match(current)
        if match is None or int(match.group(1)) != len(setup_steps) + 1 or not match.group(2).strip():
            return None, "walk.generation.blocked.setup-steps"
        setup_steps.append(match.group(2).strip())
        index += 1
    if not setup_steps:
        return None, "walk.generation.blocked.setup-steps"

    journeys: list[dict[str, object]] = []
    seen_anchors: set[str] = set()
    heading_index = 0
    heading_lines = [(position, line) for position, line in enumerate(lines) if line.startswith("## ")]
    while heading_index < len(heading_lines):
        position, line = heading_lines[heading_index]
        journey_match = _OWNER_WALK_JOURNEY_HEADING.match(line.strip())
        next_position = heading_lines[heading_index + 1][0] if heading_index + 1 < len(heading_lines) else len(lines)
        heading_index += 1
        if journey_match is None:
            continue
        body = lines[position + 1 : next_position]
        steps_markers = [offset for offset, body_line in enumerate(body) if body_line.strip() == _OWNER_WALK_STEPS_MARKER]
        repeat_markers = [offset for offset, body_line in enumerate(body) if body_line.strip().startswith(_OWNER_WALK_REPEAT_MARKER)]
        if not steps_markers and not repeat_markers:
            # A section carrying only the existing chain fields is not an owner-projection journey.
            continue
        anchor = journey_match.group(2)
        steps_token = f"walk.generation.blocked.steps.{anchor}"
        if anchor in seen_anchors:
            return None, "walk.generation.blocked.journey-set"
        if len(steps_markers) != 1:
            return None, steps_token
        if len(repeat_markers) != 1:
            return None, f"walk.generation.blocked.repeat-frames.{anchor}"
        offset = steps_markers[0] + 1
        while offset < len(body) and not body[offset].strip():
            offset += 1
        if offset >= len(body) or body[offset].strip() != _OWNER_WALK_STEPS_HEADER:
            return None, steps_token
        offset += 1
        if offset >= len(body) or body[offset].strip() != _OWNER_WALK_STEPS_DELIMITER:
            return None, steps_token
        offset += 1
        steps: list[dict[str, object]] = []
        while offset < len(body) and body[offset].strip():
            cells = _owner_walk_table_row(body[offset])
            if cells is None or len(cells) != 3:
                return None, steps_token
            ordinal_text, action, expected = cells
            ordinal = len(steps) + 1
            if not ordinal_text.isdigit() or int(ordinal_text) != ordinal:
                return None, steps_token
            if not action:
                return None, f"walk.generation.blocked.action.{anchor}.{ordinal}"
            if not expected:
                return None, f"walk.generation.blocked.expected.{anchor}.{ordinal}"
            steps.append({"ordinal": ordinal, "action": action, "expected": expected})
            offset += 1
        if not steps:
            return None, steps_token
        repeat_value = body[repeat_markers[0]].strip()[len(_OWNER_WALK_REPEAT_MARKER):].strip()
        frames = _owner_walk_repeat_frames(repeat_value)
        if frames is None:
            return None, f"walk.generation.blocked.repeat-frames.{anchor}"
        seen_anchors.add(anchor)
        journeys.append({
            "uat_test_anchor": f"#{journey_match.group(2)}",
            "title": journey_match.group(1).strip(),
            "steps": tuple(steps),
            "repeat_frames": frames,
        })
    return {
        "source_path": source_path,
        "setup_steps": tuple(setup_steps),
        "journeys": tuple(journeys),
    }, None


def verify_chain(
    *,
    scenarios_path: Path,
    schema_path: Path,
    ticket: str,
    project_root: Path,
    walk_script_path: Path | None = None,
) -> ChainVerification:
    """Return the typed, read-only verification result for one ticket root.

    Without ``walk_script_path`` (the initial-seal call) the owner-walk projection fields
    stay ``None`` and the verdict is byte/behavior unchanged. With an explicit gated walk
    script path, the owner-walk projection is extracted from that exact file by this same
    sole parser; exactly one of the two fields is non-``None`` — a valid projection, or the
    exact ``walk.generation.blocked.*`` token for the first grammar failure (a script with
    no owner-walk grammar at all yields ``walk.generation.blocked.walk-script``).
    """

    scenarios = load_json_object(scenarios_path)
    schema = load_json_object(schema_path)
    validate_scenarios(scenarios, schema)
    gaps, journeys, atoms, known_provenance, verified = _verify_chain(
        scenarios,
        ticket,
        project_root=project_root,
    )
    owner_walk: Mapping[str, object] | None = None
    owner_walk_token: str | None = None
    if walk_script_path is not None:
        owner_walk, owner_walk_token = _extract_owner_walk(
            walk_script_path.read_text(encoding="utf-8"),
            str(walk_script_path),
        )
    return ChainVerification(
        gaps=tuple(gaps),
        journey_count=journeys,
        atom_count=atoms,
        known_provenance_count=known_provenance,
        verified_gwt_sha256_by_journey=dict(verified),
        owner_walk=owner_walk,
        owner_walk_token=owner_walk_token,
    )
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--scenarios", required=True, type=Path, help="input UAT scenarios JSON path")
    _ = parser.add_argument("--schema", required=True, type=Path, help="input UAT scenarios schema JSON path")
    mode = parser.add_mutually_exclusive_group(required=True)
    _ = mode.add_argument("--list-groupings", action="store_true", help="list canonical and smoke groupings")
    _ = mode.add_argument("--get-scenario", metavar="ID", help="return the exact top-level scenario object")
    _ = mode.add_argument("--list-scenarios", action="store_true", help="list compact scenario rows")
    _ = mode.add_argument("--verify-chain", metavar="TICKET", help="verify read-only UAT derivation-chain artifacts")
    _ = parser.add_argument("--grouping", metavar="ID", help="limit --list-scenarios to one grouping id")
    return parser


def _emit_json(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    _ = sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    verify_chain_ticket = cast(str | None, args.verify_chain)

    if args.grouping and not args.list_scenarios:
        parser.error("--grouping is only valid with --list-scenarios")

    try:
        scenarios = load_json_object(cast(Path, args.scenarios))
        schema = load_json_object(cast(Path, args.schema))
        validate_scenarios(scenarios, schema)

        if args.list_groupings:
            _emit_json(list_groupings(scenarios))
        elif args.get_scenario:
            _emit_json(get_scenario(scenarios, cast(str, args.get_scenario)))
        elif args.list_scenarios:
            _emit_json(list_scenarios(scenarios, cast(str | None, args.grouping)))
        elif verify_chain_ticket:
            gaps, journeys, atoms, known_provenance, _verified_gwt_sha256_by_journey = _verify_chain(scenarios, verify_chain_ticket)
            if gaps:
                _ = sys.stderr.write("CHAIN FAIL:\n" + "\n".join(f"- {gap}" for gap in gaps) + "\n")
                return 1
            _ = sys.stdout.write(f"PASS journeys={journeys} atoms={atoms} known-provenance={known_provenance}\n")
        else:  # pragma: no cover - argparse enforces a mode before this branch.
            parser.error("exactly one query mode is required")
        return 0
    except Exception as error:
        _ = sys.stderr.write(f"ERROR: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
