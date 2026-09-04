"""Registry loader for model windows and inheritance profiles.

Loads:
  - docs/_CONTROLLED_CONFIG/model-windows.yaml
  - docs/_CONTROLLED_CONFIG/subagent-context-profiles.yaml

Public API:
  lookup_agent(slug, *, runtime="claude", model_windows_path=None,
               profiles_path=None) -> AgentInfo
  load_model_windows(path) -> dict[str, ModelInfo]
  load_profiles(path)      -> ProfileRegistry
  model_windows_key(provider, model, model_keys) -> str

Runtime selection (Phase 7):
  - runtime="claude" (default) resolves the Claude Code primary from the
    legacy agent_primary_model map — unchanged prior behavior.
  - runtime="opencode" resolves the OpenCode primary from the schema-v2
    agents.<slug>.opencode.primary block.
  The category band is runtime-agnostic; the two runtimes differ in
  derived_budget_tokens only when they back the agent with models of
  different context windows.

Fail-open semantics (A/C 8):
  - Unknown agent slug → defaults to `architect-planner` category, primary
    model resolved from agent_primary_model if present, otherwise window=0.
    A WARN is emitted on `LOGGER_NAME`.
  - Unknown / missing model → window=0 → derived budget falls back to
    category.max_budget_tokens; WARN.
  - runtime="opencode" against a v1-only YAML (no agents block) → window=0
    fallback with WARN, same fail-open contract as the Claude path.

CLI:
  python -m jswarm.subagent_context_budget.registry --lookup-agent <slug> \
    [--runtime claude|opencode]
"""

from __future__ import annotations

import argparse
import difflib
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

LOGGER_NAME = "subagent_context_budget.registry"
_LOG = logging.getLogger(LOGGER_NAME)

# Resolved relative to the repo root (this file lives at
# <repo>/jswarm/subagent_context_budget/registry.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MODEL_WINDOWS = _REPO_ROOT / "docs" / "_CONTROLLED_CONFIG" / "model-windows.yaml"
_DEFAULT_PROFILES = _REPO_ROOT / "docs" / "_CONTROLLED_CONFIG" / "subagent-context-profiles.yaml"

DEFAULT_CATEGORY = "architect-planner"

CANONICAL_AGENT_SLUG_RE = re.compile(r"^j[A-Z][A-Za-z0-9]*$")
LEGACY_AGENT_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
CATALOG_SLUG_CAPTURE = r"(j[A-Z][A-Za-z0-9]*|[a-z][a-z0-9_-]*)"


def is_canonical_slug(slug: str) -> bool:
    return CANONICAL_AGENT_SLUG_RE.fullmatch(slug) is not None


def is_legacy_slug(slug: str) -> bool:
    return LEGACY_AGENT_SLUG_RE.fullmatch(slug) is not None


def is_valid_agent_slug(slug: str) -> bool:
    return is_canonical_slug(slug) or is_legacy_slug(slug)


def alias_status(agent_block: dict) -> str:
    routing = agent_block.get("routing_metadata") if isinstance(agent_block, dict) else None
    alias = routing.get("alias_redirect") if isinstance(routing, dict) else None
    status = alias.get("status") if isinstance(alias, dict) else None
    if status in {"alias", "retired"}:
        return status
    return "canonical"


def is_canonical_agent(agent_block: dict) -> bool:
    return alias_status(agent_block) == "canonical"


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    window_tokens: int
    max_output_tokens: Optional[int] = None
    source: str = ""
    source_url: str = ""
    verified_at: str = ""
    verified_by: str = ""


@dataclass(frozen=True)
class CategoryInfo:
    name: str
    max_budget_tokens: int
    window_fraction: float
    working_headroom_floor: int
    transcript_head_tokens: int
    transcript_tail_tokens: int
    allowed_reduction_tiers: tuple[int, ...]
    description: str = ""


@dataclass(frozen=True)
class AgentInfo:
    slug: str
    model: ModelInfo
    category: CategoryInfo
    derived_budget_tokens: int
    is_fallback: bool = False
    runtime: str = "claude"
    requested_slug: str | None = None
    resolved_slug: str | None = None
    alias_status: str = "canonical"
    warning: str | None = None
    default_effort_override: str | None = None


@dataclass(frozen=True)
class EffortResolution:
    requested_slug: str
    canonical_agent: str
    alias_status: str
    requested_effort: str | None
    alias_default_effort: str | None
    resolved_effort: str | None
    runtime: str
    provider: str | None
    model: str | None
    effort: str | None
    tier_policy_ref: str
    warning: str | None


HEAVY_HIGH_XHIGH_ONLY = {"jArchitect"}


