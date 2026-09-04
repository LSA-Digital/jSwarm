## Trigger

`--with`-only addon for QUICK, FULL, or FEATURE; skipped in Lite. During Step 5, select when at least one evidenced ticket condition matches T1-T5 below. Emit only the rulings whose triggers match, plus the mandatory escalation-budget capstone. If no trigger matches, omit the addon and the entire section.

- **T1 — destructive / irreversible:** live execution can mutate or delete **non-disposable state**, and rollback is unavailable or unproven, or selecting the wrong target could cause material loss. Ordinary transactional writes, reversible migrations, and cleanup of test-created disposable state do not match T1.
- **T2 — cross-tool / cross-repository contract:** acceptance-tier proof or safety/destructive control flow consumes or asserts on output produced by another executable, repository, or independently versioned contract. Supporting-tier tests alone do not activate T2.
- **T3 — live-state proof:** acceptance requires choosing among **pre-existing live targets**, or a target's identity or eligibility can drift between planning and execution. A target the test flow creates, owns, and selects without ambiguity does not match T3.
- **T4 — inherited red baseline:** the affected battery is demonstrably failing on the verified base revision **and** governing repository policy permits a named-owner inherited-failure disposition. A repository with a strict zero-red gate does not match T4; its red baseline is a blocker to clear, not a reason to weaken the gate.
- **T5 — external dependency assumption:** an acceptance criterion depends on a prerequisite outside this ticket's normal setup or control, and the prerequisite's absence would hold or invalidate that criterion. Routine tool installation and declared local setup performed by this ticket do not match T5.

## Header lines

None.

## Plan sections

## Pre-issued Execution Rulings

> Binding answers for this ticket's matched failure classes. Retain only matched T1-T5 rulings from this pattern's library; do not copy unmatched rulings or leave placeholders. Every retained ruling includes its incident, date, and one-line consequence. This section is absent when no trigger matches.

**Matched triggers:** [T1 / T2 / T3 / T4 / T5 — cite the ticket fact that activates each]

1. **[Ruling title]:** [binding ticket-specific ruling from the matched-trigger library]. *Provenance: [incident], [date] — [one-line consequence].*

**Escalation budget (mandatory capstone):** legitimate owner escalation set: [none, or normally one narrowly named decision with the exact condition that activates it]. Before treating any other issue as blocked, the orchestrator must cite the emitted ruling it applied and state the specific uncovered fact; only a genuinely uncovered case may escalate. A category such as “anything risky,” “owner judgment,” or “unexpected blocker” is not a bounded escalation item.

**Extension protocol:** a new reusable ruling may be added only with incident provenance: incident identifier, date, and one-line consequence. A ruling without its incident is dogma and is ignored.

**Honest limit:** this armor helps orchestrators who read the plan and otherwise stall on ambiguity. It does not replace independent review gates, which catch non-readers, fabricated evidence, and incorrect execution.

## Rules

After selecting this addon, replace the plan-section scaffold with only the matched rulings below and the capstone. Adapt nouns and commands to the ticket, but preserve each ruling's invariant and provenance. Do not weaken a stricter repository policy. One ruling per matched trigger is the default; T2 emits both fixture-fidelity and structured-fields rulings because they defend different halves of the same producer/consumer seam. T4 emits both green-definition and ownership rulings when its qualified trigger matches.

### Ruling library

1. **[T4] Green definition.** Where governing repository policy permits an inherited-red disposition, “full battery green” means zero failures attributable to this ticket's diff **and** every inherited failure has pre-diff baseline evidence plus a named owning ticket. Run the complete affected battery at every gate; phase-scoped substitutes are forbidden. A strict-zero repository policy remains controlling and makes this ruling inapplicable rather than “void.” *Provenance: a full-battery deadlock — a literal strict-zero reading was unsatisfiable because the ticket needed to land before the inherited-failure fix could land.*

   Anti-evasion: “inherited” requires the same failing command/test on the verified base revision, not an assertion by the implementer; the owner ticket must exist before gate closure and name the failure and disposition.

2. **[T2] Fixture fidelity.** Acceptance-tier assertions on another tool's or repository's output must run the real producer or consume byte-captured real output from the exact producer mode the acceptance path consumes. A capture binds producer command and version, source revision, timestamp, and content hash. Schema-derived or hand-authored synthetic fixtures remain permitted in supporting-tier tests for parser branches and edge cases, but they never certify the cross-tool acceptance criterion or unlock safety/destructive control flow. Prove ledger writer/reader symmetry: every key the acceptance assertion queries is written by the real producer path exercised by that proof. *Provenance: a critical fixture incident — a stub authored the exact substring its consumer matched, producing three vacuous green assertions under a destructive-risk acceptance criterion.*

   Anti-evasion: copying real-looking bytes by hand, capturing an unrelated producer mode, or promoting a synthetic supporting fixture to acceptance evidence does not satisfy fidelity; the proof binds the exact producer mode and semantic value the consumer branches on.

