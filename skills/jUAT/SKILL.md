---
name: jUAT
description: UAT command surface used to run guided acceptance flows.
---

# /jUAT: UAT scenario utilities menu

`/jUAT` is a small, extensible menu of UAT-scenario engine utilities. It is a **thin menu**: each option delegates to an associated skill that does the work.

## Usage

```
/jUAT                  # show this menu and ask which option to run
/jUAT 1                # run option 1 directly
/jUAT extract-assets   # run option 1 by name
/jUAT 1 --pdf <f.pdf> --scenarios <scenarios.json>   # run option 1 with args passed through
/jUAT 2                # run option 2 directly
/jUAT populate-content # run option 2 by name
/jUAT 2 --scenarios <scenarios.json>                 # run option 2 with args passed through
/jUAT 3                # run option 3 directly
/jUAT author-scenario  # run option 3 by name
/jUAT 3 --scenarios <scenarios.json>                 # run option 3 with args passed through
```

If invoked with **no argument**, present the menu below and ask which option to run. If invoked with a **number** or **option key**, route directly to that option's skill. If an **unknown** option is given, show the menu and stop. Pass any extra arguments through to the routed skill.

## Menu

| # | Key | Option | Skill | What it does |
|---|-----|--------|-------|--------------|
| 1 | `extract-assets` | Extract slide images from a PDF | `uat-extract-assets` | Extract PNGs from a user-specified PDF, match each slide labeled with a `UAT.*` scenario id, inject the image path + PDF-extracted data into the scenarios JSON, then re-render so the images embed inline. |
| 2 | `populate-content` | Populate scenario content (preview-gated) | `uat-populate-content` | Draft scenario content (goal, use-case/GWT, walkthrough, `pm_note`) from source material into a proposals sidecar, render a PREVIEW Markdown via the real renderer **without** writing the canonical JSON, and only commit on developer approval. |
| 3 | `author-scenario` | Author a UAT scenario + runbook + test stubs (mid-ticket, preview-gated) | `uat-author-scenario` | Mid-ticket, resolve the active in-progress ticket, author a NEW scenario (or update an existing one), generate the ticket-local `.uat-test.md` runbook, and scaffold collectable unit/integration test stubs (each preview-gated and fail-loud). |

> More options may be added over time. Keep this command a thin menu: add a row here and put the work in a new per-option skill under `.claude/skills/`.

## Routing

- **Option 1 (`1` / `extract-assets`):** invoke the **`uat-extract-assets`** skill and follow its workflow end to end (gather PDF + scenarios paths → dry-run alignment report → confirm → extract + inject → re-render → report). If the user already supplied `--pdf` / `--scenarios` (or other flags), pass them through to the skill. The skill ultimately drives `jswarm/uat-scenarios/extract_uat_assets.py` via `.venv/bin/python`.
- **Option 2 (`2` / `populate-content`):** invoke the **`uat-populate-content`** skill and follow its workflow end to end (gather scenarios + source → draft proposals sidecar → **preview** the merged result via the real renderer → present + await developer approval → **apply** → re-render). The skill MUST NOT `apply` before a `preview` has been shown and approved. Pass any supplied `--scenarios` / `--source` / `--profile` flags through. The skill drives `jswarm/uat-scenarios/populate_scenario_content.py` (`preview` / `apply`) via `.venv/bin/python`.
- **Option 3 (`3` / `author-scenario`):** invoke the **`uat-author-scenario`** skill and follow its workflow end to end (resolve + validate the active in-progress ticket → choose NEW or UPDATE → **preview** the scenario merge → approve → **apply** + re-render → generate the ticket-local `.uat-test.md` runbook → scaffold collectable test stubs → report all paths). The skill MUST NOT write before a `preview` is shown and approved, and MUST fail loud (write nothing) when the active ticket is unresolved, closed/terminal, missing a plan, or its sources disagree. Pass any supplied `--scenarios` flag through. The skill drives `jswarm/uat-scenarios/resolve_active_ticket.py`, `create_uat_scenario.py` / `populate_scenario_content.py`, and `scaffold_uat_tests.py` via `.venv/bin/python`.

## Notes

- Python via `.venv/bin/python` only (system Python is quarantined).
- jQATester browser dispatches (any mode) are governed by the `/jTest` skill's `uat.md` §"jQATester dispatch contract": transport-ladder preflight (`PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.qa_transport ...`) + the thirteen-field handoff template (`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/templates/QA_DISPATCH_HANDOFF_TEMPLATE.md`). This menu never dispatches a browser agent without that contract.
- The generated UAT Markdown is derived/read-only: edit the JSON (the extractor does), then regenerate. The renderer embeds `scenario.image` inline in both the `pm-summary` and `engineering` profiles.

## Next

After the owner walks a round in the portal: if it found something, run
`/jFix <problem>` in this project's agent session. If the round is clean,
run `/jClose`.
