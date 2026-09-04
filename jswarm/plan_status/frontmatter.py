"""YAML frontmatter read + comment-preserving surgical key update.

Plan frontmatter carries comments (state-machine notes); a naive yaml round-trip
would strip them. ``update_keys`` therefore edits known keys line-by-line and
inserts missing keys without re-serializing the whole block.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

FENCE = "---"

#: canonical frontmatter field order. Known keys are emitted in this order;
#: unknown/legacy keys (ticket:, last_updated:, issue_type:, labels:, ...) are
#: preserved after the known block in their original relative order.
CANONICAL_ORDER: list[str] = [
    "status",
    "plan_status",
    "phase",
    "ac_complete",
    "nfr_complete",
    "nfr_color",
    "uat_complete",
    "uat_color",
    "created_from_template",
    "components",
    "features",
    "plan_status_last_updated",
    "plan_status_actor",
    "closed_at",
    "retro",
    "revisions",
]

_TOP_KEY_RE = re.compile(r"^([A-Za-z0-9_\-]+):")
_STATUS_KEY_RE = re.compile(r"^(status|plan_status)\s*:")


class FrontmatterError(ValueError):
    pass


def split_frontmatter(text: str) -> tuple[Optional[list[str]], list[str], list[str]]:
    """Return (fm_lines, body_lines, all_lines) where fm_lines excludes the fences.

    fm_lines is None when the document has no leading frontmatter block.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != FENCE:
        return None, lines, lines
    for idx in range(1, len(lines)):
        if lines[idx].strip() == FENCE:
            return lines[1:idx], lines[idx + 1:], lines
    # Unterminated frontmatter — treat as no frontmatter.
    return None, lines, lines


def read_frontmatter_text(text: str) -> dict[str, Any]:
    """Parse leading (TOP-ONLY) frontmatter from an in-memory string.

    Mirrors ``read_frontmatter`` semantics: only a block at line 1 is recognized.
    ``normalize_plan_file`` hoists first, then parses the hoisted text with this — so
    reads never expand the legacy backfill candidate set (spec §7.3 / MAJOR-4).
    """
    fm_lines, _body, _all = split_frontmatter(text)
    if fm_lines is None or yaml is None:
        return {}
    try:
        data = yaml.safe_load("".join(fm_lines))
    except Exception:
        return {}
    return data or {}


def read_frontmatter(path: Path) -> dict[str, Any]:
    """Parse the leading YAML frontmatter into a dict (empty if none/invalid)."""
    return read_frontmatter_text(Path(path).read_text(encoding="utf-8"))


def frontmatter_is_invalid(text: str) -> bool:
    """True when a leading fenced frontmatter block EXISTS but its YAML does not parse.

    Distinguishes ``present-but-broken`` from ``absent`` — both of which ``read_frontmatter``
    flattens to ``{}``. Callers that mutate a plan use this to FAIL-OPEN (skip the write)
    on a broken block, instead of rewriting it from empty-derived defaults and mangling it
    (NFR-012: no destructive write on a malformed plan)."""
    fm_lines, _body, _all = split_frontmatter(text)
    if fm_lines is None or yaml is None:
        return False  # no fenced block (or no parser) ⇒ not a "present-but-invalid" case
    try:
        yaml.safe_load("".join(fm_lines))
        return False
    except Exception:
        return True


def _inner_has_status(inner: list[str]) -> bool:
    """True when a fenced block's inner lines declare ``status:`` or ``plan_status:``.

    Disambiguates a real plan-frontmatter block from a Markdown ``---`` horizontal rule.
    """
    return any(_STATUS_KEY_RE.match(ln) for ln in inner)


def _first_section_index(lines: list[str]) -> int:
    """Index of the first ``## `` section heading outside a code fence (len if none).

    The real frontmatter block lives in the document PREAMBLE — above the body sections.
    Bounding the hoist search to before this prevents a stray ``---\\nstatus: ...\\n---``
    block in the BODY (a Markdown example) from being mistaken for frontmatter (MAJOR-3).
    """
    in_code = False
    for idx, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            in_code = not in_code
            continue
        if not in_code and re.match(r"^##\s", ln):
            return idx
    return len(lines)


def find_block(text: str) -> Optional[tuple[int, int]]:
    """Return (open_idx, close_idx) line indices of the first ``---``…``---`` pair whose
    inner YAML declares ``status``/``plan_status``, or None. Used by hoist/normalize only.

    Bounded to the document preamble: the block must open before the first ``## `` section
    heading and outside any fenced code block — so a body/example fenced block is never
    hoisted (MAJOR-3).
    """
    lines = text.splitlines(keepends=True)
    n = len(lines)
    bound = _first_section_index(lines)
    in_code = False
    i = 0
    while i < n:
        s = lines[i].lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            in_code = not in_code
            i += 1
            continue
        if not in_code and lines[i].strip() == FENCE:
            if i >= bound:
                return None  # past the preamble — not a frontmatter block
            j = i + 1
            while j < n and lines[j].strip() != FENCE:
                j += 1
            if j >= n:
                return None  # unterminated — no real block
            if _inner_has_status(lines[i + 1:j]):
                return (i, j)
            i = j + 1
            continue
        i += 1
    return None


