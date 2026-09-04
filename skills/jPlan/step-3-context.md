
# Step 3: Search existing tests + gather codebase context

## Step 3: Search existing tests + gather codebase context

Skip entirely in Lite mode. Optional: if the user already pasted file paths or stack traces in their initial message, reference them in the Lite plan under **Context** without running mandatory ColGREP or catalog searches.

### 3.1: Architecture-level test/scenario docs first

Start with the project's canonical scenario inventory. Architecture-owned docs are the stable contract; legacy catalogs are compatibility input.

```bash
ls docs/architecture/*test-scenarios*.md 2>/dev/null
ls docs/architecture/*uat-scenarios*.md 2>/dev/null
```

If found: read the relevant feature inventories and extract the scenarios this ticket modifies, extends, or adds.

### 3.2: Compatibility search (`tests/TEST_CATALOG.md` projects only)

If the project has `tests/TEST_CATALOG.md`:

**Search 1: existing tests for this ticket's functionality:**
```bash
grep -i "<feature_keywords>" tests/TEST_CATALOG.md | head -30
```

**Search 2: E2E tests where this ticket is a PREREQUISITE:**

Step A: find your ticket in the Feature-to-Ticket mapping:
```bash
grep "TICKET-XXX" tests/TEST_CATALOG.md | head -10
```

Step B: search for E2E tests listing that Feature as a prerequisite:
```bash
grep -B2 "F-XX" tests/TEST_CATALOG.md | grep -E "^### E2E-|Prerequisites:" | head -20
```

For each match found:
1. Copy the test ID, spec file, steps, expected outcomes, and screenshot evidence from TEST_CATALOG.md
2. Add an A/C line: `AC-E2E-X: E2E-XX test passes with screenshot evidence verified via look_at`
3. Add the test to the A/C-to-Test Traceability Matrix

**Dedup rule:** If the spec file already exists in the project's e2e test directory, mark it as `Upgrade` (add new assertions). If not, mark as `New`.

### 3.3: Plain test directory fallback (no TEST_CATALOG.md)

```bash
grep -ri "<feature_keywords>" tests/ --include="*.py" --include="*.spec.ts" | head -30
```

### 3.4: Codebase context via ColGREP

```
colgrep_search({"query": "<A/C keywords>", "index": "<project>", "top_k": 10})
```

### 3A: Locate the official UAT scenario inventory (when `Automated UAT: yes`)

```bash
ls docs/plans/*uat-scenarios.md 2>/dev/null
ls docs/architecture/*uat-scenarios*.md 2>/dev/null
grep -ril "UAT Scenario Inventory\|UAT Scenarios\|Phase Mapping + PE2E Traceability" docs/ 2>/dev/null | head -20
```

- **If found:** read the most relevant feature-level inventory; extract only the scenarios this ticket modifies, extends, or adds.
- **If not found:** create the ticket-local extracted scenario file anyway; flag inventory creation/promotion as follow-up work.

Outputs of Step 3A:
- `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenarios.md` created from `UAT_SCENARIO_EXTRACT_TEMPLATE.md`
- `.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-test.md` created from `UAT_TEST_TEMPLATE.md`

The scenario extract is the ticket's living UAT slice; it may evolve during `/jGo`. The executable UAT doc can start as a draft, but it must already contain the impacted scenarios, environment details, execution strategy, exact runtime contract (if needed), evidence/report path, and a concrete handoff packet for `jQATester` before `/jGo` delegates.

**Per-Phase UAT expectations (MANDATORY when Automated UAT applies):** If the ticket has UI/E2E/user-journey impact and `Automated UAT: yes`, `TICKET-XXX.uat-test.md` must define automated UAT expectations for each applicable phase, not just a single end-of-ticket verification. Each phase section lists the UAT scenarios that validate that phase's user-visible deliverables, referencing `docs/architecture/architecture.uat-scenarios.md` for the master inventory. During `/jGo`, a phase without UAT expectations BLOCKS until they're added. Backend-only, schema-only, migration-only, infrastructure-only, or tooling-only phases record `Automated UAT: no (no UI/E2E impact)` and use lower-level proof.

### Step 3A.5: UAT Pre-flight 5-question check (MANDATORY when `Automated UAT: yes`)

Skip entirely when `Automated UAT: no (no UI/E2E impact)`.

For each scenario in the extracted UAT file, answer:

| # | Check | Pass → | Fail → |
|---|---|---|---|
| 1 | Does a user-triggerable UI path exist **today** for the action this scenario tests? | Proceed | Mark scenario "backend-only" or defer to the UX ticket that adds the affordance |
| 2 | Does the state transition produce a **visible rendered diff** (not ARIA-only/hidden-label)? | Screenshots OK | Use a11y-tree/DOM text captures; do not use screenshots as evidence |
| 3 | Is any metric being claimed as evidence **actually populated** at the session stage UAT reaches? | Proceed | Verify via API `GET` before writing prose; or drop the metric claim |
| 4 | Does the scenario depend on a **timing window** (mid-dispatch, pre-commit)? If window ≤30s on dev, does an integration test already cover it? | Proceed | Push timing assertion to integration test; browser UAT only corroborates path is active |
| 5 | Is the journey **multi-stream** (browse + logs + API-read + evidence-write in parallel)? | Plan orchestrator-supplied monitor/API/evidence context for `jQATester` | Single-stream → simpler `jQATester` handoff |

Record results in `TICKET-XXX.uat-scenarios.md` under a `## UAT Pre-flight` section. Any scenario that fails checks 1–4 **MUST be re-scoped** before the executable UAT doc is written; do not carry known-infeasible scenarios into `TICKET-XXX.uat-test.md`.


## Bounded overview first: only when the project adopted the scenarios engine (WS3)

Before the architecture-first ColGREP trawl, check whether this project has adopted the UAT-scenario engine. The **adoption signal** is deterministic: the project manages `code-overview.md` (a `managed_commands.code-overview.md` entry with a `code-overview-scenario-source` anchor / a resolvable canonical scenarios JSON). Verify with:

```
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/devops_command_injection.py \
  inspect-command --project-root <PROJECT_ROOT> --command-name code-overview.md
```

Adoption requires **both** `mode: managed` **and** `code-overview-scenario-source` present in the emitted `anchors` list. A project can manage `code-overview.md` for `code-overview-required-reading` / `code-overview-smoke-config` alone, with **no** canonical scenarios JSON; that is **not** adoption. Anything short of both ⇒ not adopted.

- **If adopted →** run `/code-overview <scenario-grouping>` **first** as the bounded, UX-aligned overview pass (scenario-grouping framing, ≤12 / hard-20 tool budget, ≥3× speedup benchmark vs an unbounded trawl). Then let ColGREP enrich the already-bounded pathway with the Step 3A/C-keyword searches in this module.
- **If NOT adopted →** skip `/code-overview` entirely and use the architecture-first ColGREP search in this module. **Never advise `/code-overview` where no canonical scenarios JSON exists**: it depends on the engine and would mislead on a non-adopting project.

An adopting project is the live first adopter (manifest wires `code-overview.md` → `docs/architecture/architecture.uat-scenarios.json`).
