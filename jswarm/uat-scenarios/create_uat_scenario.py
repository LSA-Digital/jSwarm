#!/usr/bin/env python3
"""Author a NEW canonical UAT scenario, preview-gated (/uat option 3 create path).

Symmetric twin of ``populate_scenario_content.py``:

- populate UPDATES an EXISTING scenario (and rejects unknown ids).
- create INSERTS a NEW scenario into ``scenarios[]`` (and rejects ids that already exist —
  authoring over an existing scenario is the update path).

It reuses the *exact* preview/apply receipt gate, schema validation, renderer, and pretty
write-back of the populate tool, and additionally runs the renderer's link-integrity check
(the ``--strict-links`` semantics) so a dangling ``image.path`` or unknown scenario
reference fails loud before any write. A schema-invalid draft, a dangling link, or a
duplicate id all fail loud in BOTH modes (nothing written) — the human-review gate cannot be
bypassed by a malformed draft.

Proposals sidecar shape (identical to populate's, so the skill can target either tool)::

    { "proposals": [ { "id": "<new-scenario-id>", "content": { <scenario fields> } } ] }

The proposal ``id`` is authoritative; any ``id`` inside ``content`` is ignored.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

# When invoked as ``.venv/bin/python jswarm/uat-scenarios/create_uat_scenario.py`` (or imported
# by a test via spec loader), make both this script's directory (for the sibling engine tools)
# and the repository root (for shared dashboard helpers) importable.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
for _path in (str(_HERE), str(_REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import populate_scenario_content as _pop  # noqa: E402  (sibling engine tool — reuse its review gate verbatim)

JsonObject = dict[str, object]

# Reuse the populate engine's vocabulary so create/preview/apply behave identically.
PREVIEW_PROFILES = _pop.PREVIEW_PROFILES
DEFAULT_PREVIEW_PROFILE = _pop.DEFAULT_PREVIEW_PROFILE
load_json_object = _pop.load_json_object
load_proposals = _pop.load_proposals


# --------------------------------------------------------------------------- #
# Pure insert logic (mirror of populate.merge_proposals; appends instead of merging)
# --------------------------------------------------------------------------- #
def create_scenarios(data: JsonObject, proposals: list[JsonObject]) -> JsonObject:
    """Return a deep copy of ``data`` with each proposal inserted as a NEW scenario.

    A proposal is ``{"id": <new-id>, "content": {<fields>}}``. The id must NOT already exist
    (creating over an existing scenario is the update path — fail loud). Any ``id`` inside
    ``content`` is ignored; the proposal id is authoritative. The input is never mutated.
    """
    result = copy.deepcopy(data)
    scenarios = result.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("scenarios JSON must contain a 'scenarios' array to create into")
    scenario_list = cast(list[Any], scenarios)
    existing_ids: set[str] = {
        cast(str, scenario["id"])
        for scenario in scenario_list
        if isinstance(scenario, dict) and isinstance(scenario.get("id"), str)
    }

    duplicates: list[object] = []
    for proposal in proposals:
        proposal_id = proposal.get("id")
        if isinstance(proposal_id, str) and proposal_id in existing_ids:
            duplicates.append(proposal_id)
    if duplicates:
        raise ValueError(
            "create proposals reference scenario id(s) that already exist (use the update path): "
            + ", ".join(repr(value) for value in duplicates)
        )

    for proposal in proposals:
        proposal_id = cast(str, proposal["id"])
        content = proposal.get("content")
        if not isinstance(content, dict):
            raise ValueError(f"proposal for {proposal_id!r} must carry an object 'content'")
        # The proposal id is authoritative: drop any stray content 'id' so the two cannot diverge.
        body = {key: copy.deepcopy(value) for key, value in cast(JsonObject, content).items() if key != "id"}
        new_scenario: JsonObject = {"id": proposal_id, **body}
        scenario_list.append(new_scenario)
        existing_ids.add(proposal_id)  # guard against an in-batch duplicate of a just-created id
    return result


# --------------------------------------------------------------------------- #
# Gate: strict-link validation reusing the engine renderer's check (NFR-006)
# --------------------------------------------------------------------------- #
def _validate_links(merged: JsonObject, scenarios_path: Path, renderer: Any) -> None:
    """Run the renderer's link-integrity check (``--strict-links`` semantics); raise on any
    dangling ``image.path`` or unknown scenario reference (nothing is written by the caller)."""
    errors = cast("list[str]", renderer.validate_link_integrity(merged, scenarios_dir=scenarios_path.parent))
    if errors:
        raise ValueError(
            "link-integrity check failed (strict-links) — nothing written:\n  - " + "\n  - ".join(errors)
        )


# --------------------------------------------------------------------------- #
# Subcommands (mirror populate.run_preview / run_apply; reuse its path/receipt helpers)
# --------------------------------------------------------------------------- #
def run_preview(
    scenarios_path: Path,
    proposals_path: Path,
    schema_path: Path,
    profile: str,
    preview_dir: Path | None,
) -> list[Path]:
    """Insert the new scenario in memory, validate (schema + links), and render PREVIEW
    Markdown plus a gating receipt — no canonical JSON write."""
    data = load_json_object(scenarios_path)
    schema = load_json_object(schema_path)
    _pop._validate(data, schema)  # the starting document must itself be valid
    proposals = load_proposals(proposals_path)
    merged = create_scenarios(data, proposals)
    _pop._validate(merged, schema)  # GATE: schema — nothing rendered if the merged doc is invalid
    renderer = _pop._load_renderer()
    _validate_links(merged, scenarios_path, renderer)  # GATE: strict-links

    profiles = list(PREVIEW_PROFILES) if profile == "both" else [profile]
    written: list[Path] = []
    for one_profile in profiles:
        template_path, _derived_output = renderer.resolve_profile_io(one_profile, None, None, scenarios_path)
        preview_path = _pop._preview_path(scenarios_path, one_profile, preview_dir)
        rendered = renderer.render(
            merged,
            template_path,
            scenarios_path=scenarios_path,
            output_path=preview_path,
        )
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text(rendered, encoding="utf-8", newline="\n")
        written.append(preview_path)

    # Emit a receipt that mechanically gates `apply` (same scheme as populate; apply refuses
    # unless this receipt still matches the scenarios + proposals + merged result on disk).
    receipt_path = _pop._receipt_path(scenarios_path, preview_dir)
    receipt: JsonObject = {
        "kind": "uat-create-preview-receipt",
        "scenarios": str(scenarios_path),
        "scenarios_sha256": _pop._sha256_file(scenarios_path),
        "proposals": str(proposals_path),
        "proposals_sha256": _pop._sha256_file(proposals_path),
        "merged_sha256": hashlib.sha256(_pop._merged_text(merged).encode("utf-8")).hexdigest(),
        "profiles": list(profiles),
        "previews": [str(path) for path in written],
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return written


def run_apply(scenarios_path: Path, proposals_path: Path, schema_path: Path, receipt_path: Path) -> JsonObject:
    """Insert the new scenario, re-validate (schema + links), and write it into the canonical JSON.

    Gated on a current preview receipt: apply refuses unless ``preview`` was run and neither the
    scenarios nor the proposals have changed since, and the merged result still byte-matches what
    was reviewed.
    """
    receipt = _pop._require_fresh_receipt(receipt_path, scenarios_path, proposals_path)
    data = load_json_object(scenarios_path)
    schema = load_json_object(schema_path)
    _pop._validate(data, schema)
    proposals = load_proposals(proposals_path)
    merged = create_scenarios(data, proposals)
    _pop._validate(merged, schema)  # GATE: schema
    renderer = _pop._load_renderer()
    _validate_links(merged, scenarios_path, renderer)  # GATE: strict-links (defense in depth)
    merged_text = _pop._merged_text(merged)
    if receipt.get("merged_sha256") != hashlib.sha256(merged_text.encode("utf-8")).hexdigest():
        raise ValueError(
            "preview receipt is stale: the merged result no longer matches the reviewed preview. "
            "Re-run 'preview' and review again before 'apply' (nothing was written)."
        )
    # Write the exact bytes that were previewed/reviewed.
    scenarios_path.write_text(merged_text, encoding="utf-8", newline="\n")
    return merged


# --------------------------------------------------------------------------- #
# CLI (mirror of populate's parser)
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview", help="render a PREVIEW Markdown of the new scenario (no JSON write)")
    _ = preview.add_argument("--scenarios", required=True, type=Path, help="canonical UAT scenarios JSON path")
    _ = preview.add_argument("--proposals", required=True, type=Path, help="proposals sidecar JSON path (new scenario draft)")
    _ = preview.add_argument("--schema", type=Path, help="schema JSON path (default: engine schema beside this tool)")
    _ = preview.add_argument(
        "--profile",
        choices=[*PREVIEW_PROFILES, "both"],
        default=DEFAULT_PREVIEW_PROFILE,
        help=f"preview profile (default {DEFAULT_PREVIEW_PROFILE}); 'both' renders each profile",
    )
    _ = preview.add_argument("--preview-dir", type=Path, help="directory for the PREVIEW Markdown (default: scenarios dir)")

    apply_parser = sub.add_parser(
        "apply",
        help="insert the new scenario into the canonical scenarios JSON (requires the receipt from an approved preview)",
    )
    _ = apply_parser.add_argument("--scenarios", required=True, type=Path, help="canonical UAT scenarios JSON path")
    _ = apply_parser.add_argument("--proposals", required=True, type=Path, help="proposals sidecar JSON path (new scenario draft)")
    _ = apply_parser.add_argument("--schema", type=Path, help="schema JSON path (default: engine schema beside this tool)")
    _ = apply_parser.add_argument(
        "--receipt",
        required=True,
        type=Path,
        help="preview receipt JSON emitted by 'preview' (apply refuses without a current matching receipt — the review gate)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        scenarios_path = cast(Path, args.scenarios)
        proposals_path = cast(Path, args.proposals)
        schema_path = cast("Path | None", args.schema) or _pop._default_schema_path()
        if args.command == "preview":
            written = run_preview(
                scenarios_path,
                proposals_path,
                schema_path,
                cast(str, args.profile),
                cast("Path | None", args.preview_dir),
            )
            sys.stdout.write(
                "Preview rendered (canonical JSON NOT modified — review before apply):\n"
                + "\n".join(f"  - {path}" for path in written)
                + "\n"
            )
            return 0
        if args.command == "apply":
            _ = run_apply(scenarios_path, proposals_path, schema_path, cast(Path, args.receipt))
            sys.stdout.write(f"Created new scenario in {scenarios_path} (pretty JSON; re-render to refresh Markdown).\n")
            return 0
        parser.error(f"unknown command {args.command!r}")
        return 2
    except Exception as error:
        sys.stderr.write(f"ERROR: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
