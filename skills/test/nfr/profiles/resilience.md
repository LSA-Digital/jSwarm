# Resilience NFR profile

This read-only profile consumes F-43/project evidence from declared resilience telemetry or project measuring tests. Required input is full NFR reference, metric, threshold, validation recipe, measuring-test references, actual values, evidence paths, producer identity, and evidence freshness.

Threshold contradiction is `FAIL`. Missing, stale, unsupported, or incomplete producer evidence is `BLOCKED`; justified non-applicability is reasoned `N/A`; `PASS` requires every mapped proof. Achievement is an AND derivation and cannot be hand-flipped.

This profile creates no telemetry writer, catalog mutation, or threshold authoring. `nfr-catalog-system` under unchanged F-52 remains the NFR authoring authority.
