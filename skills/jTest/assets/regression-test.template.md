# TICKET-XXX: Regression Test Result — <accepted UAT scenario id>

**Ticket:** [TICKET-XXX](https://lsadigital.atlassian.net/browse/TICKET-XXX)  
**Accepted UAT scenario:** `<UAT.SCENARIO.ID>`  
**UAT scenario doc:** `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenarios.md`  
**UAT scenario-steps source:** `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenario-steps.md#<phase-or-branch>` (legacy `TICKET-XXX.uat-test.md`)  
**Original bug/debug artifacts:** `<links or paths>`  
**Result date:** YYYY-MM-DD  
**Owner:** `jTestEngineer`

## Verdict

`PASS | FAIL | BLOCKED`

One-paragraph summary of whether this regression now captures the accepted UAT behavior and replays deterministically without live LLM calls.

## Scenario and Branches Covered

| UAT scenario / branch | Source in `uat-scenario-steps.md` (legacy `uat-test.md`) | Expected user-visible outcome | Replay assertion / evidence | Status |
| --- | --- | --- | --- | --- |
| `<scenario id>` main path | `<phase/step>` | `<expected>` | `<test assertion/evidence path>` | `PASS/FAIL/BLOCKED` |
| `<branch id or condition>` | `<phase/step>` | `<expected>` | `<test assertion/evidence path>` | `PASS/FAIL/BLOCKED` |

## Traceability Chain

```text
<UAT scenario id>
  -> <TICKET-XXX.uat-scenario-steps.md phase and branch ids>
  -> <replay test path>
  -> <promoted fixture path>
  -> <regression-inventory row id>
  -> <A/C-to-Test matrix row id>
```

## Capture Run

**Capture mechanism:** whole-pipeline `WORKFLOW_MOCK_AGENTS=capture`  
**Capture fixture proposal:** `tests/fixtures/llm-replay/proposals/<fixture>.json`  
**Capture command:**

```bash
<exact command used>
```

**Capture session id:** `<session id>`  
**Capture evidence paths:**
- `<evidence path>`

**Capture notes:**
- Entry count: `<n>`
- Real provider chain used: `<LLM_PROVIDER_CHAIN / models, no secrets>`
- Relevant logs: `<no llm_replay_miss expected during capture; capture writes noted>`

## Fixture Promotion

**Promoted fixture:** `tests/fixtures/llm-replay/<fixture>.json`  
**Promotion command / action:**

```bash
<exact explicit promotion command or copy action>
```

**Promotion review:**
- Proposal inspected: `yes/no`
- Diff reviewed against existing canonical fixture: `yes/no/N/A`
- Overwrite? `no | yes, explicitly approved because ...`
- Fixture SHA-256 after promotion: `<hash>`

## Replay Test

**Replay test path:** `<tests/e2e/primary/... or tests/integration/...>`  
**Replay mode:** `WORKFLOW_MOCK_AGENTS=instant | realistic`  
**Replay fixture path:** `tests/fixtures/llm-replay/<fixture>.json`

**Replay command:**

```bash
<exact command used for both replay runs>
```

## Determinism Proof

| Check | Run 1 | Run 2 | Required result |
| --- | --- | --- | --- |
| Exit code | `<code>` | `<code>` | `0 / 0` |
| Fixture SHA-256 before run | `<hash>` | `<hash>` | identical |
| Fixture SHA-256 after run | `<hash>` | `<hash>` | identical to before |
| Evidence bundle path | `<path>` | `<path>` | present and green |
| `llm_replay_miss` in logs | `none/found` | `none/found` | none |
| `llm_replay_capture` during replay | `none/found` | `none/found` | none |
| Fixture diff after replay | `none/found` | `none/found` | none |

## Matrix and Inventory References

**Regression inventory row:**

```json
{
  "id": "<row id>",
  "executable_path": "<test path>",
  "title": "<title>",
  "status": "active",
  "uat_refs": ["<UAT.SCENARIO.ID>"],
  "nfr_refs": []
}
```

**A/C-to-Test matrix row:** `<row id or copied row text>`  
**TEST_CATALOG refresh:** `<command/evidence or N/A with reason>`

## Branch Deferrals or Gaps

List every documented UAT branch not covered by this replay test. Use `N/A` only when all branches are covered.

| Branch | Reason not covered | Follow-up / owner-visible deferral |
| --- | --- | --- |
| `N/A` | `N/A` | `N/A` |

## Final Result

- `jTestEngineer` owner confirmation: `<name/date>`
- Runner confirmation: `<agent/date>`
- Ready for promotion-review gate: `yes/no`
