# Your first ticket

Take one small change from a work item to a reviewed merge. This is the
reference path: macOS, Claude Code, and Jira. The example project is
Passenger Seat, and the example issue is `PS-14` in a Jira project with key
`PS`. Jira is the first tested tracker adapter, not a requirement of jSwarm;
if you would rather follow the same loop with no tracker at all, read
[Working without a tracker](without-jira.md) instead.

## Who runs this

The person responsible for the outcome. A solo builder does both the
development and acceptance steps. On a team, a developer runs the lifecycle
commands while a product owner or subject-matter expert walks the UAT round.

## What you will do

You will plan one small work item, build it, run two agent-led test levels,
issue a UAT round, test it yourself, repair anything that failed, then close
and merge it. Nothing should merge until the UAT round is clean. See
[How the loop works](how-the-loop-works.md) for the complete picture; on
this page, every command states exactly where it runs.

## 0. Confirm the setup

Finish [Getting started](getting-started.md) first: installer verification
should pass, and the local portal should open at
`http://localhost:8766/uat/`.

| Context | Where | What runs there |
|---|---|---|
| Terminal | `~/dev/jswarm` | installer and portal commands |
| Claude Code | your application folder, for example `~/dev/PassengerSeat` | every `/j...` lifecycle command |
| Browser | `http://localhost:8766` | UAT walks and fix decisions |

Old names (`/test`, `/fix`, `/uat`) still work for one release and point to
their new names.

## 1. Choose one small work item

Pick an existing Jira issue you can safely use for the first run. The
example here is `PS-14` in project `PS`. Do not import an entire backlog
yet; the goal is to prove one complete cycle with as little setup as
possible.

## 2. Confirm repository adoption

Skip this step if [Getting started](getting-started.md) already adopted this
repository.

```bash
./install.sh adopt <repo-path> --jira-key <KEY>
```

You should see `.jswarm/.adopted`, a managed block in `CLAUDE.md`, and
merged project hooks in `.claude/settings.json`.

## 3. Plan the work item

```
/jPlan PS-14
```

Answer the questions jSwarm asks. For the first run, keep the change
deliberately small.

You should see a plan under `.jswarm/plans/`. With Jira configured, the
issue also receives the planning update.

## 4. Build from the approved plan

```
/jGo
```

You should see the agent implement against the plan, add tests, and commit
on a branch for this work item. Building does not replace the explicit test
and UAT commands that follow; `/jGo` builds and stops.

## 5. Run automated tests and the agent smoke walk

```
/jTest
```

You should see level 1 automated test results and a level 2 agent smoke
walk. Fix any blocking failure before issuing a UAT round.

## 6. Issue the UAT round

```
/jUAT
```

You should see a numbered round for `PS-14` in the local UAT queue. This
command prepares the journeys; it does not approve them for you.

## 7. Test the change yourself

From the jSwarm folder:

```bash
./install.sh portal --background
```

Open [http://localhost:8766/uat/](http://localhost:8766/uat/), choose the
newest round for `PS-14`, and follow each journey. Mark every step pass or
fail and describe what happened. Stop at a failure that blocks the remaining
steps; partial feedback is still useful.

You should see the submitted round and its findings recorded in the
repository, ready for the next fix cycle.

## 8. Fix anything that failed

```
/jFix the narration talks over the turn-by-turn directions
```

jSwarm diagnoses the cause, proposes a bounded repair contract for your
approval, repairs the problem, and proves it again. Then repeat `/jTest`,
`/jUAT`, and your portal walk until the round is clean.

You should see a fix-decision review in the portal, followed by new test
evidence and a new UAT round once you approve it.

## 9. Close the work item

```
/jClose
```

You should see a local retro and close record. With Jira configured, jSwarm
also comments on and transitions the Jira issue. Without a tracker, it
reports that synchronization was skipped; see
[Working without a tracker](without-jira.md).

## 10. Merge the reviewed branch

```
/jMerge
```

You should see the reviewed branch merged through your repository's
configured merge protocol (rebase by default, or merge if your project sets
`merge.strategy: merge`).

One session, one work item: keep the session bound to `PS-14` from `/jPlan`
through `/jMerge`.

## 11. Repeat with the next small item

Start a new Claude Code session in your application folder and run
`/jPlan PS-15`. Each retro becomes useful context for the next plan.
