"""Anchor freshness / drift audit: `anchors_verified_or_relocated`.

Resolves every `anchor` record in a seam-spine against a source tree and
assigns exactly one of five drift states:

    verified               -- declared file:line still holds the symbol/probe.
    auto_relocated         -- exactly one high-confidence candidate found
                               elsewhere; old-to-new relocation is reported.
    needs_reselect         -- exactly one low-confidence candidate found
                               (symbol matches but the probe does not); a
                               human must confirm before relocating.
    deleted_or_unresolvable -- no candidate at any confidence level.
    ambiguous              -- more than one candidate at the same confidence
                               tier; never auto-picked.

Portability (NFR-234-003): file discovery opportunistically shells out to
`rg --files` when present on PATH, but every match/verification decision is
made by pure-Python line scanning, so the whole gate runs with `python`
alone. No `colgrep`, `scip`, or `node` dependency is used anywhere in this
module.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = ["AnchorAuditReport", "verify_anchors"]

AUDIT_SCHEMA_VERSION = "code_overview.audit.anchor_freshness.v1"
AUDIT_NAME = "anchors_verified_or_relocated"

# S4.5 (authoritative) / critic-supplement cross-check: anchor drift states
# that require a manual reselect all share strict exit code 4.
_MANUAL_RESELECT_STATES = frozenset({"ambiguous", "deleted_or_unresolvable", "needs_reselect"})

_EXIT_PASS = 0
_EXIT_ANCHOR_DRIFT_MANUAL_RESELECT = 4


def _forbidden_optional_tools() -> frozenset[str]:
    raw = os.environ.get("CODE_OVERVIEW_FORBID_OPTIONAL_TOOLS", "")
    return frozenset(token.strip() for token in raw.split(",") if token.strip())


def _rg_executable() -> str | None:
    if "rg" in _forbidden_optional_tools():
        return None
    return shutil.which("rg")


# S4.1/S4.4: cross-language declaration keywords used to recognize an exact
# symbol match as a declaration line rather than prose/comment usage. `def`
# is included as one member among equals -- an optional stronger signal for
# Python, never the sole detection mechanism (BLOCKING-2, GATE-phase3).
_DECLARATION_KEYWORDS: tuple[str, ...] = (
    "def",
    "function",
    "class",
    "const",
    "let",
    "var",
    "interface",
    "type",
    "struct",
    "fn",
    "func",
    "enum",
    "trait",
    "impl",
    "namespace",
    "module",
)


def _code_only(line: str) -> str:
    """Strip line-comment tails and string-literal contents from `line`.

    Used only to judge whether a declaration-keyword + symbol match sits in
    real code, not prose (GATE-phase3 cycle 2 BLOCKING: a symbol/probe that
    survives solely inside a comment or string literal must not be treated
    as a relocation candidate). This is a lightweight single-pass character
    scan -- not a full tokenizer/parser -- covering `#` and `//` line
    comments and single/double-quoted string contents (with backslash-escape
    awareness while inside a string). Code preceding a comment marker is
    preserved, so a real declaration followed by a trailing comment (e.g.
    `export function Foo() { // note`) still matches on its code portion.
    """
    kept: list[str] = []
    in_string: str | None = None
    index = 0
    length = len(line)
    while index < length:
        char = line[index]
        if in_string is not None:
            if char == "\\" and index + 1 < length:
                index += 2
                continue
            if char == in_string:
                in_string = None
            index += 1
            continue
        if char == "#":
            break
        if char == "/" and index + 1 < length and line[index + 1] == "/":
            break
        if char in ("\"", "'"):
            in_string = char
            index += 1
            continue
        kept.append(char)
        index += 1
    return "".join(kept)


def _symbol_declaration_pattern(symbol_name: str) -> re.Pattern[str]:
    """Language-generic exact-symbol declaration pattern (S4.1/S4.4).

    Matches a line that carries the exact `symbol_name` token preceded on
    the same line by a cross-language declaration keyword (Python `def`,
    TS/JS `function`/`class`/`const`/`interface`, etc.). This is the
    baseline relocation-candidate signal; it is not restricted to any one
    language or to Python's `def` specifically.
    """
    keywords = "|".join(re.escape(keyword) for keyword in _DECLARATION_KEYWORDS)
    return re.compile(rf"\b(?:{keywords})\b.*\b{re.escape(symbol_name)}\b")


def _is_active_anchor(record: dict[str, Any]) -> bool:
    """S3.8 active-anchor predicate: audited anchors are active and current.

    An anchor is active when `lifecycle_state == "active"` (default when the
    field is absent, for backward compatibility with older fixtures/spines)
    and it has not been superseded (`superseded_by` empty). Inactive or
    superseded anchors are excluded from verification and exit-code impact
    entirely; they are reported only via `skipped_anchor_ids`.
    """
    if record.get("lifecycle_state", "active") != "active":
        return False
    if record.get("superseded_by"):
        return False
    return True


@dataclass
class AnchorAuditReport:
    """Result of `verify_anchors()`; JSON-stable via `to_dict()`."""

    scope: str
    strict: bool
    checked_anchor_ids: list[str]
    skipped_anchor_ids: list[str]
    anchors: list[dict[str, Any]]
    rg_used: bool
    python_fallback_used: bool = field(init=False)

    def __post_init__(self) -> None:
        self.python_fallback_used = not self.rg_used

    @property
    def exit_code(self) -> int:
        if not self.strict:
            return _EXIT_PASS
        if any(anchor["state"] in _MANUAL_RESELECT_STATES for anchor in self.anchors):
            return _EXIT_ANCHOR_DRIFT_MANUAL_RESELECT
        return _EXIT_PASS

    @property
    def ok(self) -> bool:
        return self.exit_code == _EXIT_PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "audit_name": AUDIT_NAME,
            "scope": self.scope,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "checked_anchor_ids": self.checked_anchor_ids,
            "skipped_anchor_ids": self.skipped_anchor_ids,
            "anchors": self.anchors,
            "tooling": {
                "required": ["python"],
                "optional_absent_ok": ["colgrep", "scip", "node"],
                "rg_used": self.rg_used,
                "python_fallback_used": self.python_fallback_used,
            },
        }


def _list_source_files(source_root: Path, rg_executable: str | None) -> tuple[list[Path], bool]:
    """Enumerate every file under `source_root`.

    Uses `rg --files` opportunistically when available; falls back to a
    pure-Python recursive walk otherwise. Both paths return the identical
    file set for a well-formed fixture tree -- `rg` is used only to *list*
    candidates faster, never to decide a match.
    """
    if rg_executable is not None:
        try:
            proc = subprocess.run(
                [rg_executable, "--files", str(source_root)],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        else:
            files = [Path(line) for line in proc.stdout.splitlines() if line.strip()]
            if files:
                return files, True
    files = [path for path in sorted(source_root.rglob("*")) if path.is_file()]
    return files, False


def _relative_path(path: Path, source_root: Path) -> str:
    try:
        return path.resolve().relative_to(source_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None


def _window_matches(lines: Sequence[str], line_start: int, line_end: int, symbol_name: str, probe: str | None) -> bool:
    if line_start < 1 or line_end > len(lines) or line_start > line_end:
        return False
    window_text = "\n".join(lines[line_start - 1 : line_end])
    if symbol_name not in window_text:
        return False
    if probe and probe not in window_text:
        return False
    return True


def _check_declared_location(
    source_root: Path,
    file_path: str,
    line_start: int,
    line_end: int,
    symbol_name: str,
    probe: str | None,
) -> bool:
    full_path = source_root / file_path
    if not full_path.is_file():
        return False
    lines = _read_lines(full_path)
    if lines is None:
        return False
    return _window_matches(lines, line_start, line_end, symbol_name, probe)


def _find_candidates(
    files: Iterable[Path],
    source_root: Path,
    symbol_name: str,
    probe: str | None,
    span_length: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return `(high_confidence, low_confidence)` candidate lists.

    S4.1/S4.4: relocation is language-generic. The baseline signal is an
    exact `symbol_name` match on a declaration-shaped line (a line carrying
    one of a broad, cross-language set of declaration keywords -- `def`,
    `function`, `class`, `const`, `interface`, ... -- ahead of the symbol),
    not a Python-`def`-only regex. Python's `def` is one member of that
    generic keyword set, so it still contributes as a signal but is no
    longer the sole mechanism; TypeScript/JS exports, classes, and other
    non-Python declarations are recognized the same way. A high-confidence
    candidate additionally matches the probe/semantic-hint text within the
    same span; a low-confidence candidate matches only the declaration.
    """
    symbol_pattern = _symbol_declaration_pattern(symbol_name)
    high: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []

    for path in files:
        lines = _read_lines(path)
        if lines is None:
            continue
        rel_path = _relative_path(path, source_root)
        for index, line in enumerate(lines):
            # GATE-phase3 cycle 2 BLOCKING: match against the comment/string
            # -stripped line, not the raw line, so a keyword+symbol that only
            # survives inside a comment or string literal is never accepted
            # as a relocation candidate (window_text below stays raw so a
            # legitimate probe living inside real code, e.g. a string literal
            # assigned by an actual declaration, still confirms high
            # confidence).
            if not symbol_pattern.search(_code_only(line)):
                continue
            candidate_line_start = index + 1
            candidate_line_end = candidate_line_start + span_length
            if candidate_line_end > len(lines):
                continue
            window_text = "\n".join(lines[candidate_line_start - 1 : candidate_line_end])
            candidate = {
                "file_path": rel_path,
                "line_start": candidate_line_start,
                "line_end": candidate_line_end,
            }
            if probe and probe in window_text:
                high.append({**candidate, "match": "symbol+content_probe"})
            else:
                low.append({**candidate, "match": "symbol_only"})

    sort_key = lambda item: (item["file_path"], item["line_start"], item["line_end"])  # noqa: E731
    high.sort(key=sort_key)
    low.sort(key=sort_key)
    return high, low


