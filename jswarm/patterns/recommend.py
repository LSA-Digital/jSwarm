from __future__ import annotations

import argparse
import copy
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import yaml


SIGNAL_NAMES: tuple[str, ...] = (
    "scope_blast_radius",
    "user_visible_behavior",
    "shared_contract_surface",
    "security_compliance_external_write",
    "reversibility_migration_risk",
    "novelty_architecture_uncertainty",
    "concurrency_shared_files",
)

TIERS: tuple[str, ...] = ("low", "medium", "high")
TIER_RANK: dict[str, int] = {tier: index for index, tier in enumerate(TIERS)}
VALID_SIGNAL_VALUES: set[str] = set(TIERS)

PRESET_KEYS: dict[str, str] = {
    "low": "l1.story.low-essential",
    "medium": "l1.story.medium-standard",
    "high": "l1.story.high-assurance",
}

# Raw high reads on these families are persisted as hard_high_triggers evidence.
# Concurrency can raise severity to Medium, but is not itself a hard-High family.
HARD_HIGH_SIGNALS: frozenset[str] = frozenset(
    {
        "scope_blast_radius",
        "user_visible_behavior",
        "shared_contract_surface",
        "security_compliance_external_write",
        "reversibility_migration_risk",
        "novelty_architecture_uncertainty",
    }
)

# High is no longer a max-severity default. The engine recommends High only when
# a solo danger family is high, or at least two hard-High families are high.
# Medium is the selector default; applying High still requires owner approval.
SOLO_HIGH_SIGNALS: frozenset[str] = frozenset(
    {
        "security_compliance_external_write",
        "reversibility_migration_risk",
    }
)
HIGH_BAR_MIN_TRIGGERS = 2
DEFAULT_TIER = "medium"

REPO_ROOT = Path(__file__).resolve().parents[2]
PATTERN_DIR = REPO_ROOT / "docs" / "_JarviSWARM" / "patterns"


class PatternCatalogError(RuntimeError):
    """Raised when the on-disk Pattern catalog cannot supply what the selector needs.

    A bare ``KeyError`` from an unguarded catalog lookup gives the caller no idea
    what is missing or where to look. This names the missing preset(s) and the
    directory the selector reads, so a broken or partial checkout fails with a
    diagnosable message instead of a traceback.
    """


def _load_catalog() -> dict[str, dict[str, Any]]:
    """Load source Pattern records directly from PAT-*.pattern.yaml files."""
    catalog: dict[str, dict[str, Any]] = {}
    for path in sorted(PATTERN_DIR.glob("PAT-*.pattern.yaml")):
        record = yaml.safe_load(path.read_text())
        if isinstance(record, dict) and isinstance(record.get("key"), str):
            catalog[record["key"]] = record
    return catalog


def _require_presets(catalog: dict[str, dict[str, Any]]) -> None:
    """Fail with a clear, actionable message when required L1 presets are absent.

    Called right after every catalog load a public entry point depends on, so a
    missing directory or an incomplete set of PAT-*.pattern.yaml records is
    reported by name instead of surfacing as an unhandled KeyError deep in
    recommend() or explain().
    """
    missing = sorted(key for key in PRESET_KEYS.values() if key not in catalog)
    if not missing:
        return
    if not PATTERN_DIR.is_dir():
        raise PatternCatalogError(
            f"pattern catalog directory not found at {PATTERN_DIR}. "
            "The ceremony selector needs PAT-*.pattern.yaml records defining "
            f"{', '.join(sorted(PRESET_KEYS.values()))}."
        )
    raise PatternCatalogError(
        f"pattern catalog at {PATTERN_DIR} is missing required preset(s): {', '.join(missing)}. "
        "Each must be supplied by a PAT-*.pattern.yaml file whose top-level 'key' matches."
    )