class ProfileRegistry:
    """Loaded subagent-context-profiles.yaml."""

    def __init__(self, data: dict) -> None:
        self._data = data
        self._schema_version: int = int(data.get("schema_version", 1))
        self._categories: dict[str, CategoryInfo] = {
            name: _category_from_dict(name, body)
            for name, body in (data.get("categories") or {}).items()
        }
        # v1 keys — preserved as static mirrors in v2 for back-compat.
        self._slotting: dict[str, str] = dict(data.get("agent_slotting") or {})
        self._primary_model: dict[str, str] = dict(data.get("agent_primary_model") or {})
        self._overrides: dict[str, dict] = dict(data.get("overrides") or {})
        # v2 key — load-bearing source of truth for routing.
        # If missing (v1-only YAML), this is an empty dict and consumers fall
        # back to the legacy keys.
        self._agents: dict[str, dict] = dict(data.get("agents") or {})

    @property
    def schema_version(self) -> int:
        """YAML schema version. 1 = legacy hint-only; 2 = load-bearing agents block."""
        return self._schema_version

    @property
    def categories(self) -> dict[str, CategoryInfo]:
        return dict(self._categories)

    @property
    def v2_agents(self) -> dict[str, dict]:
        """Raw v2 agents map; empty dict when schema_version < 2 or block absent."""
        return dict(self._agents)

    @property
    def canonical_agents(self) -> dict[str, dict]:
        return {
            slug: body
            for slug, body in self._agents.items()
            if alias_status(body) == "canonical"
        }

    @property
    def alias_agents(self) -> dict[str, dict]:
        return {
            slug: body
            for slug, body in self._agents.items()
            if alias_status(body) in {"alias", "retired"}
        }

    def iter_core_files(self) -> dict[str, dict]:
        return self.canonical_agents

    def iter_dispatchable_agents(self) -> dict[str, dict]:
        """Return only canonical, runtime-dispatchable agent records.

        hard-cut: alias/retired records are compatibility metadata
        for legacy plan/history resolution (``resolve_agent_slug`` still
        resolves them) — they must never be surfaced as something the
        runtime could dispatch. Narrowed from ``dict(self._agents)`` (56) to
        ``self.canonical_agents`` (17).
        """
        return self.canonical_agents

    def missing_alias_targets(self) -> list[tuple[str, str | None]]:
        missing: list[tuple[str, str | None]] = []
        for slug, body in self.alias_agents.items():
            routing = body.get("routing_metadata") if isinstance(body, dict) else None
            alias = routing.get("alias_redirect") if isinstance(routing, dict) else None
            redirects_to = alias.get("redirects_to") if isinstance(alias, dict) else None
            target = self._agents.get(redirects_to) if isinstance(redirects_to, str) else None
            if target is None or alias_status(target) != "canonical":
                missing.append((slug, redirects_to))
        return sorted(missing)

    def resolve_agent_slug(self, slug: str) -> dict:
        body = self._agents.get(slug)
        if body is None:
            return {
                "requested_slug": slug,
                "resolved_slug": slug,
                "status": "unknown",
                "warning": f"unknown agent slug {slug!r} — fail-open self-resolve",
                "default_effort_override": None,
            }

        status = alias_status(body)
        if status == "canonical":
            return {
                "requested_slug": slug,
                "resolved_slug": slug,
                "status": "canonical",
                "warning": None,
                "default_effort_override": None,
            }

        routing = body.get("routing_metadata") if isinstance(body, dict) else None
        alias = routing.get("alias_redirect") if isinstance(routing, dict) else None
        alias = alias if isinstance(alias, dict) else {}
        target = alias.get("redirects_to")
        warning = (
            f"agent slug {slug!r} is a compatibility alias; use canonical {target!r}"
            if status == "alias"
            else f"agent slug {slug!r} is retired; use canonical {target!r}"
        )
        return {
            "requested_slug": slug,
            "resolved_slug": target,
            "status": status,
            "warning": warning,
            "default_effort_override": alias.get("default_effort") or None,
        }

    def v2_agent(self, slug: str) -> Optional[dict]:
        """Return the v2 agent block for slug, or None if absent."""
        return self._agents.get(slug)

    def category_for_agent(self, slug: str) -> CategoryInfo:
        cat_name = self._slotting.get(slug)
        if cat_name is None:
            _LOG.warning(
                "unknown agent slug %r — defaulting to category %r",
                slug, DEFAULT_CATEGORY,
            )
            cat_name = DEFAULT_CATEGORY
        if cat_name not in self._categories:
            _LOG.warning(
                "agent %r slotted to unknown category %r — defaulting to %r",
                slug, cat_name, DEFAULT_CATEGORY,
            )
            cat_name = DEFAULT_CATEGORY
        return self._categories[cat_name]

    def primary_model_slug(self, slug: str) -> Optional[str]:
        return self._primary_model.get(slug)

    def override_for_agent(self, slug: str) -> dict:
        return dict(self._overrides.get(slug, {}))

    @property
    def model_override_allow_all(self) -> bool:
        """global allow-all flag: root-level ``model_override_allow_all``.

        Sibling of ``agents:``/``categories:`` in the profiles root mapping.
        ``True`` only when the raw value is the literal boolean ``True`` —
        any other value (absent, ``"true"``, ``1``, etc.) is treated as off
        so the default-DENY allowlist path is unchanged unless the flag is
        deliberately set.
        """
        return self._data.get("model_override_allow_all") is True


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #


def _category_from_dict(name: str, body: dict) -> CategoryInfo:
    tk = body.get("transcript_keep_policy") or {}
    tiers = tuple(int(t) for t in (body.get("allowed_reduction_tiers") or []))
    return CategoryInfo(
        name=name,
        max_budget_tokens=int(body["max_budget_tokens"]),
        window_fraction=float(body["window_fraction"]),
        working_headroom_floor=int(body["working_headroom_floor"]),
        transcript_head_tokens=int(tk.get("head_tokens", 0)),
        transcript_tail_tokens=int(tk.get("tail_tokens", 0)),
        allowed_reduction_tiers=tiers,
        description=str(body.get("description", "")).strip(),
    )


def load_model_windows(path: Optional[Path] = None) -> dict[str, ModelInfo]:
    p = Path(path) if path is not None else _DEFAULT_MODEL_WINDOWS
    if not p.exists():
        _LOG.warning("model-windows.yaml not found at %s — returning empty registry", p)
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    models: dict[str, ModelInfo] = {}
    for slug, body in (data.get("models") or {}).items():
        models[slug] = ModelInfo(
            slug=slug,
            window_tokens=int(body.get("window_tokens", 0)),
            max_output_tokens=(
                int(body["max_output_tokens"])
                if body.get("max_output_tokens") is not None
                else None
            ),
            source=str(body.get("source", "")),
            source_url=str(body.get("source_url", "")),
            verified_at=str(body.get("verified_at", "")),
            verified_by=str(body.get("verified_by", "")),
        )
    return models


def load_profiles(path: Optional[Path] = None) -> ProfileRegistry:
    p = Path(path) if path is not None else _DEFAULT_PROFILES
    if not p.exists():
        _LOG.warning("subagent-context-profiles.yaml not found at %s", p)
        return ProfileRegistry({})
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return ProfileRegistry(data)


# --------------------------------------------------------------------------- #
# fail-loud override validation (unknown_model / unknown_effort)
# --------------------------------------------------------------------------- #
#
# "Allow any model" (model_override_allow_all) must mean "any KNOWN model",
# never "any string". These helpers are the shared, generic (non-per-model)
# validation layer used by both resolve_model_override branches below, by
# mint_dispatch_capability.py / route_override_directive.py, and mirrored in
# apps/jAgentProxy/lib/routeAuthority.js for the JS proxy trust path.

_MODEL_WINDOWS_MTIME_CACHE: dict[str, tuple[float, dict[str, ModelInfo]]] = {}


def _load_model_windows_cached(path: Path) -> dict[str, ModelInfo]:
    """mtime-cached wrapper around ``load_model_windows``.

    Raises if ``path`` does not exist or fails to parse — the caller
    (``validate_model_literal``) treats any exception as "registry
    unavailable" and fails open.
    """
    mtime = path.stat().st_mtime
    key = str(path)
    cached = _MODEL_WINDOWS_MTIME_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    models = load_model_windows(path)
    _MODEL_WINDOWS_MTIME_CACHE[key] = (mtime, models)
    return models


def validate_model_literal(model: str, *, model_windows_path: Optional[Path] = None) -> dict:
    """Validate a model literal against the canonical ``models:`` keys in
    ``model-windows.yaml``.

    Returns ``{"status": "ok"}`` when the model is a known key (or when the
    registry file is missing/unparseable — fail-open, logged, so a moved/
    broken docs file never bricks every override in the fleet), otherwise
    ``{"status": "rejected", "reason": "unknown_model", "suggestion": <str>}``
    with a difflib closest-match suggestion when one is found.
    """
    if not isinstance(model, str) or not model:
        return {"status": "rejected", "reason": "invalid_model"}

    p = Path(model_windows_path) if model_windows_path is not None else _DEFAULT_MODEL_WINDOWS
    try:
        models = _load_model_windows_cached(p)
    except Exception:
        _LOG.warning(
            "model-windows.yaml at %s is missing or unparseable — unknown_model "
            "validation is unavailable, failing open for model %r",
            p, model,
        )
        return {"status": "ok"}

    if model in models:
        return {"status": "ok"}

    if "/" not in model:
        model_lower = model.lower()
        for prefix, provider in _PROVIDER_FAMILY_PREFIXES.items():
            if model_lower.startswith(prefix) and f"{provider}/{model}" in models:
                return {"status": "ok"}

    result: dict = {"status": "rejected", "reason": "unknown_model"}
    matches = difflib.get_close_matches(model, list(models.keys()), n=1, cutoff=0.5)
    if matches:
        result["suggestion"] = matches[0]
    return result


