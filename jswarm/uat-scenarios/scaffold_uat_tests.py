#!/usr/bin/env python3
"""Scaffold a UAT-test runbook + unit/integration test stubs for an authored scenario.

/uat option 3 author-scenario, step "generate runbook + scaffold tests". Given a
canonical scenario already present in the scenarios JSON, this tool produces two ticket-local
working artifacts, both preview/apply receipt-gated:

1. ``<ticket-dir>/<TICKET>.uat-scenario-steps.md`` (legacy name: ``<TICKET>.uat-test.md``,
   still read by consumers for tickets that have not migrated) — an executable runbook
   generated from ``UAT_TEST_TEMPLATE.md`` carrying the required headings
   ``## Execution strategy`` and
   ``## Pass Criteria`` plus a per-scenario ``## Scenario <id>:`` section. New file, or a
   section inserted before ``## Pass Criteria`` when the runbook already exists (no clobber;
   re-appending the same scenario is a deterministic no-op).
2. unit + integration test STUBS in the project test directory — VALID, ``pytest
   --collect-only``-clean, intentionally skipped, with explicit TODO/skip clauses and a
   guidance note routing full authoring to ``/jGo``. NO runnable assertion logic.

Writes are confined to the active ticket folder (runbook + preview + receipt) and the project
test dir (stubs). ``apply`` refuses without a current matching preview receipt, refuses to
overwrite an existing stub unless ``--overwrite-stubs`` is given, and writes nothing on a
stale receipt or any conflict (no partial output).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

# Make this script's directory (sibling engine tools) and the repo root (shared helpers)
# importable whether run as a script or imported by a test via spec loader.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
for _path in (str(_HERE), str(_REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import populate_scenario_content as _pop  # noqa: E402  (reuse load/validate/sha helpers)

JsonObject = dict[str, object]

RUNBOOK_SUFFIX = ".uat-scenario-steps.md"
PREVIEW_SUFFIX = ".uat-scenario-steps.PREVIEW.md"
RECEIPT_SUFFIX = ".uat-scenario-steps.PREVIEW.receipt.json"
# T5.1: read-compat only — a ticket folder that has not migrated yet still has its
# runbook content picked up (and carried forward) even though every WRITE now lands on the
# canonical RUNBOOK_SUFFIX path above. Never write this suffix; never delete the legacy file.
_LEGACY_RUNBOOK_SUFFIX = ".uat-test.md"
PASS_CRITERIA_HEADING = "## Pass Criteria"
STUB_LAYERS = ("unit", "integration")
# A runbook must carry these canonical headings (AC-2); the source template must provide them too.
REQUIRED_HEADINGS = ("Execution strategy", "Pass Criteria")
# AC-4 / NFR-033: the active ticket key + its folder name must match this; anything else is a refusal.
TICKET_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
DEFAULT_TEMPLATE = _REPO_ROOT / "docs" / "templates" / "UAT_TEST_TEMPLATE.md"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _slug(scenario_id: str) -> str:
    """A safe Python-module slug from a scenario id (lowercase alnum + single underscores)."""
    out: list[str] = []
    prev_us = False
    for char in scenario_id.lower():
        if char.isalnum():
            out.append(char)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    return "".join(out).strip("_") or "scenario"


def _str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) and value else default


def _str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def find_scenario(data: JsonObject, scenario_id: str) -> JsonObject:
    """Return the scenario with ``scenario_id``; fail loud if it is not present."""
    scenarios = data.get("scenarios")
    if isinstance(scenarios, list):
        for scenario in scenarios:
            if isinstance(scenario, dict) and scenario.get("id") == scenario_id:
                return cast(JsonObject, scenario)
    raise ValueError(f"scenario id {scenario_id!r} not found in the scenarios JSON")


def load_template(template_path: Path) -> str:
    """Read the runbook source template and require it to provide the canonical headings (AC-2).

    The template is a REAL input: a missing/unreadable template, or one lacking a required
    heading, fails loud (the caller writes nothing). This makes template fidelity verifiable —
    removing the template or a required heading breaks generation rather than silently passing.
    """
    try:
        text = template_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValueError(f"runbook template not found: {template_path}") from error
    except OSError as error:
        raise ValueError(f"runbook template is unreadable: {template_path}: {error}") from error
    heading_lines = [line for line in text.splitlines() if line.lstrip().startswith("#")]
    for required in REQUIRED_HEADINGS:
        if not any(required in line for line in heading_lines):
            raise ValueError(
                f"runbook template {template_path} is missing the required heading {required!r}; "
                "cannot generate a faithful runbook"
            )
    return text


def _assert_confined(path: Path, root: Path, what: str) -> None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"refusing to write {what} outside its confinement root {root_resolved}: {resolved}")


# --------------------------------------------------------------------------- #
# Pure builders
# --------------------------------------------------------------------------- #
def _scenario_section(scenario: JsonObject, ticket: str) -> str:
    scenario_id = _str(scenario.get("id"), "UAT-UNKNOWN")
    title = _str(scenario.get("title"), scenario_id)
    goal = _str(scenario.get("goal"), "Author the user-visible outcome during /jGo.")
    pe2e = _str_list(scenario.get("pe2e_ids"))
    maps_to = ", ".join(pe2e) if pe2e else "TBD — map to A/C during /jGo"

    walkthrough = scenario.get("walkthrough")
    steps = _str_list(walkthrough.get("user_steps")) if isinstance(walkthrough, dict) else []
    if not steps:
        steps = ["Run the command/flow under test.", "Observe the user-visible outcome."]
    expected = _str_list(walkthrough.get("expected_user_visible")) if isinstance(walkthrough, dict) else []
    if not expected:
        system_detail = scenario.get("system_detail")
        if isinstance(system_detail, dict):
            expected = _str_list(system_detail.get("pass_criteria"))
    if not expected:
        expected = ["The scenario's primary user-visible outcome is observed."]

    lines: list[str] = []
    lines.append(f"## Scenario {scenario_id}: {title}")
    lines.append("")
    lines.append(f"**Maps to:** {maps_to}")
    lines.append(f"**Goal:** {goal}")
    lines.append("")
    lines.append("**Steps:**")
    lines.append("")
    for index, step in enumerate(steps, start=1):
        lines.append(f"{index}. {step}")
    lines.append("")
    lines.append("**Expected:**")
    lines.append("")
    lines.append("| What to check | Expected result |")
    lines.append("|---------------|-----------------|")
    for item in expected:
        cell = item.replace("|", "\\|")
        lines.append(f"| {cell} | PASS when observed; FAIL otherwise |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _fill_template(template_text: str, ticket: str, last_updated: str) -> str:
    """Derive the runbook base from the source template (AC-2: the output is *from* the template).

    Deterministic substitutions the tool knows; every other template placeholder/section flows
    through verbatim for the developer to tailor during /jGo. The canonical
    ``Execution strategy`` heading is normalized to H2 so the required-heading contract holds.
    """
    body = template_text.replace("[TICKET-XXX]", ticket)
    body = body.replace("**Last Updated:** YYYY-MM-DD", f"**Last Updated:** {last_updated}")
    body = body.replace("### Execution strategy", "## Execution strategy")
    provenance = (
        "<!-- Generated by /uat author-scenario (scaffold_uat_tests.py) FROM "
        "docs/templates/UAT_TEST_TEMPLATE.md. Tailor the inherited template sections and author "
        "full assertions during /jGo (TDD red-first). -->\n\n"
    )
    return provenance + body


def _insert_before_pass_criteria(body: str, section: str) -> str:
    if PASS_CRITERIA_HEADING in body:
        index = body.index(PASS_CRITERIA_HEADING)
        return body[:index].rstrip("\n") + "\n\n" + section + "\n" + body[index:]
    return body.rstrip("\n") + "\n\n" + section


def _new_document(scenario: JsonObject, ticket: str, last_updated: str, template_text: str) -> str:
    body = _fill_template(template_text, ticket, last_updated)
    section = _scenario_section(scenario, ticket)
    return _insert_before_pass_criteria(body, section)


def build_runbook(existing: str | None, scenario: JsonObject, *, ticket: str, last_updated: str, template_text: str) -> str:
    """Return the FINAL runbook bytes after adding ``scenario``.

    New file (``existing is None``) → the source template (substituted) with the authored
    scenario section inserted before ``## Pass Criteria``. Existing file → the scenario section
    inserted before ``## Pass Criteria`` (or appended if absent). If a section for this scenario
    id is already present, the existing document is returned unchanged (idempotent).
    """
    scenario_id = _str(scenario.get("id"), "UAT-UNKNOWN")
    if existing is None:
        return _new_document(scenario, ticket, last_updated, template_text)

    heading = f"## Scenario {scenario_id}:"
    if heading in existing:
        return existing  # idempotent — already present, no duplicate

    section = _scenario_section(scenario, ticket)
    return _insert_before_pass_criteria(existing, section)


def build_stub(scenario: JsonObject, *, ticket: str, layer: str) -> tuple[str, str]:
    """Return ``(filename, source)`` for a VALID, collectable, intentionally-skipped stub."""
    scenario_id = _str(scenario.get("id"), "UAT-UNKNOWN")
    goal = _str(scenario.get("goal"), "(see scenario goal)")
    slug = _slug(scenario_id)
    filename = f"test_uat_{slug}_{layer}.py"
    func = f"test_uat_{slug}_{layer}_placeholder"
    source = (
        f'"""UAT scaffold stub — {scenario_id} ({ticket}), {layer} layer.\n'
        "\n"
        "SCAFFOLD ONLY. Generated by /uat author-scenario (scaffold_uat_tests.py). This stub is\n"
        f"intentionally skipped and contains NO runnable assertion logic. Author the real {layer}\n"
        "assertions during /jGo (TDD red-first); do not ship this skipped stub as proof of\n"
        f"{scenario_id}.\n"
        "\n"
        f"Scenario goal: {goal}\n"
        '"""\n'
        "import pytest\n"
        "\n"
        "pytestmark = pytest.mark.skip(\n"
        f'    reason="SCAFFOLD STUB for {scenario_id} ({ticket}) — author during /jGo TDD lane. TODO."\n'
        ")\n"
        "\n"
        "\n"
        f"def {func}() -> None:\n"
        f"    # TODO({ticket}): implement the {layer} assertions for {scenario_id}.\n"
        "    # N/A until /jGo authors the real test logic (do not ship a passing stub).\n"
        f'    pytest.skip("SCAFFOLD STUB not yet implemented — author during /jGo.")\n'
    )
    return filename, source


