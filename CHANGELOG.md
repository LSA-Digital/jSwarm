# Changelog

## v1.1.0 candidate (not released)

- `/jFeedback` prepares an owner-reviewed local report for private submission to LSA.
  No public issue creation, automatic transcript upload, or background telemetry.
- Optional `hud enable|status|disable` commands install a bundled status line, preserve
  existing Claude settings, and restore the previous status line on removal.
- HUD quota usage/reset windows use native Claude data or session-bound provider
  snapshots. Missing, expired, and stale data are distinguished; provider collectors
  and model routing are not added to the open core by this reader.
- `verify` exercises a recorded HUD launcher; uninstall restores it before cleanup.

## v1.0.0 (unreleased)

The first release is being prepared as v1.0.0. Publication is pending the
clean-machine acceptance run and release approval; a green CI run is not
evidence that the full live lifecycle has been accepted.

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
is the first agent host; macOS is the first supported platform. Command
shims for the previous names (`/test`, `/fix`, `/uat`, `/jsetup`,
`/new-work`, `/implement`, `/close-ticket`, `/merge`) ship for one release.
