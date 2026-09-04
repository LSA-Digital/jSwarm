"""Deterministic reconciler: ticket-local result docs → plan matrix Status cells.

Pure core (``parse_results`` / ``reconcile_text``) + a fail-open IO layer
(``reconcile_file`` / ``main``). Fail-safe direction is **under-report**: a row with no
local result is left UNCHANGED — never silently flipped green.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

# Importable both as ``python jswarm/precompact_reconcile/cli.py`` and ``-m``.
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Canonical Jira key — validated before any glob so metacharacters can't match plans.
_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")
# Segmented grammar: dashes ONLY between non-empty alphanumeric segments — admits
# multi-segment ids (UAT-497-RENAMES / NFR-3-BUILD-PIPELINE) but rejects empty segments
# (UAT--, UAT-497-, NFR--) so a malformed id can't become an authoritative/false-green row.
_UAT_ID_RE = re.compile(r"^UAT-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_NFR_ID_RE = re.compile(r"^NFR-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")

_NFR_HEADING = "## A/C-to-NFR Traceability Matrix"
_UAT_HEADING = "## UAT-Scenario Traceability Matrix"
_TEST_HEADING = "## A/C-to-Test Traceability Matrix"

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

from plan_status import ladder as L

_EMPTY_REPORT = {
    "nfr_matched": 0, "nfr_unmatched": 0,
    "uat_matched": 0, "uat_unmatched": 0,
    "test_matched": 0, "test_unmatched": 0,
}


# ── pure helpers ─────────────────────────────────────────────────────────────

def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _norm_id(cell: str) -> str:
    """Strip markdown bold + code-span backticks + surrounding whitespace from an id cell.

    Result docs written in the natural markdown style wrap ids in code spans
    (`` `UAT-497-RENAMES` ``); without stripping the backticks the anchored id regex
    never matches, silently zeroing the result map (COM-167 follow-up defect, Bug 1).
    NFKC-normalized first so fullwidth/compatibility glyphs fold to ASCII.
    """
    cell = unicodedata.normalize("NFKC", cell)
    return cell.strip().strip("*").strip("`").strip()


# COM-167 R1 (retro 2026-06-22 "silent 0/N from un-gated inputs"). Real authoring deviates
# from the canonical id cell in two mechanically-strippable ways the engine must TOLERATE
# (key/match on the id) while leaving the human cell content untouched:
#   * UAT: the id is glued to a title — "UAT-1a Crash-after-checkpoint resume preserves work".
#   * NFR: the ref is decorated — "**NFR-029-...** (new)".
# The id must still LEAD the cell and be a COMPLETE token — the `(?=\s|$)` lookahead requires
# the id to be followed by whitespace (a glued title) or end-of-cell, so a dangling-dash
# malformed id ("UAT-497-", "NFR-3-") is still rejected, not silently truncated to a key.
# A cell with no leading complete id ⇒ None ⇒ rejected (never a garbage key — fail-safe).
#
# Deliberately a PERMISSIVE SUPERSET of the COM-198 canonical id grammar
# (docs/standards/catalog-id-convention.md; enforced by jswarm/nfr-catalog/nfr_common._REF_RE
# at catalog-validate / strict-link time, and at authoring time by the /jPlan gate — R2).
# The RECONCILE engine must NOT enforce that grammar: it keys/matches on whatever id-shaped
# token leads the cell so that EVERY form reconciles — the new ticket-prefix refs
# (NFR-198, NFR-198-2-DESC, UAT-324-7A), the legacy global-sequential (NFR-001-DESC) AND bare
# legacy ids (hse NFR-4). The strict canonical _REF_RE rejects NFR-198 / NFR-4, so reusing it
# here would re-introduce the silent 0/N this fix (COM-167 R1) exists to close.
_UAT_ID_LEAD_RE = re.compile(r"UAT-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?=\s|$)")
_NFR_ID_LEAD_RE = re.compile(r"NFR-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?=\s|$)")
_NFR_DECORATION_RE = re.compile(
    r"\s*\((?:new|modified|updated|extended|deprecated|revised)\)\s*$", re.IGNORECASE)


def extract_uat_id(cell: str) -> str | None:
    """The leading canonical UAT id of a matrix/index cell, tolerating a glued title and
    surrounding markdown. 'UAT-1a Crash…' → 'UAT-1a'; 'UAT-497-RENAMES' → itself. Returns
    None when no id leads the cell (so a no-id row is rejected, never keyed on garbage)."""
    m = _UAT_ID_LEAD_RE.match(_norm_id(cell))
    return m.group(0) if m else None


def extract_nfr_ref(cell: str) -> str | None:
    """The canonical NFR ref of a matrix/index cell, tolerating a trailing decoration
    annotation ('(new)'/'(modified)'/…) and bold/code-span markup. '**NFR-029-…** (new)' →
    'NFR-029-…'. Returns None when no NFR ref leads the cell."""
    s = _NFR_DECORATION_RE.sub("", _norm_id(cell)).strip("*").strip()
    m = _NFR_ID_LEAD_RE.match(s)
    return m.group(0) if m else None


_FAIL_WORD_RE = re.compile(r"\bfail(?:ed|ing|ure|ures)?\b")
_PASS_TOKENS = {"pass", "passed", "passing"}
_DONE_KEYWORDS = {
    "done", "approved", "owner-approved", "owner approved", "signoff", "signed off",
    "sign-off", "accepted",
}
_DONE_NEGATOR_RE = re.compile(
    r"(?:\b(?:not|no|never|pending|awaiting|unapproved|blocked|partial|tbd|wip)\b|n't)"
)
_READY_KEYWORDS = {
    "ready", "uat passed", "uat-passed", "nfr met", "nfr-met", "measured", "verified",
}
_DRAFTED_KEYWORDS = {
    "drafted", "integration green", "integration-green", "unit green", "unit-green", "dev green",
}
# Affirmative trailers permitted after a leading pass emoji or a pass word. EVERY alpha word
# in the cell must be in this set (or there must be none) for a pass — so "✅ Done"/"✅ complete"
# pass, but "✅ not complete"/"✅ stalled"/"PASS pending" do NOT (review rounds 5–7).
_PASS_ALLOWLIST = _PASS_TOKENS | {
    "done", "complete", "completed", "ok", "okay", "green", "yes", "success", "succeeded",
    "resolved", "resolve", "verified", "fixed", "working", "shipped", "merged", "accepted", "owner",
}
# Emoji vocabulary — fail-safe direction (a fail signal always wins over a pass signal).
# ✖ covers ✖️ (U+2716 + VS16); 🟡/⬜ (in-progress/unknown) are deliberately in NEITHER set.
_FAIL_EMOJI = ("🔴", "❌", "✖", "✗")
_PASS_EMOJI = ("✅", "✔")
# In-progress / neutral markers — never pass, never fail; their presence makes a cell
# ambiguous so it stays unmatched even if a pass emoji also appears (review round-6).
_NEUTRAL_EMOJI = ("⬜", "⚪")


def _norm_result(cell: str) -> str | None:
    """Normalize a result cell to a ladder target, 'fail', or None (unrecognized).

    Fail-safe (never a false-green), in priority order:
      1. ANY failure signal — a fail emoji (🔴/❌/✖/✗) or a fail word anywhere — ⇒ 'fail'.
      2. Neutral marker (⬜/⚪), or 🟡/🟠 with no explicit ladder keyword, ⇒ None.
      3. Explicit Done/Approved ⇒ 'done'.
      4. Explicit Ready ⇒ 'ready'.
      5. Explicit Drafted ⇒ 'drafted'.
      6. Bare automated PASS (✅/✔/pass/passed/passing) ⇒ 'ready'. Automation tops at Ready.
      7. Anything else ⇒ None (unmatched ⇒ row unchanged).

    NFKC-normalized first so fullwidth/compatibility text (e.g. "ＦＡＩＬ") cannot smuggle a
    failure past the detector behind a leading pass emoji (review round-10).
    """
    cell = unicodedata.normalize("NFKC", cell)
    stripped = cell.lstrip()
    low = cell.lower()
    low_stripped = low.strip()
    if any(e in cell for e in _FAIL_EMOJI) or _FAIL_WORD_RE.search(low):
        return L.FAIL
    if any(e in cell for e in _NEUTRAL_EMOJI):
        return None

    def has_keyword(keywords: set[str]) -> bool:
        return any(re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", low) for keyword in keywords)

    has_ready_keyword = has_keyword(_READY_KEYWORDS)
    has_drafted_keyword = has_keyword(_DRAFTED_KEYWORDS)
    if "🟡" in cell and not (stripped == "🟡" or has_ready_keyword):
        return None
    if "🟠" in cell and not (stripped == "🟠" or has_drafted_keyword):
        return None

    done_tokens = [keyword for keyword in _DONE_KEYWORDS
                   if re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", low)]
    if done_tokens and not _DONE_NEGATOR_RE.search(low):
        # Owner-approval is affirmative-only. Remove explicit approval tokens + optional
        # leading green emoji + benign punctuation; any residual prose/glyph outside the
        # affirmative pass allowlist makes the cell ambiguous (unchanged), never Done.
        residual = low
        residual = residual.replace("🟢", " ")
        for e in _PASS_EMOJI:
            residual = residual.replace(e, " ")
        for token in sorted(done_tokens, key=len, reverse=True):
            residual = re.sub(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", " ", residual)
        residual = re.sub(r"[\s.,;:!?()\[\]{}/\\|\-–—_*`\"'’️]+", " ", residual).strip()
        if not residual:
            return L.DONE
        if re.fullmatch(r"[a-z ]+", residual) and all(w in _PASS_ALLOWLIST for w in residual.split()):
            return L.DONE
    if stripped.startswith("🟢"):
        return L.READY
    if stripped.startswith("🟡") or has_ready_keyword:
        return L.READY
    if stripped.startswith("🟠") or has_drafted_keyword:
        return L.DRAFTED
    if not (stripped.startswith(_PASS_EMOJI) or low_stripped in _PASS_TOKENS):
        return None
    # Unambiguous pass: after removing pass emoji + benign punctuation, ONLY affirmative words
    # may remain. ANY leftover character — a digit (partial "2/3 passed", review round-11) or
    # any other symbol/emoji (⏳ pending, 🔜, %, …, review round-12) — makes the cell ambiguous
    # ⇒ None. Categorical, so it needs no denylist of every pending glyph; affirmative words
    # must all be in the allowlist (no contradicting prose, review rounds 5–7).
    residual = low
    for e in _PASS_EMOJI:
        residual = residual.replace(e, " ")
    residual = re.sub(r"[\s.,;:!?()\[\]{}/\\|\-–—_*`\"'’️]+", " ", residual).strip()
    if not residual:
        return L.READY
    if re.fullmatch(r"[a-z ]+", residual) and all(w in _PASS_ALLOWLIST for w in residual.split()):
        return L.READY
    return None


_RESULT_HEADERS = ("result", "status", "outcome")
_SEP_CHARS = set("-:")
_TEST_AC_HEADERS = {"a c", "a/c", "ac"}
_TEST_PATH_HEADERS = {"test path"}
_TEST_CHECK_HEADERS = {"test name evidence check", "test name / evidence check", "test name/evidence check"}


def _header_index(headers: list[str], aliases: set[str]) -> int | None:
    matches = [i for i, header in enumerate(headers) if _canonical_header(header) in aliases]
    return matches[0] if len(matches) == 1 else None


def _canonical_header(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().lower()
    return re.sub(r"[^a-z0-9/]+", " ", value).strip()


def _test_tuple(cells: list[str], *, ac_idx: int = 0, path_idx: int = 1, check_idx: int = 2) -> tuple[str, str, str]:
    ac = _norm_id(cells[ac_idx]) if ac_idx < len(cells) else ""
    path = _normalize_path(cells[path_idx]) if path_idx < len(cells) else ""
    check = _norm_id(cells[check_idx]) if check_idx < len(cells) else ""
    return (ac, path, check)


def _parse_test_results(doc_text: str) -> dict[tuple[str, str, str], str]:
    seen: dict[tuple[str, str, str], set[str]] = {}
    tainted: set[tuple[str, str, str]] = set()
    result_col: int | None = None
    ac_col: int | None = None
    path_col: int | None = None
    check_col: int | None = None
    header_ncols = 0
    after_sep = False
    header_cells: list[str] | None = None
    for line in doc_text.splitlines():
        if not line.lstrip().startswith("|"):
            result_col, ac_col, path_col, check_col, header_ncols, after_sep, header_cells = \
                None, None, None, None, 0, False, None
            continue
        cells = _cells(line)
        if len(cells) < 4:
            continue
        if all(c and set(c) <= _SEP_CHARS for c in cells):
            if header_cells is None:
                result_col, ac_col, path_col, check_col, header_ncols, after_sep = \
                    None, None, None, None, 0, False
                continue
            lowers = [_canonical_header(c) for c in header_cells]
            result_cols = [i for i, c in enumerate(lowers) if c in _RESULT_HEADERS]
            result_col = result_cols[0] if len(result_cols) == 1 else None
            ac_col = _header_index(header_cells, _TEST_AC_HEADERS)
            path_col = _header_index(header_cells, _TEST_PATH_HEADERS)
            check_col = _header_index(header_cells, _TEST_CHECK_HEADERS)
            header_ncols = len(header_cells)
            after_sep = True
            header_cells = None
            continue
        if not after_sep:
            header_cells = cells
            continue
        if result_col is None or ac_col is None or path_col is None or check_col is None:
            continue
        if len(cells) != header_ncols:
            continue
        key = _test_tuple(cells, ac_idx=ac_col, path_idx=path_col, check_idx=check_col)
        if not all(key):
            continue
        verdict = _norm_result(cells[result_col])
        if verdict == L.READY:
            verdict = L.DONE
        if verdict in (L.DONE, L.DRAFTED, L.FAIL):
            seen.setdefault(key, set()).add(verdict)
        else:
            tainted.add(key)
    return {key: next(iter(results)) for key, results in seen.items()
            if len(results) == 1 and key not in tainted}


def parse_results(doc_text: str, kind: str) -> dict:
    """Map id → ladder target or 'fail' from a result doc's markdown tables.

    Header detection is anchored to the markdown SEPARATOR row (``|---|---|``): the row
    immediately above it is the header, every row below it is data. This is the real table
    structure, so an id-bearing DATA row is never mistaken for a header (review round-2) and
    a header is never parsed as data (review HIGH-1).

    Result column, per table:
      * If the header NAMES a Result/Status/Outcome column, that column is the SOLE source —
        an unrecognized value there is left unmatched, never scavenged from another column
        (COM-167 follow-up defect Bug 2: a ``… | Result | Last tested | Evidence |`` row must
        read Result, not the trailing Evidence/date). Absent on a short row ⇒ unmatched
        (review HIGH-2).
      * Otherwise only an UNAMBIGUOUS two-column row (id + exactly one cell) is read; a wider
        unlabelled row could hide the result behind an evidence/date column, so it is left
        unmatched rather than scavenged (no false-green). The minimal ``| Scenario | Result |``
        ledger parses either way.
    Rows without both an id cell and a recognizable result are ignored.
    """
    if kind == "test":
        return _parse_test_results(doc_text)
    if kind == "uat":
        id_re = _UAT_ID_RE
    elif kind == "nfr":
        id_re = _NFR_ID_RE
    else:
        raise ValueError(f"unknown result kind: {kind}")
    seen: dict[str, set[str]] = {}
    tainted: set[str] = set()  # ids with any ambiguous/in-progress row ⇒ never emitted
    result_col: int | None = None
    header_declared = False  # the header above the separator NAMED a Result column
    header_ncols = 0         # column count of that header (data rows must match it)
    after_sep = False        # we are past the separator ⇒ in the data region
    header_cells: list[str] | None = None  # last pipe row before the separator
    for line in doc_text.splitlines():
        if not line.lstrip().startswith("|"):
            result_col, header_declared, header_ncols, after_sep, header_cells = \
                None, False, 0, False, None
            continue
        cells = _cells(line)
        if len(cells) < 2:
            continue
        if all(c and set(c) <= _SEP_CHARS for c in cells):  # |---|---| separator row
            if header_cells is None:
                # Separator NOT anchored to a fresh header row directly above it (e.g. a
                # second/mid-data separator) ⇒ fail closed: drop any stale header state so
                # the rows below are not parsed under the prior table's header (review rnd-3).
                result_col, header_declared, header_ncols, after_sep = None, False, 0, False
                continue
            lowers = [c.strip().lower() for c in header_cells]
            declared_cols = [i for i, c in enumerate(lowers) if c in _RESULT_HEADERS]
            # Exactly ONE result-like column is authoritative; zero ⇒ 2-col fallback below;
            # MORE than one ⇒ ambiguous (which column is THE result?) ⇒ no declared column, so
            # wide rows fail closed instead of trusting the first (review round-9).
            if len(declared_cols) == 1:
                result_col, header_declared = declared_cols[0], True
            else:
                result_col, header_declared = None, False
            header_ncols = len(header_cells)
            after_sep = True
            header_cells = None
            continue
        if not after_sep:
            header_cells = cells  # candidate header; decided when the separator is seen
            continue
        # ── data region ──
        ident, ident_idx = None, -1
        for i, c in enumerate(cells):
            nc = _norm_id(c)
            if id_re.match(nc):
                ident, ident_idx = nc, i
                break
        if ident is None:
            continue
        verdict: str | None = None
        if header_declared:
            # Declared Result column authoritative: require matching width + a distinct,
            # in-range column, else the positional mapping is unreliable (a dropped column
            # would shift Evidence into the Result slot — review HIGH-2 / rnd-4).
            if len(cells) == header_ncols and result_col is not None \
                    and result_col != ident_idx and result_col < len(cells):
                verdict = _norm_result(cells[result_col])
        elif len(cells) == 2:
            verdict = _norm_result(cells[1 - ident_idx])  # unambiguous id + one cell
        # A clean pass/fail is recorded; ANY other outcome (ambiguous, in-progress, absent,
        # malformed width) TAINTS the id so a later clean row cannot false-green it (rnd-8).
        if verdict in (L.DONE, L.READY, L.DRAFTED, L.FAIL):
            seen.setdefault(ident, set()).add(verdict)
        else:
            tainted.add(ident)
    # An id is emitted only if every recorded row agrees on ONE pass/fail value AND no row for
    # it was ambiguous/in-progress (tainted). Conflicting or contradicted ids drop (fail-safe:
    # never flipped green on contradiction). Same-result repeats collapse to that result.
    return {ident: next(iter(results)) for ident, results in seen.items()
            if len(results) == 1 and ident not in tainted}


def _set_last_cell(line: str, value: str) -> str:
    """Replace the inner text of a table row's last DATA cell, preserving every other
    cell verbatim.

    The last data cell is the last non-empty ``|``-segment — so a row with a trailing
    pipe (``| a | b |`` → empty trailing segment) and one without (``| a | b``) both
    target ``b``, never the id cell (critic-xhigh F3). Returns the line unchanged when
    there is no distinct data cell to set.
    """
    newline = ""
    core = line
    if core.endswith("\n"):
        newline, core = "\n", core[:-1]
    parts = core.split("|")
    if len(parts) < 3:
        return line
    # parts[0] is the leading-pipe empty; a trailing pipe adds a trailing empty. The
    # status column is the LAST real column by POSITION — not the last non-empty segment
    # (an empty status cell must still be targeted, never the id cell; recheck-F1).
    trailing_pipe = parts[-1].strip() == ""
    idx = len(parts) - 2 if trailing_pipe else len(parts) - 1
    if idx < 2:  # need id (≥ parts[1]) + a distinct status column
        return line
    parts[idx] = f" {value} "
    return "|".join(parts) + newline


def _update_row(line: str, section: str, uat_results: dict, nfr_results: dict,
                test_results: dict, report: dict) -> str:
    cells = _cells(line)
    if len(cells) < 2:
        return line  # need an id cell AND a distinct status cell to edit safely
    if section == "uat":
        ident = extract_uat_id(cells[0])  # tolerant: leading id token (R1)
        if ident is None:
            return line  # header / separator / non-data / no-id row
        result, key = uat_results.get(ident), "uat"
    elif section == "nfr":  # nfr — id is the NFR ref in column 2
        ident = extract_nfr_ref(cells[1]) if len(cells) > 1 else None  # tolerant (R1)
        if ident is None:
            return line
        result, key = nfr_results.get(ident), "nfr"
    elif section == "test":
        if all(c and set(c) <= _SEP_CHARS for c in cells):
            return line
        if len(cells) > 4 and _canonical_header(cells[0]) in _TEST_AC_HEADERS \
                and _canonical_header(cells[3]) in _TEST_PATH_HEADERS \
                and _canonical_header(cells[4]) in _TEST_CHECK_HEADERS:
            return line
        tuple_key = _test_tuple(cells, ac_idx=0, path_idx=3, check_idx=4)
        if not all(tuple_key):
            return line
        result, key = test_results.get(tuple_key), "test"
    else:
        return line
    if result is None:
        report[f"{key}_unmatched"] += 1
        return line  # UNCHANGED — never flipped green (fail-safe = under-report)
    report[f"{key}_matched"] += 1
    if result == "pass":  # backward-compatible direct-call alias; parse_results emits READY.
        result = L.READY
    if result == L.FAIL:
        return _set_last_cell(line, L.label_for_weight(0))
    current_weight = L.weight_from_status(cells[-1])
    target_weight = L.TARGET_WEIGHTS.get(result, 0)
    new_weight = max(current_weight, target_weight)
    if new_weight == current_weight:
        return line  # preserve grandfathered labels such as "🟢 Passing" verbatim
    return _set_last_cell(line, L.label_for_weight(new_weight))


def reconcile_text(plan_text: str, *, uat_results: dict, nfr_results: dict,
                   test_results: dict | None = None) -> tuple[str, dict]:
    """Set each NFR / UAT-scenario matrix row's Status cell from its id's local result.

    Section-scoped: rows are only touched inside ``## A/C-to-NFR Traceability Matrix`` and
    ``## UAT-Scenario Traceability Matrix``; any other ``##``/``###`` heading ends the
    active matrix (so the ``### NFR Validation Strategy`` subsection is never edited).
    """
    report = dict(_EMPTY_REPORT)
    test_results = test_results or {}
    section: str | None = None
    out: list[str] = []
    for line in plan_text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("## "):
            if stripped == _NFR_HEADING:
                section = "nfr"
            elif stripped == _UAT_HEADING:
                section = "uat"
            elif stripped == _TEST_HEADING:
                section = "test"
            else:
                section = None
            out.append(line)
            continue
        if stripped.startswith("### "):
            section = None  # a subsection ends the matrix's row region
            out.append(line)
            continue
        if section and stripped.startswith("|"):
            out.append(_update_row(line, section, uat_results, nfr_results, test_results, report))
            continue
        out.append(line)
    return "".join(out), report


# ── IO layer (fail-open) ─────────────────────────────────────────────────────

def _has_table_rows(text: str) -> bool:
    return any(ln.lstrip().startswith("|") for ln in text.splitlines())


def _read_results(path: Path | None, kind: str) -> tuple[dict[str, str], bool]:
    """Return (id→result, unparseable_warn).

    ``unparseable_warn`` is True when the doc EXISTS, is non-empty, and contains a markdown
    table yet yields ZERO parsed ids — the 'present but structurally unreadable' signal that
    must be LOUD, not silent (COM-167 follow-up defect: a silent ``matched=0`` reads exactly
    like a healthy 'already in sync' run, so AC-10 went inert unnoticed for a ticket's life).
    A missing/unreadable/empty/prose-only doc is NOT flagged (fail-open, nothing to read).
    """
    if path is None:
        return {}, False
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}, False  # missing/unreadable asset ⇒ no rows reconciled (fail-safe)
    parsed = parse_results(text, kind)
    warn = (not parsed) and bool(text.strip()) and _has_table_rows(text)
    return parsed, warn


def reconcile_file(plan_path: Path, uat_doc: Path | None, nfr_doc: Path | None,
                   test_doc: Path | None = None) -> dict:
    """Reconcile a plan in place from its local result docs. Writes only when changed."""
    try:
        plan_text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        report = dict(_EMPTY_REPORT)
        report.update(changed=False, reason="unreadable-plan",
                      uat_unparsed=False, nfr_unparsed=False, test_unparsed=False)
        return report
    uat_results, uat_warn = _read_results(uat_doc, "uat")
    nfr_results, nfr_warn = _read_results(nfr_doc, "nfr")
    test_results, test_warn = _read_results(test_doc, "test")
    new_text, report = reconcile_text(
        plan_text, uat_results=uat_results, nfr_results=nfr_results, test_results=test_results,
    )
    changed = new_text != plan_text
    if changed:
        Path(plan_path).write_text(new_text, encoding="utf-8")
    report["changed"] = changed
    report["uat_unparsed"] = uat_warn
    report["nfr_unparsed"] = nfr_warn
    report["test_unparsed"] = test_warn
    return report


def _ticket_from_name(name: str) -> str | None:
    m = re.match(r"^([A-Z][A-Z0-9]+-[0-9]+)\.plan\.", name)
    return m.group(1) if m else None


# HAS-525 T5.1 — canonical UAT step-script name, with legacy read-compat. Writers/resolvers
# prefer the new suffix; a ticket folder that has not migrated yet still resolves via the
# legacy suffix so its existing result doc keeps reconciling (read-both/write-new; never
# mass-rename historical ticket folders).
UAT_DOC_SUFFIX = ".uat-scenario-steps.md"
_LEGACY_UAT_DOC_SUFFIX = ".uat-test.md"


def resolve_uat_doc(directory: Path, ticket: str) -> Path:
    """Resolve a ticket's UAT result doc: canonical name first, legacy name if that's what
    exists on disk. Returns the canonical path when neither exists (nothing to read yet)."""
    canonical = directory / f"{ticket}{UAT_DOC_SUFFIX}"
    if canonical.exists():
        return canonical
    legacy = directory / f"{ticket}{_LEGACY_UAT_DOC_SUFFIX}"
    if legacy.exists():
        return legacy
    return canonical


def _resolve(args: argparse.Namespace):
    """Return (plan_path, uat_doc, nfr_doc, test_doc, key, reason). plan_path is None ⇒ no write."""
    if args.plan:
        plan = Path(args.plan)
        key = _ticket_from_name(plan.name)
        d = plan.parent / (key or "")
        return (
            plan,
            resolve_uat_doc(d, key or ""),
            d / f"{key}.nfr-test.md",
            d / f"{key}.regression-test.md",
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
            resolve_uat_doc(d, args.ticket),
            d / f"{args.ticket}.nfr-test.md",
            d / f"{args.ticket}.regression-test.md",
            args.ticket,
            None,
        )
    return None, None, None, None, None, None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="precompact-reconcile")
    parser.add_argument("--plan", help="Path to the plan file")
    parser.add_argument("--ticket", help="Ticket key used to locate the plan + result docs")
    parser.add_argument("--repo-root", default=".", help="Repository root used with --ticket")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0

    plan_path, uat_doc, nfr_doc, test_doc, key, reason = _resolve(args)
    display = key or "-"
    if plan_path is None or not Path(plan_path).exists():
        suffix = f" ({reason})" if reason else ""
        print(f"precompact-reconcile {display}: no plan{suffix}")
        return 0
    try:
        report = reconcile_file(plan_path, uat_doc, nfr_doc, test_doc)
    except Exception as exc:  # noqa: BLE001 — live-global path is strictly fail-open
        print(f"precompact-reconcile {display}: warning: {exc}")
        return 0
    warns = []
    if report.get("uat_unparsed"):
        warns.append("uat-doc-present-but-unparseable")
    if report.get("nfr_unparsed"):
        warns.append("nfr-doc-present-but-unparseable")
    if report.get("test_unparsed"):
        warns.append("test-doc-present-but-unparseable")
    warn_suffix = f" warning:{','.join(warns)}" if warns else ""
    print(
        f"precompact-reconcile {display}: "
        f"nfr(matched={report['nfr_matched']} unmatched={report['nfr_unmatched']}) "
        f"uat(matched={report['uat_matched']} unmatched={report['uat_unmatched']}) "
        f"test(matched={report['test_matched']} unmatched={report['test_unmatched']}) "
        f"changed={'Y' if report.get('changed') else 'N'}{warn_suffix}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
