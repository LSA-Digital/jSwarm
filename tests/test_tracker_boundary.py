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



@pytest.mark.parametrize("action,args", [("resolve", ()), ("comment", ("hello",)), ("transition", ("Done",))])
def test_jira_requests_host_instead_of_contacting_a_local_proxy(monkeypatch, action, args):
    import socket
    def unexpected_connection(*args, **kwargs):
        pytest.fail("Jira must use the active host's authenticated tools")
    monkeypatch.setattr(socket, "create_connection", unexpected_connection)
    request = getattr(jira_mod.JiraTracker("PS"), action)("PS-14", *args)
    assert request.status == "requires_host"
    assert request.server == "atlassian"
    assert request.ok is False and request.skipped is False


def test_local_slugs_remain_available_in_a_jira_project():
    tracker = jira_mod.JiraTracker("PS")
    assert tracker.resolve("add-csv-export") is None
    assert tracker.comment("add-csv-export", "hello").skipped
    assert tracker.transition("add-csv-export", "Done").skipped
