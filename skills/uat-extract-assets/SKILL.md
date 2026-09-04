---
name: uat-extract-assets
description: Extract slide PNGs from a PDF, align them to UAT scenario ids, inject the image path + PDF-extracted data into the scenarios JSON, and embed them inline on re-render
user-invocable: true
triggers:
  - uat extract assets
  - extract uat images
  - uat slide images
  - extract slides to scenarios
argument-hint: "[--pdf <file.pdf>] [--scenarios <scenarios.json>] [--assets-dir <dir>] [--dpi 150] [--dry-run]"
level: 2
---


# uat-extract-assets: PDF slide → scenario image extraction

Extract per-slide PNGs from a PDF and align them to the UAT scenarios JSON. A slide whose text contains a scenario id (e.g. `UAT.UPLOAD.STARTSCREEN`) is rasterized to `<assets-dir>/<id>.png`, and the image path + PDF-extracted provenance are **injected into the scenarios JSON** as `scenario.image`. The renderer then embeds the image inline in both render profiles. This skill is option 1 of the `/jUAT` menu.

## Prerequisites

- **PDF backend:** PyMuPDF (`.venv/bin/pip install pymupdf`). The tool fails loudly with install guidance if no backend is importable.
- **Python:** `.venv/bin/python` only (system Python is quarantined).

## Inputs (ask the user for any not supplied as arguments)

1. **PDF path** (`--pdf`): the slide deck. **REQUIRED.**
2. **Scenarios JSON** (`--scenarios`): the canonical UAT scenarios JSON. **REQUIRED.** For adopters this is the project-supplied scenarios JSON (see the engine README / project localization); do not hardcode it.
3. **Assets dir** (`--assets-dir`, optional): where PNGs are written. Default `<scenarios-parent>/uat-assets`.
4. **DPI** (`--dpi`, optional): raster resolution, default `150`.

## Workflow

1. **Confirm inputs.** Resolve the PDF + scenarios paths from the arguments; ask for any that are missing. Do not guess the scenarios JSON path.

2. **Dry-run first.** Show the alignment report (matched / unmatched / ambiguous / orphan / duplicate) so the user can confirm the slide↔scenario mapping before anything is written:

   ```bash
   .venv/bin/python jswarm/uat-scenarios/extract_uat_assets.py \
     --pdf "<PDF>" --scenarios "<SCENARIOS_JSON>" --dry-run
   ```

   (Use the engine's deployed path inside an adopter project.) Relay the report. If the mapping looks wrong (ambiguous/orphan pages, slides labeled with unknown ids), have the user fix the slide labels or scenario ids and re-run the dry-run; do not proceed on a bad mapping.

3. **Extract + inject.** On confirmation, run without `--dry-run` to rasterize each matched slide and inject `scenario.image` (path + `source{pdf, page, extracted_text}`) into the JSON:

   ```bash
   .venv/bin/python jswarm/uat-scenarios/extract_uat_assets.py \
     --pdf "<PDF>" --scenarios "<SCENARIOS_JSON>"
   ```

4. **Re-render.** The generated Markdown is derived/read-only: regenerate it so the injected images embed inline, then confirm link integrity:

   ```bash
   .venv/bin/python jswarm/uat-scenarios/render-uat-scenarios.py \
     --scenarios "<SCENARIOS_JSON>" --schema jswarm/uat-scenarios/schema/uat-scenarios.schema.json \
     --profile pm-summary --strict-links
   .venv/bin/python jswarm/uat-scenarios/render-uat-scenarios.py \
     --scenarios "<SCENARIOS_JSON>" --schema jswarm/uat-scenarios/schema/uat-scenarios.schema.json \
     --profile engineering --strict-links
   ```

   `--strict-links` fails if any injected `image.path` does not resolve; that catches a PNG that did not land.

5. **Report back.** Summarize: matched slides (id ← page), unmatched scenarios (no slide found), ambiguous/orphan pages, where the PNGs and the updated JSON landed, and that re-render embedded them.

## Behavior notes

- **Token-boundary matching:** `UAT.A` never matches inside `UAT.A.B`; a slide must carry the exact scenario id token.
- **Ambiguous slides** (mentioning two ids) are reported and skipped, never guessed.
- **Duplicate slides** (an id on multiple pages) use the first page; the rest are reported.
- **Minimal diff:** the scenarios JSON is rewritten pretty-printed with key order preserved (only the new `image` blocks change). Rendering and freshness are format-independent.
- The image embed is driven by the JSON `image.path` field, so the JSON remains the single source of truth.
