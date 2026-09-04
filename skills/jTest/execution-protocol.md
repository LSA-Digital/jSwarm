# /jTest: Execution Protocol (all options)

Loaded by every `/jTest` option per the MASTER INVARIANT in `SKILL.md`. Options may ADD requirements; none may skip this protocol.

## Always load local testing context first

Before executing any option:

1. Apply the Global Command Project Localization section in `SKILL.md`. For project-specific testing rules, load `.claude/project-command-injections.yaml` and `.claude/command-injections/test-advisories.md`; do not rely on a rendered local command copy.
2. Read `.jswarm/e2e-manifest.json` when present.
3. Read project `CLAUDE.md` or `AGENTS.md` only for general runtime/environment context, not testing procedure duplication.
4. Read the ticket plan and UAT docs if they exist.
5. Identify project wrapper commands. If `jswarm/agent-e2e.sh` exists, use it for Playwright test execution instead of raw `npx playwright`.
6. Record active ports/base URLs from the project manifest or `.env`; do not guess.

Required command fields for UAT/E2E handoffs:

- UAT execution mode: `live-show-headed`, `headless-automation`, or `diagnostic-cdp`
- browser driver
- exact runner command
- exact regression command, or explicit deferral/N/A reason
- preflight command
- runtime monitor command/task or `N/A (reason)`
- relevant log/telemetry sources to follow during execution: backend/service logs, runtime monitor logs, Playwright stdout, trace/video/screenshot output, structured evidence output, and any project-specific ledger/telemetry paths from `.jswarm/e2e-manifest.json`
- report and evidence paths

If any required handoff field is missing, fix the handoff before delegating. Do not ask `jQATester` to infer it.

---

## Live execution follow-along protocol (BLOCKING)

During any Live Show UAT or long-running E2E/regression execution, the agent must actively follow the test from the user's point of view instead of sending cold process messages.

1. Before starting the runner, identify every relevant evidence stream:
   - project wrapper stdout/stderr
   - Playwright stdout, trace, video, screenshot, JSON, and HTML-report paths
   - backend/API/service logs, container logs, workflow/runtime logs, SSE/streaming logs, and domain telemetry
   - runtime monitor logs and JSONL ledgers, including /OpenCode monitor ledgers when available
2. **Mechanical preflight: backend monitor starts before the runner.** When backend/API/workflow/streaming/persistence/dispatch behavior is in scope:
   - verify loud telemetry is enabled in the project `.env` (`LOG_LEVEL=DEBUG` and/or `DEBUG=true`); if it is off, turn it on before the run
   - start a Monitor task or an equivalent line-buffered capture such as `docker logs -f <api> 2>&1 | grep --line-buffered -Ei '<named-signatures>'`
   - name a signature set that covers backend exceptions (`Traceback`, `ERROR`, `CRITICAL`) **and** provider failures (`429`, `RateLimitError`, `RouterExhaustedError`, `exhausted fallback chain`, `quota`)
   - confirm the monitor is armed, then launch the browser/test runner

   **Never run a QA/smoke/E2E build as fire-and-wait-for-pass/fail when backend behavior is in scope. Live backend telemetry is the ground truth; test-level pass/fail is a lagging, lossy proxy.** If a provider-layer signature fires, immediately use `../jDebug/runtime-probes.md`'s provider-signature recipe and `../jDebug/litellm-debugging.md`; do not duplicate or improvise the LiteLLM diagnosis inside `/jTest`.
3. While the test runs, follow along with each visible test step and pair it with backend status:
   - state the current front-end step or live-show callout being exercised
   - confirm matching backend/API/runtime activity when visible in logs
   - report useful progress signals such as session IDs, revision/stage changes, SSE events, workflow activity, contributor counts, and health checks
   - avoid bare status lines such as `still running: pid=... elapsed=...` unless no log or telemetry signal exists; if only a heartbeat is available, say what evidence is missing and what is being checked next
4. On any detected error, timeout, console/network failure, backend exception, monitor trigger, stopped test, or `Serving HTML report` signal, make a prominent callout:

   ```text
   🚨 TEST FAILURE / ERROR DETECTED
   Step: <front-end or runner step>
   Signal: <log line / monitor trigger / assertion / status>
   Impact: <what this likely means for the user-visible flow>
   Evidence: <paths and line numbers when available>
   ```

5. If the test stops or fails, do not stop at reporting the failure. Continue with the next useful actions:
   - preserve evidence paths and stop/cleanup background log captures safely
   - classify likely tier: test-data/preflight, browser/UI, API/contract, backend/runtime, workflow/Temporal, persistence/database, infrastructure, or monitor/tooling
   - inspect the immediate failure artifacts and adjacent backend telemetry
   - propose remediation options to the user, for example: rerun after state reset, debug with failing trace, implement a targeted fix, open a follow-up ticket, or accept as known/non-blocking with evidence
   - ask the user how to proceed when more than one remediation path is reasonable
6. The final report must include: command, mode, result, user-visible step coverage, backend/runtime status summary, failures or explicit `no failure detected`, evidence paths, monitor ledger/footer status, and recommended next action.
