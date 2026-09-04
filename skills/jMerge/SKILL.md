---
name: jMerge
description: Integrate a work item's branch into the target branch — fetch, rebase or merge, push, open a PR when configured, confirm the work item is closed, delete the branch.
---

# /jMerge — Merge a Work Item's Branch

## Safety contract

- **Default is read-only / no-write.** Invoked with no args, `--help`, or `status`, this skill only inspects and reports; it performs no write, push, or delete.
- **Confirm before any mutation.** Push and branch deletion require explicit confirmation, or an approved dry-run first.

## Usage

```
/jMerge               # auto-detect the work item from the current branch
/jMerge <work-item>    # explicit tracker key or slug
/jMerge --dry-run      # print every action; write nothing
```

Resolve the work item the same way `/jClose` does: an explicit argument parsed with `jswarm.workitem.identity.parse`, else the current branch against `feat/<id>`, else the newest `.jswarm/work/*/state.json`.

## Step 1: Fetch and check eligibility

```bash
git fetch origin
```

Refuse and stop, without writing anything, when: the target branch has an in-progress merge/rebase/cherry-pick; the source branch has uncommitted changes; or `.jswarm/work/<ID>/close.json` is absent (the work item has not been through `/jClose` — merging an unclosed item is not this command's job; run `/jClose` first).

## Step 2: Integrate per the project's setting

Read `.jswarm/config.yaml`'s `merge.strategy` (`rebase` or `merge`; default `rebase` when the key is absent):

```bash
if [ "$MERGE_STRATEGY" = "merge" ]; then
  git merge --no-ff "origin/${TARGET}"
else
  git rebase "origin/${TARGET}"
fi
```

A real conflict stops here and is presented to the user directly — the specific files, both sides' changes, and the choice between resolving now or aborting. This skill does not auto-resolve content conflicts; that judgment belongs to the developer.

## Extension steps

Run `${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.ext jMerge`. For each path printed, in order, read the file and carry out its steps here before continuing. If nothing is printed, continue.

## Step 3: Push

```bash
git push origin "${TARGET}"
[ "$(git rev-parse "${TARGET}")" = "$(git rev-parse "origin/${TARGET}")" ] || { echo "push did not advance origin/${TARGET}"; exit 1; }
```

## Step 4: Open a pull request, when configured

When `.jswarm/config.yaml` names a PR host (`pr.enabled: true`), open one with the host's own CLI (for GitHub: `gh pr create`) using the retro at `.jswarm/work/<ID>/retro.md` as the description source. When `pr.enabled` is absent or `false`, skip this step — the push in Step 3 is the whole of the integration.

## Step 5: Confirm the work item is closed

Read `.jswarm/work/<ID>/close.json` written by `/jClose`. If it is missing, Step 1 already stopped before this point. Confirm the tracker reflects the close (re-run the transition if `/jClose`'s recorded result was a failure, not merely a skip):

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.tracker.cli describe --repo "$PROJECT_ROOT"
```

`configured: false` means there is nothing further to confirm upstream — local state (`close.json`, the retro) is the record of closure. Print the tracker's state (or the local-only note) once.

Flip the plan's status to merged:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/plan_status/cli.py record <ID> 6.closed.merged \
  --actor /jMerge --proof-source post-merge
```

## Step 6: Delete the branch

```bash
git push origin --delete "feat/<ID>" || true
git branch -d "feat/<ID>" 2>/dev/null || true
```

A remote-delete failure (permissions, branch protection) is reported and does not block the rest of this command — the merge and push already succeeded.

## Step 7: Summary

```
✅ <ID> merged

Source: feat/<ID> -> Target: <TARGET>
Strategy: rebase / merge
Merge SHA: <sha>
PR: <url> / N/A — not configured
Tracker: confirmed closed / no tracker configured
Branch: deleted / delete failed — see message above
```

There is no next lifecycle command — `/jMerge` is the last stage of the loop.
