"""Canonical three-branch browser-transport fallback ladder (AC-1/AC-3).

Pure selection logic: no I/O, no subprocess, no network. A hung probe is the
failure class this module exists to avoid, so anything that can block lives in
:mod:`jswarm.qa_transport.probes` behind hard timeouts, and this module only
ranks their already-collected results.
"""

from __future__ import annotations

from dataclasses import dataclass

TRANSPORT_DIRECT_MCP = "direct-mcp"
TRANSPORT_HARNESS_CLI = "harness-cli"
TRANSPORT_API_FALLBACK = "api-fallback"

VISUAL_TESTED = "TESTED"
VISUAL_NOT_TESTED = "NOT TESTED"


@dataclass(frozen=True)
class TransportProbe:
    """Pre-collected transport facts.

    ``direct_mcp_exposed_in_session`` is orchestrator-observed (the live tool
    list), never derived from config files: config presence != session
    exposure. There is deliberately no
    ``configured`` input.
    """

    direct_mcp_exposed_in_session: bool
    direct_mcp_healthy: bool
    harness_cli_healthy: bool


@dataclass(frozen=True)
class LadderDecision:
    transport: str
    visual_coverage: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "transport": self.transport,
            "visual_coverage": self.visual_coverage,
            "reason": self.reason,
        }


def select_transport(probe: TransportProbe) -> LadderDecision:
    """Walk the canonical ladder and return the selected transport.

    Never raises for any combination of probe values; every decision carries a
    deterministic reason string so dispatch records stay auditable
    (NFR-246-002-OBSERVABILITY).
    """
    if probe.direct_mcp_exposed_in_session and probe.direct_mcp_healthy:
        return LadderDecision(
            transport=TRANSPORT_DIRECT_MCP,
            visual_coverage=VISUAL_TESTED,
            reason="branch 1: direct MCP browser tools are exposed in-session and healthy",
        )
    if probe.harness_cli_healthy:
        if not probe.direct_mcp_exposed_in_session:
            blocked = "not exposed in-session (config presence is not exposure)"
        else:
            blocked = "exposed but failed its liveness probe"
        return LadderDecision(
            transport=TRANSPORT_HARNESS_CLI,
            visual_coverage=VISUAL_TESTED,
            reason=f"branch 2: direct MCP {blocked}; harness-CLI preflight healthy (MCP-free real UI)",
        )
    return LadderDecision(
        transport=TRANSPORT_API_FALLBACK,
        visual_coverage=VISUAL_NOT_TESTED,
        reason=(
            "branch 3: no live browser transport (direct MCP unavailable/unhealthy and "
            "harness-CLI preflight failed); API variant only — visual coverage must be "
            "recorded as NOT TESTED and never represented as browser/visual UAT"
        ),
    )