# --------------------------------------------------------------------------- #
# Plan + receipt
# --------------------------------------------------------------------------- #
def _runbook_path(ticket_dir: Path, ticket: str) -> Path:
    return ticket_dir / f"{ticket}{RUNBOOK_SUFFIX}"


def _legacy_runbook_path(ticket_dir: Path, ticket: str) -> Path:
    return ticket_dir / f"{ticket}{_LEGACY_RUNBOOK_SUFFIX}"


def _read_existing_runbook(ticket_dir: Path, ticket: str) -> str | None:
    """Read the current runbook body to append to: canonical name first, else the ticket's
    not-yet-migrated legacy-named runbook. The WRITE path always targets the canonical name
    (auto-migrate on next edit), so this is read-only — the legacy file is never touched."""
    canonical = _runbook_path(ticket_dir, ticket)
    if canonical.is_file():
        return canonical.read_text(encoding="utf-8")
    legacy = _legacy_runbook_path(ticket_dir, ticket)
    if legacy.is_file():
        return legacy.read_text(encoding="utf-8")
    return None


def _preview_path(ticket_dir: Path, ticket: str) -> Path:
    return ticket_dir / f"{ticket}{PREVIEW_SUFFIX}"


def _receipt_path(ticket_dir: Path, ticket: str) -> Path:
    return ticket_dir / f"{ticket}{RECEIPT_SUFFIX}"


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Plan:
    def __init__(
        self,
        scenario: JsonObject,
        runbook_path: Path,
        existing: str | None,
        final_runbook: str,
        stubs: list[tuple[Path, str]],
    ) -> None:
        self.scenario = scenario
        self.runbook_path = runbook_path
        self.existing = existing
        self.final_runbook = final_runbook
        self.stubs = stubs

    @property
    def mode(self) -> str:
        if self.existing is None:
            return "new"
        return "unchanged" if self.final_runbook == self.existing else "appended"


