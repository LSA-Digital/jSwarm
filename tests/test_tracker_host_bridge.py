"""Exercise the executable boundary, not a live authenticated Jira session."""
import json
import subprocess
import sys

import pytest


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".jswarm").mkdir()
    (tmp_path / ".jswarm/config.yaml").write_text("tracker:\n  adapter: jira\n  key_prefix: PS\n")
    return tmp_path


def call(repo, action, *args):
    p = subprocess.run([sys.executable, "-m", "jswarm.tracker.cli", action, "PS-14", "--repo", str(repo), *args], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


CASES = [
    ("resolve", [], {"title": "Export CSV", "description": "Acceptance criteria", "status": "To Do"}),
    ("comment", ["--text", "Plan ready"], {"comment_id": "10001", "text": "Plan ready"}),
    ("transition", ["--state", "Done"], {"status": "Done"}),
]


@pytest.mark.parametrize("action,args,observed", CASES)
def test_pending_is_not_success_and_matching_observation_completes(repo, action, args, observed):
    rc, request = call(repo, action, *args)
    assert rc == 0 and request["status"] == "requires_host"
    assert request["ok"] is False and request["skipped"] is False
    receipt = dict(request, ok=True, tool="host-atlassian-tool", observed=dict(key="PS-14", **observed))
    path = repo / "receipt.json"
    path.write_text(json.dumps(receipt))
    rc, result = call(repo, action, *args, "--result-file", str(path))
    assert rc == 0
    assert result.get("key") == "PS-14" if action == "resolve" else result["ok"] is True


@pytest.mark.parametrize("field,value", [("request_id", "other"), ("work_id", "PS-15"), ("action", "comment"), ("server", "other"), ("ok", "true"), ("tool", ""), ("observed", {"key": "PS-15", "status": "Done"}), ("observed", {"key": "PS-14", "status": "In Progress"})])
def test_rejects_wrong_or_incomplete_transition_evidence(repo, field, value):
    _, request = call(repo, "transition", "--state", "Done")
    receipt = dict(request, ok=True, tool="getJiraIssue", observed={"key": "PS-14", "status": "Done"})
    receipt[field] = value
    path = repo / "receipt.json"
    path.write_text(json.dumps(receipt))
    rc, result = call(repo, "transition", "--state", "Done", "--result-file", str(path))
    assert rc == 2 and result["ok"] is False


def test_authentication_failure_is_explicit_not_skipped(repo):
    _, request = call(repo, "resolve")
    path = repo / "receipt.json"
    path.write_text(json.dumps(dict(request, ok=False, error="Authentication required")))
    rc, result = call(repo, "resolve", "--result-file", str(path))
    assert rc == 0 and result["ok"] is False and result["skipped"] is False
    assert "Authentication required" in result["message"]


def test_comment_receipt_cannot_verify_different_text(repo):
    _, request = call(repo, "comment", "--text", "Plan ready")
    path = repo / "receipt.json"
    path.write_text(json.dumps(dict(request, ok=True, tool="getJiraIssue", observed={"key": "PS-14", "comment_id": "10001", "text": "different"})))
    rc, result = call(repo, "comment", "--text", "Plan ready", "--result-file", str(path))
    assert rc == 2 and result["ok"] is False