_MODEL_WINDOWS_REL_PATH = "docs/_CONTROLLED_CONFIG/model-windows.yaml"

# Generic family-prefix pairing rule (fail-loud spec §D.2) — no
# per-model table, only a coarse provider-family check so a request like
# ``provider=openai model=claude-sonnet-5`` is caught as internally
# inconsistent regardless of whether either literal is independently known.
_PROVIDER_FAMILY_PREFIXES: dict[str, str] = {
    "gpt-": "openai",
    "codex-": "openai",
    "claude-": "anthropic",
    "glm-": "zai",
}


def format_unknown_model_message(model: str, suggestion: Optional[str]) -> str:
    """The frozen unknown_model error message shape (shared across every
    Python surface that reports it): includes a did-you-mean suggestion when
    one is available, and always names the registry file."""
    if suggestion:
        return (
            f"unknown model {model!r} — did you mean {suggestion!r}? "
            f"(registry: {_MODEL_WINDOWS_REL_PATH})"
        )
    return f"unknown model {model!r} (registry: {_MODEL_WINDOWS_REL_PATH})"


def validate_provider_model_pairing(provider: str, model: str) -> dict:
    """Generic (non-per-model) provider/model family-prefix consistency
    check: ``gpt-*``/``codex-*`` -> openai, ``claude-*`` -> anthropic,
    ``glm-*`` -> zai. A model outside every known family prefix is not
    constrained by this check (it only fires on a *recognized* mismatch).

    fail-loud FIX CYCLE 2 (main-lane jCritic FINDING 2 — HIGH): some
    provider families (e.g. Z.ai's ``zai/glm-5.1`` in model-windows.yaml) key
    their ``models:`` entries with an explicit ``<provider>/<stem>`` prefix,
    unlike the bare stems every other family uses. A "/" in the model literal
    is itself a provider assertion — the text before the slash MUST equal the
    given ``provider`` (a mismatch is unconditionally rejected, independent
    of the family-prefix table below), and the family-prefix rule is then
    applied to the suffix after the slash for defense-in-depth /
    self-consistency. This is purely generic string handling — no per-model
    special case.
    """
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        return {"status": "ok"}
    check_model = model
    if "/" in model:
        slash_prefix, _, suffix = model.partition("/")
        if slash_prefix and slash_prefix != provider:
            return {"status": "rejected", "reason": "provider_model_mismatch"}
        check_model = suffix
    model_lower = check_model.lower()
    for prefix, expected_provider in _PROVIDER_FAMILY_PREFIXES.items():
        if model_lower.startswith(prefix):
            if provider != expected_provider:
                return {"status": "rejected", "reason": "provider_model_mismatch"}
            break
    return {"status": "ok"}


def _canonical_effort_set(profiles: ProfileRegistry) -> set[str]:
    """Union of ``effort_tiers`` declared across every canonical agent in
    ``profiles`` — the fleet's own effort vocabulary, reused here rather than
    inventing a new hardcoded effort enum (per the frozen spec)."""
    efforts: set[str] = set()
    for body in profiles.canonical_agents.values():
        tiers = body.get("effort_tiers") if isinstance(body, dict) else None
        if isinstance(tiers, list):
            efforts.update(t for t in tiers if isinstance(t, str) and t)
    return efforts


def validate_effort_literal(effort: str, *, profiles: Optional[ProfileRegistry] = None,
                             profiles_path: Optional[Path] = None) -> dict:
    """Validate an effort literal against the canonical effort set derived
    from ``subagent-context-profiles.yaml``'s ``agents.*.effort_tiers``.

    Fail-open (``ok`` + logged warning) when no canonical effort vocabulary
    can be derived at all (e.g. profiles file missing/empty); fail-closed
    once a real vocabulary is available.
    """
    if not isinstance(effort, str) or not effort:
        return {"status": "rejected", "reason": "invalid_effort"}

    registry_ = profiles if profiles is not None else load_profiles(profiles_path)
    canonical = _canonical_effort_set(registry_)
    if not canonical:
        _LOG.warning(
            "no canonical effort_tiers could be derived from the profiles "
            "registry — unknown_effort validation is unavailable, failing "
            "open for effort %r",
            effort,
        )
        return {"status": "ok"}

    if effort in canonical:
        return {"status": "ok"}
    return {"status": "rejected", "reason": "unknown_effort"}


