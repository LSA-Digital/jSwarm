## Trigger

QUICK, FULL, or FEATURE bundle after core and selected conditional patterns.

## Header lines

None.

## Plan sections

## Testing Strategy

> Define the full test lifecycle for this ticket. See `docs/devops-practices.md` for the taxonomy.

| Layer | Required? | Planned proof | Owner / timing | Status |
|-------|-----------|---------------|----------------|--------|
| Unit | yes / no | [local behavior or helper contract] | [phase/task] | 🔴 |
| Integration | yes / no | [API + DB / workflow / service composition] | [phase/task] | 🔴 |
| Live Show UAT/E2E | yes / no | `docs/plans/TICKET-XXX.uat-test.md` visible browser run, or N/A — no UI/E2E impact | phase gate / plan completion | 🔴 |
| Regression-mode E2E/PE2E | per-ticket / deferred / N/A | [headless Playwright spec or feature-level deferral] | plan completion / feature verification | 🔴 |
| Smoke | impacted / not impacted | [smoke command or N/A] | phase completion / closeout | 🔴 |

## A/C-to-Test Traceability Matrix

> Every A/C MUST map to tests and evidence. If UI/E2E UAT is N/A, still provide lower-level proof (unit/integration/schema/CLI/smoke/review artifact) and an explicit UAT N/A rationale. Authoring status: 🆕 New / ✏️ Upgrade / ✅ Existing; passing proof advances UAT/NFR ladder rows toward 🟡 Ready or 🟢 Done.

| A/C | UAT refs | NFR refs | Test path | Test name / evidence check | Type | How it proves A/C | Status |
| --- | -------- | -------- | --------- | -------------------------- | ---- | ----------------- | ------ |
| A/C 1 | UAT-___ or `N/A — no UI/E2E impact because ...` | **NFR-[n]-[DESCRIPTOR]** or `N/A — reason` | `test_file.py` | `test_name` | Unit | [acceptance proof, not implementation shape] | 🆕 New |

## Implementation Phases

### Phase 0: Human-Assisted Runway

> Delete if not needed. Include only if human intervention required.

**Purpose:** Tasks requiring human intervention before agents can proceed.

**Pre-emptive Verification:**

```
[paste actual command outputs]
❌ [failed check] - [error]
✅ [passed check] - OK
```

**Tasks:**

1. [ ] **T0.1: [Task]** `@human`
   - **Blocker for:** Phase X
   - **What's needed:** [action]
   - **Verification:** `[command]`

**Exit Criteria:** All verifications pass.

---

### Phase 1: [Name]

**Lead Agent:** [agent] ([model])
**Goal:** [What this phase accomplishes]

**Tasks:**

1. [ ] **T1.1: [Task]** `@agent-tag`

**Exit Criteria:**

- [ ] All tasks complete
- [ ] All tests passing (🟢)
- [ ] Demo path verified

---

### Phase 2: [Name]

**Lead Agent:** [agent] ([model])
**Goal:** [What this phase accomplishes]

**Tasks:**

1. [ ] **T2.1: [Task]** `@agent-tag`

#### Phase 2 Demo Path

| Step | Action    | Expected    | Status | Test        |
| ---- | --------- | ----------- | ------ | ----------- |
| 1    | [from P1] | [available] | 🔴     | P1 tests    |
| 2    | [action]  | [result]    | 🔴     | `test_name` |

**Prerequisites:**

- [ ] Phase 1 complete

**Exit Criteria:**

- [ ] All tasks complete
- [ ] All tests passing (🟢)
- [ ] Screenshots captured (if UI)

---

## Status Updates

| Date       | Status         | Notes        |
| ---------- | -------------- | ------------ |
| YYYY-MM-DD | 🔴 Not started | Plan created |

---

## Critical Reminders

### TDD Workflow (NON-NEGOTIABLE)

1. A/C shapes tests → Write test FIRST → RED → Implement → GREEN → Verify prod
2. Use `/jGo` command to enforce TDD cycle
3. SUCCESS = ALL TESTS PASS

### Completion Gates

| Gate       | Command                                        |
| ---------- | ------------------------------------------------ |
| Unit Tests | `[project-specific unit test command]`         |
| Integration Tests | `[project-specific integration test command]` |
| E2E Tests  | `[project-specific e2e test command]` — only if `E2E policy: per-ticket` |
| Automated UAT | `jQATester` + `docs/plans/TICKET-XXX.uat-test.md` — only if `Automated UAT: yes` and UI/E2E impact exists |
| Generated catalog validation | `[project-specific catalog generator --validate]` — only if project uses generated `TEST_CATALOG.md` |

### Todo Management

- ONE TODO LIST PER PHASE
- Clear todos at phase boundary
- Create fresh list for next phase

---

## Completion Checklist

- [ ] All A/C marked complete (here AND Jira)
- [ ] All tests passing
- [ ] A/C-to-NFR matrix rows are 🟢 Done (validated + owner sign-off), or explicitly deferred / N/A
- [ ] Testing strategy complete across required unit, integration, live-show, regression, and smoke layers
- [ ] Jira updated with completion summary
- [ ] Retrospective completed via `/jClose`

---

## Retrospective

> Filled during `/jClose`. Do not fill manually.

**Retro:** _[link added by /jClose → `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/retros/TICKET-XXX.retro.YYYYMMDD.md]_
**Tool Failure Reports:** _[links to `docs/tool-failure-reports/TICKET-XXX.toolfail.*.md`, or "None"]_

---

## Changelog

| Date       | Author        | Change                     |
| ---------- | ------------- | -------------------------- |
| YYYY-MM-DD | [agent/human] | Plan created via /jPlan |

## Rules

Use one TODO list per implementation phase. Apply the TDD sequence: A/C shapes tests, write the test first, observe RED, implement the smallest change, observe GREEN, then verify production intent. Keep phase exit criteria and completion gates current.

Provenance: `PLAN_TEMPLATE.md` §§ Testing Strategy, Implementation Phases, Status Updates, Critical Reminders, Completion Checklist, Retrospective, Changelog; `PLAN_TEMPLATE_QUICK.md` §§ Tasks and Completion Checklist. Critical Reminders restored 2026-07-10 (COM-249 Slice B gate-4 round-1 MAJOR-1 remediation) — originally FULL-only; this pattern's membership (QUICK+FULL+FEATURE) is a superset, so restoring it here also newly adds it to QUICK and FEATURE plans (homogenization-addition, ledgered; this Rules line already claimed the provenance before the restore). A legacy UAT-execution agent slug in the Completion Gates row was updated to the current roster's `jQATester` per the COM-249 mapping table (see the ledger for the exact before/after string). QUICK's original flat `## Tasks` checklist is superseded by this pattern's Implementation Phases task/checkbox scaffold (`T1.1` numbered checkboxes under a phase) — ledgered as SUPERSEDED-by, not restored; see `.jswarm/plans/COM-249/COM-249.dedrift-ledger.md`. `### Phase 0: Human-Assisted Runway` and `### Phase 2: [Name]` byte-restored 2026-07-10 (COM-249 Slice B gate-4 remediation, final content pass) — originally FULL-only worked-example phases in `PLAN_TEMPLATE.md`'s Implementation Phases; this pattern's bundle-shared membership means QUICK and FEATURE also newly gain both headings (homogenization-addition, ledgered in the same manner as Critical Reminders above).