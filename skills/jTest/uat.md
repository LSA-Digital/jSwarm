# /jTest: UAT sub-area (options 2–4)

Read `execution-protocol.md` first (MASTER INVARIANT in `SKILL.md`).

## Active rule loader

`UAT_RULES.json` is the only active-rule authority. At each gate, load only the IDs named below and apply their `imperative`, `stop_condition`, and `required_evidence` fields exactly. Do not paraphrase a second active rule into Markdown. Historical detail is cold in `UAT_RULE_RECEIPTS_REFERENCE.md` and is read only when a receipt pointer needs investigation.

| Point of use | Load |
|---|---|
| New owner input or recorded behavior confirmation; before fix/test dispatch; before round refresh | `UAT-G1`, `UAT-R3` |
| Ready signal, owner invite, or jQATester dispatch | `UAT-G0` |
| Round creation, issuance, refresh, or readiness/deploy change | `UAT-T1`, `UAT-R1`–`UAT-R8` |
| Asset-class selection or legacy archive review | `UAT-T1` plus the asset-class contracts below |
| Test-authoring dispatch, GWT-change drift sweep, or return review | `UAT-R9` |
| QA transport and handoff | `UAT-D1` |
| Combinatorial verification design | `UAT-D2` |
| Layer-2 semantic judgment dispatch | `UAT-D3` |
| Instrument selection | `UAT-D4` |

## Instrument selection behavior lock

Load `UAT-D4` before selecting a UAT verification instrument. Its `imperative`, `stop_condition`, and `required_evidence` fields are the controlling contract. The behavior decision is simple: a `YES` behavior lock permits deterministic replay; `NO` or `UNKNOWN` selects a current-script jQATester walk first.

The only legal lock-down points are **pre-merge**, **post-owner-acceptance**, and **round close**. Record the behavior-lock decision and the required UAT-D4 evidence at the point used. Stop instead of substituting deterministic replay when the decision is `NO` or `UNKNOWN`, the current-script walk is unavailable, or the evidence does not establish the selected instrument.

Green unit or deterministic tests, a prior walk against an older script, checkout state, and an owner assertion without the current walk are not behavior-lock evidence. `/jTest` remains the sole verification authority.

## Asset map and ownership

- Owner/product intent: `TICKET.uat-scenarios.md` and high-level `uat-scenarios-e2e.md` chains.
- Delivery journey design: canonical `TICKET.uat-scenario-steps.md`; legacy `uat-test.md` remains an alias until its separate migration.
- Orchestrator verification: jQATester execution, then the ticket-owned `<ticket plan folder>/<TICKET>.UAT-CURRENT-ROUND.md` instantiated from `UAT_CURRENT_ROUND_TEMPLATE.md`.
- UAT assets are mutable acceptance assets, not durable regression tests. Regression guidance lives in `regression.md`.
- There is no separate E2E step-script asset: high-level E2E chains compose scenarios, and each scenario owns its steps.
- Archive scenarios and tests that predate the acceptance baseline established by ; they are legacy/invalid until separately audited and aligned.
- Detailed background: `docs/devops-testing-master.md`; troubleshooting history: `docs/testing/uat-fix-playbook.md`.

## Point-of-use checklists

### Owner-sync checklist

1. Load `UAT-G1` and `UAT-R3` when owner input, a review finding, or a recorded confirmation event changes expected behavior.
2. When a processed owner-feedback receipt, an owner or ticket boss ruling receipt, or an orchestrator verification receipt confirms an accepted behavior outcome absent from or contradictory to current GWT, update canonical `uat-scenarios`/GWT in that same work unit before `/jFix`, test or implementation dispatch, round refresh, re-seal, preparation, or any owner-ready claim; record a successor clause and never defer the update to a later lifecycle step.
3. When a canonical GWT outcome or FAIL clause changes with an authoritative ruling receipt, or a batch dispatch/return binds shipped production behavior to a new ruling already recorded in GWT, the same batch dispatches `jTestEngineer` over unit/integration expectations; its report cites the ruling, records the superseded and successor clauses plus search seeds/results, fixes only `TEST-DRIFT`, and leaves every real defect RED. Observed behavior alone never authorizes an expectation change.
4. After the sweep, re-derive scenario steps, the canonical `uat-test`/legacy alias, and affected acceptance wording from the amended GWT in the same work unit.
5. Before dispatch or round refresh, retain the evidence required by those records and the triggered drift-sweep report.

### Acceptance TDD chain

Derive detailed steps from executable GWT, write clause-bound tests, prove RED, implement production behavior, prove GREEN at the production/user-visible boundary, then obtain an independent jCritic review. Feed accepted code feedback back into the scenario steps as the journey evolves.

### Preflight checklist

1. Load `UAT-G0` before any ready signal, invite, or QA dispatch.
2. Use the project wrapper/advisories for runtime-specific commands; never substitute checkout state for running-stack proof.
3. Record the required currency, smoke, and applicable replay evidence in the handoff or round source material.

