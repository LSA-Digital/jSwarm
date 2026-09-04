"""COM-167 AC-11 — /precompact-time ROW-REBUILD of the count-bearing matrices.

Regenerates each plan traceability matrix's DATA rows from the ticket-local working
slices — the UAT-Scenario matrix from ``KEY.uat-scenarios.md`` and the A/C-to-NFR matrix
from ``KEY.nfr.md`` — so an agent never hand-authors matrix rows. This runs BEFORE the
AC-10 status reconcile (``matrices.py``) and the AC-9 count (``update_plan``).

The slice carries a fixed-format **index table** under a canonical heading (``## UAT
Scenario Index`` / ``## A/C-to-NFR Index``) whose columns are exactly the plan matrix
columns minus Status. Rebuild = regenerate the matrix data rows from that index, pure
pass-through of the index cells + a Status cell:

  * ADD rows newly in the index (Status defaults to 🔴 — never green).
  * DROP plan rows whose key is no longer in the index.
  * PRESERVE the existing Status cell for a surviving key (so a row already reconciled to
    🟢 is NOT reset). UAT key = scenario id (col 0); NFR key = (A/C, NFR ref) pair.

Fail-safe: an EMPTY index (missing/absent slice) leaves that matrix UNCHANGED — a missing
slice never wipes the plan's rows. Idempotent; fail-open (always exits 0).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Importable both as ``python jswarm/precompact_reconcile/rows_cli.py`` and ``-m``.
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from precompact_reconcile.matrices import (  # noqa: E402
    _NFR_ID_RE, _TICKET_RE, _cells, _norm_id, _ticket_from_name,
    extract_nfr_ref, extract_uat_id,
)

_TEST_CATALOG_DIR = str(Path(__file__).resolve().parents[1] / "test-catalog")
if _TEST_CATALOG_DIR not in sys.path:
    sys.path.insert(0, _TEST_CATALOG_DIR)
try:  # pragma: no cover - exercised when the enterprise test-catalog/ tree is present
    from markdown_tables import normalize_path as _normalize_path  # noqa: E402
except ImportError:  # markdown_tables lives under test-catalog/, which is enterprise-
    # only and not part of this repo. Its normalize_path is a trivial, generic cell-
    # value cleanup (strip whitespace/backticks, drop a leading "./") with no
    # test-catalog-specific behavior, so the guard is a full local equivalent rather
    # than a degraded stub -- this module stays fully correct without the dependency.
    def _normalize_path(value: str) -> str:  # type: ignore[no-redef]
        text = value.strip().strip("`")
        while text.startswith("./"):
            text = text[2:]
        return text

_UAT_MATRIX = "## UAT-Scenario Traceability Matrix"
_NFR_MATRIX = "## A/C-to-NFR Traceability Matrix"
_TEST_MATRIX = "## A/C-to-Test Traceability Matrix"
_UAT_INDEX_HEADING = "## UAT Scenario Index"
_NFR_INDEX_HEADING = "## A/C-to-NFR Index"
_TEST_INDEX_HEADING = "## A/C-to-Test Index"

# Fail-safe default for a brand-new (not-yet-reconciled) row: never green.
_NEW_STATUS = "🔴 Backlogged"

# Segmented grammar (matches matrices._UAT_ID_RE): dashes only between non-empty segments —
# admits UAT-497-RENAMES, rejects UAT-- / UAT-497- so a malformed id can't be authoritative.
_UAT_ID_RE = re.compile(r"^UAT-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_SEP_CELL_RE = re.compile(r"^:?-+:?$")
_HEADING_RE = re.compile(r"^#{2,6}\s")            # any ##..###### heading ends the search
_EMPTY_REPORT = {
    "uat_added": 0, "uat_dropped": 0, "uat_kept": 0, "uat_skipped": None,
    "nfr_added": 0, "nfr_dropped": 0, "nfr_kept": 0, "nfr_skipped": None,
    "test_added": 0, "test_dropped": 0, "test_kept": 0, "test_skipped": None,
}


# ── pure helpers ─────────────────────────────────────────────────────────────

def _is_separator(cells: list[str]) -> bool:
    nonempty = [c for c in cells if c != ""]
    return bool(nonempty) and all(_SEP_CELL_RE.match(c) for c in nonempty)


def _fence_mask(lines: list[str]) -> list[bool]:
    """Per-line mask: True when a line is inside (or is a delimiter of) a ``` / ~~~ code
    fence. Shared by ``parse_index`` and the AC-16 migrate seeder so a fenced heading is
    never mistaken for a real one (and a real index appended below a fenced example is still
    found)."""
    mask = [False] * len(lines)
    in_fence = False
    fence = ""
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            if not in_fence:
                in_fence, fence, mask[i] = True, s[:3], True
                continue
            if s.startswith(fence):
                mask[i], in_fence, fence = True, False, ""
                continue
        mask[i] = in_fence
    return mask


def _uat_key(cells: list[str]):
    return (extract_uat_id(cells[0]) or "") if cells else ""  # tolerant leading-id key (R1)


def _nfr_key(cells: list[str]):
    ac = _norm_id(cells[0]) if len(cells) > 0 else ""
    ref = (extract_nfr_ref(cells[1]) or "") if len(cells) > 1 else ""  # tolerant ref key (R1)
    return (ac, ref)


def _test_key(cells: list[str]):
    ac = _norm_id(cells[0]) if len(cells) > 0 else ""
    path = _normalize_path(cells[3]) if len(cells) > 3 else ""
    check = _norm_id(cells[4]) if len(cells) > 4 else ""
    return (ac, path, check)


def _key_fn(kind: str):
    if kind == "uat":
        return _uat_key
    if kind == "nfr":
        return _nfr_key
    if kind == "test":
        return _test_key
    raise ValueError(f"unknown matrix kind: {kind}")


def _validate_index(index_rows: list[list[str]], expected_width: int, kind: str) -> str | None:
    """Return None when the index is a well-formed fixed-format table, else a one-line
    reason. A malformed/duplicate index is REJECTED (caller leaves the matrix unchanged)
    rather than spliced — so a bad slice edit can never wipe rows or create a false-green
    via an inherited status on a duplicate key. Rules: every row exactly ``expected_width``
    cells (= matrix columns minus Status — no pad/truncate); non-empty + well-formed key;
    no duplicate keys."""
    if expected_width < 1:
        return "matrix has no data columns"
    seen: set = set()
    for n, cells in enumerate(index_rows, 1):
        if len(cells) != expected_width:
            return f"row {n}: {len(cells)} cells, expected {expected_width}"
        if kind == "uat":
            ident = extract_uat_id(cells[0])  # tolerant: glued title accepted (R1)
            if ident is None:
                return f"row {n}: bad UAT scenario id {cells[0]!r}"
            key = ident
        elif kind == "nfr":
            ac, ref = _norm_id(cells[0]), extract_nfr_ref(cells[1])  # tolerant ref (R1)
            if not ac:
                return f"row {n}: empty A/C cell"
            if ref is None:
                return f"row {n}: bad NFR ref {cells[1]!r}"
            key = (ac, ref)
        elif kind == "test":
            ac, path, check = _norm_id(cells[0]), _normalize_path(cells[3]), _norm_id(cells[4])
            if not ac:
                return f"row {n}: empty A/C cell"
            if not path:
                return f"row {n}: empty test path"
            if not check:
                return f"row {n}: empty test name / evidence check"
            key = (ac, path, check)
        else:
            return f"unknown matrix kind: {kind}"
        if key in seen:
            return f"row {n}: duplicate key {key!r}"
        seen.add(key)
    return None


def parse_index(slice_text: str, heading: str) -> list[list[str]]:
    """Return the data rows (each a list of cells) of the first markdown table under the
    canonical ``heading`` in a working slice. Header + separator rows are dropped. Returns
    ``[]`` when the heading or its table is absent (⇒ matrix left untouched downstream)."""
    lines = slice_text.splitlines()
    mask = _fence_mask(lines)            # ignore a fenced EXAMPLE heading; find the REAL one
    i = next((n for n in range(len(lines))
              if not mask[n] and lines[n].strip() == heading), None)
    if i is None:
        return []
    i += 1
    rows: list[list[str]] = []
    header_consumed = False
    table_started = False
    while i < len(lines):
        if mask[i]:
            i += 1                       # skip lines inside a code fence
            continue
        s = lines[i].strip()
        if s.startswith("|"):
            cells = _cells(lines[i])
            if not header_consumed:
                header_consumed = True   # first pipe row = column header → drop
                table_started = True
            elif _is_separator(cells):
                pass                     # separator row → drop
            else:
                rows.append(cells)
            i += 1
            continue
        if s.startswith("## ") or s.startswith("### "):
            break                        # next section ends the search
        if table_started:
            break                        # blank/prose after the table ends it
        i += 1                           # still scanning for the table
    return rows


def validate_index_rows(index_rows: list[list[str]], expected_width: int, kind: str) -> str | None:
    """Public wrapper over the rebuild's index validation, so any producer of an index (e.g.
    the AC-16 migrate seeder) can check that what it writes WILL be accepted by the rebuild —
    no dead seeds. Returns None when valid, else a one-line reason."""
    return _validate_index(index_rows, expected_width, kind)


def _rebuild_matrix(lines: list[str], heading: str, index_rows: list[list[str]], kind: str):
    """Replace the data rows of the matrix under ``heading`` with rows derived from
    ``index_rows`` (Status preserved by key, new rows defaulted 🔴). Header/separator/
    everything else preserved verbatim. Returns (new_lines, added, dropped, kept, skipped):
    ``skipped`` is None on success, else a one-line reason the matrix was left UNCHANGED
    (no table found, or a malformed/duplicate index — fail-safe, never destructive)."""
    key_fn = _key_fn(kind)
    h = next((i for i, ln in enumerate(lines) if ln.strip() == heading), None)
    if h is None:
        return lines, 0, 0, 0, None  # matrix section absent ⇒ nothing to rebuild
    # find the table header — stop at ANY heading (## .. ######) before a table is seen, so a
    # missing/malformed matrix table never causes the first `### NFR Validation Strategy`
    # table to be mistaken for the matrix and clobbered (recheck F3).
    header_idx = None
    j = h + 1
    while j < len(lines):
        s = lines[j].strip()
        if _HEADING_RE.match(s):
            break
        if s.startswith("|"):
            header_idx = j
            break
        j += 1
    if header_idx is None:
        return lines, 0, 0, 0, None  # no matrix table to rebuild (fail-safe no-op)
    ncols = len(_cells(lines[header_idx]))
    if ncols < 2:
        return lines, 0, 0, 0, "matrix header has <2 columns"
    # REJECT a malformed/duplicate index BEFORE any splice — leave the matrix unchanged.
    reason = _validate_index(index_rows, ncols - 1, kind)
    if reason is not None:
        return lines, 0, 0, 0, reason
    # skip a separator row immediately after the header
    data_start = header_idx + 1
    if (data_start < len(lines) and lines[data_start].strip().startswith("|")
            and _is_separator(_cells(lines[data_start]))):
        data_start += 1
    # collect existing data rows (for status preservation) up to the first non-table line
    data_end = data_start
    existing: dict = {}
    while data_end < len(lines):
        s = lines[data_end].strip()
        if not s.startswith("|"):
            break
        cells = _cells(lines[data_end])
        if _is_separator(cells):
            break
        existing[key_fn(cells)] = cells[-1] if cells else _NEW_STATUS
        data_end += 1
    # build the new data rows from the (validated, exact-width) index — pure pass-through
    # of the index cells + a Status cell (preserved by key, else the 🔴 default).
    new_rows: list[str] = []
    index_keys: list = []
    for cells in index_rows:
        key = key_fn(cells)
        index_keys.append(key)
        rc = list(cells) + [existing.get(key, _NEW_STATUS)]
        new_rows.append("| " + " | ".join(rc) + " |\n")
    existing_set, index_set = set(existing), set(index_keys)
    added = len(index_set - existing_set)
    dropped = len(existing_set - index_set)
    kept = len(existing_set & index_set)
    return lines[:data_start] + new_rows + lines[data_end:], added, dropped, kept, None