def _runtime_route_from_policy(route: object, *, agent: str, tier_ref: str) -> tuple[str | None, str | None, str | None]:
    if not isinstance(route, dict):
        raise ValueError(f"{tier_ref} for {agent} must be a mapping")
    if set(route) != {"provider", "model", "effort"}:
        raise ValueError(f"{tier_ref} for {agent} must expose provider/model/effort only")
    provider = route.get("provider")
    model = route.get("model")
    effort = route.get("effort")
    return (
        str(provider) if provider is not None else None,
        str(model) if model is not None else None,
        str(effort) if effort is not None else None,
    )


def resolve_effort(
    agent_slug: str,
    requested_effort: str | None = None,
    *,
    runtime: str = "claude",
    profiles_path: Optional[Path] = None,
) -> EffortResolution:
    """Resolve an agent slug plus optional effort into an executable runtime route.

    6C makes effort an explicit dispatch parameter. Resolution is
    fail-closed: selected efforts must be declared by the canonical core's
    ``effort_tiers`` and backed by ``tier_policy`` for the requested runtime.
    Fixed no-effort micro cores declare ``effort_tiers: []`` and only accept a
    null requested effort.
    """
    if runtime not in _RUNTIMES:
        raise ValueError(f"unknown runtime {runtime!r} — expected one of {_RUNTIMES}")

    profiles = load_profiles(profiles_path)
    resolution = profiles.resolve_agent_slug(agent_slug)
    canonical = resolution.get("resolved_slug")
    if not isinstance(canonical, str) or not canonical:
        raise ValueError(f"agent slug {agent_slug!r} does not resolve to a canonical agent")
    alias_state = str(resolution.get("status") or "canonical")
    if alias_state == "unknown":
        raise ValueError(f"unknown agent slug {agent_slug!r}")
    canonical_block = profiles.v2_agent(canonical)
    if not isinstance(canonical_block, dict) or alias_status(canonical_block) != "canonical":
        raise ValueError(f"agent slug {agent_slug!r} resolves to non-canonical target {canonical!r}")

    alias_default = resolution.get("default_effort_override")
    alias_default = alias_default if isinstance(alias_default, str) else None
    effort_tiers = canonical_block.get("effort_tiers")
    if not isinstance(effort_tiers, list):
        raise ValueError(f"agents.{canonical}.effort_tiers must be a list")
    default_effort = canonical_block.get("default_effort")
    if default_effort is not None and not isinstance(default_effort, str):
        raise ValueError(f"agents.{canonical}.default_effort must be a string or null")
    tier_policy = canonical_block.get("tier_policy")
    if not isinstance(tier_policy, dict):
        raise ValueError(f"agents.{canonical}.tier_policy must be a mapping")

    if canonical in HEAVY_HIGH_XHIGH_ONLY:
        if effort_tiers != ["high", "xhigh"] or default_effort not in ("high", "xhigh"):
            raise ValueError(
                f"agents.{canonical} heavy cores must declare effort_tiers ['high', 'xhigh'] "
                "and default_effort in ('high', 'xhigh')"
            )
        if requested_effort == "medium":
            raise ValueError(f"agents.{canonical} does not support medium effort")

    if effort_tiers:
        selected = requested_effort or alias_default or default_effort
        if selected is None:
            raise ValueError(f"agents.{canonical}.default_effort is required for selectable effort tiers")
        if selected not in effort_tiers:
            raise ValueError(
                f"unsupported effort {selected!r} for {canonical}; expected one of {effort_tiers!r}"
            )
        tier_block = tier_policy.get(selected)
        tier_ref = f"agents.{canonical}.tier_policy.{selected}.{runtime}"
        if not isinstance(tier_block, dict):
            raise ValueError(f"agents.{canonical}.tier_policy.{selected} must be a mapping")
        provider, model, effort = _runtime_route_from_policy(
            tier_block.get(runtime), agent=canonical, tier_ref=tier_ref
        )
        if effort != selected:
            raise ValueError(f"{tier_ref}.effort must equal selected effort {selected!r}")
        resolved_effort = selected
    else:
        if requested_effort is not None:
            raise ValueError(f"agents.{canonical} is fixed no-effort; requested effort {requested_effort!r} is unsupported")
        if default_effort is not None:
            raise ValueError(f"agents.{canonical}.default_effort must be null for fixed no-effort cores")
        fixed = tier_policy.get("fixed")
        tier_ref = f"agents.{canonical}.tier_policy.fixed.{runtime}"
        if not isinstance(fixed, dict):
            raise ValueError(f"agents.{canonical}.tier_policy.fixed must be a mapping")
        provider, model, effort = _runtime_route_from_policy(
            fixed.get(runtime), agent=canonical, tier_ref=tier_ref
        )
        if effort is not None:
            raise ValueError(f"{tier_ref}.effort must be null for fixed no-effort cores")
        resolved_effort = None

    warning = None
    if alias_state == "alias":
        warning = (
            f"agent slug {agent_slug!r} is a compatibility alias; use canonical {canonical!r}"
            + (f" with effort {resolved_effort!r}" if resolved_effort is not None else "")
        )
    elif alias_state == "retired":
        warning = (
            f"agent slug {agent_slug!r} is retired; use canonical {canonical!r}"
            + (f" with effort {resolved_effort!r}" if resolved_effort is not None else "")
        )

    return EffortResolution(
        requested_slug=agent_slug,
        canonical_agent=canonical,
        alias_status=alias_state,
        requested_effort=requested_effort,
        alias_default_effort=alias_default,
        resolved_effort=resolved_effort,
        runtime=runtime,
        provider=provider,
        model=model,
        effort=effort,
        tier_policy_ref=tier_ref,
        warning=warning,
    )