def _resolve_anchor(
    record: dict[str, Any],
    source_root: Path,
    files: list[Path],
) -> dict[str, Any]:
    record_id = record["record_id"]
    previous_state = record.get("anchor_state", "verified")
    file_path = record["file_path"]
    line_start = record["line_start"]
    line_end = record["line_end"]
    symbol_name = record["symbol_name"]
    probe = record.get("content_probe") or record.get("semantic_hint")
    span_length = line_end - line_start

    base_result = {
        "record_id": record_id,
        "previous_state": previous_state,
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "relocation": None,
        "candidates": [],
        "message": "",
    }

    if _check_declared_location(source_root, file_path, line_start, line_end, symbol_name, probe):
        return {
            **base_result,
            "state": "verified",
            "message": f"anchor {record_id!r} verified at declared location {file_path}:{line_start}-{line_end}.",
        }

    high, low = _find_candidates(files, source_root, symbol_name, probe, span_length)

    if len(high) == 1:
        only = high[0]
        return {
            **base_result,
            "state": "auto_relocated",
            "file_path": only["file_path"],
            "line_start": only["line_start"],
            "line_end": only["line_end"],
            "relocation": {
                "from": {"file_path": file_path, "line_start": line_start, "line_end": line_end},
                "to": {
                    "file_path": only["file_path"],
                    "line_start": only["line_start"],
                    "line_end": only["line_end"],
                },
            },
            "candidates": high,
            "message": (
                f"anchor {record_id!r} auto-relocated from {file_path}:{line_start} to "
                f"{only['file_path']}:{only['line_start']} (symbol+probe match)."
            ),
        }

    if len(high) > 1:
        return {
            **base_result,
            "state": "ambiguous",
            "candidates": high,
            "message": (
                f"anchor {record_id!r} symbol {symbol_name!r} matches {len(high)} high-confidence "
                "candidate locations; never auto-picking, manual reselect required."
            ),
        }

    # No high-confidence candidate. Fall back to the lower-confidence tier
    # (symbol found, probe not confirmed) per S4.3: "any non-verified state
    # may move to needs_reselect when relocation confidence is insufficient."
    if len(low) == 1:
        return {
            **base_result,
            "state": "needs_reselect",
            "candidates": low,
            "message": (
                f"anchor {record_id!r} symbol {symbol_name!r} has one low-confidence candidate at "
                f"{low[0]['file_path']}:{low[0]['line_start']}; probe not confirmed, manual reselect required."
            ),
        }

    if len(low) > 1:
        return {
            **base_result,
            "state": "ambiguous",
            "candidates": low,
            "message": (
                f"anchor {record_id!r} symbol {symbol_name!r} matches {len(low)} low-confidence "
                "candidate locations; never auto-picking, manual reselect required."
            ),
        }

    return {
        **base_result,
        "state": "deleted_or_unresolvable",
        "message": (
            f"anchor {record_id!r} symbol {symbol_name!r} not found anywhere under {source_root}; "
            "declared location no longer matches and no relocation candidate exists."
        ),
    }


