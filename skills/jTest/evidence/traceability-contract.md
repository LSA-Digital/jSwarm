# Acceptance-quality traceability contract

The sole writable ledger is `.jswarm/plans/<TICKET>/<TICKET>.test-traceability.md` using `schema_version: acceptance-quality@1` and `TEST_TRACEABILITY_TEMPLATE.md`.

Its frontmatter is `ticket`, `generated_at`, `latest_test_report_path`, `overall_verdict`, `processing_state`, `coverage`, `uat_package`, `processing_log`, and `legacy_import_receipt`. Rows are exactly `UAT`, `NFR`, or `STRUCTURAL`; UAT rows record independent outcome, Finding severity, and finding disposition. Multiple UAT rows may map to one AC through `requirement_ref`.

`overall_verdict` is derived as `FAIL > BLOCKED > INCOMPLETE > PASS_WITH_FINDINGS > PASS`; only PASS and PASS_WITH_FINDINGS are COMPLETE. Scenario/GWT identifiers and full NFR references are consumed read-only identifiers. Legacy `.uat-traceability.md` is a one-time source-SHA import only and is never writable after import.
