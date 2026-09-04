"""Phase 4/13b — regenerate this host's live agent files from the YAML.

This is the Claude-side regeneration helper. It assembles each live agent
file ``<slug>.md`` in this host's agents directory
(`jswarm.host.claude_code.ClaudeCodeHost.agents_dir`) from three
version-controlled sources in the ``common`` repo:

  1. ``docs/_CONTROLLED_CONFIG/subagent-context-profiles.yaml`` — the
     ``agents:`` block. Supplies the frontmatter mirror (``frontmatter:``) and
     the Claude routing chain (``claude.model`` / ``claude.effort`` /
     ``claude.fallbacks``).
  2. ``docs/_CONTROLLED_CONFIG/agent-bodies/<slug>.body.md`` — the prompt
     body (extracted by ``jswarm/extract_agent_bodies.py``, Phase 13a).

Each agent file is assembled as::

    ---
    name: <slug>
    description: "<frontmatter.description>"
    model: <derived from claude.model>
    [category: <frontmatter.category>]      # 9 quick/writing micro agents
    [cost: <frontmatter.cost>]
    ---

    <ROUTE:...>          # derived from the claude routing chain

    <body file content>

The ``model:`` line is *derived*, never stored: Anthropic models are emitted
as the bare base id (``claude-opus-4-7``) because the Claude Code harness
appends ``[1m]`` and ``-high``/``-max`` suffixes produce non-existent SKUs
(Phase 0). Non-Anthropic models are emitted ``provider/model``.

The ``<ROUTE:...>`` tag is derived from the claude chain: the primary hop is
``claude.{model,effort}``; the fallbacks are ``claude.fallbacks``. Anthropic
effort is rendered as a ``-suffix`` (``claude-opus-4-7-high``); other
providers use a ``(level)`` parenthetical (``gpt-5.5(low)``).

Disabled agents (``claude.model: disabled`` — currently only ``critic-xhigh``)
get no prefix ROUTE tag: their body file carries the ``[AGENT TEMPORARILY
DISABLED]`` marker and a commented-out ROUTE verbatim.

Why this exists (AC-13 — upgrade survival): OMC upgrades have historically
overwritten every agent file in this host's agents directory with packaged
defaults. After this helper lands, that wipeout is recoverable — every
customization lives in the ``common`` repo (YAML + body files) and
``--apply`` restores the live files.

Modes:
  --report  (default) : assemble every agent, diff against the live files,
                        print a status table. Writes nothing.
  --check             : same as --report but exit 1 if any agent differs
                        from its canonical regeneration. This is the AC-5
                        idempotency gate.
  --apply             : write every assembled agent file to the agents dir.

Idempotency (AC-5): after one ``--apply``, ``--check`` exits 0 and a second
``--apply`` changes nothing. The first ``--apply`` also performs a one-time
normalization of ``flash-tasker`` (its ROUTE tag moves from mid-body to the
canonical prefix position) — see Phase 4/13b.

Usage:
  ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python jswarm/regenerate_claude_agents.py
  ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python jswarm/regenerate_claude_agents.py --check
  ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python jswarm/regenerate_claude_agents.py --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Support running this file directly (``python jswarm/regenerate_claude_agents.py``,
# per the module docstring) as well as ``-m``/package-relative import from tests —
# the direct-script form has ``jswarm/`` (not the repo root) as ``sys.path[0]``, so
# ``jswarm.subagent_context_budget`` (and ``jswarm.host``) would otherwise fail to
# resolve.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jswarm.host import current as _current_host  # noqa: E402
from jswarm.subagent_context_budget.registry import alias_status, is_legacy_slug  # noqa: E402
DEFAULT_PROFILES = _REPO_ROOT / "docs" / "_CONTROLLED_CONFIG" / "subagent-context-profiles.yaml"
DEFAULT_BODIES = _REPO_ROOT / "docs" / "_CONTROLLED_CONFIG" / "agent-bodies"
DEFAULT_AGENTS_DIR = _current_host().agents_dir()

# Sentinel value in `claude.model` marking an agent that is intentionally
# disabled. Such agents emit no prefix ROUTE tag.
DISABLED_MODEL = "disabled"

# Day-0 machines have no jAgentProxy, so no GPT/GLM routing. The
# claude-only profile maps each canonical core to a Claude Code model alias.
CLAUDE_ONLY_MODELS: dict[str, str] = {
    "jArchitect": "opus", "jPlanner": "opus", "jOracle": "opus", "jCritic": "opus",
    "jDebugger": "opus", "jSecurityReviewer": "opus",
    "jCoder": "sonnet", "jTestEngineer": "sonnet", "jUIDesigner": "sonnet",
    "jVerifier": "sonnet", "jResearcher": "sonnet", "jQATester": "sonnet",
    "jExplorer": "haiku", "jOps": "haiku", "jMicroBuildFixer": "haiku",
    "jMicroCliSmoke": "haiku", "jWriter": "haiku",
}
CLAUDE_ONLY_BY_CATEGORY: dict[str, str] = {
    "architect-planner": "opus", "reviewer": "opus",
    "executor": "sonnet", "focused-research": "sonnet",
    "micro-leaf": "haiku",
}
PROVIDERS = ("yaml", "claude-only")


def claude_only_model(slug: str, agent: dict[str, Any]) -> str:
    return CLAUDE_ONLY_MODELS.get(slug) or CLAUDE_ONLY_BY_CATEGORY.get(str(agent.get("category", "")), "sonnet")


# A short, MODEL-AGNOSTIC reliability clause injected into EVERY
# generated agent body from this single source of truth. It guarantees the
# core context-survival rules are present in every dispatch even when the
# `subagent-environment` skill is not preloaded (the skill carries the full
# mechanics). Keep it short — detail belongs in the skill, not here.
RELIABILITY_PREAMBLE = (
    "<!-- universal reliability clause -->\n"
    "**Reliability (any model):** Checkpoint your primary output to disk as soon "
    "as it is drafted **when file writes are permitted for your role** — don't "
    "hold the only copy in context. If writing reports/artifacts is prohibited "
    "for your role, instead return an early partial result plus a resume pointer "
    "for the orchestrator to transcribe or an editable stub. If you approach your "
    "context limit, STOP and return a partial result plus a resume pointer (what "
    "you produced, the next step) instead of failing with 0 output. Read narrow "
    "line-ranges, not whole large files (>~1500 lines), and never re-read files "
    "already in context. If a focused task crosses ~100 tool-uses or ~20–25 "
    "minutes, checkpoint and hand back to be re-scoped. Full mechanics: the "
    "`subagent-environment` skill."
)

# A short, universal write-governance clause injected into EVERY
# generated agent body alongside the reliability clause. It tells
# the model — even when the `subagent-environment` skill is not preloaded —
# WHERE to write (zone hierarchy), HOW to name files (regex pointers), and
# WHICH master to consult for the per-agent tier label. The per-agent
# `Write mode:` annotation lives in each `<slug>.body.md` (Path C hybrid);
# this clause is the model-agnostic global half.
WRITE_GOVERNANCE_CLAUSE = (
    "<!-- universal write-governance clause -->\n"
    "**Write governance (any agent):** "
    "Write mode tier is one of `read-only` | `docs-write` | `source-write`; "
    "see this agent's per-body `Write mode:` bullet for its tier. "
    "Destination hierarchy: ticket-coupled plans/specs/research/reviews → "
    "`.jswarm/plans/KEY-XXX.plan.<desc>.md` (master) + `.jswarm/plans/KEY-XXX/` "
    "(artifacts). Ad-hoc / handoff / non-ticket → `.jswarm/agent-reports/`. "
    "See `docs/agent-system/agent-write-permissions.md` for the canonical policy. "
    "Filename conventions: plans `KEY-XXX.<type>.<descriptive>.md` "
    "(master uses `.plan.`, artifacts use `.specs.`, `.research.`, `.retro.`, etc.); "
    "agent-reports `KEY-XXX.<agent>.<descriptive>.YYYYMMDD.md`. "
    "Hooks `pretool-jswarm-plans-gate.py` and `pretool-agent-report-filename-gate.py` "
    "enforce filename + folder structure on writes to those zones."
)

DELIVERABLE_DURABILITY_CLAUSE = (
    "<!-- advisor deliverable-durability contract -->\n"
    "**Deliverable durability (advisory/review/research roles):** Writing your ONE "
    "markdown deliverable file IS explicitly authorized and EXPECTED for your role. "
    "Nothing prohibits it: a `read-only`/`docs-write` posture, the reliability-clause "
    '"if writes are prohibited" caveat, and the orchestrator-facing ".md-write '
    'transcription tax" recovery note all mean "do not modify SOURCE files" — NOT '
    '"do not write your deliverable". Resolve any apparent read-only/write conflict '
    "in FAVOR of writing this one deliverable file. That deliverable (review, "
    "analysis, design, assessment, research report) is your PRODUCT; your final chat "
    "message is only a POINTER to it. Because a long routing-model stream can hit a "
    "per-turn `max_tokens` continuation wall and truncate mid-run, write so that whatever lands on disk is ALWAYS usable: "
    "(1) write your ACTUAL FINDINGS to the file EARLY and INCREMENTALLY, and put the "
    "VERDICT / most important conclusions FIRST, so even a truncated file carries the "
    "answer; "
    "(2) keep the file COMPLETE-AS-OF-EACH-WRITE — every write must leave real, usable "
    "content for the work done so far. Do NOT write an empty/placeholder/\"IN PROGRESS\" "
    "SCAFFOLD intending to fill it later: a long run can truncate before those fill-turns "
    "reach disk, leaving an unusable scaffold that still LOOKS done (the Mode-2 "
    "incomplete-artifact failure — the single worst outcome). Equally, never defer the "
    "whole composition to one final turn — that final write is exactly what a drop severs; "
    "(3) end your chat with a one-line banner: `DELIVERABLE: <path> — "
    "<verdict/one-line summary>`. Only if a write tool genuinely errors, say so "
    "explicitly and return an early partial — never withhold the product as chat by choice."
)

SOURCE_WRITE_NO_SCAFFOLD_CLAUSE = (
    "<!-- source-write no-stop-at-scaffold guard -->\n"
    "**Source-write completion guard:** If you modify source/config/tests/docs, do not "
    "stop at scaffold-only, placeholder-only, or TODO-only output. Complete the "
    "requested implementation slice, verify it, and report any explicit blocker "
    "instead of returning an incomplete scaffold as success."
)

DELIVERABLE_DURABILITY_AGENTS = frozenset(
    {
        "jArchitect",
        "jCritic",
        "jDebugger",
        "jOracle",
        "jPlanner",
        "jResearcher",
        "jSecurityReviewer",
        "jUIDesigner",
        "jVerifier",
        "jWriter",
    }
)


# --------------------------------------------------------------------------- #
# Routing derivation
# --------------------------------------------------------------------------- #


def infer_provider(model: str) -> str:
    """Infer the provider for a model id.

    A namespaced id (``lsadigital/gemma-...``, ``zai/glm-5.2``) already carries
    its provider as the first path segment. Bare stems keep the historical
    prefix rules. Mirrors ``infer_provider`` in ``migrate_agent_assignment_to_v2.py``.
    """
    if "/" in model:
        return model.split("/", 1)[0]
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3"):
        return "openai"
    if model.startswith("glm-"):
        return "zai"
    if model.startswith("gemini-"):
        return "gemini"
    return "unknown"


def namespaced_model_id(provider: str, model: str) -> str:
    """Join ``provider/model`` without doubling an already-matching prefix.

    ``lsadigital`` + ``lsadigital/gemma-...`` stays the official id.
    ``openai`` + ``lsadigital/gemma-...`` keeps the OpenCode transport prefix
    so remap can become ``cliproxy/lsadigital/gemma-...``.
    """
    model_s = (model or "").strip()
    provider_s = (provider or "").strip()
    if provider_s and model_s.startswith(f"{provider_s}/"):
        return model_s
    if provider_s:
        return f"{provider_s}/{model_s}"
    return model_s


def render_hop(provider: str, model: str, effort: str | None) -> str:
    """Render one routing hop as a ROUTE-tag token.

    Anthropic effort is a model-name ``-suffix``; every other provider uses a
    ``(level)`` parenthetical (the CLIProxyAPI / Phase 8 convention). A hop
    with no effort is just the namespaced model id.
    """
    official = namespaced_model_id(provider, model)
    if not effort:
        return official
    if provider == "anthropic":
        return f"{official}-{effort}"
    return f"{official}({effort})"


def build_model_line(claude: dict[str, Any], opencode: dict[str, Any]) -> str:
    """Derive the frontmatter ``model:`` value from the claude block.

    Anthropic → bare base id (the harness appends ``[1m]``; effort suffixes
    produce non-existent SKUs). An already-namespaced official id is kept as-is
    (``lsadigital/gemma-...``). Other bare stems become ``provider/model``.
    A disabled agent mirrors its OpenCode disabled marker (``zai/disabled``).
    """
    model = str(claude.get("model", ""))
    if model == DISABLED_MODEL:
        provider = str((opencode.get("primary") or {}).get("provider", "zai"))
        return f"{provider}/{DISABLED_MODEL}"
    if "/" in model:
        return model
    provider = infer_provider(model)
    if provider == "anthropic":
        return model
    return namespaced_model_id(provider, model)


_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
_MODEL_ROUTE_CLAUSE_RE = re.compile(r"(?:^|\s)Model route:\s*(.+?)(?=\.\s+Routing:|\s+Routing:|$)")


def _normalized_hop(provider: Any, model: Any, effort: Any) -> tuple[str, str, str | None] | None:
    """Normalize one source route hop, returning ``None`` when it is incomplete."""
    normalized_model = str(model or "").strip().lower()
    if not normalized_model or normalized_model == DISABLED_MODEL:
        return None
    normalized_provider = str(provider or infer_provider(normalized_model)).strip().lower()
    normalized_effort = str(effort).strip().lower() if effort else None
    return normalized_provider, normalized_model, normalized_effort or None


def build_route_hops(claude: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    """Build the one normalized, ordered route chain for a Claude assignment.

    The primary is always first, followed by valid fallbacks. Route tags,
    generated description clauses, and clause parsing all share this primitive.
    Disabled or incomplete assignments intentionally produce no hops.
    """
    primary = _normalized_hop(
        infer_provider(str(claude.get("model") or "").strip().lower()),
        claude.get("model"),
        claude.get("effort"),
    )
    if primary is None:
        return []

    hops = [primary]
    for fallback in claude.get("fallbacks") or []:
        if not isinstance(fallback, dict):
            continue
        hop = _normalized_hop(
            fallback.get("provider") or infer_provider(str(fallback.get("model") or "").strip().lower()),
            fallback.get("model"),
            fallback.get("effort"),
        )
        if hop is not None:
            hops.append(hop)
    return hops


def build_route_tag(claude: dict[str, Any]) -> str | None:
    """Derive the ``<ROUTE:...>`` tag from ``build_route_hops``."""
    hops = build_route_hops(claude)
    if not hops:
        return None
    tokens = [render_hop(*hops[0])]
    tokens.extend(f"fallback:{render_hop(*hop)}" for hop in hops[1:])
    return "<ROUTE:" + "|".join(tokens) + ">"


def build_model_route_clause(claude: dict[str, Any]) -> str | None:
    """Render the description's human-readable route from ``build_route_hops``."""
    hops = build_route_hops(claude)
    if not hops:
        return None
    return "Model route: " + " → ".join(render_hop(*hop) for hop in hops)


