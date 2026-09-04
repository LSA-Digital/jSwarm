
# Rationale and reference

## Plan-status seed rationale

Immediately after creating a plan and applying provenance, record its initial `plan_status` through `jswarm/plan_status/cli.py record TICKET-XXX <SEED-STATE> --actor /jPlan --proof-source new-work-plan-created --plan-file .jswarm/plans/TICKET-XXX.plan.<descriptive>.md`. Use `2.planning.detailed` for full plans and `0.planning.lite_init` for Lite.

The creation stamp gives frontmatter a fresh lifecycle timestamp and actor for plan metrics ordering. It does not bind a new ticket to another terminal HUD: creation seed states write no HUD binding. A ticket appears only when its own terminal binds it during implementation or an explicit checkpoint. A non-project key advisory skip is fail-open; the plan still has `status: ACTIVE`.

## Canonical authoring rationale

The jPlan lint gate validates authoring shape, not counts. A fresh ticket may have `0/0` rows, but a declared applicable UAT or NFR dimension needs a canonical starter index or matrix. Fix malformed widths, seedability, or missing applicable dimensions; do not use `|| true` or prose substitutes.

## Optional rule modules

Read the rule-bearing dashboard-render, security-compliance baseline, Feature-child projections, UAT chain, and NFR chain modules only when their declared triggers apply. Keep their authoritative content in their existing owner modules until the later composition slice moves them.