def _derive_budget(model: ModelInfo, cat: CategoryInfo) -> int:
    if model.window_tokens <= 0:
        # Unknown / disabled model — fall back to category cap.
        return int(cat.max_budget_tokens)
    by_fraction = int(model.window_tokens * cat.window_fraction)
    return min(int(cat.max_budget_tokens), by_fraction)


_RUNTIMES = ("claude", "opencode")


def model_windows_key(provider: Optional[str], model: str, model_keys) -> str:
    """Resolve the model-windows.yaml key for a (provider, model) pair.

    model-windows.yaml keys Z.ai models with a ``zai/`` prefix and OpenAI /
    Anthropic models bare. Tries the bare model name first, then the
    ``provider/model`` form; falls back to the bare name so a missing-model
    WARN names what was actually requested. ``model_keys`` is anything
    supporting ``in`` (a dict of ModelInfo, a dict of raw model bodies,
    a set of key strings).
    """
    if model in model_keys:
        return model
    if provider:
        prefixed = f"{provider}/{model}"
        if prefixed in model_keys:
            return prefixed
    return model


def _resolve_claude_model(
    slug: str,
    profiles: ProfileRegistry,
    models: dict[str, ModelInfo],
    is_fallback: bool,
) -> ModelInfo:
    """Claude Code primary model for ``slug``.

    Reads the legacy ``agent_primary_model`` map — mirror-consistent with the
    v2 schema's per-agent ``claude`` provider block (``model`` field under
    ``agents.<slug>``, provider key ``claude``; enforced by the schema-v2
    tests), so the default lookup path is unchanged from the prior
    registry.
    """
    model_slug = profiles.primary_model_slug(slug)
    if model_slug is None:
        if not is_fallback:
            _LOG.warning(
                "agent %r has no agent_primary_model entry — window=0 fallback",
                slug,
            )
        return ModelInfo(slug="<unknown>", window_tokens=0)
    from jswarm.regenerate_claude_agents import infer_provider

    key = model_windows_key(infer_provider(model_slug), model_slug, models)
    m = models.get(key)
    if m is None:
        _LOG.warning(
            "agent %r references model %r which is missing from model-windows.yaml",
            slug, model_slug,
        )
        return ModelInfo(slug=model_slug, window_tokens=0)
    return m


def _resolve_opencode_model(
    slug: str,
    profiles: ProfileRegistry,
    models: dict[str, ModelInfo],
    is_fallback: bool,
) -> ModelInfo:
    """OpenCode primary model for ``slug``.

    Reads ``agents.<slug>.opencode.primary`` from the schema-v2 block. On a
    v1-only YAML (no ``agents:`` map) the model resolves to window=0 with a
    WARN — the same fail-open contract as the Claude path.
    """
    block = profiles.v2_agent(slug)
    primary = ((block or {}).get("opencode") or {}).get("primary") or {}
    provider = primary.get("provider")
    model_name = primary.get("model")
    if not model_name:
        if not is_fallback:
            _LOG.warning(
                "agent %r has no opencode.primary.model (schema v2 required) "
                "— window=0 fallback",
                slug,
            )
        return ModelInfo(slug="<unknown>", window_tokens=0)
    key = model_windows_key(provider, model_name, models)
    m = models.get(key)
    if m is None:
        _LOG.warning(
            "agent %r references opencode model %r which is missing from "
            "model-windows.yaml",
            slug, key,
        )
        return ModelInfo(slug=key, window_tokens=0)
    return m