def parse_model_route_clause(description: str) -> list[tuple[str, str, str | None]]:
    """Decode the single generated ``Model route:`` description clause."""
    match = _MODEL_ROUTE_CLAUSE_RE.search(description)
    if not match:
        return []

    hops: list[tuple[str, str, str | None]] = []
    for token in (part.strip() for part in match.group(1).split("→")):
        provider, separator, rendered_model = token.partition("/")
        if not separator:
            return []
        effort: str | None = None
        parenthetical = re.fullmatch(r"(.+?)\(([^()]+)\)", rendered_model)
        if parenthetical:
            rendered_model, effort = parenthetical.groups()
        elif provider.strip().lower() == "anthropic":
            for candidate in _EFFORT_LEVELS:
                suffix = f"-{candidate}"
                if rendered_model.lower().endswith(suffix):
                    rendered_model = rendered_model[: -len(suffix)]
                    effort = candidate
                    break
        hop = _normalized_hop(provider, rendered_model, effort)
        if hop is None:
            return []
        hops.append(hop)
    return hops


# --------------------------------------------------------------------------- #
# Agent-file assembly
# --------------------------------------------------------------------------- #


def yaml_frontmatter_scalar(value: Any, *, default_style: str | None = None) -> str:
    """Emit one deterministic YAML scalar suitable for a frontmatter line.

    PyYAML safely escapes quotes, backslashes, colons, and table separators, but
    ``safe_dump`` for a top-level scalar may append a document terminator. The
    frontmatter builder needs exactly one scalar value after ``key:``.
    """
    dumped = yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=1_000_000,
        default_style=default_style,
    ).strip()
    if dumped.endswith("\n..."):
        dumped = dumped[:-4].rstrip()
    if dumped == "...":
        return "''"
    if "\n" in dumped:
        raise ValueError(f"frontmatter scalar must render on one line: {value!r}")
    return dumped


