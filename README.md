# jSwarm

Plan, build, prove, and ship one ticket at a time with Claude Code. Jira is
the first tracker adapter, not a requirement: run the same loop with a local
slug and no tracker at all.

```
/jPlan <work-item> -> /jGo -> /jTest -> /jUAT -> /jFix -> /jClose -> /jMerge
```

## Install

```bash
git clone https://github.com/LSA-Digital/jSwarm ~/dev/jswarm && cd ~/dev/jswarm
./install.sh check
./install.sh install
```

Then `./install.sh verify`, adopt one application repository with
`./install.sh adopt <repo-path>`, and read
[Getting started](docs/getting-started.md) for the full walkthrough.

## Docs

- [Getting started](docs/getting-started.md), install, verify, and adopt a project
- [Your first ticket](docs/your-first-ticket.md), the complete reference loop with Jira
- [How the loop works](docs/how-the-loop-works.md), the three operating contexts and what each command does
- [Working without a tracker](docs/without-jira.md), the same loop with no tracker configured
- [Man pages](docs/manpages/), one per command

## Support

macOS is the only supported platform, Claude Code is the only supported
agent host, and Jira is the only tracker adapter shipped in this release.
See [Getting started](docs/getting-started.md#what-this-guide-supports) for
the exact statement of what is and is not supported.

## Enterprise

jSwarm is the open core: a complete way to run one work item through the
loop, on your own machine, with nothing hidden behind it. Multi-model
routing, cost controls, coordinated agent orchestration, and compliance
gates for larger and regulated teams are available as an enterprise add-on
that installs on top of this open core and never forks it. See
[jarviswarm.com](https://jarviswarm.com) for details.

## License

Apache-2.0. See [LICENSE](LICENSE).
