# Update jSwarm

Run these commands in **Terminal, in your jSwarm clone**, not in your application
repository and not as slash commands inside Claude Code. The example uses the
default `~/dev/jswarm` location; substitute your actual installation folder.

You do not need to uninstall, delete your project, repeat adoption, or authenticate
Jira again. Finish your current work and close Claude Code before updating.

## 1. Check your checkout

```bash
cd "${JSWARM_HOME:-$HOME/dev/jswarm}"
git status --short
git branch --show-current
git log -1 --format='%h %s'
```

If status lists local changes, stop and preserve them before continuing. Do not
discard them or use a force reset. If you deliberately use a custom branch or a
release tag, review its update path rather than switching it automatically.

## 2. Get the latest main

For a clean checkout following the public `main` branch:

```bash
git switch main && git pull --ff-only origin main
./install.sh check
./install.sh upgrade --dry-run
```

Stop if any command fails. `--ff-only` refuses to merge diverged history.
`git pull` updates the source files; it does **not** replace the installed copies
of your Claude skills. Review the dry run before applying the next step.

The latest `main` includes the v1.1 feedback/HUD work. A commit on `main` is not
a published release tag; record the commit above when reporting a problem.

## 3. Apply and verify

```bash
./install.sh upgrade && ./install.sh verify
git log -1 --format='%h %s'
```

Upgrade refreshes Python dependencies, backs up and redeploys managed skills,
and repairs ColGREP if it was already installed. It preserves existing project
adoption and does not turn on the optional HUD. If there is no existing install,
follow [Getting started](getting-started.md) instead.

Open a fresh Claude Code session in your **application folder**. Confirm the new
`/jFeedback` command is available. Older sessions may retain previously loaded skills.

If the local portal was running, restart it after finishing any active review.
Preview each change, then apply it:

```bash
./install.sh portal --stop --dry-run
./install.sh portal --stop
./install.sh portal --background --dry-run
./install.sh portal --background
```

## 4. Optional: enable the new HUD

Still in Terminal in the jSwarm clone:

```bash
./install.sh hud enable --dry-run
./install.sh hud enable
./install.sh verify
```

Restart Claude Code again and check the status line. If you already have a status
line, the installer stops rather than replacing it without approval. See
[Feedback and HUD](feedback-and-hud.md) for replacement, restoration, and quota-source limits.

Updating the public core does not install or upgrade Enterprise.
