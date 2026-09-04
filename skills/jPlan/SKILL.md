---
name: jPlan
description: Initialize a work item (tracker key or local slug), technical design spec, and plan file (full or --lite/briefing mode).
---

# /jPlan: router

## Safety contract (destructive skill, agent-invocable)

- **Default is read-only / no-write.** Invoked with no args (or `--help`/`status`), this skill only inspects and reports; it performs NO write, apply, push, delete, remote, or trim.
- **Confirm before any mutation.** Before any state-changing mode, the invoker (human or agent) must obtain explicit confirmation, or run an approved preview/dry-run first and act only on that approved plan.
- **Agents are NOT locked out** (no `disable-model-invocation`); this contract (not frontmatter) is what gates writes, so the read-only path is freely usable and a mutation requires approval.

Invoke as **`/jPlan`**, **`/jPlan --lite`**, **`/jPlan rapid-vibe-ui`**, or with clear Lite / rapid-vibe intent ("lite", "briefing only", "ticket + context only", or owner-approved rapid existing-UI refinement).

| # | Output | Required when | Owner |
|---|---|---|---|
| 1 | **Work item identified (tracker key or slug), local state written** | Always | `mode-lite.md` Lite Step 2 or `operations.md` Step 2A |
| 2 | **Session renamed** | Always unless Feature-orchestrator carve-out | `mode-lite.md` Lite Step 2 or `operations.md` Step 2B |
| 3 | **Technical design spec written** | Standard + Deep | `step-4-spec.md` |
| 4 | **Plan file written** | Always | `mode-lite.md` or `step-5-assemble-plan.md` |
| 5 | **Ticket-local UAT scenario extract written** | Automated UAT + user-visible behavior; never Lite | `operations.md` |
| 6 | **Executable UAT doc written** | Automated UAT + user-visible behavior; never Lite | `operations.md` |
| 7 | **UAT round initialized in a non-ready state** | UAT round tracking: on | `step-5-assemble-plan.md` |
| 8 | **Bug evidence pack deposited** (`<plans-root>/TICKET-XXX/design-inputs/`; see Output 8 detail below) | **Every DEFECT ticket, ALL modes incl. Lite** (Bug issue type, or Story/Task chartering a bug/defect follow-up from triage, review finding, or UAT report) | `mode-lite.md` Lite Step 2a (Lite) / this gate (full modes) |

**If you finish without all required outputs for the chosen mode, you have FAILED.** Do not ask user about next steps until required outputs are done.

> **Output 8 detail:** frozen COPIES (or source-headed excerpts) of diagnosis/triage material + symptom artifacts (screenshots, log slices, session projections) + an indexed `README.md` with **reproduction anchors** (sessions/commands/ids, trigger condition, code anchors with verbatim failure signatures, environment state) and **boundary notes** mirroring the plan's non-goals. Pointers to origin-ticket evidence are allowed IN ADDITION, never INSTEAD: origin folders evaporate at close (worktree teardown, log rotation, context compaction). Commit plan + pack together. Full-mode plans additionally cite the pack in the plan's Required Reading. Non-defect tickets skip with no annotation.

## Global Command Project Localization

This is a global/shared command. Project-local rendered command files are not the source of truth.

Before executing any non-smoke instruction in this command:

1. Determine the active project root from the current working directory.
2. If `.claude/project-command-injections.yaml` exists, read `managed_commands.jPlan.md`. The resolver temporarily accepts the compatibility declaration during migration, but `jPlan.md` is the canonical logical key.
3. For each configured anchor whose marker appears in this command, read its `snippet_path` or inline `content` and treat that content as if it replaced the matching `<!-- inject:... -->` marker.
4. If a configured anchor is `required: true` but the marker is missing, the snippet is missing, or the snippet is empty, stop with `LOCALIZATION ERROR` and explain the missing anchor.
5. If no project manifest exists, continue with the global command body as-is.
6. **Parameter defaults (fail-open).** If `managed_commands.jPlan.md` declares an optional `parameters:` mapping, use those values as the **defaults** for the matching Step 1 questions (`default_depth`, `default_execution_team`, `default_review_tier`, `default_arch_tier`, `default_automated_uat`, `default_e2e_policy`, `default_test_data_strategy`). They pre-fill the questions; the developer still overrides every answer per ticket. A missing, unknown, or malformed `parameters:` block NEVER blocks; fall back to the global defaults stated in Step 1. Resolve deterministically (and fail-open) with:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/devops_command_injection.py \
  inspect-command --project-root <PROJECT_ROOT> --command-name jPlan.md
