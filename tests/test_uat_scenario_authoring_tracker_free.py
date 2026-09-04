"""`/jUAT`'s "author a scenario" path must resolve a slug work item, not just
a tracker key.

Context: docs/superpowers/specs/2026-09-03-jswarm-public-repo-split-design.md
section 4 and the clean-Mac lifecycle walk (finding 5): before the fix,
jswarm/uat-scenarios/resolve_active_ticket.py's TICKET_RE matched only
uppercase KEY-### shapes, so `/jUAT`'s Step 2 "author a UAT scenario" path
was unusable for every tracker-free/slug work item -- the exact tier the
tracker-free adoption path exists to support. The walker had to route
around it entirely.

Exercises the real script via subprocess (its directory, jswarm/uat-scenarios/,
has a hyphen and is not an importable package), mirroring exactly how the
clean-Mac walk invoked it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
SCRIPT = REPO_ROOT / "jswarm" / "uat-scenarios" / "resolve_active_ticket.py"

SLUG_TICKET = "add-multiply"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(VENV_PYTHON), str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30,
    )


def _write_plan(plans_dir: Path, ticket: str) -> Path:
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / f"{ticket}.plan.description.md"
    plan_path.write_text(
        '---\nstatus: ACTIVE\nplan_status: "2.planning.detailed"\n---\n\n# Plan\n',
        encoding="utf-8",
    )
    return plan_path


def test_resolves_a_slug_work_item_from_the_active_ticket_binding(tmp_path):
    """The exact repro from the clean-Mac walk: `--active-ticket "add-multiply"
    --branch master` (the walked project never had a feat/<id> branch --
    finding 4 of the same walk)."""
    plans_dir = tmp_path / ".jswarm" / "plans"
    plan_path = _write_plan(plans_dir, SLUG_TICKET)

    result = _run(
        "--plans-dir", str(plans_dir),
        "--active-ticket", SLUG_TICKET,
        "--branch", "master",
        "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ticket"] == SLUG_TICKET
    assert payload["plan_file"] == str(plan_path)
    assert payload["status"] == "ACTIVE"


def test_resolves_a_slug_work_item_from_a_feat_branch_name(tmp_path):
    plans_dir = tmp_path / ".jswarm" / "plans"
    _write_plan(plans_dir, SLUG_TICKET)

    result = _run("--plans-dir", str(plans_dir), "--branch", f"feat/{SLUG_TICKET}", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ticket"] == SLUG_TICKET


def test_a_generic_branch_name_is_not_misread_as_a_slug_work_item(tmp_path):
    """`master`/`main` are themselves syntactically valid slugs -- the
    resolver must not treat a bare branch name as a work item id; only a
    documented feat/<id>-shaped branch, or an explicit --active-ticket,
    counts (see _WORK_BRANCH_PREFIXES)."""
    plans_dir = tmp_path / ".jswarm" / "plans"
    plans_dir.mkdir(parents=True)

    result = _run("--plans-dir", str(plans_dir), "--branch", "master", "--json")
    assert result.returncode == 1
    assert "no active ticket could be resolved" in (result.stdout + result.stderr)


def test_tracker_key_still_resolves(tmp_path):
    """Regression guard: fixing the slug path must not break the original
    tracker-key path."""
    plans_dir = tmp_path / ".jswarm" / "plans"
    _write_plan(plans_dir, "PS-14")

    result = _run(
        "--plans-dir", str(plans_dir),
        "--active-ticket", "PS-14",
        "--branch", "master",
        "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ticket"] == "PS-14"


def test_tracker_key_still_resolves_embedded_in_a_feat_branch_suffix(tmp_path):
    """Regression guard for the pre-existing embedded-search behavior: a
    tracker key followed by extra branch-name text must still resolve."""
    plans_dir = tmp_path / ".jswarm" / "plans"
    _write_plan(plans_dir, "PS-14")

    result = _run("--plans-dir", str(plans_dir), "--branch", "feat/PS-14-fix-bug", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ticket"] == "PS-14"
