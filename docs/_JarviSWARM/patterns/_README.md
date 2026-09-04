# docs/_JarviSWARM/patterns/ — Catalog Pattern records

This directory holds one YAML file per JarviSWARM **catalog Pattern**: reusable engineering knowledge for a recurring problem in context. A catalog Pattern is a first-class JarviSWARM entity alongside Feature, Tool, Document, Process, and Asset. It is distinct from the `/jPlan` **execution-team Pattern 1/2** staffing shorthand.

Pattern records are operational guidance, not copied pattern literature. They encode applicability, forces, practices, evidence, tradeoffs, lifecycle hooks, NFR weighting, and reviewer enforcement so lifecycle commands can reference Pattern IDs leanly and materialize concrete obligations in plans, prompts, tests, retros, and maintenance.

## Terminology: two senses of "Pattern"

- **Catalog Pattern** = the first-class catalog entity defined in this directory, identified by `level: l1_preset | l2_ingredient`, `key: l1.*/l2.*`, and `id: PAT-###`. This includes the H/M/L ceremony presets and the L2 ingredients they compose.
- **Q6 execution-team Pattern 1/2** = the implementation execution mode selected by `/jPlan` Q6: Pattern 1 Full-TDD or Pattern 2 Orchestrator+Critic, surfaced by the compiler as `compile.q6_execution_team_hint ∈ {pattern_1, pattern_2}`.

These are different concepts and not aliases; a catalog Pattern is distinct from, and must not alias, the Q6 execution-team Pattern 1/2 mode.

## Authoring rules

1. **Reference, don't copy.** Cite external precedents in `external_precedents`; synthesize JarviSWARM guidance in your own words.
2. **Name the fit and the non-fit.** Every record must include `applicability.use_when` and `applicability.avoid_when`; `avoid_when` is the canonical non-applicability field.
3. **Materialize constraints.** A selected Pattern must create real surfaces: plan fields, prompt clauses, evidence tests, stop conditions, and reviewer checks.
4. **Keep commands lean.** `/jPlan` and `/jGo` should select Pattern IDs, rationale, and compact obligations; the full Pattern body stays here.
5. **Prove early slices.** If a Pattern requires acceptance/UAT proof, broad coding stops until the thin slice is proven against the A/C-to-UAT evidence map.
6. **Weight NFRs per job.** Pattern-guided work states which NFRs dominate instead of treating reliability, compliance/security, transparency, maintainability, and readability as equal every time.
7. **Govern promotion.** Risk, compliance, and security-family Patterns require recorded higher-review sign-off before `active` use.

## File naming

Use:

```text
PAT-NNN-descriptive-name.pattern.yaml
```

Example: `PAT-001-thin-slice-acceptance-proof.pattern.yaml`.

## Minimum fields

A valid Pattern record carries these information categories:

| Category | Purpose |
|---|---|
| identity | `schema_version`, `id`, `name`, `family`, `kind` |
| problem form | `intent`, `problem.context`, `problem.forces`, `applicability.use_when`, `applicability.avoid_when`, `solution_summary` |
| lifecycle use | hooks for `/jPlan`, `/jGo`, `/jClose`, `/feature-reconcile`, `/devops-maint` |
| execution constraints | `constraint_surfaces`, `execution_practices`, `evidence_tests`, `ac_to_uat_mapping`, `thin_slice_uat_proof`, stop conditions |
| design controls | `minimum_viable_architecture`, `sufficient_design`, `nfr_weighting`, `reviewer_enforcement` |
| governance | anti-patterns, tradeoffs, relationships, external precedents, worked examples, publication metadata, owner/reviewer/status metadata, changelog |

## Family coverage

The catalog should eventually cover these families:

- design/code patterns
- architecture/cloud patterns
- microservice/distributed-system patterns
- enterprise integration/eventing patterns
- refactoring/modernization patterns
- TDD/BDD/testing patterns
- DevOps/CI-CD/platform patterns
- agile/lean discovery/product patterns
- team collaboration/org-design patterns
- risk/compliance/security patterns
- UI/UX/design-system patterns

A minimal MVP record seeds the catalog first; broad family population follows only after schema/build/drift support proves the entity contract.
