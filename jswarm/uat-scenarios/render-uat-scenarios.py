#!/usr/bin/env python3
"""Render a generic UAT scenario JSON inventory to deterministic Markdown."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, TypedDict, cast

from jinja2 import Environment, FileSystemLoader

# When invoked as ``.venv/bin/python jswarm/uat-scenarios/render-uat-scenarios.py``,
# sys.path contains only this script directory. Bootstrap the repository root so
# shared dashboard helpers import the same way they do under module execution.
if __package__ in (None, ""):
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from jswarm.dashboard_ui import canonical_json, schema_validate  # noqa: E402
from jswarm.dashboard_ui.schema_validate import SchemaValidationError  # noqa: E402

JsonObject = dict[str, object]
BANNER_START = "<!-- uat-scenarios:banner:start -->"
BANNER_END = "<!-- uat-scenarios:banner:end -->"

# COM-129 RD-3: render profiles map a profile name to its shipped template and the
# suffix used to derive a default output path from the scenarios JSON path. The
# deployed engine keeps these repo-relative locations (see deploy_uat_engine.py:
# render-uat-scenarios.py -> jswarm/uat-scenarios/, templates -> docs/templates/),
# so resolving the template dir relative to this file works in COM and every adopter.
PROFILE_TEMPLATES: dict[str, str] = {
    "engineering": "UAT_SCENARIOS.md.j2",
    "pm-summary": "UAT_SCENARIOS.pm-summary.md.j2",
}
PROFILE_OUTPUT_SUFFIX: dict[str, str] = {
    "engineering": ".engineering.md",
    "pm-summary": ".pm-summary.md",
}
DEFAULT_PROFILE = "engineering"

# COM-129 RD-9: the single Mermaid escaping path. Reserved characters that would
# break a quoted node label, normalized away here and ONLY here.
_MERMAID_RESERVED = ('"', "[", "]", "{", "}", "|", "<", ">", "(", ")")
# Mermaid does NOT auto-wrap long single-line node labels — the renderer clips them
# ("Upload a process from the s…"). Wrap labels at word boundaries with <br/> so each
# visual line fits its box. Width chosen so typical titles wrap to 2–3 short lines.
_MERMAID_LABEL_WRAP = 24


def _templates_dir() -> Path:
    """Resolve the shipped templates directory relative to this engine file."""
    return Path(__file__).resolve().parents[2] / "docs" / "templates"


def resolve_profile_io(
    profile: str,
    template_arg: Path | None,
    output_arg: Path | None,
    scenarios_path: Path,
) -> tuple[Path, Path]:
    """Resolve the (template, output) pair for a render (RD-3).

    Explicit ``--template`` / ``--output`` always win (back-compat for every current
    adopter invocation). Otherwise the profile supplies the template from
    :data:`PROFILE_TEMPLATES` and the output is derived deterministically from the
    *scenarios* path — never from a mutable field such as the document title.
    """
    if profile not in PROFILE_TEMPLATES:
        raise ValueError(f"unknown render profile {profile!r}; expected one of {sorted(PROFILE_TEMPLATES)}")
    template = template_arg if template_arg is not None else _templates_dir() / PROFILE_TEMPLATES[profile]
    if output_arg is not None:
        output = output_arg
    else:
        suffix = PROFILE_OUTPUT_SUFFIX[profile]
        name = scenarios_path.name
        stem = name[: -len(".json")] if name.endswith(".json") else name
        output = scenarios_path.with_name(stem + suffix)
    return Path(template), Path(output)


def _mermaid_node_id(raw: str) -> str:
    """Normalize an identifier into a Mermaid-safe node id (alnum + underscore)."""
    node = re.sub(r"[^0-9A-Za-z]", "_", raw)
    if not node or not (node[0].isalpha() or node[0] == "_"):
        node = "n_" + node
    return node


def _mermaid_label(raw: str) -> str:
    """Strip Mermaid-reserved characters from a display label (used for ALL labels)."""
    label = raw
    for char in _MERMAID_RESERVED:
        label = label.replace(char, " ")
    label = re.sub(r"\s+", " ", label).strip()
    return label or "untitled"


def _mermaid_wrap(label: str, width: int = _MERMAID_LABEL_WRAP) -> str:
    """Wrap an (already-escaped) label at word boundaries using ``<br/>``.

    Mermaid clips long single-line labels; wrapping keeps every visual line within the
    box. A word longer than ``width`` is left on its own line rather than split. The
    inserted ``<br/>`` is the only angle-bracket markup that survives :func:`_mermaid_label`.
    """
    words = label.split()
    if not words:
        return label
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "<br/>".join(lines)


def image_markdown(scenario: object, scenarios_dir: object, output_dir: object) -> str:
    """Return an inline Markdown image embed for ``scenario.image`` (RD-16), or ``""``.

    Template-support global. Driven by the JSON ``scenario.image.path`` field
    (deterministic from JSON alone — no filesystem probe). ``path`` is stored relative
    to the scenarios-JSON directory; it is re-based to the resolved output directory so
    the link renders wherever the ``.md`` lands. ``alt`` defaults to the scenario title.
    """
    if not isinstance(scenario, dict):
        return ""
    image = scenario.get("image")
    if not isinstance(image, dict):
        return ""
    path = image.get("path")
    if not isinstance(path, str) or not path:
        return ""
    alt = image.get("alt")
    if not isinstance(alt, str) or not alt:
        title = scenario.get("title")
        alt = title if isinstance(title, str) else str(scenario.get("id", "image"))
    rel = path
    if isinstance(scenarios_dir, str) and scenarios_dir and isinstance(output_dir, str) and output_dir:
        try:
            rel = os.path.relpath(os.path.join(scenarios_dir, path), output_dir).replace(os.sep, "/")
        except ValueError:
            rel = path
    alt = alt.replace("[", " ").replace("]", " ")
    return f"![{alt}]({rel})"


def find_by_id(items: object, id_value: object) -> object:
    """Return the first object in ``items`` whose ``id`` equals ``id_value`` (or ``None``).

    Template-support global: lets a template resolve a scenario by id (PE2E member
    listing) or a walkthrough screen by id (step->screen alignment) without relying
    on Jinja ``selectattr`` semantics over dicts.
    """
    for item in _object_list(items):
        if item.get("id") == id_value:
            return item
    return None


def unclustered_scenarios(data: object) -> list[object]:
    """Return scenarios not referenced by any ``primary_e2e_process``.

    Template-support global: the pm-summary profile clusters scenarios under their
    PE2E process(es); any scenario in no process is rendered in a trailing section so
    nothing is silently dropped. A scenario in multiple processes is intentionally
    rendered under each (RD-7) and is NOT considered unclustered.
    """
    if not isinstance(data, dict):
        return []
    referenced: set[str] = set()
    for process in _object_list(data.get("primary_e2e_processes")):
        scenario_ids = process.get("scenario_ids")
        if isinstance(scenario_ids, list):
            referenced.update(value for value in scenario_ids if isinstance(value, str))
    orphans: list[object] = []
    for scenario in _object_list(data.get("scenarios")):
        scenario_id = scenario.get("id")
        if not (isinstance(scenario_id, str) and scenario_id in referenced):
            orphans.append(scenario)
    return orphans


def pe2e_overview_mermaid(data: object) -> str:
    """Compute a deterministic PE2E overview ``flowchart`` from ``primary_e2e_processes``.

    Each process becomes a subgraph whose member scenarios are chained in
    ``scenario_ids`` array order (no sorting). Member node ids are namespaced by
    process so the same scenario may appear in multiple processes (RD-7) and so
    duplicate display labels still get distinct node ids (RD-9). Absent/empty
    ``primary_e2e_processes`` renders nothing.
    """
    if not isinstance(data, dict):
        return ""
    processes = _object_list(data.get("primary_e2e_processes"))
    if not processes:
        return ""
    title_by_id: dict[str, str] = {}
    for scenario in _object_list(data.get("scenarios")):
        scenario_id = scenario.get("id")
        if isinstance(scenario_id, str):
            title = scenario.get("title")
            title_by_id[scenario_id] = title if isinstance(title, str) else scenario_id

    lines: list[str] = ["flowchart LR"]
    for process in processes:
        process_id = process.get("id")
        if not isinstance(process_id, str):
            continue
        process_node = _mermaid_node_id(f"pe2e__{process_id}")
        process_title = process.get("title")
        process_label = _mermaid_wrap(_mermaid_label(process_title if isinstance(process_title, str) else process_id))
        lines.append(f'  subgraph {process_node}["{process_label}"]')
        member_nodes: list[str] = []
        scenario_ids = process.get("scenario_ids")
        if isinstance(scenario_ids, list):
            for scenario_id in scenario_ids:
                if not isinstance(scenario_id, str):
                    continue
                node = _mermaid_node_id(f"{process_id}__{scenario_id}")
                label = _mermaid_wrap(_mermaid_label(title_by_id.get(scenario_id, scenario_id)))
                lines.append(f'    {node}["{label}"]')
                member_nodes.append(node)
        for source, target in zip(member_nodes, member_nodes[1:]):
            lines.append(f"    {source} --> {target}")
        lines.append("  end")
    return "\n".join(lines) + "\n"


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


def render(
    scenarios: JsonObject,
    template_path: Path,
    *,
    scenarios_path: Path | None = None,
    output_path: Path | None = None,
) -> str:
    """Render deterministic Markdown from scenario data and a Jinja2 template.

    ``scenarios_path``/``output_path`` (when supplied) let the image-embed global
    (RD-16) re-base a scenarios-dir-relative ``image.path`` to the output directory.
    """
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["join_list"] = _join_list
    env.filters["markdown_value"] = _markdown_value
    env.filters["table_cell"] = _table_cell
    env.globals["scenario_row_columns"] = _scenario_row_columns
    env.globals["pe2e_overview_mermaid"] = pe2e_overview_mermaid
    env.globals["find_by_id"] = find_by_id
    env.globals["unclustered_scenarios"] = unclustered_scenarios
    env.globals["image_markdown"] = image_markdown
    env.globals["scenarios_dir"] = str(scenarios_path.parent) if scenarios_path is not None else ""
    env.globals["output_dir"] = str(output_path.parent) if output_path is not None else ""
    template = env.get_template(template_path.name)
    source_digest = canonical_json.hash_canonical(scenarios)
    rendered = template.render(data=scenarios, source_digest=source_digest)
    return rendered if rendered.endswith("\n") else rendered + "\n"


def _join_list(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _markdown_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _table_cell(value: object) -> str:
    text = _markdown_value(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _scenario_row_columns(rows: object) -> list[str]:
    """Return first-seen union of scenario row keys for deterministic tables."""
    columns: list[str] = []
    seen: set[str] = set()
    if not isinstance(rows, list):
        return columns
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            if isinstance(key, str) and key not in seen:
                seen.add(key)
                columns.append(key)
    return columns


def check_output(output_path: Path, rendered: str) -> int:
    """Return non-zero when ``output_path`` is missing or has whole-file drift."""
    if not output_path.exists():
        _ = sys.stderr.write(
            f"{output_path} is missing or out of date. Run the renderer without --check first.\n"
        )
        return 1
    current_bytes = output_path.read_bytes()
    rendered_bytes = rendered.encode("utf-8")
    if current_bytes == rendered_bytes:
        return 0
    current = current_bytes.decode("utf-8", errors="replace")
    expected = rendered_bytes.decode("utf-8", errors="replace")
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(output_path),
            tofile="render(uat_scenarios)",
        )
    )
    message = (
        f"{output_path} is stale; generated UAT scenario Markdown drift detected.\n"
        f"On-disk byte length: {len(current_bytes)}\n"
        f"In-memory byte length: {len(rendered_bytes)}\n"
        "Unified diff:\n"
        f"{diff}"
    )
    _ = sys.stderr.write(message)
    return 1


def validate_link_integrity(data: JsonObject, scenarios_dir: Path | str | None = None) -> list[str]:
    """Return semantic link-integrity errors not expressible in the JSON schema.

    When ``scenarios_dir`` is provided, also verify each ``scenario.image.path`` (RD-17)
    resolves to an existing file relative to that directory; pure callers omit it.
    """
    scenarios = _object_list(data.get("scenarios"))
    scenario_ids = {str(scenario.get("id")) for scenario in scenarios if isinstance(scenario.get("id"), str)}
    errors: list[str] = []

    def check_scenario_ids(container: object, path: str) -> None:
        if not isinstance(container, dict):
            return
        scenario_id_values = container.get("scenario_ids")
        if not isinstance(scenario_id_values, list):
            return
        for index, scenario_id in enumerate(scenario_id_values):
            if isinstance(scenario_id, str) and scenario_id not in scenario_ids:
                errors.append(f"{path}.scenario_ids[{index}] references unknown scenario id {scenario_id}")

    def check_array(path: str, items: object) -> None:
        for index, item in enumerate(_object_list(items)):
            check_scenario_ids(item, f"{path}[{index}]")

    contracts = data.get("contracts")
    if isinstance(contracts, dict):
        check_array("contracts.canonical_timeline", contracts.get("canonical_timeline"))

    smoke = data.get("smoke")
    if isinstance(smoke, dict):
        check_array("smoke.smoke_groupings", smoke.get("smoke_groupings"))
        check_array("smoke.scenarios", smoke.get("scenarios"))
        check_array("smoke.failure_seam_map", smoke.get("failure_seam_map"))
        check_array("smoke.automation_roadmap", smoke.get("automation_roadmap"))

    traceability = data.get("traceability")
    pe2e_ids: set[str] = set()
    if isinstance(traceability, dict):
        check_array("traceability.phase_mapping", traceability.get("phase_mapping"))
        pe2e_matrix = _object_list(traceability.get("pe2e_matrix"))
        for index, row in enumerate(pe2e_matrix):
            pe2e_id = row.get("pe2e_id")
            if isinstance(pe2e_id, str):
                pe2e_ids.add(pe2e_id)
            check_scenario_ids(row, f"traceability.pe2e_matrix[{index}]")

    check_array("groupings", data.get("groupings"))
    check_array("changelog", data.get("changelog"))
    check_array("pending_merge_back", data.get("pending_merge_back"))

    # COM-129 RD-7: PE2E process invariants — scenario_ids resolve to known scenarios,
    # process ids are unique, and no scenario repeats WITHIN one process. The same
    # scenario appearing across DIFFERENT processes is explicitly allowed.
    seen_process_ids: set[str] = set()
    for process_index, process in enumerate(_object_list(data.get("primary_e2e_processes"))):
        process_id = process.get("id")
        if isinstance(process_id, str):
            if process_id in seen_process_ids:
                errors.append(
                    f"primary_e2e_processes[{process_index}].id duplicates process id {process_id}"
                )
            seen_process_ids.add(process_id)
        process_scenario_ids = process.get("scenario_ids")
        if isinstance(process_scenario_ids, list):
            seen_in_process: set[str] = set()
            for member_index, member_id in enumerate(process_scenario_ids):
                if not isinstance(member_id, str):
                    continue
                if member_id not in scenario_ids:
                    errors.append(
                        f"primary_e2e_processes[{process_index}].scenario_ids[{member_index}] "
                        f"references unknown scenario id {member_id}"
                    )
                if member_id in seen_in_process:
                    errors.append(
                        f"primary_e2e_processes[{process_index}].scenario_ids[{member_index}] "
                        f"duplicates scenario id {member_id} within the same process"
                    )
                seen_in_process.add(member_id)

    def scenario_anchor_resolves(anchor: str) -> bool:
        return anchor in scenario_ids or any(scenario_id.startswith(anchor + ".") for scenario_id in scenario_ids)

    for scenario_index, scenario in enumerate(scenarios):
        scenario_id = scenario.get("id")
        scenario_label = scenario_id if isinstance(scenario_id, str) else f"index {scenario_index}"
        family = scenario.get("family")
        if isinstance(family, dict):
            members = family.get("members")
            if isinstance(members, list):
                for member_index, member_id in enumerate(members):
                    if isinstance(member_id, str) and member_id not in scenario_ids:
                        errors.append(
                            f"scenarios[{scenario_index}].family.members[{member_index}] "
                            f"references unknown scenario id {member_id}"
                        )
        pe2e_values = scenario.get("pe2e_ids")
        if isinstance(pe2e_values, list):
            for pe2e_index, pe2e_id in enumerate(pe2e_values):
                if isinstance(pe2e_id, str) and pe2e_id not in pe2e_ids:
                    errors.append(
                        f"scenarios[{scenario_index}].pe2e_ids[{pe2e_index}] on {scenario_label} "
                        f"references unknown PE2E id {pe2e_id}"
                    )
        evidence_links = scenario.get("evidence_links")
        for link_index, link in enumerate(_object_list(evidence_links)):
            ref = link.get("ref")
            if not isinstance(ref, str) or ref == "":
                errors.append(f"scenarios[{scenario_index}].evidence_links[{link_index}].ref must be non-empty")

        # COM-129 RD-6: walkthrough screen-id uniqueness + step->screen resolution
        # (within the same scenario). Both surface as --strict-links failures.
        walkthrough = scenario.get("walkthrough")
        if isinstance(walkthrough, dict):
            screen_ids: set[str] = set()
            screens = walkthrough.get("screens")
            if isinstance(screens, list):
                for screen_index, screen in enumerate(screens):
                    if not isinstance(screen, dict):
                        continue
                    screen_id = screen.get("id")
                    if isinstance(screen_id, str):
                        if screen_id in screen_ids:
                            errors.append(
                                f"scenarios[{scenario_index}].walkthrough.screens[{screen_index}].id "
                                f"duplicates screen id {screen_id} on {scenario_label}"
                            )
                        screen_ids.add(screen_id)
            steps = walkthrough.get("steps")
            if isinstance(steps, list):
                for step_index, step in enumerate(steps):
                    if not isinstance(step, dict):
                        continue
                    screen_ref = step.get("screen_ref")
                    if isinstance(screen_ref, str) and screen_ref not in screen_ids:
                        errors.append(
                            f"scenarios[{scenario_index}].walkthrough.steps[{step_index}].screen_ref "
                            f"references unknown screen id {screen_ref} on {scenario_label}"
                        )

        # COM-129 RD-15: image.source.page is one-based (a value contract not expressible in the
        # stdlib validator, which has no `minimum`); surface page < 1 as a --strict-links failure.
        image_for_page = scenario.get("image")
        if isinstance(image_for_page, dict):
            source = image_for_page.get("source")
            if isinstance(source, dict):
                page = source.get("page")
                if isinstance(page, int) and not isinstance(page, bool) and page < 1:
                    errors.append(
                        f"scenarios[{scenario_index}].image.source.page must be >= 1 (one-based) "
                        f"on {scenario_label}, got {page}"
                    )

        # COM-129 RD-17: image-file existence (only when a base dir is supplied).
        if scenarios_dir is not None:
            image = scenario.get("image")
            if isinstance(image, dict):
                image_path = image.get("path")
                if isinstance(image_path, str) and image_path:
                    # Must resolve to a real FILE — a directory (or other non-file) would render a
                    # broken Markdown image link, so is_file (not exists) is the gate (COM-129 RD-17).
                    if not (Path(scenarios_dir) / image_path).is_file():
                        errors.append(
                            f"scenarios[{scenario_index}].image.path references missing image file "
                            f"{image_path} on {scenario_label}"
                        )

        # COM-130 RD-18: optional screen-state anchors and transition anchors
        # resolve either to an exact scenario id or to a numbered scenario id nested
        # under the anchor (e.g. SCREEN.REF resolves via SCREEN.REF.1).
        screen_ref = scenario.get("screen_ref")
        if isinstance(screen_ref, str) and screen_ref and not scenario_anchor_resolves(screen_ref):
            errors.append(
                f"scenarios[{scenario_index}].screen_ref references unknown scenario anchor "
                f"{screen_ref} on {scenario_label}"
            )
        transitions = scenario.get("transitions")
        if isinstance(transitions, list):
            for transition_index, transition in enumerate(transitions):
                if not isinstance(transition, dict):
                    continue
                anchor = transition.get("anchor")
                if isinstance(anchor, str) and anchor and not scenario_anchor_resolves(anchor):
                    errors.append(
                        f"scenarios[{scenario_index}].transitions[{transition_index}].anchor "
                        f"references unknown scenario anchor {anchor} on {scenario_label}"
                    )

    return errors


def _object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, object], item) for item in value if isinstance(item, dict)]


def _strip_generated_banner(text: str) -> list[str]:
    """Remove only the explicit top-of-file generated provenance/banner block."""
    lines = text.splitlines()
    if not lines or lines[0] != BANNER_START:
        return lines
    try:
        end_index = lines.index(BANNER_END, 1)
    except ValueError:
        return lines
    remainder_start = end_index + 1
    if remainder_start < len(lines) and lines[remainder_start] == "":
        remainder_start += 1
    return lines[remainder_start:]


class AllowedDiffBudget(TypedDict):
    expected: str
    reference: str
    remaining: int
    label: str
    context: str | None


def _load_allowed_diff_budgets(path: Path | None) -> list[AllowedDiffBudget]:
    if path is None:
        return []
    config = load_json_object(path)
    entries = config.get("allowed_render_diffs")
    if not isinstance(entries, list):
        raise ValueError(f"{path} must contain allowed_render_diffs as a list")
    allowed: list[AllowedDiffBudget] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: allowed_render_diffs[{index}] must be an object")
        expected = entry.get("expected")
        reference = entry.get("reference")
        if not isinstance(expected, str) or not isinstance(reference, str):
            raise ValueError(
                f"{path}: allowed_render_diffs[{index}] must include string expected and reference fields"
            )
        count_value = entry.get("count", 1)
        if not isinstance(count_value, int) or isinstance(count_value, bool) or count_value < 1:
            raise ValueError(f"{path}: allowed_render_diffs[{index}].count must be a positive integer")
        context_value = entry.get("context", entry.get("section"))
        if context_value is not None and not isinstance(context_value, str):
            raise ValueError(f"{path}: allowed_render_diffs[{index}].context/section must be a string when present")
        label_value = entry.get("id", f"allowed_render_diffs[{index}]")
        label = label_value if isinstance(label_value, str) else f"allowed_render_diffs[{index}]"
        allowed.append(
            {
                "expected": expected,
                "reference": reference,
                "remaining": count_value,
                "label": label,
                "context": context_value,
            }
        )
    return allowed


def parity_check(rendered: str, reference_path: Path, allowed_diffs_path: Path | None = None) -> int:
    """Compare rendered Markdown to a reference, ignoring banner and sanctioned line diffs."""
    rendered_lines = _strip_generated_banner(rendered)
    reference_lines = _strip_generated_banner(reference_path.read_text(encoding="utf-8"))
    allowed_budgets = _load_allowed_diff_budgets(allowed_diffs_path)
    opcodes = difflib.SequenceMatcher(a=rendered_lines, b=reference_lines).get_opcodes()
    unsanctioned_expected: list[str] = []
    unsanctioned_reference: list[str] = []

    for tag, rendered_start, rendered_end, reference_start, reference_end in opcodes:
        if tag == "equal":
            continue
        expected_chunk = rendered_lines[rendered_start:rendered_end]
        reference_chunk = reference_lines[reference_start:reference_end]
        if _consume_allowed_chunk(expected_chunk, reference_chunk, rendered_lines, rendered_start, allowed_budgets):
            continue
        unsanctioned_expected.extend(expected_chunk)
        unsanctioned_reference.extend(reference_chunk)

    if not unsanctioned_expected and not unsanctioned_reference:
        return 0

    diff = "\n".join(
        difflib.unified_diff(
            unsanctioned_reference,
            unsanctioned_expected,
            fromfile=str(reference_path),
            tofile="render(uat_scenarios)",
            lineterm="",
        )
    )
    _ = sys.stderr.write(
        f"Parity check failed for {reference_path}; unsanctioned render differences remain.\n"
        "Allowed generated banner/provenance differences were ignored.\n"
        "Allowed render diffs are occurrence-bounded; each entry defaults to count=1.\n"
        "Unified diff of unsanctioned content:\n"
        f"{diff}\n"
    )
    return 1


def _consume_allowed_chunk(
    expected_chunk: list[str],
    reference_chunk: list[str],
    rendered_lines: list[str],
    rendered_start: int,
    allowed_budgets: list[AllowedDiffBudget],
) -> bool:
    if len(expected_chunk) != len(reference_chunk):
        return False
    consumed: list[AllowedDiffBudget] = []
    for offset, (expected, reference) in enumerate(zip(expected_chunk, reference_chunk)):
        budget = _find_allowed_budget(
            expected,
            reference,
            rendered_lines,
            rendered_start + offset,
            allowed_budgets,
        )
        if budget is None:
            for previous in consumed:
                previous["remaining"] += 1
            return False
        budget["remaining"] -= 1
        consumed.append(budget)
    return True


def _find_allowed_budget(
    expected: str,
    reference: str,
    rendered_lines: list[str],
    rendered_index: int,
    allowed_budgets: list[AllowedDiffBudget],
) -> AllowedDiffBudget | None:
    for budget in allowed_budgets:
        if budget["remaining"] <= 0:
            continue
        if budget["expected"] != expected or budget["reference"] != reference:
            continue
        context = budget["context"]
        if context and not _context_matches(rendered_lines, rendered_index, context):
            continue
        return budget
    return None


def _context_matches(lines: list[str], index: int, context: str) -> bool:
    start = max(0, index - 5)
    end = min(len(lines), index + 6)
    return any(context in line for line in lines[start:end])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--scenarios", required=True, type=Path, help="input UAT scenarios JSON path")
    _ = parser.add_argument("--schema", required=True, type=Path, help="input UAT scenarios schema JSON path")
    _ = parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_TEMPLATES),
        default=DEFAULT_PROFILE,
        help="render profile selecting the default template + derived output path (RD-3); default 'engineering'",
    )
    _ = parser.add_argument(
        "--template",
        type=Path,
        help="Jinja2 Markdown template path; overrides the profile's template when given",
    )
    _ = parser.add_argument(
        "--output",
        type=Path,
        help="canonical Markdown output path; overrides the profile-derived output when given",
    )
    _ = parser.add_argument("--check", action="store_true", help="exit non-zero when output Markdown is stale")
    _ = parser.add_argument("--stdout", action="store_true", help="also write rendered Markdown to stdout")
    _ = parser.add_argument("--parity", type=Path, help="reference Markdown path for banner-tolerant parity comparison")
    _ = parser.add_argument("--allowed-diffs", type=Path, help="JSON parity config with allowed_render_diffs[]")
    _ = parser.add_argument("--strict-links", action="store_true", help="exit non-zero for semantic dangling references")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        scenarios = load_json_object(cast(Path, args.scenarios))
        schema = load_json_object(cast(Path, args.schema))
        validate_scenarios(scenarios, schema)
        link_errors = validate_link_integrity(scenarios, scenarios_dir=cast(Path, args.scenarios).parent)
        if args.strict_links and link_errors:
            _ = sys.stderr.write(
                "Strict link-integrity validation failed; broken scenario references were found:\n"
                + "\n".join(f"- {error}" for error in link_errors)
                + "\n"
            )
            return 1
        template_path, output_path = resolve_profile_io(
            cast(str, args.profile),
            cast("Path | None", args.template),
            cast("Path | None", args.output),
            cast(Path, args.scenarios),
        )
        rendered = render(
            scenarios,
            template_path,
            scenarios_path=cast(Path, args.scenarios),
            output_path=output_path,
        )

        if args.check:
            return check_output(output_path, rendered)
        if args.parity:
            return parity_check(rendered, cast(Path, args.parity), cast(Path | None, args.allowed_diffs))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        _ = output_path.write_text(rendered, encoding="utf-8", newline="\n")
        _ = sys.stderr.write(f"Rendered UAT scenario inventory to {output_path}\n")
        if args.stdout:
            _ = sys.stdout.write(rendered)
        return 0
    except Exception as error:
        _ = sys.stderr.write(f"ERROR: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
