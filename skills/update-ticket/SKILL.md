---
name: update-ticket
description: Reliable single entry point for lifecycle plan-maintenance (migrate, rebuild matrix rows, reconcile status, count, audit) plus the interactive promotion-review gate, as a thin facade over jswarm/update_ticket/cli.py.
---

# Update Ticket

## Safety contract (destructive skill, agent-invocable)

- **Default is read-only / no-write.** Invoked with no args (or `--help`/`status`), this skill only inspects and reports; it performs NO write, apply, push, delete, remote, or trim.
- **Confirm before any mutation.** Before any state-changing mode, the invoker (human or agent) must obtain explicit confirmation — or run an approved preview/dry-run first and act only on that approved plan.
- **Agents are NOT locked out** (no `disable-model-invocation`); this contract — not frontmatter — is what gates writes.


**Last Updated:** 2026-06-23

A reliable single entry point for lifecycle plan-maintenance. The deterministic sections are a
thin **facade** over `jswarm/update_ticket/cli.py` (run via `.venv/bin/python`), which itself
orchestrates the existing engine CLIs as subprocesses — no logic is reimplemented. This
skill owns the ONE piece that cannot be a CLI: the interactive **promotion-review gate**.

Every deterministic refresh section is **fail-open** (exit 0; a missing/malformed plan is
reported and skipped, never aborting the checkpoint) and **idempotent** (re-run = no diff). The
`audit`/`lint` sections are **fail-loud** (exit 1 on BLOCK). The promotion gate is **fail-safe**
(⏸ Hold unless evidence is positively confirmed) and **never auto-promotes**.

---

## When to Use This Skill

Load this skill when a lifecycle command delegates plan-maintenance to `/update-ticket`,
specifically when an **interactive promotion-review gate** is required:

- `/jPrecompact TICKET` **full** mode (`--preset precompact-full --interactive`) — the gate runs
  first, then the deterministic refresh chain.
- Any time you need to run the promotion-review gate by preset before reconciling matrix counts.

The deterministic-only callers (`/jClose` `close-refresh`, `/jGo` `implement-gate`,
`/jPlan` `new-work-lint`) need **no** gate — they invoke `jswarm/update_ticket/cli.py`
directly and never load this gate.

---

## Step 1: Resolve preset → section set

| Preset | Sections (canonical order) |
|---|---|
| `precompact-full` | `promotion-gate, migrate, rebuild-rows, reconcile-status, count` |
| `close-refresh` | `migrate, rebuild-rows, reconcile-status, count, audit` (stage `close`) |
| `implement-gate` | `audit` (stage `implement`) |
| `new-work-lint` | `lint` |

Section vocabulary: `promotion-gate` (skill-only, interactive), `apply-promotions`, `migrate`,
`rebuild-rows`, `reconcile-status`, `count`, `bind-session` (available, not in any preset),
`audit`, `lint`. Only `precompact-full` contains `promotion-gate`.

---

## Step 2: The interactive promotion-review gate (blocking)

Run only when the resolved section set includes `promotion-gate`.

1. **Scan** the plan's count-bearing matrices (`## A/C-to-NFR Traceability Matrix`,
   `## UAT-Scenario Traceability Matrix`, `## A/C-to-Test Traceability Matrix`) and the
   `## Acceptance Criteria` checkboxes, alongside the ticket-local evidence docs
   (`*.uat-test.md`, `*.nfr-test.md`, `*.regression-test.md`).
2. **Recommend** ONE block: for each candidate row/checkbox, `promote` only when evidence
   positively confirms it; otherwise ⏸ **Hold** (fail-safe — never recommend promote on absent
   or ambiguous evidence).
3. **Developer decision (blocking):** approve all / approve selected / deny / request changes /
   abort. Record each candidate's decision as `approved`, `denied`, or `held`.
4. **Write the ticket-local promotion-decision JSON** (the frozen contract below). This artifact
   — not prose — is what the deterministic apply step consumes.
5. Run the `apply-promotions` CLI section against that artifact; it applies ONLY the
   **approved-and-still-matching** flips (atomic — any stale/ambiguous/invalid approved entry
   means NO writes), then re-run the deterministic refresh/count sections.

