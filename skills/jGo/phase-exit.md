# /jGo Phase Exit: Accept and Advance One Phase

Use this companion only after every planned task in the active phase is complete.

1. Confirm task/A-C completion and run only applicable planned gates: tests,
    smoke/demo, UAT, and NFR. Their current owners are `/jTest` and `/jUAT`;
    self-skip only when the plan makes a gate inapplicable.
   When the plan carries `## UAT Execution Trigger`, consume its extracted and
   validated document through `jswarm/uat_trigger.py::evaluate_phase_exit`,
   **only for a phase the trigger's own `phases` array actually names**. The
   trigger only ever declares the UAT-relevant phase(s) (see
   `pattern.uat-chain.md`); a phase it is silent on — pure implementation,
   with no UAT relevance — has nothing to evaluate, so skip the call and keep
   the existing gate behavior for that phase instead of invoking it. Calling
   it for a phase absent from `phases` returns `BLOCK` (an unmet
   `phase-exit.phase-id` reason) and would wrongly stop that phase from ever
   exiting; the array being silent on a phase is not itself a block. For a
   phase the trigger does name: `BLOCK` halts acceptance with its typed
   reason. `DISPATCH` invokes `/jTest uat prepare <TICKET>` and keeps
   acceptance blocked until the current package/script/build PASS receipt has
   all six freshness fields and matching identities. `SKIP` records its
   reason without dispatch; `PROCEED` continues. Plans without this section
   retain the existing gate behavior.
2. Complete the existing phase transaction: verify claims, update the plan,
   use its existing per-phase commit/push discipline, and run the localized
   `component-attach-at-creation` step when supplied. It is advisory and
   fail-open for projects without that localization.
3. Invoke `/jPrecompact TICKET-XXX` at this checkpoint. It owns checkpoint,
   compaction, and resume state.

In `--phased` mode, report the accepted phase and wait for approval. In
`--continuous` mode, continue to `session.md` and then the next task. Load
`completion.md` only when this was the final accepted phase.