def _build_plan(
    scenarios_path: Path,
    scenario_id: str,
    ticket: str,
    ticket_dir: Path,
    tests_dir: Path,
    schema_path: Path | None,
    last_updated: str | None,
    template_path: Path,
) -> _Plan:
    # AC-4 / NFR-033 confinement: refuse an unsafe ticket key, and require the ticket folder's
    # basename to equal the ticket — before reading or writing anything.
    if not TICKET_KEY_RE.match(ticket):
        raise ValueError(f"refusing unsafe --ticket {ticket!r}: must be a ticket key like ABC-123")
    if ticket_dir.resolve().name != ticket:
        raise ValueError(
            f"--ticket-dir basename {ticket_dir.resolve().name!r} must equal --ticket {ticket!r} "
            "(writes must land in the active ticket folder)"
        )
    template_text = load_template(template_path)  # AC-2: the template is a real INPUT to the runbook

    data = _pop.load_json_object(scenarios_path)
    if schema_path is not None:
        _pop._validate(data, _pop.load_json_object(schema_path))  # starting doc must be valid
    scenario = find_scenario(data, scenario_id)
    resolved_last_updated = last_updated or _resolve_last_updated(data)

    runbook_path = _runbook_path(ticket_dir, ticket)
    existing = _read_existing_runbook(ticket_dir, ticket)
    final_runbook = build_runbook(existing, scenario, ticket=ticket, last_updated=resolved_last_updated, template_text=template_text)

    stubs: list[tuple[Path, str]] = []
    for layer in STUB_LAYERS:
        name, source = build_stub(scenario, ticket=ticket, layer=layer)
        stubs.append((tests_dir / name, source))

    # Defense in depth: every target path must resolve inside its confinement root.
    _assert_confined(runbook_path, ticket_dir, "runbook")
    _assert_confined(_preview_path(ticket_dir, ticket), ticket_dir, "runbook preview")
    _assert_confined(_receipt_path(ticket_dir, ticket), ticket_dir, "receipt")
    for stub_path, _source in stubs:
        _assert_confined(stub_path, tests_dir, "test stub")
    return _Plan(scenario, runbook_path, existing, final_runbook, stubs)


