# Main-sync preflight

The check every `/jPlan` run performs as **Step 0a**, before any planning
work begins, so a new plan is never seeded from a branch that is dirty or
diverged from `main` in a way that would corrupt the merge state machine
later (see [`state-machine.md`](state-machine.md)).

This is a documented procedure the agent follows, not a standalone script —
`/jPlan` (`skills/jPlan/operations.md`, Step 0a) is the one caller. It is
**silent on success**: emit exactly one line of confirmation before moving
to Step 1, and say nothing else unless one of the escalation conditions
below applies.

## What it checks, and what it does about each

1. **Gitignored untracked chaff.** Build artifacts, caches, or other
   untracked-and-ignored files sitting in the working tree
   (`git status --porcelain --ignored` shows them as `!!`). These are safe
   to remove because git itself already excludes them from anything that
   could be committed or merged.
   → **Auto-resolve silently**: clean them (equivalent to
   `git clean -fdX`, which only touches ignored paths, never tracked or
   plain-untracked ones) and continue.

2. **Ahead-only push.** The local branch has commits `origin/<branch>` does
   not, and nothing new has landed upstream (`git rev-list --count
   HEAD..@{u}` is `0`).
   → **Auto-resolve silently**: fast-forward push (`git push`) and
   continue.

3. **Behind-only pull.** `origin/<branch>` has commits the local branch does
   not, and the local branch has nothing unpushed
   (`git rev-list --count @{u}..HEAD` is `0`).
   → **Auto-resolve silently**: fast-forward pull (`git pull --ff-only`)
   and continue.

4. **Tracked-file dirt.** `git status --porcelain` shows staged or unstaged
   changes to tracked files (anything not `!!` and not clean).
   → **Escalate.** Do not stash, discard, or commit on the operator's
   behalf. Report exactly what is dirty and let the operator decide
   (commit, stash, or discard) before `/jPlan` proceeds.

5. **Diverged branch.** Both `HEAD..@{u}` and `@{u}..HEAD` are non-zero —
   local and remote have each moved independently.
   → **Escalate.** A merge or rebase decision belongs to the operator, not
   to a silent auto-resolve; report the ahead/behind counts and stop.

Checks run in the order above because later checks assume the working tree
is already clean of ignored chaff, and the push/pull checks are mutually
exclusive with divergence (an ahead-only or behind-only tree cannot also be
diverged).

## Why this exists

A plan seeded from a branch with uncommitted tracked changes or unresolved
divergence inherits that instability: `/jGo` implementation phases,
`/jMerge`'s eventual gate, and the `plan_status` -> `status` derivation in
[`state-machine.md`](state-machine.md) all assume the plan's branch state
was sane at `0.planning.lite_init`. Catching drift here, once, at the front
door, is cheaper than diagnosing it three lifecycle stages later.

## Scope

This preflight only ever touches the working tree in the two auto-resolve
cases above (removing ignored files, fast-forwarding a clean push or pull).
It never rewrites history, never force-pushes, and never resolves tracked
conflicts on its own — those always escalate to the operator.
