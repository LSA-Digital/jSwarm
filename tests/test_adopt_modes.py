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

# --- Regression for task-A5A2A3: adopt is documented and tested as safe to
# re-run (e.g. to change --jira-key). Each adopt backs up the file's
# *current* state before writing, so a second adopt's backup already
# contains jswarm's own managed block -- unadopt must not restore from
# that. It must reach all the way back to the state before the *first*
# adopt, no matter how many times adopt ran in between.

def test_unadopt_after_three_adopts_restores_the_true_pre_adoption_state(tmp_path):
    repo = _repo(tmp_path, "proj6")
    original_claude_md = "# Original rules\nDo not delete me either.\n"
    (repo / "CLAUDE.md").write_text(original_claude_md)
    (repo / ".claude").mkdir()
    original_settings = '{"env": {"MINE": "1"}, "permissions": {"allow": ["Bash(ls:*)"]}}\n'
    (repo / ".claude/settings.json").write_text(original_settings)

    r1 = run("adopt", str(repo), "--jira-key", "AAA", home=tmp_path)
    assert r1.returncode == 0, r1.stderr
    r2 = run("adopt", str(repo), "--jira-key", "BBB", home=tmp_path)
    assert r2.returncode == 0, r2.stderr
    r3 = run("adopt", str(repo), "--jira-key", "CCC", home=tmp_path)
    assert r3.returncode == 0, r3.stderr

    # Sanity: after three adopts the repo really is jswarm-modified.
    adopted_md = (repo / "CLAUDE.md").read_text()
    assert "jswarm:begin" in adopted_md and "CCC" in adopted_md
    import json
    adopted_settings = json.loads((repo / ".claude/settings.json").read_text())
    assert "hooks" in adopted_settings

    r_un = run("unadopt", str(repo), home=tmp_path)
    assert r_un.returncode == 0, r_un.stderr
    assert not (repo / ".jswarm/.adopted").exists()

    # Byte-identical restore of CLAUDE.md, not merely "no jswarm block".
    assert (repo / "CLAUDE.md").read_text() == original_claude_md

    # settings.json is back to its original keys, jswarm hooks removed,
    # and nothing the user put there (including "permissions") was lost.
    restored_settings = json.loads((repo / ".claude/settings.json").read_text())
    assert restored_settings == json.loads(original_settings)
    assert "hooks" not in restored_settings

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
