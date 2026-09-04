# /jTest: Diagnose (option 10)

Read `execution-protocol.md` first (MASTER INVARIANT in `SKILL.md`).

## Option 10: Diagnose a failed or flaky testing run

1. Classify the failed tier: unit, integration, live-show, regression, smoke, or diagnostic.
2. Check whether the right runner was used. If a wrapper exists and the run used raw `npx playwright`, rerun through the wrapper.
3. For live-show failures, inspect:
   - malformed handoff fields
   - headed/foreground browser visibility
   - runtime monitor output
   - console/network failures
   - stale state/preflight failures
4. For regression failures, inspect:
   - deterministic data setup
   - evidence bundle assertion failures
   - trace/video/screenshots
   - backend/API contract drift
5. If failed test/run evidence says `gate`, `invariant`, `rejected`, `escalated`, `blocked`, `violation`, or `pre_conditions_failed`, treat it as a rules/invariant failure rather than a flaky run: identify and check the specific rule or invariant these tags name before generic tracing. A non-rule provider/runtime `429` is a transient provider issue, not a test defect; retry rather than treating it as a rule violation.
6. After two repeated attempts without root cause, stop and use `/jFix` or a debugger/architect consultation.

Cross-links: `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/testing/uat-fix-playbook.md`: "Verify-first, always" (classify real vs stale vs artifact before tracing) and "Live-instrumentation ladder + the two-fault rule" (when the same live symptom survives two component reverts, stop reverting and instrument) · `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/uat-fix-cycle.living-process.md` §Agent-death classification (forensic causes for sub-agent failures encountered mid-diagnosis: `overBudget400` context bust vs `fallbackExhausted`/upstream 429).