```

and read the `parameters` object from the JSON output. (The reader is fail-open: a broken manifest yields an empty `parameters` object, i.e. global defaults; it never aborts `/jPlan`.)

If invoked with `--localization-smoke`, do only the localization pass, print the project root, each configured anchor name, whether it resolved, the first non-empty line of each resolved snippet, AND the resolved `parameters:` defaults (or `none, global defaults` when absent); then stop without running the normal command workflow.

### Project-local required reading

<!-- inject:project-required-reading -->

<!-- inject:project-advisories -->

## Dispatch and Step 1

- **Lite:** load **only** `mode-lite.md`; skip `operations.md` and Steps 3 and 4.
- **Quick:** load `operations.md`, `step-3-context.md`, and `step-5-assemble-plan.md`.
- **Standard / Deep:** load `operations.md`, `step-3-context.md`, `step-4-spec.md`, and `step-5-assemble-plan.md`.
- **Step 0a, 2A/2B, 6, UAT/NFR read-integration, output tables:** `operations.md`.
- **Step 3:** `step-3-context.md`; **Step 4:** `step-4-spec.md`; **Step 5:** `step-5-assemble-plan.md`.
- **Q6 rubric:** read `agent-team-rubric.md` in full before answering; preserve its frozen enum literals and team-line grammar.
- **Background, auto-context, ColGREP lifecycle, rationale:** load `operations.md` and `reference.rationale.md` only when relevant.

Ask condensed Q1–Q10 together in the full-mode owner: Q1–Q4 are feature/fix, ticket, issue type, and scope/high-level A/C; Feature requires Standard or Deep. Before the standard selector, detect an explicit owner request for `rapid-vibe-ui`: it is valid only for existing-UI Story/Task/Bug refinement whose concrete defect list will emerge through immediate browser feedback; genuine feature, architecture, backend/workflow, migration, shared-contract, or security/compliance work is ineligible. When valid, compile Quick and `--with rapid-vibe-ui`; do not invoke H/M/L merely because user-visible behavior would otherwise raise the ceremony tier. Otherwise invoke `jPlan.ceremony-selector`; it owns Q5–Q9 compilation, including worktree, NFR, precompact cadence, and orchestrator context. Alongside Q7 (Automated UAT needed?), ask the opt-in `UAT round tracking: yes|no` question; an affirmative answer selects the `--with`-only `uat-round-tracking` addon pattern, translated to the assembler flag in Step 5. Q10 is the once-only `rollup_comments: on|off` opt-in. **Chatbot testing protocol injection:** whenever the ticket's tested surface includes non-deterministic behavior judged semantically (LLM-judgment UAT: chatbot conversations, advisor answers, generated content), the plan MUST bind the **chatbot testing protocol** (`~/.claude/skills/jTest/UAT_SEMANTIC_VERDICT_CONSUMPTION_GATE.md`): (a) name it in the plan's `Testing strategy` header line; (b) every semantic scenario the ticket authors or amends carries a scenario-owned `judgment_rubric` in the canonical UAT inventory at the slice that lands the behavior; authoring a semantic scenario without one is an incomplete deliverable (jQATester returns `BLOCKED: missing scenario judgment contract`); (c) the executable UAT doc binds `UAT_SEMANTIC_JUDGMENT_DISPATCH_TEMPLATE.md` (UAT-D3) for those scenarios. Deterministic-only tickets skip this with no annotation. Detection cue: any A/C or scenario clause whose verification requires judging free-form model/LLM output rather than asserting exact state. Do not re-ask compiled answers unless required detail remains unresolved. Persist selector decision state in the plan.

Q6 is rubric-governed, not pre-fill-governed: project defaults are lean starting points only. Do not pre-default `critic-xhigh` or `architect-master`; apply the rubric and record the result using the literal header grammar in `agent-team-rubric.md`.

<!-- inject:project-worktree-policy -->

<!-- inject:project-step4d-rule-discipline -->

**Wait for user response before proceeding.**

## Content ownership

- `mode-lite.md`: self-contained Lite trigger, questions, Step 2 ticket/rename mechanics, briefing shape, and Step 6 comment/summary.
- `operations.md`: full-mode questions, operational workflow, Q10 rollups, and output mechanics.
- `step-3-context.md`: discovery and UAT preflight.
- `step-4-spec.md`: design-spec authoring and advisor gate.
- `step-5-assemble-plan.md`: v1 template selection and plan authoring.
- `agent-team-rubric.md`: Q6 escalation contract.
- `reference.rationale.md`: invocation-optional rationale and lifecycle reference.
- `pattern.rapid-vibe-ui.md`: owner-approved existing-UI rapid-feedback trigger, living-contract record, browser acceptance loop, and close reconciliation.
- `CHANGELOG.md`: historical changes.

## Next

Once the plan file exists and Step 6's outputs are done, run `/jGo` in this project's agent session to execute it.
