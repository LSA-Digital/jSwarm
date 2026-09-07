"""Execute the adopted hook from the project, not from the jSwarm source cwd."""
import json
import os
from pathlib import Path
import subprocess


def test_adopted_hook_runs_without_pythonpath_and_with_spaces_in_clone_path(tmp_path):
    source = Path(__file__).resolve().parents[1]
    alias = tmp_path / "framework with spaces"
    alias.symlink_to(source, target_is_directory=True)
    repo = tmp_path / "application with spaces"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "JSWARM_HOME": str(alias)}
    env.pop("PYTHONPATH", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    result = subprocess.run(["bash", "install.sh", "adopt", str(repo)], cwd=source,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    settings = json.loads((repo / ".claude/settings.json").read_text())
    command = settings["hooks"]["PreCompact"][0]["hooks"][0]["command"]
    before = {str(p.relative_to(repo)): p.read_bytes() for p in (repo / ".jswarm").rglob("*") if p.is_file()}
    result = subprocess.run(["sh", "-c", command], cwd=repo, env=env,
                            input=json.dumps({"hook_event_name": "PreCompact", "cwd": str(repo)}),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "/jPrecompact" in result.stdout
    assert "does not run it or save state" in result.stdout
    after = {str(p.relative_to(repo)): p.read_bytes() for p in (repo / ".jswarm").rglob("*") if p.is_file()}
    assert after == before
