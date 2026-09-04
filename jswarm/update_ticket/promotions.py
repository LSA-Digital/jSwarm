"""Deterministic apply of an approved promotion-decision artifact (Phase 3).

The interactive scan / recommend / approve flow lives in the ``/update-ticket`` SKILL;
this module ONLY applies an explicit, frozen decision JSON. It is **atomic**: every
approved candidate is validated against the CURRENT plan (locator uniqueness, per-row
hash, expected marker, plan sha) BEFORE any write. If ANY approved candidate is
stale / ambiguous / invalid, NOTHING is written — no partial apply, no false-green
(NFR-014-NO-FALSE-GREEN-UNDERREPORT). Denied / held candidates are never flipped.

Exit contract (returned to the wrapper):
    0  applied   — every approved+still-matching flip written in one atomic write
    0  noop      — zero approved candidates; no write
    2  aborted   — invalid artifact OR any approved candidate not still-matching; NO write

Output is a single machine-readable JSON summary line so the skill can branch on it.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

# Self-bootstrap so the sibling engine package imports whether run as a script
# (python jswarm/update_ticket/cli.py) or imported as update_ticket.promotions.
# Insert the repository root (parents[2]: <pkg>/ -> jswarm/ -> repo root), not
# jswarm/ itself (parents[1]). jswarm/ on sys.path would shadow the stdlib for
# anything under jswarm/ sharing a name with it (e.g. jswarm/platform/ vs the
# stdlib platform module).
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Reuse the existing matrix parsing/flip helpers — never re-roll matrix parsing.
from jswarm.precompact_reconcile.matrices import (  # noqa: E402
    _cells,
    _set_last_cell,
    _test_tuple,
    extract_nfr_ref,
    extract_uat_id,
    _NFR_HEADING,
    _UAT_HEADING,
    _TEST_HEADING,
)

SCHEMA_VERSION = "1"

_REQUIRED_TOP = (
    "schema_version", "ticket", "plan_path", "plan_sha256_before",
    "created_at", "created_by", "decision_scope", "candidates",
)
_KINDS = {"uat-row", "nfr-row", "test-row", "ac-checkbox"}
_RECOMMENDATIONS = {"promote", "hold"}
_DECISIONS = {"approved", "denied", "held"}
_ROW_EXPECTED = {"🔴 Backlogged", "🟠 Drafted", "🟡 Ready"}
_ROW_TARGET = "🟢 Done"
_AC_EXPECTED = "[ ]"
_AC_TARGET = "[x]"
_AC_HEADING = "## Acceptance Criteria"

_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")
_HEADING_SECTION = {
    _NFR_HEADING: "nfr",
    _UAT_HEADING: "uat",
    _TEST_HEADING: "test",
    _AC_HEADING: "ac",
}


def canonical_line_hash(line: str) -> str:
    """sha256 of a plan line as the decision was recorded against it (newline-insensitive)."""
    return hashlib.sha256(line.rstrip("\n").encode("utf-8")).hexdigest()


def _ac_label_re(label: str) -> re.Pattern[str]:
    # A checkbox bullet whose text leads with the A/C label as a complete token,
    # tolerating bold (**AC-1**). \b after the escaped label rejects AC-1 vs AC-10.
    return re.compile(r"^\s*-\s*\[( |x|X)\]\s+(?:\*\*\s*)?" + re.escape(label) + r"\b")


def _loc_desc(cand: dict) -> dict:
    """A compact, JSON-safe locator echo for the failure summary."""
    kind = cand.get("kind")
    if kind == "uat-row":
        return {"kind": kind, "scenario_id": cand.get("scenario_id")}
    if kind == "nfr-row":
        return {"kind": kind, "nfr_ref": cand.get("nfr_ref")}
    if kind == "test-row":
        return {"kind": kind, "ac": cand.get("ac"), "test_path": cand.get("test_path"),
                "test_name": cand.get("test_name")}
    if kind == "ac-checkbox":
        return {"kind": kind, "ac_label": cand.get("ac_label")}
    return {"kind": kind}


def _struct_errors(cand: dict) -> list[str]:
    """Structural validation applied to EVERY candidate (a corrupt artifact aborts)."""
    errs: list[str] = []
    if not isinstance(cand, dict):
        return ["not-an-object"]
    kind = cand.get("kind")
    if kind not in _KINDS:
        return [f"bad-kind:{kind!r}"]
    if cand.get("recommendation") not in _RECOMMENDATIONS:
        errs.append(f"bad-recommendation:{cand.get('recommendation')!r}")
    if cand.get("decision") not in _DECISIONS:
        errs.append(f"bad-decision:{cand.get('decision')!r}")
    if not isinstance(cand.get("row_sha256"), str) or not cand.get("row_sha256"):
        errs.append("missing-row_sha256")
    if kind == "ac-checkbox":
        if cand.get("expected_marker") != _AC_EXPECTED:
            errs.append("bad-expected_marker")
        if cand.get("target_marker") != _AC_TARGET:
            errs.append("bad-target_marker")
        if not cand.get("ac_label"):
            errs.append("missing-ac_label")
    else:  # row kinds
        if cand.get("expected_marker") not in _ROW_EXPECTED:
            errs.append("bad-expected_marker")
        if cand.get("target_marker") != _ROW_TARGET:
            errs.append("bad-target_marker")
        # The declared matrix_heading must be the canonical heading for the kind — a candidate
        # cannot claim to target a different/forged section than the one it is located in.
        expected_heading = {"uat-row": _UAT_HEADING, "nfr-row": _NFR_HEADING, "test-row": _TEST_HEADING}[kind]
        if cand.get("matrix_heading") != expected_heading:
            errs.append("bad-matrix_heading")
        if kind == "uat-row" and not cand.get("scenario_id"):
            errs.append("missing-scenario_id")
        if kind == "nfr-row" and not cand.get("nfr_ref"):
            errs.append("missing-nfr_ref")
        if kind == "test-row" and not all(cand.get(k) for k in ("ac", "test_path", "test_name")):
            errs.append("missing-test-locator")
    return errs


def _locator_key(cand: dict) -> tuple:
    """A normalized identity for a candidate's target, independent of its decision — so two
    candidates that point at the SAME row (even one denied + one approved) are a corrupt artifact."""
    kind = cand.get("kind")
    if kind == "uat-row":
        return (kind, cand.get("matrix_heading"), cand.get("scenario_id"))
    if kind == "nfr-row":
        return (kind, cand.get("matrix_heading"), cand.get("nfr_ref"))
    if kind == "test-row":
        return (kind, cand.get("matrix_heading"), cand.get("ac"), cand.get("test_path"), cand.get("test_name"))
    if kind == "ac-checkbox":
        return (kind, cand.get("ac_label"))
    return (kind,)


def _duplicate_locators(candidates: list[dict]) -> list[str]:
    keys = [_locator_key(c) for c in candidates]
    dups = {k for k in keys if keys.count(k) > 1}
    return sorted(" / ".join(str(p) for p in k) for k in dups)


def _resolve_plan(repo_root: Path, ticket: str) -> tuple[Path | None, str | None]:
    # New-location-only by design: promotion decisions resolve the master plan under
    # `.jswarm/plans/KEY.plan.*.md`. Legacy `docs/plans/` tickets predate the slice/matrix model
    # the promotion gate operates on and are intentionally out of scope.
    if not _TICKET_RE.match(ticket):
        return None, "invalid-ticket-key"
    matches = sorted((repo_root / ".jswarm" / "plans").glob(f"{ticket}.plan.*.md"))
    if len(matches) > 1:
        return None, "ambiguous-plan"
    if not matches:
        return None, "plan-not-found"
    return matches[0], None


def _matches(lines: list[str], cand: dict) -> list[int]:
    """Section-scoped line indices matching this candidate's locator (like reconcile_text)."""
    kind = cand["kind"]
    found: list[int] = []
    section: str | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            section = _HEADING_SECTION.get(stripped)
            continue
        if stripped.startswith("### "):
            section = None  # a subsection ends the matrix/section row region
            continue
        if kind == "ac-checkbox":
            if section == "ac" and _ac_label_re(cand["ac_label"]).match(line):
                found.append(i)
            continue
        if not stripped.startswith("|"):
            continue
        cells = _cells(line)
        if kind == "uat-row" and section == "uat":
            if cells and extract_uat_id(cells[0]) == cand["scenario_id"]:
                found.append(i)
        elif kind == "nfr-row" and section == "nfr":
            if len(cells) > 1 and extract_nfr_ref(cells[1]) == cand["nfr_ref"]:
                found.append(i)
        elif kind == "test-row" and section == "test":
            if len(cells) > 4 and _test_tuple(cells, ac_idx=0, path_idx=3, check_idx=4) == (
                cand["ac"], cand["test_path"], cand["test_name"]
            ):
                found.append(i)
    return found


