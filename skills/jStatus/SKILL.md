---
name: jStatus
description: Render evidence-backed ticket status reports from a selected, versioned project-local template.
---

# jStatus

> **Generally available** — canonical JSWARM user skill; registered and deployed through the governed catalog lifecycle.

## Purpose

`jStatus` renders a concise, evidence-backed ticket status report to both chat and the ticket's plan folder. It does not invent Jira keys, status facts, measurements, commits, or verdicts.

## Invocation

```text
/jStatus [TICKET-XXX]
/jStatus [TICKET-XXX] --template <name>@<version>
/jStatus [TICKET-XXX] --quick | --lite | --fast
```

`--template` selects or reselects a catalog pattern while authoring the ticket-local template. The selector must be an exact catalog identity in `name@version` form, such as `standard@1`. `--quick`, `--lite`, and `--fast` are exact synonyms for the one-screen product-owner report contract described below; quick is the canonical mode name in prose, filenames, and provenance.

## Resolve the ticket

Resolve the ticket in this order:

1. An explicit `[TICKET-XXX]` argument.
2. An active-session ticket signal.
3. A ticket key parsed from the current branch name.

If no key resolves, ask for one; do not guess.

## Resolve the template

For the resolved `TICKET-XXX`, define the plan folder once as `.jswarm/plans/TICKET-XXX/`.

1. Resolve `.jswarm/plans/TICKET-XXX/.jstatus.template.md`.
2. A `--template` selector must exactly match either a generally available catalog identity or the identity of the already-materialized ticket-local template, always in `name@version` form. An unversioned or unknown selector is invalid: explain it and show the available choices; never guess.
3. If the ticket-local template is absent, resolve the repository fallback `.jswarm/jstatus.template.md` (mirroring quick mode's repository layer; e.g. hai-sim-engine's `hai-sim-full@1`, owner-directed 2026-08-30: lite-core base + intent-aligned detail layers + portal-anchored cycle header with the portal-vs-ground reconciliation rule). Only if that is also absent, offer the generally available `standard@1` pattern.
4. Materialize a selected generally available pattern only after explicit confirmation.
5. If the selected template is missing or malformed, explain the problem, fall back to `standard@1`, and continue. A template problem never blocks ticket progress.

The generally available catalog starts with:

- `standard@1` — four-section Product-Engineer status.

Projects and tickets may carry already-materialized, versioned local patterns. For example, 's `has617-l1-pipeline@1` remains in that ticket's plan folder; local patterns are instances, not generally available catalog assets.

## Quick mode

With any quick synonym, render a screenshot-friendly, one-screen product-owner report. Resolve its template in this order:

1. `.jswarm/plans/TICKET-XXX/.jstatus.quick.template.md` — the ticket-local override.
2. `.jswarm/jstatus.quick.template.md` — the repository fallback. It lives under `.jswarm` because it is project-owned JSWARM reporting policy, not Claude runtime configuration; keeping it outside `.claude` also makes the ticket override and repository default part of one evidence namespace.
3. If neither exists, offer to materialize the repository fallback; do not invent a quick report without a template.

The owner-approved `has652-quick@1` ticket-local pattern is the reference example. Repository fallbacks should generalize that shape under their own versioned identity; local quick patterns are not generally available catalog assets.

Render quick mode to `.jswarm/plans/TICKET-XXX/.jstatus.quick.latest.md` and intentionally overwrite it on every quick render. Apply the same evidence, currency, byte-identical chat/file, and honest-unknown rules as standard mode.

## Render and overwrite

Render the selected template to:

```text
.jswarm/plans/TICKET-XXX/.jstatus.latest.md
```

Intentionally overwrite the latest file on every render. Emit byte-for-byte identical report content in chat and in that file. Preserve authoring comments only where the selected template requires them; do not present comments as status evidence.

**Chat emission requirement:** "in chat" means the report body is reproduced in the assistant's own final message for that turn, where the terminal renders it as markdown. Script or tool stdout — including the render script's own print of the report — displays to the owner as raw text and does NOT satisfy the chat half of the contract. Never end a render turn with commentary pointing at report content that only exists in tool output; frontmatter is omitted from the chat copy, ASCII diagrams stay in fenced blocks, and everything else is byte-identical to the file.

## Render triggers

Render on:

- owner request;
- phase boundary;
- gate verdict;
- terminal run result;
- owner decision; or
- a ticket-specific event declared by the selected template.

The orchestrator judges whether an event is significant enough to trigger a render. Do not create a second, hidden significance policy in the template.

## Generated provenance

Generated frontmatter contains exactly the fields for its selected mode.

For an on-demand standard render:

```yaml
generated_at: <ISO timestamp>
ticket: <resolved ticket key>
template: <name>@<version>
trigger: on-demand
repository_head: <repository HEAD commit>
status_owner: <orchestrator model identifier>
```

For a quick render, use the same fields and add the mode discriminator:

```yaml
generated_at: <ISO timestamp>
ticket: <resolved ticket key>
template: <name>@<version>
mode: quick
trigger: on-demand
repository_head: <repository HEAD commit>
status_owner: <orchestrator model identifier>
```

