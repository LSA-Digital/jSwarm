---
name: jUpgrade
description: Check for a newer public jSwarm main checkout, preview an upgrade, and install after the user approves. Does not update the application or Enterprise.
---

# /jUpgrade

Update jSwarm itself. Run from any Claude Code session; all helper commands below
resolve the jSwarm clone, never the application's working directory. Jira is not needed.
This follows **main**, which can contain changes newer than the latest tagged release.
Never call a main commit a released version. A pinned tag or custom branch stops for
a deliberate channel choice; do not switch it automatically.

1. Run the read-only check. It queries public Git refs, without fetching objects,
   editing files, installing anything, or reading credentials:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.upgrade check
```

2. Show current commit, target commit, latest tag, and whether an update exists.
   If already current, stop. If a prior attempt updated source but installation failed,
   offer an explicit repair: preview `install.sh upgrade --dry-run` in the jSwarm clone,
   then use the install step below only after approval. Do not infer success from equal refs.
   Stop on dirty files, wrong origin, custom branch, or missing installation. Do not reset,
   stash, delete, re-adopt, or run sudo to get past a failure.
3. Explain that the next step downloads code and fast-forwards the **jSwarm source**,
   then runs prerequisite checks and prints the installer dry run. Git fetch/merge has
   no installer-style dry run; source will change but deployed skills will not yet change.
   Ask the user to finish active work in other sessions and approve this exact commit.
4. Substitute the full hashes from check (never the literal placeholders):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.upgrade prepare --expected-current <current-sha> --expected-target <target-sha>
```

5. Stop if prepare fails. Otherwise show the complete dry run. Ask approval to apply it,
   including dependencies, backups, managed skill replacement, and any existing ColGREP repair.
   Do not treat initial approval to check as permission to install. Then run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.upgrade install --expected-target <target-sha>
```

6. Report success only if upgrade **and** verification pass. Otherwise preserve the output,
   explain whether source advanced, and stop; no automatic rollback or retries. Ask the user
   to restart Claude Code in the application folder. Do not close sessions yourself.
   If the portal was running, offer a separately approved stop/start after active reviews finish.
   The HUD remains opt-in; Enterprise and application files are not upgraded.

Older installations do not contain this command. Bootstrap it once using the manual update
guide at https://jarviswarm.com/docs/troubleshooting#update-jswarm, then restart Claude.
