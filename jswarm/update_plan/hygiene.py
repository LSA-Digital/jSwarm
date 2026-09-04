"""Content hygiene for plan files: relocate tech-spec content, archive historic
content, and leave discoverability links behind.

Design constraints (COM-167 spec / Oracle F2):
  * Default is **propose** — compute candidate moves, write nothing but normalization.
  * Only **explicit headings/markers** are ever moved, and only under ``--apply``.
  * Acceptance Criteria / active blockers / decisions / verification / ambiguous
    content are **never** moved, even with a marker present.
  * Every applied move leaves a link; same-day archive runs append (no clobber, no
    duplicate links); re-running with nothing new produces no diff (idempotent).

Pure functions where possible; the one filesystem mutator is :func:`apply`.
All callers run fail-open — exceptions here are caught by the CLI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Explicit triggers (case-insensitive). Headings or markers — nothing else moves.
SPEC_HEADINGS = {"technical specification", "tech spec", "architecture", "api design"}
SPEC_MARKER = "<!-- update-plan:move-to-specs -->"
ARCHIVE_MARKER = "<!-- update-plan:archive -->"
ARCHIVE_WORDS = {"historical", "superseded", "deprecated", "archive"}
RELOCATED_MARKER = "<!-- update-plan:relocated -->"

# Plan structure that is ALWAYS planning content — never moved, even with a marker.
# Matched against the normalized heading by equality or prefix.
PROTECTED_PREFIXES = (
    "acceptance criteria",
    "scope",
    "changelog",
    "required reading",
    "implementation phase",
    "overview",
    "risk",
    "rollback",
    "open technical question",
    "status update",
    "completion checklist",
    "critical files",
    "agent assignment",
    "testing strategy",
    "a/c-to-test",
    "outcome metrics",
    "error handling",
    "non-functional",
    "constraints",
    "planning handoff",
    "domain rules",
    "archived detail",
    "acceptance",        # acceptance criteria / acceptance tests
)
# Short acceptance-criteria heading forms — matched by EQUALITY only (a "ac" prefix
# would wrongly protect e.g. "Action Items").
PROTECTED_EXACT = {"ac", "a/c", "a-c", "acs"}
# AC short-form headings (singular OR plural) with an optional trailing noun:
# "AC", "ACs", "AC Criteria", "A/C Traceability", "A/Cs", "A/C Acceptance Criteria",
# "AC Coverage", "ac-to-test". The optional `s` + trailing \b keeps "Action Items" /
# "Accuracy" UNprotected (no word boundary after the "ac" token there).
_AC_HEADING_RE = re.compile(r"^(?:acs?|a/cs?|a-cs?)\b")
# Any heading mentioning these words is current planning content — never moved
# (even with an explicit move marker). "ambiguous" self-declares unmovable content.
PROTECTED_WORDS = {"blocker", "decision", "verification", "ambiguous"}

ARCHIVED_DETAIL_HEADING = "## Archived Detail"

# Top-level section heading (## ), not ### .
_SECTION_RE = re.compile(r"^##(?!#)[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)
_FM_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n?", re.DOTALL)


@dataclass
class Section:
    title: str       # heading text after "## "
    text: str        # exact substring: "## ...\n" through just before the next "## "
    kind: str        # "keep" | "spec" | "archive"


def _norm(title: str) -> str:
    return title.strip().rstrip("#").strip().lower()


def _is_protected(title: str) -> bool:
    n = _norm(title)
    if n in PROTECTED_EXACT:
        return True
    if _AC_HEADING_RE.match(n):
        return True
    if any(w in n for w in PROTECTED_WORDS):
        return True
    return any(n == p or n.startswith(p) for p in PROTECTED_PREFIXES)


def _is_archive_heading(title: str) -> bool:
    n = _norm(title)
    words = n.split()
    if any(w in ARCHIVE_WORDS for w in words):
        return True
    if n.startswith("completed phase"):
        return True
    if words and words[-1] == "log" and n != "changelog":
        return True
    return False


def classify(title: str, body: str) -> str:
    """Classify one section. Protected content always wins (never moved). An
    already-relocated stub stays put (idempotency)."""
    if _is_protected(title):
        return "keep"
    if RELOCATED_MARKER in body:
        return "keep"
    if SPEC_MARKER in body or _norm(title) in SPEC_HEADINGS:
        return "spec"
    if ARCHIVE_MARKER in body or _is_archive_heading(title):
        return "archive"
    return "keep"


def slug(title: str) -> str:
    """GitHub-style heading anchor."""
    s = _norm(title)
    s = re.sub(r"[^a-z0-9 \-]", "", s)
    s = s.replace(" ", "-")
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _split(text: str) -> tuple[str, list[Section]]:
    """Return (head, sections) where head is the frontmatter + title + preamble up to
    the first top-level ``## `` heading, and sections cover the rest with byte-exact
    text (so reconstructing an unchanged plan is a no-op)."""
    fm = _FM_RE.match(text)
    body_start = fm.end() if fm else 0
    matches = list(_SECTION_RE.finditer(text, body_start))
    if not matches:
        return text, []
    head = text[:matches[0].start()]
    sections: list[Section] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start():end]
        sections.append(Section(title=m.group("title"), text=body, kind=classify(m.group("title"), body)))
    return head, sections


def proposals(text: str) -> list[tuple[str, str]]:
    """List (kind, title) for every section that --apply WOULD move. No writes."""
    _, sections = _split(text)
    return [(s.kind, s.title) for s in sections if s.kind in ("spec", "archive")]


def _specs_file(plan_path: Path, key: str) -> Path:
    ticket_dir = plan_path.parent / key
    existing = sorted(ticket_dir.glob(f"{key}.specs.*.md"))
    if existing:
        return existing[0]
    return ticket_dir / f"{key}.specs.relocated.md"


def _append_unique_section(target: Path, section_text: str, title: str, header: str) -> bool:
    """Append ``section_text`` under ``target`` unless a ``## <title>`` already exists
    (dedup / no-clobber). Creates the file with ``header`` when absent.

    Returns True if the section was appended, False on a same-title collision (the
    caller must then KEEP the source section — removing it would be data loss)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text("utf-8") if target.exists() else header
    if re.search(rf"^##(?!#)[ \t]+{re.escape(title)}[ \t]*$", existing, re.MULTILINE):
        return False  # collision — do not append, do not let the caller drop the source
    if not existing.endswith("\n"):
        existing += "\n"
    block = section_text if section_text.endswith("\n") else section_text + "\n"
    target.write_text(existing + "\n" + block, encoding="utf-8")
    return True


