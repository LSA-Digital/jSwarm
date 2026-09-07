import subprocess

from jswarm.installer.cli import _public_version
from jswarm.plan_status.jira_sync import sync_closeout
from jswarm.plan_status.state import STATE_MERGED


def test_dirty_checkout_is_never_reported_as_a_stable_release(tmp_path):
    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Release Test")
    (tmp_path / "file").write_text("original")
    git("add", "file")
    git("commit", "-qm", "fixture")
    git("tag", "v1.0.0")
    assert _public_version(tmp_path) == "v1.0.0"
    (tmp_path / "file").write_text("changed")
    assert _public_version(tmp_path) == "v1.0.0-dirty"


def test_legacy_plan_status_sync_uses_host_boundary_not_local_proxy(tmp_path):
    (tmp_path / ".jswarm").mkdir()
    (tmp_path / ".jswarm/config.yaml").write_text("tracker:\n  adapter: jira\n  key_prefix: PS\n")
    result = sync_closeout(tmp_path, ticket="PS-14", plan_status=STATE_MERGED)
    assert result["status"] == "requires_host"
    assert result["ok"] is False
    assert result["target_state"] == "Done"