def verify_anchors(
    records: list[dict[str, Any]],
    *,
    source_root: str | Path,
    strict: bool = True,
    changed_anchor_ids: set[str] | list[str] | None = None,
) -> AnchorAuditReport:
    """Resolve every `anchor` record in `records` against `source_root`."""
    root = Path(source_root)
    anchor_records = [record for record in records if record.get("record_type") == "anchor"]

    changed_ids: set[str] | None
    if changed_anchor_ids is None:
        changed_ids = None
        scope = "all"
    else:
        changed_ids = set(changed_anchor_ids)
        scope = "changed"

    rg_executable = _rg_executable()
    files, rg_used = _list_source_files(root, rg_executable)

    checked_ids: list[str] = []
    skipped_ids: list[str] = []
    anchor_results: list[dict[str, Any]] = []

    for record in anchor_records:
        record_id = record["record_id"]
        if not _is_active_anchor(record):
            # S3.8: inactive/superseded anchors are excluded from the audit
            # entirely -- skipped, not resolved, and never affect exit code.
            skipped_ids.append(record_id)
            continue
        if changed_ids is not None and record_id not in changed_ids:
            skipped_ids.append(record_id)
            continue
        checked_ids.append(record_id)
        anchor_results.append(_resolve_anchor(record, root, files))

    return AnchorAuditReport(
        scope=scope,
        strict=strict,
        checked_anchor_ids=checked_ids,
        skipped_anchor_ids=skipped_ids,
        anchors=anchor_results,
        rg_used=rg_used,
    )
