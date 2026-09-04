from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import recommend

BEGIN_GENERATED_CONTRACT = "<!-- BEGIN GENERATED CONTRACT (ceremony_selector.py --describe --format json) — do not hand-edit -->"
END_GENERATED_CONTRACT = "<!-- END GENERATED CONTRACT -->"

# A rationale value containing an angle-bracket <...> token is a documentation
# template placeholder, not a real rationale. apply rejects it so a copied command
# cannot waive a hard-High floor with placeholder text.
PLACEHOLDER = re.compile(r"<[^>]+>")

# apply is an exact subcommand: these are the only flags compatible with it.
# Any other mode's flag supplied alongside apply is rejected with exit 2.
APPLY_INCOMPATIBLE_FLAGS: tuple[tuple[str, str], ...] = (
    ("describe", "--describe"),
    ("emit_skill_block", "--emit-skill-block"),
    ("input_json_file", "--input-json-file"),
    ("beat", "--beat"),
    ("scope_json", "--scope-json"),
    ("goal", "--goal"),
    ("in_scope", "--in-scope"),
    ("out_of_scope", "--out-of-scope"),
    ("acceptance", "--acceptance"),
    ("no_worktree", "--no-worktree"),
    ("no_nfr", "--no-nfr"),
    ("no_tty", "--no-tty"),
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render deterministic ceremony selector beats.")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("render", "render-scope", "apply"),
        help="One-shot render mode for live ceremony selection.",
    )
    parser.add_argument("--describe", action="store_true", help="Print the selector contract and exit.")
    parser.add_argument("--signals", help="JSON object of ceremony selector signal values.")
    parser.add_argument("--input-json-file", help="Path to JSON input object for selector beats.")
    parser.add_argument(
        "--no-worktree",
        action="store_true",
        help="Compile with serialized fallback when worktree capability is unavailable.",
    )
    parser.add_argument(
        "--no-nfr",
        action="store_true",
        help="Compile with local-weight fallback when NFR capability is unavailable.",
    )
    parser.add_argument("--no-tty", action="store_true", help="Render Jarvi surfaces in plain non-TTY form.")
    parser.add_argument("--goal", help="One-sentence drafted scope goal for render-scope mode.")
    parser.add_argument("--in-scope", dest="in_scope", help="Comma- or semicolon-separated in-scope items for render-scope mode.")
    parser.add_argument("--out-of-scope", dest="out_of_scope", help="Comma- or semicolon-separated out-of-scope items for render-scope mode.")
    parser.add_argument("--acceptance", help="Comma- or semicolon-separated acceptance items for render-scope mode.")
    parser.add_argument(
        "--beat",
        choices=("scope", "cards", "recommend", "grid", "reevaluate", "explain", "all"),
        help="Selector beat to render.",
    )
    parser.add_argument("--tier", choices=recommend.TIERS, help="Tier to explain or select for apply mode.")
    parser.add_argument("--jarvi-tier", choices=recommend.TIERS, help="Jarvi's situational tier recommendation for apply mode.")
    parser.add_argument("--situational-rationale", help="Non-empty scope rationale for Jarvi's apply recommendation.")
    parser.add_argument("--rationale", help="Downgrade rationale for an apply selection below a fired High floor.")
    parser.add_argument(
        "--owner-approved-high",
        action="store_true",
        help="Record explicit owner approval required to persist High ceremony.",
    )
    parser.add_argument("--scope-json", help="JSON object with goal, in_scope, out_of_scope, and acceptance fields.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format for JSON-capable beats.")
    parser.add_argument("--emit-skill-block", help="Rewrite the generated contract block in the given SKILL.md path.")
    tier_group = parser.add_mutually_exclusive_group()
    tier_group.add_argument("--low", action="store_const", const="low", dest="locked_tier", help="Pre-lock Low ceremony tier.")
    tier_group.add_argument(
        "--medium", action="store_const", const="medium", dest="locked_tier", help="Pre-lock Medium ceremony tier."
    )
    tier_group.add_argument("--high", action="store_const", const="high", dest="locked_tier", help="Pre-lock High ceremony tier.")
    return parser.parse_args(argv)


