from __future__ import annotations

import re
import sys
from pathlib import Path


TIERS = ("low", "medium", "high")
TIER_RANK = {tier: rank for rank, tier in enumerate(TIERS)}

# The seven canonical selector signals (mirrors recommend.SIGNAL_NAMES). The lint
# requires exactly these keys so a floor can never be waived by omitting, duplicating,
# or corrupting a signal in the persisted state.
SIGNAL_NAMES = (
    "scope_blast_radius",
    "user_visible_behavior",
    "shared_contract_surface",
    "security_compliance_external_write",
    "reversibility_migration_risk",
    "novelty_architecture_uncertainty",
    "concurrency_shared_files",
)

# A raw `high` read on any of these is persisted evidence (mirrors recommend.HARD_HIGH_SIGNALS).
# The floor is RECOMPUTED from selector_signals using the raised High bar, never trusted
# from the authored hard_high_triggers line.
HARD_HIGH_SIGNALS = frozenset(
    {
        "scope_blast_radius",
        "user_visible_behavior",
        "shared_contract_surface",
        "security_compliance_external_write",
        "reversibility_migration_risk",
        "novelty_architecture_uncertainty",
    }
)
SOLO_HIGH_SIGNALS = frozenset(
    {
        "security_compliance_external_write",
        "reversibility_migration_risk",
    }
)
HIGH_BAR_MIN_TRIGGERS = 2

TIER_FIELDS = ("selected_ceremony_tier", "engine_recommended_tier", "jarvi_recommended_tier")
REQUIRED_FIELDS = (
    "selected_ceremony_tier",
    "engine_recommended_tier",
    "jarvi_recommended_tier",
    "situational_rationale",
    "selector_signals",
    "hard_high_triggers",
)

# Recall-first heading recognition (never a precision matcher used as a hard-cut): any ATX
# or Setext heading whose NORMALIZED text equals the section title counts, so ordinary
# authoring variants (trailing `:`/`#`, indentation, Setext underline) never fail the gate open.
SECTION_TITLE = "ceremony decision state"
ATX_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+\S")
# A Setext underline: a line of only `=` (H1) or only `-` (H2), 0-3 indent.
SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
# A REAL top-level list entry carries 0-3 leading spaces; 4+ spaces is a Markdown
# indented code block and must not satisfy a persisted field.
FIELD_LINE = re.compile(r"^ {0,3}-[ \t]+`([^`]+):`[ \t]*(.*)$")
# A fence line opens with a run of >=3 backticks OR >=3 tildes (group 2 is the info string).
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# A CLOSING fence is a bare run of one marker char + optional trailing whitespace.
FENCE_CLOSE = re.compile(r"^ {0,3}(`+|~+)[ \t]*$")
# A real HTML-comment opener starts a line (0-3 leading spaces) — NOT inline code (`<!--`),
# escaped text (\<!--), or a 4-space indented code block (    <!--).
COMMENT_OPEN = re.compile(r"^ {0,3}<!--")
PLACEHOLDER = re.compile(r"<[^>]+>")


def _hidden_mask(lines: list[str]) -> list[bool]:
    """One shared visibility mask: mark lines inside a fenced code block OR an HTML comment.

    A fence opens with >=3 backticks or >=3 tildes and closes ONLY with a bare run of the
    SAME marker char of at least the opening length (a different marker or shorter run does
    not close it). A multiline `<!-- ... -->` comment hides everything until its `-->`,
    INCLUDING a comment that opens before a heading. This single mask feeds section counting,
    field extraction, and CLI N/A detection so no fenced or commented content is ever live.
    """
    mask = [False] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    in_comment = False
    for index, line in enumerate(lines):
        if fence_char is not None:
            mask[index] = True  # inside the fence (delimiters included)
            closing = FENCE_CLOSE.match(line)
            if closing:
                run = closing.group(1)
                if run[0] == fence_char and len(run) >= fence_len:
                    fence_char = None
                    fence_len = 0
            continue
        if in_comment:
            mask[index] = True
            if "-->" in line:
                in_comment = False
            continue
        opening = FENCE_OPEN.match(line)
        if opening:
            marker, info = opening.group(1), opening.group(2)
            # A backtick fence's info string may not contain a backtick (CommonMark),
            # so `` ```lang`x `` is NOT a real fence opener.
            if marker[0] == "`" and "`" in info:
                continue
            fence_char = marker[0]
            fence_len = len(marker)
            mask[index] = True  # the opening delimiter is not content
            continue
        if COMMENT_OPEN.match(line):
            mask[index] = True
            if "-->" not in line:
                in_comment = True
            continue
    return mask


def _clean_value(value: str) -> str:
    """Normalize markdown decoration before testing whether a field has content."""
    value = re.sub(r"\([^)]*\)", "", value)  # drop parenthetical asides
    return value.replace("`", "").replace("**", "").strip(" \t\"'")


