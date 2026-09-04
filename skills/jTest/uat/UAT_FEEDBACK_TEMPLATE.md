---
schema_version: uat-feedback@1
canonical_schema_version: uat-canonical-package@1
ticket: <TICKET>
round_id: <ROUND_ID>
package_id: <PACKAGE_ID>
package_hash: <PACKAGE_HASH>
sealed_payload_sha256: <SEALED_PAYLOAD_SHA256>
script_id: <SCRIPT_ID>
script_hash: <SCRIPT_HASH>
certified_build_hash: <BUILD_HASH>
folder_path: <TICKET_FOLDER>
app_url: <HTTPS_URL>
login: <OWNER_LOGIN>
observer_available: <true|false>
observer_capture: <BROWSER_CAPTURE_METHOD_OR_N/A>
observer_fallback: <IMMEDIATE_SERVER_CAPTURE_OR_N/A>
recovery_policy: SETUP_REFRESH_ONLY; POST_ACTION_REFRESH_RELOAD_RETRY_CANNOT_PASS_ORIGINAL_ATOM
known_sources_checked: [<SOURCE_REF>]
generated_at: <UTC_TIMESTAMP>
processing_state: UNPROCESSED
feedback_verdict: N/A
scenario_results: {<JOURNEY_ID>: {requirement_ref: <AC_REF>, scenario_outcome: NOT_RUN, finding_severity: NONE, finding_disposition: OPEN}}
scenario_result_reasons: {}
---

# UAT feedback

Record independent scenario outcome and finding values: `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`, or reasoned `N/A`; severity `NONE`, `MINOR`, or `MAJOR`; and the matching disposition. many scenarios may map to one AC. do not infer from free-text comments. For N/A, copy the exact reason into candidate summary. `PASS_WITH_FINDINGS` is a derived ledger verdict. Tool-owned receipt: `<TOOL_OWNED_RECEIPT>`.

## Canonical journey walk

Record each journey's copied `journey_id`, `requirement_ref`, `source`, `scenario_id`, `uat_test_anchor`, actions, outcomes, atom IDs, known items, and structured result. Setup refresh is allowed only before the observed action; it cannot pass the original atom after action.

BEGIN NORMALIZED PACKAGE
```json
{"schema_version":"uat-canonical-package@1","ticket":"<TICKET>","round_id":"<ROUND_ID>","package_id":"<PACKAGE_ID>","journeys":[{"journey_id":"<JOURNEY_ID>","requirement_ref":"<AC_REF>","source":"<SOURCE>","scenario_id":"<SCENARIO_ID>","uat_test_anchor":"<ANCHOR>","actions":["<ACTION>"],"outcomes":[{"atom_id":"<ATOM_ID>","expected":"<EXPECTED>","fail_if":["<FAIL_CLAUSE>"]}],"atom_ids":["<ATOM_ID>"],"known_items":[{"text":"<KNOWN_ITEM>","source_ref":"<SOURCE_REF>"}]}]}
```
END NORMALIZED PACKAGE