def _load_json_object(raw: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must decode to a JSON object")
    return payload


def _load_input_file(path: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"--input-json-file is not readable: {exc}") from exc
    return _load_json_object(raw, label="--input-json-file")


def _parse_signal_pairs(raw: str) -> dict[str, str]:
    signals: dict[str, str] = {}
    for item in raw.split(","):
        pair = item.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"--signals entries must use key=value form: {pair}")
        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"--signals entries must use non-empty key=value form: {pair}")
        if key in signals:
            raise ValueError(f"--signals contains duplicate key: {key}")
        signals[key] = value
    if not signals:
        raise ValueError("--signals must include at least one signal")
    return signals


def _load_signals(raw: str) -> dict[str, Any]:
    try:
        return _load_json_object(raw, label="--signals")
    except ValueError as json_error:
        if raw.lstrip().startswith("{"):
            raise json_error
        return _parse_signal_pairs(raw)


def _validate_signals(signals: dict[str, Any]) -> None:
    _detected, unknown_or_bad = recommend._normalize_signals(signals)
    if unknown_or_bad:
        allowed = ", ".join(recommend.TIERS)
        names = ", ".join(unknown_or_bad)
        raise ValueError(f"--signals contains unknown keys or invalid values ({allowed}): {names}")


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _split_scope_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for raw in value for item in _split_scope_items(raw) if item.strip()]
    if value is None:
        return []
    return [item.strip() for item in re.split(r"[;,]", str(value)) if item.strip()]


def _scope_display(value: Any) -> str:
    items = _split_scope_items(value)
    return ", ".join(items) if items else "Not provided"


def _resolve_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, str | None, bool, bool]:
    has_file = bool(args.input_json_file)
    has_signals = args.signals is not None
    if has_file == has_signals:
        raise ValueError("exactly one of --input-json-file or --signals is required")

    prior_signals: dict[str, Any] | None = None
    prior_tier: str | None = None
    no_worktree = bool(args.no_worktree)
    no_nfr = bool(args.no_nfr)

    if has_file:
        payload = _load_input_file(args.input_json_file)
        signals = payload.get("signals")
        if not isinstance(signals, dict):
            raise ValueError("--input-json-file must contain a signals object")
        scope_value = payload.get("scope", {})
        if not isinstance(scope_value, dict):
            raise ValueError("--input-json-file scope must be a JSON object when supplied")
        prior_value = payload.get("prior_signals")
        if prior_value is not None:
            if not isinstance(prior_value, dict):
                raise ValueError("--input-json-file prior_signals must be a JSON object when supplied")
            prior_signals = prior_value
        prior_tier_value = payload.get("prior_tier")
        if prior_tier_value is not None:
            prior_tier = str(prior_tier_value)
        for key in ("no_worktree", "no_nfr"):
            if key in payload and not isinstance(payload[key], bool):
                raise ValueError(f"--input-json-file {key} must be a boolean when supplied")
        no_worktree = payload.get("no_worktree", no_worktree)
        no_nfr = payload.get("no_nfr", no_nfr)
        if args.scope_json:
            scope_value = _load_json_object(args.scope_json, label="--scope-json")
        return signals, scope_value, prior_signals, prior_tier, no_worktree, no_nfr

    signals = _load_json_object(args.signals, label="--signals")
    scope = _load_json_object(args.scope_json, label="--scope-json") if args.scope_json else {}
    return signals, scope, prior_signals, prior_tier, no_worktree, no_nfr


def _resolve_render_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], bool, bool]:
    if args.signals is None:
        raise ValueError("render requires --signals")
    if args.input_json_file:
        raise ValueError("render accepts --signals only; do not use --input-json-file")
    if args.scope_json:
        raise ValueError("render does not accept --scope-json; present scope before running render")
    signals = _load_signals(args.signals)
    _validate_signals(signals)
    return signals, bool(args.no_worktree), bool(args.no_nfr)


