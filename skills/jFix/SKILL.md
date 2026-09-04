---
name: jFix
description: Architect-first fix cycle — typed acceptance intake, user-authorized outcome boundary, jArchitect-issued plain-English contract, and the retained proof floor through causal RED, connected proof, and selected canary before expensive UAT
user-invocable: true
triggers:
  - fix
  - debug and fix
  - diagnose and fix
  - root cause fix
argument-hint: "[--narrow|--broad] [--diagnose-only] <description of what's broken | error message | test name>"
level: 3
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/fix/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy. -->


# /jFix — Architect-First Fix Cycle (COM-386)

## Help & Quick Reference

**If the user passes `--help`, `help`, or `menu`, display this section and STOP. Do not execute fix steps.**
```
USAGE:
  /jFix <description>                 Proportionate shape (default)
  /jFix --narrow|--broad <description>  Bounded repair shape
  /jFix --quick <description>         Quick ticket-envelope repair
  /jFix --diagnose-only <description> Diagnose only; no repair contract
  /jFix --help                        Show this help
```

<!-- fix:help-decision-block -->
- `--quick`: cite the ticket envelope path, effective digest, and bypass class.
- Standard: Diagnose → Contract → Repair → Prove; promote on the second same-class miss.
- `/jTest uat` is required only for a real journey or `test_uat`/`both` closure mode.
- Unenrolled tickets use `/jCheckin` only.
<!-- fix:context-diagram -->
```
+------------------------------+
| /jCheckin                    |
| unenrolled; churn/intent     |
| advisory                     |
+--------------+---------------+
               | MAJOR-with-class hand-off
               v
+------------------------------+
| /jFix: policy v2 enrollment   |
| quick envelope | standard    |
| FIX: Diagnose → Contract →   |
|      Repair → Prove          |
+--------------+---------------+
               | closure mode
               v  real journey or test_uat/both
+------------------------------+      canonical defect backlog
| /jTest uat: Prepare → Execute |<---- shared by /jTest, /jFix, /jClose
| → Observe → Feedback → Encode|
+------------------------------+
```

## Legacy removal

The previous full-versus-fast dual-mode design, its parallel swarm investigation,
its blind fan-out, and every full-only path are removed by COM-386. No alias, deprecated spelling, hidden mode, compatibility path, or default-to-old-behavior branch exists.
**No old mode survives behind another spelling or default.**
There is one cycle with typed shapes; **no blind swarm path remains.**

## Architect-effort rubric consult (mandatory, at jArchitect launch time)

When composing this cycle's jArchitect launch (the architect-first call this skill already intends) — whether the cycle was entered through `/jFix` or run as orchestrator-driven Diagnose→Contract→Repair dispatches — consult the agent-team rubric's **"Fix-cycle architect effort"** section for the EFFORT TIER: `~/.claude/skills/jPlan/agent-team-rubric.md` (single source of truth; the rubric lives under jPlan for historical COM-128 ownership, but its fix-cycle scoring section binds every fix-cycle entry point, this skill included).

Current binding rule (owner, 2026-08-30): **any defect that touches pipeline build logic scores the intended jArchitect call at automatic xhigh** — via the governed jAgentLaunch override, verifying `servedEffort=xhigh`. This rule adds no extra architect step; it scores the one this methodology already has. The rubric section, not this summary, is authoritative; re-read it at consult time.

## Shape selection (typed, before jArchitect launch)

`contract_shape_request: narrow|proportionate|broad` is resolved from the CLI
flags exactly once, before jArchitect launches:

- `--narrow` -> `narrow`: the smallest safe authorized-outcome contract. If the
  evidence shows narrow cannot safely restore the selected outcome, that is a
  conflict returned to the user — never a silent widening.
- no shape flag -> `proportionate`: an evidence-sized contract; neither a point
  patch nor automatic hardening.
- `--broad` -> `broad`: a bounded recurrence-class hardening boundary that names
  the class, routes or stages, inclusions, exclusions, evidence budget, and stop
  condition. It is never product-scope authority.
- `--narrow` and `--broad` are mutually exclusive: passing both is invalid before jArchitect launch.
- `--full` and `--fast` are invalid inputs and fail with no fallback: no alias, no compatibility mapping, no default-to-old-mode, and no warning-level continue. The command stops and states the valid shapes.