def _resolve_last_updated(data: JsonObject) -> str:
    document = data.get("document")
    if isinstance(document, dict):
        value = document.get("last_updated")
        if isinstance(value, str) and value:
            return value
    return "draft"


def _receipt(scenarios_path: Path, scenario_id: str, tests_dir: Path, plan: _Plan) -> JsonObject:
    return {
        "kind": "uat-scaffold-preview-receipt",
        "scenarios": str(scenarios_path),
        "scenarios_sha256": _pop._sha256_file(scenarios_path),
        "scenario_id": scenario_id,
        "runbook": str(plan.runbook_path),
        "runbook_mode": plan.mode,
        "runbook_existing_sha256": _sha_text(plan.existing) if plan.existing is not None else None,
        "runbook_final_sha256": _sha_text(plan.final_runbook),
        "stubs": [{"path": str(path), "sha256": _sha_text(source)} for path, source in plan.stubs],
        "tests_dir": str(tests_dir),
    }


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #
def run_preview(
    scenarios_path: Path,
    scenario_id: str,
    ticket: str,
    ticket_dir: Path,
    tests_dir: Path,
    schema_path: Path | None,
    last_updated: str | None,
    template_path: Path,
) -> list[Path]:
    """Render a PREVIEW runbook + receipt — write neither the real runbook nor the stubs."""
    plan = _build_plan(scenarios_path, scenario_id, ticket, ticket_dir, tests_dir, schema_path, last_updated, template_path)
    ticket_dir.mkdir(parents=True, exist_ok=True)
    preview_path = _preview_path(ticket_dir, ticket)
    preview_path.write_text(plan.final_runbook, encoding="utf-8", newline="\n")
    receipt_path = _receipt_path(ticket_dir, ticket)
    receipt_path.write_text(
        json.dumps(_receipt(scenarios_path, scenario_id, tests_dir, plan), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return [preview_path, receipt_path]


def _require_fresh(receipt_path: Path, scenarios_path: Path, plan: _Plan) -> None:
    """Verify a current preview receipt that still matches scenarios + planned outputs on disk."""
    if not receipt_path.is_file():
        raise FileNotFoundError(
            f"preview receipt not found: {receipt_path}. Run 'preview' and review BEFORE 'apply'."
        )
    receipt = _pop.load_json_object(receipt_path)
    if receipt.get("scenarios_sha256") != _pop._sha256_file(scenarios_path):
        raise ValueError("preview receipt is stale: the scenarios JSON changed since the preview (nothing written).")
    existing_sha = _sha_text(plan.existing) if plan.existing is not None else None
    if receipt.get("runbook_existing_sha256") != existing_sha:
        raise ValueError("preview receipt is stale: the runbook changed since the preview (nothing written).")
    if receipt.get("runbook_final_sha256") != _sha_text(plan.final_runbook):
        raise ValueError("preview receipt is stale: the planned runbook no longer matches the reviewed preview.")
    receipt_stubs = {
        str(entry.get("path")): entry.get("sha256")
        for entry in cast("list[Any]", receipt.get("stubs") or [])
        if isinstance(entry, dict)
    }
    planned_stubs = {str(path): _sha_text(source) for path, source in plan.stubs}
    if receipt_stubs != planned_stubs:
        raise ValueError("preview receipt is stale: the planned test stubs no longer match the reviewed preview.")


def run_apply(
    scenarios_path: Path,
    scenario_id: str,
    ticket: str,
    ticket_dir: Path,
    tests_dir: Path,
    schema_path: Path | None,
    last_updated: str | None,
    template_path: Path,
    receipt_path: Path,
    overwrite_stubs: bool,
) -> _Plan:
    """Write the runbook + stubs, gated on a current matching receipt; no partial output."""
    plan = _build_plan(scenarios_path, scenario_id, ticket, ticket_dir, tests_dir, schema_path, last_updated, template_path)
    _require_fresh(receipt_path, scenarios_path, plan)

    # Validate ALL stub targets before writing anything (no partial output on a collision).
    if not overwrite_stubs:
        collisions = [str(path) for path, _source in plan.stubs if path.exists()]
        if collisions:
            raise ValueError(
                "refusing to overwrite existing test stub(s) without --overwrite-stubs:\n  - "
                + "\n  - ".join(collisions)
            )

    ticket_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    plan.runbook_path.write_text(plan.final_runbook, encoding="utf-8", newline="\n")
    for path, source in plan.stubs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="\n")
    return plan


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _add_common(parser: argparse.ArgumentParser) -> None:
    _ = parser.add_argument("--scenarios", required=True, type=Path, help="canonical UAT scenarios JSON path")
    _ = parser.add_argument("--scenario-id", required=True, help="id of the (already-authored) scenario to scaffold")
    _ = parser.add_argument("--ticket", required=True, help="active ticket key, e.g. ABC-123")
    _ = parser.add_argument("--ticket-dir", required=True, type=Path, help="active ticket folder (.jswarm/plans/<TICKET>/)")
    _ = parser.add_argument("--tests-dir", required=True, type=Path, help="project test directory for the scaffolded stubs")
    _ = parser.add_argument("--template", type=Path, help="UAT_TEST_TEMPLATE.md path (reference; default repo template)")
    _ = parser.add_argument("--schema", type=Path, help="schema JSON path (default: engine schema beside this tool)")
    _ = parser.add_argument("--last-updated", help="runbook Last Updated value (default: document.last_updated or 'draft')")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview", help="render a PREVIEW runbook + receipt (writes neither the runbook nor stubs)")
    _add_common(preview)
    _ = preview.add_argument("--preview-dir", type=Path, help="(reserved) directory for preview artifacts; default ticket dir")

    apply_parser = sub.add_parser("apply", help="write the runbook + test stubs (requires the receipt from an approved preview)")
    _add_common(apply_parser)
    _ = apply_parser.add_argument("--receipt", required=True, type=Path, help="preview receipt JSON (apply refuses without a current matching receipt)")
    _ = apply_parser.add_argument("--overwrite-stubs", action="store_true", help="allow overwriting existing test stub files (explicit approval)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        scenarios_path = cast(Path, args.scenarios)
        scenario_id = cast(str, args.scenario_id)
        ticket = cast(str, args.ticket)
        ticket_dir = cast(Path, args.ticket_dir)
        tests_dir = cast(Path, args.tests_dir)
        schema_path = cast("Path | None", args.schema) or _pop._default_schema_path()
        last_updated = cast("str | None", args.last_updated)
        template_path = cast("Path | None", args.template) or DEFAULT_TEMPLATE

        if args.command == "preview":
            written = run_preview(scenarios_path, scenario_id, ticket, ticket_dir, tests_dir, schema_path, last_updated, template_path)
            sys.stdout.write(
                "Preview rendered (runbook + stubs NOT written — review before apply):\n"
                + "\n".join(f"  - {path}" for path in written)
                + "\n"
            )
            return 0
        if args.command == "apply":
            plan = run_apply(
                scenarios_path,
                scenario_id,
                ticket,
                ticket_dir,
                tests_dir,
                schema_path,
                last_updated,
                template_path,
                cast(Path, args.receipt),
                bool(args.overwrite_stubs),
            )
            lines = [f"Scaffolded UAT artifacts for {scenario_id} ({ticket}):"]
            lines.append(f"  runbook: {plan.runbook_path}  ({plan.mode})")
            for path, _source in plan.stubs:
                lines.append(f"  stub:    {path}")
            lines.append(
                "Next: author full assertions during /jGo (TDD red-first). These stubs are skipped "
                "placeholders — do not ship them as proof. Merge authored scenarios back at /jClose."
            )
            sys.stdout.write("\n".join(lines) + "\n")
            return 0
        parser.error(f"unknown command {args.command!r}")
        return 2
    except Exception as error:
        sys.stderr.write(f"ERROR: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
