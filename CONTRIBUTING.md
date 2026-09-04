# Contributing

## Before you open a pull request

Run the test suite and the leak gate from the repository root, with the
project's own virtual environment, never system Python:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m jswarm.leakgate
```

Both must pass. The leak gate fails the build on ticket keys, internal
hostnames, and other markers that should never leave the repository this
project was cut from; a clean commit introduces none of them.

## Rules

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
exact commands you ran. Include your platform; macOS is the only supported
platform in this release, and an issue on another platform is still
welcome, but is not expected to be fixed.

## Security issues

Do not open a public issue for a security vulnerability. See
[SECURITY.md](SECURITY.md).
