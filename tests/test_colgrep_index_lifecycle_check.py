"""R6a ruling 2: `colgrep_index_lifecycle.py check` must fail OPEN, not crash,
when the enterprise-only `colgrep_index_evictor` module is absent -- which it
always is in this repository. Four core skills (jClose, jGo, jPlan,
jPrecompact) invoke `check --command ... --json` verbatim with no error
handling of their own, so an unguarded ImportError there would break the core
loop whenever ColGREP (an optional component) is not installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jswarm.colgrep_index_lifecycle import (
    _colgrep_component_import_error,
    _colgrep_unavailable_check_result,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_colgrep_component_import_error_reports_missing_module():
    # colgrep_index_evictor is enterprise-only and deliberately not part of
    # this repo, so this must always detect it as unavailable here.
    err = _colgrep_component_import_error()
    assert err is not None
    assert isinstance(err, ImportError)


def test_unavailable_check_result_matches_documented_json_contract():
    result = _colgrep_unavailable_check_result("jPlan")
    # Same key set as CheckResult.to_dict() (colgrep_lifecycle_check.py), plus
    # the two extra fields that make the degraded case distinguishable from a
    # genuine "nothing ambiguous" result.
    assert set(result) == {
        "command",
        "ambiguous",
        "auto_resolved",
        "protected_holds",
        "question",
        "suppressed",
        "candidate_hash",
        "colgrep_available",
        "detail",
    }
    assert result["command"] == "jPlan"
    assert result["ambiguous"] == []
    assert result["auto_resolved"] == []
    assert result["protected_holds"] == []
    assert result["question"] is None
    assert result["suppressed"] is True
    assert result["candidate_hash"] is None
    assert result["colgrep_available"] is False
    assert "not installed" in result["detail"]


def test_check_subcommand_succeeds_and_reports_component_unavailable():
    """The exact call shape the core-loop skills use: `check --command X --json`."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "jswarm.colgrep_index_lifecycle",
            "check",
            "--command",
            "jPlan",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["colgrep_available"] is False
    assert payload["suppressed"] is True
    assert payload["question"] is None


def test_cleanup_subcommand_fails_clean_not_with_a_traceback():
    completed = subprocess.run(
        [sys.executable, "-m", "jswarm.colgrep_index_lifecycle", "cleanup", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    assert "not installed" in completed.stderr
