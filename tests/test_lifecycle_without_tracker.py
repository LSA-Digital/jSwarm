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


def test_tracker_failure_is_distinct_from_skip_and_leaves_local_state_intact(tmp_path):
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

    tracker = load(repo)
    assert tracker.is_configured()
    for request in (
        tracker.comment(wid.value, "planned"),
        tracker.transition(wid.value, "In Progress"),
    ):
        evidence = dataclasses.asdict(request)
        evidence.update(ok=False, error="simulated hosted MCP outage")
        result = jira_mod.complete_request(request, evidence)
        assert result.ok is False
        assert result.skipped is False
        assert "Local evidence is preserved" in result.message

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