def render_scope(scope: dict[str, Any], *, tty: bool) -> str:
    goal = str(scope.get("goal") or "Not provided")
    body_lines = [
        f"Goal: {goal}",
        f"In scope: {_scope_display(scope.get('in_scope'))}",
        f"Out of scope: {_scope_display(scope.get('out_of_scope'))}",
        f"Acceptance: {_scope_display(scope.get('acceptance'))}",
    ]
    return recommend.render_jarvi_panel("Drafted scope", body_lines)


def render_cards(result: dict[str, Any]) -> str:
    return "PATTERN CARD OPTIONS\n" + str(result["cards"])


def render_recommendation(result: dict[str, Any], *, tty: bool) -> str:
    baseline = str(result["engine_recommended_tier"])
    detected = result["detected"]
    body_lines = [
        f"Engine baseline tier: {baseline} (Medium default; High only when the High bar is met).",
        "High bar: two or more hard-High signals, or a solo security/migration high. High requires owner approval.",
        "Signal read for this Story:",
    ]
    body_lines.extend(f"{signal_name}: {detected[signal_name]}" for signal_name in recommend.SIGNAL_NAMES)
    body_lines.extend(
        str(note)
        for note in result.get("capability_flags", {}).get("notes", [])
        if not str(note).startswith("capabilities_adopted")
    )
    # Deterministic engine data only. The Jarvi recommendation sentence is authored
    # by the jPlan.ceremony-selector SKILL after scope confirmation (spec §2.1), never here.
    return recommend.render_jarvi_panel("Engine Baseline", body_lines)


def build_grid(result: dict[str, Any], locked_tier: str | None = None) -> dict[str, Any]:
    recommended_tier = str(result["recommended_tier"])
    default_tier = recommend.DEFAULT_TIER
    questions: list[dict[str, Any]] = []
    if locked_tier is None:
        questions.append(
            {
                "name": "ceremony_tier",
                "header": "Ceremony tier",
                "label": "Choose ceremony tier",
                "options": [
                    {
                        "value": tier,
                        "label": tier,
                        "recommended": tier == recommended_tier,
                        "default": tier == default_tier,
                    }
                    for tier in recommend.TIERS
                ],
                "recommended": recommended_tier,
                "default": default_tier,
            }
        )

    rationale_tiers = [locked_tier] if locked_tier is not None else list(recommend.TIERS)
    for tier_value in rationale_tiers:
        if tier_value and recommend.requires_rationale(result, tier_value):
            question: dict[str, Any] = {
                "name": f"{tier_value}_downgrade_rationale",
                "label": f"Downgrade rationale for {tier_value}",
                "type": "textarea",
                "context_only": True,
            }
            if locked_tier is None:
                question["show_if"] = {"ceremony_tier": tier_value}
            else:
                question["required_when"] = {"locked_tier": tier_value}
            questions.append(question)

    for note in result.get("capability_flags", {}).get("notes", []):
        if str(note).startswith("capabilities_adopted"):
            continue
        questions.append(
            {
                "name": "capability_context",
                "label": "Capability fallback context",
                "type": "text",
                "context_only": True,
                "value": str(note),
            }
        )

    grid: dict[str, Any] = {"internal_only": True, "questions": questions}
    if locked_tier is not None:
        grid["locked_tier"] = locked_tier
    return grid


def render_grid_text(result: dict[str, Any], locked_tier: str | None = None) -> str:
    recommended_tier = str(result["recommended_tier"])
    default_tier = recommend.DEFAULT_TIER
    if locked_tier is None:
        lines = [f"Ceremony tier: {default_tier} (default; engine baseline {recommended_tier})"]
        lines.append("- High requires explicit owner approval")
        lines.append("- choose low | choose medium | choose high")
    else:
        lines = [f"Ceremony tier: locked: {locked_tier} (via --{locked_tier})"]
        if locked_tier == "high":
            lines.append("- High lock records owner approval")
    lines.append("- explain low | explain medium | explain high")

    for question in build_grid(result, locked_tier=locked_tier)["questions"]:
        name = str(question.get("name", ""))
        if name == "ceremony_tier":
            continue
        value = question.get("value")
        suffix = f": {value}" if value is not None else ""
        if "downgrade_rationale" in name:
            lines.append(f"- downgrade rationale required for {locked_tier or name.split('_', 1)[0]}")
        else:
            lines.append(f"- {question['label']}{suffix}")
    return "\n".join(lines)