def lookup_agent(
    slug: str,
    *,
    runtime: str = "claude",
    model_windows_path: Optional[Path] = None,
    profiles_path: Optional[Path] = None,
) -> AgentInfo:
    """Resolve (model, category, derived_budget) for an agent slug.

    ``runtime`` selects which runtime's primary model backs the lookup:

      - ``"claude"`` (default) — the Claude Code primary. Preserves the
        prior behavior exactly.
      - ``"opencode"`` — the OpenCode primary from the schema-v2
        ``agents.<slug>.opencode.primary`` block.

    The category band is runtime-agnostic (an agent-intrinsic budget
    class), so the two runtimes differ in ``derived_budget_tokens`` only when
    they back the agent with models of different context windows.
    """
    if runtime not in _RUNTIMES:
        raise ValueError(
            f"unknown runtime {runtime!r} — expected one of {_RUNTIMES}"
        )
    models = load_model_windows(model_windows_path)
    profiles = load_profiles(profiles_path)
    resolution = profiles.resolve_agent_slug(slug)
    resolved_slug = resolution.get("resolved_slug") if isinstance(resolution.get("resolved_slug"), str) else slug
    alias_state = str(resolution.get("status") or "canonical")
    warning = resolution.get("warning") if isinstance(resolution.get("warning"), str) else None
    default_effort_override = (
        resolution.get("default_effort_override")
        if isinstance(resolution.get("default_effort_override"), str)
        else None
    )
    if warning:
        _LOG.warning(warning)

    is_fallback = alias_state == "unknown" or resolved_slug not in (profiles._slotting if profiles else {})  # type: ignore[attr-defined]
    cat = profiles.category_for_agent(resolved_slug)

    if runtime == "opencode":
        model = _resolve_opencode_model(resolved_slug, profiles, models, is_fallback)
    else:
        model = _resolve_claude_model(resolved_slug, profiles, models, is_fallback)

    # An alias that pins a non-default effort tier via default_effort_override
    # dispatches at THAT tier's model, which can differ from the canonical's default model
    # (e.g. architect-master -> jArchitect xhigh -> claude-fable-5, a 1M model). Band the
    # window/budget against the effective dispatch model so the generated catalog and derived
    # budget do not drift from routing truth. Canonical / no-override lookups are unchanged
    # (prior behavior preserved); fail-open on any resolution error.
    if default_effort_override:
        try:
            eff = resolve_effort(
                resolved_slug,
                default_effort_override,
                runtime=runtime,
                profiles_path=profiles_path,
            )
        except Exception:
            eff = None
        if (
            eff is not None
            and isinstance(eff.model, str)
            and eff.model
            and eff.model != model.slug
        ):
            tier_model = models.get(eff.model)
            if tier_model is not None:
                model = tier_model

    override = profiles.override_for_agent(resolved_slug)
    if override:
        max_budget = int(override.get("max_budget_tokens", cat.max_budget_tokens))
        win_frac = float(override.get("window_fraction", cat.window_fraction))
        cat = CategoryInfo(
            name=cat.name + "+override",
            max_budget_tokens=max_budget,
            window_fraction=win_frac,
            working_headroom_floor=cat.working_headroom_floor,
            transcript_head_tokens=cat.transcript_head_tokens,
            transcript_tail_tokens=cat.transcript_tail_tokens,
            allowed_reduction_tiers=cat.allowed_reduction_tiers,
            description=cat.description,
        )

    derived = _derive_budget(model, cat)
    return AgentInfo(
        slug=slug,
        model=model,
        category=cat,
        derived_budget_tokens=derived,
        is_fallback=is_fallback,
        runtime=runtime,
        requested_slug=slug,
        resolved_slug=resolved_slug,
        alias_status=alias_state,
        warning=warning,
        default_effort_override=default_effort_override,
    )


