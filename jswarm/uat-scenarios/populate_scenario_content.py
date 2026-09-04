#!/usr/bin/env python3
"""Populate UAT scenario content from agent-drafted proposals, preview-gated.

An agent (the ``uat-populate-content`` skill) drafts proposed scenario content — goal,
``use_case``/``gwt`` per ``render_method``, ``walkthrough`` steps/screens, ``pm_note`` —
grounded in source material (e.g. the PDF-extracted ``image.source.extracted_text`` from
``/uat`` option 1) and writes it to a proposals sidecar::

    { "proposals": [ { "id": "<scenario-id>", "content": { <scenario fields to set/merge> } } ] }

This tool turns that sidecar into either a **preview** or an **apply**:

- ``preview`` deep-merges the proposals into the scenarios in memory, schema-validates the
  result, and renders a PREVIEW Markdown via the *real* renderer to ``<stem>.PREVIEW.<profile>.md``
  **without writing the canonical JSON**. This is the mandatory human-review gate — the
  developer sees exactly what would ship before anything is committed.
- ``apply`` re-merges, schema-validates, and writes the populated content into the canonical
  scenarios JSON (pretty ``json.dumps(indent=2)``, key order preserved — minimal diff).

A schema-invalid proposal fails loud in BOTH modes (nothing written): the gate cannot be
bypassed by a malformed draft. The merge is pure (deep copy, never mutates the input);
rendering reuses the exact renderer the engine ships, so the preview is byte-faithful.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

if __package__ in (None, ""):
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from jswarm.dashboard_ui import schema_validate  # noqa: E402
from jswarm.dashboard_ui.schema_validate import SchemaValidationError  # noqa: E402

JsonObject = dict[str, object]
DEFAULT_PREVIEW_PROFILE = "pm-summary"
PREVIEW_PROFILES = ("pm-summary", "engineering")


# --------------------------------------------------------------------------- #
# Pure merge logic
# --------------------------------------------------------------------------- #
def deep_merge(base: JsonObject, overlay: JsonObject) -> JsonObject:
    """Return a deep copy of ``base`` with ``overlay`` recursively merged in.

    Nested objects are deep-merged; lists and scalars are replaced wholesale; new keys
    are appended. Neither input is mutated. Existing keys keep their position (base
    order), so write-back diffs stay minimal.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = deep_merge(cast(JsonObject, existing), cast(JsonObject, value))
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_proposals(data: JsonObject, proposals: list[JsonObject]) -> JsonObject:
    """Return a deep copy of ``data`` with each proposal's ``content`` merged into its scenario.

    A proposal is ``{"id": <scenario-id>, "content": {<fields>}}``. Proposals referencing an
    unknown scenario id fail loud (populating content into a non-existent scenario is a
    mistake). The input document is never mutated.
    """
    result = copy.deepcopy(data)
    by_id: dict[str, JsonObject] = {}
    scenarios = result.get("scenarios")
    if isinstance(scenarios, list):
        for scenario in scenarios:
            if isinstance(scenario, dict) and isinstance(scenario.get("id"), str):
                by_id[cast(str, scenario["id"])] = cast(JsonObject, scenario)

    unknown: list[object] = []
    for proposal in proposals:
        proposal_id = proposal.get("id")
        if not isinstance(proposal_id, str) or proposal_id not in by_id:
            unknown.append(proposal_id)
    if unknown:
        raise ValueError(
            "populate proposals reference unknown scenario id(s): "
            + ", ".join(repr(value) for value in unknown)
        )

    for proposal in proposals:
        proposal_id = cast(str, proposal["id"])
        content = proposal.get("content")
        if not isinstance(content, dict):
            raise ValueError(f"proposal for {proposal_id!r} must carry an object 'content'")
        merged = deep_merge(by_id[proposal_id], cast(JsonObject, content))
        target = by_id[proposal_id]
        target.clear()
        target.update(merged)
    return result


