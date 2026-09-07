# Portability validation — 2026-09-07

These results apply to the local portability changes based on `cb2d9af`, not to
a published release. Nothing in this report establishes a completed human
lifecycle or Windows clean-machine acceptance.

## Executed

- **macOS:** 309 core tests passed; 13 browser-integration tests skipped.
  All five portal build/boot tests passed separately.
- **Linux:** Debian Bookworm ARM64 container, Python 3.12 and Node 24.14.0.
  296 core tests passed; 26 skipped. All five portal build/boot tests passed.
  The skips were 13 browser-integration tests, 12 real ColGREP tests (binary
  absent), and one private-upstream provenance check.
- **Fresh Linux installation:** copied the source without a virtual environment,
  used an empty home, and ran check, install dry run, install, verify, adopt,
  unadopt, upgrade, verify, and uninstall. The dry run created neither a virtual
  environment nor global state. Uninstall removed the installation lock.
- **Real installed hook:** adopted a temporary application and ran its generated
  command from that application's folder, with no inherited Python search path
  and a framework path containing spaces. The reminder executed and did not
  change project state.
- **Real portal process:** started, answered API requests, served the built UAT
  page, rejected duplicate startup and occupied ports, respected dry-run stop,
  stopped, and restarted. An unrelated process referenced by a stale PID file
  was not terminated. Linux's unreaped-process case was exercised and fixed.
- **Real Claude CLI on Linux:** isolated-home MCP registration/resume and
  uninstall containment tests passed. These did not authenticate with Jira or
  invoke a model-driven lifecycle.
- **Public leak gate:** no unallowlisted findings.
- **Website:** 69 tests, production build, and TypeScript checks passed with
  Node 24.14.1. Platform instructions and claims now distinguish WSL2 from native
  Windows and component tests from end-to-end acceptance.

## Still required before broader release claims

- The new macOS/Ubuntu 24.04 CI matrix must actually run after the changes are
  committed and pushed. Local Debian-container results are not Ubuntu CI results.
- Walk a full work item through Claude Code and the browser on each clean target.
- Exercise Windows-to-WSL browser forwarding, host authentication, and optional
  Jira authorization on a real Windows machine.
- Install optional ColGREP and perform a real indexed search on Linux/WSL.
- Validate enterprise capabilities independently. Its installation tests do not
  prove Mac-specific maintenance jobs or a governed enterprise lifecycle.

Native Windows PowerShell/CMD execution remains outside this minimal port.
Use [platform setup](platforms.md) for the supported shell arrangement.
