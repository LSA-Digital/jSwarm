# UAT GWT Test-Authoring Dispatch — UAT-R9

Copy this block into every jTestEngineer prompt and every jCoder prompt whose scope includes creating or altering tests. Fill every field. Acceptance comes only from the bound clauses, not from the defect narrative.

## Dispatch identity
- **Ticket:** <KEY>
- **Role:** <jTestEngineer | jCoder with test-authoring scope>
- **Source revision / commit:** <sha>
- **Scenario document:** <absolute path>
- **Step-script document:** <absolute path to TICKET.uat-scenario-steps.md; legacy alias if applicable>
- **Scenario ID:** <UAT-SLUG>
- **Implementation scope (jCoder only):** <exact production files/behavior, or N/A>

## Bound acceptance contract — paste verbatim
Assign a stable label outside each quote (`C1`, `C2`, or the source's label). Do not edit, summarize, or "improve" the clause text.

### <SCENARIO-ID:C1>
<BEGIN VERBATIM GWT>
Given ...
When ...
Then ...
FAIL if ...
<END VERBATIM GWT>

### <SCENARIO-ID:C2>
<BEGIN VERBATIM GWT>
...
<END VERBATIM GWT>

## Boundary and coverage contract
- **Production/user-visible boundary described by C1:** <entry point, action, observable result>
- **Production/user-visible boundary described by C2:** <entry point, action, observable result>
- A satisfying test MUST drive the described boundary. Direct helper calls, constant/dict membership checks, forged fixtures, or private-state assertions are supplemental and do not satisfy a user-visible/full-path clause.
- Each satisfying test name or metadata MUST contain `[<SCENARIO-ID>:<CLAUSE>]`.
- Use real captured payloads/fixtures where the clause depends on production shape; synthetic symmetric fixtures do not prove the contract.

## Required return
| Clause | Test name/path | Boundary driven | RED proof | GREEN proof | Supplemental coverage |
|---|---|---|---|---|---|
| <C1> | ... | ... | ... | ... | ... |

Also return:
1. Files changed.
2. Exact commands and results.
3. Any clause not proven, with `BLOCKED: GWT-CONTRACT-GAP — <why>`.
4. Any new expectation discovered. Do not encode it only in a test; return it for UAT-G1 folding before further work.

## STOP conditions
- Bound GWT is absent, paraphrased, internally contradictory, or lacks an observable outcome/FAIL condition.
- The requested test can only be designed from the defect narrative rather than the clause.
- The proposed "acceptance" test bypasses the production/user-visible boundary named above.
