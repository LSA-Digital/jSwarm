import os, subprocess, yaml
from pathlib import Path

def _repo(tmp_path, name):
    d = tmp_path / name
    d.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    return d

def run(*args, home):
    env = {**os.environ, "HOME": str(home), "JSWARM_HOME": str(Path.cwd())}
    return subprocess.run(["bash", "install.sh", *args], capture_output=True, text=True, env=env)

def test_adopt_without_a_jira_key_succeeds(tmp_path):
    repo = _repo(tmp_path, "proj")
    r = run("adopt", str(repo), home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (repo / ".jswarm/.adopted").exists()
    cfg = yaml.safe_load((repo / ".jswarm/config.yaml").read_text())
    assert cfg["tracker"]["adapter"] == "none"

def test_adopt_with_a_jira_key_records_the_tracker(tmp_path):
    repo = _repo(tmp_path, "proj2")
    r = run("adopt", str(repo), "--jira-key", "PS", home=tmp_path)
    assert r.returncode == 0, r.stderr
    cfg = yaml.safe_load((repo / ".jswarm/config.yaml").read_text())
    assert cfg["tracker"] == {"adapter": "jira", "key_prefix": "PS"}

def test_adopt_preserves_an_existing_claude_md_and_settings(tmp_path):
    repo = _repo(tmp_path, "proj3")
    (repo / "CLAUDE.md").write_text("# My rules\nDo not delete me.\n")
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text('{"env": {"MINE": "1"}}')
    assert run("adopt", str(repo), home=tmp_path).returncode == 0
    memory = (repo / "CLAUDE.md").read_text()
    assert "Do not delete me." in memory and "jswarm:begin" in memory
    import json
    settings = json.loads((repo / ".claude/settings.json").read_text())
    assert settings["env"]["MINE"] == "1" and "hooks" in settings
    assert list((repo / ".jswarm/backups").iterdir()), "adoption must back up first"

def test_adopt_rejects_a_path_that_is_not_a_repository(tmp_path):
    plain = tmp_path / "notarepo"; plain.mkdir()
    r = run("adopt", str(plain), home=tmp_path)
    assert r.returncode != 0
    assert "git" in (r.stdout + r.stderr).lower()

def test_unadopt_restores_the_project(tmp_path):
    repo = _repo(tmp_path, "proj4")
    (repo / "CLAUDE.md").write_text("# Mine\n")
    run("adopt", str(repo), home=tmp_path)
    assert run("unadopt", str(repo), home=tmp_path).returncode == 0
    assert not (repo / ".jswarm/.adopted").exists()
    assert (repo / "CLAUDE.md").read_text() == "# Mine\n"

# --- Carried requirement (task-A5-brief §2, "Adoption never clobbers"):
# re-running adopt on an already-adopted repository must replace the
# managed block rather than appending a second one. Not one of the brief's
# three given test bodies verbatim, but explicitly called for by name
# ("test that").

def test_readopt_replaces_the_managed_block_not_appends(tmp_path):
    repo = _repo(tmp_path, "proj5")
    (repo / "CLAUDE.md").write_text("# Keep me\n")
    r1 = run("adopt", str(repo), "--jira-key", "AAA", home=tmp_path)
    assert r1.returncode == 0, r1.stderr
    r2 = run("adopt", str(repo), "--jira-key", "BBB", home=tmp_path)
    assert r2.returncode == 0, r2.stderr

    memory = (repo / "CLAUDE.md").read_text()
    assert memory.count("jswarm:begin") == 1
    assert memory.count("jswarm:end") == 1
    assert "Keep me" in memory
    assert "BBB" in memory and "AAA" not in memory

    cfg = yaml.safe_load((repo / ".jswarm/config.yaml").read_text())
    assert cfg["tracker"] == {"adapter": "jira", "key_prefix": "BBB"}
