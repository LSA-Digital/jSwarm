
# Authoring a UAT round the owner can actually work

This is the orchestrator's contract for building the `canonical_manifest` that
`/jTest uat prepare` seals. Author it, check it, then prepare: the check is
cheap and the alternative is discovering a shape error at cutover.

The deployed [feedback result contract](feedback-result-contract.json) is the
single authority for result values, ordering, aliases, defaults, nullability,
and projections. Inspect the same contract and its SHA with:

```bash
.venv/bin/python -m jswarm.uat_feedback result-contract --json
```

Round production belongs to F-26; the portal that renders the result belongs to
F-15. Portal-side detail lives in `docs/tools/fix-decisions/uat-rounds.md`.

## Check before you prepare

```bash
.venv/bin/python -m jswarm.uat_round_materialize preflight-manifest \
  --request .jswarm/plans/<KEY>/<KEY>.uat-prepare-request.json
```

It accepts a bare manifest or a request wrapping one, writes nothing, and
reports every problem at once with the step it is in and how to fix it. A clean
run prints the journey, step, scenario, and GWT-block counts; read them, since
a manifest can be valid and still not be the round you meant.

## Choose the round source

Use the project's localized executable UAT-scenarios E2E source as the preferred
basis for round instructions: it carries the end-to-end scenarios from the
user's perspective. Resolve its exact path through the project's `/jTest`
localization hook and E2E manifest, then preserve canonical scenario/GWT
lineage when authoring the round.

