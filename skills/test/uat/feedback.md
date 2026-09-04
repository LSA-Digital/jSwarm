# Feedback ingestion procedure

F-26 owns this routing-only procedure. It validates deterministic feedback assets and routes next actions; it never authors GWT or executes downstream work. There is no separate user-facing `uat-feedback` skill.

The deployed [feedback result contract](feedback-result-contract.json) is the
single authority for owner-entry cells, compatibility aliases, deterministic
ledger projections, system-only states, aggregate rules, and requiredness.
Inspect the same contract and its SHA with:

```bash
.venv/bin/python -m jswarm.uat_feedback result-contract --json
```

Owner-entered null finding fields remain nullable in the feedback document and
are normalized deterministically only at the processing boundary for the
existing ledger grammar. The validator and processor dual-read v2 and v3
feedback contracts; processed feedback and ledger artifacts are immutable and
are never rewritten.

## Select

Run `jswarm/uat_feedback.py plan <inbox>`. The helper parses timezone-aware `generated_at` from canonical feedback frontmatter, selects the newest eligible `UNPROCESSED` file, and reports every older unprocessed path. Select by parsed `generated_at`, never mtime alone; process only the selected file.

## Validate

Run `jswarm/uat_feedback.py validate <feedback> <candidate> <ledger>` before any write. The deterministic validator checks normalized-package identity, candidate projection, two-axis scenario outcome/finding severity/finding disposition, exact UAT N/A grammar, requirement-reference equality, ledger coverage and log identity. It is read-only and returns typed routing outcomes.

## Process

Run `jswarm/uat_feedback.py process <feedback> <candidate> <ledger>` only for that selected file. It validates before writes, keeps scenario outcome separate from finding severity and disposition, derives the batch roll-up from the master's aggregate rules, atomically stages the upserted ledger, final `PROCESSED` feedback, receipt, and aligned processing log, and preserves idempotent same-run behavior. `PARTIAL ASSET CHANGES:` is factual recovery output, not success. Route resulting actions to their owners; do not execute scenarios, tests, implementation, or claims from this procedure.

## Propagate owner intent before downstream work

Whenever owner UAT feedback is received or drained, dispatch a jTestEngineer lane in the same work unit. It MUST reconcile and update all four project-localized assets: the ticket's `uat-scenarios.md`, `uat-test.md`, `nfr.md`, and executable UAT-scenarios E2E source. Resolve exact paths and names through the project's `/test` localization hook and E2E manifest; do not guess a global filename.

Encode every owner observation in scenario/GWT intent, the UAT test document, applicable NFR clauses, and executable end-to-end coverage so the chain remains user intent → design → tests → code. Defect-ledger registration alone is not sufficient, and propagation may not be deferred to the next preparation or close cycle.

When owner feedback carries defects, the orchestrator must trigger a /fix cycle; recording the finding without routing it to /fix is not a completed disposition.

When a processed owner-feedback receipt confirms an accepted behavior outcome absent from or contradictory to current GWT, the orchestrator must route and complete canonical `uat-scenarios`/GWT authoring in the same work unit before that `/fix` cycle; record the successor clause and never defer the update to preparation or close.

When evidence falsifies a scenario premise, the chain requires a recorded successor clause (replace, not strike-through); a loosened clause with no recorded successor is how green-invisible regressions get built.
