## Trigger

Owner-approved opt-in for a Story/Task/Bug that refines an existing UI through rapid browser feedback, where the concrete defect list is expected to emerge during delivery and the owner can accept or reject each item immediately. Select only when the feedback loop is the primary correctness mechanism. Do not select for a new feature, uncertain architecture, backend/workflow change, migration, shared contract, security/compliance work, or UI work whose requirements can and should be planned before implementation.

## Header lines

**Rapid vibe UI:** on (QUICK plan plus living per-item browser contracts; no speculative defect A/C)
**Living contracts:** [.jswarm/plans/<TICKET>/<TICKET>.uat-scenarios.md](<TICKET>/<TICKET>.uat-scenarios.md) (authoritative delivery and acceptance record)

## Plan sections

## Rapid Vibe UI Delivery Contract

### Deliberate omissions versus CORE/FULL

Use the QUICK bundle with this addon. Do not create a technical design spec, speculative per-defect A/C, pre-authored UX mockups, detailed implementation phases, or a separate `<TICKET>.uat-test.md`. Those artifacts predict a defect list that does not exist yet and become stale after the first owner-feedback cycle. Keep scope as guardrails, collapse implementation to one continuous fix/measure/accept loop, and replace the CORE A/C placeholders with exactly one closure A/C: every living contract is terminally accepted or explicitly deferred to a named ticket, and the final combined walkthrough is owner-accepted.

### Living contracts surface

`.jswarm/plans/<TICKET>/<TICKET>.uat-scenarios.md` is the single delivery record. Append one GWT contract when each defect or style issue is reported; never pre-invent the backlog. Every item records:

| ID | GWT contract / what is wrong | Status | Commit | Owner acceptance date |
| --- | --- | --- | --- | --- |
| BUG-N / STYLE-N | Given / When / Then plus the visible defect | `OPEN` / `READY_FOR_OWNER` / `ACCEPTED` / `REJECTED` / `DEFERRED→TICKET` | SHA or pending | YYYY-MM-DD or pending |

Amendments are first-class evidence: append the date, prior wording, revised wording, and owner reason beneath the item instead of overwriting history.

### Delivery and acceptance loop

1. Append the reported contract as `OPEN`.
2. Assign concurrent lanes only across disjoint file sets. Any cross-cutting token/theme/shared-ancestor change runs alone.
3. Implement the smallest fix and verify it in a real browser. Geometry and rendered-style claims use `getBoundingClientRect()` / `getComputedStyle()`, never jsdom. Every contrast result names both measured colours and the ratio.
4. Report every owner check as a table, never bare IDs: `ID | what changed | what to check in the browser | status | commit`.
5. Ask for immediate per-item acceptance. On acceptance, record `ACCEPTED`, commit, and date. On rejection, record `REJECTED`, append the amendment, return it to `OPEN`, and repeat.

### Final combined walkthrough and close reconciliation

After all individual items are terminal, add one numbered, continuous browser journey to the living-contracts file, ordered to expose inter-fix conflicts. Individual acceptance does not substitute for this walkthrough; record its owner acceptance date separately.

Reconcile at every `/jPrecompact` and immediately before `/jClose`: derive the plan's single closure A/C from the living file rather than duplicating item state. Mark it complete only when no item remains `OPEN`, `READY_FOR_OWNER`, or `REJECTED`; every deferral names its destination ticket; and the combined walkthrough is accepted. Then record the normal lifecycle transition through the canonical plan-status command. Never hand-edit derived frontmatter. This keeps the close gate's plan A/C count truthful while the living file remains the detailed source of record.

## Rules

This pattern optimizes for minimum ceremony and maximum owner-feedback cycles; it is not permission to skip planning for genuine feature work. The living contracts file replaces upfront defect A/C and the duplicate executable UAT document, but not per-item browser acceptance, measured evidence, the final combined walkthrough, or close-gate reconciliation.

Provenance: pattern derived from field evidence.
