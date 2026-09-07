# Platform setup

Use the same installer, skills, and hooks on macOS, Linux, or inside Ubuntu on
Windows through WSL2. Windows PowerShell/CMD is not a supported execution
environment for jSwarm. This is a shell-based workflow, not a native Windows app.

## Required tools

Install Python 3.12 or newer with `venv`, Git, GitHub CLI, and Claude Code.
Install Node 24 and npm for the portal. Install Rust only for optional ColGREP.
The installer checks the tools themselves. It does not require Homebrew or
Apple's Xcode command-line tools as separate prerequisites.

Package managers or source builds may need additional build tools. In particular,
`cargo install colgrep` requires your platform's compiler/linker; on macOS this
can require Xcode command-line tools, and on Ubuntu `build-essential`.

## macOS

Existing tool installations are fine. If you use Homebrew, it can install Python,
Git, GitHub CLI, and Node. It is not required by jSwarm. Continue with
[Getting started](getting-started.md).

## Ubuntu 24.04

Run in an Ubuntu terminal:

```bash
sudo apt update
sudo apt install python3 python3-venv git gh
```

Install [Node 24](https://nodejs.org/en/download) with npm and
[Claude Code](https://code.claude.com/docs/en/setup). Authenticate `gh` and `claude`
in this same environment. Ubuntu's default Node package may be older than 24;
check `node --version` before starting the portal.

Other Linux distributions need equivalent packages; the automated CI target is
Ubuntu 24.04. Python must be at least 3.12, even if your distribution ships an older default.

## Windows with WSL2

In an administrator PowerShell window, install Ubuntu:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart if requested, then open **Ubuntu**. Follow the Ubuntu steps above there.
See [Microsoft's WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install).

Keep jSwarm and your application repository under the Ubuntu home directory,
for example `~/dev/jswarm` and `~/dev/my-app`. Run installation, Claude Code,
GitHub/Jira authentication, and all lifecycle commands **inside Ubuntu**, not
across a mixture of Windows and Linux tool installations. A browser login on
Windows does not authenticate the Claude Code session inside Ubuntu.

Open `http://localhost:8766/uat/` in your Windows browser after starting the
portal inside Ubuntu. If local forwarding is unavailable in your WSL setup,
resolve that using [Microsoft's WSL networking guide](https://learn.microsoft.com/en-us/windows/wsl/networking);
do not expose the portal publicly as a workaround. Jira remains optional.

## Starting and stopping the portal

From the jSwarm clone, in your Mac/Linux/Ubuntu terminal:

```bash
./install.sh portal --background
./install.sh portal --stop
```

This starts a detached user process, **not** an automatically restarting boot
service. No launchd/systemd registration or administrator permissions are needed.
Start it again after a reboot or after WSL shuts down. Use `./install.sh portal`
to run in the foreground; Ctrl-C stops it.

Startup waits for the API to answer, reports the actual configured port, and
keeps the log at `~/.jswarm/decision-review/server.log`. If another program owns
the port, jSwarm reports an error; it does not kill that program. Stop and
uninstall verify process identity before termination. `--dry-run` neither
starts/stops processes nor writes files, including with `portal --stop`.

After updating an existing checkout, rerun `./install.sh install` to refresh
Python dependencies before starting the portal. Rerun `./install.sh adopt <repo-path>`
for existing applications to refresh the managed hook (include
the existing `--jira-key KEY` if that application uses Jira). Review and commit
the intended settings change. The hook is a reminder only; it does not execute
`/jPrecompact` or claim to have saved a checkpoint.

## Validation and release acceptance

The CI matrix runs the core tests and real portal build/boot tests on macOS and
Ubuntu 24.04. Tests cover installation in an isolated home, adoption, hooks,
recovery, and process startup/shutdown. CI configuration alone is not a passing
run, and container tests do not prove Windows-to-WSL browser/auth integration.

Before claiming clean-machine acceptance, run the reference walkthrough on
each target: install, adopt an application, open Claude Code, run one full work
item through the lifecycle, review it in the browser, then stop and uninstall.
Optional ColGREP needs a real index/search test on each target too.

Enterprise's installer can use the same Linux environment, but that does not
validate every enterprise feature. Its Mac-specific maintenance skills require
separate porting/acceptance; see the Enterprise documentation before enabling them.