def _ensure_archived_detail(head_and_sections: str, links: list[str]) -> str:
    """Ensure a stable ``## Archived Detail`` section exists and contains each link
    exactly once (dedup)."""
    text = head_and_sections
    if ARCHIVED_DETAIL_HEADING not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += f"\n{ARCHIVED_DETAIL_HEADING}\n\n"
    new_links = [ln for ln in links if ln not in text]
    if not new_links:
        return text
    # Insert links right after the Archived Detail heading line.
    idx = text.index(ARCHIVED_DETAIL_HEADING)
    nl = text.index("\n", idx)
    insert_at = nl + 1
    # skip an immediately-following blank line for tidy placement
    if text[insert_at:insert_at + 1] == "\n":
        insert_at += 1
    addition = "".join(f"{ln}\n" for ln in new_links)
    return text[:insert_at] + addition + text[insert_at:]


def apply(plan_path: Path, key: str, today: str) -> dict:
    """Move spec/archive sections out of the plan, write specs/archive files, and
    inject discoverability links. Idempotent. Returns a small report dict."""
    text = plan_path.read_text("utf-8")
    head, sections = _split(text)

    archive_name = f"{key}.plan-archive.summary-of-archive.{today}.md"
    archive_path = plan_path.parent / key / "_archive" / archive_name
    specs_path = _specs_file(plan_path, key)

    rebuilt = [head]
    backlinks: list[str] = []
    moved_spec = 0
    moved_archive = 0
    skipped: list[str] = []  # same-title collisions kept in the plan (no data loss)

    for s in sections:
        if s.kind == "spec":
            anchor = slug(s.title)
            appended = _append_unique_section(
                specs_path, s.text, s.title,
                header=f"# {key} Spec\n",
            )
            if appended:
                rel = f"{key}/{specs_path.name}#{anchor}"
                stub = (
                    f"## {s.title}\n\n"
                    f"> 📄 Relocated to specs: [{specs_path.name}#{anchor}]({rel}) "
                    f"{RELOCATED_MARKER}\n\n"
                )
                rebuilt.append(stub)
                moved_spec += 1
            else:
                # Collision: destination already has this heading. Keep the source in
                # the plan rather than stub it — stubbing without an append is data loss.
                rebuilt.append(s.text)
                skipped.append(f"spec:{s.title}")
        elif s.kind == "archive":
            anchor = slug(s.title)
            appended = _append_unique_section(
                archive_path, s.text, s.title,
                header=f"# {key} — Plan Archive ({today})\n",
            )
            if appended:
                backlinks.append(
                    f"- [{s.title} → archive]({key}/_archive/{archive_name}#{anchor})"
                )
                moved_archive += 1
                # section removed from the plan (not re-appended)
            else:
                # Collision: keep the source in the plan (do not drop it).
                rebuilt.append(s.text)
                skipped.append(f"archive:{s.title}")
        else:
            rebuilt.append(s.text)

    new_text = "".join(rebuilt)
    if backlinks:
        new_text = _ensure_archived_detail(new_text, backlinks)

    if new_text != text:
        plan_path.write_text(new_text, encoding="utf-8")

    return {"moved_spec": moved_spec, "moved_archive": moved_archive, "skipped": skipped}
