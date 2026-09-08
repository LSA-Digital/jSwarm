import argparse
from pathlib import Path
import subprocess

import pytest

from jswarm.installer import cli, lockfile
from jswarm.installer.fsops import WriteContext


@pytest.mark.parametrize("pip_exit", [0, 1])
def test_upgrade_refreshes_dependencies_before_deploy_and_stops_on_failure(tmp_path, monkeypatch, pip_exit):
    source = Path.cwd()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JSWARM_HOME", str(source))
    ctx = WriteContext(dry_run=False, home=tmp_path)
    lockfile.write(ctx, tmp_path, lockfile.Lock("v1.0.0", "fixture", "complete", ["venv", "skills", "portal_config"]))
    before = lockfile.lock_path(tmp_path).read_bytes()
    actions = []

    def run(self, argv, **kwargs):
        actions.append("dependencies")
        assert argv == [str(source / ".venv/bin/python"), "-m", "pip", "install", "-q", "-r", str(source / "requirements.txt")]
        return subprocess.CompletedProcess(argv, pip_exit, "", "dependency error" if pip_exit else "")

    monkeypatch.setattr(WriteContext, "run", run)
    monkeypatch.setattr(cli, "_install_skills", lambda *a, **k: actions.append("skills"))
    monkeypatch.setattr(cli, "_public_version", lambda *a, **k: "v1.0.1")
    assert cli._cmd_upgrade(argparse.Namespace(dry_run=False)) == pip_exit
    assert actions == (["dependencies"] if pip_exit else ["dependencies", "skills"])
    if pip_exit:
        assert lockfile.lock_path(tmp_path).read_bytes() == before


def test_upgrade_deploys_feedback_without_resetting_projects_or_enabling_hud(tmp_path, monkeypatch):
    """Exercise real skill copies and backups; only the pip network call is stubbed."""
    source = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JSWARM_HOME", str(source))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    ctx = WriteContext(dry_run=False, home=tmp_path)
    lockfile.write(ctx, tmp_path, lockfile.Lock("v1.0.0", "fixture", "complete", ["venv", "skills", "portal_config"]))
    old_skill = tmp_path / ".claude/skills/jPlan/SKILL.md"
    old_skill.parent.mkdir(parents=True)
    old_skill.write_text("previous installed skill\n")
    settings = tmp_path / ".claude/settings.json"
    settings.write_text('{"statusLine":{"type":"command","command":"existing-hud"}}\n')
    project = tmp_path / "application/.jswarm/.adopted"
    project.parent.mkdir(parents=True)
    project.write_text("keep existing adoption\n")
    before_settings = settings.read_bytes()
    before_lock = lockfile.lock_path(tmp_path).read_bytes()

    def dependencies(self, argv, **kwargs):
        assert argv[1:4] == ["-m", "pip", "install"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(WriteContext, "run", dependencies)
    assert cli._cmd_upgrade(argparse.Namespace(dry_run=True)) == 0
    assert not (tmp_path / ".claude/skills/jFeedback").exists()
    assert lockfile.lock_path(tmp_path).read_bytes() == before_lock
    assert old_skill.read_text() == "previous installed skill\n"

    assert cli._cmd_upgrade(argparse.Namespace(dry_run=False)) == 0
    assert (tmp_path / ".claude/skills/jFeedback/SKILL.md").read_bytes() == (source / "skills/jFeedback/SKILL.md").read_bytes()
    assert old_skill.read_bytes() == (source / "skills/jPlan/SKILL.md").read_bytes()
    assert any(p.read_text() == "previous installed skill\n" for p in (tmp_path / ".jswarm/backups").rglob("SKILL.md"))
    assert settings.read_bytes() == before_settings
    assert project.read_text() == "keep existing adoption\n"
    assert not (tmp_path / ".jswarm/hud-install.json").exists()
