<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/test/uat/verification.md -->

# UAT verification overview

Canonical grammar: `/test uat prepare|execute|feedback`. The complete ownership map is [test-uat.lifecycle.md](../test-uat.lifecycle.md).

| Verb | Owns | Does not own |
| --- | --- | --- |
| `prepare` | Chain/build verification, sealed round preparation, pre-walk report consumption, issuance gating | Browser walking or judgment; owner-feedback ingestion |
| `execute` | Supplying and collecting generated views for the owner walk | Altering sealed journey payload or deciding feedback disposition |
| `feedback` | Deterministic validation and idempotent ledger upsert of returned structured feedback | Scenario/GWT authoring, test execution, implementation, or downstream execution |

The chain owns marker refresh, the transport-ladder probe, UAT-G0 currency re-certification, and monitor arming; a direct dispatch silently skips all four.

Use [round-prep.md](round-prep.md) for the preparation procedure and [feedback.md](feedback.md) for feedback ingestion. The [traceability contract](../evidence/traceability-contract.md) defines durable ledger proof.

The deployed [feedback result contract](feedback-result-contract.json) is the
single authority for aggregate verdicts and result semantics. Verify the
runtime-published contract, managed CLI output, and deployed master agree before
making a new issue, reseal, or owner-ready claim:

```bash
.venv/bin/python -m jswarm.uat_feedback result-contract --json
```

The CLI-reported SHA must match the runtime API's contract SHA and the SHA of
`feedback-result-contract.json`; any mismatch is a fail-closed activation
condition for new issue, reseal, and invitation.

## Two-axis observation model

UAT feedback records the scenario outcome separately from finding severity and
finding disposition. The batch roll-up is derived from the aggregate-verdict
rules in the deployed master; an accepted or slugged-deferred finding may
produce a findings-bearing pass. Structured fields and exact requirement
references are authoritative; prose does not infer axes.

Decision 4B determines which source types are eligible for the unified ledger. Eligibility classification is not execution: `Automated UAT: yes` and its applicable phase declare that the deterministic UAT route is required, while the Phase-4 trigger implementation decides when that route fires after its required lower-level proof.

Active `/test nfr verify` is deferred. NFR verification support is not an active COM-376 route and `/test` does not author NFR catalog data.

## Verification rules

A negative finding from a pattern-matcher is worthless until the matcher is shown to fire on a known positive. Record the demonstration alongside the zero.

Any journey whose validity depends on a precondition carries a machine-checkable gate whose failure mode is BLOCKED, never FAIL.

When evidence falsifies a scenario premise, the chain requires a recorded successor clause (replace, not strike-through); a loosened clause with no recorded successor is how green-invisible regressions get built.

When a processed owner-feedback receipt, an owner or ticket boss ruling receipt, or an orchestrator verification receipt confirms an accepted behavior outcome absent from or contradictory to current GWT, update canonical `uat-scenarios`/GWT in that same work unit before `/fix`, test or implementation dispatch, round refresh, re-seal, preparation, or any owner-ready claim; record a successor clause and never defer the update to a later lifecycle step.

When a canonical GWT outcome or FAIL clause changes with an authoritative ruling receipt, or a batch dispatch/return binds shipped production behavior to a new ruling already recorded in GWT, the same batch dispatches `jTestEngineer` over unit/integration expectations; its report cites the ruling, records the superseded and successor clauses plus search seeds/results, fixes only `TEST-DRIFT`, and leaves every real defect RED—observed behavior alone never authorizes an expectation change.