def yaml_frontmatter_value(value: Any) -> str:
    """Emit one deterministic ONE-LINE YAML value for a frontmatter mirror key.

    Scalars route through ``yaml_frontmatter_scalar`` (unchanged behavior).
    Sequences (e.g. the j-core ``tags`` list) render as a one-line YAML
    *flow* sequence (``[a, b, c]``) that ``yaml.safe_load`` parses back to the
    identical list — PyYAML handles any escaping/quoting. Mappings and other
    non-scalar values fall through to ``yaml_frontmatter_scalar`` (which raises
    if they cannot render on one line).
    """
    if isinstance(value, (list, tuple)):
        dumped = yaml.safe_dump(
            list(value),
            default_flow_style=True,
            sort_keys=False,
            allow_unicode=True,
            width=1_000_000,
        ).strip()
        if "\n" in dumped:
            raise ValueError(f"frontmatter sequence must render on one line: {value!r}")
        return dumped
    return yaml_frontmatter_scalar(value)


def routing_description(
    base: str,
    routing_metadata: dict[str, Any],
    claude: dict[str, Any] | None = None,
    *,
    provider: str = "yaml",
) -> str:
    """Append derived route and compact routing summaries to a base description.

    The model route comes from the canonical ``build_route_tag`` hop chain;
    role routing mirrors ``validate_routing_metadata._routing_summary``. Both
    are derived from source YAML, never a prior generated description, keeping
    regeneration deterministic and idempotent.

    The ``claude-only`` provider replaces the YAML's proxy-routed
    model with a Claude Code alias, so the derived "Model route: ..." clause
    (which always describes the YAML hop chain) would misdescribe the agent
    and leak the disallowed provider/model string. Omit it for that provider.
    """
    clauses = [str(base).strip()]
    model_route = None if provider == "claude-only" else build_model_route_clause(claude or {})
    if model_route:
        clauses.append(model_route)

    use_when = routing_metadata.get("use_when")
    do_not_use_when = routing_metadata.get("do_not_use_when")
    if isinstance(use_when, list) and isinstance(do_not_use_when, list):
        if len(use_when) >= 2 and len(do_not_use_when) >= 2:
            routing_summary = (
                f"Routing: use={use_when[0]}; {use_when[1]} "
                f"| avoid={do_not_use_when[0]}; {do_not_use_when[1]}"
            )
            if model_route:
                clauses[-1] += "."
            clauses.append(routing_summary)
    return " ".join(clauses)


