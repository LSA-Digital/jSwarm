# Security NFR profile

This read-only profile consumes F-39 evidence from declared scan and posture outputs named by the canonical NFR validation recipe. It requires the full NFR reference, metric, threshold, measuring-test references, actual values, evidence paths, producer identity, and evidence freshness.

Evidence contradicting the threshold is `FAIL`. Missing, stale, unsupported, or incomplete evidence is `BLOCKED`; justified non-applicability is reasoned `N/A`; all required proofs passing yields `PASS`. Achievement is derived by AND over required proofs, never hand-flipped.

This profile performs no scanner execution, catalog mutation, or threshold authoring. `nfr-catalog-system` under unchanged F-52 retains NFR authoring authority.
