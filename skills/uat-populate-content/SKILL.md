---
name: uat-populate-content
description: Draft UAT scenario content (goal, use-case/GWT, walkthrough, pm_note) from source material into a proposals sidecar, PREVIEW the merged result via the real renderer, and only commit to the canonical JSON after the developer approves the preview
user-invocable: true
triggers:
  - uat populate content
  - populate scenario content
  - draft uat scenarios
  - uat content from pdf
argument-hint: "[--scenarios <scenarios.json>] [--source <file|UAT.ID>] [--profile pm-summary|engineering|both]"
level: 2
---


# uat-populate-content: preview-gated scenario content authoring

Turn source material into drafted UAT scenario content **with a mandatory human-review preview before anything is committed** to the canonical scenarios JSON. This skill is option 2 of the `/jUAT` menu (RD-22). It pairs with option 1 (`uat-extract-assets`): option 1 injects each slide's image + PDF-extracted text into `scenario.image.source.extracted_text`; this skill can use that extracted text (and/or a source doc) to draft the scenario's *content*.

The hard rule: **never call `apply` before a `preview` has been produced and approved by the developer.** The preview is rendered by the real engine renderer, so the developer sees exactly what will ship. A schema-invalid draft fails loud in both modes: the gate cannot be bypassed.

## Prerequisites

- **Python:** `.venv/bin/python` only (system Python is quarantined).
- Renderer + this tool are stdlib + jinja2 (no PDF backend needed for this skill).

## Inputs (ask the user for any not supplied)

1. **Scenarios JSON** (`--scenarios`): the canonical UAT scenarios JSON. **REQUIRED.** For adopters this is the project-supplied scenarios JSON (engine README / project localization); do not hardcode it.
2. **Source material** (`--source`, optional): a doc/notes to ground the drafts, and/or the `image.source.extracted_text` already present on scenarios from option 1. If none is supplied, use the scenario's existing fields + the extracted text already in the JSON.
3. **Preview profile** (`--profile`, optional): `pm-summary` (default), `engineering`, or `both`.

## Workflow

1. **Gather + scope.** Resolve the scenarios JSON path (ask if missing, do not guess). Identify which scenario id(s) to populate and read their current content + any `image.source.extracted_text` so drafts extend rather than fight existing data.

2. **Draft proposals into a sidecar.** Write a proposals sidecar next to the scenarios JSON (e.g. `<stem>.populate-proposals.json`). Ground every field in the source; do not invent behavior:

   ```json
   {
     "proposals": [
       {
         "id": "UAT.UPLOAD.STARTSCREEN",
         "content": {
           "goal": "...",
           "render_method": "use_case",
           "use_case": { "trigger": "...", "main_flow": ["..."] },
           "pm_note": "..."
         }
       }
     ]
   }
   ```

   Merge semantics: nested objects deep-merge, lists/scalars are replaced, new fields are added. Only include fields you intend to set. Unknown scenario ids fail loud.

3. **Preview (the gate).** Render the merged result WITHOUT touching the canonical JSON:

   ```bash
   .venv/bin/python jswarm/uat-scenarios/populate_scenario_content.py preview \
     --scenarios "<SCENARIOS_JSON>" --proposals "<PROPOSALS_SIDECAR>" \
     --schema jswarm/uat-scenarios/schema/uat-scenarios.schema.json --profile both
   ```

   (Use the engine's deployed path inside an adopter project.) This writes `<stem>.PREVIEW.<profile>.md` **and** a `<stem>.PREVIEW.receipt.json` (digests of the scenarios JSON, the proposals, and the merged result), and leaves the scenarios JSON byte-unchanged. If the merged document is schema-invalid the command fails loud and writes nothing (no receipt); fix the proposals and re-preview. The receipt is what mechanically gates `apply`.

4. **Present + wait for approval.** Show the developer the PREVIEW Markdown (the rendered result, not the raw JSON) and the proposed field changes. **Wait for explicit approval or edits.** Loop back to step 2/3 on edits. Do NOT proceed to apply without approval. (Put any preview artifacts the developer will review under the ticket's plan folder, not `/tmp`.)

5. **Apply (only after approval).** Commit the merged content into the canonical JSON (pretty `indent=2`, key order preserved). `apply` **requires** the receipt the preview emitted and refuses if the scenarios or proposals changed since (re-preview if so):

   ```bash
   .venv/bin/python jswarm/uat-scenarios/populate_scenario_content.py apply \
     --scenarios "<SCENARIOS_JSON>" --proposals "<PROPOSALS_SIDECAR>" \
     --schema jswarm/uat-scenarios/schema/uat-scenarios.schema.json \
     --receipt "<SCENARIOS_STEM>.PREVIEW.receipt.json"
   ```

6. **Re-render for real + verify.** The generated Markdown is derived/read-only: regenerate both profiles and confirm link integrity:

   ```bash
   .venv/bin/python jswarm/uat-scenarios/render-uat-scenarios.py \
     --scenarios "<SCENARIOS_JSON>" --schema jswarm/uat-scenarios/schema/uat-scenarios.schema.json \
     --profile pm-summary --strict-links
   .venv/bin/python jswarm/uat-scenarios/render-uat-scenarios.py \
     --scenarios "<SCENARIOS_JSON>" --schema jswarm/uat-scenarios/schema/uat-scenarios.schema.json \
     --profile engineering --strict-links
   ```

7. **Report back.** Summarize which scenarios were populated, where the preview landed, that the developer approved before apply, and that the re-render passed `--strict-links`.

## Behavior notes

- **Preview before apply is mandatory, and mechanically enforced.** The skill MUST NOT run `apply` until a `preview` has been shown and approved. Beyond that workflow rule, the tool itself refuses: `apply --receipt` fails unless the preview receipt exists and still matches the current scenarios + proposals + merged result, so a stale or absent preview cannot be applied. The preview never writes the canonical JSON.
- **Fail-loud gate:** schema-invalid proposals fail in BOTH `preview` and `apply`; nothing is written, so a malformed draft cannot slip through.
- **Pure deep-merge:** the tool merges on a deep copy and never mutates the input; the write-back is pretty `indent=2` with key order preserved (minimal diff).
- **JSON is the source of truth:** edit the JSON via this tool, then regenerate the Markdown; never hand-edit the generated `.md`.
- **Grounding:** drafts must be grounded in the source material / existing scenario fields; the gate ensures a human verifies the rendered result before it ships.
