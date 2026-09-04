# How the loop works

jSwarm moves one work item at a time through Plan, Build, Test, UAT, Fix,
Close, and Merge. Confusing where you are is the single most common way a
first install fails, so this page states plainly what runs where, and what
each command does and does not do on its own.

## The three operating contexts

You are always in exactly one of three places.

| Context | Where | What runs there |
|---|---|---|
| Terminal, in the jSwarm clone | `~/dev/jswarm` | `check`, `install`, `verify`, `adopt`, `unadopt`, `upgrade`, `uninstall`, `portal` |
| Agent session, in the adopted application repository | your own project | `/jSetup`, `/jPlan`, `/jGo`, `/jTest`, `/jUAT`, `/jFix`, `/jClose`, `/jMerge`, `/jPrecompact` |
| Browser, at the local portal | `http://localhost:8766/uat/` | walking a UAT round, approving fix decisions |

The installer subcommands only ever run in the jSwarm folder. The lifecycle
commands only ever run inside a Claude Code session opened in your own
adopted project, never in the jSwarm folder. The portal is a browser tab you
open separately; nothing types commands into it for you.

## Every command is typed by the user

**No command silently invokes another.** You type each one, in order, and
each command ends by printing the next command to type and where to run it.

`/jGo` builds and stops: it does not run `/jTest`, and it does not issue a
UAT round on its own. `/jTest` does not close anything. `/jFix` does not
hand off to another agent for triage before it starts, and it does not close
acceptance on its own. The one thing jSwarm does on its own is the work
inside a single command you asked it to run; where a command would otherwise
hand off, it stops and tells you the next command instead of running it for
you.

## The loop, in order

```
/jPlan <work-item>  ->  /jGo  ->  /jTest  ->  /jUAT  ->  [you walk the round in the portal]
   ->  /jFix <problem> when the round finds something  ->  back to /jTest
   ->  /jClose  ->  /jMerge
```

| Command | Was | Does |
|---|---|---|
| `/jSetup` | `/jsetup` | Doctor and guided setup from inside the agent session |
| `/jPlan <work-item>` | same | Plan the work item, write the spec |
| `/jGo` | same | Build it (creates `feat/<id>` first, when you're still on the default branch) |
| `/jTest` | `/test` | Automated tests, level 1, and the agent smoke walk, level 2 |
| `/jUAT` | `/uat` | Issue a round to the portal, author scenarios |
| `/jFix <problem>` | `/fix` | Diagnose, Contract, Repair, Prove |
| `/jClose` | same | Close the work item, write the retro |
| `/jMerge` | same | Merge the branch |
| `/jPrecompact` | same | Checkpoint before context compaction |

`/test`, `/fix`, `/uat`, `/jsetup`, `/new-work`, `/implement`, `/close-ticket`,
and `/merge` still work for one release: each prints its new name and
delegates.

The loop above completes with none of the above. `install --with-colgrep`
adds one optional coding-intelligence component: semantic code search,
backed by the `colgrep` CLI and a small bundled MCP server. `/code-overview`
uses it to enrich its bottom-up code pathway when available, but its
top-down, scenario-driven structure works the same without it -- see
[Getting started](getting-started.md#colgrep-optional-code-search).

## Three levels of proof before anything merges

1. **Level 1, automated.** `/jTest` runs the project's own unit and
   integration tests.
2. **Level 2, agent smoke walk.** `/jTest` also drives an agent-led pass
   through the change before asking a human to look.
3. **Level 3, you.** `/jUAT` issues a round to the portal; you walk it
   yourself at `http://localhost:8766/uat/`, mark each step pass or fail, and
   describe what happened. Nothing should merge until the round is clean.

## Inside "prove it": two cycles handing work to each other

The fix cycle establishes that a change is right. The UAT round puts it in
front of you. They repeat until the round is clean.

**The fix cycle**, which `/jFix` runs:

- **Diagnose.** Find the cause, with evidence, before proposing a change.
- **Contract.** Write down what the fix will and will not touch, for you to
  approve.
- **Repair.** Make the change, no wider than the approved contract.
- **Prove.** Show the failure is gone and nothing else broke.

**The UAT round**, which `/jUAT` issues to the portal:

- **Prepare.** Turn scenarios into a round of journeys to walk.
- **Execute.** You work through the steps in the portal, on the real thing.
- **Observe.** You mark each step pass or fail and say what you saw.
- **Feedback.** Your findings land in the repository and reopen the fix
  cycle when something failed.

## What is portable, and what is supported first

Plans, state, reports, UAT rounds, retros, and the portal belong to the
public core and do not depend on any particular agent host, platform, or
tracker. Claude Code is the first supported agent host (`jswarm/host/`),
macOS is the first supported platform (`jswarm/platform/`), and Jira is the
first tracker adapter (`jswarm/tracker/`). A future adapter can add another
host, platform, or tracker without moving the loop underneath it; see
[Getting started](getting-started.md#what-this-guide-supports) for exactly
what this release supports.

## Next

Read [Your first ticket](your-first-ticket.md) for the complete Jira-backed
walkthrough, or [Working without a tracker](without-jira.md) for the same
loop with no tracker configured at all.
