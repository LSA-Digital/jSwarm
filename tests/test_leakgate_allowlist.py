"""The leakgate allowlist mechanism itself: narrow, explicit, and honest.

An allowance must never be silent (every applied entry is printed), a
reasonless entry is a configuration error (rejected outright), and a stale
entry (matches nothing) fails the gate so it gets noticed and removed
instead of quietly rotting.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jswarm.leakgate import AllowlistError, load_allowlist, main, scan


# Split with string concatenation, same convention as tests/test_leakgate.py's
# header comment explains: this file is itself scanned by the gate it tests,
# so the literal forbidden strings must never appear contiguous here either.
TICKET = "COM-" + "123"
TICKET_PATTERN = "COM-" + r"\d+"


def _write_rules(tmp_path: Path, patterns: list[str]) -> Path:
    rules = tmp_path / "leakgate.yaml"
    body = "\n".join(f"  - '{p}'" for p in patterns)
    rules.write_text(f"patterns:\n{body}\n")
    return rules


def test_allowed_finding_is_suppressed_and_reported(tmp_path, capsys):
    _write_rules(tmp_path, [TICKET_PATTERN])
    (tmp_path / "a.md").write_text(f"see {TICKET} for details\n")
    (tmp_path / "leakgate-allowlist.yaml").write_text(
        "allow:\n"
        "  - path: a.md\n"
        f"    pattern: '{TICKET_PATTERN}'\n"
        "    reason: 'documented example ticket key, not a real leak'\n"
    )
    rc = main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ALLOWED: a.md matches" in out
    assert "documented example ticket key" in out
    # An allowed finding must not also print as a failing one.
    assert "a.md:1: matches" not in out


def test_reasonless_entry_is_rejected(tmp_path):
    (tmp_path / "leakgate-allowlist.yaml").write_text(
        "allow:\n"
        "  - path: a.md\n"
        f"    pattern: '{TICKET_PATTERN}'\n"
        "    reason: ''\n"
    )
    with pytest.raises(AllowlistError, match="reason"):
        load_allowlist(tmp_path / "leakgate-allowlist.yaml")


def test_reasonless_entry_fails_the_gate(tmp_path, capsys):
    _write_rules(tmp_path, [TICKET_PATTERN])
    (tmp_path / "a.md").write_text(f"see {TICKET} for details\n")
    (tmp_path / "leakgate-allowlist.yaml").write_text(
        "allow:\n"
        "  - path: a.md\n"
        f"    pattern: '{TICKET_PATTERN}'\n"
    )
    rc = main(["--root", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "reason" in err.lower()


def test_stale_allowlist_entry_fails_the_gate(tmp_path, capsys):
    """An entry that matches nothing (the occurrence was fixed, the file
    moved, a typo in path/pattern) must fail loudly, not sit there unused."""
    _write_rules(tmp_path, [TICKET_PATTERN])
    (tmp_path / "clean.md").write_text("nothing sensitive here\n")
    (tmp_path / "leakgate-allowlist.yaml").write_text(
        "allow:\n"
        "  - path: nonexistent-file.md\n"
        f"    pattern: '{TICKET_PATTERN}'\n"
        "    reason: 'stale on purpose, for this test'\n"
    )
    rc = main(["--root", str(tmp_path)])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "STALE ALLOWLIST ENTRY" in err
    assert "nonexistent-file.md" in err
    assert "ALLOWED:" not in out


def test_allowlist_file_is_never_scanned_for_its_own_pattern_text(tmp_path):
    """The allowlist necessarily quotes the raw pattern text as data (so it
    can compare against real findings); it must not therefore report a
    finding against itself."""
    _write_rules(tmp_path, [TICKET_PATTERN])
    (tmp_path / "leakgate-allowlist.yaml").write_text(
        "allow:\n"
        "  - path: elsewhere.md\n"
        f"    pattern: '{TICKET_PATTERN}'\n"
        "    reason: 'documented example, contains the literal ticket pattern as data'\n"
    )
    found = scan(
        tmp_path, tmp_path / "leakgate.yaml",
        also_skip=frozenset({"leakgate-allowlist.yaml"}),
    )
    assert found == []


def test_missing_allowlist_file_means_no_allowances(tmp_path):
    assert load_allowlist(tmp_path / "leakgate-allowlist.yaml") == []