Shape is orthogonal to normal versus UAT ceremony and to diagnose-only versus repair intent.
One shape flag is valid with a UAT ceremony, with a diagnose-only run, and with a
normal repair; the ceremony axis and the intent axis never change the shape
mapping. Downstream metadata records `shape_is_orthogonal: true` to make the
orthogonality checkable.

## Forward-intelligence shape obligations (COM-393 Phase 3)

The consumer records the **requested shape** and the **effective shape** before
jArchitect launches. The exact vocabulary is:

- requested shape: `narrow | proportionate | broad`
- effective shape: `narrow | broad`

The `proportionate` request is normally evidence-sized, but it promotes to
`broad` analysis when any one of these automatic-broad triggers is true:

1. shared representation or transport changed;
2. scheduling, marker, or replay decision changed;
3. more than one material reader or writer exists;
4. identity, cardinality, or presence semantics are involved;
5. deterministic/transient exception behavior crosses a retry boundary;
6. the same class has already caused an expensive late miss.

Promotion expands analysis and counterexample obligations only. It does not widen product or implementation authority.

A `narrow` effective shape maps the terminal route, runs a cheap sibling-consumer scan, dispositions every credible adjacent member, and returns a mismatch when local work cannot safely restore the authorized outcome. A narrow result must never claim recurrence-class closure. If trigger analysis requires promotion,
`narrow` cannot silently become `broad`; the mismatch is returned to the user
unless the user authorizes the broader effective shape. A `broad` effective
shape carries the bounded recurrence-class packet and its proof obligations,
while preserving the caller-authorized implementation boundary.

## Typed factual intake (what `/jFix` passes to jArchitect)

The orchestrator **copies facts and authority** into the intake. It does not add
a likely cause, preferred patch, file list, or narrowed diagnosis of its own.
The intake must include, verbatim where they exist:

1. exact A/C and GWT text;
2. the expected terminal user or system outcome;
3. the relevant scenario group, not only the currently failing step;
4. the pipeline route, entry, and exit;
5. the observed failing point and its raw evidence;
6. known adjacent evidence, including prior failures, silent states, canaries,
   fixture limitations, producer/consumer facts, and already-exonerated stages;
7. current controlling design and product-authority references;
8. explicit safety, migration, data, identity, and protected-specimen limits.

Logging and telemetry disposition is part of the intake, not an afterthought:
intake records `capture_status: proven|partial|broken|unavailable` for each
evidence source.

## Pre-launch user selection (Choice A / Choice B)

Before jArchitect launches, `/jFix` presents the acceptance boundary and asks the
user to select one authorized outcome:

- **A. Scenario repair** — repair the reported defect(s) sufficient to restore
  the named scenario outcome. jArchitect still evaluates the broader goal and
  names credible adjacent risks, but the fix contract stops at the smallest
  change and proof needed for the selected scenario.
- **B. Bounded pipeline or class hardening** — repair the reported defect(s) and
  investigate or prevent a bounded recurrence class or adjacent pipeline defect
  set within a user-approved hardening boundary naming the class, routes or
  stages, excluded areas, evidence budget, and stop condition.

Neither choice is a quality tier. Choice A is not blind point-patching, and
Choice B is not an automatic forward audit. A narrow selection constrains the
contract, not the analysis: jArchitect still reasons from the terminal outcome
across relevant stages and records credible adjacent risks as accepted,
deferred, or blocked. When evidence shows the selected outcome is unsafe,
wasteful, or insufficient, jArchitect returns a cited recommendation to the
user; no widened or narrowed contract is emitted before approval.

## Forward-Intelligence Packet v1 gate (COM-393 Phase 3)

A qualifying broad analysis carries one immutable Forward-Intelligence Packet
v1. The packet is an evidence artifact, not a second implementation authority,
and it remains distinct from the existing downstream A/C 7 handoff packet
section later in this skill. The packet identity and revision contract are:

- Artifact kind: `fix-forward-intelligence`
- Schema: `fix-forward-intelligence.v1`
- Path: `.jswarm/plans/<KEY>/<KEY>.fix-forward-intelligence.<slug-slug-slug>.<YYYYMMDD>[.vN].md`
- Same-day revisions append `.v2`, `.v3`, and so on; referenced packets are never overwritten.
- Packet revision invalidates old receipts until consumers re-read the revised path and digest.
- Every downstream dispatch records packet path and SHA-256 digest, schema, effective shape, paired fix-contract path and SHA-256 digest, and the packet revision.

The packet contains these 17 exact headings, in this order, without renaming or
collapsing them:

1. Named Recurrence Class And Authorized Terminal Outcome
2. First Visible Break And Masked Downstream Legs
3. Changed Contract Surfaces
4. Producer / Writer Inventory
5. Consumer / Reader Inventory
6. Caller / Route Inventory
7. Authority And Correlation Matrix
8. Presence And Shape Semantics
9. Replay / History Matrix
10. Exception / Retry Matrix
11. Trace Receipt And Gaps
12. Discovery Receipts
13. Counterexample Packet
14. Inclusions, Exclusions, And Adjacent-Risk Dispositions
15. Evidence Budget And Stop Condition
16. Review Receipts
17. Downstream Leverage Criterion

The packet gate is fail-closed for qualifying broad implementation. Reject when any heading is missing or empty. A materially applicable section may not use a bare `N/A`; use the evidence-backed form `N/A — <specific reason>; evidence: <citation>`. Record a disposition for every discovery candidate. Allowed candidate dispositions are `included`, `excluded — <evidence-backed reason>`, `accepted outside scope`, `deferred — <owner>`, and `unresolved`. Boundary-changing `unresolved` is blocking. The gate also rejects when effective shape conflicts with trigger analysis, when packet path and SHA-256 digest do not match the dispatched packet, or when paired fix-contract path and SHA-256 digest do not match the current fix contract. Broad implementation also requires an approving pre-review receipt whose verdict is exactly `APPROVED_FOR_IMPLEMENTATION` (not `REVISE_CONTRACT` or `BLOCKED_BY_EVIDENCE`).

Choice A/B remains the caller-authorized outcome boundary. Packet completeness,
analysis promotion, and pre-review do not widen product or implementation
authority. The downstream A/C 7 handoff packet remains a separate contract and
its existing semantics are unchanged.

### Production consumer invocation and preactivation

For qualifying work, `/jFix` invokes the sole executable Packet v1 authority,
`skills/fix/scripts/forward_intelligence_packet.py`,
rather than prose or a test-side oracle. It runs `assemble` with the canonical
producer report, immutable fix contract, structured fix-cycle input, producer
readiness, Gate A, and per-fix pre-review inputs; it then uses `validate
--for-implementation` against the exact persisted Packet digest. The assembler
is the only component that may group/order source facts, derive revision and
effective shape, render the 17 headings, and hash retained bytes. It refuses
missing semantic inputs rather than inventing them.

Qualifying dispatch then runs `activation-check`. The controlled
`forward-intelligence-activation.v1.json` is active since COM-393 closed on
2026-09-01, and `activation-check --qualification qualifying` returns allowed.
This does not change Choice A/B, proof order, shape triggers, or the separate A/C 7 packet.

## Two-stage review composition (COM-393 Phase 4)

For a **ticketed qualifying broad** `/jFix`, pre-implementation contract challenge
and post-implementation code review are distinct stages. The pre-implementation
review challenges the Packet, authorized outcome, causal RED, and proposed
counterexamples before implementation. It returns exactly one verdict:
`APPROVED_FOR_IMPLEMENTATION | REVISE_CONTRACT | BLOCKED_BY_EVIDENCE`.

The post-implementation review inspects the actual diff and proof; it is not a
re-run of the pre-implementation contract decision. Each activated review stage
records a lean receipt with reviewer identity, evidence identity, verdict, and
stage. When review is activated, the pre-reviewer may not approve its own later
post-implementation review.

Ticketed qualifying broad fixes compose the two stages only when existing
condition 4 activates review. This does not add condition 5, a standing review
trigger, or a standing reviewer fleet. Phase boundaries and code existence remain
non-triggers. No standing reviewer fleet is created.

Ad-hoc local fixes remain on the ordinary cheap path. They do not materialize
these ticketed composition obligations merely because code exists or a phase
ends. The current owner-authorized activation binding, Packet v1, Choice A/B,
six automatic-broad triggers, caller-authorized scope, and separate A/C 7
handoff packet remain unchanged.

## Caller-dependent approval matrix

`/jFix` validates its own invocation before any launch:

- `invocation_origin`: `developer-direct` or `delegated` (with the named
  orchestrator reference). Malformed or unknown input blocks the run.
- For delegated runs, `inherited_developer_stop_directive.mode`: `none` or
  `require_contract_approval` (with the directive reference).

