---
name: "jTest"
description: "Run the project's automated tests (level 1) and an agent-led smoke walk (level 2), before a UAT round is issued."
---

# /jTest: Automated Tests and the Agent Smoke Walk

## Safety contract

- **Read-only against the project.** `/jTest` runs the project's own test command and walks the running app; it does not change application code. Invoked with `--help`, it prints usage only.
- **Level 2 assumes level 1 is green.** Do not walk the app while automated tests are red; fix or report the failure first.

## Usage

```
/jTest                  # run automated tests (level 1) and the agent smoke walk (level 2)
/jTest --help            # usage only
```

## Extension steps

Run `${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.ext jTest`. For each path printed, in order, read the file and carry out its steps here before continuing. If nothing is printed, continue.

## Step 1: Level 1, automated tests

Run the project's own test command for real: `npm test` / `pnpm test`, `pytest`, `go test ./...`, `cargo test`, or whatever this project actually uses. When it is not obvious, check `package.json`'s `scripts.test`, a `Makefile` target, or the project's own CI config rather than guessing.

Report the true result:

- Every failure, by name, with the assertion or output that failed.
- A test that passed only after a retry: report it as flaky, not as a plain pass.
- Skipped or excluded tests: name them; a skip is not a pass.

Do not summarize from memory or from a previous run. If anything fails here, stop and report FAIL; do not continue to Step 2 on a red suite.

## Step 2: Level 2, agent smoke walk

Once level 1 is green, walk the user-visible surface this work item actually changed the way a person would use it, before asking a person to look. Derive the journeys to walk from the plan's acceptance criteria and `git diff <base>...HEAD` for this branch, not from memory of what the ticket was supposed to do.

Reach the running app with whichever of these actually works, in order, and say which you used:

1. A browser automation tool already exposed in this session.
2. Failing that, the project's own end-to-end harness CLI (headless, project-appropriate).
3. Failing that, exercise the same paths at the API/CLI level, and say plainly that visual coverage was **not tested**.

For each journey walked, record what you did and what you saw: PASS, FAIL (with the exact mismatch), or BLOCKED (the app could not be reached, or a step's outcome could not be told apart from a similar-looking failure). Do not mark PASS on an assumption; if you could not tell, it is BLOCKED.

## Step 3: Report

```
Level 1 (automated): PASS / FAIL <n> of <total> tests (list failures, flaky retries, skips)
Level 2 (smoke walk): PASS / FAIL / BLOCKED, one line per journey walked
Instrument used: browser tool / project e2e CLI / API-only (note if visual coverage was not tested)
```

**Next:** once level 1 is green, type `/jUAT` in this project's agent session to issue a round to the local review portal.
