# Changelog

## v0.1.0

First public cut of the core loop: `/jPlan`, `/jGo`, `/jTest`, `/jUAT`,
`/jFix`, `/jClose`, and `/jMerge`, plus `/jSetup` and `/jPrecompact`. The
installer supports `check`, `install`, `verify`, `adopt`, `unadopt`,
`upgrade`, `uninstall`, and `portal`, each write-taking subcommand with a
`--dry-run`. Jira is the first tracker adapter and is optional; Claude Code
is the first agent host; macOS is the first supported platform. Command
shims for the previous names (`/test`, `/fix`, `/uat`, `/jsetup`,
`/new-work`, `/implement`, `/close-ticket`, `/merge`) ship for one release.