def _tier_token(value: str) -> str:
    """Extract the leading tier word, ignoring trailing prose (e.g. 'medium — recorded ...')."""
    cleaned = _clean_value(value)
    parts = re.split(r"[\s—–]+", cleaned)
    return parts[0].lower() if parts and parts[0] else ""


def _hard_high_triggers(signals: dict[str, str]) -> set[str]:
    return {signal for signal in HARD_HIGH_SIGNALS if signals.get(signal) == "high"}


def _meets_high_bar(signals: dict[str, str]) -> bool:
    """Mirrors recommend._meets_high_bar: solo danger family or two-plus hard-High highs."""
    fired = _hard_high_triggers(signals)
    return bool(fired & SOLO_HIGH_SIGNALS) or len(fired) >= HIGH_BAR_MIN_TRIGGERS


def _recommended_tier(signals: dict[str, str]) -> str:
    """Medium-default engine baseline (mirrors recommend._recommended_tier)."""
    if _meets_high_bar(signals):
        return "high"
    if any(signals.get(name, "low") != "low" for name in SIGNAL_NAMES):
        return "medium"
    return "low"


def _normalize_heading(text: str) -> str:
    """Normalize heading text for recall-first matching: strip ATX hashes, trailing punctuation, case."""
    stripped = re.sub(r"^ {0,3}#{1,6}[ \t]*", "", text)  # leading ATX hashes
    stripped = re.sub(r"[ \t]*#*[ \t]*$", "", stripped)  # trailing ATX hashes
    stripped = stripped.strip().rstrip(":.-—– \t").strip()
    return re.sub(r"\s+", " ", stripped).lower()


def _headings(lines: list[str], mask: list[bool]) -> list[tuple[int, str]]:
    """Return (line_index, normalized_text) for every LIVE ATX or Setext heading (outside fences/comments)."""
    result: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if mask[index]:
            continue
        if ATX_HEADING.match(line):
            result.append((index, _normalize_heading(line)))
            continue
        # Setext heading: a non-blank paragraph line immediately followed by a live `===`/`---` underline.
        if index + 1 < len(lines) and not mask[index + 1] and SETEXT_UNDERLINE.match(lines[index + 1]):
            stripped = line.strip()
            if stripped and not stripped.startswith(("-", "#", ">", "|", "`", "~")):
                result.append((index, _normalize_heading(line)))
    return result


def _count_sections(lines: list[str]) -> int:
    """Count LIVE ceremony-decision-state headings (recall-first, outside fences/comments)."""
    mask = _hidden_mask(lines)
    return sum(1 for _, norm in _headings(lines, mask) if norm == SECTION_TITLE)


