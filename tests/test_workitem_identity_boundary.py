"""Class guard: no module may reimplement work-item identity with a private
tracker-key-only regex instead of delegating to ``jswarm.workitem.identity``.

Same style as ``tests/test_host_boundary.py`` and
``tests/test_platform_boundary.py`` -- scan the whole ``jswarm/`` tree for the
reimplementation signature, with a small, individually-justified allowlist for
the handful of places that legitimately parse a tracker-key SHAPE for a
reason that has nothing to do with work-item identity (see each entry below).

Context: docs/superpowers/specs/2026-09-03-jswarm-public-repo-split-design.md
section 4 says a work item is EITHER a tracker key (``PS-14``) OR a local
slug (``add-csv-export``), and nothing downstream of ``/jPlan`` knows or
cares which form was used. Four modules were found hand-rolling a
tracker-key-only regex instead of using ``jswarm.workitem.identity`` --
two of them (``jswarm/plan_status/cli.py`` via ``config.py``, and
``jswarm/uat-scenarios/resolve_active_ticket.py``) silently broke the
default tracker-free lifecycle for every slug work item. This test exists so
a fifth instance fails CI immediately instead of shipping silently broken,
like the first four did.
"""
from __future__ import annotations

import re
from pathlib import Path

# Each entry is a file that legitimately contains a tracker-key-SHAPED regex
# for a reason unrelated to validating/resolving a work item's identity.
ALLOWED = {
    # The source of truth. TRACKER_KEY is defined here; everything else
    # should import it, not restate it.
    "jswarm/workitem/identity.py",
    # Parses Jira's OWN API response text to recover a newly created issue's
    # key. This reads Jira's output, it does not validate a work item id a
    # caller supplied -- a slug can never appear in a Jira create-issue
    # response regardless of how the fix is written.
    "jswarm/jira_mcp_closeout.py",
    # IMPLEMENTATION validates an evidence CITATION format (task#N / PR#N /
    # tracker-key / commit-sha, each followed by a descriptive slug) -- not a
    # work item id. It already accepts several non-tracker citation forms
    # (task#, PR#, a bare commit hash), so it is not tracker-only to begin
    # with.
    "jswarm/uat_feedback.py",
    # _LEGACY_PLAN_FILE_RE only (docs/plans/<KEY>-<description>.md). That
    # naming scheme predates jswarm.workitem.identity / slug support
    # entirely -- it is only ever produced by an old Jira-ticket-based
    # project migrated from before the tracker-free split, and a "-"
    # separator would be ambiguous against a slug's own "-"-delimited
    # alphabet with no way to tell id from description apart. The canonical
    # _JSWARM_PLAN_FILE_RE in the same file (".plan." separator, unambiguous
    # either way) delegates to jswarm.workitem.identity.
    "jswarm/plan_status/config.py",
}

# The reimplementation signature: a hand-rolled "[A-Z][A-Z0-9_]+-" character-
# class pair immediately followed by a dash -- the exact shape of
# jswarm.workitem.identity.TRACKER_KEY (`^[A-Z][A-Z0-9]+-\d+$`) -- rather than
# importing and reusing it. This is deliberately narrower than a bare
# "A-Z0-9" search: an unrelated char class used for something else entirely
# (a slash-command name, an NFR id, an injection-directive name) does not
# match this signature and must not be flagged.
_OFFENDER_RE = re.compile(r"\[A-Z\]\[A-Z0-9_?\][+*]-")


def _offenders() -> list[str]:
    offenders: list[str] = []
    for path in sorted(Path("jswarm").rglob("*.py")):
        rel = str(path)
        if rel in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _OFFENDER_RE.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    return offenders


def test_no_module_reimplements_tracker_key_identity_with_a_private_regex():
    offenders = _offenders()
    assert offenders == [], (
        "private tracker-key-only regex found outside jswarm.workitem.identity "
        "(delegate to jswarm.workitem.identity instead, or add a justified "
        "entry to ALLOWED in this test):\n" + "\n".join(offenders)
    )


# --- Proof the guard actually bites: the exact lines this round found and fixed. ---

def test_offender_pattern_catches_the_original_defects():
    original_bad_lines = [
        # jswarm/uat-scenarios/resolve_active_ticket.py, before the fix.
        r'TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")',
        # jswarm/update_ticket/promotions.py, before the fix.
        r'_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")',
        # jswarm/update_plan/cli.py, before the fix.
        r'_TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")',
        # jswarm/uat-scenarios/scaffold_uat_tests.py, before the fix.
        r'TICKET_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")',
        # jswarm/precompact_reconcile/matrices.py::_ticket_from_name, before the fix.
        r'm = re.match(r"^([A-Z][A-Z0-9]+-[0-9]+)\.plan\.", name)',
    ]
    for bad_line in original_bad_lines:
        assert _OFFENDER_RE.search(bad_line), f"guard failed to catch: {bad_line!r}"


def test_offender_pattern_does_not_flag_the_fixed_delegating_form():
    good_lines = [
        'TICKET_RE = re.compile(f"^(?:{_workitem_identity.TRACKER_KEY[1:-1]}'
        '|{_workitem_identity.SLUG[1:-1]})$")',
        "from jswarm.workitem import identity as _workitem_identity",
        '_workitem_identity.parse(ticket)',
    ]
    for good_line in good_lines:
        assert not _OFFENDER_RE.search(good_line), f"guard false-positived on: {good_line!r}"


def test_offender_pattern_does_not_flag_unrelated_uppercase_char_classes():
    """Sanity: a generic [A-Z0-9] class used for something that is not a
    tracker key (a slash-command name, an NFR id) must not be flagged."""
    unrelated_lines = [
        'SLASH_COMMAND_RE = re.compile(r"`/([a-zA-Z][a-zA-Z0-9_-]*)`")',
        'r"^\\s*<!--\\s*inject:(?P<name>[a-zA-Z0-9._-]+)\\s*-->\\s*$"',
        'r"\\bNFR-[A-Z0-9-]+\\b"',
    ]
    for line in unrelated_lines:
        assert not _OFFENDER_RE.search(line), f"guard false-positived on unrelated line: {line!r}"
