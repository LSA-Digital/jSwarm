
# Developer guide: the Level 1 UAT cycle

This is the sole complete, developer-facing overview of Level 1 UAT. Use it
when you need to understand how an official UAT scenario becomes a sealed,
owner-ready walk and then either a tracked finding or an approved outcome. You
author and fix product intent; the agents and deterministic tools prove and
record it. The exact procedures remain linked below so this overview explains
the end-to-end flow without redefining their contracts.

Start with current official UAT scenarios. They drive two related paths at the
same time: the executable `uat-test` used by the jQATester pre-walk and the
chain verification used by `/jTest uat prepare`. A successful pre-walk permits a
later prepare invocation to issue an already-populated feedback file for the
owner. The owner records observations in that file and runs `/jTest uat feedback`.
A finding or other recorded confirmation that changes expected behavior returns immediately through canonical GWT update → same-batch unit/integration drift sweep → re-derived `uat-test` → fix/re-proof; an all-PASS result that introduces no new behavior records approval and exits the cycle. Never defer source synchronization to a later prepare or close step.

## Artifact hierarchy

| File | Purpose | Audience (process/tooling/human) | Author |
| --- | --- | --- | --- |
| `uat-scenarios.md` | Official scenario/GWT source of truth for the ticket | Process (chain binds `gwt_sha256`) + human | You + agent via `/jUAT author` |
| `uat-scenario-steps.md` | Delivery-journey runbook per scenario (the modern name for legacy `uat-test.md`) | Human + process (jQATester pre-walk source) | You + agent via `/jUAT author` |
| `uat-test.md` | Legacy alias of `uat-scenario-steps.md`, resolved by the chain when the canonical file is absent; at issuance PREP gates it as the owner walk script | Human (owner walk) + tooling (chain-verifier fallback; A4 walk-script gate) | You + agent via `/jUAT author` |
| `<TICKET>.UAT-CURRENT-ROUND.md` | Sealed round package (`DRAFT_SEALED` → `ISSUED`) with gate cards and bound identities | Process + tooling (issuance gates, exact-file walk) | `jswarm/uat_round_materialize.py` via `/jTest uat prepare` |
| `<TICKET>.uat-feedback.md` | Pre-filled owner feedback document with per-journey verdict slots | Human (owner) + tooling (`jswarm/uat_feedback.py`) | `jswarm/uat_prepare.py` at `ISSUED` |

## Diagram 1: DevOps lifecycle with the UAT overlay

```text
You type each command in this loop yourself; none of them runs the next one
for you. jPrecompact is a checkpoint you can invoke at any point inside
/jGo, not a loop stage.

 [jPlan] -> [jGo] -> [jTest] -> [jUAT] -> [jFix, if the round finds something]
                                              |                    |
                                              |                    +-- back to [jTest]
                                              v
                                         [jClose] -> [jMerge]
```

`jswarm/uat_trigger.py::compose_trigger_section` renders the jPlan trigger;
`evaluate_phase_exit` (invoked from within `/jGo`'s own phase-exit companion,
not as a separately typed command) returns only `PROCEED`, `DISPATCH`,
`BLOCK`, or `SKIP` and dispatches `/jTest uat prepare <TICKET>` when current
proof is absent or stale (`jswarm/uat_trigger.py:162-177,180-235`). The
canonical loop order is **jPlan -> jGo -> jTest -> jUAT -> jFix (when the
round finds something) -> jClose -> jMerge**. Each stage is typed by the
user; `/jClose` does not invoke `/jMerge` (`skills/jClose/SKILL.md`).

## Diagram 2: Level 1 UAT is a cycle

```text
                          official UAT scenarios (source of truth)
                                         |
                   +---------------------+---------------------+
                   |                                           |
                   v                                           v
      /jUAT author creates current scenario/GWT          query_uat_scenarios.py
      + derived uat-test script                          --verify-chain checks
                   |                                    current scenario/GWT binding
                   v                                           |
      jQATester pre-walks exact sealed                  /jTest uat prepare verifies
      <TICKET>.UAT-CURRENT-ROUND.md                     chain + certified build, then
      -> uat-prewalk-report@1                            seals the package
                   \                                           /
                    \                                         /
                     +------ exact identity-bound package ----+
                                         |
                              complete PASS only
                                         v
                       QA_VERIFIED -> ISSUED by later prepare
                                         |
                                         v
                   owner receives ready <TICKET>.uat-feedback.md
                   and walks the product; no formatting required
                                         |
                                         v
                              /jTest uat feedback
                                         |
                            +------------+------------+
                            |                         |
                         findings                    all PASS
                            |                         |
                            v                         v
        traceability + plan Bug Master Ledger      approval recorded
        rows updated in the same work unit              EXIT
                            |
                            v
       update canonical GWT now -> unit/integration sweep
                            |
                            v
              re-derive uat-test -> fix/re-proof
                            |
                            +---------- back to source of truth
```