def _decision_fields(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    """Collect REAL top-level fields from the first LIVE decision-state section only.

    The section runs from just after the ceremony heading to the NEXT live heading (ATX or
    Setext), so a later example section can never contribute a field. Fenced code, HTML
    comments (even ones opened before the heading), and 4-space indented code are hidden by
    the shared visibility mask, so an example-only or commented-out section is never live.
    """
    mask = _hidden_mask(lines)
    heads = _headings(lines, mask)
    start: int | None = None
    end = len(lines)
    for position, (index, norm) in enumerate(heads):
        if norm == SECTION_TITLE:
            start = index + 1
            if position + 1 < len(heads):
                end = heads[position + 1][0]  # bound the section at the next live heading
            break
    if start is None:
        return {}, []

    fields: dict[str, str] = {}
    duplicates: list[str] = []

    for index in range(start, end):
        line = lines[index]
        if mask[index]:
            continue  # hidden: fenced code, fence delimiter, or HTML comment
        match = FIELD_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if key in fields:
            if key not in duplicates:
                duplicates.append(key)
            continue
        fields[key] = value

    return fields, duplicates


def _validate_selector_signals(value: str) -> tuple[dict[str, str], list[str]]:
    """Parse and strictly validate selector signals; return (signals, diagnostics)."""
    cleaned = _clean_value(value)
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    diagnostics: list[str] = []
    for token in re.split(r"[·,;\s]+", cleaned):
        if "=" not in token:
            continue
        key, raw = token.split("=", 1)
        key = key.strip()
        raw = raw.strip().lower()
        if key in seen:
            if key not in duplicates:
                duplicates.append(key)
            continue
        seen[key] = raw
        if key not in SIGNAL_NAMES:
            diagnostics.append(f"selector_signals has unknown signal key: {key}")
        elif raw not in TIER_RANK:
            diagnostics.append(f"selector_signals has invalid value for {key}: '{raw}' (must be low/medium/high)")
    for key in duplicates:
        diagnostics.append(f"selector_signals has duplicate signal key: {key}")
    for name in SIGNAL_NAMES:
        if name not in seen:
            diagnostics.append(f"selector_signals is missing required signal: {name}")
    return seen, diagnostics


def _parse_trigger_list(value: str) -> set[str]:
    cleaned = _clean_value(value).strip("[]")
    return {token.strip() for token in re.split(r"[,\s]+", cleaned) if token.strip()}


def lint_decision_state(plan_path: Path) -> list[str]:
    """Return decision-state diagnostics for the persisted ceremony choice.

    An empty list means the plan's single `## Ceremony Decision State` section is valid.
    The floor and the engine tier are RECOMPUTED from the seven canonical selector_signals,
    so authored fields can neither introduce nor waive a floor.
    """
    lines = plan_path.read_text(encoding="utf-8").splitlines()

    section_count = _count_sections(lines)
    if section_count == 0:
        # No LIVE decision-state section (lite/feature/legacy, or heading only inside a
        # fenced example) → nothing to validate; fail open like a plan without the section.
        return []

    diagnostics: list[str] = []
    if section_count > 1:
        diagnostics.append(f"duplicate Ceremony Decision State section: found {section_count} (exactly one required)")

    fields, duplicate_fields = _decision_fields(lines)
    for key in duplicate_fields:
        diagnostics.append(f"duplicate decision-state field: {key}")

    for field in REQUIRED_FIELDS:
        if field not in fields:
            diagnostics.append(f"{field} is required")

    if "situational_rationale" in fields:
        rationale = _clean_value(fields["situational_rationale"])
        if not rationale:
            diagnostics.append("situational_rationale is required and must be non-empty")
        elif PLACEHOLDER.search(rationale):
            diagnostics.append("situational_rationale must be real text, not a <...> template placeholder")

    for field in TIER_FIELDS:
        if field in fields:
            token = _tier_token(fields[field])
            if token not in TIER_RANK:
                diagnostics.append(f"{field} must be one of low/medium/high; found invalid tier '{token}'")

    if "selector_signals" in fields:
        signals, signal_diagnostics = _validate_selector_signals(fields["selector_signals"])
        diagnostics.extend(signal_diagnostics)
        if not signal_diagnostics:  # signals complete + valid → recompute-dependent checks are trustworthy
            recomputed_engine = _recommended_tier(signals)
            if "engine_recommended_tier" in fields:
                persisted_engine = _tier_token(fields["engine_recommended_tier"])
                if persisted_engine != recomputed_engine:
                    diagnostics.append(
                        "engine_recommended_tier must equal the deterministic High-bar result "
                        f"'{recomputed_engine}' (Medium default; High only when the High bar is met), "
                        f"found '{persisted_engine}'"
                    )
            recomputed_floor = _hard_high_triggers(signals)
            if "hard_high_triggers" in fields:
                persisted_floor = _parse_trigger_list(fields["hard_high_triggers"])
                if persisted_floor != recomputed_floor:
                    missing = sorted(recomputed_floor - persisted_floor)
                    unexpected = sorted(persisted_floor - recomputed_floor)
                    detail = []
                    if missing:
                        detail.append("missing " + ", ".join(missing))
                    if unexpected:
                        detail.append("unexpected " + ", ".join(unexpected))
                    diagnostics.append(
                        "hard_high_triggers disagrees with the floor recomputed from selector_signals: "
                        + "; ".join(detail)
                    )
            selected = _tier_token(fields.get("selected_ceremony_tier", ""))
            if selected == "high":
                approval = _clean_value(fields.get("owner_approved_high", "")).lower()
                if approval not in {"true", "yes"}:
                    diagnostics.append(
                        "owner_approved_high must be true when selected_ceremony_tier is high"
                    )
            if _meets_high_bar(signals) and TIER_RANK.get(selected, -1) < TIER_RANK["high"]:
                downgrade = _clean_value(fields.get("downgrade_rationale", ""))
                if not downgrade:
                    diagnostics.append(
                        "downgrade_rationale is required below a fired High bar "
                        f"(recomputed from selector_signals: {', '.join(sorted(recomputed_floor))})"
                    )
                elif PLACEHOLDER.search(downgrade):
                    diagnostics.append("downgrade_rationale must be real text, not a <...> template placeholder")

    return diagnostics


def _main(argv: list[str] | None = None) -> int:
    """Fail-closed CLI: exit 1 on a present-but-invalid decision state; exit 0 when valid
    or when the plan legitimately has no `## Ceremony Decision State` section (lite/feature/legacy)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: decision_state_lint.py <plan_path>", file=sys.stderr)
        return 2
    plan_path = Path(args[0])
    if not plan_path.is_file():
        print(f"error: plan not found: {plan_path}", file=sys.stderr)
        return 2
    lines = plan_path.read_text(encoding="utf-8").splitlines()
    if _count_sections(lines) == 0:  # fence- and ATX-aware: a fenced-only heading is not live
        print(f"N/A: no ceremony decision state in {plan_path} (fail-open for lite/feature/legacy plans)")
        return 0
    diagnostics = lint_decision_state(plan_path)
    if diagnostics:
        print("decision-state lint FAILED:", file=sys.stderr)
        for diagnostic in diagnostics:
            print(f"  - {diagnostic}", file=sys.stderr)
        return 1
    print(f"decision-state lint OK: {plan_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
