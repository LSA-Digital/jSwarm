# /jGo Session: Admission and Execution Context

Use this companion after the facade has resolved an invocation. Return one
executable context or one clear blocker; do not start a task here.

1. Run the existing main-sync preflight. In a worktree, inspect its own branch
   state and ignored chaff only: never push or pull `main` from the worktree.
   Escalate tracked-file dirt or divergence under the existing authorization
   contract.
2. Resolve explicit path, ticket, or auto-detected plan. Prefer
   `.jswarm/plans/` first and use `docs/plans/` only as the legacy fallback;
   never mix them. Confirm the plan is executable (A/C, phases, traceability,
   and team facts) and its status permits execution.
3. Apply `managed_commands.implement.md` localization and project-required
   reading. Gather the ticket, plan path, active phase, next incomplete task,
   A/C progress, selected `--phased` or `--continuous` mode, declared team,
   testing/UAT/E2E/NFR applicability, worktree facts, and status.
4. Before task work, run the read-only ColGREP lifecycle check. ColGREP is
   optional; an uninstalled or erroring check must never block the loop:

   ```bash
   ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/colgrep_index_lifecycle.py \
     check --command implement --json || echo '{"note": "ColGREP unavailable, continuing"}'
   ```

   Continue silently when there is no unsuppressed question, or when the
   command itself failed to run. Surface a single advisory operator choice
   for ambiguous candidates; never auto-apply cleanup, and never block on
   this check.
5. Only after admission succeeds, call the existing dashboard `started`
   mechanism. Preserve the command's `dashboard-facts` contract and
   `implement_progress_contract`; append/fold/compose/check remains owned by
   the existing dashboard contract. Do not write legacy machine cells.

Return a concise context summary and load `task-cycle.md` for the next task.