For an event-triggered render in either mode, record the actual trigger instead:

```yaml
trigger: "event: <actual trigger>"
```

For example, a phase-boundary render uses `trigger: "event: phase boundary"`.

`status_owner` is the orchestrator model identifier available to the runtime. If it is unavailable, write `unknown`; never infer it or substitute an agent/core identity. Immediately after the closing frontmatter delimiter, write a human-readable timestamp line. Use `unknown` when a required evidence value cannot be obtained; use `unmeasured` for a measurement that was not run. Never convert unavailable evidence into zero, pass, complete, or a fabricated commit.

## Template grammar

Templates are Markdown. They use `{{placeholder}}` tokens, ordered sections, and HTML comments that tell the author or renderer what each section must contain. A template declares its explicit name and version outside or above its ordered sections. Unknown placeholders are evidence gaps, not values to infer.

## Authoring rules

- Refresh stale plan, source, test, log, and evidence inputs before rendering; never render known-outdated status.
- Keep the Product Manager view plain-English and outcome-focused.
- Put technical detail after the product view.
- Tie claims to inspected plan, source, test, log, or evidence paths.
- When a report includes an existing diagram, name its authoritative evidence source and pull the diagram from there. Preserve its labels and shape; do not redraw it from memory.
- Treat sample table rows as scaffolding, never as a completeness cap. Add or remove rows so every material evidenced blocker, review, or verdict is represented.
- Report blockers with the condition that clears each blocker.
- Report unknown and unmeasured values honestly.
- Do not claim a verdict until the source evidence records that verdict.

## Ceremony, assurance and security/privacy ledger (owner-directed 2026-08-31)

Standard-mode (FULL) templates MUST carry a dedicated call-out section that separately ledgers ALL overhead work — everything that is ceremony, assurance, or security/privacy — so this spend is never invisible inside progress narrative. Quick/lite mode does NOT render this ledger (owner-directed 2026-08-31): quick stays one-screen; the ledger is a full-report section only. Template grammar:

| Line item | Driver (defect / work item) | Product / DevOps | Importance | Effort | Comments |

Rules:

- Every line item is ALIGNED to the specific defect, ruling, contract, or work item driving it — no free-floating "process" rows.
- **Product vs DevOps:** `Product` = the overhead ships in or with the product (feature security/privacy NFRs, user-data protections, upload identity checks). `DevOps` = process overhead (runbooks, cutover evidence, review ceremony, replay/session-compatibility machinery, restart procedures).
- **Importance banding:** `HIGH` — its absence would plausibly produce owner-visible failures, data exposure, or blocked work; not doing it costs more than doing it. `MEDIUM` — catches real issues but is duplicative of another layer or narrow in scope. `LOW` — conformance-only value in this environment; its absence changes no outcome the owner cares about.
- **Effort banding:** `HIGH` — consuming ≳30% of the period's effort (tokens, wall-clock, or lanes). `MEDIUM` — hours-scale or repeated interventions. `LOW` — minutes-scale.
- **Mandatory flags:** any row scoring Effort HIGH + Importance LOW is a CUT CANDIDATE and must be surfaced to the owner explicitly (see the project lean-assurance ruling where adopted); Effort HIGH + Importance MEDIUM requires a comment justifying continuation. The Comments column always explains the scoring, in plain English.

Few-shot category definitions (illustrative, from field instances):

- **Ceremony** — process or evidence work whose primary output is procedural conformance rather than product behavior or new defect knowledge. E.g. a 12-phase provable-deletion cutover on a disposable pre-production stack (DevOps · Importance LOW · Effort HIGH · "owner cancelled; lean clear-all achieves the same outcome in minutes"); a promotion sign-off harness rendering counts nobody consumes (DevOps · LOW · LOW).
- **Assurance** — work that raises confidence in correctness: tests, reviews, replay floors, triage, mutation-resistance proofs. E.g. within-deployment replay determinism batteries (DevOps · HIGH · MEDIUM · "protects crash recovery — real user-facing failure class"); a four-round adversarial contract review chain (DevOps · MEDIUM · HIGH · "caught 11 real findings, but later rounds re-proved settled scope — split verdict"); a vacuous-test hunt with executed mutation proof (DevOps · HIGH · MEDIUM · "found 3 tests green for the wrong reason").
- **Security/privacy** — authN/Z, data protection, secrets handling, privacy NFRs. E.g. an upload identity check (sha256 == content_hash) with a 409-mismatch discriminator test (Product · HIGH · LOW); credential non-logging rails (Product · HIGH · LOW).

## Generally available lifecycle

The canonical source is the controlled-config user skill in the JSWARM common repository. Catalog changes are prepared and validated with `/jregister`; `/jdeploy` materializes and verifies the registered closure at the live user-skill target. Ticket-local `.jstatus.template.md` and `.jstatus.latest.md` files remain project evidence and are never promoted as catalog assets.

TODO (maintainer): run `/jregister prepare` and then `/jdeploy` for this catalog change; this edit prepares the master only and does not run either lifecycle step.
