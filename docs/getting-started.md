# Getting started

Install jSwarm, verify it, and adopt one application repository. This
is the manual path: every command below runs in a terminal, and you can see
exactly what each one changes before it changes it.

## What this guide supports

- **Use macOS, Linux, or Windows through WSL2/Ubuntu.** Run all commands and Claude Code in that environment. Native Windows PowerShell/CMD execution is not supported. See [Platform setup and validation](platforms.md).
- **Claude Code is the only supported agent host. Codex, Cursor, and ChatGPT are not supported.**
- **Jira is optional, and it is the only tracker implemented in this release.** You can run the
  complete lifecycle with no tracker at all; see [Working without a tracker](without-jira.md).

If you use a different agent or need another tracker integration today, jSwarm is not yet a fit. The
boundaries above are real code boundaries (`jswarm/platform/`, `jswarm/host/`,
`jswarm/tracker/`), not marketing language, and a future release can add an adapter
without moving the loop underneath it.

## 1. Before you start

You need a supported shell environment, a local git repository you can safely test against, and about
twenty minutes.

Required tools (`./install.sh check` reports these; install them by any method):

- **Python 3.12 or newer**, including `venv` support, available as `python3.12` or `python3`.
- **Git**, available as `git`.
- **GitHub CLI**, available as `gh`; then run `gh auth login`.
- **Claude Code**, available as `claude`; run it once to sign in. See [Claude Code setup](https://code.claude.com/docs/en/setup).
- **A git identity**: the name and email used for your commits (not checked by `check`, but
  needed for the commits the loop makes on your behalf)

Only if you use the related feature:

- **Node 24 with npm** is needed for the local review portal. fnm is an option, not a requirement.
- **A Jira account and project** are needed only if you connect a project to Jira.
- **A Rust toolchain** (`cargo`) is needed only for `install --with-colgrep`; see below.
  `check` reports it like any other prerequisite, but it never fails `check` on its own
  -- the core loop works without it.

**ColGREP semantic code search is optional.** Pass `--with-colgrep` to `install` to
set it up: it installs the `colgrep` CLI (`cargo install colgrep`, a public crate at
[crates.io](https://crates.io/crates/colgrep), upstream
[`lightonai/next-plaid`](https://github.com/lightonai/next-plaid)), registers a small
bundled MCP server (`jswarm/colgrep_mcp_server.py`) that wraps it, and installs the
`colgrep-search` and `code-overview` skills. Without the flag, neither skill is
installed and nothing in the core loop calls them. Nothing here runs a
background service or container, and nothing private is involved -- code
search only, over your own checkout. See
[ColGREP setup](#colgrep-optional-code-search) below.

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
never writes anything at all. Add `--with-colgrep` to also set up ColGREP
semantic code search; see [ColGREP setup](#colgrep-optional-code-search) below.
Without it, `colgrep-search` and `code-overview` are skipped and nothing else
in the loop needs them.

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

### ColGREP (optional, code search)

```bash
./install.sh install --with-colgrep --dry-run
./install.sh install --with-colgrep
```

This is the same `install` step, with one more flag. It:

1. Checks for a Rust toolchain (`cargo` on PATH). If it's missing, it prints the
   exact fix (`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y`)
   and stops there -- it never fails the rest of `install` over this, since ColGREP
   is optional.
2. Installs the `colgrep` CLI with `cargo install colgrep` if it isn't already on
   PATH (or verifies the existing one).
3. Registers `jswarm/colgrep_mcp_server.py` -- a small server bundled in this repo
   that wraps the `colgrep` CLI -- as an MCP server with Claude Code
   (`claude mcp add --scope user colgrep -- <python> <server path>`).
4. Installs the `colgrep-search` and `code-overview` skills, which are skipped
   without this flag.

The MCP server exposes exactly two tools, both code search over your own
checkout, backed by the `colgrep` CLI: `colgrep_search` (`colgrep search`) and
`colgrep_list_dev_indices` (`colgrep status`). Nothing here runs a background
service or container, there is no network dependency beyond the one-time
`cargo install`, and nothing private is involved. The first search on a
checkout builds its index, which can take a while on a large, never-indexed
repository; later searches reuse it.

If you already ran `install` without `--with-colgrep`, re-run it with the flag
added -- `install` resumes from the first incomplete optional step, the same
way it resumes any partial install.

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

Before your first plan, open Terminal in the **application repository** and run
`git status --short` and `git diff`. Review the adoption changes, including
`.gitignore`, `CLAUDE.md`, and `.claude/settings.json`. Commit only the setup
files you intend to keep, leaving unrelated work out. Start `/jPlan` with a
clean working tree.

To connect Jira, register Atlassian's hosted MCP server and authorize it once.
Run both commands in the same Mac/Linux/Ubuntu terminal environment as Claude Code; the
second command opens your browser for approval:

```bash
claude mcp add --scope user --transport http atlassian https://mcp.atlassian.com/v2/mcp
claude mcp login atlassian
```

Already being signed in to Atlassian in that browser does not authorize the MCP
connection by itself. After approval, start Claude Code and run `/mcp` to
confirm Atlassian is connected. For SSH or another headless session, use
`claude mcp login atlassian --no-browser` and follow the URL and redirect
instructions in Terminal. There is no self-hosted Jira container in jSwarm;
this is host-specific setup and is documented as such.

Claude Code performs Jira reads and writes through that authenticated connection.
If you run the Python tracker helper yourself, `requires_host` means the active
agent still has work to do, not that Jira was updated. See the
[host bridge procedure](jira-host-bridge.md). A failed Jira read stops planning;
failed writes keep your local evidence and report the unsynchronized state.

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
