# /jTest: Regression sub-area (options 5-8)

Read `execution-protocol.md` first (MASTER INVARIANT in `SKILL.md`). This file has three hard-labeled subsections: what is available today, what is T4-owned and must not be invoked yet, and the maintenance rule that is active now regardless of T4.

## Route-proof tripwire (guards against the six-wrong-target-test failure mode)

A route-proof test does not count if it fakes, directly calls, or bypasses the classifier or router instead of traversing the real public route to a visible/public outcome. A prior baseline shipped six wrong-target tests that passed while proving nothing about the real route — passing is not proof. A satisfying route-proof test must traverse the real public route (the actual entry point a user or caller invokes) and assert a visible/public outcome (observable state, response, or rendered UI); a test that fakes, directly calls, or bypasses the classifier/router is supplemental evidence at best — never route proof.

**Class boundary (owner-ruled 2026-07-08):** regression tests are the higher-effort DETERMINISTIC class, constructed to survive non-deterministic conditions (capture/replay, managed state). UAT-class assets (`uat-scenarios` + `uat-scenario-steps`, see `uat.md`) are flexible and rapid-change — they are NOT regression tests. Alignment rule: the capture-replay regression ticket owns the audit of ALL existing tests against updated uat-scenarios — update aligned tests, retire misaligned ones; anything predating the capture-replay regression rollout is legacy/invalid and gets archived.

## UAT-D4 regression eligibility

