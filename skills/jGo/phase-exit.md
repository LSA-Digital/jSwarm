# /jGo Phase Exit — Accept and Advance One Phase

Use this companion only after every planned task in the active phase is complete.

1. Confirm task/A-C completion and run only applicable planned gates: tests,
    smoke/demo, UAT, and NFR. Their current owners are `/jTest` and `/jUAT`;
    self-skip only when the plan makes a gate inapplicable.
   When the plan carries `## UAT Execution Trigger`, consume its extracted and
   validated document through `jswarm/uat_trigger.py::evaluate_phase_exit`.
   `BLOCK` halts acceptance with its typed reason. `DISPATCH` invokes
   `/jTest uat prepare <TICKET>` and keeps acceptance blocked until the current
   package/script/build PASS receipt has all six freshness fields and matching
   identities. `SKIP` records its reason without dispatch; `PROCEED` continues.
   Plans without this section retain the existing gate behavior.
2. Complete the existing phase transaction: verify claims, update the plan,
   use its existing per-phase commit/push discipline, and run the localized
   `component-attach-at-creation` step when supplied. It is advisory and
   fail-open for projects without that localization.
3. Only after phase acceptance, invoke the existing `phase-advanced` dashboard
   mechanism. Preserve `implement_progress_contract` and the append/fold/
   compose/check owner contract; do not hand-write dashboard machine state.
4. Invoke `/jPrecompact TICKET-XXX` at this checkpoint. It owns checkpoint,
   compaction, and resume state.

In `--phased` mode, report the accepted phase and wait for approval. In
`--continuous` mode, continue to `session.md` and then the next task. Load
`completion.md` only when this was the final accepted phase.
