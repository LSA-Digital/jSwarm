# UAT rule receipts reference

Lesson reference only. Active imperatives and stop conditions live exclusively in `UAT_RULES.json`; routine loaders do not read this file. Each row is why the paired rule exists, stated as a general lesson, not a record of a specific dated event.

| Canonical key | Lesson | Active binding |
|---|---|---|
| `STALE-STACK-LINEAGE` | A stale or stopped runtime can keep serving old content while everything upstream looks current | UAT-G0 |
| `OWNER-SYNC-GWT-UPDATE` | Confirmed behavior that current GWT doesn't reflect must update GWT in the same work unit, not later | UAT-G1 |
| `ROUND-FRESHNESS` | A round's header and deploy-status statement must stay truthful on every patch, not just at creation | UAT-R1, UAT-R5 |
| `SOURCES-JOURNEY-WALK` | An uncited journey row invites invented content; source every row and cite above the table | UAT-R2 |
| `OWNER-SYNC-DRIFT` | Missing or wrong intent content is upstream drift, not a downstream formatting problem | UAT-R3 |
| `CONFIRMATION-TIME-GWT-SYNC` | Confirmed behavior updates canonical GWT immediately, not at the next convenient checkpoint | UAT-G1 |
| `HOT-COLD-TIMESTAMP-SETS` | A mixed-temperature journey table and semantically named Sets both erode over time | UAT-R4 |
| `ASSET-LOOKUP-CONTRACT` | A referenced term, defect, commit, or session with no lookup entry makes the round unreadable | UAT-R6 |
| `OPEN-LEDGER-COMPLETE` | An OPEN bug missing from HOT, or disagreeing ledgers, lets a known defect fall through | UAT-R7 |
| `NFR-CITATION-GAP` | A reliability-class defect that doesn't name the NFR it threatens makes the gap invisible | UAT-R8 |
| `VERBATIM-GWT-BINDING` | A test bound to a paraphrase or a helper-derived expectation instead of the verbatim clause can pass while proving nothing | UAT-R9 |
| `GWT-DRIFT-SWEEP` | When behavior changes, tests elsewhere whose expectations depended on the old behavior can keep passing against retired behavior unless something forces a clause-by-clause sweep | UAT-R9 |
| `TICKET-OWNED-ROUND` | A round with no fixed, ticket-owned path drifts into ad hoc locations and formats | UAT-T1 |
| `ROUND-FILE-PRECEDENCE` | The durable round file is canonical; a chat-format summary is convenience only and must link back to it | UAT-T1 |
| `QA-DISPATCH-HANDOFF` | Dispatching QA against an unprobed transport or an incomplete handoff wastes the run before it starts | UAT-D1 |
| `COVERAGE-ILLUSION` | A test can look like coverage while proving nothing: asserting on a fixture it also wrote, exercising only a telemetry side effect, or duplicating an anchor under a new name | UAT-D2 |
| `SEMANTIC-DISPATCH-RUBRIC-GAP` | Semantic judgment dispatched against a narrative or a paraphrase, instead of a scenario-bound verbatim rubric, can drift from the scenario it's meant to judge | UAT-D3 |
| `BEHAVIOR-LOCK-INSTRUMENT-ORDER` | Deterministic replay is only trustworthy once behavior is locked; an unlocked behavior needs a current-script walk first | UAT-D4 |

## Receipt interpretation

- ROUND-FRESHNESS binds both UAT-R1 (round header truthfulness) and UAT-R5 (same-work-unit patching): the same underlying lesson, two different points of enforcement.
- NFR-CITATION-GAP established relevance-filtered NFR citation for reliability defects and defect narratives, including data-integrity failures.
- VERBATIM-GWT-BINDING established verbatim GWT binding and production-boundary proof as the bar for a satisfying test.
- ROUND-FILE-PRECEDENCE establishes that the durable round file has precedence over the short chat-format summary and preserves the provenance link.
- SEMANTIC-DISPATCH-RUBRIC-GAP establishes that Layer-2 semantic judgment dispatch requires its own scenario-bound `judgment_rubric`, distinct from UAT-D1's transport/handoff concern.
