import os, subprocess, yaml
from pathlib import Path

def run(*args, home):
    env = {**os.environ, "HOME": str(home), "JSWARM_HOME": str(Path.cwd())}
    return subprocess.run(["bash", "install.sh", *args], capture_output=True, text=True, env=env)

def test_verify_on_a_partial_install_names_the_fix(tmp_path):
    (tmp_path / ".jswarm").mkdir()
    (tmp_path / ".jswarm/install.lock.yaml").write_text(
        yaml.safe_dump({"public_version": "v1.0.0", "state": "partial", "steps_completed": ["venv"]}))
    r = run("verify", home=tmp_path)
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "partial" in out.lower() and "install.sh install" in out

def test_verify_without_an_install_says_so(tmp_path):
    r = run("verify", home=tmp_path)
    assert r.returncode != 0 and "install.sh install" in (r.stdout + r.stderr)

def test_uninstall_dry_run_lists_what_it_would_remove(tmp_path):
    r = run("uninstall", "--dry-run", home=tmp_path)
    assert r.returncode == 0 and "would remove" in (r.stdout + r.stderr).lower()

def test_no_jira_credentials_are_needed_to_install(tmp_path):
    env_before = {k: v for k, v in os.environ.items() if "JIRA" in k.upper() or "ATLASSIAN" in k.upper()}
    assert env_before == {}, "test environment must not carry tracker credentials"
    assert run("install", "--dry-run", home=tmp_path).returncode == 0
