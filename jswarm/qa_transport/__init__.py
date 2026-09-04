"""P1a — browser-QA transport probes and canonical fallback ladder.

The ladder (single formulation — every other doc defers here):

  1. direct MCP exposed IN-SESSION and healthy -> direct MCP
  2. otherwise harness-CLI preflight healthy   -> project e2e harness CLI (MCP-free real UI)
  3. otherwise                                 -> API variant, visual coverage NOT TESTED

Selection (`ladder.select_transport`) is a pure function over pre-collected
probe results and can never hang. Probe collection (`probes`) runs the real
subprocess/network checks under hard timeouts. In-session MCP tool exposure
can only be observed by the orchestrator (config presence != session
exposure), so it is an input, never inferred from config.
"""

from jswarm.qa_transport.ladder import LadderDecision, TransportProbe, select_transport
from jswarm.qa_transport.probes import ProbeResult, probe_harness_cli

__all__ = [
    "LadderDecision",
    "ProbeResult",
    "TransportProbe",
    "probe_harness_cli",
    "select_transport",
]