The chain verifier binds the current scenario/GWT through `gwt_sha256`; a stale
GWT causes `SCENARIO-GWT-SHA256-MISMATCH` and blocks prepare
(`jswarm/uat-scenarios/query_uat_scenarios.py --verify-chain`). The jQATester
walks the exact sealed current-round file, never a summary, and only a complete
identity-bound PASS produces `QA_VERIFIED` and then `ISSUED`
(`skills/jTest/uat/round-prep.md:11-20`).
- Authoring the round request (shapes, nested GWT lineage, digest rule, pre-flight check): [`uat/round-authoring.md`](uat/round-authoring.md)

## Step-by-step: what you do and what the system does

| Step | Actor | Inputs | Outputs | Command or authoritative path |
| --- | --- | --- | --- | --- |
| Author scenarios | You + agent | Acceptance criteria and product intent | Official scenario/GWT and derived `uat-test` | `/jUAT author`; `jswarm/uat-scenarios/` |
| Compile trigger | Agent | Applicability, obligations, receipt source | `uat-execution-trigger@1` in the plan | `/jPlan`; `jswarm/uat_trigger.py::compose_trigger_section` |
| Evaluate exit | Agent | Trigger, lower-level result, current ledger identities | `PROCEED`, `DISPATCH`, `BLOCK`, or `SKIP` | `/jGo`; `jswarm/uat_trigger.py::evaluate_phase_exit` |
| Seal package | Agent + deterministic tool | Current scenarios, derived script, verified chain, certified build | `DRAFT_SEALED` `<TICKET>.UAT-CURRENT-ROUND.md` | `/jTest uat prepare`; `jswarm/uat_prepare.py` |
| Pre-walk | jQATester agent | Exact sealed round, package/script/build identities | `uat-prewalk-report@1` | jQATester dispatch; `uat/round-prep.md` |
| Consume and issue | Agent | Complete identity-bound PASS report | `QA_VERIFIED`, then `ISSUED`, plus feedback file | Later `/jTest uat prepare`; `jswarm/uat_prepare.py:342-349` |
| Owner walk | Owner | Issued round and ready feedback file | Journey verdicts and observations | `<TICKET>.uat-feedback.md` |
| Ingest feedback | Agent + deterministic tool | Newest unprocessed valid feedback file | Idempotent traceability-ledger upsert and processing receipt | `/jTest uat feedback`; `uat/feedback.md`, `jswarm/uat_feedback.py` |
| Fix loop | You + agent | Finding rows and current scenarios | scenario/GWT successor → drift sweep → re-derived uat-test → product fix/renewed proof | `/jGo`, then repeat this cycle |
| Approve and close | Owner + agent | All-PASS ledger state and acceptance evidence | Approval; jClose reconciliation | `/jClose`, then `/jMerge` where applicable |

## The feedback-file lifecycle

You never format an owner feedback document. At `ISSUED`, the agent uses
`render_feedback_document(package, generated_at, tool_owned_receipt)` to
atomically create `<TICKET>.uat-feedback.md`, pre-filled with the sealed
package's exact journeys, IDs, and identities (`jswarm/uat_prepare.py:342-349`;
`jswarm/uat_feedback.py:348-415`). You add only journey verdicts and
observations.

When you run `/jTest uat feedback`, the procedure selects the **newest
unprocessed** candidate by parsed `generated_at`, never file modification time.
It validates the document before writing and idempotently upserts the result to
`.jswarm/plans/<TICKET>/<TICKET>.test-traceability.md`
(`skills/jTest/uat/feedback.md`;
`jswarm/uat_feedback.py`). Re-running a processed file does not create a second
finding or corrupt the ledger. Owner feedback that carries defects routes to a /jFix cycle.

## Bug-log maintenance: agent obligations

The agent keeps two related records synchronized in the same work unit:

1. The plan-level **Bug Master Ledger**, when the ticket enables `UAT round
   tracking: on` through
   `skills/jPlan/pattern.uat-round-tracking.md`.
2. The unified traceability ledger, always at
   `.jswarm/plans/<TICKET>/<TICKET>.test-traceability.md`.

The ledger records separate scenario outcome, finding severity, and disposition
axes. `PASS_WITH_FINDINGS` is valid; findings become rows such as `FB-NNN-slug`
with dispositions `SATISFIED`, `OPEN`, `ACCEPTED-BY-OWNER`, or
`DEFERRED→ticket`. A FAIL or finding path updates the ledger and, when tracking
is enabled, the Bug Master Ledger together. All-PASS records approval instead
of inventing a defect.

`UAT_FEEDBACK_TEMPLATE.md` and `UAT_CURRENT_ROUND_TEMPLATE.md` are controlled
masters. Change them only at their controlled-config master paths through the
controlled-config workflow, never by editing deployed copies.

## Detailed navigation

- [UAT verification](uat/verification.md)
- [UAT round preparation](uat/round-prep.md)
- [Feedback ingestion](uat/feedback.md)
- [Traceability contract](evidence/traceability-contract.md)
- [NFR verification support](nfr/verification.md): supporting contract only;
  no active `/jTest nfr verify` route.

`execution-protocol.md` and `strategy-and-proof.md` remain their own procedures
and are not duplicated here. Broad precompact/close gates, `jStatus standard@2`,
and active NFR verification remain deferred scope; this guide does not advertise
them as deployed behavior.