def rebuild_text(plan_text: str, *, uat_index: list[list[str]],
                 nfr_index: list[list[str]],
                 test_index: list[list[str]] | None = None) -> tuple[str, dict]:
    """Rebuild matrices from their indexes. An empty index leaves its matrix
    untouched (fail-safe — a missing slice never wipes rows)."""
    report = dict(_EMPTY_REPORT)
    lines = plan_text.splitlines(keepends=True)
    if nfr_index:
        lines, a, d, k, reason = _rebuild_matrix(lines, _NFR_MATRIX, nfr_index, "nfr")
        report.update(nfr_added=a, nfr_dropped=d, nfr_kept=k, nfr_skipped=reason)
    if uat_index:
        lines, a, d, k, reason = _rebuild_matrix(lines, _UAT_MATRIX, uat_index, "uat")
        report.update(uat_added=a, uat_dropped=d, uat_kept=k, uat_skipped=reason)
    if test_index:
        lines, a, d, k, reason = _rebuild_matrix(lines, _TEST_MATRIX, test_index, "test")
        report.update(test_added=a, test_dropped=d, test_kept=k, test_skipped=reason)
    return "".join(lines), report


# ── IO layer (fail-open) ─────────────────────────────────────────────────────

def _read_index(path: Path | None, heading: str) -> tuple[list[list[str]], str | None]:
    if path is None:
        return [], None
    try:
        return parse_index(Path(path).read_text(encoding="utf-8"), heading), None
    except OSError:
        return [], None  # missing/unreadable slice ⇒ empty index ⇒ matrix untouched (fail-safe)
    except Exception as exc:  # noqa: BLE001 — malformed one slice leaves all matrices fail-open
        return [], str(exc)


