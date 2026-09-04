"""AC-16 — /jPrecompact AUTO-MIGRATION: make a legacy ticket self-heal to current
lifecycle asset standards, so /jPrecompact never has to "figure anything out".

Run FIRST in /jPrecompact Surface 2 (Step 2.0a, before the AC-11 rebuild). Idempotently:
  * SEED a missing slice index (``## UAT Scenario Index`` / ``## A/C-to-NFR Index``) from the
    plan's (possibly hand-authored) matrix rows — matrix cells minus the Status column. The
    slice file is created when absent; the index is appended when the slice exists without one.
  * ENSURE the plan carries both matrix SECTIONS — add an empty section when a slice index
    exists but the plan lacks the matrix (so the rebuild can then populate it).

Safety (critic-xhigh AC-16):
  * No dead seeds. A candidate index is validated with the rebuild's OWN rules
    (``rows.validate_index_rows``) BEFORE writing. The source matrix rows must be EXACT
    canonical width (header incl. trailing Status; no pad/truncate). On any violation the
    slice is left untouched and a ``reason`` is reported — never a file the rebuild rejects.
  * Code-fence aware. Heading detection + the section-insertion anchor ignore headings
    inside fenced code blocks, so a markdown example never blocks seeding or corrupts a file.
  * Never re-seeds/clobbers an existing index/matrix; idempotent; fail-open (exits 0).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Insert the repository root (parents[2]: <pkg>/ -> jswarm/ -> repo root), not
# jswarm/ itself (parents[1]). jswarm/ on sys.path would shadow the stdlib for
# anything under jswarm/ sharing a name with it (e.g. jswarm/platform/ vs the
# stdlib platform module).
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from jswarm.precompact_reconcile.matrices import _TICKET_RE, _cells, _ticket_from_name  # noqa: E402
from jswarm.precompact_reconcile.rows import (  # noqa: E402
    _HEADING_RE, _NFR_INDEX_HEADING, _NFR_MATRIX, _TEST_INDEX_HEADING, _TEST_MATRIX,
    _UAT_INDEX_HEADING, _UAT_MATRIX, _fence_mask, _is_separator, validate_index_rows,
)

_MATRIX_COLUMNS = {
    "uat": ["UAT scenario", "A/C served", "Test(s) / Evidence", "Status"],
    "nfr": ["A/C", "NFR ref (full descriptor)", "Dimension (tags)",
            "Validation recipe / Evidence", "Threshold / Pass criteria", "Status"],
    "test": ["A/C", "UAT refs", "NFR refs", "Test path", "Test name / evidence check",
             "Type", "How it proves A/C", "Status"],
}
_KIND = {
    "nfr": (_NFR_MATRIX, _NFR_INDEX_HEADING, "NFRs"),
    "uat": (_UAT_MATRIX, _UAT_INDEX_HEADING, "UAT scenarios"),
    "test": (_TEST_MATRIX, _TEST_INDEX_HEADING, "regression tests"),
}
_SEED_NOTE = ("> Seeded from the plan matrix by /jPrecompact migrate (AC-16) — edit "
              "HERE; /jPrecompact rebuilds the matrix from this index. A cell must not contain "
              "a literal/escaped `|`.")
_SECTION_NOTE = ("> Added by /jPrecompact migrate (AC-16); rows are rebuilt from the "
                 "slice index.")
_STARTER_NOTE = ("> Scaffolded by /jPrecompact migrate (AC-17) because the plan declares "
                 "this dimension applicable. Add one row per {unit} below — then /jPrecompact "
                 "rebuilds the plan matrix + refreshes the HUD. See "
                 "docs/agent-system/upgrade-existing-ticket.md.")
# Applicability is the one judgment the tool cannot make — it is read from the plan's declared
# header LINES, never guessed. Line-anchored (^, optional bullet) + fence-aware so a fenced
# example or a prose mention of the header is NOT treated as a declaration (critic-xhigh AC-17 F1).
_APPLICABLE = {
    "nfr": re.compile(r"^\s*(?:[-*]\s+)?\*\*NFR catalog:\*\*\s*applicable\b", re.IGNORECASE),
    "uat": re.compile(r"^\s*(?:[-*]\s+)?\*\*Automated UAT:\*\*\s*yes\b", re.IGNORECASE),
}
_STARTER_UNIT = {"nfr": "(A/C, NFR) pair", "uat": "UAT scenario", "test": "(A/C, test path, check) tuple"}
_EMPTY_REPORT = {"uat_seeded": False, "nfr_seeded": False, "test_seeded": False,
                 "uat_section_added": False, "nfr_section_added": False, "test_section_added": False,
                 "uat_scaffolded": False, "nfr_scaffolded": False, "test_scaffolded": False,
                 "uat_skipped": None, "nfr_skipped": None, "test_skipped": None}


def _is_applicable(plan_text: str, kind: str) -> bool:
    lines = plan_text.splitlines()
    mask = _fence_mask(lines)
    rx = _APPLICABLE[kind]
    return any(not mask[i] and rx.match(ln) for i, ln in enumerate(lines))


def _starter_index_section(index_heading: str, columns: list[str], kind: str) -> str:
    """An empty index table (heading + note + header + separator, NO data rows) for an
    applicable ticket that has authored nothing yet. The agent fills rows; /jPrecompact then
    rebuilds + counts (0/0 until filled)."""
    head = columns[:-1]  # drop trailing Status
    return "\n".join([
        index_heading, "", _STARTER_NOTE.format(unit=_STARTER_UNIT[kind]), "",
        "| " + " | ".join(head) + " |",
        "| " + " | ".join(["---"] * len(head)) + " |",
    ]) + "\n"


# ── fence-aware scanning (``_fence_mask`` shared from rows.py) ────────────────

def _is_heading(stripped: str) -> bool:
    return bool(_HEADING_RE.match(stripped))


def _has_heading(text: str, heading: str) -> bool:
    """True when ``heading`` appears as a real (non-fenced) heading line."""
    lines = text.splitlines()
    mask = _fence_mask(lines)
    return any(not mask[i] and ln.strip() == heading for i, ln in enumerate(lines))


# ── matrix extraction (fence-aware; width validated by the caller) ───────────

def _extract_matrix(plan_text: str, heading: str):
    """Return (header_cells, [row_cells, ...]) for the non-fenced matrix table under
    ``heading``, or None when the section/table is absent. ``rows`` is empty for a
    header-only matrix. Rows are returned verbatim."""
    lines = plan_text.splitlines()
    mask = _fence_mask(lines)
    h = next((i for i, ln in enumerate(lines)
              if not mask[i] and ln.strip() == heading), None)
    if h is None:
        return None
    header_idx = None
    j = h + 1
    while j < len(lines):
        if mask[j]:
            j += 1
            continue
        s = lines[j].strip()
        if _is_heading(s):
            break
        if s.startswith("|"):
            header_idx = j
            break
        j += 1
    if header_idx is None:
        return None
    header = _cells(lines[header_idx])
    if len(header) < 2:
        return None
    data_start = header_idx + 1
    if (data_start < len(lines) and not mask[data_start]
            and lines[data_start].strip().startswith("|")
            and _is_separator(_cells(lines[data_start]))):
        data_start += 1
    rows: list[list[str]] = []
    k = data_start
    while k < len(lines):
        if mask[k]:
            break
        s = lines[k].strip()
        if not s.startswith("|"):
            break
        cells = _cells(lines[k])
        if _is_separator(cells):
            break
        rows.append(cells)
        k += 1
    return header, rows


def _seed_index_section(index_heading: str, header: list[str],
                        index_rows: list[list[str]]) -> str:
    """Build the index-table section. ``index_rows`` are already exact-width (len(header)-1)
    and validated by the caller."""
    width = len(header) - 1
    head = header[:width]
    out = [index_heading, "", _SEED_NOTE, "",
           "| " + " | ".join(head) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    for row in index_rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out) + "\n"


def _ensure_matrix_section(plan_text: str, matrix_heading: str, columns: list[str],
                           kind: str) -> str:
    """Insert an empty matrix section before the real (non-fenced) ``## Testing Strategy``
    heading (else at end). No-op if the matrix heading already exists (non-fenced). The NFR
    section also carries its canonical ``### NFR Validation Strategy`` subsection — the count
    derivation (``_nfr_counts``) needs it to bound the matrix, so without it nfr_complete would
    be omitted (HUD ``—``) instead of the correct ``0/0`` for an applicable-but-empty ticket."""
    if _has_heading(plan_text, matrix_heading):
        return plan_text
    parts = [matrix_heading, "", _SECTION_NOTE, "",
             "| " + " | ".join(columns) + " |",
             "| " + " | ".join(["---"] * len(columns)) + " |"]
    if kind == "nfr":
        parts += ["", "### NFR Validation Strategy", "", "**NFR catalog:** applicable."]
    parts += ["", ""]
    block = "\n".join(parts)
    lines = plan_text.splitlines(keepends=True)
    mask = _fence_mask([ln.rstrip("\n") for ln in lines])
    anchor = next((i for i, ln in enumerate(lines)
                   if not mask[i] and ln.strip() == "## Testing Strategy"), None)
    if anchor is not None:
        return "".join(lines[:anchor]) + block + "".join(lines[anchor:])
    return plan_text.rstrip("\n") + "\n\n" + block


def migrate_text(plan_text: str, *, uat_slice_text: str | None,
                 nfr_slice_text: str | None, test_slice_text: str | None = None,
                 key: str = "TICKET", include_test: bool = False):
    """Pure migration. Returns the legacy 4-tuple by default.

    With ``include_test=True``, returns
    (new_plan, new_uat_slice, new_nfr_slice, new_test_slice, report).
    A slice stays None when there is nothing to seed into it.
    """
    report = dict(_EMPTY_REPORT)
    new_plan = plan_text
    slices = {"uat": uat_slice_text, "nfr": nfr_slice_text, "test": test_slice_text}
    for kind in ("nfr", "uat", "test"):  # NFR first; section edits to new_plan accumulate
        matrix_heading, index_heading, label = _KIND[kind]
        slice_text = slices[kind]
        matrix = _extract_matrix(new_plan, matrix_heading)
        has_index = slice_text is not None and _has_heading(slice_text, index_heading)
        if matrix is not None and matrix[1] and not has_index:
            header, rows = matrix
            width = len(header) - 1
            # The source matrix must be canonical: the right column COUNT and a trailing
            # ``Status`` column. This catches a header missing Status (which would otherwise
            # drop a real data column into the index). Column LABELS may vary (e.g.
            # "Test / Evidence" vs the template's "Test(s) / Evidence") — only count + the
            # Status anchor are enforced.
            expected_cols = len(_MATRIX_COLUMNS[kind])
            if len(header) != expected_cols or header[-1].strip().lower() != "status":
                report[f"{kind}_skipped"] = (
                    f"unseedable-matrix: noncanonical header "
                    f"(need {expected_cols} cols ending in Status)")
                continue
            # No dead seeds: source rows must be EXACT canonical width, and the candidate
            # index must pass the rebuild's own validation BEFORE anything is written.
            if any(len(r) != len(header) for r in rows):
                report[f"{kind}_skipped"] = "unseedable-matrix: a row is not exact canonical width"
                continue
            candidate = [r[:width] for r in rows]
            reason = validate_index_rows(candidate, width, kind)
            if reason is not None:
                report[f"{kind}_skipped"] = f"unseedable-matrix: {reason}"
                continue
            section = _seed_index_section(index_heading, header, candidate)
            if slice_text is None:
                slices[kind] = (f"# {key}: ticket-local {label} "
                                f"(migrated by /jPrecompact — AC-16)\n\n{section}")
            else:
                slices[kind] = slice_text.rstrip("\n") + "\n\n" + section
            report[f"{kind}_seeded"] = True
        elif matrix is None and has_index:
            new_plan = _ensure_matrix_section(new_plan, matrix_heading, _MATRIX_COLUMNS[kind], kind)
            report[f"{kind}_section_added"] = True
        elif (kind != "test" and not has_index and (matrix is None or not matrix[1])
              and _is_applicable(plan_text, kind)):
            # AC-17 SCAFFOLD: a declared-applicable ticket with NEITHER matrix rows NOR a slice
            # index (e.g. a legacy ticket) — create the empty structure so it self-heals to current
            # standards; the agent then authors the rows. (Applicability is read, never guessed.)
            if matrix is None:
                new_plan = _ensure_matrix_section(new_plan, matrix_heading, _MATRIX_COLUMNS[kind], kind)
                report[f"{kind}_section_added"] = True
            starter = _starter_index_section(index_heading, _MATRIX_COLUMNS[kind], kind)
            if slice_text is None:
                slices[kind] = (f"# {key}: ticket-local {label} "
                                f"(scaffolded by /jPrecompact — AC-17)\n\n{starter}")
            else:
                slices[kind] = slice_text.rstrip("\n") + "\n\n" + starter
            report[f"{kind}_scaffolded"] = True
    if include_test:
        return new_plan, slices["uat"], slices["nfr"], slices["test"], report
    return new_plan, slices["uat"], slices["nfr"], report


# ── IO layer (fail-open, no-clobber on unreadable) ───────────────────────────

def _read_slice(path: Path):
    """Return (text, ok). ok is False only when the file EXISTS but is unreadable — then the
    caller must NOT seed into it (don't risk clobbering unknown content)."""
    p = Path(path)
    if not p.exists():
        return None, True
    try:
        return p.read_text(encoding="utf-8"), True
    except (OSError, UnicodeDecodeError):
        return None, False


def migrate_files(plan_path: Path, uat_slice: Path, nfr_slice: Path, test_slice: Path | None,
                  key: str) -> dict:
    try:
        plan_text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        report = dict(_EMPTY_REPORT)
        report.update(changed=False, reason="unreadable-plan")
        return report
    uat_text, uat_ok = _read_slice(uat_slice)
    nfr_text, nfr_ok = _read_slice(nfr_slice)
    test_text, test_ok = _read_slice(test_slice) if test_slice is not None else (None, True)
    # An existing-but-unreadable slice must NOT be seeded into (could clobber unknown content);
    # feed a synthetic "already has the index heading" so migrate_text skips seeding that kind.
    new_plan, new_uat, new_nfr, new_test, report = migrate_text(
        plan_text,
        uat_slice_text=uat_text if uat_ok else (_UAT_INDEX_HEADING + "\n"),
        nfr_slice_text=nfr_text if nfr_ok else (_NFR_INDEX_HEADING + "\n"),
        test_slice_text=test_text if test_ok else (_TEST_INDEX_HEADING + "\n"),
        key=key,
        include_test=True)
    if not uat_ok:
        report["uat_skipped"] = "unreadable-slice"
    if not nfr_ok:
        report["nfr_skipped"] = "unreadable-slice"
    if not test_ok:
        report["test_skipped"] = "unreadable-slice"
    changed = False
    if new_plan != plan_text:
        Path(plan_path).write_text(new_plan, encoding="utf-8"); changed = True
    if uat_ok and new_uat is not None and new_uat != uat_text:
        Path(uat_slice).parent.mkdir(parents=True, exist_ok=True)
        Path(uat_slice).write_text(new_uat, encoding="utf-8"); changed = True
    if nfr_ok and new_nfr is not None and new_nfr != nfr_text:
        Path(nfr_slice).parent.mkdir(parents=True, exist_ok=True)
        Path(nfr_slice).write_text(new_nfr, encoding="utf-8"); changed = True
    if test_slice is not None and test_ok and new_test is not None and new_test != test_text:
        Path(test_slice).parent.mkdir(parents=True, exist_ok=True)
        Path(test_slice).write_text(new_test, encoding="utf-8"); changed = True
    report["changed"] = changed
    return report


def _resolve(args: argparse.Namespace):
    if args.plan:
        plan = Path(args.plan)
        key = _ticket_from_name(plan.name)
        d = plan.parent / (key or "")
        return (
            plan,
            d / f"{key}.uat-scenarios.md",
            d / f"{key}.nfr.md",
            d / f"{key}.regression-tests.md",
            key,
            None,
        )
    if args.ticket:
        if not _TICKET_RE.match(args.ticket):
            return None, None, None, None, args.ticket, "invalid ticket key"
        plans = Path(args.repo_root or ".") / ".jswarm" / "plans"
        matches = sorted(plans.glob(f"{args.ticket}.plan.*.md"))
        if len(matches) > 1:
            return None, None, None, None, args.ticket, "ambiguous: multiple matching plans"
        if not matches:
            return None, None, None, None, args.ticket, None
        d = plans / args.ticket
        return (
            matches[0],
            d / f"{args.ticket}.uat-scenarios.md",
            d / f"{args.ticket}.nfr.md",
            d / f"{args.ticket}.regression-tests.md",
            args.ticket,
            None,
        )
    return None, None, None, None, None, None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="precompact-migrate")
    parser.add_argument("--plan", help="Path to the plan file")
    parser.add_argument("--ticket", help="Ticket key used to locate the plan + slices")
    parser.add_argument("--repo-root", default=".", help="Repository root used with --ticket")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0

    plan_path, uat_slice, nfr_slice, test_slice, key, reason = _resolve(args)
    display = key or "-"
    if plan_path is None or not Path(plan_path).exists():
        suffix = f" ({reason})" if reason else ""
        print(f"precompact-migrate {display}: no plan{suffix}")
        return 0
    try:
        report = migrate_files(plan_path, uat_slice, nfr_slice, test_slice, key or "TICKET")
    except Exception as exc:  # noqa: BLE001 — live-global path is strictly fail-open
        print(f"precompact-migrate {display}: warning: {exc}")
        return 0
    warn = ""
    for kind in ("uat", "nfr", "test"):
        if report.get(f"{kind}_skipped"):
            warn += f" {kind}-skipped({report[f'{kind}_skipped']})"
    print(
        f"precompact-migrate {display}: "
        f"nfr(seeded={report['nfr_seeded']} scaffolded={report['nfr_scaffolded']} "
        f"section_added={report['nfr_section_added']}) "
        f"uat(seeded={report['uat_seeded']} scaffolded={report['uat_scaffolded']} "
        f"section_added={report['uat_section_added']}) "
        f"test(seeded={report['test_seeded']} scaffolded={report['test_scaffolded']} "
        f"section_added={report['test_section_added']}) "
        f"changed={'Y' if report.get('changed') else 'N'}{warn}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
