# Accessibility NFR profile

This read-only profile consumes project accessibility tests/evidence named by the canonical NFR validation recipe. It requires the full NFR reference, metric, threshold, measuring-test references, actual values, evidence paths, producer identity, and evidence freshness.

Compare the reported actual values with the declared threshold. A contradiction is `FAIL`; incomplete, stale, or unavailable producer evidence is `BLOCKED`; a justified non-applicable requirement is reasoned `N/A`; all required proofs passing is `PASS`. Achievement is derived by AND, never hand-flipped.

This profile provides no accessibility service, no catalog mutation, and no threshold authoring. `nfr-catalog-system` under unchanged F-52 remains the NFR authoring authority.
