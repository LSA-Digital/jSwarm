# /jGo Task Cycle — One Planned Task

Use this companion only for the next incomplete task in an admitted session.

1. Bind that task to its stated A/C, planned proof, affected surface, and
   applicable test layer. Search existing coverage before changing it; perform
   practical RED -> minimal implementation -> GREEN work when the plan requires
   a behavior change.
2. Route governance and delegation through
   [`subagent-environment`](../subagent-environment/SKILL.md), tests through
   [`/jTest`](../jTest/SKILL.md), and check-ins through
   [`/jCheckin`](../jCheckin/SKILL.md). The jPlan rubric owns whether review
   activates and its tier. Do not duplicate their long procedures or create
   typed receipts.
3. Use the planned implementation lane and normal verification. For an
   unexpected defect or blocked environment, stop or use the existing `/jFix`
   route rather than inventing a fallback.
4. Verify returned claims against disk and the actual command output before
   accepting the task. Update the plan's task and A/C state, validation, and
   next action using its existing authority; do not claim completion from an
   agent summary alone.

If another task remains in the phase, load this companion again. Load
`phase-exit.md` only after every task in the active phase is complete.
