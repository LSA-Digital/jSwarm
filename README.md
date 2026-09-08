# jSwarm

Plan, build, prove, and ship one ticket at a time with Claude Code. Jira is
the first tracker adapter, not a requirement: run the same loop with a local
slug and no tracker at all.

```
/jPlan <work-item> -> /jGo -> /jTest -> /jUAT -> /jFix -> /jClose -> /jMerge
```

## Install

The first release is **[v1.0.0](https://github.com/LSA-Digital/jSwarm/releases/tag/v1.0.0)**.
The commands below install that fixed release. `main` is the development channel
and may include newer, not-yet-released features.

```bash
git clone --branch v1.0.0 https://github.com/LSA-Digital/jSwarm ~/dev/jswarm && cd ~/dev/jswarm
./install.sh check
./install.sh install
```

Then `./install.sh verify`, adopt one application repository with
`./install.sh adopt <repo-path>`, and read
[Getting started](docs/getting-started.md) for the full walkthrough.

Add `--with-colgrep` to `install` for optional semantic code search: it installs
the [`colgrep`](https://crates.io/crates/colgrep) CLI (`cargo install colgrep`,
needs a Rust toolchain), registers a small bundled MCP server that wraps it, and
enables the `colgrep-search` and `code-overview` skills. No Docker, nothing
private -- see [Getting started](docs/getting-started.md#colgrep-optional-code-search).

## Docs

- [Getting started](docs/getting-started.md), install, verify, and adopt a project
- [Update jSwarm](docs/updating.md), get the latest source, upgrade installed skills, and restart Claude
- [Your first ticket](docs/your-first-ticket.md), the complete reference loop with Jira
- [How the loop works](docs/how-the-loop-works.md), the three operating contexts and what each command does
- [Working without a tracker](docs/without-jira.md), the same loop with no tracker configured
- [Man pages](docs/manpages/), one per command

## Support

The shell workflow targets macOS, Linux, and Windows through WSL2/Ubuntu.
Native Windows shells are not supported. See [Platform setup](docs/platforms.md)
for prerequisites and the distinction between automated tests and clean-machine acceptance.
Claude Code is the only supported agent host, and Jira is the only tracker adapter shipped in this release.
See [Getting started](docs/getting-started.md#what-this-guide-supports) for
the exact statement of what is and is not supported.

## v1.1 preview: feedback and HUD

The v1.1 candidate adds `/jFeedback` for private, reviewed reports to LSA and an
optional local HUD with provider-quota usage and reset times. See
[Feedback and HUD](docs/feedback-and-hud.md) for setup and data-source limits.
This is not a v1.1 release announcement.

## Enterprise

jSwarm is the open core: a complete way to run one work item through the
loop, on your own machine, with nothing hidden behind it. Multi-model
routing, cost controls, coordinated agent orchestration, and compliance
gates for larger and regulated teams are the focus of the Enterprise offering.
Contact us to confirm capabilities, prerequisites, and rollout scope. Enterprise
is additive to the open core, never a fork. See
[jarviswarm.com](https://jarviswarm.com) for details.

## License

Apache-2.0. See [LICENSE](LICENSE).