def build_frontmatter(slug: str, agent: dict[str, Any], *, provider: str = "yaml") -> str:
    """Assemble the YAML frontmatter block (including the closing ``---``).

    Emission order matches the live agent files: ``name``, ``description``,
    ``model``, ``effort`` (when claude.effort is non-null), then any
    remaining mirror keys (``category`` / ``cost``).

    The ``effort`` line is load-bearing — the Claude Code harness honors
    per-agent ``effort: low|medium|high|xhigh|max`` in frontmatter and
    maps it to Anthropic ``thinking.budget_tokens``, overriding the
    session-global ``effortLevel`` for that sub-agent dispatch
    (see the effort-level subagent research notes).
    Emitted only when ``claude.effort`` is non-null in the YAML — null
    leaves the agent at session-default effort.
    """
    fm = agent.get("frontmatter") or {}
    if "description" not in fm:
        raise ValueError(f"{slug}: agents.{slug}.frontmatter.description is missing")
    claude = agent.get("claude") or {}
    opencode = agent.get("opencode") or {}
    effort = claude.get("effort")

    lines = ["---", f"name: {yaml_frontmatter_scalar(slug)}"]
    desc = routing_description(
        fm["description"],
        agent.get("routing_metadata") or {},
        claude,
        provider=provider,
    )
    lines.append(f"description: {yaml_frontmatter_scalar(desc, default_style='\"')}")
    if provider == "claude-only":
        model_line = claude_only_model(slug, agent)
    else:
        model_line = build_model_line(claude, opencode)
    lines.append(f"model: {yaml_frontmatter_scalar(model_line)}")
    if effort:
        lines.append(f"effort: {yaml_frontmatter_scalar(effort)}")
    for key, value in fm.items():
        # The enriched j-core blocks mirror ``name``/``model`` inside
        # ``frontmatter`` too — skip them so they are not double-emitted (they
        # are already emitted canonically above: ``name`` from the slug,
        # ``model`` from the routing chain, and ``description`` in position).
        if key in ("name", "description", "model"):
            continue
        lines.append(f"{key}: {yaml_frontmatter_value(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


_OPEN_TAG_RE = re.compile(r"(?m)^[ \t]*<[A-Za-z_][A-Za-z0-9_]*>")


def inject_reliability_preamble(body_text: str) -> str:
    """Insert ``RELIABILITY_PREAMBLE`` INSIDE the body's ``<Permissions>`` block.

    A/C 2 (settled by authoritative jAgentProxy upstream capture): the
    runtime parses ``<Agent_Prompt>`` and delivers its RECOGNIZED child elements
    (``<Permissions>``, ``<Role>``, …) verbatim, but DROPS free text/comments
    between the ``<Agent_Prompt>`` open tag and the first child — so a clause
    placed before ``<Agent_Prompt>`` (with the ``<ROUTE:>`` tag) or just inside
    ``<Agent_Prompt>`` never reaches the model. The clause therefore lives inside
    a recognized child that is delivered verbatim. ``<Permissions>`` is present in
    every body, delivered intact, and already carries operational meta-rules
    (``META-PERMISSION``, ``IF BLOCKED``), so the reliability clause fits there.

    - Insert immediately before the first ``</Permissions>``.
    - Fallbacks (no ``<Permissions>`` block): after the first opening tag, else
      prepend — so the clause is never silently lost.
    """
    if "</Permissions>" in body_text:
        return body_text.replace(
            "</Permissions>",
            "\n" + RELIABILITY_PREAMBLE + "\n  </Permissions>",
            1,
        )
    m = _OPEN_TAG_RE.search(body_text)
    if m:
        idx = m.end()
        return body_text[:idx] + "\n" + RELIABILITY_PREAMBLE + "\n" + body_text[idx:]
    return RELIABILITY_PREAMBLE + "\n\n" + body_text


def inject_write_governance(body_text: str) -> str:
    """Insert ``WRITE_GOVERNANCE_CLAUSE`` INSIDE the body's ``<Permissions>`` block.

    Path C (hybrid) — global half. Same delivery constraint as
    ``inject_reliability_preamble`` (the runtime drops free text/comments
    between the ``<Agent_Prompt>`` open tag and the first recognized child,
    so the clause must live inside a recognized child to reach the model).
    The ``<Permissions>`` block is present in every body and already carries
    operational meta-rules — write governance fits there alongside reliability.

    Idempotent: if ``WRITE_GOVERNANCE_CLAUSE`` is already present the body is
    returned unchanged. The reliability injector deliberately lacks this guard;
    the governance injector adds it so re-running ``--apply`` is
    safe even if a body file accidentally inlined the clause manually.

    - Insert immediately before the first ``</Permissions>``.
    - Fallbacks (no ``<Permissions>`` block): after the first opening tag, else
      prepend — so the clause is never silently lost.
    """
    if WRITE_GOVERNANCE_CLAUSE in body_text:
        return body_text
    if "</Permissions>" in body_text:
        return body_text.replace(
            "</Permissions>",
            "\n" + WRITE_GOVERNANCE_CLAUSE + "\n  </Permissions>",
            1,
        )
    m = _OPEN_TAG_RE.search(body_text)
    if m:
        idx = m.end()
        return body_text[:idx] + "\n" + WRITE_GOVERNANCE_CLAUSE + "\n" + body_text[idx:]
    return WRITE_GOVERNANCE_CLAUSE + "\n\n" + body_text


def inject_source_write_no_scaffold_guard(body_text: str, write_mode: str | None) -> str:
    """Insert no-stop-at-scaffold language for source-writing agents."""
    if write_mode != "source-write":
        return body_text
    if "source-write no-stop-at-scaffold guard" in body_text:
        return body_text
    if "</Permissions>" in body_text:
        return body_text.replace(
            "</Permissions>",
            "\n" + SOURCE_WRITE_NO_SCAFFOLD_CLAUSE + "\n  </Permissions>",
            1,
        )
    m = _OPEN_TAG_RE.search(body_text)
    if m:
        idx = m.end()
        return body_text[:idx] + "\n" + SOURCE_WRITE_NO_SCAFFOLD_CLAUSE + "\n" + body_text[idx:]
    return SOURCE_WRITE_NO_SCAFFOLD_CLAUSE + "\n\n" + body_text


def inject_deliverable_durability(body_text: str, slug: str) -> str:
    """Insert ``DELIVERABLE_DURABILITY_CLAUSE`` for markdown-deliverable advisors.

    AC-9 targets advisory/review/research agents whose markdown
    deliverable is the product. Placement mirrors ``inject_write_governance``:
    inside ``<Permissions>`` when present, otherwise after the first structured
    opening tag, otherwise prepended. Idempotent so repeated regeneration never
    duplicates the clause.
    """
    if slug not in DELIVERABLE_DURABILITY_AGENTS:
        return body_text
    if "advisor deliverable-durability contract" in body_text:
        return body_text
    if "</Permissions>" in body_text:
        return body_text.replace(
            "</Permissions>",
            "\n" + DELIVERABLE_DURABILITY_CLAUSE + "\n  </Permissions>",
            1,
        )
    m = _OPEN_TAG_RE.search(body_text)
    if m:
        idx = m.end()
        return body_text[:idx] + "\n" + DELIVERABLE_DURABILITY_CLAUSE + "\n" + body_text[idx:]
    return DELIVERABLE_DURABILITY_CLAUSE + "\n\n" + body_text


def assemble_agent_file(slug: str, agent: dict[str, Any], body_text: str, *, provider: str = "yaml") -> str:
    """Assemble the full content of one agent file in this host's agents
    directory (`jswarm.host.claude_code.ClaudeCodeHost.agents_dir`).

    Canonical layout: frontmatter, one blank line, the ROUTE tag, one blank
    line, the body — with the reliability preamble, the
    write-governance clause, the source-write no-scaffold guard, and
    the targeted deliverable-durability clause injected INSIDE the body's
    ``<Permissions>`` block (see ``inject_reliability_preamble``,
    ``inject_write_governance``, ``inject_source_write_no_scaffold_guard``, and
    ``inject_deliverable_durability``) so they survive to the dispatch.
    Disabled agents omit the ROUTE tag.

    Injection order: governance first (where to write), reliability second
    (when to checkpoint), source-write no-scaffold guard third, then durability
    last for targeted agents (write the deliverable early). This preserves the
    existing order and keeps fallback insertion from treating the
    ``<path>`` placeholder as a tag.
    """
    frontmatter = build_frontmatter(slug, agent, provider=provider)
    route = None if provider == "claude-only" else build_route_tag(agent.get("claude") or {})
    body_with_clauses = inject_write_governance(body_text)
    body_with_clauses = inject_reliability_preamble(body_with_clauses)
    body_with_clauses = inject_source_write_no_scaffold_guard(body_with_clauses, agent.get("write_mode"))
    body_with_clauses = inject_deliverable_durability(body_with_clauses, slug)
    if route is None:
        return frontmatter + "\n" + body_with_clauses
    return frontmatter + "\n" + route + "\n\n" + body_with_clauses


# --------------------------------------------------------------------------- #
# Regeneration / diff
# --------------------------------------------------------------------------- #


@dataclass
class RegenResult:
    """Outcome of regenerating one agent file."""

    slug: str
    status: str       # match | drift | missing-body | missing-live
    detail: str = ""


def _first_diff(a: str, b: str) -> str:
    """Describe the first byte where strings ``a`` and ``b`` differ."""
    for i, (ca, cb) in enumerate(zip(a, b)):
        if ca != cb:
            return f"byte {i}: regenerated {ca!r}, live {cb!r}"
    return f"length diff: regenerated={len(a)} live={len(b)}"


def regenerate_all(
    profiles: Path,
    bodies_dir: Path,
    agents_dir: Path,
    *,
    provider: str = "yaml",
) -> tuple[list[RegenResult], dict[str, str]]:
    """Assemble every agent file and diff it against the live state.

    Returns ``(results, assembled)`` where ``assembled[slug]`` is the
    regenerated file content for agents whose body file was found.

    hard-cut: the source YAML intentionally keeps compatibility
    ``alias``/``retired`` records (``routing_metadata.alias_redirect.status``)
    alongside the 17 ``canonical`` j-cores for legacy plan/history resolution.
    Those records are metadata only — they must never be assembled/emitted as
    runtime agent files (they would resurrect a legacy dispatch target), so
    this filters to canonical-only BEFORE assembly. A missing body file for a
    non-canonical record is therefore never reported as ``missing-body``.
    """
    data = yaml.safe_load(profiles.read_text(encoding="utf-8")) or {}
    agents = data.get("agents") or {}
    agents = {slug: body for slug, body in agents.items() if alias_status(body) == "canonical"}
    results: list[RegenResult] = []
    assembled: dict[str, str] = {}

    for slug in sorted(agents):
        body_path = bodies_dir / f"{slug}.body.md"
        if not body_path.exists():
            results.append(RegenResult(slug, "missing-body", str(body_path)))
            continue
        body_text = body_path.read_text(encoding="utf-8")
        out = assemble_agent_file(slug, agents[slug], body_text, provider=provider)
        assembled[slug] = out

        live_path = agents_dir / f"{slug}.md"
        if not live_path.exists():
            results.append(RegenResult(slug, "missing-live", str(live_path)))
            continue
        live = live_path.read_text(encoding="utf-8")
        if out == live:
            results.append(RegenResult(slug, "match"))
        else:
            results.append(RegenResult(slug, "drift", _first_diff(out, live)))
    return results, assembled


def apply_all(assembled: dict[str, str], agents_dir: Path) -> int:
    """Write every assembled agent file. Returns the count of files changed."""
    agents_dir.mkdir(parents=True, exist_ok=True)
    changed = 0
    for slug, content in sorted(assembled.items()):
        path = agents_dir / f"{slug}.md"
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            changed += 1
    return changed


def prune_legacy_agent_files(profiles: Path, agents_dir: Path) -> list[str]:
    """Delete ``<slug>.md`` for every known alias/retired slug found in ``agents_dir``.

    hard-cut: ``--apply`` must converge the runtime agents dir to
    exactly the 17 canonical j-core files. Known compatibility alias/retired
    slugs may remain in the source YAML for legacy plan/history resolution,
    but any stray runtime file left over for them (e.g. a pre-cut
    ``critic.md``/``executor.md``) must be pruned. Only the exact
    ``<slug>.md`` path is ever touched — ``.bak`` files and any other
    extension are left untouched. Returns the sorted list of slugs actually
    pruned (files that existed).

    Path-containment safety (C+): a malformed/adversarial profile
    slug (e.g. ``"../victim"`` or an absolute-path slug) must never let
    pruning escape ``agents_dir``. Every candidate slug is validated against
    the registry's legacy-slug grammar *and* the resulting path is
    double-checked for containment before any unlink. Any slug failing
    either check is skipped (fail closed) rather than unlinked.

    Detection is form-agnostic: a legacy ``<slug>.md`` entry may be a
    regular file, a symlink, or a **dangling** symlink — all three are
    runtime entries that must be pruned. ``os.path.lexists`` (not
    ``Path.exists``) is used so a dangling symlink is still detected, and
    containment is checked against the *link's own location*
    (``path.parent.resolve()``) rather than the resolved target
    (``path.resolve().parent``) — the latter would follow a symlink to its
    target and wrongly skip an externally-pointing legacy entry, or crash
    on a dangling one. ``path.unlink()`` only ever removes the directory
    entry itself (the symlink's link, never its target); an externally
    pointing legacy symlink's target is left untouched.
    """
    data = yaml.safe_load(profiles.read_text(encoding="utf-8")) or {}
    agents = data.get("agents") or {}
    agents_dir_resolved = agents_dir.resolve()
    pruned: list[str] = []
    for slug, body in agents.items():
        if alias_status(body) == "canonical":
            continue
        # 1) Slug grammar: reject anything that isn't a bare legacy slug
        # (rejects path separators, "..", absolute paths, etc.).
        if not is_legacy_slug(slug):
            continue
        path = agents_dir / f"{slug}.md"
        # 2) Containment double-check: the ENTRY itself (not its resolved
        # target) must sit directly in agents_dir with the expected
        # filename. Using path.parent.resolve() here (never
        # path.resolve().parent) means a symlink pointing outside
        # agents_dir is still recognized as an in-place legacy entry.
        if path.name != f"{slug}.md" or path.parent.resolve() != agents_dir_resolved:
            continue
        if os.path.lexists(path):
            path.unlink()
            pruned.append(slug)
    return sorted(pruned)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_report(results: list[RegenResult]) -> None:
    by_status: dict[str, list[RegenResult]] = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)
    for status in ("match", "drift", "missing-body", "missing-live"):
        bucket = by_status.get(status, [])
        if not bucket:
            continue
        print(f"  {status}: {len(bucket)}")
        if status != "match":
            for r in bucket:
                print(f"    {r.slug}: {r.detail}")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--bodies-dir", type=Path, default=DEFAULT_BODIES)
    parser.add_argument("--agents-dir", type=Path, default=DEFAULT_AGENTS_DIR)
    parser.add_argument("--check", action="store_true",
                        help="Exit 1 if any agent differs from its regeneration.")
    parser.add_argument("--apply", action="store_true",
                        help="Write every assembled agent file to the agents dir.")
    parser.add_argument("--provider", choices=PROVIDERS, default="yaml",
                        help="Model source: 'yaml' (default, routes via jAgentProxy) or "
                             "'claude-only' (Claude Code aliases, no ROUTE tag).")
    args = parser.parse_args(argv)

    results, assembled = regenerate_all(args.profiles, args.bodies_dir, args.agents_dir,
                                         provider=args.provider)
    drift = [r for r in results if r.status != "match"]

    if args.apply:
        changed = apply_all(assembled, args.agents_dir)
        pruned = prune_legacy_agent_files(args.profiles, args.agents_dir)
        print(f"regenerate-claude-agents: applied {len(assembled)} agents "
              f"→ {args.agents_dir} ({changed} changed, {len(pruned)} legacy pruned)")
        if pruned:
            print(f"  pruned legacy runtime files: {', '.join(pruned)}")
        missing = [r for r in results if r.status == "missing-body"]
        if missing:
            print(f"  WARNING: {len(missing)} agents have no body file:", file=sys.stderr)
            for r in missing:
                print(f"    {r.slug}: {r.detail}", file=sys.stderr)
            return 1
        return 0

    print(f"regenerate-claude-agents: {len(results)} agents "
          f"(profiles={args.profiles.name})")
    _print_report(results)

    if args.check:
        if drift:
            print(f"CHECK FAILED: {len(drift)} agents drift from canonical "
                  f"regeneration", file=sys.stderr)
            return 1
        print(f"CHECK OK: all {len(results)} agents match canonical regeneration")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(_main())