Do not start by transforming a prior round manifest when that executable source
exists. A prior round is fallback context only; apply the owner-facing content
rules in [Best practice: UAT round content writing](#best-practice-uat-round-content-writing)
to every newly authored instruction.

## The shape

A round is `uat-canonical-package@2`: journeys hold **scenarios** and **steps**,
and a step points back at the scenarios it exercises.

```json
{
  "journeys": [{
    "journey_id": "uat-391-2",
    "title": "Owner saves feedback and the consumer processes the exact bytes",
    "scenarios": [{
      "scenario_id": "UAT-391-2",
      "title": "Save feedback",
      "gwt": [{
        "gwt_ref": "<sha256>", "sha256": "<same sha256>",
        "given": ["clause 1", "clause 2"],
        "when":  ["clause A"],
        "then":  ["result X", "result Y"]
      }]
    }],
    "steps": [{
      "step_id": "uat-391-2-step-02", "ordinal": 2,
      "name": "Enter a complete valid journey draft",
      "instruction": "Change one journey entry with a valid outcome, severity, disposition, and comment.",
      "expected_outcome": "Save enables only once the draft is dirty and valid.",
      "scenario_links": [
        {"scenario_id": "UAT-391-2", "gwt_refs": ["<digest-a>"]},
        {"scenario_id": "UAT-391-6", "gwt_refs": ["<digest-b>", "<digest-c>"]}
      ]
    }]
  }]
}
```

For a newly authored or resealed package, every step must carry a non-empty
`assessment_options` array validated against the deployed master. Use only the
master's canonical identifiers, preserve its order, include every mandatory
safety choice, and add the optional non-applicable choice only when the step's
requirement allows it. Do not copy the option values into this document; run
`preflight-manifest` and the `result-contract --json` command above instead.
Missing options are legacy-read-only: they remain valid when reading an existing
sealed package, but they must block first issue and explicit reseal rather than
being emitted or silently inserted.

Every `known_items[]` entry must carry `text`, exactly one `source_ref` from the
manifest's `known_sources_checked`, and `step_refs` containing unique `step_id`
values that exist in that journey. A known item without step references is not
traceable to the walk and is incomplete.

## Rules that are easy to get wrong

**Steps are what the owner does in the app.** Not test-runner actions, fixture
commands, or digest checks. If a card would read as internal machinery, it does
not belong in the round. `name`, `instruction`, and `expected_outcome` are all
required and all owner-facing; an id is not a name.

**`name` is a short DESCRIPTION, never the ordinal restated.** The portal
renders the card header as `Step <ordinal> · <name>`, so `name: "Step 1"`
produces the broken header "Step 1 · Step 1". Write what the step does in a few words, such as `"Upload the EPC PDF"` or
`"Build from the document row"`, the way a checklist line would read.

**`expected_outcome` is ONE sentence, then short bullets for observables.**
Never a wall of text, and never the whole journey's outcome pasted onto every
step (a long journey-level summary copied onto every one of that journey's
step cards is the shape to avoid). Shape:

```
One sentence stating the outcome of THIS step.
- short observable, if any
- short observable, if any
```

Each step's outcome is its own; journey-level outcomes belong on the journey's
outcome atom, not copied per step. When transforming a prior round's manifest
into a new one, re-read every step's `name` and `expected_outcome` as the owner
will see them rendered; inherited fields carry inherited mistakes.

**`step_id` is stable identity.** Feedback is keyed by it, so it must be unique
across the whole round and must not be recomputed from wording or current order.
`ordinal` is presentation order and may change; `step_id` may not.

**Given/when/then are ordered arrays of independent length.** Real acceptance
criteria are rarely one clause each. Write `["only clause"]` rather than a bare
string; a scalar is rejected.

**Lineage is nested, never parallel.** A step has no top-level `gwt_refs`. Every
reference sits inside the `{scenario_id, gwt_refs[]}` entry for the scenario that
owns it, so a reference always resolves in its own scenario. Parallel
`scenario_links[]` and `gwt_refs[]` lists lose the association, which is why the
shape rejects them. A step may link several scenarios, and each link several
blocks; link order and reference order are canonical.

**The digest is content.** `gwt_ref` equals `sha256` equals the SHA-256 of the
exact clause arrays:

```python
payload = json.dumps({"given": given, "when": when, "then": then},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
digest = hashlib.sha256(payload).hexdigest()
```

Order and exact bytes count, so recompute after **any** wording change. Editing a
clause and keeping the old digest is the most common authoring defect, and the
pre-flight catches it.

**Link scenarios that are genuinely exercised.** Lineage closes the loop back to
acceptance, so a decorative link is worse than a missing one: it claims coverage
the step does not provide. If a step touches durable draft state, link the
scenario about durable draft state.

## Best practice: UAT round content writing

**Journey shape:** ONE
document is each journey's end-to-end thread; other documents may be uploaded
within it, but a single named document travels the whole journey the way a real
user's would. Steps are SPECIFIC BUILD ACTIONS; do not atomize into minor
observe-and-report steps; observations belong in the action step's expected
outcome, and fewer steps beat granular ones. Assign DIFFERENT documents to
different journeys so the round exercises different pipeline permutations
(randomized coverage through clear user-shaped arcs).


Owner-taught rules (rounds 016-017); every one of these was a real
mid-walk complaint. Apply them to every journey and step before sealing:

1. **Number the journeys.** Titles read `Journey N: <name>`. The owner
   identifies journeys by number when reporting; an unnumbered list cannot be
   referenced.
2. **NAME the artifacts, never "the named documents" / "one of the listed
   fixtures".** Lazy indirection forces the owner to hunt. Write the exact
   filename in backticks every time it is needed, even when repeating it.
3. **Cross-reference reuse explicitly.** When a step reuses earlier work, say
   "the SAME documents used in Journey X step Y (`file-a.pdf`, `file-b.md`)",
   both the reference AND the names. Add a fallback for skipped prerequisites
   ("if you skipped Journey X, build one fresh from `file` first").
4. **`name` is a short description, never the ordinal restated** (see the rule
   above); **`expected_outcome` is one sentence plus short observable bullets**,
   per step, never a journey blob.
5. **Read every step as the owner will see it rendered** before sealing: the
   card header, the instruction, the outcome. If any field makes the reader ask
   "what/which?", it is not done.
6. **Never reference "the normal flow", "the available action", or any other
   implied knowledge.** Name the concrete UI surface and control: which editor
   to open, which button to select, what appears next ("open the Inputs editor
   and select Build", not "continue through the normal Inputs flow"). Sweep
   sealed instructions for "normal", "available", "as appropriate", "the
   named" before cutover.
7. **Show the fixed-defect ledger identifiers in the advisory notices.**
   Every journey/step that
   re-proves a defect fixed in the fix cycle being tested MUST carry a
   `known_items` entry (rendered as the yellow advisory notice) whose text
   leads with the FULL defect-ledger identifier in its canonical form,
   `TICKET.DEFECTID.slug-slug-slug` (e.g.
   `TICKET-XXX.B211.activity-panel-stuck-merging-inputs`), followed by one plain
   sentence: what was broken, and what the owner should now see instead.
   Attach it to the specific step(s) via `step_refs` (never a journey-wide
   unscoped item). Multiple defects re-proven by one step get one advisory
   each. Source the identifiers from the ticket's bug-master ledger; never
   paraphrase or invent slugs. Purpose: the owner testing a step knows exactly
   which fix from the last cycle they are validating.

## Feedback the owner returns

Feedback is `uat-feedback@2`, one durable entry per `step_id`. Result values,
compatibility aliases, requiredness, nullability, and projections come from the
deployed [feedback result contract](feedback-result-contract.json), not from
this document. New steps must offer the options authored on that step; aliases
are read-only compatibility inputs and must not be emitted as new options.
Owners save partially on purpose; an untouched card never blocks a save, and the
step they filled in first is usually the one that matters most.

Scenario and GWT content is read-only to the owner. If a scenario is wrong, they
say so in step feedback; the round is not where scenarios get edited.

## When the owner works it on the web

A registered round appears on the decision-review portal, where sending commits
a durable event. An armed orchestrator monitor may then discover it and continue
the mapped lifecycle step with an idempotent effect. Registration in the deployed config is an owner-facing
deployment change; confirm the round is listed before sending anyone a link, or
they will open an empty queue rather than an error.
