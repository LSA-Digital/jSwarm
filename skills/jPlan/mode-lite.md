
# Lite mode

## Lite mode

**Triggers:** `/jPlan --lite`, `/jPlan lite`, or clear user intent ("briefing only", "context dump", "no phases yet", "not ready to plan implementation").

**Forbidden in Lite (non-exhaustive):** Technical design spec (`*.specs.md`); Step 3 codebase/test research (no required ColGREP or `TEST_CATALOG` trawling); Oracle; implementation phases; task/checkbox work breakdown; A/C-to-test traceability matrices; testing strategy / execution-team / UAT / E2E / test-data header blocks; `*.uat-scenarios.md` / `*.uat-test.md`; Feature `integr-fixes` / `pe2e-fixes`; any prose prescribing *how* to build beyond restating goals.

**Required in Lite:** Same as always: work item identity (tracker key or slug), session rename intent (Step 2B), and a master plan file at `.jswarm/plans/ID.plan.{short-description}.md` using the Lite plan shape below.

**Step 1 (Lite): one message, minimal questions:**

1. Problem or opportunity (what hurts, what could be better; user's words)
2. Concrete examples they care about (screens, flows, messages, data cases); capture verbatim
3. New ticket or existing? `[new / TICKET-XXX]`
4. If new: issue type? `[Task / Story / Bug / Subtask / Feature]`
5. Scope and acceptance criteria (can be draft bullets)
6. Project/repo this work targets

**Do not ask** Quick/Standard/Deep, execution team pattern, Automated UAT, E2E policy, or test-data strategy in Lite. Optionally add `**Deeper planning:** deferred; run full /jPlan when ready` to plan header.

**Lite plan file shape:**

```markdown
# TICKET-XXX: [Short title]

> **⚡ ORCHESTRATOR EFFORT:** run this orchestrator on **[claude-fable-5 | claude-opus-4-8] at [HIGH | XHIGH]**. [List phase escalations, or "No escalation."] At each escalation boundary the orchestrator MUST remind the owner to switch the session effort, and back afterwards. Source of truth: [parent feature plan] §Orchestrator model routing. (Delete this block only if the project has no orchestrator-routing policy.)

**Last Updated:** YYYY-MM-DD
**Work item:** ID (tracker: [link] | local slug, no tracker)
**Planning mode:** Lite (briefing only, no implementation plan in this file)
**Orchestrator model & effort:** [FABL claude-fable-5 | OPUS claude-opus-4-8] · [HIGH | XHIGH throughout | HIGH with phase escalations, list them] (routes the ORCHESTRATOR session only; named j-cores stay route-pinned)

---
status: ACTIVE
---

## Problem / opportunity
[Narrative + user examples]

## Context
[What we know: systems, constraints, links; still not a solution write-up]

## Scope
### In scope
- ...
### Out of scope
- ...

## Acceptance criteria
- [ ] ...

## Not in this briefing (intentionally deferred)
Full technical design, phases, tasks, tests/UAT plan, and `/jGo` scaffolding. Create later via `/jPlan` without Lite.

## Suggested next step
[e.g. Re-run `/jPlan` Standard/Deep after discovery]
```

After Step 2 (work item identity + rename intent), jump to Step 5 Lite (this shape only), then Step 6 (Lite tracker-comment variant below).

## Lite Step 2: Work item identity and session rename intent

Resolve identity, check the hard stop, write local state, and (when a tracker is configured) resolve or create the tracked issue exactly as in operations.md Step 2A.

**Record the ticket key.** For session rename, use `TICKET-{NUMBER}-{DESCRIPTION}` (UPPERCASE, hyphens, ~40 chars max). In Claude Code, write the title as one line to `.jswarm/state/pending-session-rename`; hooks / user submit apply `sessionTitle`. Do NOT claim you ran `/rename`. In OpenCode, set `OPENCODE_SERVER_URL`, then `PATCH /session/:id` with `{"title": "..."}`. A user may run `/rename TICKET-XXX-DESCRIPTION` as fallback.

> **Feature-orchestrator carve-out: SKIP rename.** When you are the Feature orchestrator running `/jPlan` serially for child Stories under a Feature you own, do not write `.jswarm/state/pending-session-rename` and mark the Step 6 Session row `n/a (Feature orchestrator)`. This applies in Lite too.

## Lite Step 2a: Bug evidence pack (DEFECT tickets, MANDATORY, owner-ruled 2026-08-05)

When the ticket is a **defect** (Bug issue type, or a Story chartering a bug/defect follow-up, e.g. filed from a triage, review finding, or UAT report), the filing is NOT complete until a **reproduction-evidence pack** exists at `<plans-root>/TICKET-XXX/design-inputs/` per [`${JSWARM_HOME:-$HOME/dev/jswarm}/docs/templates/BUG_EVIDENCE_PACK_TEMPLATE.md`](../../docs/templates/BUG_EVIDENCE_PACK_TEMPLATE.md): frozen COPIES (or source-headed excerpts) of the diagnosis/triage material, symptom artifacts (screenshots, log slices), and an indexed `README.md` carrying **reproduction anchors** (sessions/commands/ids, trigger condition, code anchors with verbatim failure signatures, environment state) and **boundary notes** mirroring the plan's non-goals. Pointers to origin-ticket evidence are allowed IN ADDITION, never INSTEAD: origin folders evaporate at close (worktree teardown, log rotation, context compaction). Commit plan + pack together. Non-defect lite tickets (features, enablers, docs) skip this step with no annotation.

## Lite Step 6: Tracker comment and summary

Comment through the tracker boundary (skipped cleanly when no tracker is configured):

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.tracker.cli comment <ID> --repo "$PROJECT_ROOT" --text "$(cat <<'EOF'
Lite briefing plan: .jswarm/plans/ID.plan.<descriptive>.md
Planning mode: Lite (briefing only)
Depth / execution team / UAT: N/A (deferred until full /jPlan)
EOF
)"
```

Print the result's `message` once; `skipped` and a failed `ok` are both non-blocking; local state is already written.

```markdown
## Work Initialized (Lite)

| Output | Status |
|---|---|
| **Work item** | ID (tracker: [link] / slug: local only) |
| **Session** | Renamed to ID-DESCRIPTION |
| **Briefing plan** | .jswarm/plans/ID.plan.<descriptive>.md (status: ACTIVE) |

**Next:** run `/jPlan` again without Lite when ready for Quick/Standard/Deep planning. Do NOT run `/jGo` until then.
```
