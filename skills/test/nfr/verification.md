# NFR verification adapters

`/test` consumes verification evidence read-only. It does not author NFRs, mutate the NFR catalog, author thresholds, or run producers. `nfr-catalog-system` retains NFR authoring authority under the unchanged F-52 `/jconfig Installer & Component Catalog` identity.

## Required adapter input

Every mapped proof supplies these fields: `full_nfr_reference`, `metric`, `threshold`, `validation_recipe`, `measuring_test_refs`, `actual_values`, `evidence_paths`, `producer_identity`, and `evidence_freshness`. The adapter also records `profile` and the complete `required_proofs` set.

The typed verdict vocabulary is `PASS|FAIL|BLOCKED`. `achievement` is derived as an AND over `required_proofs`; it is never hand-flipped. A missing threshold, measuring test, actual value, evidence path, eligible producer, or fresh evidence is `BLOCKED`, not an invented measurement or PASS. A measured contradiction with the threshold is `FAIL`; only every required proof passing yields `PASS`. `N/A` is allowed only with a stated applicability reason and never substitutes for missing evidence.

F-61 semantic evidence is consumed only when the canonical NFR validation recipe explicitly cites its measuring test. F-61 retains execution authority.

## Boundary

Profiles select and interpret existing producer evidence. They do not create a second writable ledger, parser, catalog, telemetry writer, scanner, service, database, or queue.