### Round checklist

1. Load `UAT-T1` and `UAT-R1`–`UAT-R8` before creating or patching a round.
2. Instantiate the co-located template only at the canonical ticket-owned path.
3. Populate header, Definitions, Keys & Sets, HOT and COLD tables, data-source links, Who, Journey/Walk, PASS/FAIL, Closes/NFRs, Notes, and clickable sessions from source assets.
4. Re-evaluate every loaded stop condition before issuing or refreshing.
5. Preserve Options 2–4 ownership: source intent first, derived steps second, QA execution third, owner round last.

## /jTest uat prepare: PREP-only

Use `/jTest uat prepare [HAS-XXX] [--only #NNN,#MMM] [--force-recreate]`. It resolves focus, gathers the ready-to-test bundle, certifies currency, and prepares the canonical round. It does **not** dispatch jQATester, run the QA walk, or compose the owner invitation. The deprecated `/uat-round` alias forwards its arguments verbatim here, including `--localization-smoke`.

Before consuming a scenario semantic verdict into the prepared round, apply `UAT_SEMANTIC_VERDICT_CONSUMPTION_GATE.md`; this canonical `/jTest` pointer owns the detailed cold contract.

Resolve focus from the explicit ticket, then branch, testing context, or the Most-recently-modified eligible round; `INITIALIZED_NOT_READY` is ineligible. Unless `--only` pins a subset, the bundle = union of every **`1.OPEN`** row with a committed fix and commits landed since the round file's `Last refreshed` header. Materialize wholesale only at issuance; a post-issuance change must patch in place. The canonical round file outranks chat; under `UAT-R2`, cite sources in each Journey cell and do not add a Sources column.

## Gate card: complete before preparation

1. Load `UAT-D4` before instrument selection; record the behavior-lock decision.
2. Load `UAT-G0`; require the selected typed certification receipt: Docker retains jInfra recreate/currency evidence, while `local-process-v1` requires fresh listener lineage, content/build/fixture/config identity, served-byte binding, targeted smoke, and current-script replay N/A when UAT-D4 selects the current walk.
3. Load `UAT-G1`; require owner GWT, derived steps, and acceptance wording to agree.
4. Load `UAT-T1`, `UAT-R1`, `UAT-R2`, and `UAT-R8`; require the canonical path, truthful time/deploy state, Journey citations, and relevant NFRs.
5. If any loaded stop condition fires, stop: do not prepare, materialize, or patch.

For UAT-G0, `jinfra-docker-recreate-v1` retains its certified recreate/currency path. `local-process-v1` certifies only with current listener lineage, byte-equal served production assets, and targeted smoke; a missing, stale, cross-source, or malformed receipt is `BLOCKED: stale stack`.

## Instrument-currency outcome

When behavior lock is not `YES`, deterministic replay records exactly `N/A (behavior not locked; current jQATester walk required first)`; the unlocked path requires a current walk receipt. Any required diagnostic run names the current step, defaults to no more than five minutes per step and fifteen minutes overall, and honors stricter project limits. It aborts at deadline and never retries unchanged. Emit `STALE-SPEC-CANDIDATE` when the current specification is stale or contradictory. Record recovery, but recovery is diagnostic only and cannot pass the original live-path outcome.

## /jTest uat prepare delegation advisory

Delegate `/jTest uat prepare` to a `jTestEngineer` subagent; do not run it inline in the orchestrator context. The deterministic gates (stack currency, certification, chain verification, issuance) make routine preparation executor-class work; escalate to `jOracle` only for a persistent logical blocker (per the QA dispatch checklist). It is not jQATester's lighter browser/API UAT-execution lane.

Give the subagent this compact handoff and require it to: **resolve focus** → **gather test bundle** → **certify stack currency** → **refresh the canonical round file**.

- Ticket identity and plan folder: ticket and `.jswarm/plans/<KEY>/`.
- Resolved focus and round state: exclude `INITIALIZED_NOT_READY`; use `default@1` and the canonical round file path.
- Test bundle: scenarios JSON / `uat-scenarios`, `uat-scenario-steps`, and exact GWT clauses.
- Active point-of-use rules: `UAT-D4`, `UAT-G0`, `UAT-G1`, `UAT-T1`, `UAT-R1`, `UAT-R8`.
- Stack currency and environment: stack .env, `API_PORT`, `UI_PORT`, health check, and currency certification.
- Round materialization contract: `uat_round_materialize.py`; adopt-absent-only; receipt-gated switch; snapshot/receipt; gate-card pass before materialization.
- QA handoff: 13-field handoff, transport ladder, field 10 verbatim GWT.
- Return contract: refreshed round file, receipts, gate-card pass/fail, and no auto-promotion.

### Test-authoring checklist

