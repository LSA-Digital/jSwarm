## Trigger

`NFR catalog: applicable`; retain the conditional header fields in QUICK, FULL, and FEATURE plans.

## Header lines

**NFR catalog:** applicable | N/A — <reason>
**Automated NFR:** yes | no — <reason>
**Per-Phase NFR Gate:** applicable | N/A — <reason>

## Plan sections

## A/C-to-NFR Traceability Matrix

> Every applicable A/C maps to the NFR catalog with a full descriptor ref, validation recipe, threshold, and evidence status; use N/A with a rationale when no NFR applies. Status cells use the 4-state ladder: 🔴 Backlogged / 🟠 Drafted / 🟡 Ready / 🟢 Done.

| A/C | NFR ref (full descriptor) | Dimension (tags) | Validation recipe / Evidence | Threshold / Pass criteria | Status |
| --- | ------------------------- | ---------------- | ---------------------------- | ------------------------- | ------ |
| A/C 1 | **NFR-[n]-[DESCRIPTOR]** | reliability / security / performance / maintainability | [test, review, metric, or evidence path] | [specific threshold or `N/A — reason`] | 🔴 Backlogged |

### NFR Validation Strategy

**NFR status (derived):** 0/1
**NFR catalog:** applicable | N/A — <reason>
**Automated NFR:** yes | no — <reason>
**Per-Phase NFR Gate:** applicable | N/A — <reason>

**Per-job / per-Pattern NFR weighting (AC-14):**

| Pattern ID | Reliability | Compliance / Security | Transparency | Maintainability | Efficiency / Readability | Other NFRs | Dominant weights and implementation consequence |
|------------|-------------|-----------------------|--------------|-----------------|--------------------------|------------|-------------------------------------------------|
| PAT-___ | high / med / low | high / med / low | high / med / low | high / med / low | high / med / low | [name: weight] | [what design/test/review choices must follow] |

## Rules

Use full descriptor references and canonical matrix shape. The final `Status` column is load-bearing and must remain byte-exact. Where the NFR catalog applies, create the NFR working slice and machine sidecar; automated NFR plans also have a derived test document and must pass the jPlan lint gate.

Provenance: `PLAN_TEMPLATE.md` § A/C-to-NFR Traceability Matrix; `step-5-assemble-plan.md` § NFR catalog and canonical-format authoring gate.