def _current_marker(kind: str, line: str) -> str | None:
    if kind == "ac-checkbox":
        m = re.search(r"\[( |x|X)\]", line)
        return f"[{m.group(1)}]" if m else None
    cells = _cells(line)
    return cells[-1] if cells else None


def _flip(kind: str, line: str, target: str) -> str:
    if kind == "ac-checkbox":
        return line.replace(_AC_EXPECTED, target, 1)
    return _set_last_cell(line, target)


def _summary(base: dict, **extra) -> str:
    return json.dumps({**base, **extra})


def apply(repo_root: Path, ticket: str, promotions_file: str | None) -> tuple[int, str]:
    """Atomically apply approved+still-matching flips from a frozen decision JSON.

    Returns (exit_code, json_summary_line). exit 0 = applied/noop; exit 2 = aborted (no write).
    """
    base = {"section": "apply-promotions", "ticket": ticket}

    if not promotions_file:
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "no-promotions-file"}])
    pf = Path(promotions_file)
    if not pf.exists():
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "promotions-file-not-found", "path": promotions_file}])
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — any parse failure is a hard abort, never a write
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "invalid-json", "detail": str(exc)}])

    if not isinstance(data, dict):
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "not-an-object"}])
    missing = [k for k in _REQUIRED_TOP if k not in data]
    if missing:
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "missing-field", "fields": missing}])

    # Semantic top-level validation: the artifact is a FROZEN authorization token, so it must be
    # for this schema version, this ticket, and this plan — a sha that merely happens to match is
    # not enough authority to mutate (B1 / NFR-014 no-false-green).
    if data.get("schema_version") != SCHEMA_VERSION:
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "bad-schema-version", "expected": SCHEMA_VERSION, "got": data.get("schema_version")}])
    if data.get("ticket") != ticket:
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "ticket-mismatch", "artifact_ticket": data.get("ticket"), "cli_ticket": ticket}])

    candidates = data["candidates"]
    if not isinstance(candidates, list):
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "candidates-not-list"}])

    # Structural validation of EVERY candidate (a corrupt artifact aborts before any write).
    struct = [{"index": i, "reason": "invalid-candidate", "errors": e}
              for i, c in enumerate(candidates) if (e := _struct_errors(c))]
    if struct:
        return 2, _summary(base, status="aborted", applied=0, failures=struct)

    # Contradictory artifact: the SAME row targeted by more than one candidate (e.g. one denied +
    # one approved) is ambiguous authority — abort regardless of decisions, before any write.
    dups = _duplicate_locators(candidates)
    if dups:
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "duplicate-locator", "locators": dups}])

    plan_path, reason = _resolve_plan(repo_root, ticket)
    if plan_path is None:
        return 2, _summary(base, status="aborted", applied=0, failures=[{"reason": reason}])
    # The artifact's declared plan_path must resolve to the very plan we selected for this ticket.
    try:
        declared_matches = (repo_root / data["plan_path"]).resolve() == plan_path.resolve()
    except Exception:  # noqa: BLE001 — any resolution failure is a hard mismatch, never a write
        declared_matches = False
    if not declared_matches:
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "plan-path-mismatch", "declared": data.get("plan_path")}])
    text = plan_path.read_text(encoding="utf-8")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != data["plan_sha256_before"]:
        return 2, _summary(base, status="aborted", applied=0,
                           failures=[{"reason": "plan-sha-mismatch"}])
    lines = text.splitlines(keepends=True)

    approved = [c for c in candidates if c["decision"] == "approved"]
    denied = sum(1 for c in candidates if c["decision"] == "denied")
    held = sum(1 for c in candidates if c["decision"] == "held")

    # Validate-all-then-apply: resolve every approved flip first; one failure aborts all.
    edits: dict[int, str] = {}
    failures: list[dict] = []
    for cand in approved:
        locs = _matches(lines, cand)
        if len(locs) == 0:
            failures.append({"reason": "not-found", "candidate": _loc_desc(cand)})
            continue
        if len(locs) > 1:
            failures.append({"reason": "ambiguous", "candidate": _loc_desc(cand), "matches": len(locs)})
            continue
        idx = locs[0]
        line = lines[idx]
        if canonical_line_hash(line) != cand["row_sha256"]:
            failures.append({"reason": "stale-hash", "candidate": _loc_desc(cand)})
            continue
        marker = _current_marker(cand["kind"], line)
        if marker != cand["expected_marker"]:
            failures.append({"reason": "stale-marker", "candidate": _loc_desc(cand), "current": marker})
            continue
        if idx in edits:
            failures.append({"reason": "duplicate-target", "candidate": _loc_desc(cand)})
            continue
        edits[idx] = _flip(cand["kind"], line, cand["target_marker"])

    if failures:
        return 2, _summary(base, status="aborted", applied=0, failures=failures)
    if not edits:
        return 0, _summary(base, status="noop", applied=0,
                           approved=len(approved), denied=denied, held=held)

    for idx, new_line in edits.items():
        lines[idx] = new_line
    plan_path.write_text("".join(lines), encoding="utf-8")
    return 0, _summary(base, status="applied", applied=len(edits),
                       approved=len(approved), denied=denied, held=held)
