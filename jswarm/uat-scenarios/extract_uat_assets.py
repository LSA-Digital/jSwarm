#!/usr/bin/env python3
"""Extract slide PNGs from a PDF and align them to UAT scenarios.

A slide *labeled* with a scenario id (its id appears as a token in the slide's text)
is rasterized to ``<assets-dir>/<scenario-id>.png`` and the image path + PDF-extracted
provenance is injected into the canonical scenarios JSON as ``scenario.image``. The
renderer then embeds the image inline (see render-uat-scenarios.py, RD-16).

Matching/alignment is pure (unit-testable without a PDF). Rasterization uses a
pluggable PDF backend — PyMuPDF (``fitz``) today; the function fails loudly with
install guidance if no backend is importable. The scenarios JSON is written back
pretty-printed (indent=2, key order preserved) for a minimal, human-readable diff;
rendering/freshness are format-independent (json.load + hash_canonical).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Protocol, cast

if __package__ in (None, ""):
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from jswarm.dashboard_ui import schema_validate  # noqa: E402
from jswarm.dashboard_ui.schema_validate import SchemaValidationError  # noqa: E402

JsonObject = dict[str, object]
DEFAULT_DPI = 150
_MAX_EXTRACTED_TEXT = 2000


# --------------------------------------------------------------------------- #
# Pure matching / alignment
# --------------------------------------------------------------------------- #
def _id_pattern(scenario_id: str) -> re.Pattern[str]:
    # Token boundary: the id must not be embedded in a longer dotted/dashed token,
    # so 'UAT.A' does not match inside 'UAT.A.B'.
    return re.compile(r"(?<![\w.\-])" + re.escape(scenario_id) + r"(?![\w.\-])")


def align_pages(page_texts: list[str], scenario_ids: list[str]) -> JsonObject:
    """Align PDF pages to scenario ids by token-boundary id matches in page text.

    Returns a report dict: ``matches`` (id -> 0-based page; first page wins),
    ``ambiguous_pages`` (page matched >1 id), ``orphan_pages`` (page matched none),
    ``duplicates`` (extra pages for an already-matched id), ``unmatched_scenarios``.
    """
    patterns = [(scenario_id, _id_pattern(scenario_id)) for scenario_id in scenario_ids]
    matches: dict[str, int] = {}
    ambiguous_pages: list[int] = []
    orphan_pages: list[int] = []
    duplicates: list[JsonObject] = []
    for page_index, text in enumerate(page_texts):
        found: list[str] = []
        for scenario_id, pattern in patterns:
            if pattern.search(text or "") and scenario_id not in found:
                found.append(scenario_id)
        if not found:
            orphan_pages.append(page_index)
        elif len(found) > 1:
            ambiguous_pages.append(page_index)
        else:
            scenario_id = found[0]
            if scenario_id in matches:
                duplicates.append({"page": page_index, "id": scenario_id})
            else:
                matches[scenario_id] = page_index
    unmatched = sorted(scenario_id for scenario_id in scenario_ids if scenario_id not in matches)
    return {
        "matches": matches,
        "ambiguous_pages": ambiguous_pages,
        "orphan_pages": orphan_pages,
        "duplicates": duplicates,
        "unmatched_scenarios": unmatched,
    }


def inject_images(
    data: JsonObject,
    matches: dict[str, int],
    page_texts: list[str],
    pdf_name: str,
    assets_relative: str,
) -> JsonObject:
    """Return a deep copy of ``data`` with ``scenario.image`` set for each matched id.

    ``assets_relative`` is the assets directory relative to the scenarios-JSON dir, so
    the stored ``image.path`` is ``<assets_relative>/<id>.png`` (what the renderer
    expects). The input document is never mutated in place.
    """
    result = copy.deepcopy(data)
    by_id: dict[str, JsonObject] = {}
    scenarios = result.get("scenarios")
    if isinstance(scenarios, list):
        for scenario in scenarios:
            if isinstance(scenario, dict) and isinstance(scenario.get("id"), str):
                by_id[cast(str, scenario["id"])] = cast(JsonObject, scenario)
    base = assets_relative.rstrip("/")
    for scenario_id, page_index in matches.items():
        scenario = by_id.get(scenario_id)
        if scenario is None:
            continue
        title = scenario.get("title")
        alt = title if isinstance(title, str) and title else scenario_id
        raw_text = page_texts[page_index] if 0 <= page_index < len(page_texts) else ""
        extracted = " ".join((raw_text or "").split())[:_MAX_EXTRACTED_TEXT]
        path = f"{base}/{scenario_id}.png" if base else f"{scenario_id}.png"
        scenario["image"] = {
            "path": path,
            "alt": alt,
            "source": {"pdf": pdf_name, "page": page_index + 1, "extracted_text": extracted},
        }
    return result


# --------------------------------------------------------------------------- #
# Pluggable PDF backend
# --------------------------------------------------------------------------- #
class PdfBackend(Protocol):
    def page_count(self) -> int: ...
    def page_text(self, index: int) -> str: ...
    def render_png(self, index: int, out_path: Path, dpi: int) -> None: ...
    def close(self) -> None: ...


class PyMuPDFBackend:
    """PDF backend using PyMuPDF (``fitz``): per-page text + page rasterization."""

    def __init__(self, pdf_path: Path) -> None:
        import fitz  # type: ignore

        self._doc = fitz.open(str(pdf_path))

    def page_count(self) -> int:
        return int(self._doc.page_count)

    def page_text(self, index: int) -> str:
        return str(self._doc[index].get_text())

    def render_png(self, index: int, out_path: Path, dpi: int) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = self._doc[index].get_pixmap(dpi=dpi)
        pixmap.save(str(out_path))

    def close(self) -> None:
        self._doc.close()


def get_backend(pdf_path: Path) -> PdfBackend:
    """Return a usable PDF backend, or raise with install guidance if none is present."""
    try:
        import fitz  # type: ignore  # noqa: F401
    except Exception:
        raise RuntimeError(
            "No PDF backend available. The extractor needs per-page text + rasterization.\n"
            "Install one (PyMuPDF recommended):\n"
            "  .venv/bin/pip install pymupdf"
        )
    return PyMuPDFBackend(pdf_path)


# --------------------------------------------------------------------------- #
# Orchestration
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


def _scenario_ids(data: JsonObject) -> list[str]:
    scenarios = data.get("scenarios")
    ids: list[str] = []
    if isinstance(scenarios, list):
        for scenario in scenarios:
            if isinstance(scenario, dict) and isinstance(scenario.get("id"), str):
                ids.append(cast(str, scenario["id"]))
    return ids


def _write_pretty(data: JsonObject, path: Path) -> None:
    """Write the scenarios JSON back pretty-printed, preserving key order (minimal diff)."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def extract(
    pdf_path: Path,
    scenarios_path: Path,
    schema_path: Path,
    assets_dir: Path,
    dpi: int,
    dry_run: bool,
) -> JsonObject:
    """Run extraction; rasterize matched pages and inject images unless ``dry_run``."""
    data = load_json_object(scenarios_path)
    schema = load_json_object(schema_path)
    errors = schema_validate.validate(data, schema, path="data")
    if errors:
        raise SchemaValidationError(errors)
    scenario_ids = _scenario_ids(data)

    backend = get_backend(pdf_path)
    try:
        page_texts = [backend.page_text(index) for index in range(backend.page_count())]
        alignment = align_pages(page_texts, scenario_ids)
        matches = cast("dict[str, int]", alignment["matches"])
        if not dry_run:
            for scenario_id, page_index in matches.items():
                backend.render_png(page_index, assets_dir / f"{scenario_id}.png", dpi)
    finally:
        backend.close()

    assets_relative = os.path.relpath(assets_dir, scenarios_path.parent).replace(os.sep, "/")
    if not dry_run and matches:
        injected = inject_images(data, matches, page_texts, pdf_path.name, assets_relative)
        post_errors = schema_validate.validate(injected, schema, path="data")
        if post_errors:
            raise SchemaValidationError(post_errors)
        _write_pretty(injected, scenarios_path)

    alignment["assets_relative"] = assets_relative
    alignment["page_count"] = len(page_texts)
    return alignment


