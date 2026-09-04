---
status: ACTIVE
plan_status: "2.planning.detailed"
phase: "2.planning"
ac_complete: "0/0"
template_id: PLAN_TEMPLATE
template_version: 1
# COM-138 — phase: + ac_complete: are DERIVED (like status:) and hook-maintained.
#   phase:       "<N>.<heading>" from plan_status (impl) or the stage label; do NOT hand-edit.
#   ac_complete: "<done>/<total>" counted from the ## Acceptance Criteria section.
# COM-114 — logical components this ticket contributes to + marketable features it advances.
#   components: list of logical-component ids (docs/_JarviSWARM/components/{id}.component.yaml);
#               use [] (explicit, never omit) if the ticket touches no durable component.
#   features:   list of marketable-feature ids (F-MKT/MTH/TOOL/INF-N) the ticket advances; [] allowed.
# Validated WARN-only + fail-open (an unknown id never blocks; never auto-creates a record).
components: []
features: []
# State machine: ACTIVE → READY_FOR_MERGE → DONE (or WONT_DO / DEFERRED)
# Transitions:
#   - /jPlan seeds ACTIVE (this default)
#   - /jGo flips to READY_FOR_MERGE at Plan Completion (Step 5S, A-band auto-flip)
#   - /jClose flips to DONE post-merge (Step 3.10, after /jMerge returns green)
# Canonical: ${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/state-machine.md
#
# plan_status (COM-84): canonical lifecycle; status: above is DERIVED from it.
#   /jPlan --lite -> 0.planning.lite_init ; /jPlan -> 2.planning.detailed
#   /jGo -> 3.implementation.phase_N.<descr> ; Plan Completion -> 5.closed.ready_for_merge
#   /jClose -> 4.closed.all_ac_met then 6.closed.merged ; terminal.wont_do / terminal.deferred
# Canonical: docs/agent-system/plan-status-state-machine.md and docs/plans/COM-84.specs.md
---

# TICKET-XXX: Feature Title

> **⚡ ORCHESTRATOR EFFORT:** run this orchestrator on **[claude-fable-5 | claude-opus-4-8] at [HIGH | XHIGH]**. [List phase escalations, or "No escalation."] At each escalation boundary the orchestrator MUST remind the owner to switch the session effort, and back afterwards. Source of truth: [parent feature plan] §Orchestrator model routing. — delete this block only if the project has no orchestrator-routing policy

**Last Updated:** YYYY-MM-DD

**Jira Ticket:** [TICKET-XXX](https://lsadigital.atlassian.net/browse/TICKET-XXX)
**Parent:** [PARENT-XXX (Epic Title) — delete if none]
**Technical Design Spec:** [TICKET-XXX.specs.md](TICKET-XXX.specs.md) — delete for Quick plans that do not use a spec
**Recommended agent team:** Pattern <1|2> · review:<critic|critic-xhigh> · arch:<none|architect|architect-master> · escalation-trigger:<verbatim trigger or "none">
**Orchestrator model & effort:** [FABL claude-fable-5 | OPUS claude-opus-4-8] · [HIGH | XHIGH throughout | HIGH with phase escalations — list them] — routes the ORCHESTRATOR session only; named j-cores stay route-pinned
**Ceremony tier:** Low | Medium | High | N/A — legacy/no-selection
**Effective pattern:** [compiled H/M/L preset key such as `l1.story.medium-standard`, or `N/A — legacy/no-selection`]
**Testing strategy:** unit [required/upgrade/N/A]; integration [required/upgrade/N/A]; live-show UAT [yes only if UI/E2E impact / no]; regression E2E [per-ticket/deferred/N/A]; smoke [impact yes/no]
**UAT state policy:** fresh-created | curated-existing | diagnostic-broken | N/A — use N/A when `Automated UAT: no`; default to fresh-created for live-show UAT; existing data requires named IDs and preflight
**Test data strategy:** managed cluster required for regression | managed cluster recommended for scripted UAT | exploratory-ad-hoc allowed for live UAT | N/A — [cluster IDs or setup summary]
**Status:** Tracked via frontmatter `status:` field (ACTIVE → READY_FOR_MERGE → DONE) — see [state-machine.md](../../../docs/merge/state-machine.md)

---

## Overview

[2-3 sentences: What this accomplishes and why it matters]

---

## Scope

### In Scope

- [What IS included]

### Out of Scope

- [What is NOT included]

---

## Acceptance Criteria

> Synced with Jira ticket. Update both.
> Author A/C as few, high-level summaries of UAT/NFR clusters (1 A/C : N, not 1:1), with perspective flexibility; see `docs/agent-system/ac-uat-nfr-traceability.design.md`.

- [ ] **A/C 1:** [Description]
- [ ] **A/C 2:** [Description]
