## Trigger

QUICK, FULL, or FEATURE bundle; catalog Pattern selection is optional but explicit.

## Header lines

**Catalog Pattern selection:** [1-3 catalog Pattern IDs such as `PAT-001`, or `N/A (no recurring engineering Pattern selected)`; keep separate from execution-team Pattern 1/2]

## Plan sections

## Catalog Pattern Selection

> Lean references only: list 1-3 catalog Pattern IDs, rationale, and obligations. Do not inline full Pattern bodies, external-source prose, or pattern literature. This section is distinct from the Q6 execution-team Pattern 1/2 field above.

**Selected catalog Patterns:** `PAT-___` / `N/A (why no catalog Pattern applies)`

**Rationale:** [why each selected Pattern fits this ticket]

**Alternatives / rejected Patterns:** [PAT-___, rejected because ...; or N/A]

**Constraint surfaces to materialize (AC-11):** [plan fields, required/forbidden agent routing, required prompt clauses, anti-patterns/tradeoffs, evidence tests, reviewer enforcement markers]

**A/C-to-UAT/test/evidence map (AC-12):** [every affected A/C maps to scenario/test/smoke/evidence or explicit N/A rationale]

**Thin-slice proof point / stop condition (AC-13):** [smallest acceptance-relevant proof before broad coding; stop if proof cannot run, evidence map is missing, or proof no longer matches intended behavior]

## Rules

Keep catalog Pattern references lean and distinct from the execution-team Pattern 1/2 contract. Materialize selected Pattern constraints in the plan; reviewers reject prose-only compliance, missing evidence maps, broad coding before the thin-slice proof, and choices contradicting dominant NFR weights.

Provenance: `PLAN_TEMPLATE.md` § Catalog Pattern Selection; `step-5-assemble-plan.md` § Catalog Pattern selection.