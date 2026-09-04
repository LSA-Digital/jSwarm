
# Changelog

| Date | Author | Change |
|---|---|---|
| 2026-08-02 | Owner-directed | Added the evidence-triggered `preissued-rulings@1` addon, generalized from another project's near-miss incident: matched T1-T5 execution rulings only, incident provenance, hostile-lazy anti-evasion clauses, and a mandatory bounded escalation budget. |
| 2026-07-16 | Owner directive | Added orchestrator model and effort routing to the core plan header, Lite briefing skeleton, and Feature governance pattern, including phase-boundary reminders; field pattern proven in hai-sim-engine. `template.CORE.md`'s source `template_version` remains `1`; consumers validate generated-template versions, so the core-change rule regenerates and bumps all generated templates. |
| 2026-05-25 | Sisyphus | Added Security & Compliance baseline-risk capture for /jPlan, including applicability, before probability/impact, baseline controls context, safe-rationale rules, and fail-open writer guidance. |
| 2026-05-22 | -        | **All new plan writes go to `.jswarm/plans/`.** Plan master: `.jswarm/plans/TICKET-XXX.plan.<descriptive>.md`. Spec / UAT scenarios / UAT test doc / integr-fixes / pe2e-fixes: `.jswarm/plans/TICKET-XXX/TICKET-XXX.<type>.<descriptive>.md`. Oracle research output: `.jswarm/plans/TICKET-XXX/TICKET-XXX.research.<descriptive>.md` (Phase 3c finalizes the `research-output` skill repoint). Updated: output inventory table, Lite-mode requirements, Step 5 write paths, template provenance transform, Feature defect tracker paths, Lite/Full Jira-comment templates, work-initialized summary tables, post-compact resume. Template source files at `docs/plans/plan-templates/` remain (not plan instances). Legacy `docs/plans/` paths kept readable for pre-2026-05-22 tickets; `/jGo` `/jClose` `/jMerge` `/jPrecompact` resolve dual-path on load. Per canonical doc `docs/agent-system/agent-write-permissions.md`. |
| 2026-05-11 | Sisyphus | **Lifecycle + leanness pass.** Plan templates now default `status: ACTIVE` in frontmatter (per `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/merge/state-machine.md`). Extracted Step 3/4/5 procedural detail to `${JSWARM_HOME:-$HOME/dev/jswarm}/docs/jplan/step-by-step.md`. Reduced 679 → ~500 lines. State machine documented in template comments. |
| 2026-05-05 | - | Added Lite mode (`--lite` / "briefing only"): ticket + briefing plan only; skips spec, research, UAT, phases, `/jGo` scaffolding. |
| 2026-05-04 | GPT-5.5 | Clarified UAT routing: `qa-tester-uat` default; multi-stream requires orchestrator-supplied monitor/API/evidence context. |
| 2026-05-02 | GPT-5.5 | Updated testing policy prompts and plan header for full testing lifecycle. |
| 2026-04-21 | Sisyphus | Added UAT Pre-flight Check (Step 3A.5): 5-point feasibility gate before executable UAT doc. (Retro finding.) |
| 2026-04-21 | Sisyphus | Replaced inline checkpoint protocol with `/jPrecompact` reference. |
| 2026-04-21 | Sisyphus | Added Per-Phase UAT Gate advisory. |
| 2026-04-19 | GPT-5.4 | Replaced "poll every turn" with notification-driven checks. |
| 2026-04-19 | GPT-5.4 | Added inert HTML-comment injection anchors. |
| 2026-04-18 | Sisyphus | Removed git worktree branch isolation. Added auto-context checkpoint protocol. |
| 2026-04-15 | Claude | Added ticket-local UAT working-slice workflow. |
| 2026-04-15 | Claude | Added qa-tester-uat for post-implementation UAT verification. |
| 2026-04-14 | Claude | Worktree for branch isolation. |
| 2026-04-06 | Claude | Q6: execution agent team pattern (Full TDD vs Orchestrator+Critic). |
| 2026-04-04 | Claude | Added /jFix guidance. |
| 2026-05-08 | - | Retro filenames per `close-ticket`. |
| 2026-04-03 | Claude | Updated retro references. |
| 2026-04-01 | Claude | Added Tool Failure Reports section. |
| 2026-03-24 | Claude | Renamed to "technical design spec". |
| 2026-03-23 | Claude | Added technical design spec workflow (Step 4). |
| 2026-03-17 | Sisyphus | Added mandatory E2E prerequisite search (Search 2). |
| 2026-03-02 | Sisyphus | Added research output persistence for depth=3. |
| 2026-01-29 | Sisyphus | REWRITE: 332→80 lines. Hard output gate. |
| 2026-01-24 | Sisyphus | Replaced complexity scoring with user choice. |
| 2026-01-02 | - | Initial version. |

| 2026-08-05 | Fable orchestrator (owner-directed) | **Output 8: bug evidence pack for EVERY defect ticket, all modes incl. Lite**: new required output binding docs/templates/BUG_EVIDENCE_PACK_TEMPLATE.md (frozen copies + indexed README with reproduction anchors + boundary notes, committed with the plan); Lite Step 2a added in mode-lite.md; rationale: origin-ticket evidence evaporates at close (worktree teardown, log rotation, compaction). |
