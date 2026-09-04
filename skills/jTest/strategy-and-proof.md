# /jTest: Strategy and non-UAT proof (options 1, 9)

Read `execution-protocol.md` first (MASTER INVARIANT in `SKILL.md`).

## Option 1: Decide testing strategy for a ticket

Use during `/jPlan`, `/jGo`, or plan repair.

1. Read the A/C and ticket scope.
2. Classify impact:
   - UI/user journey/E2E impact → Live Show UAT likely required.
   - Stable user journey that must persist → regression E2E/PE2E required or explicitly deferred.
   - Backend/API/workflow only → unit/integration/API proof; no `jQATester` unless there is a user journey.
   - Migration/schema/tooling only → migration/schema/CLI/smoke proof; no placeholder UAT.
3. Update the plan header fields:
   - `Testing strategy`
   - `Automated UAT`
   - `E2E policy`
   - `Test data strategy`
4. If Automated UAT is yes, ensure ticket-local scenario and executable UAT docs exist.

Test-class ladder: uat-class (scenarios + `uat-scenario-steps.md`, formerly `uat-test.md`) = flexible, rapid-change, drives QA agents/humans step-by-step, never regression proof; regression class = higher-effort deterministic (see `regression.md`); pre-scenarios/tests = legacy/invalid → archive. E2E chains string together scenarios that already own their steps; no separate E2E step-script assets.

Reference: `docs/devops-practices.md`, `docs/devops-testing-master.md` (detailed testing governance map), and `docs/templates/UAT_TEST_TEMPLATE.md`.

---

## Option 9: Prove backend/API/CLI-only work without UAT

Do not call `jQATester` just to satisfy a checkbox.

1. Confirm there is no UI/E2E/user-journey impact.
2. Set or keep `Automated UAT: no` (no UI/E2E impact).
3. Prove with the correct tier:
   - unit tests for local logic
   - integration tests for composed services/workflows
   - API/contract checks for public endpoints
   - migration/schema checks for database work
   - CLI/smoke checks for tooling/runtime changes
4. Record exact commands and results in the plan.

## Risk-triggered fixture floor

For a qualifying shared-contract change, turn only each **declared risk** into a fixture obligation. An activated row must resolve to exactly one of: an **existing exact assertion** that already discriminates the risk, an **evidence-backed N/A**, or an **explicit mismatch/block** that stops implementation. An **undeclared risk** creates **no fixture obligation**.

Choose the **smallest set of cells that distinguishes** the declared risk. The cells below are discriminators to select from, not a demand to run every combination.

| Declared risk | Candidate discriminating cells |
|---|---|
| selection / authority / cardinality | zero / one / many only as needed; stale-first / current-authoritative-later for arbitrary-first selection; wrong identity |
| presence / carrier | absent / valid / present-null; partial keys; malformed scalar/list/mapping; selected-plus-ambient collision |
| replay / marker | marker on/off; old/new history; accepted/refused and stamped/unstamped ingress; final replay |
| durable record / wake | sibling reader/writer; both consume sites; record present/absent; actual wake versus declarative owner |
| cross-await identity | resolve once; mutate/remove/upload between stages; reuse immutable bundle; identity/cardinality/presence boundary |
| exception / retry | each applicable deterministic reference family as non-retryable; a real transient operational family on the retry path |
| LLM seam | prompt vocabulary/precedence; mint/copy/omit; deterministic refusal; zero live-LLM assertions |
| route migration | selected/ambient; V1/V2; presence-only/body-required; shallow/deep/catch-up/rearm |

**Prohibit universal Cartesian expansion.** Do not multiply unrelated dimensions or activate rows merely because they exist. **Happy-path-only proof does not prove** a declared boundary risk; include the nearest counterexample that makes the wrong behavior observably different.

This fixture floor shifts discriminating checks earlier but does not replace later evidence. Existing smoke, runtime, browser, UAT, and owner-walk obligations **remain required** for their normal residual classes. Applying this rule does not create UAT assets, browser runs, or product E2E machinery.

Reference: `docs/devops-practices.md`.
