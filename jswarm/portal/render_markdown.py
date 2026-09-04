"""COM-389 Phase 2 — deterministic Markdown projection of a contract review.

Renders the SC-12 review format (sections 1-8 summary frame + full contract
body with stable section anchors) from the normalized view model produced by
:mod:`jswarm.portal.view_model`.

Determinism contract: no timestamps, no random ids, sorted iteration —
identical input documents produce byte-identical Markdown.

CLI:
    .venv/bin/python -m jswarm.portal.render_markdown \
        --contract <contract.json> [--manifest <manifest.json>] --out <review.md>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jinja2 import Environment, StrictUndefined, Undefined

from jswarm.portal.view_model import build_view_model

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "contract-review.md.j2"


def _escape_cell(value: str) -> str:
    """Escape pipe + newline so table cells never break the table grid."""
    return value.replace("|", "\\|").replace("\n", " ")


def _scope_rows(view_model: dict) -> list[dict]:
    in_scope = view_model["scope"]["in_scope"]
    out_rows = view_model["scope"]["out_of_scope"]
    out_cells = [
        f"{row['item']}: {row['reason']}" if row["reason"] else row["item"]
        for row in out_rows
    ]
    size = max(len(in_scope), len(out_cells), 1)
    rows = []
    for i in range(size):
        left = in_scope[i] if i < len(in_scope) else ""
        right = out_cells[i] if i < len(out_cells) else ""
        rows.append({"in_scope": left, "out_of_scope": right})
    return rows


def _revision_policy_rows(view_model: dict) -> list[tuple[str, str]]:
    policy = view_model["gate"].get("revision_policy") or {}
    # Tolerate both object and (legacy) prose forms; render object fields in
    # document order, prose as a single row.
    if isinstance(policy, str):
        return [("Revision policy", policy)] if policy else []
    rows = []
    for key, value in policy.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=False)
        rows.append((key.replace("_", " ").capitalize(), str(value)))
    return rows


def _sections_recursive(sections: list[dict]) -> list[dict]:
    """Flatten the section tree for the FULL CONTRACT body, numbered stably."""
    out = []
    for index, section in enumerate(sections, start=1):
        subsections = section.get("subsections", [])
        node = {
            "section_id": section["section_id"],
            "number": str(index),
            "title": section["title"],
            "body": section["body"],
            "subsections": subsections,
            "subsection_titles": [s["title"] for s in subsections],
        }
        node["subsections"] = [
            {**sub, "index": sub_index, "number": f"{index}.{sub_index}"}
            for sub_index, sub in enumerate(subsections, start=1)
        ]
        out.append(node)
    return out


def render_markdown(view_model: dict) -> str:
    """Render the SC-12 review Markdown from a normalized view model."""
    environment = Environment(
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
        undefined=StrictUndefined,
    )
    environment.filters["escape_cell"] = _escape_cell
    template = environment.from_string(TEMPLATE_PATH.read_text(encoding="utf-8"))

    context = {
        "vm": dict(view_model),
        "scope_rows": _scope_rows(view_model),
        "revision_policy_rows": _revision_policy_rows(view_model),
        "sections_recursive": _sections_recursive(view_model["sections"]),
    }
    context["vm"].update(
        {
            "scope_rows": context["scope_rows"],
            "revision_policy_rows": context["revision_policy_rows"],
            "sections_recursive": context["sections_recursive"],
        }
    )
    return template.render(**context)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="jswarm.portal.render_markdown",
        description="Render the deterministic SC-12 contract-review Markdown.",
    )
    parser.add_argument("--contract", required=True, help="path to the contract JSON")
    parser.add_argument("--manifest", default=None, help="optional publication-manifest JSON")
    parser.add_argument("--out", required=True, type=Path, help="output Markdown path")
    args = parser.parse_args(argv)

    try:
        view_model = build_view_model(args.contract, args.manifest)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(view_model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(markdown, encoding="utf-8", newline="\n")
    print(f"rendered: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
