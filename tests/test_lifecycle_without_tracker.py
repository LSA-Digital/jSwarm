"""The tracker-free path, end to end, at the level the Python layer owns.

The skills themselves are Markdown and are exercised on the clean Mac. What this
proves is that every artefact the lifecycle writes lands correctly with no tracker,
and that nothing in the chain reaches for one.
"""
from pathlib import Path
import subprocess, yaml
from jswarm.workitem.identity import parse
from jswarm.workitem.state import work_dir
from jswarm.tracker.resolve import load

def _adopted(tmp_path):
    repo = tmp_path / "proj"; repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".jswarm").mkdir()
    (repo / ".jswarm/config.yaml").write_text(yaml.safe_dump({"tracker": {"adapter": "none"}}))
    (repo / ".jswarm/.adopted").write_text("")
    return repo

def test_a_slug_work_item_gets_local_state_and_no_tracker_calls(tmp_path):
    repo = _adopted(tmp_path)
    wid = parse("add-csv-export")
    d = work_dir(repo, wid); d.mkdir(parents=True)
    (d / "plan.md").write_text("# Plan\n")
    (d / "state.yaml").write_text(yaml.safe_dump({"status": "planned"}))
    tracker = load(repo)
    assert not tracker.is_configured()
    for result in (tracker.comment(wid.value, "planned"), tracker.transition(wid.value, "In Progress")):
        assert result.skipped and result.ok
    assert (d / "plan.md").exists() and yaml.safe_load((d / "state.yaml").read_text())["status"] == "planned"

def test_close_and_merge_artefacts_are_local(tmp_path):
    repo = _adopted(tmp_path)
    wid = parse("add-csv-export")
    d = work_dir(repo, wid); d.mkdir(parents=True)
    for name in ("plan.md", "retro.md", "close.yaml", "uat-round-1.yaml"):
        (d / name).write_text("x\n")
    assert sorted(p.name for p in d.iterdir()) == ["close.yaml", "plan.md", "retro.md", "uat-round-1.yaml"]
    assert load(repo).describe() == "no tracker"


# --- strengthening beyond the brief's minimal version ------------------------------
#
# Candidates considered from the task brief, checked against what actually holds:
#
# - "work_dir is identical in shape for a slug and for a tracker key": already covered,
#   directly and thoroughly, by tests/test_workitem_identity.py::
#   test_work_dir_is_identical_for_both_kinds. Not duplicated here.
#
# - "a tracker failure, as distinct from a skipped call, also leaves local state intact":
#   holds. jswarm/tracker/jira.py's comment()/transition() catch every exception from the
#   underlying MCP client and return Result(ok=False, skipped=False, ...) rather than
#   raising -- distinct from NullTracker's Result(ok=True, skipped=True, ...). Covered
#   below with local state written first, then a failing tracker call, then re-asserting
#   the local state is untouched.
#
# - "nothing under jswarm/workitem/ imports the tracker at all": holds. Verified below
#   by statically checking every jswarm/workitem/*.py file for an import of jswarm.tracker
#   (the word "tracker" appears only in docstrings/comments there, never in an import).


def test_tracker_failure_is_distinct_from_skip_and_leaves_local_state_intact(monkeypatch, tmp_path):
    import dataclasses

    from jswarm.tracker import jira as jira_mod

    repo = _adopted(tmp_path)
    (repo / ".jswarm/config.yaml").write_text(
        yaml.safe_dump({"tracker": {"adapter": "jira", "key_prefix": "PS"}})
    )
    wid = parse("PS-14")
    d = work_dir(repo, wid)
    d.mkdir(parents=True)
    (d / "plan.md").write_text("# Plan\n")
    (d / "state.yaml").write_text(yaml.safe_dump({"status": "planned"}))
    before = {p.name: p.read_text() for p in d.iterdir()}

    def boom(self, name, arguments):
        raise jira_mod.JiraMcpError("simulated MCP outage")

    monkeypatch.setattr(jira_mod.JiraMcpClient, "call_tool", boom)

    # load() reads the real config wiring (adapter/key_prefix); attempts/backoff are
    # overridden only so the test doesn't sit through real retry backoff sleeps.
    tracker = dataclasses.replace(load(repo), attempts=1, backoff_seconds=0)
    assert tracker.is_configured()
    for result in (
        tracker.comment(wid.value, "planned"),
        tracker.transition(wid.value, "In Progress"),
    ):
        # ok=False, skipped=False: a real failure, never confused with the null
        # adapter's ok=True, skipped=True "there is nothing to do here" skip.
        assert result.ok is False
        assert result.skipped is False
        assert "Local state was already written" in result.message

    after = {p.name: p.read_text() for p in d.iterdir()}
    assert after == before


def test_nothing_under_workitem_imports_the_tracker():
    import re

    tracker_import_re = re.compile(r"^\s*(from jswarm\.tracker|import jswarm\.tracker)\b", re.MULTILINE)
    offenders = []
    for p in Path("jswarm/workitem").rglob("*.py"):
        if tracker_import_re.search(p.read_text(encoding="utf-8")):
            offenders.append(str(p))
    assert offenders == [], offenders