3. **[T2] Structured fields, never prose.** Safety/destructive verdict branching parses a structured discriminant with a closed enum or validated schema; substring or regex matching of human prose is forbidden. If this ticket lawfully owns the producer contract and the needed distinction is absent, extending that structured contract is in scope. If the producer is third-party or otherwise outside the ticket's authority, pin the producer version and output grammar, isolate the parser behind one adapter, and prove it against real output from every consumed state—never silently expand the ticket's authority. The acceptance proof shows each consumed structured value reaches the intended branch. *Provenance: live-lease misclassification, 2026-08-02 — the consumer matched error prose the real producer never emits for the dangerous state, so a live watcher could have been retired incorrectly.*

   Anti-evasion: wrapping prose in JSON is still prose matching; the discriminant must be a typed field, or the external-producer adapter must expose one from a pinned, real-output-proven grammar.

4. **[T3] Execution-time targets.** A live proof selects its target from a named predicate evaluated immediately before the attempt. State the evidence source and maximum evidence age; re-evaluate after any wait or mutating precondition. Prefer a target created and owned by the test flow. A planning-time name, cached “current” target, or manually chosen existing target is forbidden. *Provenance: two pre-named targets went stale in turn; target-selection method, not target availability, was the defect.*

   Anti-evasion: the predicate must exclude unsafe/foreign targets and the evidence record must show its values and timestamp, not merely say “selected dynamically.”

5. **[T4] Pre-existing is not a disposition.** Every newly surfaced inherited failure receives a named owning ticket before the gate closes. The ticket records the failing command/test, base-revision evidence, impact, and intended disposition; “pre-existing,” “unrelated,” or a backlog note without an owner is not closure. *Provenance: an unowned failure was nearly laundered as inherited until a follow-up ticket was filed at the gate.*

   Anti-evasion: a placeholder or umbrella ticket that does not name the failure and evidence is not an owner.

6. **[T1] Destructive-step preconditions.** State the destructive ordering before implementation starts. No live destructive execution occurs until every refusal/guard path named by the acceptance criteria and risk model has deterministic RED-to-GREEN proof, the exact target has a fresh read-only preflight, and the final independent review gate returns **Ship** while explicitly considering that evidence. *Provenance: a destructive live proof was nearly run on coverage later shown to be fabricated.*

   Anti-evasion: one happy refusal test, an author's self-review, or a Ship verdict that predates/fails to cite the final guard evidence cannot unlock the action.

7. **[T5] Dependency-assumption preflight.** State every external assumption with a Phase-1 verification command or observable check and a named `BLOCKED-INCONCLUSIVE` fallback. Record command, result, timestamp/revision, and which acceptance criterion is held. Never silently skip the check, claim success through the fallback, or block unrelated phases whose prerequisites are satisfied. *Provenance: a sequencing dependency — primary-checkout invocations exit 2 until the prerequisite lands; without the fallback the plan either lies about parity or stalls unrelated fixture/worktree work.*

   Anti-evasion: “verify dependency” without a command/observable predicate and recorded result is not a preflight; `BLOCKED-INCONCLUSIVE` is a visible held state, never a pass synonym.

8. **[capstone — mandatory whenever any ruling is emitted] Escalation budget.** Name the complete legitimate owner-escalation set, normally one narrowly defined decision or none. Each item states the exact observed condition and the owner decision requested. For any other perceived blocker, the orchestrator first cites which emitted ruling it applied and names the uncovered fact; only that uncovered case escalates. The budget bounds recurring decision classes; it does not suppress implementation defects, novel runtime evidence, or genuinely uncovered decisions. *Provenance: coordinator escalation tax — recurring decision classes across several tickets could have been bounded by the plan before execution.*

   Anti-evasion: “anything unexpected,” “anything risky,” “owner judgment,” or a broad class of future decisions defeats the budget and is forbidden.

### Compatibility

- Lite never selects this pattern and carries no empty section or trigger assessment; the manifest's addon `allowed_bundles` contract makes `LITE --with preissued-rulings` a typed assembly failure.
- This pattern does not weaken `security-baseline`, NFR, UAT, plan-governance, review, or repository-specific zero-red requirements; the stricter applicable rule wins.
- The capstone constrains escalation behavior in the authored plan but cannot mechanically force a non-reader to comply; independent review remains the enforcement backstop.

Provenance: generalized from another project's § Pre-Issued Execution Rulings (owner-directed, 2026-08-02).