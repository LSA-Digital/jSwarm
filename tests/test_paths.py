from pathlib import Path

from jswarm.paths import jswarm_home, python


def test_default_home(monkeypatch):
    monkeypatch.delenv("JSWARM_HOME", raising=False)
    assert jswarm_home() == Path.home() / "dev" / "jswarm"


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("JSWARM_HOME", str(tmp_path))
    assert jswarm_home() == tmp_path
    assert python() == tmp_path / ".venv" / "bin" / "python"
