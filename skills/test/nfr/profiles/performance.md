# Performance NFR profile

This read-only profile consumes F-43/project evidence from declared telemetry or project measuring tests. It requires full NFR reference, metric, threshold, validation recipe, measuring-test references, actual values, evidence paths, producer identity, and evidence freshness.

Compare the actual measurement to the canonical threshold: contradiction is `FAIL`; absent, stale, or incomplete required evidence is `BLOCKED`; justified non-applicability is reasoned `N/A`; every required proof passing is `PASS`. Achievement is derived as AND over required proofs, never hand-flipped.

This profile has no telemetry writer, catalog mutation, or threshold authoring responsibility. `nfr-catalog-system` under unchanged F-52 retains NFR authoring authority.
