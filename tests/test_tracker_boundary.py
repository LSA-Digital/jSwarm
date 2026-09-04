from pathlib import Path

import pytest
import yaml

from jswarm.tracker.resolve import load
from jswarm.tracker.none import NullTracker
from jswarm.tracker import jira as jira_mod


def _repo(tmp_path, cfg):
    (tmp_path / ".jswarm").mkdir(parents=True)
    (tmp_path / ".jswarm/config.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


def test_no_tracker_configured_yields_null_adapter(tmp_path):
    t = load(_repo(tmp_path, {"tracker": {"adapter": "none"}}))
    assert isinstance(t, NullTracker) and not t.is_configured()
    assert t.describe() == "no tracker"


def test_null_adapter_skips_every_write_without_raising(tmp_path):
    t = load(_repo(tmp_path, {"tracker": {"adapter": "none"}}))
    for r in (t.comment("x", "hello"), t.transition("x", "Done")):
        assert r.skipped and r.ok and "no tracker configured" in r.message
    assert t.resolve("x") is None


def test_missing_config_is_treated_as_no_tracker(tmp_path):
    assert isinstance(load(tmp_path), NullTracker)


def test_jira_adapter_selected_when_configured(tmp_path):
    t = load(_repo(tmp_path, {"tracker": {"adapter": "jira", "key_prefix": "PS"}}))
    assert t.is_configured() and "PS" in t.describe()


def test_unknown_adapter_raises_naming_the_known_ones(tmp_path):
    with pytest.raises(ValueError) as e:
        load(_repo(tmp_path, {"tracker": {"adapter": "linear"}}))
    msg = str(e.value)
    assert "linear" in msg and "jira" in msg and "none" in msg


def test_no_lifecycle_module_imports_the_jira_adapter_directly():
    # The boundary exists so a second tracker can be added without touching the lifecycle.
    import re

    offenders = []
    for p in Path("jswarm").rglob("*.py"):
        if p.parts[1] in ("tracker",):
            continue
        if re.search(r"from jswarm\.tracker\.jira|import jswarm\.tracker\.jira", p.read_text()):
            offenders.append(str(p))
    assert offenders == [], offenders


# --- JiraTracker must never raise out of comment() or transition() -------
#
# These stub the MCP client entirely (no network calls): JiraMcpClient.call_tool
# is replaced at the class level with a function that raises immediately, so
# retry()'s backoff never sleeps for real and no socket is ever opened.


def _stub_failing_call_tool(monkeypatch, message="simulated MCP outage"):
    def boom(self, name, arguments):
        raise jira_mod.JiraMcpError(message)

    monkeypatch.setattr(jira_mod.JiraMcpClient, "call_tool", boom)


def test_jira_comment_failure_returns_result_without_raising(monkeypatch):
    _stub_failing_call_tool(monkeypatch)
    t = jira_mod.JiraTracker(key_prefix="PS", attempts=1, backoff_seconds=0)

    r = t.comment("PS-14", "hello")

    assert r.ok is False
    assert r.skipped is False
    assert "PS-14" in r.message
    assert "simulated MCP outage" in r.message


def test_jira_transition_failure_returns_result_without_raising(monkeypatch):
    _stub_failing_call_tool(monkeypatch)
    t = jira_mod.JiraTracker(key_prefix="PS", attempts=1, backoff_seconds=0)

    r = t.transition("PS-14", "Done")

    assert r.ok is False
    assert r.skipped is False
    assert "PS-14" in r.message
    assert "Done" in r.message
    assert "simulated MCP outage" in r.message


def test_jira_transition_to_unknown_state_is_a_result_not_an_exception(monkeypatch):
    def only_lists_todo(self, name, arguments):
        if name == "jira_get_transitions":
            return {"content": [{"type": "text", "text": '[{"id": "1", "name": "To Do"}]'}]}
        raise AssertionError(f"unexpected tool call: {name}")

    monkeypatch.setattr(jira_mod.JiraMcpClient, "call_tool", only_lists_todo)
    t = jira_mod.JiraTracker(key_prefix="PS", attempts=1, backoff_seconds=0)

    r = t.transition("PS-14", "Done")

    assert r.ok is False
    assert r.skipped is False
    assert "Done" in r.message