def render_one_block(result: dict[str, Any], *, tty: bool, locked_tier: str | None = None) -> str:
    return "\n\n".join(
        [
            render_cards(result),
            render_recommendation(result, tty=tty),
            render_grid_text(result, locked_tier=locked_tier),
        ]
    )


def render_reevaluate(
    reevaluation: dict[str, Any], *, tty: bool, output_format: str, locked_tier: str | None = None
) -> str:
    if output_format == "json":
        return json.dumps(reevaluation, indent=2, sort_keys=True)
    lines = [str(reevaluation["why_changed"])]
    if reevaluation.get("reprompt"):
        result = reevaluation["result"]
        lines.append(render_cards(result))
        lines.append(render_grid_text(result, locked_tier=locked_tier))
    return "\n\n".join(lines)


def render_beat(
    beat: str,
    result: dict[str, Any],
    scope: dict[str, Any],
    *,
    tty: bool,
    output_format: str,
    locked_tier: str | None = None,
) -> str:
    if beat == "scope":
        return render_scope(scope, tty=tty)
    if beat == "cards":
        return render_cards(result)
    if beat == "recommend":
        if output_format == "json":
            return json.dumps(result, indent=2, sort_keys=True)
        return render_recommendation(result, tty=tty)
    if beat == "grid":
        grid = build_grid(result, locked_tier=locked_tier)
        if output_format == "json":
            return json.dumps(grid, indent=2, sort_keys=True)
        return render_grid_text(result, locked_tier=locked_tier)
    if beat == "explain":
        if locked_tier is None:
            raise ValueError("--beat explain requires --tier")
        return recommend.explain(result, locked_tier)
    if beat == "all":
        parts = [
            render_scope(scope, tty=tty),
            render_cards(result),
            render_recommendation(result, tty=tty),
            render_grid_text(result, locked_tier=locked_tier),
        ]
        return "\n\n".join(parts)
    raise ValueError(f"unknown beat: {beat}")


def _generated_contract_block() -> str:
    payload = json.dumps(recommend.describe_contract(), indent=2, sort_keys=True)
    return f"{BEGIN_GENERATED_CONTRACT}\n```json\n{payload}\n```\n{END_GENERATED_CONTRACT}"


def emit_skill_block(path: str) -> None:
    skill_path = Path(path)
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"--emit-skill-block target is not readable: {exc}") from exc
    block = _generated_contract_block()
    if BEGIN_GENERATED_CONTRACT in text and END_GENERATED_CONTRACT in text:
        start = text.index(BEGIN_GENERATED_CONTRACT)
        end = text.index(END_GENERATED_CONTRACT, start) + len(END_GENERATED_CONTRACT)
        updated = text[:start] + block + text[end:]
    else:
        updated = text.rstrip() + "\n\n" + block + "\n"
    skill_path.write_text(updated, encoding="utf-8")