# --------------------------------------------------------------------------- #
# IO helpers
# --------------------------------------------------------------------------- #
def load_json_object(path: Path) -> JsonObject:
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


def load_proposals(path: Path) -> list[JsonObject]:
    """Load and shape-check the proposals sidecar; fail loud on a malformed sidecar."""
    sidecar = load_json_object(path)
    proposals = sidecar.get("proposals")
    if not isinstance(proposals, list):
        raise ValueError(f"{path} must contain a 'proposals' array")
    result: list[JsonObject] = []
    seen_ids: set[str] = set()
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            raise ValueError(f"{path}: proposals[{index}] must be an object")
        proposal_id = proposal.get("id")
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError(f"{path}: proposals[{index}] must carry a non-empty string 'id'")
        if not isinstance(proposal.get("content"), dict):
            raise ValueError(f"{path}: proposals[{index}] must carry an object 'content'")
        # A human-review gate must not silently merge two drafts for the same scenario
        # (the preview would show only the sequentially-merged result, hiding the conflict).
        if proposal_id in seen_ids:
            raise ValueError(
                f"{path}: duplicate proposal id {proposal_id!r} — provide a single proposal per scenario"
            )
        seen_ids.add(proposal_id)
        result.append(cast(JsonObject, proposal))
    return result


