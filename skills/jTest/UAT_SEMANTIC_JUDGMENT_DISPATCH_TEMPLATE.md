# UAT Semantic Judgment Dispatch — UAT-D3

Copy this block into every Layer-2 semantic judgment dispatch. Fill every field. A dispatch missing the bound rubric is `BLOCKED: missing scenario judgment contract` before any probe is attempted.

## Dispatch identity
- **Ticket:** <KEY>
- **Scenario ID:** <UAT-SLUG>
- **Round/report file:** `.jswarm/plans/<TICKET>/uat-results/<TICKET>.agentic-verdict.round-N.md`

## Bound acceptance contract — paste verbatim
### Canonical GWT
<BEGIN VERBATIM GWT>
Given ...
When ...
Then ...
FAIL if ...
<END VERBATIM GWT>

### Judgment rubric — paste verbatim
<BEGIN VERBATIM JUDGMENT RUBRIC>
pass_exemplars:
<paste verbatim>

fail_exemplars:
<paste verbatim>

tolerance_notes:
<paste verbatim>

grounding_requirements:
<paste verbatim>

forbidden_behaviors:
<paste verbatim>
<END VERBATIM JUDGMENT RUBRIC>

Copy the scenario-owned rubric only — never derive, infer, or invent a missing clause from the GWT, the defect narrative, or the observed answer; if any required field is absent, return `BLOCKED: missing scenario judgment contract`.

## Probe set
- **Probe 1 — canonical:** GWT verbatim + rubric verbatim, exactly as bound above.
- **Probe 2 — paraphrase 1:** canonical GWT restated as paraphrase 1 in different wording; the rubric clauses stay bound and unchanged.
- **Probe 3 — paraphrase 2:** canonical GWT restated as paraphrase 2 in different wording; the rubric clauses stay bound and unchanged.
- **Probe 4 — conditional:** a third paraphrase, ONLY for a materially distinct language family from probes 1-3; otherwise omit.

Default set is probes 1-3 (3 executions). Probe 4 is added only for a materially distinct language family (4 executions max).

## Continuation sweep
Continuation sweep: for every action offer observed on a probe, confirm it is continued to completion and consumed by a reachable typed behavior before recording that probe's Layer-3 result. An offer left open is a Layer-3 FAIL for that probe, not a silent pass.

## Per-probe evidence
Record for every probe: exact input, disposition (PASS/FAIL/BLOCKED), L1 invariant evidence, L2 rubric clause + observed quote + rationale, L3 offer/continuation/typed outcome, transcript, screenshots, logs/console/network.

## State isolation
A browser process may be reused across probes for setup speed, but state isolation is mandatory for anything mutating: any mutating or state-sensitive specimen and any authenticated session MUST NOT be shared across probes — each probe that touches mutable state starts from its own fresh specimen/session so one probe's mutation cannot leak into another probe's verdict.

## Resume bound
This dispatch accepts no more than two resumes before the orchestrator must re-scope it; a third stall is a report of `BLOCKED`, not a further resume.

## Required return
The `L2 semantic vote` cell must use the fixed compact form `semantic_passes/completed_probes; threshold N; PASS|FAIL|BLOCKED` (e.g. `2/3; threshold 2; PASS`) so a reader can audit the numerator and denominator directly, never a bare verdict.

| Scenario | Canonical GWT | Paraphrase probes | L1 invariants | L2 semantic vote | L3 continuation | Overall | Key evidence |
|---|---|---|---|---|---|---|---|
| <UAT-SLUG> | ... | ... | ... | 2/3; threshold 2; PASS | ... | ... | ... |

## STOP conditions
- The bound `judgment_rubric` is absent, paraphrased, or not bound to this scenario ID: `BLOCKED: missing scenario judgment contract`.
- The harness/provider probe set is incomplete: BLOCKED, never shrink the denominator to manufacture a verdict; the intended probe count is never reduced, and this forces the scenario's Overall disposition to BLOCKED.
- Preflight is invalid for a probe: that probe is BLOCKED and excluded from the L2 denominator, and this forces the scenario's Overall disposition to BLOCKED.
- A product response error is a COMPLETED, FAILED semantic probe: it stays in the L2 denominator and is never reclassified as provider incompleteness or excluded.

## Prompt hygiene when a semantic probe fails (system-prompt accretion guard)

When a rubric FAIL (or repeated L2 flake) traces to LLM non-compliance with a system-prompt instruction, the fix triage is **rails-first, never prose-append**:

1. **Rails or model tier, not volume.** Ask "which deterministic mechanism is missing?" (post-processing backstop, parse-and-mint through an existing validation gate, deterministic collapse/suppression) or "is the slot's model tier adequate?" before touching prompt text. Repeating an instruction more loudly is superstition: repetition dilutes per-instruction salience, and the contradictions that actually cause misbehavior hide inside the accumulated mass. Field evidence: closed three compliance-failure classes in one day with rails + a model swap while the prompt got ~1,000 chars SMALLER (`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/.retro.prompt-accretion.md`).
2. **One rule, one home.** Before adding any instruction, grep the RENDERED prompt (builder output, not the source file) for the concept — if the rule exists, strengthen it in place at its canonical home; never restate it near your seam.
3. **Dedupe-scan-on-touch.** Any fix whose diff touches a prompt file runs a repeated-phrase scan (count repeated 7-word sequences in the rendered output; report anything ≥2) and a key-concept frequency count — the same mandatory reflex as replay-determinism for reducer-touching fixes.
4. **Exactly-once guard tests.** Every canonical rule gets a test asserting its key phrase appears ≤1 time in the rendered instructions. Presence tests defend a rule; only exactly-once tests defend the prompt — they turn future accretion into a RED test instead of silent rot.
5. **Prefer machine-checkable phrasing contracts.** When a rubric depends on what the model says, pin the critical sentence to a deterministic phrasing the code can parse/verify ('s one-phrasing rule enabled exact-containment duplicate detection, sentence parse-and-mint, and collapse rendering). A parseable contract converts prompt compliance from a hope into a backstoppable property — and gives the rubric a deterministic L1 anchor.
6. **Record the counter.** Every rail added for a compliance failure logs a telemetry counter; rubric rounds cite the counter trend to distinguish "model fixed it" from "rail is carrying it."
