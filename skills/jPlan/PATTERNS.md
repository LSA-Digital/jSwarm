# New-work composition patterns

`patterns.manifest.yaml` is the machine-readable source of truth. This document explains the registry for authors; if it conflicts with the manifest, the manifest wins.

## Composition model

Every assembled plan begins with `template.CORE.md`. The assembler applies the selected bundle in the manifest's total injection order. Standard bundles are `LITE`, `QUICK`, `FULL`, and `FEATURE`; the ceremony mapping is deterministic and fail-loud when a selector preset is unmapped.

Patterns carry a versioned, self-contained contribution: its Q&A trigger, contributed header lines, verbatim legacy plan fragments, and rules that formerly travelled separately. A pattern may own a singleton header or section only once; the manifest records that ownership and collision behavior.

## Registry

| Pattern | Primary use | Standard bundle use |
| --- | --- | --- |
| `briefing@1` | Lite briefing shape | LITE |
| `planning-scaffold@1` | Tasks, phases, completion, retro | QUICK, FULL, FEATURE |
| `uat-chain@1` | UAT matrix and execution plan | FULL, FEATURE |
| `rapid-vibe-ui@1` | Living per-defect browser contracts and close reconciliation for rapid existing-UI refinement | QUICK + explicit `--with` |
| `preissued-rulings@1` | Conditional T1-T5 execution rulings plus a mandatory owner-escalation budget | QUICK, FULL, FEATURE + evidence-triggered `--with` |
| `nfr-chain@1` | NFR traceability and validation | QUICK, FULL, FEATURE |
| `security-baseline@1` | Security/compliance before-state | QUICK, FULL, FEATURE |
| `outcome-metrics@1` | Measurable outcomes | QUICK, FULL, FEATURE |
| `ux-section@1` | UI journey contract | FULL |
| `catalog-pattern-selection@1` | Catalog Pattern obligations | QUICK, FULL, FEATURE |
| `feature-governance@1` | Feature PE2E and phase gates | FEATURE |
| `pe2e-contributions@1` | Story contribution to Feature PE2E | FULL |
| `dashboard-render-tier@1` | Dashboard delivery classification | FULL, FEATURE when triggered |
| `dashboard-projections@1` | Feature-child dashboard projections | FULL when triggered |
| `plan-governance@1` | Required reading, agent assignment, demo path, risk/rollback (FULL), screenshot evidence, smoke impact, error handling, observability, TEST_CATALOG review, E2E-enablement, critical files | FULL |

`rapid-vibe-ui` is deliberately addon-only: it changes the evidence and acceptance instrument for one delivery shape without creating another default ceremony bundle. Its trigger is owner-approved existing-UI refinement; genuine feature work continues through the normal selector and CORE/FULL planning obligations.

## Terminology clarifier

NFR-022 uses **catalog Pattern ceremony preset** language for the Low/Medium/High ceremony selection. Q6's **execution-team Pattern 1/2** names the implementation-team operating model. They are different concepts, are not aliases, and must remain separately represented in plan headers and assembly decisions.

## Versioning and provenance

Pattern versions and template versions are integers. Any composition change that reaches a generated template bumps that template's integer version; a no-op regeneration leaves both the version and bytes unchanged. Assembled plans record `created_from_template: CORE@<v>` and their `assembled_patterns` list. The UAT-round-tracking addon is intentionally not registered in this Slice B manifest; Slice C introduces it as a `--with`-only addon.
