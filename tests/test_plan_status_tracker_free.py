"""A slug work item's plan status must genuinely advance through the lifecycle
-- not silently no-op with `advisory-skip: config-unresolved` -- exactly like
a tracker-key ticket does.

Context: docs/superpowers/specs/2026-09-03-jswarm-public-repo-split-design.md
section 4 ("nothing downstream of /jPlan knows or cares which form was
used") and the clean-Mac lifecycle walk (finding 2): before the fix,
jswarm/plan_status/config.py's resolve_config() required a resolvable Jira
key prefix to set enabled=True, so /jGo's and /jClose's plan-status `record`
calls were silently inert for a tracker-free project -- the product's own
documented default path. A plan's frontmatter never advanced past
`2.planning.detailed` even after the full lifecycle genuinely completed.

These tests exercise the real CLI end to end (subprocess, the actual
`.venv/bin/python -m jswarm.plan_status.cli`) against a tracker-free
project fixture, and assert the plan FILE's frontmatter actually changes --
not just that the command exits zero, which is exactly what let the original
bug pass CI.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

TICKET = "add-multiply"

PLAN_TEXT = """---
status: ACTIVE
plan_status: "2.planning.detailed"
plan_status_last_updated: "2026-09-04T00:00:00Z"
plan_status_actor: "/jPlan"
---

# add-multiply

Plan body.
"""


@pytest.fixture
def tracker_free_project(tmp_path):
    """A minimal stand-in for a real /jPlan-adopted, tracker-free project:
    `.jswarm/config.yaml` with `tracker: {adapter: none}` (exactly what
    `install.sh adopt` writes with no `--jira-key`), and a canonical plan
    already written at `.jswarm/plans/<slug>.plan.<description>.md` (exactly
    what /jPlan writes -- skills/jPlan/CHANGELOG.md)."""
    plans_dir = tmp_path / ".jswarm" / "plans"
    plans_dir.mkdir(parents=True)
    (tmp_path / ".jswarm" / "config.yaml").write_text(
        "tracker:\n  adapter: none\n", encoding="utf-8"
    )
    plan_path = plans_dir / f"{TICKET}.plan.multiply-function.md"
    plan_path.write_text(PLAN_TEXT, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path, plan_path


def _record(project_root: Path, to_status: str, *, from_status: str | None = None) -> dict:
    argv = [
        str(VENV_PYTHON), "-m", "jswarm.plan_status.cli",
        "--project-root", str(project_root),
        "record", TICKET, to_status,
        "--actor", "test", "--proof-source", "test",
    ]
    if from_status:
        argv += ["--from", from_status]
    result = subprocess.run(
        argv, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_plan_status_does_not_advisory_skip_a_tracker_free_project(tracker_free_project):
    """The exact symptom from the walk: `record` must never silently no-op
    with config-unresolved on this repo's own documented default path."""
    project_root, _ = tracker_free_project
    result = _record(project_root, "3.implementation.phase_1", from_status="2.planning.detailed")
    assert result.get("action") != "advisory-skip", result
    assert result["action"] == "applied", result


def test_plan_status_advances_a_slug_work_item_through_the_lifecycle(tracker_free_project):
    project_root, plan_path = tracker_free_project

    before = plan_path.read_text(encoding="utf-8")
    assert 'plan_status: "2.planning.detailed"' in before

    result = _record(project_root, "3.implementation.phase_1", from_status="2.planning.detailed")
    assert result["action"] == "applied", result
    after_phase1 = plan_path.read_text(encoding="utf-8")
    assert after_phase1 != before
    assert 'plan_status: "3.implementation.phase_1"' in after_phase1

    result = _record(project_root, "4.closed.all_ac_met")
    assert result["action"] == "applied", result
    after_ac_met = plan_path.read_text(encoding="utf-8")
    assert after_ac_met != after_phase1
    assert 'plan_status: "4.closed.all_ac_met"' in after_ac_met

    result = _record(project_root, "5.closed.ready_for_merge")
    assert result["action"] == "applied", result
    final = plan_path.read_text(encoding="utf-8")
    assert final != after_ac_met
    assert 'plan_status: "5.closed.ready_for_merge"' in final
    assert "status: READY_FOR_MERGE" in final


def test_show_resolves_a_tracker_free_project(tracker_free_project):
    """cmd_show gates on cfg.plans_dir directly (not cfg.enabled) -- assert it
    resolves too, not just `record`."""
    project_root, _ = tracker_free_project
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "jswarm.plan_status.cli",
         "--project-root", str(project_root), "show"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload.get("action") != "advisory-skip", payload
