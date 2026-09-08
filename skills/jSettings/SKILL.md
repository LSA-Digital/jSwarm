---
name: jSettings
description: Inspect jSwarm settings and configure the optional HUD after preview and approval. No project changes.
disable-model-invocation: true
---

# /jSettings

Everyday settings inside Claude Code. Resolve the jSwarm clone from JSWARM_HOME
or the standard location; do not change application files or require Jira.

1. Start with the read-only overview, from any folder:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.settings status
```

2. Offer HUD status, enable, or disable; explain that quota values depend on the
   current host/session data. No provider collectors or routing are installed here.
   Display customization is not implemented; do not invent toggles or flags.
3. For the chosen HUD action, run a preview. Substitute enable or disable for ACTION:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.settings hud ACTION
```

4. Show the preview. If another status line exists, explain that replacement is
   optional and its previous value will be saved. Only after the user chooses to
   replace it, preview again with --replace-existing. Never use that flag silently.
5. Ask approval to apply the exact preview. Use its SHA-256, not a placeholder,
   with the same action and replacement flag used in the approved preview:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.settings hud ACTION --apply --approved-sha SHA
```

6. A changed configuration requires a new preview and approval. Enable executes
   the actual HUD launcher for verification; report failures without claiming it
   works. Ask the user to restart Claude Code in their application folder and
   visually check the live session. Never close a session automatically.

## Private feedback connection

The overview reports whether a credential is saved, not that server authorization
or inbox delivery has been verified. A normal feedback report can be prepared offline.

If the user wants direct sending, LSA must issue a scoped feedback credential and
activate its server digest. Never request a Brevo, GitHub, or AI-provider key.
Never ask for the credential in chat, read password stores, or print its contents.
Preview using `python -m jswarm.settings feedback connect` via the environment
prefix above. After approval, have the user run the following once in an interactive
Terminal in their jSwarm clone; the hidden prompt requires the user, not the agent:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.settings feedback connect --apply
```

Wait for completion. For removal, preview `feedback disconnect`, ask approval,
then use `feedback disconnect --apply` via the helper. This removes only the local
credential; LSA must revoke the server digest separately. Drafts and send records
remain. The browser form is still an optional fallback with its own CAPTCHA.

Use /jUpgrade for updates and /jFeedback for reports. Do not run either automatically.
