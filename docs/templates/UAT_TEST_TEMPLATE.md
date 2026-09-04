# [TICKET-XXX]: UAT Test Runbook

**Last Updated:** YYYY-MM-DD

This runbook is the executable verifier for the ticket's UAT scenarios: a human- or
agent-driven walkthrough that proves the user-visible behavior described in
`TICKET-XXX.uat-scenarios.md` actually holds against the running app. `jQATester`
executes it during `/jGo`; `jswarm/uat-scenarios/scaffold_uat_tests.py` scaffolds it
(and inserts one `## Scenario <id>:` section per authored scenario, immediately above
`## Pass Criteria`) from this template.

### Execution strategy

State the driver and mode this runbook uses:

- **Driver:** Playwright MCP (headed/foreground) or headed Playwright. Chrome DevTools
  MCP only for a documented Chrome/CDP diagnostic exception.
- **Mode:** developer-watched Live Show. Headless automation is never claimed as
  Live Show UAT.
- **Preconditions:** app running, any fixtures/seed data, and environment variables
  the scenarios below assume.

<!-- Scenario sections land here, one per authored scenario -->

## Pass Criteria

The runbook passes when every `## Scenario <id>:` section above is observed exactly as
described in its **Expected** table, with no unexplained deviation. A single unexplained
FAIL fails the runbook; document any accepted deviation with its rationale before marking
PASS.
