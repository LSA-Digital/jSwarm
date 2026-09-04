# Chatbot Testing Protocol: UAT Semantic Verdict Consumption Gate

> **Name:** this protocol is the **"chatbot testing protocol"** (owner-named 2026-08-02). The filename stays stable for existing references; prose references should use the new name.

## Applicability (binding rule, owner-ruled 2026-08-02)

**Any plan whose testing includes non-deterministic behavior judged semantically (LLM-judgment UAT such as chatbot conversations, advisor answers, or generated content) MUST use this protocol.** Concretely, such a plan:

1. names the **chatbot testing protocol** in its `Testing strategy` header line,
2. binds it into every jQATester dispatch that produces semantic verdicts (alongside `UAT_SEMANTIC_JUDGMENT_DISPATCH_TEMPLATE.md`), and
3. accepts that `/uat-round` enforces the consumption gate below: a verdict failing any check stops at PREP.

Deterministic-only plans (pure layout, pure API contracts) do not need it.

## The consumption gate

Before `/uat-round` consumes a scenario's semantic verdict into the round file, verify every item below. Any failure means the verdict is not yet consumable; patch the source evidence, never paper over the gap in the round file.

1. **All three layer dispositions present.** Require all three layer dispositions (Layer 1, Layer 2, and Layer 3), each recorded as PASS, FAIL, or BLOCKED before consumption.
2. **Layer-2 audited form.** The Layer-2 numerator/denominator must appear in the audited compact form `semantic_passes/completed_probes; threshold N; PASS|FAIL|BLOCKED` (e.g. `2/3; threshold 2; PASS`); this is the form Phase 2 already established in `jQATester.body.md`; stay consistent with it.
3. **Formula-consistent overall verdict.** An Overall PASS is REJECTED whenever any layer is FAIL or BLOCKED: a listed formula without this rejection permits a contradictory recorded verdict.
4. **Evidence for every failed layer.** Require evidence for every failed layer: cite the transcript excerpt, screenshot, or log line that proves the failure.
5. **Transcript reference per probe.** Require a transcript reference for every probe (canonical GWT and each paraphrase), not only the aggregate row.
6. **Continuation evidence for every offer.** Require continuation evidence for every action offer: cite the typed continuation each offer was consumed by.
7. **Rubric in Definitions.** Carry the scenario's judgment_rubric verbatim into the round file's Definitions section (pass_exemplars, fail_exemplars, tolerance_notes, grounding_requirements, forbidden_behaviors); never invent rubric text outside it.
8. **Product NFRs as context only.** Product NFRs are citable as acceptance context only, for example the mutation-consent and backed-affordance items owned by the product-NFR catalog.
9. **Ownership boundary.** This gate never registers, proposes, promotes, or catalogs product NFRs; that workflow belongs to the product-NFR catalog alone; this gate cites it, and does not manage it.

A verdict failing any check above stops at `/uat-round` PREP; do not materialize a round file around an unauditable or contradictory semantic verdict.
