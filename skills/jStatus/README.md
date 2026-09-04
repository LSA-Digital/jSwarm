# jStatus

> **Generally available**: the canonical, catalog-recognized `jStatus` pattern (as opposed to a ticket-local instance -- see Pattern availability below).

`jStatus` produces one evidence-backed ticket status report in two places: chat and `.jswarm/plans/<TICKET-XXX>/.jstatus.latest.md`. The chat and file render are intentionally identical. The latest file is overwritten on each render so it remains the current report, not an append-only log.

## Use it

```text
/jStatus [TICKET-XXX]
/jStatus [TICKET-XXX] --template <name>@<version>
```

Ticket resolution is deterministic:

1. explicit command argument;
2. active-session ticket signal;
3. ticket key parsed from the current branch name.

If none resolves, ask for the key rather than guessing.

For `TICKET-XXX`, the plan folder is `.jswarm/plans/TICKET-XXX/`, and the skill resolves `.jswarm/plans/TICKET-XXX/.jstatus.template.md`. It renders on an owner request, phase boundary, gate verdict, terminal run result, owner decision, or a significant ticket-specific event declared by the selected template. The orchestrator decides whether an event is significant.

## Pattern availability

| Pattern | Availability | Use |
|---|---|---|
| `standard@1` | Generally available | A compact Product-Engineer report with Technical Terms, Background / Relevant Context, Product Manager View, and Software Engineering Details. |
| `TICKET-XXX-l1-pipeline@1` | ticket-local instance | This pattern's L1 operational-pipeline vocabulary, stage table, direct blocker-to-outcome table, spend/envelope fields, review verdicts, and A/C 1–9 alignment. It is not a common catalog asset. |

A `--template` selector must exactly match either a generally available catalog identity or the identity of the already-materialized ticket-local template, always in `name@version` form. An unversioned or unknown selector is invalid; explain it and show the available choices instead of guessing. When the ticket-local template is absent, the authoring flow offers `standard@1` and materializes it only after explicit confirmation. A missing or malformed selection fails open with an explanation and falls back to `standard@1`; it never blocks ticket progress.

## Customize a ticket

1. Start with `/jStatus TICKET-XXX`.
2. Select or reselect a pattern with `--template`, if needed.
3. Author the ticket-local `.jstatus.template.md` using Markdown, ordered sections, `{{placeholder}}` tokens, and HTML authoring comments.
4. Keep the template's declared name and version explicit.
5. Render again and inspect both the chat report and `.jstatus.latest.md`.

Use `unknown` for an unavailable fact and `unmeasured` for a measurement that was not run. Do not turn a missing record into a zero, pass, complete state, fabricated commit, or invented Jira key.

When a report uses an existing diagram, the template names its authoritative evidence source and the renderer pulls it from there, preserving labels and shape rather than redrawing it from memory. Sample rows are scaffolding rather than limits: blocker and review-verdict tables expand or contract to cover all material evidence.

Generated frontmatter records `generated_at`, `ticket`, `template`, `trigger`, `repository_head`, and orchestrator model `status_owner`. `status_owner` is the runtime's orchestrator model identifier, or `unknown` when unavailable; never infer it or substitute an agent/core identity. A human-readable timestamp follows the frontmatter immediately.
