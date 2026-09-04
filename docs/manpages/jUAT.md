---
command: /jUAT
title: Issue a round to the portal
summary: Prepare and author UAT scenarios, and issue a numbered round to the local review portal.
stage: verify
---
# /jUAT

## Diagram

```mermaid
flowchart LR
    Plan["/jPlan"] --> Go["/jGo"] --> Test["/jTest"] --> UAT(["/jUAT"]):::current --> Fix["/jFix"]
    Fix --> Test
    UAT --> Close["/jClose"] --> Merge["/jMerge"]
    classDef current fill:#f9a825,stroke:#333,stroke-width:2px
```

## What it is

`/jUAT` issues a round to the local review portal at
`http://localhost:8766/uat/`. When the work item has no UAT scenarios yet,
it authors one first. It prepares the journeys a human walks; **it does not
approve them for you**, and it does not close anything on its own.

## When to use it

Run `/jUAT` after `/jTest` passes, to issue a round for someone to walk in
the portal. Run `/jUAT author` on its own to author or update a scenario
before there is anything to issue.

## Options

```
/jUAT                  # issue a round for the current work item
/jUAT <work-item>       # explicit tracker key or slug
/jUAT author            # author or update a scenario; do not issue a round
```

## What it writes

- The round file, its feedback shell, and the portal registration for the
  current work item
- Canonical UAT scenario JSON and its rendered Markdown, once you approve a
  preview from the authoring step
- Findings recorded back to the repository once you walk a round in the
  portal

## See also

- [How the loop works](../how-the-loop-works.md)
- [jTest](jTest.md)
- [jFix](jFix.md)
