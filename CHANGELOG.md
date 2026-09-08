# Changelog

## v1.1.0 candidate (not released)

### Added

- `/jSettings` puts HUD status, previewed enable/disable, and private-feedback
  credential connection inside the agent workflow. Changes require approval;
  replacing an existing status line requires separate consent.
- `/jFeedback` can send an exact reviewed report from Claude after explicit
  approval, using a separately authenticated LSA-only endpoint. Payload hashes,
  local attempt records, durable rate limits, and report deduplication guard sends.
- `/jUpgrade` checks public main, pins the reviewed commit, previews installation,
  and upgrades/verifies after approval. Dirty, custom, and diverged checkouts stop
  without force resets. Existing installations need one manual upgrade to add it.

- `/jFeedback` prepares an owner-reviewed local report for private submission to LSA.
  No public issue creation, automatic transcript upload, or background telemetry.
- Optional `hud enable|status|disable` commands install a bundled status line, preserve
  existing Claude settings, and restore the previous status line on removal.
- HUD quota usage/reset windows use native Claude data or session-bound provider
  snapshots. Missing, expired, and stale data are distinguished; provider collectors
  and model routing are not added to the open core by this reader.

### Improved

- Everyday docs now lead with `/jSettings`, `/jUpgrade`, and `/jFeedback`;
  terminal installer controls remain supported as recovery fallbacks.
- `/jUpgrade` retrieves release notes at the exact offered commit before source
  approval, with a notes diff and a code comparison link. Unavailable notes stop
  the upgrade before source changes; notes are never executed as instructions.
- Versioned notes now include upgrade steps, breaking changes, known limitations,
  and verification. CI requires changelog changes alongside product changes;
  maintainers still review their accuracy. See [Release process](docs/releasing.md).

### Fixed

- `verify` exercises a recorded HUD launcher; uninstall restores it before cleanup.

### Breaking changes

- None intended. `/jUpgrade` follows main, not the stable-release channel, and
  deliberately refuses dirty checkouts, custom branches, and pinned tags.

### Upgrade steps

- Existing users need one [manual upgrade](docs/updating.md) to install `/jUpgrade`.
  Review these notes and the installer dry run, apply, verify, then restart Claude Code.
- HUD setup is optional and separate. Do not re-adopt projects or reinstall Jira.

### Known limitations

- This is unreleased main-channel work, not a published v1.1.0 release.
- Direct feedback activation needs LSA-issued sender credentials and a configured
  durable rate-limit store. Browser submission separately needs production CAPTCHA.
  Neither path has a verified inbox round trip yet. Local drafting is not delivery.
- Direct feedback currently has no separate reply-email field; use the browser
  form if needed. Sender connection is a one-time hidden Terminal prompt, not chat.
- The HUD reader does not collect other providers' quotas. Real restarted-session
  display and external provider snapshots still need acceptance testing.
- Native Windows shells are unsupported. WSL2 manual acceptance remains separate
  from automated Linux tests. Enterprise is not upgraded by the public installer.

### Verification

- Settings tests exercise preview/apply, stale approvals, restoration of an existing
  status line, and a real HUD launcher in temporary homes. Direct-feedback tests
  exercise exact-payload consent, private credentials, receipts, and no retry after
  uncertainty. Website tests and an isolated real Redis exercise authentication,
  concurrency, rate limits, and duplicate claims without sending real email.
- Automated temporary-home upgrade tests exercise real Git checkouts, commit
  pinning, approval boundaries, dirty/diverged rejection, and installer failure.
- Release-note tests cover missing sections, product-change coverage, pinned note
  retrieval, and stopping before source writes when notes cannot be read.
- Passing tests are not a substitute for the remaining manual checks above.

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
