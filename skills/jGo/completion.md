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
   ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/plan_status/cli.py record TICKET-XXX \
     5.closed.ready_for_merge --actor /jGo --proof-source verification-complete
   ```

## Extension steps

Run `PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.ext jGo`. For each path printed, in order, read the file and carry out its steps here before continuing. If nothing is printed, continue. This is the hook point for anything wanting to observe or record completion (a dashboard, a metrics sink) without this repo hardcoding an integration it does not ship.

Report concise completion state. `/jGo` builds and stops here. **Next:** run
`/jTest` in this project's agent session.