def resolve_model_override(
    *,
    agent_slug: str,
    provider: str,
    model: str,
    effort: str,
    reason: str,
    runtime: str = "claude",
    profiles_path: Optional[Path] = None,
) -> dict:
    """Resolve a requested model override against the per-core allowlist policy.

    D-1 Stage 0: ``model_override_policy`` is a default-DENY control —
    a canonical core with no policy block (or no matching entry) rejects with
    ``reason=model_not_allowed``. This never raises; every outcome is a
    structured ``{status, reason}`` (rejected) or ``{status, provider, model,
    effort, tier_policy_ref}`` (ok) mapping, fail-closed on any malformed or
    unresolvable input.
    """
    if not isinstance(agent_slug, str) or not agent_slug:
        return {"status": "rejected", "reason": "invalid_agent"}
    if not isinstance(provider, str) or not provider:
        return {"status": "rejected", "reason": "invalid_provider"}
    if not isinstance(model, str) or not model:
        return {"status": "rejected", "reason": "invalid_model"}
    if not isinstance(effort, str) or not effort:
        return {"status": "rejected", "reason": "invalid_effort"}
    if not isinstance(runtime, str) or not runtime:
        return {"status": "rejected", "reason": "model_not_allowed"}

    profiles = load_profiles(profiles_path)
    resolution = profiles.resolve_agent_slug(agent_slug)
    if resolution.get("status") == "unknown":
        return {"status": "rejected", "reason": "unknown_agent"}

    canonical = resolution.get("resolved_slug")
    canonical_block = profiles.v2_agent(canonical) if isinstance(canonical, str) else None
    if not isinstance(canonical_block, dict) or alias_status(canonical_block) != "canonical":
        return {"status": "rejected", "reason": "uncanonical_agent"}

    # global allow-all: when the profiles root sets
    # ``model_override_allow_all: true``, ANY provider/model/effort tuple is
    # authorized for ANY canonical agent — gated only by the HMAC capability
    # upstream (mint/Stage-1/Stage-2), never by an enumerated allowlist. This
    # takes precedence over the per-agent ``model_override_policy.allowed``
    # list below; when the flag is not exactly ``True``, behavior is
    # byte-for-byte unchanged (existing default-DENY allowlist).
    if profiles.model_override_allow_all is True:
        if not isinstance(reason, str) or not reason.strip():
            return {"status": "rejected", "reason": "missing_reason"}
        model_check = validate_model_literal(model)
        if model_check.get("status") != "ok":
            return model_check
        effort_check = validate_effort_literal(effort, profiles=profiles)
        if effort_check.get("status") != "ok":
            return effort_check
        pairing_check = validate_provider_model_pairing(provider, model)
        if pairing_check.get("status") != "ok":
            return pairing_check
        return {
            "status": "ok",
            "provider": provider,
            "model": model,
            "effort": effort,
            "tier_policy_ref": f"agents.{canonical}.model_override_policy.{runtime}.allow_all",
        }

    policy = canonical_block.get("model_override_policy")
    if not isinstance(policy, dict):
        return {"status": "rejected", "reason": "model_not_allowed"}
    runtime_policy = policy.get(runtime)
    if not isinstance(runtime_policy, dict):
        return {"status": "rejected", "reason": "model_not_allowed"}
    allowed = runtime_policy.get("allowed")
    if not isinstance(allowed, list):
        return {"status": "rejected", "reason": "model_not_allowed"}

    matched_index: Optional[int] = None
    for idx, entry in enumerate(allowed):
        if not isinstance(entry, dict):
            continue
        efforts = entry.get("efforts")
        if not isinstance(efforts, list) or not all(isinstance(x, str) for x in efforts):
            continue
        if (
            entry.get("provider") == provider
            and entry.get("model") == model
            and effort in efforts
        ):
            matched_index = idx
            break

    if matched_index is None:
        return {"status": "rejected", "reason": "model_not_allowed"}

    if bool(runtime_policy.get("require_reason", False)):
        if not isinstance(reason, str) or not reason.strip():
            return {"status": "rejected", "reason": "missing_reason"}

    model_check = validate_model_literal(model)
    if model_check.get("status") != "ok":
        return model_check
    effort_check = validate_effort_literal(effort, profiles=profiles)
    if effort_check.get("status") != "ok":
        return effort_check
    pairing_check = validate_provider_model_pairing(provider, model)
    if pairing_check.get("status") != "ok":
        return pairing_check

    return {
        "status": "ok",
        "provider": provider,
        "model": model,
        "effort": effort,
        "tier_policy_ref": f"agents.{canonical}.model_override_policy.{runtime}.allowed[{matched_index}]",
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jswarm.subagent_context_budget.registry",
        description="Inspect agent → (model, category, derived budget) mapping.",
    )
    parser.add_argument(
        "--lookup-agent",
        metavar="SLUG",
        required=True,
        help="Agent slug to resolve (e.g. micro-code-patcher, architect).",
    )
    parser.add_argument(
        "--model-windows",
        type=Path,
        default=None,
        help="Override path to model-windows.yaml (default: controlled-config).",
    )
    parser.add_argument(
        "--profiles",
        type=Path,
        default=None,
        help="Override path to subagent-context-profiles.yaml.",
    )
    parser.add_argument(
        "--runtime",
        choices=list(_RUNTIMES),
        default="claude",
        help="Runtime whose primary model backs the lookup (default: claude).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    info = lookup_agent(
        args.lookup_agent,
        runtime=args.runtime,
        model_windows_path=args.model_windows,
        profiles_path=args.profiles,
    )
    print(
        f"agent={info.slug} runtime={info.runtime} model={info.model.slug} "
        f"window={info.model.window_tokens} category={info.category.name} "
        f"budget={info.derived_budget_tokens} fallback={info.is_fallback}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
