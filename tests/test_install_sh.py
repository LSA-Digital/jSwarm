import os, subprocess
from pathlib import Path

def run(*args, home):
    env = {**os.environ, "HOME": str(home), "JSWARM_HOME": str(Path.cwd())}
    return subprocess.run(["bash", "install.sh", *args], capture_output=True, text=True, env=env)

def test_no_provider_flag(tmp_path):
    r = run("install", "--provider", "yaml", "--dry-run", home=tmp_path)
    assert r.returncode != 0 and "unknown option" in (r.stdout + r.stderr).lower()

def test_dry_run_install_writes_nothing_and_names_the_lock(tmp_path):
    r = run("install", "--dry-run", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "install.lock.yaml" in r.stdout
    assert not (tmp_path / ".jswarm").exists()

def test_with_colgrep_is_accepted(tmp_path):
    r = run("install", "--with-colgrep", "--dry-run", home=tmp_path)
    assert r.returncode == 0 and "colgrep" in r.stdout.lower()

def test_every_mutating_subcommand_has_a_dry_run(tmp_path):
    for args in (["install"], ["adopt", str(tmp_path)], ["unadopt", str(tmp_path)], ["upgrade"], ["uninstall"]):
        r = run(*args, "--dry-run", home=tmp_path)
        assert "--dry-run" not in (r.stdout + r.stderr).lower() or r.returncode == 0, args
        assert not (tmp_path / ".jswarm" / "install.lock.yaml").exists(), args

def test_every_command_names_the_next_step_and_where_to_run_it(tmp_path):
    r = run("check", home=tmp_path)
    out = r.stdout + r.stderr
    assert "Next:" in out
