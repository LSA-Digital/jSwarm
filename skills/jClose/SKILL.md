---
name: jClose
description: Close a work item: update its plan status, write the retro, sync the tracker (when one is configured), and record the close.
---

# /jClose: Close a Work Item

## Safety contract

- **Default is read-only / no-write.** Invoked with no args, `--help`, or `status`, this skill only inspects and reports; it performs no write.
- **Confirm before any mutation.** State-changing steps below run once the invoker (human or agent) has confirmed the work is actually done, normally because `/jGo` finished the build and, when the loop needed them, `/jTest`, `/jUAT`, and `/jFix` already ran. `/jClose` does not re-verify that itself; an installation that wants a hard gate here adds one through the extension point below.

## Usage

```
/jClose               # auto-detect the work item from the current branch or the newest .jswarm/work/*/state.json
/jClose <work-item>    # explicit tracker key (PS-14) or slug (add-csv-export)
```

## Step 1: Resolve the work item

If an argument was given, parse it with `jswarm.workitem.identity.parse` (a tracker key or a slug; anything else is a usage error naming both accepted shapes). If no argument was given, try, in order: the current branch name against `feat/<id>`, then the single most recently modified `.jswarm/work/*/state.json`. If neither resolves, ask which work item to close.

Load `.jswarm/work/<ID>/state.json`, written by `/jPlan`, for the plan file path. If it is missing, fall back to `.jswarm/plans/<ID>.plan.*.md` and reconstruct `.jswarm/work/<ID>/` (a work item started before this state directory existed still closes cleanly).

## Step 2: Write the retro (mandatory, autonomous)

Every close includes a structured reflection, written by the agent without asking the user questions first.

**Location:** `.jswarm/work/<ID>/retro.md`. If it already exists (an earlier `/jClose` pass, or `/jPrecompact` seeded it), **append**: add a dated section and a Changelog row; never overwrite the existing body.

**Evidence sources:** the plan file (scope, phases, deferred items), `git log --oneline` for this work item's commits, and the conversation context (stalls, rework, surprises).

**Template** (when creating fresh):

```markdown
# Retrospective for <ID>: [Short Title]

**Date:** YYYY-MM-DD
**Work item:** <ID> (tracker: [link] | local slug)
**Plan:** .jswarm/plans/<ID>.plan.<descriptive>.md
**Duration:** [start -> close]

## Summary
[1-2 sentences]

## Retrospective
[Categories with findings only; skip a category with nothing to say]

### [Category]
| | Finding |
|---|---|
| + | [what went well] |
| - | [what didn't] |
| Δ | [what to change] |

## Action Items
| # | Action | Owner | Target | Status |
|---|---|---|---|---|

## Changelog
| Date | Author | Change |
|---|---|---|
| YYYY-MM-DD | [agent/human] | Retro created via /jClose |
```

## Extension steps

Run `PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.ext jClose`. For each path printed, in order, read the file and carry out its steps here before continuing. If nothing is printed, continue.

## Step 3: Sync the tracker

Comment with the retro summary, then transition the work item, through the tracker boundary; never by inventing tracker behavior in this file. Both calls are safe to run whether or not a tracker is configured:

```bash
PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.tracker.cli comment <ID> --repo "$PROJECT_ROOT" --text "$(cat <<'EOF'
Closed via /jClose.
Retro: .jswarm/work/<ID>/retro.md
Plan: .jswarm/plans/<ID>.plan.<descriptive>.md
Next: /jMerge
EOF
)"
PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.tracker.cli transition <ID> --repo "$PROJECT_ROOT" --state Done
```

Print each result's `message` once. `skipped: true` (no tracker configured) and `ok: false` (tracker reachable but the call failed) are both non-blocking: local state from Steps 1-2 was already written; continue to Step 4 either way.

## Step 4: Update plan status and record the close

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/plan_status/cli.py record <ID> 5.closed.ready_for_merge \
  --actor /jClose --proof-source close
```

This is idempotent when `/jGo` already recorded this state; an `advisory-skip` or `error` result is local bookkeeping, not a close blocker; print it and continue.

Record the close itself at `.jswarm/work/<ID>/close.json`:

```json
{
  "id": "<ID>",
  "closed_at": "<UTC ISO-8601>",
  "retro": ".jswarm/work/<ID>/retro.md",
  "tracker_comment": {"ok": "...", "skipped": "...", "message": "..."},
  "tracker_transition": {"ok": "...", "skipped": "...", "message": "..."}
}
```

## Step 5: Summary

```
✅ <ID> closed

Work item: <ID> (tracker: [link] / local slug, no tracker)
Plan: .jswarm/plans/<ID>.plan.<descriptive>.md
Retro: .jswarm/work/<ID>/retro.md
Tracker sync: commented + transitioned / skipped (no tracker) / failed (see message above)
Close record: .jswarm/work/<ID>/close.json
```

**Next:** run `/jMerge` in this project's agent session to integrate the branch.