def _normalize_signals(signals: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    detected: dict[str, str] = {}
    unknown: set[str] = set()

    for signal_name in SIGNAL_NAMES:
        raw_value = signals.get(signal_name)
        if raw_value in VALID_SIGNAL_VALUES:
            detected[signal_name] = str(raw_value)
        else:
            detected[signal_name] = "low"
            unknown.add(signal_name)

    for supplied_name in signals:
        if supplied_name not in SIGNAL_NAMES:
            unknown.add(str(supplied_name))

    return detected, sorted(unknown)


def _hard_high_triggers(detected: dict[str, str]) -> list[str]:
    return sorted(signal for signal in HARD_HIGH_SIGNALS if detected.get(signal) == "high")


def _meets_high_bar(detected: dict[str, str]) -> bool:
    """Return True when the raised High bar is met (not every raw high read)."""
    fired = _hard_high_triggers(detected)
    return any(signal in SOLO_HIGH_SIGNALS for signal in fired) or len(fired) >= HIGH_BAR_MIN_TRIGGERS


def _recommended_tier(detected: dict[str, str]) -> str:
    """Medium-default engine baseline. High only when the High bar is met."""
    if _meets_high_bar(detected):
        return "high"
    if any(value != "low" for value in detected.values()):
        return "medium"
    return "low"


def _compile_for_capabilities(
    compile_block: dict[str, Any], *, worktree_adopted: bool, nfr_adopted: bool
) -> dict[str, Any]:
    compiled = copy.deepcopy(compile_block)
    if not worktree_adopted and str(compiled.get("worktree", "")).lower() not in {"no", "none", "n_a"}:
        compiled["worktree"] = "serialized_fallback"
    if not nfr_adopted and str(compiled.get("nfr", "")).lower() not in {"none", "no", "n_a"}:
        compiled["nfr"] = "local_weights_fallback"
    return compiled


def _first_text(items: Any, default: str) -> str:
    if isinstance(items, list) and items:
        return str(items[0])
    return default


def _delta_vs_lower(tier: str, compile_block: dict[str, Any], options_so_far: list[dict[str, Any]]) -> str:
    if tier == "low":
        return "Baseline: acceptance core, focused proof, standard review evidence."
    lower_compile = options_so_far[-1]["compile"]
    changed = [key for key in sorted(compile_block) if compile_block.get(key) != lower_compile.get(key)]
    if not changed:
        return "No compile delta from lower tier."
    return "Changes vs lower: " + ", ".join(changed) + "."


def _card_for_preset(
    tier: str,
    preset: dict[str, Any],
    compile_block: dict[str, Any],
    options_so_far: list[dict[str, Any]],
) -> dict[str, str]:
    applicability = preset.get("applicability", {}) if isinstance(preset.get("applicability"), dict) else {}
    use_when = _first_text(applicability.get("use_when"), "Matches this tier's delivery shape.")
    includes = preset.get("composition", {}).get("includes", []) if isinstance(preset.get("composition"), dict) else []
    ingredient_count = len(includes) if isinstance(includes, list) else 0
    return {
        "pattern": f"{preset.get('name', PRESET_KEYS[tier])} ({preset.get('key', PRESET_KEYS[tier])})",
        "fits": use_when,
        "tests": (
            f"Q5={compile_block.get('q5_depth')}; UAT={compile_block.get('automated_uat')}; "
            f"E2E={compile_block.get('e2e_policy')}; data={compile_block.get('test_data_strategy')}"
        ),
        "isolation": f"worktree={compile_block.get('worktree')}",
        "quality": f"NFR={compile_block.get('nfr')}; review={compile_block.get('review_tier_input')}",
        "team": f"execution_hint={compile_block.get('q6_execution_team_hint')}; architecture={compile_block.get('architecture_tier_input')}",
        "load": (
            f"ingredients={ingredient_count}; precompact={compile_block.get('precompact_cadence')}; "
            f"context={compile_block.get('orchestrator_context')}"
        ),
        "delta_vs_lower": _delta_vs_lower(tier, compile_block, options_so_far),
    }


def _box_lines(title: str, rows: list[tuple[str, str]], *, width: int = 100) -> list[str]:
    content_width = width - 4
    lines = [f"┌─ {title:<{content_width - 2}} ┐"]
    for label, value in rows:
        text = f"{label}: {value}"
        if len(text) > content_width:
            text = text[: content_width - 1] + "…"
        lines.append(f"│ {text:<{content_width}} │")
    lines.append(f"└{'─' * (width - 2)}┘")
    return lines


def _render_cards(options: list[dict[str, Any]], recommended_tier: str) -> str:
    lines: list[str] = []
    for option in options:
        tier = option["tier"]
        chooser = f"[{tier[0].upper()}]"
        marker = " ✓ recommended" if tier == recommended_tier else ""
        card = option["card"]
        title = f"{chooser} {tier.upper()}{marker}"
        lines.extend(
            _box_lines(
                title,
                [
                    ("pattern", card["pattern"]),
                    ("fits", card["fits"]),
                    ("tests", card["tests"]),
                    ("isolation", card["isolation"]),
                    ("quality", card["quality"]),
                    ("team", card["team"]),
                    ("load", card["load"]),
                    ("+vs-lower", card["delta_vs_lower"]),
                ],
            )
        )
    return "\n".join(lines)


def _trigger_evidence(recommended_compile: dict[str, Any], detected: dict[str, str]) -> dict[str, Any]:
    triggers: list[dict[str, str]] = []
    for signal_name in SIGNAL_NAMES:
        value = detected[signal_name]
        if value != "low":
            triggers.append(
                {
                    "signal": signal_name,
                    "value": value,
                    "hard_high": "yes" if signal_name in HARD_HIGH_SIGNALS and value == "high" else "no",
                }
            )
    return {
        "review_tier_input": recommended_compile.get("review_tier_input"),
        "architecture_tier_input": recommended_compile.get("architecture_tier_input"),
        "triggers": triggers,
    }


def _reasoning(detected: dict[str, str], recommended_tier: str) -> list[str]:
    fired = _hard_high_triggers(detected)
    lines = [f"{signal}: {detected[signal]}" for signal in SIGNAL_NAMES]
    if recommended_tier == "high":
        lines.append(
            f"chosen_tier: high because the High bar is met on {', '.join(fired)}. "
            "Selecting High still requires explicit owner approval."
        )
    elif recommended_tier == "medium":
        if fired:
            lines.append(
                "chosen_tier: medium because a single non-solo high signal does not meet the High bar "
                f"({', '.join(fired)}); Medium is the default."
            )
        else:
            lines.append("chosen_tier: medium because at least one signal is above low and the High bar is not met.")
    else:
        lines.append("chosen_tier: low because all signals normalize to low.")
    return lines


def _capability_flags(worktree_adopted: bool, nfr_adopted: bool) -> dict[str, Any]:
    notes: list[str] = []
    if not worktree_adopted:
        notes.append("fail_open: worktree capability unavailable; serialized fallback compiled")
    if not nfr_adopted:
        notes.append("fail_open: NFR catalog unavailable; local weights fallback compiled")
    if not notes:
        notes.append("capabilities_adopted: source compile values preserved")
    return {
        "worktree_adopted": worktree_adopted,
        "nfr_adopted": nfr_adopted,
        "notes": notes,
    }


def recommend(
    signals: dict[str, Any], *, worktree_adopted: bool = True, nfr_adopted: bool = True
) -> dict[str, Any]:
    """Compile deterministic Low/Medium/High Pattern recommendations from signal values."""
    catalog = _load_catalog()
    _require_presets(catalog)
    detected, unknown_signals = _normalize_signals(signals)
    recommended_tier = _recommended_tier(detected)

    options: list[dict[str, Any]] = []
    for tier in TIERS:
        preset_key = PRESET_KEYS[tier]
        preset = catalog[preset_key]
        compile_block = _compile_for_capabilities(
            preset["compile"], worktree_adopted=worktree_adopted, nfr_adopted=nfr_adopted
        )
        option: dict[str, Any] = {
            "tier": tier,
            "preset_key": preset_key,
            "compile": compile_block,
        }
        option["card"] = _card_for_preset(tier, preset, compile_block, options)
        options.append(option)

    recommended_compile = options[TIER_RANK[recommended_tier]]["compile"]
    hard_high = _hard_high_triggers(detected)
    floor_fires = _meets_high_bar(detected)
    result: dict[str, Any] = {
        "detected": detected,
        "unknown_signals": unknown_signals,
        "recommended_tier": recommended_tier,
        "engine_recommended_tier": recommended_tier,
        "options": options,
        "q6_trigger_evidence": _trigger_evidence(recommended_compile, detected),
        "downgrade_warnings": [
            f"Downgrading below high requires visible risk rationale for hard-High signal: {signal}."
            for signal in hard_high
        ]
        if floor_fires
        else [],
        "reasoning": _reasoning(detected, recommended_tier),
        "capability_flags": _capability_flags(worktree_adopted, nfr_adopted),
        "cards": _render_cards(options, recommended_tier),
    }
    return result


JARVI_PANEL_ROBOT: tuple[str, ...] = (
    "   ___",
    "  [o_o]",
    "  /|_|\\",
    "   d b",
)
JARVI_PANEL_GAP = "   "
JARVI_PANEL_CONTENT_WIDTH = 78


def render_jarvi_callout(text: str, *, tty: bool) -> str:
    """Render Jarvi's deterministic presentation wrapper for selector explanation turns."""
    if not tty:
        return f"Jarvi: {text}"

    content = f"Jarvi: {text}"
    width = max(len(content) + 4, 32)
    inner_width = width - 4
    return "\n".join(
        [
            " [o_o]",
            " /|_|\\",
            f"╭{'─' * (width - 2)}╮",
            f"│ {content:<{inner_width}} │",
            f"╰{'─' * (width - 2)}╯",
        ]
    )


def render_jarvi_panel(title: str, body_lines: list[str]) -> str:
    """Render Jarvi's deterministic robot-left recommendation panel."""
    right_lines = [str(title), *(str(line) for line in body_lines)]
    figure_width = max(len(line) for line in JARVI_PANEL_ROBOT)
    left_blank = " " * figure_width
    output_lines: list[str] = []
    wrapped_segments: list[str] = []

    for right_line in right_lines:
        wrapped_segments.extend(
            textwrap.wrap(
                right_line,
                width=JARVI_PANEL_CONTENT_WIDTH,
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [""]
        )

    total_rows = max(len(wrapped_segments), len(JARVI_PANEL_ROBOT))
    for index in range(total_rows):
        segment = wrapped_segments[index] if index < len(wrapped_segments) else ""
        figure_line = JARVI_PANEL_ROBOT[index] if index < len(JARVI_PANEL_ROBOT) else left_blank
        output_lines.append(f"{figure_line:<{figure_width}} │ {segment}")

    return "\n".join(output_lines)


def render_jarvi_bubble(title: str | None, body_lines: list[str], *, tty: bool, width: int = 70) -> str:
    """Render a deterministic Jarvi figure plus speech bubble without ambient terminal state."""
    normalized_lines = [str(line) for line in body_lines]
    title_text = str(title) if title else ""
    if not tty:
        plain_lines = [title_text, *normalized_lines] if title_text else normalized_lines
        return "\n".join(f"Jarvi: {line}" for line in plain_lines)

    figure_lines = [" [o_o]", " /|_|\\", " /   \\"]
    figure_width = max(len(line) for line in figure_lines)
    gap = "  "
    inner_width = max(20, width - figure_width - len(gap) - 4)
    top_label = f" {title_text} " if title_text else ""
    if len(top_label) > inner_width:
        top_label = top_label[:inner_width]
    top_border = "╭" + top_label + "─" * (inner_width - len(top_label) + 2) + "╮"
    bottom_border = "╰" + "─" * (inner_width + 2) + "╯"

    bubble_lines = [top_border]
    for body_line in normalized_lines:
        wrapped = textwrap.wrap(body_line, width=inner_width, break_long_words=False, break_on_hyphens=False) or [""]
        for wrapped_line in wrapped:
            bubble_lines.append(f"│ {wrapped_line:<{inner_width}} │")
    bubble_lines.append(bottom_border)

    output_lines: list[str] = []
    total_lines = max(len(figure_lines), len(bubble_lines))
    for index in range(total_lines):
        figure = figure_lines[index] if index < len(figure_lines) else ""
        bubble = bubble_lines[index] if index < len(bubble_lines) else ""
        output_lines.append(f"{figure:<{figure_width}}{gap}{bubble}".rstrip())
    return "\n".join(output_lines)


def describe_contract() -> dict[str, Any]:
    """Return the deterministic public contract for the ceremony selector CLI."""
    signal_schema: dict[str, dict[str, Any]] = {
        "scope_blast_radius": {
            "low": "Small, localized change with narrow blast radius.",
            "medium": "Moderate cross-file or workflow impact that needs standard coordination.",
            "high": "Cross-repo or multi-team lifecycle change that cannot be isolated to one delivery unit.",
            "trigger_hint": "Score high only when the change spans multiple repos or teams and has no bounded rollback unit. Broad-but-shallow edits are medium.",
            "hard_high": True,
        },
        "user_visible_behavior": {
            "low": "No user-facing behavior changes or only invisible implementation details.",
            "medium": "User-visible behavior changes with bounded and reversible impact.",
            "high": "Breaking user workflow or an external customer-facing contract, not ordinary visible refinement.",
            "trigger_hint": "Score high only for breaking or externally contracted behavior. Ordinary UI or workflow edits are medium.",
            "hard_high": True,
        },
        "shared_contract_surface": {
            "low": "No shared API, command, hook, schema, or prompt contract changes.",
            "medium": "Shared contract extension or local contract change with compatible consumers.",
            "high": "Breaking change to a live-global shared contract consumed by many agents or downstream projects.",
            "trigger_hint": "Score high only for breaking live-global contracts. Compatible extensions and local contracts are medium.",
            "hard_high": True,
        },
        "security_compliance_external_write": {
            "low": "No security, compliance, credential, external-write, or policy surface.",
            "medium": "Controlled write or policy-adjacent change with local rollback.",
            "high": "Security, compliance, credential, live external write, or policy enforcement impact.",
            "trigger_hint": "Raise when secrets, permissions, live systems, or compliance evidence are involved.",
            "hard_high": True,
        },
        "reversibility_migration_risk": {
            "low": "Easy rollback with no migration or durable state concerns.",
            "medium": "Rollback needs coordination or touches durable configuration/state.",
            "high": "Migration, destructive, irreversible, or hard-to-rollback state change.",
            "trigger_hint": "Raise when rollback needs a migration plan or manual recovery path.",
            "hard_high": True,
        },
        "novelty_architecture_uncertainty": {
            "low": "Known pattern in familiar code with low design uncertainty.",
            "medium": "Some new integration or uncertain implementation detail.",
            "high": "Greenfield architecture or unresolved ownership that blocks design without an architecture runway.",
            "trigger_hint": "Score high only when design cannot proceed without architectural invention. A new integration in a known pattern is medium.",
            "hard_high": True,
        },
        "concurrency_shared_files": {
            "low": "No shared hot files or concurrent-session collision risk.",
            "medium": "Touches files likely shared with adjacent work but conflicts are manageable.",
            "high": "High concurrency or shared-file collision risk requiring serialized coordination.",
            "trigger_hint": "Raise when multiple active sessions may edit the same controlled artifacts.",
            "hard_high": False,
        },
    }
    return {
        "capabilities": {
            "apply_choice": True,
            "owner_approved_high": True,
            "cards": True,
            "drafted_scope_card": True,
            "file_input": True,
            "jarvi_bubble": True,
            "reevaluate": True,
            "scope_gate": True,
            "tier_flag": True,
        },
        "cli_modes": ["cards", "describe", "explain", "grid", "recommend", "reevaluate", "scope", "signals"],
        "contract_version": "2.1",
        "default_tier": DEFAULT_TIER,
        "high_bar": {
            "min_hard_high_triggers": HIGH_BAR_MIN_TRIGGERS,
            "owner_approval_required": True,
            "solo_high_signals": sorted(SOLO_HIGH_SIGNALS),
        },
        "fallbacks": ["no_nfr", "no_worktree"],
        "forbidden_discovery": [
            "--help",
            "cat recommend.py",
            "echo \"==== BEAT\"",
            "find patterns",
            "grep SIGNAL_NAMES",
            "import recommend",
            "ls patterns",
            "python3 selector",
            "sed recommend.py",
        ],
        "hard_high_signals": sorted(HARD_HIGH_SIGNALS),
        "invocation_recipe": {
            "beats": ["scope", "cards", "recommend", "grid", "reevaluate", "explain"],
            "describe": "ceremony_selector.py --describe --format json",
            "format_flag": "--format",
            "input_flag": "--input-json-file",
            "input_mode": "file",
            "session_state_dir": ".jswarm/state/sessions/<session>/ceremony-selector/",
            "tier_flags": ["--high", "--low", "--medium"],
        },
        "presentation_contract": {
            "cards_heading": "PATTERN CARD OPTIONS",
            "explain_as_menu": True,
            "grid_json_internal_only": True,
            "no_beat_banner": True,
            "no_card_footer_choose": True,
            "no_card_footer_explain": True,
            "no_visible_applicability": True,
            "recommendation_title": "Jarvi's Recommendation",
        },
        "required_sequence": ["describe", "scope", "scope_confirm", "cards", "recommend", "grid"],
        "signal_names": list(SIGNAL_NAMES),
        "signal_schema": signal_schema,
        "tiers": list(TIERS),
        "value_domain": list(TIERS),
    }


def requires_rationale(result: dict[str, Any], chosen_tier: str) -> bool:
    """Return True when the High bar is met and the chosen tier is below High."""
    detected = result.get("detected", {})
    return _meets_high_bar(detected) and TIER_RANK.get(chosen_tier, -1) < TIER_RANK["high"]


def apply_choice(
    result: dict[str, Any],
    chosen_tier: str,
    *,
    rationale: str | None = None,
    owner_approved_high: bool = False,
) -> dict[str, Any]:
    """Apply a selected tier: High needs owner approval; below-bar High still needs rationale."""
    if chosen_tier not in TIER_RANK:
        raise ValueError(f"unknown tier: {chosen_tier}")

    compiled = result["options"][TIER_RANK[chosen_tier]]["compile"]
    detected = result.get("detected", {})
    hard_high_triggers = _hard_high_triggers(detected)
    rationale_text = rationale.strip() if isinstance(rationale, str) else ""
    needs_rationale = _meets_high_bar(detected) and TIER_RANK[chosen_tier] < TIER_RANK["high"]

    if chosen_tier == "high" and not owner_approved_high:
        return {
            "chosen_tier": chosen_tier,
            "compiled": compiled,
            "accepted": False,
            "downgrade_rationale": None,
            "blocked_reason": "Selecting High requires explicit owner approval (--owner-approved-high).",
        }

    if needs_rationale and not rationale_text:
        return {
            "chosen_tier": chosen_tier,
            "compiled": compiled,
            "accepted": False,
            "downgrade_rationale": None,
            "blocked_reason": "Choosing below High requires downgrade rationale for fired hard-High trigger(s): "
            + ", ".join(hard_high_triggers),
        }

    return {
        "chosen_tier": chosen_tier,
        "compiled": compiled,
        "accepted": True,
        "downgrade_rationale": rationale if needs_rationale else None,
        "blocked_reason": None,
    }


def reevaluate(
    prior_signals: dict[str, Any],
    prior_tier: str,
    new_signals: dict[str, Any],
    *,
    worktree_adopted: bool = True,
    nfr_adopted: bool = True,
) -> dict[str, Any]:
    """Recompute selector recommendation after scope drift and decide whether to re-prompt."""
    if prior_tier not in TIER_RANK:
        raise ValueError(f"unknown prior tier: {prior_tier}")

    prior_detected, _ = _normalize_signals(prior_signals)
    new_result = recommend(new_signals, worktree_adopted=worktree_adopted, nfr_adopted=nfr_adopted)
    new_detected = new_result["detected"]
    recommended_tier = new_result["recommended_tier"]
    tier_shifted = recommended_tier != prior_tier
    newly_fired_hard_high = sorted(set(_hard_high_triggers(new_detected)) - set(_hard_high_triggers(prior_detected)))

    changed_signals = [
        f"{signal}: {prior_detected[signal]} -> {new_detected[signal]}"
        for signal in SIGNAL_NAMES
        if prior_detected[signal] != new_detected[signal]
    ]
    if not changed_signals:
        changed_signals = ["no selector signal changes detected"]

    why_changed = "; ".join(changed_signals)
    if tier_shifted:
        why_changed += f"; recommended_tier: {prior_tier} -> {recommended_tier}"
    if newly_fired_hard_high:
        why_changed += "; newly_fired_hard_high: " + ", ".join(newly_fired_hard_high)

    return {
        "recommended_tier": recommended_tier,
        "prior_tier": prior_tier,
        "tier_shifted": tier_shifted,
        "newly_fired_hard_high": newly_fired_hard_high,
        "reprompt": tier_shifted or bool(newly_fired_hard_high),
        "why_changed": why_changed,
        "result": new_result,
    }


def _ingredient_lines(preset: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> list[str]:
    includes = preset.get("composition", {}).get("includes", []) if isinstance(preset.get("composition"), dict) else []
    lines: list[str] = []
    for include in includes if isinstance(includes, list) else []:
        key = str(include.get("key"))
        ingredient = catalog.get(key, {})
        name = ingredient.get("name", key)
        depth = include.get("depth", "n/a")
        required = include.get("required", False)
        params = include.get("params", {}) or {}
        params_text = ", ".join(f"{k}={params[k]}" for k in sorted(params)) if isinstance(params, dict) and params else "none"
        lines.append(f"- {key}: {name}; depth={depth}; required={required}; params={params_text}")
    return lines


def explain(result: dict[str, Any], tier: str) -> str:
    """Render the selected tier's L2 ingredients and compiled Q5-Q9 obligations."""
    if tier not in TIER_RANK:
        raise ValueError(f"unknown tier: {tier}")
    option = result["options"][TIER_RANK[tier]]
    catalog = _load_catalog()
    _require_presets(catalog)
    preset = catalog[option["preset_key"]]
    compile_block = option["compile"]

    lines = [
        f"Tier: {tier}",
        f"Preset: {option['preset_key']}",
        "L2 ingredients:",
        *_ingredient_lines(preset, catalog),
        "Compiled obligations:",
        f"- Q5 depth: {compile_block.get('q5_depth')}",
        f"- Q6 execution team hint: {compile_block.get('q6_execution_team_hint')}",
        f"- Q6 review tier input: {compile_block.get('review_tier_input')}",
        f"- Q6 architecture tier input: {compile_block.get('architecture_tier_input')}",
        f"- Q7 automated UAT: {compile_block.get('automated_uat')}",
        f"- Q8 E2E policy: {compile_block.get('e2e_policy')}",
        f"- Q9 test data strategy: {compile_block.get('test_data_strategy')}",
        f"- Worktree: {compile_block.get('worktree')}",
        f"- NFR: {compile_block.get('nfr')}",
        f"- Precompact cadence: {compile_block.get('precompact_cadence')}",
        f"- Orchestrator context: {compile_block.get('orchestrator_context')}",
    ]
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render deterministic Low/Medium/High Pattern recommendation cards.")
    parser.add_argument("--signals", help="JSON object of ceremony selector signal values.")
    parser.add_argument("--no-worktree", action="store_true", help="Compile with serialized fallback when worktree capability is unavailable.")
    parser.add_argument("--no-nfr", action="store_true", help="Compile with local-weight fallback when NFR capability is unavailable.")
    parser.add_argument("--explain", choices=TIERS, help="Print the compiled obligations behind the selected tier and exit.")
    parser.add_argument("--describe", action="store_true", help="Print the selector contract and exit.")
    parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format for --describe.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.describe:
        payload = describe_contract()
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for key in sorted(payload):
                value = payload[key]
                if isinstance(value, (dict, list)):
                    rendered = json.dumps(value, sort_keys=True)
                else:
                    rendered = str(value)
                print(f"{key}: {rendered}")
        return 0

    if args.signals is None:
        print("error: the following arguments are required: --signals", file=sys.stderr)
        return 2

    try:
        signals = json.loads(args.signals)
    except json.JSONDecodeError as exc:
        print(f"error: --signals must be valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(signals, dict):
        print("error: --signals must decode to a JSON object", file=sys.stderr)
        return 2

    try:
        result = recommend(signals, worktree_adopted=not args.no_worktree, nfr_adopted=not args.no_nfr)
        output = explain(result, args.explain) if args.explain else result["cards"]
    except PatternCatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
