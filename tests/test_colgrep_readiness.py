"""Clean-Mac regression: exercise the saved command via a real MCP handshake."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from jswarm.installer import cli
from jswarm.installer.colgrep import desired_registration, verify_registration
from jswarm.installer.fsops import WriteContext
from tests.mcp_stub import write_claude_stub

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed(tmp_path):
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    write_claude_stub(bindir)
    binary = bindir / "colgrep"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    env = {"HOME": str(home), "JSWARM_HOME": str(ROOT),
           "PATH": f"{bindir}:/usr/bin:/bin:{ROOT / '.venv/bin'}"}

    def run(*args):
        return subprocess.run(["bash", "install.sh", *args], cwd=ROOT, env=env,
                              text=True, capture_output=True, timeout=60)

    result = run("install", "--with-colgrep")
    assert result.returncode == 0, result.stdout + result.stderr
    return home, binary, env, run


def registration_file(home):
    return home / ".claude.json"


def set_registration(home, entry):
    path = registration_file(home)
    data = json.loads(path.read_text())
    data["mcpServers"]["colgrep"] = entry
    path.write_text(json.dumps(data))


def legacy():
    return {"type": "stdio", "command": str(ROOT / ".venv/bin/python"),
            "args": [str(ROOT / "jswarm/colgrep_mcp_server.py")], "env": {}}


def test_verify_rejects_legacy_then_resume_repairs_even_completed_lock(installed):
    home, binary, env, run = installed
    set_registration(home, legacy())
    broken = run("verify")
    assert broken.returncode == 1
    assert "legacy file-path launcher needs repair" in broken.stdout
    before = registration_file(home).read_bytes()
    lock_before = (home / ".jswarm/install.lock.yaml").read_bytes()
    preview = run("install", "--with-colgrep", "--dry-run")
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert "mcp remove" in preview.stdout and "-m jswarm.colgrep_mcp_server" in preview.stdout
    assert registration_file(home).read_bytes() == before
    assert (home / ".jswarm/install.lock.yaml").read_bytes() == lock_before
    repaired = run("install", "--with-colgrep")
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert "MCP handshake and both tools verified" in repaired.stdout
    assert json.loads(registration_file(home).read_text())["mcpServers"]["colgrep"] == desired_registration(ROOT, str(binary))
    assert run("verify").returncode == 0
    registered = registration_file(home).read_bytes()
    assert run("install", "--with-colgrep").returncode == 0
    assert registration_file(home).read_bytes() == registered


@pytest.mark.parametrize("partial", [False, True])
def test_upgrade_repairs_legacy_registration(installed, monkeypatch, partial):
    home, _, env, _ = installed
    set_registration(home, legacy())
    if partial:
        lock = home / ".jswarm/install.lock.yaml"
        state = yaml.safe_load(lock.read_text())
        state["steps_completed"].remove("colgrep")
        state["state"] = "partial"
        lock.write_text(yaml.safe_dump(state))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    original = WriteContext.run

    def run(self, argv, **kwargs):
        if "pip" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return original(self, argv, **kwargs)

    monkeypatch.setattr(WriteContext, "run", run)  # only avoid a network dependency refresh
    assert cli._cmd_upgrade(argparse.Namespace(dry_run=False)) == 0
    assert verify_registration(ROOT, env)[0]
    assert "colgrep" in yaml.safe_load((home / ".jswarm/install.lock.yaml").read_text())["steps_completed"]


def test_unrelated_registration_is_neither_executed_replaced_nor_removed(installed):
    home, _, _, run = installed
    set_registration(home, {"type": "stdio", "command": "/usr/bin/true", "args": [], "env": {}})
    before = registration_file(home).read_bytes()
    assert run("verify").returncode == 1
    result = run("install", "--with-colgrep")
    assert result.returncode == 1
    assert "refusing to replace" in result.stdout
    assert run("uninstall", "--keep-backups").returncode == 1
    assert registration_file(home).read_bytes() == before
    assert (home / ".jswarm/install.lock.yaml").exists()


def test_verify_detects_missing_registration_and_missing_binary(installed):
    home, binary, _, run = installed
    binary.unlink()
    assert run("verify").returncode == 1
    data = json.loads(registration_file(home).read_text())
    del data["mcpServers"]["colgrep"]
    registration_file(home).write_text(json.dumps(data))
    result = run("verify")
    assert result.returncode == 1
    assert "MCP registration missing" in result.stdout


def test_module_launch_from_unrelated_directory_with_spaces_and_no_inherited_pythonpath(tmp_path, monkeypatch):
    source = tmp_path / "clone with spaces"
    shutil.copytree(ROOT / "jswarm", source / "jswarm", ignore=shutil.ignore_patterns("__pycache__", "dist"))
    (source / ".venv").symlink_to(ROOT / ".venv", target_is_directory=True)
    home = tmp_path / "unrelated home"
    home.mkdir()
    binary = tmp_path / "colgrep"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    registration_file(home).write_text(json.dumps({"mcpServers": {"colgrep": desired_registration(source, str(binary))}}))
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    assert verify_registration(source, env)[0]
    # A subprocess that exits successfully without serving MCP must NOT pass.
    (source / "jswarm/colgrep_mcp_server.py").write_text("raise SystemExit(0)\n")
    assert not verify_registration(source, env)[0]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_failed_removal_preserves_lock_for_retry(installed, exit_code):
    home, binary, _, run = installed
    (binary.parent / "claude").write_text(f"#!/bin/sh\nexit {exit_code}\n")
    result = run("uninstall", "--keep-backups")
    assert result.returncode == 1
    assert "keeping the install record" in result.stdout
    assert (home / ".jswarm/install.lock.yaml").exists()
    assert "colgrep" in json.loads(registration_file(home).read_text())["mcpServers"]


def test_failed_handshake_still_records_registration_for_uninstall(installed, monkeypatch):
    home, _, env, _ = installed
    # Simulate the first registration succeeding but its runtime failing.
    lock = home / ".jswarm/install.lock.yaml"
    lock.unlink()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("jswarm.installer.colgrep.verify_registration", lambda *a, **k: (False, "fixture handshake failure"))
    assert cli._cmd_install(argparse.Namespace(dry_run=False, with_colgrep=True)) == 1
    state = yaml.safe_load(lock.read_text())
    assert "colgrep_registered" in state["steps_completed"]
    assert "colgrep" not in state["steps_completed"]
    assert cli._cmd_uninstall(argparse.Namespace(dry_run=False, keep_backups=True)) == 0
    assert "colgrep" not in json.loads(registration_file(home).read_text())["mcpServers"]
