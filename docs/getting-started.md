# Getting started

Install jSwarm on a Mac, verify it, and adopt one application repository. This
is the manual path: every command below runs in a terminal, and you can see
exactly what each one changes before it changes it.

## What this guide supports

- **macOS is the only supported platform. Linux and Windows are not supported.**
- **Claude Code is the only supported agent host. Codex, Cursor, and ChatGPT are not supported.**
- **Jira is optional, and it is the only tracker implemented in this release.** You can run the
  complete lifecycle with no tracker at all; see [Working without a tracker](without-jira.md).

If you use a different platform, agent, or tracker today, jSwarm is not yet a fit. The
boundaries above are real code boundaries (`jswarm/platform/`, `jswarm/host/`,
`jswarm/tracker/`), not marketing language, and a future release can add an adapter
without moving the loop underneath it.

## 1. Before you start

You need a Mac, a local git repository you can safely test against, and about
twenty minutes.

Required (this is exactly what `./install.sh check` verifies):

- **Xcode command line tools**: `xcode-select --install`
- **Homebrew**: install it from [brew.sh](https://brew.sh)
- **Python 3.12**: `brew install python@3.12`
- **git**: `brew install git` (Xcode command line tools normally provide this already)
- **GitHub CLI**: `brew install gh`, then `gh auth login`
- **Claude Code**: `npm install -g @anthropic-ai/claude-code`, then run `claude` once to sign in
- **A git identity**: the name and email used for your commits (not checked by `check`, but
  needed for the commits the loop makes on your behalf)

Only if you use the related feature:

- **fnm and Node 24** are needed for the local review portal.
- **A Jira account and project** are needed only if you connect a project to Jira.

ColGREP semantic code search is **not implemented in this release**. The
`install --with-colgrep` flag exists but sets up nothing: it does not install
ColGREP, configure a service, or register the MCP tools (`colgrep_search`,
`colgrep_list_dev_indices`, `colgrep_search_content`, ...) the `colgrep-search`
skill would call. That skill, and `code-overview` (which depends on it), are
not installed in this release. A decision on shipping ColGREP for a future
release is pending; this page will be updated with real setup steps once it
lands.

## 2. Clone jSwarm

```bash
git clone https://github.com/LSA-Digital/jSwarm ~/dev/jswarm
cd ~/dev/jswarm
```

The examples below keep jSwarm at `~/dev/jswarm`. Every path in jSwarm reads
`${JSWARM_HOME:-$HOME/dev/jswarm}`, so if you clone somewhere else, set
`JSWARM_HOME` before running anything.

Every `./install.sh` command runs from inside this folder.

## 3. Check the machine

```bash
./install.sh check
```

This is read-only and safe to run anywhere. It prints one line per
prerequisite and a specific fix for anything missing. Apply the fixes, then
run `check` again until it is clean.

## 4. Preview and install

```bash
./install.sh install --dry-run
./install.sh install
```

`--dry-run` prints every write the installer would make and changes nothing.
Every subcommand that writes anything supports `--dry-run`, and `--dry-run`
never writes anything at all. Skip `--with-colgrep`: ColGREP is not
implemented in this release (see [What this guide supports](#1-before-you-start)
above), and the flag sets up nothing.

The installer builds the Python virtual environment under `~/dev/jswarm/.venv`,
copies each command in `skills/` into `~/.claude/skills/<name>`, which is the
whole of the Claude Code command surface, and writes the local review
portal's config to `~/.jswarm/decision-review/config.json`. That is all this
step writes globally: it does not touch your project's `CLAUDE.md` or
`.claude/settings.json` (those belong to `adopt`, in step 7, run against your
adopted repository, not here), and it does not write to `~/.claude/agents`.
If a skill or the portal config already exists from an earlier install, the
existing copy is backed up first, to a timestamped folder under
`~/.jswarm/backups/`, before it is replaced.

## 5. Restart Claude Code

Quit every running Claude Code session and start a new one, so it loads the
commands that were just installed.

## 6. Verify the installation

```bash
./install.sh verify
```

This checks three things: the skills directory (`~/.claude/skills`) is in
place, the portal config (`~/.jswarm/decision-review/config.json`) exists,
and the jSwarm virtual environment's Python is present. It names exactly
what to fix when one of them is missing. Do not continue until `verify`
passes.

## 7. Adopt one application repository

Adoption connects one of your own local repositories to jSwarm. Choose a
small repository you can safely test against; its folder is separate from
`~/dev/jswarm`.

```bash
./install.sh adopt <repo-path> [--jira-key <KEY>] [--no-hooks] [--dry-run]
```

- `<repo-path>` is the local application repository you want to adopt.
- `--jira-key <KEY>` is optional. Pass it only if you want this project connected to
  Jira, for example `--jira-key PS`. Without it, jSwarm keeps local state only; see
  [Working without a tracker](without-jira.md).
- `--no-hooks` adopts the repository without jSwarm's guardrail hooks.
- `--dry-run` prints every project file that would change, then stops.

Run the dry run first, then the real adoption:

```bash
./install.sh adopt ~/dev/PassengerSeat --jira-key PS --dry-run
./install.sh adopt ~/dev/PassengerSeat --jira-key PS
```

You should see a `.jswarm/` folder with an adoption marker, a managed block
added to the project's `CLAUDE.md`, and jSwarm's hooks merged into its
`.claude/settings.json`. Adoption merges into existing files; nothing you
already wrote there is replaced.

To connect Jira, add Atlassian's hosted MCP server once, in any Claude Code
session:

```bash
claude mcp add --scope user --transport http atlassian https://mcp.atlassian.com/v2/mcp
```

Then run `/mcp` inside Claude Code and follow the sign-in prompt. There is no
self-hosted Jira container in jSwarm; this is host-specific setup and is
documented as such.

## 8. Start the local review portal

```bash
./install.sh portal --background
./install.sh portal --stop
```

- `--background` starts the portal and gives you the terminal back.
- `--stop` stops a portal already running in the background.

Open [http://localhost:8766/uat/](http://localhost:8766/uat/); it should show
an empty UAT queue. If the port is already in use, the portal reports which
process holds it and how to choose another; it never kills anything for you.

## Recovering from a bad start

- **`./install.sh unadopt <repo-path> [--dry-run]`** removes `.jswarm/`, the
  managed `CLAUDE.md` block, and jSwarm's hook entries from the project,
  restoring `CLAUDE.md` and `.claude/settings.json` to their state from
  before your first `adopt`. Nothing you wrote yourself is touched.
- **`./install.sh upgrade [--dry-run]`** redeploys skills from a newer
  jSwarm checkout and reports the version it moved from and to.
- **`./install.sh uninstall [--keep-backups] [--dry-run]`** removes the
  deployed skills, `~/.jswarm/`, and the portal daemon. It lists any adopted
  repositories and leaves them alone, suggesting `unadopt` for each.
- If `install` is interrupted partway through, running it again resumes from
  the first incomplete step; `verify` always names what is still missing.

## Next

Open Claude Code in your adopted application repository, not in
`~/dev/jswarm`, and run [your first ticket](your-first-ticket.md). To
understand exactly what runs where before you start, read
[how the loop works](how-the-loop-works.md).