### Frozen promotion-decision JSON contract

Path: `.jswarm/plans/KEY/KEY.update-ticket.promotions.<UTC>.json`.

Top-level (all required): `schema_version`, `ticket`, `plan_path`, `plan_sha256_before`
(sha256 of the plan the decisions were made against — stale-guards concurrent edits),
`created_at`, `created_by`, `decision_scope`, `candidates[]`.

Each candidate: `kind` ∈ {`uat-row`, `nfr-row`, `test-row`, `ac-checkbox`};
`recommendation` ∈ {`promote`, `hold`}; `decision` ∈ {`approved`, `denied`, `held`};
`expected_marker` (the CURRENT marker the decision was made against — rows ∈
{`🔴 Backlogged`, `🟠 Drafted`, `🟡 Ready`}; ac-checkbox = `[ ]`); `target_marker`
(rows = `🟢 Done`; ac-checkbox = `[x]` — no other target is allowed); `row_sha256`
(sha256 of the current row/line, to defeat moved/reordered/duplicate/edited rows); and
kind-specific locators — uat-row: `matrix_heading` + `scenario_id`; nfr-row: `matrix_heading`
+ `nfr_ref`; test-row: `(ac, test_path, test_name)`; ac-checkbox: `ac_label`.

`apply-promotions` treats the decision JSON as a **frozen authorization token** and aborts with a
machine-readable failure summary and **writes nothing** on: invalid JSON; a missing field; a bad
enum; a `schema_version` other than the current one; an artifact `ticket` ≠ the `--ticket`; a
`plan_path` that does not resolve to the selected plan; a `matrix_heading` that is not the canonical
heading for the candidate's kind; a duplicate locator across *any* candidates (even one `denied`/`held`
+ one `approved` on the same row); a `plan_sha256_before` mismatch; a not-found / ambiguous locator;
a stale `row_sha256`; or a stale `expected_marker`. Promotion decisions are **new-location-only**
(`.jswarm/plans/KEY.plan.*.md`); legacy `docs/plans/` tickets are out of scope. There is no
partial apply: if any approved candidate fails, re-run this gate (re-scan and write a fresh
decision). `denied`/`held` candidates are never flipped.

---

## Step 3: Deterministic refresh + count

After the gate, the wrapper runs the deterministic chain in canonical order
(`migrate → rebuild-rows → reconcile-status → count`) so the HUD's `nfr_complete`/`uat_complete`
counts reflect accepted promotions. These sections are fail-open exit 0; `count` reuses
`jswarm/update_plan/cli.py --apply` (see the [`update-plan`](../update-plan/SKILL.md) skill for
the count/hygiene contract).

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/update_ticket/cli.py --ticket KEY --repo-root . \
  --sections apply-promotions,migrate,rebuild-rows,reconcile-status,count \
  --promotions-file .jswarm/plans/KEY/KEY.update-ticket.promotions.<UTC>.json
```

The wrapper is common-owned and invoked by absolute common path (it resolves its engines from
`common`, not the target project); `--repo-root` names the project whose plan is maintained.

---

## Step 4: Non-interactive safety (no auto-promote)

A preset containing `promotion-gate` (i.e. `precompact-full`) requires `--interactive` **and** a
real blocking approval channel (TTY). Invoked without `--interactive`, or with `--interactive`
but no TTY/blocking approval channel, the CLI prints `NON_INTERACTIVE_APPROVAL_UNAVAILABLE` and
**exits 2 before any write** — it never auto-promotes. This is the hard safety floor: a checkpoint
that cannot obtain a real approval must stop, not guess.

---

## Maintenance

| Date | Author | Change |
|------|--------|--------|
| 2026-06-23 | Phase 4, /jGo | Initial `update-ticket` skill: preset→section resolution, the interactive promotion-review gate (fail-safe Hold), the frozen ticket-local promotion-decision JSON contract, deterministic `apply-promotions` + refresh/count, and non-interactive exit-2 safety. Facade over `jswarm/update_ticket/cli.py`; `.venv/bin/python` only. |
