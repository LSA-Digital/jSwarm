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