See [the instrument selection behavior lock](uat.md#instrument-selection-behavior-lock): deterministic replay/regression is eligible only with a `YES behavior lock` at `pre-merge`, `post-owner-acceptance`, or `round close`. Defer all operational requirements to canonical UAT-D4 guidance.

---

## Regression and replay ownership split

**Layer 1 — durable, deterministic regression owned by affected product tickets.** This ownership rule does not author other tickets' tests. A Layer-1 invariant that tabletop review proves true is promoted to durable deterministic regression owned by the affected product ticket whose behavior it constrains, gated on real-route UI coverage: the promoted test must traverse the real public entry point to a visible/public outcome, never an implementation-level substitute.

A promoted Layer-1 regression test is still subject to the route-proof tripwire above — promotion never exempts it from that boundary: a test that fakes, directly calls, or bypasses the classifier/router is not route proof merely because its assertion started life as a Layer-1 invariant. Composition applies exactly as it does to every other regression test in this file.

**Layer 2 — bounded to ticket boundaries and official semantic smoke.** Semantic judgment (rubric-based LLM-judge review) runs ONLY at ticket boundaries — UAT rounds gated by the Phase 3 semantic-verdict consumption gate — and at the official semantic smoke, a bounded category of deliberately infrequent, hand-run checks; it is not a named scenario, fixture, or CI lane, and this ticket does not create one. There is no continuous LLM-judge CI suite, cron, scheduled job, or per-commit gate, and none may be added under this policy — binding exclusion #7.

**Layer 3 — stays semantic by default.** A Layer-3 continuation is a semantic-round obligation: its correctness is judged, not string-matched. Deterministic regression is added only for replayable fixed-copy or replayable typed continuation cases: fixed UI copy that never varies, or a typed continuation the router returns as a structured shape rather than free text. Any continuation whose proof depends on model phrasing stays semantic and is never converted into a deterministic regression test.

**Capture-replay freezes mechanisms, not ideal sentences.** A replay fixture may pin a deterministic mechanism — a typed continuation, fixed UI copy, or a request/response shape — but must never pin a model's preferred phrasing as the expected answer. Doing so would recreate the exact-string false-fail that this three-layer method exists to reject, and that the Phase 2 tabletop case (b) explicitly overturned.

---

## (1) Available today: provider-seam capture/replay

### Option 5: Create an E2E regression test

Use when a stable journey needs durable automated proof.

1. Read `.jswarm/e2e-manifest.json` and `docs/dev-guide.structured-evidence.md`.
2. Search existing tests/catalog first. Upgrade existing coverage when possible.
3. Use deterministic state: managed test data cluster, or test-local setup that creates equivalent state every run.
4. Write the test under the project-approved regression/primary E2E path.
5. Use structured evidence helpers when available.
6. Do not mock backend APIs in acceptance/regression E2E. If mocking is necessary, classify it as UI contract testing, not acceptance proof.
7. Run through the project wrapper: preferred `jswarm/agent-e2e.sh verify <test_file>`; never raw `npx playwright` when a wrapper exists.
8. Copy or record evidence using the project evidence command.

Reference: `docs/dev-guide.e2e-testing.md` and `docs/dev-guide.structured-evidence.md`.

### Option 6: Promote Live Show UAT to E2E regression

Use after live-show passes and the flow should become durable.

1. Read the passing UAT report and `TICKET-XXX.uat-scenario-steps.md` (legacy `uat-test.md`).
2. Identify the stable path, assertions, data setup, and evidence names.
3. Convert ad hoc live-show data to deterministic setup or a managed cluster.
4. Create or update a headless Playwright regression spec.
5. Preserve traceability: UAT scenario IDs · A/C IDs · report/evidence links · `Relevant UAT` catalog metadata when the project uses a catalog.
6. Run through the project wrapper, usually `jswarm/agent-e2e.sh verify <test_file>`.
7. Update the plan, UAT report, and catalog/architecture scenario docs as applicable.

If the scenario's behavior depends on LLM calls, pair this with Option 7 below for the deterministic replay layer.

Reference: `docs/testing/live-show-mode-guide.md`, `docs/dev-guide.e2e-testing.md`, and `docs/dev-guide.structured-evidence.md`.

### Option 7: Capture/refresh deterministic LLM-replay fixtures (NEW; "available today" half only, until T4)

Coordinate LLM-traffic capture/replay regression promotion for an accepted UAT scenario whose behavior depends on LLM calls. This is a `jTestEngineer` procedure: `jTestEngineer` owns the working test, coordinates a bounded team, and does not return until replay is green and deterministic.

**When to use:** a UAT scenario is accepted or ready for promotion and the durable regression must replay the same LLM traffic later.

> **HARD PRECONDITION — DO NOT INVOKE** (owner directive 2026-06-29): a COMPLETE `TICKET-XXX.uat-scenarios.md` scenario AND its associated executable `TICKET-XXX.uat-scenario-steps.md` (legacy `uat-test.md`) scripts MUST both exist and be **owner-signed-off** before this option runs. No signed-off UAT pair ⇒ stay PARKED — no capture baseline, no replay authoring, no fixture promotion. Authoring/committing this option does NOT authorize using it; the signed-off scenario+scripts are the spec the guard encodes, so they are locked first. If invoked without that pair, STOP and report the missing/unratified input rather than capturing against an unratified scenario.

> **TIMING WEIGHTING — SHAPE-COUPLING** (owner directive 2026-07-03): this guard captures a STATIC LLM request/response SHAPE and replays it. If a later bugfix changes that shape, the guard must be UPDATED, and the non-deterministic→deterministic conversion is painful/expensive. Weight the timing before building: only build when the scenario's LLM/pipeline shape is STABLE — not while the ticket is mid-churn with imminent fixes (e.g. an in-flight broad-impact/choke-point fix that will alter the merge/build/terminal shape the capture encodes). Building mid-churn means paying the conversion cost again on the next shape change. **Prefer:** build guards for a complete scenario or a whole multi-leg e2e chain at a *stabilization point* (after the broad-impact fixes land + owner sign-off), not per-fix mid-flight — a chain-level guard also catches cross-leg regressions. If asked to build mid-churn, surface the shape-churn cost and recommend deferring to the stabilization point unless the owner accepts the re-capture cost.

Offer points: `/jPrecompact` full-mode promotion review gate, when a UAT row is proposed for Done and needs regression promotion · `/jClose` Block 4, when Regression/E2E policy requires a deterministic promotion artifact · Options 5/6 above cover E2E-shaped promotion; use this option for the missing LLM-traffic replay layer.

Start-here mechanism: whole-pipeline `WORKFLOW_MOCK_AGENTS=capture`. The ordered-ledger harness (§2 below) is a future-fidelity upgrade note only; do not make it the required path.

**Required inputs** (do not start without all of these): ticket key `TICKET-XXX` · accepted UAT scenario id from `TICKET-XXX.uat-scenarios.md` · executable `TICKET-XXX.uat-scenario-steps.md` (legacy `uat-test.md`) phase/steps/condition branches for that scenario · aligned GWT/use-case text from `TICKET-XXX.uat-scenarios.md` · original bug report(s), owner UAT notes, screenshots, runtime logs, or debug artifacts that explain the regression risk · current target locations for the replay spec path, proposal fixture path, promoted fixture path, `.jswarm/plans/TICKET-XXX/TICKET-XXX.regression-test.md` result doc, `tests/catalog/regression-inventory.json` row, and A/C-to-Test matrix row.

Traceability must stay explicit: `uat-scenario id -> uat-test phase/branches -> replay test -> fixture -> matrix/inventory rows`.

**Delegation model.** This is not a policy exception. Common delegation policy permits allowlisted L1 to L2 delegation within the depth cap; L2 must never spawn L3. `jTestEngineer` coordinates exactly this bounded team when needed. Owner-facing role labels are `jTestEngineer -> {jQATester, runner, oracle}`:

| Required role label | Dispatch target | Responsibility | Must not do |
| --- | --- | --- | --- |
| `jQATester` | `jQATester` | Execute the accepted `uat-scenario-steps.md` (legacy `uat-test.md`) browser script as the capture baseline with `WORKFLOW_MOCK_AGENTS=capture`; return session id, evidence, fixture path, and scenario verdict. | Author the replay test, promote fixtures silently, or explore beyond the script. |
| `runner` | `jVerifier` by default; `jMicroCliSmoke` only for an exact command-output rerun | Execute the emitted replay test under `WORKFLOW_MOCK_AGENTS=instant` or `realistic`; run twice; prove deterministic; return commands, exit codes, evidence paths, fixture hashes, and log findings. | Change source/test files or recapture fixtures. |
| `oracle` | `jOracle` | Answer technical capture/replay questions and classify infra uncertainty. | Own delivery or expand scope. |

Every L2 dispatch MUST include this exact guard: `You may NOT dispatch, spawn, or delegate to ANY other agent — perform this entire task YOURSELF.` If an L2 identifies work requiring another agent, it reports that need back to `jTestEngineer` with evidence; it does not spawn.

Authoring separation: the `jTestEngineer` running this option must not personally run live builds, browser captures, live LLM captures, Docker restarts/recreates, or replay executions while authoring the regression. It authors/wires the test and delegates capture to `jQATester` and replay to the runner role with the no-L3 guard above. If the owner explicitly asks `jTestEngineer` to execute a command directly in a later handoff, treat that as a separate execution task and still obey safe/quiescent-stack and no-in-flight constraints.

**Procedure**

1. *Resolve scope and branches.* Read the accepted scenario id, GWT, and `uat-scenario-steps.md` (legacy `uat-test.md`) procedure. Enumerate every documented branch/condition in the UAT script that belongs to this regression, including failure/blocked branches if the original bug was branch-specific. Translate each branch into a replay assertion target: user-visible UI outcome, public API-visible state, generated model content, or structured evidence field. Verify the original bug/debug artifacts are covered; if a branch is intentionally deferred, record the explicit deferral in the result doc and matrix row.

2. *Prepare replay test and fixture names.* Use stable, ticket-scoped names:
   ```text
   tests/e2e/primary/pe2e_TICKETXXX_<scenario_slug>_llm_replay.spec.ts
   tests/fixtures/llm-replay/proposals/TICKET-XXX-<scenario_slug>.json
   tests/fixtures/llm-replay/TICKET-XXX-<scenario_slug>.json
   .jswarm/plans/TICKET-XXX/TICKET-XXX.regression-test.md
   ```
   For non-browser integration regression, use the approved integration path and model the fixture use on the exemplar tests: `tests/unit/test_mock_agents_provider.py` (fixture keying, capture wrapper, fail-loud replay semantics) · `tests/integration/test_phase2_pipeline.py` (workflow/integration style) · `tests/integration/test_has497_append_replay_determinism.py` (replay determinism and replay-key stability assertions). Acceptance-style browser regressions go in `tests/e2e/primary/` and must not use `page.route()`.

3. *Capture whole-pipeline LLM traffic.* Capture mode wraps the real provider chain and writes `{key, request, response}` entries to `MOCK_AGENTS_FIXTURE_PATH`. It still needs the normal real-LLM environment. The backend process must actually start with the capture env; setting env only on the Playwright wrapper is not enough if the Docker service is already running with different env. Use a dedicated/quiescent stack. Never recreate or restart the worker while an extraction/build/Temporal activity is in flight. If no safe window exists, stop and ask the orchestrator/owner for one.
   ```bash
   cd <worktree root>
   export DEV_AUTH_EMAIL="<dev-email>"
   export REMOTE_USER="<dev-email>"
   export WORKFLOW_MOCK_AGENTS=capture
   export MOCK_AGENTS_FIXTURE_PATH=tests/fixtures/llm-replay/proposals/TICKET-XXX-<scenario_slug>.json
   # Only in a safe/quiescent capture window so the backend reads the env above.
   # jInfra is the single infrastructure authority — recreate through it, not a hand-rolled docker command.
   # There is no `jInfra` on PATH: invoke by absolute path (jInfra SKILL.md § How to invoke).
   "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" "${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/jinfra_cli.py" \
     --docker-recreate --services hai-simulator --confirm
   jswarm/agent-e2e.sh verify tests/e2e/primary/pe2e_TICKETXXX_<scenario_slug>_llm_replay.spec.ts
   ```
   Capture guardrails: do not use the default `tests/fixtures/llm-replay/smoke-fixture.json` for ticket promotion · capture into a proposal fixture first; do not capture directly into the committed canonical fixture unless the owner explicitly asks for a refresh of that fixture · record scenario id, session id, command, fixture path, entry count, and decisive evidence paths · capture replaces entries with the same deterministic key in the target file — this is why proposal-first promotion is mandatory.

4. *Promote the fixture explicitly.* Never silently overwrite a promoted fixture. Inspect the proposal fixture entry count and a diff against any existing canonical fixture. Confirm the proposal covers every expected LLM call for the accepted scenario and its documented branches. Promote only by explicit action:
   ```bash
   PROPOSAL=tests/fixtures/llm-replay/proposals/TICKET-XXX-<scenario_slug>.json
   CANONICAL=tests/fixtures/llm-replay/TICKET-XXX-<scenario_slug>.json
   if test -e "$CANONICAL"; then
     diff -u "$CANONICAL" "$PROPOSAL" || true
     echo "STOP: canonical fixture exists. Record the diff review and explicit PROMOTE approval before copying."
     exit 2
   fi
   cp "$PROPOSAL" "$CANONICAL"
   ```
   If the canonical fixture exists and the diff is understood, run a separate owner-visible promotion command after recording the approval (`# Approved promotion: <who/when/why>` then `cp "$PROPOSAL" "$CANONICAL"`). Do not overwrite by habit; require an explicit approval or equivalent owner-visible promotion decision before copying.

5. *Run deterministic replay.* Replay mode substitutes `MockAgentsProvider` and never falls back to live LLMs. A fixture miss raises fail-loud `KeyError` and logs `llm_replay_miss`; fix by repairing the test/fixture, not by allowing live calls.
   ```bash
   cd <worktree root>
   export DEV_AUTH_EMAIL="<dev-email>"
   export REMOTE_USER="<dev-email>"
   export WORKFLOW_MOCK_AGENTS=instant
   export MOCK_AGENTS_FIXTURE_PATH=tests/fixtures/llm-replay/TICKET-XXX-<scenario_slug>.json
   # jInfra is the single infrastructure authority — recreate through it, not a hand-rolled docker command.
   # There is no `jInfra` on PATH: invoke by absolute path (jInfra SKILL.md § How to invoke).
   "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" "${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/jinfra_cli.py" \
     --docker-recreate --services hai-simulator --confirm
   shasum -a 256 "$MOCK_AGENTS_FIXTURE_PATH"
   jswarm/agent-e2e.sh verify tests/e2e/primary/pe2e_TICKETXXX_<scenario_slug>_llm_replay.spec.ts
   shasum -a 256 "$MOCK_AGENTS_FIXTURE_PATH"
   jswarm/agent-e2e.sh verify tests/e2e/primary/pe2e_TICKETXXX_<scenario_slug>_llm_replay.spec.ts
   shasum -a 256 "$MOCK_AGENTS_FIXTURE_PATH"
   ```
   Determinism proof requires: two consecutive green replay runs · same promoted fixture path · identical fixture SHA-256 before, between, and after replay runs · no `llm_replay_miss` · no `llm_replay_capture` during replay · no fixture diff · no live provider fallback · evidence bundle(s) or command output proving the scenario assertions passed. Use `WORKFLOW_MOCK_AGENTS=realistic` only when latency fidelity is the test target. `instant` is the normal fast regression mode. `WORKFLOW_MOCK_AGENTS=true` is an alias for `instant`.

6. *Wire traceability.* Add or update all of these before returning: replay test file under the approved regression path · promoted fixture under `tests/fixtures/llm-replay/` · `tests/catalog/regression-inventory.json` row with `uat_refs` populated, for example:
   ```json
   {
     "id": "PE2E-TICKETXXX-<SCENARIO>-LLM-REPLAY",
     "executable_path": "tests/e2e/primary/pe2e_TICKETXXX_<scenario_slug>_llm_replay.spec.ts",
     "title": "Accepted <scenario id> replay regression with captured LLM traffic.",
     "status": "active",
     "uat_refs": ["<scenario id>"],
     "nfr_refs": []
   }
   ```
   Regenerated `tests/TEST_CATALOG.md` if catalog tooling is in scope for the ticket · A/C-to-Test matrix row whose evidence cell cites the replay test, fixture, two replay runs, and result doc · `.jswarm/plans/TICKET-XXX/TICKET-XXX.regression-test.md` from `assets/regression-test.template.md`. Matrix refresh command shape after row edits:
   ```bash
   ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python \
     ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_ticket/cli.py \
     --ticket TICKET-XXX \
     --repo-root <worktree root> \
     --sections rebuild-rows,reconcile-status,count
   ```

7. *Definition of done.* `jTestEngineer` MUST NOT return until the runner confirms all items below: a green deterministic replay test exists · the promoted fixture exists and is the fixture used by replay · two replay runs pass with identical fixture hashes and no replay miss/capture logs · `tests/catalog/regression-inventory.json` has a row with `uat_refs` populated · the A/C-to-Test matrix cites the replay test and fixture evidence · `TICKET-XXX.regression-test.md` is complete from the template · the trace chain is explicit: `uat-scenario -> uat-test branches -> replay test -> fixture -> evidence` · any omitted branch has an explicit deferral and owner/orchestrator-visible rationale.

**Why this ownership model exists.** The orchestrator should not be the hidden owner of a half-promoted regression. `jTestEngineer` owns the working deliverable, while bounded L2 delegation lets `jQATester`, runner, and oracle contribute without blowing the context limit or forcing the orchestrator through repeated capture/replay/debug cycles.

Steps 3-6 above are the complete successor procedure — the standalone `test-regression` skill's separate command-shape cheatsheet carried no content beyond what is already written here and was not recreated. The project-local `test-regression` skill directory (including its cheatsheet and template assets) was fully removed on 2026-07-14 per owner ruling (controlled-config masters remain the source of truth); only a minimal redirect stub remains at `.claude/skills/test-regression/SKILL.md` pointing back to this file.

### Option 8: Run existing E2E/regression verification

1. Read `.jswarm/e2e-manifest.json`.
2. Identify the exact test file or suite.
3. Run wrapper preflight if available.
4. Run the declared wrapper command:
   - `jswarm/agent-e2e.sh test <test_file>` for standard headless E2E
   - `jswarm/agent-e2e.sh verify <test_file>` for structured evidence
   - project equivalent from manifest if different
5. Report command, result, evidence path, trace/video path, and failures.
6. For long-running regression runs, apply the live execution follow-along protocol (`execution-protocol.md`): tail relevant logs/telemetry, summarize meaningful backend/runtime progress, call out failures prominently, and continue into analysis/remediation options if the run stops.

---

## (2) T4-owned: DO NOT INVOKE until T4 lands

T4 ships the tooling layer this redesign's SKILL/doc layer does not: permutation manifest · call-slot registry · staleness CI · matrix-row generator · demo mode · runtime toggle.

**Future-fidelity upgrade note: ordered ledger harness.** `tests/harness/llm_complete_capture_replay.py` is the future upgrade path for stricter complete-boundary capture/replay around `run_candidate_gate`. It supports proposal-only capture, explicit `promote_fixture`, and failure taxonomy such as `FIXTURE_STALE`, `COVERAGE_GAP`, `OUT_OF_ORDER`, `REPLAY_REGRESSION`, and `NFR_THRESHOLD_FAIL`. Do not require the ledger harness for Option 7 above — start with whole-pipeline `WORKFLOW_MOCK_AGENTS=capture`; upgrade to the ledger harness only when the ticket needs stricter prompt-order/staleness fidelity than provider-level replay gives.

Do not invoke any T4-owned capability (permutation manifest, call-slot registry, staleness CI, matrix-row generator, demo mode, runtime toggle, or the ordered ledger harness) until T4 lands. Option 7 above is the "available today" half only.

---

## (3) Maintenance rule active now

Any ticket that changes workflow/pipeline behavior updates the AFFECTED deterministic capture-replay regression tests and fixtures as part of its own Definition of Done (re-capture via Option 7 above) — per the project's Ticket Closure Standards ("Capture-replay regression maintenance"). A stale-fixture flag caused by a ticket's change is a blocking finding on THAT ticket — never deferred debt parked on the regression-suite ticket.

Pre-T4, record the affected-fixture set (or an explicit deferral with rationale) in the ticket's plan/close-ticket artifacts. T4's staleness CI (§2 above) makes this mechanical once it lands.
