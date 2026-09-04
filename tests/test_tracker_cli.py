"""The CLI boundary skills call for tracker state. Every path prints JSON on
stdout and exits 0, whether or not a tracker is configured, so a skill never
has to branch on a nonzero exit to stay non-blocking.
"""
import json

import pytest
import yaml

from jswarm.tracker.cli import main


def _repo(tmp_path, cfg=None):
    if cfg is not None:
        (tmp_path / ".jswarm").mkdir(parents=True)
        (tmp_path / ".jswarm/config.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


def test_is_configured_false_with_no_tracker(tmp_path, capsys):
    rc = main(["is-configured", "--repo", str(_repo(tmp_path))])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"configured": False}


def test_describe_names_no_tracker(tmp_path, capsys):
    rc = main(["describe", "--repo", str(_repo(tmp_path))])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"describe": "no tracker", "configured": False}


def test_resolve_with_no_tracker_prints_null(tmp_path, capsys):
    rc = main(["resolve", "add-csv-export", "--repo", str(_repo(tmp_path))])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "null"


def test_comment_with_no_tracker_is_skipped_not_a_failure(tmp_path, capsys):
    rc = main(["comment", "add-csv-export", "--text", "hello", "--repo", str(_repo(tmp_path))])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["skipped"] is True
    assert "no tracker configured" in out["message"]


def test_transition_with_no_tracker_is_skipped_not_a_failure(tmp_path, capsys):
    rc = main(["transition", "add-csv-export", "--state", "Done", "--repo", str(_repo(tmp_path))])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["skipped"] is True


def test_is_configured_true_with_jira(tmp_path, capsys):
    rc = main(["is-configured", "--repo", str(_repo(tmp_path, {"tracker": {"adapter": "jira", "key_prefix": "PS"}}))])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"configured": True}


def test_comment_without_work_id_is_a_usage_error(tmp_path):
    with pytest.raises(SystemExit):
        main(["comment", "--repo", str(_repo(tmp_path)), "--text", "hi"])


def test_unknown_adapter_still_raises(tmp_path):
    with pytest.raises(ValueError):
        main(["is-configured", "--repo", str(_repo(tmp_path, {"tracker": {"adapter": "linear"}}))])