1. Load `UAT-R9` before every jTestEngineer or test-authoring jCoder dispatch.
2. Use the accepted `UAT_GWT_TEST_AUTHORING_DISPATCH_TEMPLATE.md`; copy it into the dispatch and fill every placeholder without altering the bound clauses.
3. Bind exact scenario ID and verbatim relevant GWT clauses; require clause-tagged tests and production/user-visible boundary proof. For a drift sweep, derive search seeds from removed/replaced copy, public codes/status names, affected public identifiers/API names, and scenario or requirement IDs.
4. Apply `UAT-G1` before further work if the lane discovers an expectation absent from the GWT.

### QA dispatch checklist

1. Load `UAT-G0`, `UAT-D1`, and, when applicable, `UAT-D2`.
2. Select transport with `jswarm.qa_transport` from live exposure plus a bounded health probe, not configuration presence.
3. Build the handoff from `docs/templates/QA_DISPATCH_HANDOFF_TEMPLATE.md`; fill all 13 fields or use `N/A (reason)`.
4. Field 10 carries exact scenario IDs, relevant GWT clauses verbatim, and matching step-script sections.
5. Keep run-specific specimen IDs, environment facts, known-good walks, monitor/report/evidence paths, and expected-failure semantics in the handoff; standing browser mechanics stay in the agent contract.
6. For combinatorial fixes, deterministic tests enumerate the full matrix on captured production-shape data and capture-replay/projection fixtures pin write-path→read-path behavior; above about 10 permutations, jQATester remains limited to a handful of journey-shape spot checks.
7. If a malformed handoff blocks the run, correct it once; use disk-first recovery and jOracle escalation for a persistent logical blocker. Orchestrator-direct QA is not permitted.

## Option 2: Create or tighten a Live Show UAT script

Use before delegating jQATester.

1. Read the official architecture UAT/scenario inventory.
2. Create or update the ticket's scenario inventory from `UAT_SCENARIO_EXTRACT_TEMPLATE.md`.
3. Create or update canonical `TICKET.uat-scenario-steps.md` (legacy `uat-test.md`) from `UAT_TEST_TEMPLATE.md`.
4. Apply the owner-sync and test-authoring checklists.
5. Fill execution mode, driver, runner, regression command/deferral, base URL/health, auth, state preflight, runtime monitor, report/evidence paths, and live-show callouts.
6. Assert user-visible behavior, public APIs, console/network results, or documented evidence; internal state is diagnostic only.
7. For backend/API/workflow UAT, include the monitor contract or an explicit `N/A (reason)`.

### UAT scenario JSON READ integration
<!-- uat-scenarios:read-integration -->
When a project has adopted the scenario engine, query canonical JSON with `jswarm/uat-scenarios/query_uat_scenarios.py`; generated Markdown is derived/read-only. Projects without adoption continue using their Markdown inventory.

Reference: `docs/testing/live-show-mode-guide.md` and `docs/templates/UAT_TEST_TEMPLATE.md`.

## Option 3: Execute Live Show UAT with jQATester

Use for developer-watched UAT.

1. Confirm the canonical step script is current and complete.
2. Load `UAT-D4`; when behavior lock is `NO` or `UNKNOWN`, select the current-script jQATester walk first and retain its required evidence before any deterministic replay.
3. Apply the preflight and QA dispatch checklists.
4. Run the project preflight wrapper or manifest health/auth/data checks.
5. Start the script-required runtime monitor; record task/log/filter/cadence and review it after each scenario.
6. Dispatch jQATester with ticket/plan, scenario and step assets, architecture inventory, e2e manifest, mode/driver/commands, environment/auth/state setup, monitor contract, and report/evidence paths.
7. Require visible headed/foreground browser proof before counting live-show as started.
8. Review the QA report and monitor findings; preserve evidence and classify every terminal or blocked outcome.

Reference: `docs/testing/live-show-mode-guide.md`, the live jQATester definition, and `docs/templates/UAT_REPORT_TEMPLATE.md`.

**Before preparing or issuing a round, you MUST read the [Good-enough round methodology](uat/round-registration.md#good-enough-rounds).**
## Option 4: Compose an owner UAT invitation round

Use after Option 3 or a fix wave makes journeys ready for owner review.

1. Apply the preflight and round checklists; a stale-stack invite is blocked.
2. Identify invited journeys in priority order and known-broken adjacent paths.
3. Instantiate or patch the one canonical ticket-owned round from `UAT_CURRENT_ROUND_TEMPLATE.md`; never free-compose or append history.
4. When issuing or cutting a bundle round, wholesale template instantiation is valid; after issuance, readiness/deploy/defect truth changes patch that same round in place.
5. Keep detailed steps in the step script. The canonical round file governs durable content and provenance; send only a short chat summary table (journey, closes, one-line walk) plus the round-file link, and never let chat contradict it.
6. Re-check loaded stop conditions and issue only when the round reflects current runtime and ledger truth.

### Website feedback notification operation

Before arming the Monitor or sending an Option 4 invitation, read and follow
[`uat/notification-operation.md`](uat/notification-operation.md). It is required operating procedure. For the prepared-round front door, read [`uat/round-registration.md`](uat/round-registration.md) before sending the owner a link.
