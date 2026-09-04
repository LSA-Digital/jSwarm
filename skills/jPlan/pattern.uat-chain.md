## Trigger

`Automated UAT: yes` for UI/E2E/user-journey impact; omit for work with no UI/E2E impact.

### Machine-readable execution trigger

When `Automated UAT: yes`, `/jPlan` MUST emit exactly one `## UAT Execution Trigger` section containing one fenced `json` object in the `uat-execution-trigger@1` schema. Derive phases, applicability, impact, obligation references, and required assets from this plan's own phases and acceptance criteria; produce the section only through `jswarm/uat_trigger.py::compose_trigger_section`, never hand-typed JSON. The schema authority is the plan's **Machine-readable UAT trigger contract**; `/jGo` consumes these exact bytes at phase exit. When `Automated UAT: no`, omit this section entirely.

## Header lines

**Automated UAT:** yes | no (yes only for UI/E2E/user-journey impact; backend-only/schema-only/migration-only/infrastructure-only tickets use lower-level proof and record `no (no UI/E2E impact)`)
**E2E policy:** per-ticket | deferred (`per-ticket` promotes stable live-show flows into headless Playwright regression artifacts near plan completion; `deferred` moves regression E2E to feature-level verification)

## Plan sections

## Definition of Done-ness (UAT/NFR Status Ladder)

| State | Glyph | Color | Cell label | Weight | Definition (operator) |
|---|---|---|---|---|---|
| DoBacklogged | 🔴 | red | `🔴 Backlogged` | 0 | not started, or code written but not unit-tested, or failed unit testing |
| DoDrafted | 🟠 | orange | `🟠 Drafted` | 1 | code written and green unit/integration tested |
| DoReady | 🟡 | yellow | `🟡 Ready` | 2 | UAT-tested (UAT scenarios) and measured against NFR criteria (NFRs) |
| DoDone | 🟢 | green | `🟢 Done` | 3 | owner tested and/or approved (human sign-off) |

Rules:
- The **UAT-Scenario** and **A/C-to-NFR** matrix Status cells use this ladder.
- `uat_complete` / `nfr_complete` (derived → HUD count) = **🟢 Done rows / total**.
- The HUD's **NFR/UAT colored circle** = the **weighted-average** band over that matrix's rows; **A/C has no circle and stays white**.
- **Automation tops at 🟡 Ready**; **owner sign-off → 🟢 Done**; reconciler is **monotonic** (PASS never demotes 🟢; FAIL forces 🔴).

---

## UAT-Scenario Traceability Matrix

> **One row per UAT scenario** (NOT per A/C; that is the matrix above). The single Status aggregates ALL tests proving that scenario (1 scenario : many tests). `uat_complete` (derived frontmatter → HUD) = 🟢 rows / total rows. Include this matrix only when `Automated UAT: yes`; omit it for tickets with no UI/E2E impact (the field then stays absent → the HUD shows no UAT segment). `/jPrecompact` keeps the statuses truthful from the ticket-local UAT results each checkpoint (AC-10). Status cells use the 4-state ladder: 🔴 Backlogged / 🟠 Drafted / 🟡 Ready / 🟢 Done.

| UAT scenario | A/C served | Test(s) / Evidence | Status |
| ------------ | ---------- | ------------------ | ------ |
| UAT-1 | A/C 1 | `e2e_file.spec.ts::test flow` | 🔴 Backlogged |

---

## Automated UAT Plan

> **Required when `Automated UAT: yes`.** Keep this section even if the detailed scenario doc lives in a separate `*.uat-test.md` file. `/jGo` uses this section plus the linked UAT doc to drive `jQATester`.

**Official UAT inventory:** `[docs/plans/FEATURE-XXX-uat-scenarios.md or docs/architecture/... ]`

**Local UAT scenarios:** `docs/plans/TICKET-XXX.uat-scenarios.md`, the working slice extracted from the official inventory. May evolve during the ticket.

**UAT doc:** `docs/plans/TICKET-XXX.uat-test.md`, derived from `TICKET-XXX.uat-scenarios.md` and created from `docs/templates/UAT_TEST_TEMPLATE.md` (`jswarm/uat-scenarios/scaffold_uat_tests.py` scaffolds it).

## Automated UAT Results

> Filled during `/jGo` when `Automated UAT: yes`. If `Automated UAT: no`, leave a short note explaining why this section is intentionally unused.

| Scenario / A/C | Expected | Actual | Status | Evidence | Report / Links |
|----------------|----------|--------|--------|----------|----------------|
| [A/C 1 or Scenario 1] | [expected outcome] | [actual outcome] | PASS / FAIL / BLOCKED / N/A | [snapshot / API / console evidence] | [`docs/plans/evidence/TICKET-XXX/uat-report.md`] |

**Summary:** [overall pass/fail statement]

## Rules

Use the UAT chain only after unit/integration proof. State whether execution is live-show-headed, headless automation, or diagnostic CDP; include driver, preflight, data, contracts, monitoring, durable report, and regression-promotion decision. Passing live-show flows are promoted or explicitly deferred.

Provenance: `PLAN_TEMPLATE.md` / `PLAN_FEATURE_TEMPLATE.md` §§ Definition of Done-ness (UAT/NFR Status Ladder), UAT-Scenario Traceability Matrix, Automated UAT Plan, Automated UAT Results. Definition of Done-ness was restored (byte-identical between the two source templates); see `.jswarm/plans/TICKET-XXX/TICKET-XXX.dedrift-ledger.md`.
