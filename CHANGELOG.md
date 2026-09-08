# Changelog

## v1.0.0 — 2026-09-08

First tagged public release. This freezes the core implementation through
commit `68637ac`; the release commit changes documentation only. The user
reported a successful clean-Mac setup and workflow. Automated CI covers macOS
and Linux; that is not evidence of every possible project, provider, or WSL2
installation working end to end. See the GitHub Release for scope and limitations.

- Jira lifecycle operations use the active Claude Code session's authenticated
  Atlassian tools, with explicit pending requests and validated observed results.
- ColGREP can be added after a core-only install and interrupted setup can resume.
- ColGREP uses a directory-independent module launch. Install and upgrade repair
  this clone's legacy MCP registration, and verify performs a real handshake.
  Explicitly requested search failures return nonzero; failed uninstall keeps
  the registration record for retry instead of claiming successful cleanup.
- Local work-item slugs remain usable, including in Jira-configured projects.
- Adoption excludes local runtime state from Git; review and commit the setup
  changes before starting the first plan.

First public cut of the core loop: `/jPlan`, `/jGo`, `/jTest`, `/jUAT`,
`/jFix`, `/jClose`, and `/jMerge`, plus `/jSetup` and `/jPrecompact`. The
installer supports `check`, `install`, `verify`, `adopt`, `unadopt`,
`upgrade`, `uninstall`, and `portal`, each write-taking subcommand with a
`--dry-run`. Jira is the first tracker adapter and is optional; Claude Code
is the supported agent host. The shell workflow supports macOS, Linux, and
Windows through WSL2/Ubuntu, not native Windows shells. Command
shims for the previous names (`/test`, `/fix`, `/uat`, `/jsetup`,
`/new-work`, `/implement`, `/close-ticket`, `/merge`) ship for one release.
