# Working without a tracker

Jira is the first tracker jSwarm can synchronize with, not the definition of
a work item. A work item is a unit of work with a local identity: either a
tracker key, or a local slug you invent. Both forms write identical local
state to `.jswarm/work/<id>/` in your adopted repository, and nothing
downstream of `/jPlan` knows or cares which form you used.

This page walks the complete lifecycle with a slug, `add-csv-export`, and no
tracker configured at all. Everything here runs exactly as it would with
Jira; the only difference is that each tracker-facing step reports that it
was skipped, and keeps going.

## Adopt without a tracker

If you have not adopted your project yet, do it with no tracker key at all:

```bash
./install.sh adopt <repo-path>
```

Adoption writes `.jswarm/config.yaml` with `tracker: {adapter: none}`. Install,
verify, and adopt all succeed with no Jira credentials and no project key.
The full lifecycle below runs unchanged.

## The lifecycle

Every command runs from a Claude Code session opened in your adopted
application repository. You type each one; nothing here runs automatically.

### 1. Plan

```
/jPlan add-csv-export
```

`add-csv-export` is a slug: lowercase letters, digits, and hyphens, starting
with a letter or digit. jSwarm writes the plan and local state under
`.jswarm/work/add-csv-export/` and `.jswarm/plans/`. There is no issue to
update, so nothing is synchronized upstream; the plan itself is complete
without one.

### 2. Build

```
/jGo
```

jSwarm implements against the approved plan, adds tests, and commits on a
branch named for the work item. `/jGo` builds and stops: it does not run
tests beyond what the plan's own phases required, and it does not issue a
UAT round.

### 3. Test

```
/jTest
```

Runs the automated tests (level 1) and an agent smoke walk (level 2). Fix
any blocking failure before issuing a round.

### 4. Issue a UAT round

```
/jUAT
```

Prepares a numbered round of journeys for `add-csv-export` in the local
review portal. Open [http://localhost:8766/uat/](http://localhost:8766/uat/)
from the jSwarm folder (`./install.sh portal --background`), walk each
journey yourself, and mark every step pass or fail.

### 5. Fix anything the round found

```
/jFix the CSV export drops the header row
```

jSwarm diagnoses the cause, writes a plain-language repair contract for you
to approve, repairs it, and proves the fix. Repeat `/jTest` and `/jUAT` until
the round is clean.

### 6. Close

```
/jClose
```

Writes the retro and the close record at `.jswarm/work/add-csv-export/close.json`.
With no tracker configured, the comment and transition steps each report
`skipped: true, "no tracker configured, keeping local state only"` and jSwarm
continues; nothing about closing the work item locally depends on them.

### 7. Merge

```
/jMerge
```

Rebases (or merges, per your project's `merge.strategy`), lands the branch on
your target branch, opens a pull request when one is configured, confirms
`/jClose` already ran, and deletes the branch. `describe()` on the tracker
boundary reports `configured: false`, so the only record of closure is
local: `close.json` and the retro.

A project this tutorial adopted has no `origin` remote either -- `adopt`
never creates one. `/jMerge` detects that and integrates locally: no fetch,
no push, no pull request. Everything above still happens; only the parts
that genuinely need a remote (pushing, opening a PR, deleting the remote
branch) are skipped, each with a one-line reason, the same way a missing
tracker is reported.

## What is different from the Jira path

Nothing about the shape of the work changes. Plans, specs, local state,
UAT rounds, retros, and the merge protocol are all local and complete with
no tracker. Each step that would otherwise have commented on or transitioned
an issue prints one line saying it was skipped and why, then continues; the
lifecycle never stops because a tracker is absent.

## Adding a tracker key later

If you later want a slug's work item connected to a tracker, adopt the
project again with a Jira project key supplied (see
[Getting started](getting-started.md#7-adopt-one-application-repository)) and
use a tracker key for your next work item. Local state under
`.jswarm/work/<id>/` is not migrated automatically; a slug and a tracker key
are two independent identities.

If you type a tracker key such as `PS-14` into `/jPlan` before a tracker is
configured, jSwarm stops with an actionable message pointing you at project
adoption with a tracker key, and offering the slug form instead. It never
invents a placeholder key, and it never silently downgrades a real key to a
slug.
