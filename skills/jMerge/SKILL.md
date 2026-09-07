---
name: jMerge
description: Integrate a work item's branch into the target branch: fetch, rebase or merge, push, open a PR when configured, confirm the work item is closed, delete the branch.
---

# /jMerge: Merge a Work Item's Branch

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

Resolve `${TARGET}`: `.jswarm/config.yaml`'s `merge.target` when the project sets one, else the repository's default branch (`main` or `master`, whichever this checkout has).

## Step 1: Check for a remote, fetch, and check eligibility

```bash
HAS_REMOTE=false
git remote get-url origin >/dev/null 2>&1 && HAS_REMOTE=true
[ "$HAS_REMOTE" = true ] && git fetch origin
```

No `origin` remote is not a failure to route around: it is the ordinary state `install.sh adopt` leaves a purely local project in. When there is none, skip the fetch — there is nothing to fetch — and continue with `HAS_REMOTE=false`; every step below says what changes.

Refuse and stop, without writing anything, when: the target branch has an in-progress merge/rebase/cherry-pick; the source branch has uncommitted changes; or `.jswarm/work/<ID>/close.json` is absent (the work item has not been through `/jClose`; merging an unclosed item is not this command's job, run `/jClose` first).

## Step 2: Integrate per the project's setting

Read `.jswarm/config.yaml`'s `merge.strategy` (`rebase` or `merge`; default `rebase` when the key is absent). Integrate against `origin/${TARGET}` when there is a remote, or the local `${TARGET}` branch directly when there is none:

```bash
UPSTREAM="${TARGET}"
[ "$HAS_REMOTE" = true ] && UPSTREAM="origin/${TARGET}"
if [ "$MERGE_STRATEGY" = "merge" ]; then
  git merge --no-ff "$UPSTREAM"
else
  git rebase "$UPSTREAM"
fi
```

A real conflict stops here and is presented to the user directly: the specific files, both sides' changes, and the choice between resolving now or aborting. This skill does not auto-resolve content conflicts; that judgment belongs to the developer.

## Extension steps

Run `PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.ext jMerge`. For each path printed, in order, read the file and carry out its steps here before continuing. If nothing is printed, continue.

## Step 3: Land on the target branch, then push when there is a remote

`${TARGET}` is now behind `feat/<ID>` (Step 2 rebased or merged it onto `${TARGET}`'s own tip), so landing it is always a fast-forward:

```bash
git checkout "${TARGET}"
git merge --ff-only "feat/<ID>"
if [ "$HAS_REMOTE" = true ]; then
  git push origin "${TARGET}"
  [ "$(git rev-parse "${TARGET}")" = "$(git rev-parse "origin/${TARGET}")" ] || { echo "push did not advance origin/${TARGET}"; exit 1; }
else
  echo "No origin remote: ${ID} is integrated locally onto ${TARGET}. Nothing to push."
fi
```

## Step 4: Open a pull request, when configured

Opening a pull request needs somewhere to open it against. When there is a remote and `.jswarm/config.yaml` names a PR host (`pr.enabled: true`), open one with the host's own CLI (for GitHub: `gh pr create`) using the retro at `.jswarm/work/<ID>/retro.md` as the description source. When there is no remote, or `pr.enabled` is absent or `false`, skip this step: Step 3 is the whole of the integration.

## Step 5: Confirm the work item is closed

Read `.jswarm/work/<ID>/close.json` written by `/jClose`. If it is missing, Step 1 already stopped before this point. Confirm the tracker reflects the close (re-run the transition if `/jClose`'s recorded result was a failure, not merely a skip):

```bash
PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.tracker.cli describe --repo "$PROJECT_ROOT"
```

`configured: false` means there is nothing further to confirm upstream; local state (`close.json`, the retro) is the record of closure. For a configured tracker, `describe` is only configuration information, not proof of closure. Run the tracker CLI `resolve <ID> --repo "$PROJECT_ROOT"`; for `status: requires_host`, follow `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/jira-host-bridge.md`, read the real issue, and validate the receipt with `--result-file`. If the requested close transition was not completed, run the tracker `transition` request through that same host procedure and verify the resulting status. Report a failure as failed; never print confirmed closed based only on configuration. Print the actual state or local-only note once.

Flip the plan's status to merged:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/plan_status/cli.py record <ID> 6.closed.merged \
  --actor /jMerge --proof-source post-merge
```

## Step 6: Delete the branch

```bash
[ "$HAS_REMOTE" = true ] && { git push origin --delete "feat/<ID>" || true; }
git branch -d "feat/<ID>" 2>/dev/null || true
```

A remote-delete failure (permissions, branch protection) is reported and does not block the rest of this command: the merge and push already succeeded. With no remote, only the local branch is deleted; there is no remote copy to clean up.

## Step 7: Summary

```
✅ <ID> merged

Source: feat/<ID> -> Target: <TARGET>
Strategy: rebase / merge
Merge SHA: <sha>
Remote: origin / none (integrated locally)
Push: pushed / N/A (no remote)
PR: <url> / N/A (not configured or no remote)
Tracker: confirmed closed / no tracker configured
Branch: deleted / delete failed (see message above)
```

There is no next lifecycle command: `/jMerge` is the last stage of the loop.
