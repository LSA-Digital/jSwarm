import json
from pathlib import Path
import subprocess

import pytest

from jswarm import upgrade


def run(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    run(upstream, "init", "-q", "-b", "main")
    run(upstream, "config", "user.name", "Upgrade Test")
    run(upstream, "config", "user.email", "upgrade-test")
    run(upstream, "config", "commit.gpgsign", "false")
    (upstream / "install.sh").write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/installer-calls"\n')
    run(upstream, "add", "install.sh")
    run(upstream, "commit", "-qm", "old version")
    run(upstream, "tag", "v1.0.0")
    source = tmp_path / "source"
    run(tmp_path, "clone", "-q", str(upstream), str(source))
    run(source, "config", "user.name", "Upgrade Test")
    run(source, "config", "user.email", "upgrade-test")
    run(source, "config", "commit.gpgsign", "false")
    monkeypatch.setattr(upgrade, "ORIGINS", {str(upstream)})
    monkeypatch.setenv("JSWARM_HOME", str(source))
    (home / ".jswarm").mkdir()
    (home / ".jswarm/install.lock.yaml").write_text("state: complete\n")
    (upstream / "new-feature").write_text("new version\n")
    run(upstream, "add", "new-feature")
    run(upstream, "commit", "-qm", "new version")
    return source, upstream, home


def snapshot(source):
    return {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}


def test_check_is_read_only_and_reports_tag_separately_from_main(checkout):
    source, upstream, home = checkout
    before = snapshot(source)
    result = upgrade.check(source)
    assert result["current"] == run(source, "rev-parse", "HEAD")
    assert result["target"] == run(upstream, "rev-parse", "HEAD")
    assert result["update_available"] is True
    assert result["latest_tag"] == "v1.0.0"
    assert snapshot(source) == before
    assert not (home / "installer-calls").exists()


def test_prepare_previews_without_installing_and_install_verifies(checkout):
    source, _, home = checkout
    state = upgrade.check(source)
    upgrade.prepare(source, state["current"], state["target"])
    assert run(source, "rev-parse", "HEAD") == state["target"]
    assert (home / "installer-calls").read_text().splitlines() == ["check", "upgrade --dry-run"]
    upgrade.install(source, state["target"])
    assert (home / "installer-calls").read_text().splitlines() == ["check", "upgrade --dry-run", "upgrade", "verify"]
    assert upgrade.check(source)["update_available"] is False


@pytest.mark.parametrize("change", ["dirty", "untracked", "branch", "origin", "missing_install"])
def test_check_blocks_unsafe_checkouts(checkout, change):
    source, _, home = checkout
    if change == "dirty":
        (source / "install.sh").write_text("local edits")
    elif change == "untracked":
        (source / "personal-notes").write_text("keep me")
    elif change == "branch":
        run(source, "switch", "-qc", "custom")
    elif change == "origin":
        run(source, "remote", "set-url", "origin", "/unapproved/repository")
    else:
        (home / ".jswarm/install.lock.yaml").unlink()
    before = snapshot(source)
    with pytest.raises(upgrade.UpgradeError):
        upgrade.check(source)
    assert snapshot(source) == before


def test_remote_moving_after_review_requires_new_approval(checkout):
    source, upstream, _ = checkout
    state = upgrade.check(source)
    run(upstream, "commit", "--allow-empty", "-qm", "concurrent change")
    before = snapshot(source)
    with pytest.raises(upgrade.UpgradeError, match="changed since review"):
        upgrade.prepare(source, state["current"], state["target"])
    assert snapshot(source) == before


def test_diverged_history_does_not_merge_or_install(checkout):
    source, _, home = checkout
    run(source, "commit", "--allow-empty", "-qm", "local commit")
    state = upgrade.check(source)
    with pytest.raises(upgrade.UpgradeError, match="diverged"):
        upgrade.prepare(source, state["current"], state["target"])
    assert run(source, "rev-parse", "HEAD") == state["current"]
    assert not (home / "installer-calls").exists()


def test_edited_source_after_preview_cannot_install(checkout):
    source, _, home = checkout
    state = upgrade.check(source)
    upgrade.prepare(source, state["current"], state["target"])
    (source / "new-feature").write_text("changed after approval")
    with pytest.raises(upgrade.UpgradeError, match="local changes"):
        upgrade.install(source, state["target"])
    assert (home / "installer-calls").read_text().splitlines() == ["check", "upgrade --dry-run"]


def test_installer_failure_stops_before_verify(checkout, monkeypatch):
    source, _, _ = checkout
    calls = []
    def fail(path, *args):
        calls.append(args)
        raise upgrade.UpgradeError("dependency failed")
    monkeypatch.setattr(upgrade, "installer", fail)
    with pytest.raises(upgrade.UpgradeError):
        upgrade.install(source, run(source, "rev-parse", "HEAD"))
    assert calls == [("upgrade",)]


def test_git_environment_cannot_redirect_to_application(checkout, monkeypatch):
    source, upstream, home = checkout
    monkeypatch.setenv("GIT_DIR", str(upstream / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(upstream))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(upstream))
    assert upgrade.check(source)["current"] != upgrade.check(source)["target"]
    env = upgrade.environment(source)
    assert "GIT_DIR" not in env and "CLAUDE_CONFIG_DIR" not in env
    assert env["HOME"] == str(home)


def test_no_arguments_is_help_only(capsys):
    assert upgrade.main([]) == 0
    assert "prepare" in capsys.readouterr().out