| Origin / directive | Approval behavior |
| --- | --- |
| `developer-direct` | **Developer-direct issuance always stops after the exact path and SHA-256 receipt** are presented, for explicit developer approval of the contract. |
| `delegated` + `none` | **delegated+none records orchestrator approval and continues**; the record names the orchestrator that holds the authorization. |
| `delegated` + `require_contract_approval` | **delegated+require stops for explicit developer approval** tied to the inherited directive reference. |
| anything else | **malformed or unknown input blocks** the run before jArchitect launches. |

## Website action notifications

The website-action watcher operation is maintained in [reference.monitor-operation.md](reference.monitor-operation.md).

## Fix-contract identity and revision

After evidence synthesis and before jTestEngineer, jCoder, or jUIDesigner
starts work, jArchitect authors and issues the developer-readable plain-English
contract at exactly:

`.jswarm/plans/<KEY>/<KEY>.fix-contract.<slug-slug-slug>.<YYYYMMDD>.md`

`<slug-slug-slug>` is a concise three-part lowercase ASCII kebab-case
description of the repair outcome; `<YYYYMMDD>` is the issuance date. A same-day
revision appends `.v2`, `.v3`, and so on after the date and before `.md`.
Issuance by jArchitect is the contract agreement for workflow purposes; no new
human acknowledgement gate is inserted after issuance because Choice A/B was
already authorized upstream.

Every downstream dispatch and return receipt records the exact contract path and SHA-256 digest.
A missing or mismatched identity blocks that lane from claiming it executed the
current issuance. Every contract revision creates a new version and SHA-256 digest and
invalidates every prior approval: the approval matrix is re-run against the new
issuance before downstream work continues against it. Only jArchitect revises the contract; the orchestrator,
tester, implementer, and assurance lanes cannot edit, substitute, or paraphrase
it. A revision that changes the user-authorized outcome first returns to the
user.

## Qualifying consumer activation and receipt binding (COM-393 Phase 3)

For qualifying work, use the current producer-readiness and Gate A receipt bindings recorded in the active Packet v1 authority. The active `forward-intelligence-activation.v1.json` authorizes qualifying dispatch after the deterministic activation check returns allowed. Ordinary non-qualifying local fixes retain their existing `/jFix` path.

## Downstream composition

The issued contract is the bulk of every downstream jTestEngineer, jCoder,
jUIDesigner, and selected assurance prompt — verbatim or through a direct
immutable file reference the lane must read before acting. The orchestrator may
add a clearly separated situational and ground-conditions envelope limited to
current execution facts (runtime identity, latest logs and failing commands,
allowed reads and writes, launch metadata, safety restrictions). The envelope is
not architectural authority: it cannot paraphrase, weaken, omit, reinterpret, or
contradict the contract, and a conflict stops the lane and returns to the same
jArchitect thread.

## Retained proof floor (ordering is mandatory)

The retained proof sequence for every repair, in order:

1. `/jFix` consumes the `/jDebug` diagnosis-conviction receipt without reinterpretation — conviction precedes implementation.
2. Localization: the boundary-fidelity resolver contract below runs before any
   production change.
3. **Causal RED collects and executes before production changes**, fails for the
   intended mechanism, and uses an independently sourced expected value with a
   faithful fixture.
4. For `composed_runtime|sandbox|command|wire|persistence|identity|vocabulary`
   declared risks, the risk-triggered boundary map precedes implementation.
5. **connected real-seam integration, parity, or capture-replay proof** for every
   declared non-`local_logic` risk.
6. **counterexample, revert, or fault-seed proof** showing the test or canary
   fails when the protected behavior is deliberately broken or reversed.
7. **selected cheap post-chain canary before expensive UAT/walk** — the canary
   is selected by risk, not universal.
8. The cycle **reruns the final effective proof set** after the last production
   change.
9. When UAT is required, closure runs through
   **/jTest uat prepare -> execute -> feedback**; `/jFix` cannot close acceptance.

## Boundary-fidelity localization contract (COM-387)

Before implementation, resolve the immutable floor → global profiles → project
→ ticket → frozen-cycle contract with the non-executing
`$HOME/.claude/skills/fix/scripts/fix_localization.py` receipt. The floor
requires **Diagnosis conviction before implementation**, **Causal RED before production code**,
**Independently sourced expected values**, **Connected real-seam proof for every non-`local_logic` declared risk**,
**Counterexample/revert/fault-seed proof**, **Selected canary before expensive walk/UAT**,
and **Final rerun of the effective proof set**.

