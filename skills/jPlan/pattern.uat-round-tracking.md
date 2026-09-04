## Trigger

Opt-in: pairs with the Q7-family question in `/jPlan`'s condensed Q-list (asked alongside Q7 "Automated UAT needed?"): answer `UAT round tracking: yes`/`on` to enable, or answer `no`/`off` (or leave it unanswered) to omit. A `--with`-only addon: it is a member of no default bundle (LITE/QUICK/FULL/FEATURE); select it explicitly per ticket.

## Header lines

**UAT round tracking:** on (enables the Bug Master Ledger and its status-token clearing contract, and cross-links `<TICKET>.UAT-CURRENT-ROUND.md`, the ticket's round file)

## Plan sections

## Bug Master Ledger

> Ticket-owned discrete status ledger for every bug this ticket must clear before close, cross-linked with the ticket's round file, `<TICKET>.UAT-CURRENT-ROUND.md`. Status tokens (sorted 0→1→2): `0.CLOSED` = fixed & verified, nothing left on the ticket · `1.OPEN` = still needs action on this ticket · `2.DEFERRED→TICKET` = parked with a named follow-up home. Every `1.OPEN` row needs a matching testable row in the round file; every deferred row must name its destination ticket.

| Bug | Status | Owner | Notes |
|---|---|---|---|
| [#NNN-slug] | 0.CLOSED / 1.OPEN / 2.DEFERRED→TICKET | [who closes it] | [trailing note; deferrals name their home ticket] |

## Rules

The opt-in seventh /jPlan output initializes the default round only after the born plan and applicable sidecars are complete. Its lifecycle state is `INITIALIZED_NOT_READY`: it is not ready, prepared, or deployment-current; when tracking is off or unanswered, the output is omitted and no round/state/receipt is written. F-06 precedence: for the round FILE itself (`<TICKET>.UAT-CURRENT-ROUND.md`), the /jTest-owned `UAT_CURRENT_ROUND_TEMPLATE.md`'s HOT/COLD Journeys split is authoritative: that is the schema the round file must follow. `docs/testing/uat-fix-playbook.md`'s two-table "Owner UAT invitation format" governs chat invitation rendering only; it does not define the round file's own schema. Keep this pattern's ledger and the round file in sync in the same work unit: enable it only when the ticket runs UAT round tracking, and when it is off, omit the Bug Master Ledger and the round-file cross-link entirely.

Provenance: Slice C F-06/F-07 (`.jswarm/plans/TICKET-XXX/TICKET-XXX.specs.new-work-modularize.md`); operationalizes `/jTest` skill's `uat.md` §Round-file rule 7 and `docs/testing/uat-fix-playbook.md`'s OPEN-ledger / round-file bug-clearing pattern.
