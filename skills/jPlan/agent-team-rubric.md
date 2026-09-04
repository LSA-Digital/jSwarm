
# Agent-Team Rubric: review-tier and architecture-tier escalation

**Last Updated:** 2026-08-30 (fix-cycle architect-effort rule added; prior 2026-07-27)

## Fix-cycle architect effort (owner rubric update 2026-08-30)

**Owner rule, verbatim: "any defect that touches pipeline build logic is automatic xhigh call."**

Scope: this is an EFFORT-SCORING rule for the jArchitect call the fix methodology ALREADY intends (the `/jFix` cycle is architect-first by design — this rule adds no new step and mandates no extra architect launch). When that existing intended jArchitect call concerns a defect whose mechanism or fix touches pipeline build logic (build rails, dispatch/harvest/park machinery, revision persistence, Temporal build workflows and replay semantics), score it **xhigh automatically** — no per-case rubric scoring, no owner prompt. Execute via the governed jAgentLaunch effort override (never raw effort params on a named core) and verify `servedEffort=xhigh` on the [OUTCOME] line. Rulings already delivered and accepted at lower effort stand (owner 2026-08-30: existing advice usable, don't relaunch).

Skipping the methodology's intended architect call entirely remains a process error under the existing fix methodology — not something this rule adds or governs. Non-pipeline-build defects continue to score through the ordinary tiers below.

This is the **single source of truth** for deciding **whether independent review activates at all** (§ Review activation), for choosing the review tier (`critic` vs `critic-xhigh`) once it does, and for the architecture tier (`none` / `architect` / `architect-master`) on a ticket. It is referenced by `/jPlan` (Q6), `/jGo` (plan-header parse), and `/jFix` (when a ticket plan exists). Do not duplicate this rubric into those commands — they point here.

**Why this exists:** the legacy Q6 guidance ("use critic-xhigh for complex multi-file/multi-component/architecture-aligned work") matched almost every ticket, so agents over-escalated to `critic-xhigh`, and `architect-master` — the most expensive agent in the fleet — was ungoverned and self-escalated ad hoc. This rubric makes the **lean tier the default** and requires a **named, falsifiable trigger** (recorded in the plan) to escalate. The discipline mirrors `/jMerge`'s lean-default + CHK-AM gate.

---

## The inherited plan-header line

`/jPlan` writes exactly one line into the plan header; `/jGo` and `/jFix` read it verbatim and MUST NOT escalate beyond it without surfacing a new trigger to the user:

```
**Recommended agent team:** Pattern <1|2> · review:<critic|critic-xhigh> · arch:<none|architect|architect-master> · escalation-trigger:<verbatim trigger or "none">
```

- `Pattern 1` = Full TDD (jTestEngineer + jCoder in craftsmanship mode + jUIDesigner + review). `Pattern 2` = Orchestrator + review.
- `review:` and `arch:` are the **tiers**; `escalation-trigger:` is the **verbatim reason** any non-default tier was chosen (or `none`).
- An agent that runs at a tier above the line without a matching trigger is **visibly wrong** — the trigger is auditable.

**Dynamic topology/staffing selection:** the agent-team catalog ([`docs/_JarviSWARM/agent-team-index.v1.json`](../../docs/_JarviSWARM/agent-team-index.v1.json) plus `agent-team-fallback.v1.json`) owns dynamic selection through the `agent-team-advisor` skill. Fail open: if the catalog is missing or unparseable, use this rubric's inline guidance; never block. This rubric remains the SSoT only for the frozen header grammar and review/architecture tier enums.

---

## Review activation: decide this before you pick a tier

**Independent review is not automatic.** The orchestrator reviews the diff itself by default. An independent `jCritic` lane is dispatched **only** when at least one of these holds:

1. **Highly complex** — the change is genuinely intricate, not merely large. Line count and file count are not this trigger.
2. **Very broad impact** — a shared contract surface, an external write, an irreversible change, or a wide blast radius. Live-global commands, hooks, and shared policy files are always in this class.
3. **Errors would not be observable through runtime telemetry** — a defect here would not surface in tests, logs, exit codes, or UAT. A repair whose previous "green" turned out to be false is in this class by construction.
4. **A ticket acceptance criterion requires an independent review.** This binds regardless of risk, and it is the only non-risk activation.

### two-stage composition

A ticketed qualifying broad `/jFix` may make both the pre-implementation contract
challenge and the post-implementation code review acceptance criteria. They
compose through existing condition 4; this does not add condition 5. The
pre-implementation stage returns exactly
`APPROVED_FOR_IMPLEMENTATION | REVISE_CONTRACT | BLOCKED_BY_EVIDENCE`; the
post-implementation stage reviews the actual diff and proof. Each activated
stage records reviewer identity, evidence identity, verdict, and stage. The
pre-reviewer may not approve its own later post-implementation review when
review is activated.

This is composition, not a standing trigger or reviewer fleet. No standing
reviewer fleet is created. Ad-hoc local fixes remain cheap, and ticketed review
still activates only when existing condition 4 applies. Phase boundaries and
code existence remain non-triggers.

**A phase boundary is not a trigger. Neither is "code was written."** Cadence — a phase exit, a checkpoint, a commit, the end of a slice — never activates review on its own. Scheduling a reviewer by cadence is what produced fleets of reviews that found nothing; if none of the four conditions above holds, the orchestrator's own review is the gate.

The orchestrator remains a **different lane from the author**, so the author-not-approve separation holds whether or not `jCritic` activates. What changes is who reviews, not whether anyone does.

**How this reaches the plan header.** The header grammar is unchanged and there is no `review:none` token: `review:` names the **tier to use if review activates**, not an instruction to review. The deterministic advisor (`jswarm/patterns/team_advisor.py`) reports activation separately as `review_activation` and emits `effective_review_tier: "none"` when nothing activates; a consumer that reads the header as a standing order to dispatch a reviewer is wrong.

---

## Review tier: the intensity, once review has activated

**Default review tier: `critic`.** Escalate to `critic-xhigh` only when **≥1** of the following triggers holds. Copy the matching trigger text verbatim into `escalation-trigger:`.

1. **Cross-service / public-contract / API-schema change** — the diff alters a published interface, API schema, or wire contract other code depends on.
2. **Security- or compliance-critical surface** — auth, secrets, access control, PII/regulated-data handling, or a documented compliance control.
3. **Irreversible or destructive data migration** — schema migration, data backfill, or any operation that is hard to roll back.
4. **(RC-1) ≥3 service boundaries or DDD bounded contexts** touched in one coherent change — i.e. separate microservices, separate API schemas, or separate database schemas. **Files within the same service/repository module do NOT count as separate contexts** (3 folders in one monorepo ≠ 3 bounded contexts). The threshold of **3 is tunable** after 2–3 months of escalation-log data.
5. **Live-global lifecycle-command / hook / shared-policy surface** — edits to `~/.claude/commands/*.md`, `~/.claude/hooks/*`, or shared `AGENTS-*`/policy docs that affect every project.
6. **(RC-2) Contested design with an evidence trail** — a design where **two or more viable approaches have been explicitly debated** in the ticket, spec, or a linked ADR, and **no consensus approach has been selected**. An agent merely *thinking* the work is complicated does NOT fire this trigger; absent an ADR or a discussion trail, it does not apply.

If none hold → `review:critic` · `escalation-trigger:none`.

---

## Architecture tier

**Default architecture tier: `none`.** Most tickets record `Architecture review: none` and use **no** architect agent.

**Architecture tier — Step up to `architect`** (not `-master`) only when ≥1 holds:
- A **net-new subsystem or service boundary** is introduced.
- The ticket is an **architecture-runway / enabler** ticket (its value is making *future* delivery faster/safer, not shipping a feature).
- A **multi-service contract redesign** is in scope.

**`architect-master` is NEVER automatic.** It is the fleet's most expensive agent (an xhigh-effort architecture lane) and is reserved for architecture-runway *strategy spanning multiple downstream features*. It requires a **CHK-AM-equivalent gate** (mirroring `/jMerge`):

> **CHK-AM gate:** dispatch `architect-master` only when BOTH (a) eligibility holds — runway/enabler strategy spanning multiple downstream features/epics — AND (b) the user has explicitly approved the `architect-master` dispatch, recorded in the plan. Absent either, the step-up tier is `architect`.

`architect-master` is non-substitutable: if the user asks for it, only it will do (retry it on failure, never swap another agent in). But the orchestrator does not *reach* for it on its own.

---

## Consumer contract (how `/jGo` and `/jFix` read this)

**Replace-immediately with a dual-format parser during transition (TQ3).** `/jPlan` writes only the new `Recommended agent team` line going forward. Consumers accept BOTH formats while in-flight plans drain:

1. If the plan header has **`Recommended agent team`** → parse `Pattern` + `review` + `arch` + `escalation-trigger`.
2. Else if it has the legacy **`Execution team pattern`** line → parse `Pattern` + critic tier; default `arch:none`, `escalation-trigger:none`.
3. Else (no header) → ask once using this rubric and append the new line.

Remove the legacy-format branch after one release cycle / once all in-flight plans close. **`/jFix`** consumes the same line when operating on a ticket that has a plan, and applies the same escalation constraints; for ad-hoc `/jFix` with no plan, it uses its own jDebugger→jOracle→jCritic discipline and escalates to `architect` only under the architecture-tier rules above.

**Parsing convention (so the line never silently misparses).** Fields are separated by ` · ` (U+00B7 middle dot with surrounding spaces); parse with the regex `\s+·\s+`. The first field is literal `Pattern <1|2>` (no colon); the remaining `review`, `arch`, and `escalation-trigger` fields are order-free `key:value` pairs. The `escalation-trigger:` value is free-form to end of line, **double-quoted** if it contains the ` · ` separator. A consumer that cannot parse the line MUST fail loud (ask the user) rather than guess a tier — never silently default to `critic-xhigh`/`architect`.

---

## Calibration: the escalation log (RC-4)

Every escalation above the default appends an entry to [`agent-escalation-log.md`](agent-escalation-log.md). Schema:

| Field | Meaning |
|---|---|
| `date` | ISO date/time of the escalation |
| `ticket` | ticket key |
| `tier` | `critic-xhigh` \| `architect` \| `architect-master` |
| `trigger` | verbatim trigger text from the plan header |
| `outcome / warranted` | what the escalation found — **was it warranted?** (`yes` / `no` / `partial` + one line) |

The `outcome / warranted` field is load-bearing: it lets a quarterly calibration measure escalation *appropriateness*, not just frequency. Over-escalation should trend down; if a trigger fires often but is rarely `warranted: yes`, tighten or retire it.

---

## Quick reference

| Situation | review tier | architecture tier |
|---|---|---|
| Routine single-context feature/bugfix, no activation trigger | **no independent review** — orchestrator reviews | none |
| Any of the four activation conditions holds, nothing escalates | critic | none |
| Touches a published API schema other services consume | critic-xhigh | none |
| New service boundary / runway enabler | critic-xhigh | architect |
| Runway strategy across many downstream features + user-approved | critic-xhigh | architect-master (gated) |
| Live-global command/hook/policy edit (e.g. this rubric itself) | critic-xhigh | none |