def apply_selection(args: argparse.Namespace) -> dict[str, Any]:
    """Validate an explicit ceremony choice and construct its persisted state."""
    if args.signals is None:
        raise ValueError("apply requires --signals")
    if args.tier is None:
        raise ValueError("apply requires --tier")
    if args.jarvi_tier is None:
        raise ValueError("apply requires --jarvi-tier")
    if not args.situational_rationale or not args.situational_rationale.strip():
        raise ValueError("apply requires a non-empty --situational-rationale")
    if PLACEHOLDER.search(args.situational_rationale):
        raise ValueError(
            "--situational-rationale looks like a template placeholder (contains <...>); "
            "supply a real scope-anchored rationale"
        )
    if args.rationale is not None and PLACEHOLDER.search(args.rationale):
        raise ValueError(
            "--rationale looks like a template placeholder (contains <...>); "
            "supply a real downgrade rationale"
        )

    signals = _load_signals(args.signals)
    _validate_signals(signals)
    result = recommend.recommend(signals)
    owner_approved_high = bool(args.owner_approved_high)
    decision = recommend.apply_choice(
        result, args.tier, rationale=args.rationale, owner_approved_high=owner_approved_high
    )
    if not decision["accepted"]:
        raise RuntimeError(str(decision["blocked_reason"]))

    return {
        "selected_ceremony_tier": args.tier,
        "engine_recommended_tier": result["engine_recommended_tier"],
        "jarvi_recommended_tier": args.jarvi_tier,
        "situational_rationale": args.situational_rationale,
        "selector_signals": result["detected"],
        "hard_high_triggers": sorted(
            signal for signal in recommend.HARD_HIGH_SIGNALS if result["detected"].get(signal) == "high"
        ),
        "downgrade_rationale": decision["downgrade_rationale"],
        "owner_approved_high": bool(owner_approved_high and args.tier == "high"),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.mode == "apply":
        # apply is a strict subcommand: reject every foreign flag (including the
        # global --describe / --emit-skill-block side-effect branches) BEFORE they run.
        offending = [flag for attr, flag in APPLY_INCOMPATIBLE_FLAGS if getattr(args, attr)]
        if args.locked_tier is not None:
            offending.append(f"--{args.locked_tier}")
        if offending:
            print(
                f"error: apply does not accept {', '.join(offending)}; "
                "apply takes only --signals --tier --jarvi-tier --situational-rationale --rationale "
                "--owner-approved-high --format",
                file=sys.stderr,
            )
            return 2
        try:
            state = apply_selection(args)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(state, sort_keys=True))
        return 0

    if args.describe:
        payload = recommend.describe_contract()
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

    if args.emit_skill_block:
        try:
            emit_skill_block(args.emit_skill_block)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.mode == "render-scope":
        scope = {
            "goal": args.goal,
            "in_scope": args.in_scope,
            "out_of_scope": args.out_of_scope,
            "acceptance": args.acceptance,
        }
        print(render_scope(scope, tty=not args.no_tty))
        return 0

    if args.mode == "render":
        try:
            signals, no_worktree, no_nfr = _resolve_render_inputs(args)
            result = recommend.recommend(signals, worktree_adopted=not no_worktree, nfr_adopted=not no_nfr)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except recommend.PatternCatalogError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        print(render_one_block(result, tty=not args.no_tty, locked_tier=args.locked_tier))
        return 0

    if not args.beat:
        print("error: the following arguments are required: --beat", file=sys.stderr)
        return 2

    try:
        signals, scope, prior_signals, prior_tier, no_worktree, no_nfr = _resolve_inputs(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.beat == "reevaluate":
        if prior_signals is None or prior_tier is None:
            print("error: reevaluate requires prior_signals and prior_tier", file=sys.stderr)
            return 2
        try:
            reevaluation = recommend.reevaluate(
                prior_signals,
                prior_tier,
                signals,
                worktree_adopted=not no_worktree,
                nfr_adopted=not no_nfr,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except recommend.PatternCatalogError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        print(render_reevaluate(reevaluation, tty=not args.no_tty, output_format=args.format, locked_tier=args.locked_tier))
        return 0

    if args.beat == "explain" and args.tier is None:
        print("error: --beat explain requires --tier", file=sys.stderr)
        return 2

    try:
        result = recommend.recommend(signals, worktree_adopted=not no_worktree, nfr_adopted=not no_nfr)
    except recommend.PatternCatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    try:
        output = render_beat(
            args.beat,
            result,
            scope,
            tty=not args.no_tty,
            output_format=args.format,
            locked_tier=args.tier if args.beat == "explain" else (None if args.beat == "scope" else args.locked_tier),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