def rebuild_file(plan_path: Path, uat_slice: Path | None, nfr_slice: Path | None,
                 test_slice: Path | None = None) -> dict:
    try:
        plan_text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        report = dict(_EMPTY_REPORT)
        report.update(changed=False, reason="unreadable-plan")
        return report
    uat_index, uat_read_error = _read_index(uat_slice, _UAT_INDEX_HEADING)
    nfr_index, nfr_read_error = _read_index(nfr_slice, _NFR_INDEX_HEADING)
    test_index, test_read_error = _read_index(test_slice, _TEST_INDEX_HEADING)
    new_text, report = rebuild_text(
        plan_text,
        uat_index=uat_index,
        nfr_index=nfr_index,
        test_index=test_index,
    )
    if uat_read_error:
        report["uat_skipped"] = uat_read_error
    if nfr_read_error:
        report["nfr_skipped"] = nfr_read_error
    if test_read_error:
        report["test_skipped"] = test_read_error
    changed = new_text != plan_text
    if changed:
        Path(plan_path).write_text(new_text, encoding="utf-8")
    report["changed"] = changed
    return report


def _resolve(args: argparse.Namespace):
    """Return (plan_path, uat_slice, nfr_slice, test_slice, key, reason). plan_path None ⇒ no write."""
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
    parser = argparse.ArgumentParser(prog="precompact-rebuild")
    parser.add_argument("--plan", help="Path to the plan file")
    parser.add_argument("--ticket", help="Ticket key used to locate the plan + working slices")
    parser.add_argument("--repo-root", default=".", help="Repository root used with --ticket")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0

    plan_path, uat_slice, nfr_slice, test_slice, key, reason = _resolve(args)
    display = key or "-"
    if plan_path is None or not Path(plan_path).exists():
        suffix = f" ({reason})" if reason else ""
        print(f"precompact-rebuild {display}: no plan{suffix}")
        return 0
    try:
        report = rebuild_file(plan_path, uat_slice, nfr_slice, test_slice)
    except Exception as exc:  # noqa: BLE001 — live-global path is strictly fail-open
        print(f"precompact-rebuild {display}: warning: {exc}")
        return 0
    warn = ""
    if report.get("uat_skipped"):
        warn += f" warning: uat index invalid ({report['uat_skipped']}) — matrix unchanged"
    if report.get("nfr_skipped"):
        warn += f" warning: nfr index invalid ({report['nfr_skipped']}) — matrix unchanged"
    if report.get("test_skipped"):
        warn += f" warning: test index invalid ({report['test_skipped']}) — matrix unchanged"
    print(
        f"precompact-rebuild {display}: "
        f"uat(added={report['uat_added']} dropped={report['uat_dropped']} kept={report['uat_kept']}) "
        f"nfr(added={report['nfr_added']} dropped={report['nfr_dropped']} kept={report['nfr_kept']}) "
        f"test(added={report['test_added']} dropped={report['test_dropped']} kept={report['test_kept']}) "
        f"changed={'Y' if report.get('changed') else 'N'}{warn}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