def hoist_to_top(text: str) -> str:
    """Move a not-at-line-1 frontmatter block to the top, preserving the preamble below.

    Idempotent; a no-op when the block is already at line 1 or there is no block.
    """
    blk = find_block(text)
    if blk is None:
        return text
    open_idx, close_idx = blk
    if open_idx == 0:
        return text
    lines = text.splitlines(keepends=True)
    block = lines[open_idx:close_idx + 1]
    if block and not block[-1].endswith("\n"):
        block[-1] = block[-1] + "\n"
    preamble = lines[:open_idx]
    rest = lines[close_idx + 1:]
    return "".join(block + ["\n"] + preamble + rest)


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        # Quote strings that contain special chars or start with a digit-dot form.
        if value == "" or re.search(r'[:#]|^\s|\s$', value) or re.match(r"^[\d\.]", value):
            escaped = value.replace('"', '\\"')
            return f'"{escaped}"'
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _close_fence_idx(lines: list[str]) -> Optional[int]:
    """Index of the closing fence for a top-of-text frontmatter block, else None."""
    if not lines or lines[0].strip() != FENCE:
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == FENCE:
            return idx
    return None


def upsert_keys_text(text: str, updates: dict[str, Any]) -> str:
    """Pure text variant of ``update_keys``: set/insert TOP-LEVEL keys in a top block.

    Existing top-level ``key:`` lines are replaced in place; missing keys are appended
    before the closing fence. Nested (indented) keys are never touched. No-op when the
    text has no top-of-document frontmatter block (caller hoists first).
    """
    lines = text.splitlines(keepends=True)
    close_idx = _close_fence_idx(lines)
    if close_idx is None:
        return text
    remaining = dict(updates)
    newline = "\n"
    for idx in range(1, close_idx):
        if lines[idx][:1] in (" ", "\t"):
            continue  # nested continuation — never a top-level key
        m = _TOP_KEY_RE.match(lines[idx])
        if not m:
            continue
        key = m.group(1)
        if key in remaining:
            line_nl = newline if lines[idx].endswith("\n") else ""
            lines[idx] = f"{key}: {_format_value(remaining[key])}{line_nl or newline}"
            del remaining[key]
    if remaining:
        insert = [f"{key}: {_format_value(val)}{newline}" for key, val in remaining.items()]
        lines[close_idx:close_idx] = insert
    return "".join(lines)


def remove_keys_text(text: str, keys) -> str:
    """Remove TOP-LEVEL ``key:`` lines (and their indented continuations) for ``keys``."""
    drop = set(keys)
    lines = text.splitlines(keepends=True)
    close_idx = _close_fence_idx(lines)
    if close_idx is None:
        return text
    out = [lines[0]]
    i = 1
    while i < close_idx:
        if lines[i][:1] not in (" ", "\t"):
            m = _TOP_KEY_RE.match(lines[i])
            if m and m.group(1) in drop:
                i += 1
                while i < close_idx and lines[i][:1] in (" ", "\t") and lines[i].strip() != "":
                    i += 1
                continue
        out.append(lines[i])
        i += 1
    out.extend(lines[close_idx:])
    return "".join(out)


def canonicalize(text: str) -> str:
    """Reorder a top-of-document frontmatter block into ``CANONICAL_ORDER``.

    Comment-preserving and reorder-only (no value reformatting). Column-0 comments
    travel with the key they precede; trailing comments stay last; indented
    continuations (list items, ``revisions:`` nested blocks, interleaved indented
    comments) travel with their owning key. Unknown/legacy keys are emitted after the
    known block in original relative order. Idempotent (byte-identical 2nd pass).
    """
    lines = text.splitlines(keepends=True)
    close_idx = _close_fence_idx(lines)
    if close_idx is None:
        return text
    inner = lines[1:close_idx]

    chunks: list[dict[str, Any]] = []
    pending: list[str] = []  # column-0 comments/blanks awaiting the next key
    i = 0
    while i < len(inner):
        line = inner[i]
        is_top_key = line[:1] not in (" ", "\t") and bool(_TOP_KEY_RE.match(line))
        if is_top_key:
            key = _TOP_KEY_RE.match(line).group(1)
            chunk_lines = pending + [line]
            pending = []
            i += 1
            while i < len(inner) and inner[i][:1] in (" ", "\t") and inner[i].strip() != "":
                chunk_lines.append(inner[i])
                i += 1
            chunks.append({"key": key, "lines": chunk_lines})
        else:
            pending.append(line)  # column-0 comment or blank line
            i += 1
    trailing = pending

    out_inner: list[str] = []
    emitted: set[int] = set()
    for known_key in CANONICAL_ORDER:
        for idx, chunk in enumerate(chunks):
            if chunk["key"] == known_key and idx not in emitted:
                out_inner.extend(chunk["lines"])
                emitted.add(idx)
    for idx, chunk in enumerate(chunks):
        if idx not in emitted:
            out_inner.extend(chunk["lines"])
            emitted.add(idx)
    out_inner.extend(trailing)

    return "".join([lines[0]] + out_inner + lines[close_idx:])


def update_keys(path: Path, updates: dict[str, Any]) -> None:
    """Surgically set/insert frontmatter keys, preserving comments + other keys.

    Hoists a not-at-line-1 block to the top first, then sets/inserts the given
    top-level keys. Raises FrontmatterError only when the document has no frontmatter
    block at all (so callers that catch it — cli/backfill — keep their skip semantics).
    """
    path = Path(path)
    text = hoist_to_top(path.read_text(encoding="utf-8"))
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != FENCE:
        raise FrontmatterError(f"{path} has no leading frontmatter block")
    if _close_fence_idx(lines) is None:
        raise FrontmatterError(f"{path} frontmatter block is unterminated")
    path.write_text(upsert_keys_text(text, updates), encoding="utf-8")
