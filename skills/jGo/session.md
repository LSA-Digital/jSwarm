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
3. **Branch:** this is the one place in the lifecycle that creates
   `feat/<ID>` — `/jClose`, `/jMerge`, and `/jUAT` only ever resolve against
   it. If the current branch is already `feat/<ID>`, proceed on it. Otherwise,
   only when the current branch is the repository's own default branch
   (`main` or `master`, whichever this checkout has) and `feat/<ID>` does not
   already exist, create and switch to it:

   ```bash
   git checkout -b "feat/<ID>"
   ```

   Any other current branch means the user deliberately checked it out for
   this work; leave it alone and proceed there. Never switch a user off a
   branch they are already on.
4. Apply `managed_commands.implement.md` localization and project-required
   reading. Gather the ticket, plan path, active phase, next incomplete task,
   A/C progress, selected `--phased` or `--continuous` mode, declared team,
   testing/UAT/E2E/NFR applicability, worktree facts, and status.

Return a concise context summary and load `task-cycle.md` for the next task.
