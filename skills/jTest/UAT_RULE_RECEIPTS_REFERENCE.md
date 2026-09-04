# UAT rule receipts reference

Cold historical evidence only. Active imperatives and stop conditions live exclusively in `UAT_RULES.json`; routine loaders do not read this file.

| Canonical key | Receipt / historical contract | Date / lineage | Active binding |
|---|---|---|---|
| `STALE-STACK-LINEAGE` | Stale-stack owner incident lineage | 2026-07-05 and 2026-07-08 | UAT-G0 |
| `OWNER-SYNC-2026-07-12` | Owner-expectation sync directive | Originated 2026-06-12; restated four times 2026-07-12 | UAT-G1 |
| `FRESHNESS-108` | Freshness receipt | #108, 2026-07-08 | UAT-R1, UAT-R5 |
| `SOURCES-JOURNEY-WALK` | Sources-column removal and Journey/Walk contract | 2026-07-11 | UAT-R2 |
| `OWNER-SYNC-DRIFT` | Missing/wrong intent is an upstream drift alarm | 2026-07-12 | UAT-R3 |
| `COM-386-CONFIRMATION-TIME-GWT-SYNC-2026-08-25` | Confirmation-time GWT synchronization | Owner requirement: collected feedback or otherwise confirmed behavior updates canonical GWT immediately; 2026-08-25 | UAT-G1 |
| `HOT-COLD-TIMESTAMP-SETS` | HOT/COLD split and timestamp-only Sets | 2026-07-09 | UAT-R4 |
| `ASSET-LOOKUP-CONTRACT` | Definitions, pill keys, and owner-openable session links | 2026-07-08 | UAT-R6 |
| `OPEN-LEDGER-COMPLETE` | OPEN-ledger, Who, executable-step, and both-ledgers completeness | 2026-07-09 | UAT-R7 |
| `HAS-520-19-NFR-520-5-2026-07-12` | NFR citation failure | HAS-520 #19; NFR-520-5; 2026-07-12 | UAT-R8 |
| `HAS-520-FIX19-REVISE-2X-2026-07-12` | GWT test-contract failure | HAS-520 fix19; two jCritic REVISE rounds; 2026-07-12 | UAT-R9 |
| `HAS-617-GWT-DRIFT-SWEEP-2026-08-25` | GWT-change unit/integration drift sweep | HAS-617 eleven stale expectations in two modules; clause-by-clause attribution all TEST-DRIFT; localized precursor 78b110d7f; 2026-08-25 | UAT-R9 |
| `TICKET-OWNED-ROUND` | Ticket-prefixed plan-folder ownership and controlled template | 2026-07-09 | UAT-T1 |
| `COM-249-SLICE-C-F06-F07` | F-06 precedence/provenance: round file over chat | COM-249 Slice C F-06/F-07 | UAT-T1 |
| `QA15-QA21` | QA dispatch receipts: transport and complete handoff | QA15 2026-07-08; QA16/QA17 2026-07-09; QA21 2026-07-10 | UAT-D1 |
| `COVERAGE-ILLUSION-2026-07-12` | Coverage-illusion class | fake test anchors ×2; telemetry extras-only test; forged `connectivity_repair` fixture; week of 2026-07-12 | UAT-D2 |
| `COM-307-SEMANTIC-DISPATCH-RUBRIC-GAP` | Semantic judgment dispatch had no scenario-bound rubric contract | COM-307 Phase 2, 2026-07-26 | UAT-D3 |
| `COM-376-UAT-D4-BEHAVIOR-LOCK` | Behavior-lock instrument-order contract | 2026-08-20 | UAT-D4 |

## Receipt interpretation

- QA15 established executable specimen facts and known-good walks for UAT-D1. It is not the source for UAT-R6's round-file lookup-section contract.
- QA16 and QA17 established standing browser mechanics and post-action mutation verification.
- QA21 established transport health probing and the complete 13-field dispatch handoff.
- The HAS-520 #19 / NFR-520-5 miss established relevance-filtered NFR citation for reliability defects and defect narratives, including data-integrity failures.
- The HAS-520 fix19 REVISE history established verbatim GWT binding and production-boundary proof.
- COM-249 Slice C F-06/F-07 establishes that the durable round file has precedence over the short chat-format summary and preserves the provenance link.
- COM-307 Phase 2 establishes that Layer-2 semantic judgment dispatch requires its own scenario-bound `judgment_rubric`, distinct from UAT-D1's transport/handoff concern.
