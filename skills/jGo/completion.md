# /jGo Completion: Ready for Merge

Use this companion only after every phase is accepted.

1. Confirm all A/C, applicable tests, smoke/demo, UAT, NFR, feature, and
   infrastructure checks are complete through their existing owners. Apply the
   current jPlan rubric to the final delta: a review occurs only when it
   activates; otherwise record the non-author orchestrator review.
2. Run the canonical completion gate before promotion:

   ```bash
   ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_ticket/cli.py \
     --ticket TICKET-XXX --repo-root . --preset implement-gate
   ```

   `update-ticket` owns audit-stage `implement` grammar and result eligibility;
   act on its result before continuing.
3. Record, rather than hand-edit, the ready status:

   ```bash
   .venv/bin/python jswarm/plan_status/cli.py record TICKET-XXX \
     5.closed.ready_for_merge --actor /jGo --proof-source verification-complete
   ```

4. After final gates, `implement-gate`, and the ready status record succeed,
   call the existing `ready-for-merge` dashboard event through the established
   `implement_progress_contract` owner contract. Do not create a receipt schema
   or write legacy dashboard machine cells.

Report concise completion state. `/jGo` builds and stops here. **Next:** run
`/jTest` in this project's agent session.