For `composed_runtime|sandbox|command|wire|persistence|identity|vocabulary`,
`jTestEngineer` precedes `jCoder` and supplies the boundary map, representative
fixture, independent authority, closing connected proof, counterexample, and
canary. `local_logic` retains diagnosis → RED → fix → GREEN → module/impact/final verification.
The resolver consumes `next_dispatch: normal_debug|inventory_audit` verbatim from COM-386 receipts;
it does not evaluate “same path,” downstream ordinal, prior-fix effectiveness,
or any COM-386 predicate.

Frozen policy is proof metadata, not an executor: structured runners are
rendered by the global runtime and never executed by the resolver. A
UAT-required cycle returns to `/jTest uat prepare → execute → feedback`; `/jFix cannot close acceptance`.

## Telemetry claimability and durable evidence

- Log absence under broken, partial, filtered, or unproven capture is not claimable from logs, and never proof of non-execution: broken or unproven capture cannot support a log-absence claim.
- Every failing gate persists complete failure evidence even when display output is truncated.
  Display truncation may summarize only when it preserves and cites the full
  result; every gate failure keeps a durable path for complete gate failure evidence.
- Authoritative durable state outranks telemetry: the authoritative durable state
  controls unless a separate product contract explicitly makes an event or
  telemetry state authoritative.
- telemetry-invisible classes close through causal production-path tests or connected proof — never through more logging; classes invisible to telemetry are closed this way, not with more logging.
- Rejecting-input snapshots are **selectable safe rejecting-input snapshots**:
  safe to persist only when bounded, useful, and stripped of secrets, protected
  data, and unnecessary payloads. They are selectable, not universal.

## LLM producer/consumer pair

For any LLM-produced contract field (ids, guids, stable keys, target refs), the
contract must include both halves of the defense pair: the prompt-side mint/copy/omit rule
(the producing prompt carries the identity vocabulary and the
mint-versus-copy-versus-omit rule) and the **deterministic consumer gate**
(typed refusal or invariant making incorrect output safe). Tests prove the
rendered prompt contract and the deterministic gate — never live LLM output. A
gate without the prompt contract is a classified failure loop; a prompt without
a gate is silent corruption.

## A/C 7 packet schema

Every downstream packet requires **one current approved contract path/digest**
recorded on the dispatch and the return receipt; a stale digest prevents the
lane from claiming execution against the current issuance. The retro/evidence
schema requires contract shape, approval path, catch stage/latency, defect location, and control value
for each recorded defect and catch, so field learning stays measurable without a
calibration phase: there is no delayed calibration or later activation.

## Fix-cycle assembly, not architecture doctrine

`/jFix` assembles acceptance context, user authority, trusted cycle identity, policy metadata, and ground conditions, then invokes the generic jArchitect contract; it must not reteach the full architecture method. Terminal-
outcome framing, anti-taint context, proportionate evidence selection, rejected
alternatives, adjacent risks, and mismatch-return rules live in the controlled
jArchitect body and its regenerated projections, not in this skill. `/jFix` owns
the workflow: Choice A/B labels, the exact fix-contract filename, UAT and
`/jTest` routing, reviewer authorization, packet fields, path/digest receipts,
and ticket closure.

Choice A/B selection governs reviewer authorization proportionally: jCritic is
selected when actual code/contract/test-quality critique has decision value;
jVerifier and jSecurityReviewer appear only with explicit user authorization;
UAT appears only for a real user journey and runs through `/jTest`-owned
procedures (referenced here, never duplicated).

## Fresh-reset guard and atomic activation

Every completed or interrupted cycle leaves no stale capability, authorization leakage, or orphan child:
trusted fix-cycle capabilities expire with the cycle, child sessions terminate
or return before the cycle resets, and the next cycle starts from a clean
authorization state.

Once all masters, runtime assets, validators, generators, projections, and
fresh-process proofs pass, the upgraded cycle activates atomically for 100 percent of applicable Claude Code orchestrators.
No shadow mode, percentage rollout, delayed calibration, compatibility cohort, or later activation phase exists.

## Integration

- `/jFix` → `/jDebug` when diagnosis conviction is missing; the cycle consumes
  the conviction receipt.
- `/jFix` → `/jPlan` when the fix reveals need for a new ticket.
- `/jFix` → `/jGo` when multiple related fixes are needed.
- `/jFix` → `/jClose` when the fix completes a ticket.
- `/jFix` → `/jTest` when a real user journey requires UAT; closure runs through
  `/jTest uat prepare -> execute -> feedback`.
