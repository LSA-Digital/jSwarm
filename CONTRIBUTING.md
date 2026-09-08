# Contributing

## Before you open a pull request

Run the test suite and the leak gate from the repository root, with the
project's own virtual environment, never system Python:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m jswarm.leakgate
.venv/bin/python -m jswarm.release_notes
```

All must pass. The leak gate fails the build on ticket keys, internal
hostnames, and other markers that should never leave the repository this
project was cut from; a clean commit introduces none of them.

## Rules

- **Release notes with product changes.** Update the current candidate in
  [CHANGELOG.md](CHANGELOG.md), including user actions and limitations. See the
  [release process](docs/releasing.md) for required sections and publication checks.
- **No ticket keys.** Do not reference an issue key from the private tracker
  this project was cut from in code, commits, or docs. Use a public example
  like `PS-14`, or a local slug like `add-csv-export`.
- **No em dashes.** Use commas, colons, or full stops in prose and comments.
- **One skill per pull request.** Keep a change scoped to one command or one
  clearly related piece of behavior, so a reviewer can reason about the
  whole diff.
- **Tests before implementation.** Write a failing test for the behavior you
  are adding or fixing, then make it pass with the smallest change that
  does so.

## Reporting a bug

Open an issue describing what you expected, what happened instead, and the
exact commands you ran. Include your platform and installed commit. The shell
workflow supports macOS, Linux, and Windows through WSL2/Ubuntu, not native
Windows shells. State whether you used a tagged release or main.

## Security issues

Do not open a public issue for a security vulnerability. See
[SECURITY.md](SECURITY.md).