def _format_report(alignment: JsonObject, *, dry_run: bool) -> str:
    matches = cast("dict[str, int]", alignment["matches"])
    lines: list[str] = []
    lines.append(f"UAT asset extraction{' (dry-run)' if dry_run else ''} — {alignment.get('page_count', 0)} page(s)")
    lines.append(f"Matched ({len(matches)}):")
    for scenario_id, page_index in matches.items():
        lines.append(f"  - {scenario_id}  <-  page {page_index + 1}")
    unmatched = cast("list[str]", alignment["unmatched_scenarios"])
    lines.append(f"Unmatched scenarios ({len(unmatched)}):")
    for scenario_id in unmatched:
        lines.append(f"  - {scenario_id}")
    ambiguous = cast("list[int]", alignment["ambiguous_pages"])
    if ambiguous:
        lines.append(f"Ambiguous pages (matched >1 id, skipped): {[p + 1 for p in ambiguous]}")
    orphans = cast("list[int]", alignment["orphan_pages"])
    if orphans:
        lines.append(f"Orphan pages (no recognized id): {[p + 1 for p in orphans]}")
    duplicates = cast("list[JsonObject]", alignment["duplicates"])
    if duplicates:
        rendered = [f"page {cast(int, d['page']) + 1}->{d['id']}" for d in duplicates]
        lines.append(f"Duplicate slides (id already matched, skipped): {rendered}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--pdf", required=True, type=Path, help="source PDF (slide deck) path")
    _ = parser.add_argument("--scenarios", required=True, type=Path, help="canonical UAT scenarios JSON path")
    _ = parser.add_argument("--schema", type=Path, help="schema JSON path (default: engine schema beside this tool)")
    _ = parser.add_argument("--assets-dir", type=Path, help="output PNG dir (default: <scenarios-parent>/uat-assets)")
    _ = parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help=f"raster DPI (default {DEFAULT_DPI})")
    _ = parser.add_argument("--dry-run", action="store_true", help="report alignment without writing PNGs or JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        scenarios_path = cast(Path, args.scenarios)
        schema_path = cast("Path | None", args.schema) or (Path(__file__).resolve().parent / "schema" / "uat-scenarios.schema.json")
        assets_dir = cast("Path | None", args.assets_dir) or (scenarios_path.parent / "uat-assets")
        alignment = extract(
            cast(Path, args.pdf),
            scenarios_path,
            schema_path,
            assets_dir,
            cast(int, args.dpi),
            bool(args.dry_run),
        )
        sys.stdout.write(_format_report(alignment, dry_run=bool(args.dry_run)) + "\n")
        return 0
    except Exception as error:
        sys.stderr.write(f"ERROR: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