def _write_pretty(data: JsonObject, path: Path) -> None:
    """Write the scenarios JSON back pretty-printed, preserving key order (minimal diff)."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _default_schema_path() -> Path:
    return Path(__file__).resolve().parent / "schema" / "uat-scenarios.schema.json"


def _validate(data: JsonObject, schema: JsonObject) -> None:
    """Schema-validate the merged document; raise the aggregated error (the gate)."""
    errors = schema_validate.validate(data, schema, path="data")
    if errors:
        raise SchemaValidationError(errors)


def _load_renderer() -> Any:
    """Import the sibling renderer (its filename has hyphens, so load it by path)."""
    renderer_path = Path(__file__).resolve().parent / "render-uat-scenarios.py"
    spec = importlib.util.spec_from_file_location("render_uat_scenarios_for_populate", renderer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load renderer at {renderer_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _preview_path(scenarios_path: Path, profile: str, preview_dir: Path | None) -> Path:
    name = scenarios_path.name
    stem = name[: -len(".json")] if name.endswith(".json") else name
    directory = preview_dir if preview_dir is not None else scenarios_path.parent
    return directory / f"{stem}.PREVIEW.{profile}.md"


def _receipt_path(scenarios_path: Path, preview_dir: Path | None) -> Path:
    """Deterministic path for the preview receipt that gates ``apply`` (RD-22 / AC-17)."""
    name = scenarios_path.name
    stem = name[: -len(".json")] if name.endswith(".json") else name
    directory = preview_dir if preview_dir is not None else scenarios_path.parent
    return directory / f"{stem}.PREVIEW.receipt.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _merged_text(merged: JsonObject) -> str:
    """The exact bytes ``apply`` writes (pretty indent=2 + trailing newline) — digested for the receipt."""
    return json.dumps(merged, indent=2, ensure_ascii=False) + "\n"


def _validate_links(merged: JsonObject, scenarios_path: Path) -> None:
    """Opt-in strict-link gate (COM-194): run the renderer's link-integrity check on the merged
    document and raise (nothing written) on any dangling ``image.path`` / unknown scenario ref.

    Off by default so the existing ``/uat`` option-2 behavior is unchanged; option 3's UPDATE
    path passes ``--strict-links`` so an update cannot write dangling links into canonical JSON.
    """
    renderer = _load_renderer()
    errors = renderer.validate_link_integrity(merged, scenarios_dir=scenarios_path.parent)
    if errors:
        raise ValueError(
            "link-integrity check failed (strict-links) — nothing written:\n  - " + "\n  - ".join(errors)
        )


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #
def run_preview(
    scenarios_path: Path,
    proposals_path: Path,
    schema_path: Path,
    profile: str,
    preview_dir: Path | None,
    strict_links: bool = False,
) -> list[Path]:
    """Merge proposals in memory, validate, and render PREVIEW Markdown — no JSON write."""
    data = load_json_object(scenarios_path)
    schema = load_json_object(schema_path)
    _validate(data, schema)  # the starting document must itself be valid
    proposals = load_proposals(proposals_path)
    merged = merge_proposals(data, proposals)
    _validate(merged, schema)  # GATE: nothing is rendered if the merged doc is invalid
    if strict_links:
        _validate_links(merged, scenarios_path)  # GATE: no dangling links written (opt-in)

    profiles = list(PREVIEW_PROFILES) if profile == "both" else [profile]
    renderer = _load_renderer()
    written: list[Path] = []
    for one_profile in profiles:
        template_path, _derived_output = renderer.resolve_profile_io(one_profile, None, None, scenarios_path)
        preview_path = _preview_path(scenarios_path, one_profile, preview_dir)
        rendered = renderer.render(
            merged,
            template_path,
            scenarios_path=scenarios_path,
            output_path=preview_path,
        )
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text(rendered, encoding="utf-8", newline="\n")
        written.append(preview_path)

    # RD-22 / AC-17: emit a receipt that mechanically gates `apply`. apply will refuse to write the
    # canonical JSON unless this receipt exists AND still matches the current scenarios + proposals +
    # merged result — so AI-drafted content cannot reach the source of truth without a current,
    # reviewed preview (the gate is enforced by the tool, not only by the skill workflow).
    receipt_path = _receipt_path(scenarios_path, preview_dir)
    receipt: JsonObject = {
        "kind": "uat-populate-preview-receipt",
        "scenarios": str(scenarios_path),
        "scenarios_sha256": _sha256_file(scenarios_path),
        "proposals": str(proposals_path),
        "proposals_sha256": _sha256_file(proposals_path),
        "merged_sha256": hashlib.sha256(_merged_text(merged).encode("utf-8")).hexdigest(),
        "profiles": list(profiles),
        "previews": [str(path) for path in written],
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return written


def _require_fresh_receipt(receipt_path: Path, scenarios_path: Path, proposals_path: Path) -> JsonObject:
    """RD-22 / AC-17: apply MUST be preceded by a current preview. Verify the receipt exists and still
    matches the scenarios + proposals on disk; raise (nothing written) on a missing or stale receipt."""
    if not receipt_path.is_file():
        raise FileNotFoundError(
            f"preview receipt not found: {receipt_path}. Run 'preview' and review the output BEFORE 'apply' — "
            "apply will not write the canonical JSON without a current reviewed preview."
        )
    receipt = load_json_object(receipt_path)
    if receipt.get("scenarios_sha256") != _sha256_file(scenarios_path):
        raise ValueError(
            "preview receipt is stale: the scenarios JSON changed since the preview was produced. "
            "Re-run 'preview' and review again before 'apply' (nothing was written)."
        )
    if receipt.get("proposals_sha256") != _sha256_file(proposals_path):
        raise ValueError(
            "preview receipt is stale: the proposals changed since the preview was produced. "
            "Re-run 'preview' and review again before 'apply' (nothing was written)."
        )
    return receipt


def run_apply(
    scenarios_path: Path,
    proposals_path: Path,
    schema_path: Path,
    receipt_path: Path,
    strict_links: bool = False,
) -> JsonObject:
    """Merge proposals, validate, and write the populated content into the canonical JSON.

    The write is gated on a current preview receipt (the mandatory human-review gate): apply refuses
    unless ``preview`` was run and neither the scenarios nor the proposals have changed since, and the
    merged result still byte-matches what was reviewed.
    """
    receipt = _require_fresh_receipt(receipt_path, scenarios_path, proposals_path)
    data = load_json_object(scenarios_path)
    schema = load_json_object(schema_path)
    _validate(data, schema)
    proposals = load_proposals(proposals_path)
    merged = merge_proposals(data, proposals)
    _validate(merged, schema)  # GATE: nothing is written if the merged doc is invalid
    if strict_links:
        _validate_links(merged, scenarios_path)  # GATE: no dangling links written (opt-in)
    merged_text = _merged_text(merged)
    if receipt.get("merged_sha256") != hashlib.sha256(merged_text.encode("utf-8")).hexdigest():
        raise ValueError(
            "preview receipt is stale: the merged result no longer matches the reviewed preview. "
            "Re-run 'preview' and review again before 'apply' (nothing was written)."
        )
    # Write the exact bytes that were previewed/reviewed.
    scenarios_path.write_text(merged_text, encoding="utf-8", newline="\n")
    return merged


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview", help="render a PREVIEW Markdown of the merged content (no JSON write)")
    _ = preview.add_argument("--scenarios", required=True, type=Path, help="canonical UAT scenarios JSON path")
    _ = preview.add_argument("--proposals", required=True, type=Path, help="proposals sidecar JSON path")
    _ = preview.add_argument("--schema", type=Path, help="schema JSON path (default: engine schema beside this tool)")
    _ = preview.add_argument(
        "--profile",
        choices=[*PREVIEW_PROFILES, "both"],
        default=DEFAULT_PREVIEW_PROFILE,
        help=f"preview profile (default {DEFAULT_PREVIEW_PROFILE}); 'both' renders each profile",
    )
    _ = preview.add_argument("--preview-dir", type=Path, help="directory for the PREVIEW Markdown (default: scenarios dir)")
    _ = preview.add_argument("--strict-links", action="store_true", help="also fail loud (write nothing) on a dangling image.path / unknown scenario ref (opt-in; off keeps legacy behavior)")

    apply_parser = sub.add_parser(
        "apply",
        help="write the merged content into the canonical scenarios JSON (requires the receipt from an approved preview)",
    )
    _ = apply_parser.add_argument("--scenarios", required=True, type=Path, help="canonical UAT scenarios JSON path")
    _ = apply_parser.add_argument("--proposals", required=True, type=Path, help="proposals sidecar JSON path")
    _ = apply_parser.add_argument("--schema", type=Path, help="schema JSON path (default: engine schema beside this tool)")
    _ = apply_parser.add_argument(
        "--receipt",
        required=True,
        type=Path,
        help="preview receipt JSON emitted by 'preview' (apply refuses without a current matching receipt — the review gate)",
    )
    _ = apply_parser.add_argument("--strict-links", action="store_true", help="also fail loud (write nothing) on a dangling image.path / unknown scenario ref (opt-in; off keeps legacy behavior)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        scenarios_path = cast(Path, args.scenarios)
        proposals_path = cast(Path, args.proposals)
        schema_path = cast("Path | None", args.schema) or _default_schema_path()
        if args.command == "preview":
            written = run_preview(
                scenarios_path,
                proposals_path,
                schema_path,
                cast(str, args.profile),
                cast("Path | None", args.preview_dir),
                bool(getattr(args, "strict_links", False)),
            )
            sys.stdout.write(
                "Preview rendered (canonical JSON NOT modified — review before apply):\n"
                + "\n".join(f"  - {path}" for path in written)
                + "\n"
            )
            return 0
        if args.command == "apply":
            _ = run_apply(
                scenarios_path,
                proposals_path,
                schema_path,
                cast(Path, args.receipt),
                bool(getattr(args, "strict_links", False)),
            )
            sys.stdout.write(f"Applied populated content to {scenarios_path} (pretty JSON; re-render to refresh Markdown).\n")
            return 0
        parser.error(f"unknown command {args.command!r}")
        return 2
    except Exception as error:
        sys.stderr.write(f"ERROR: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
