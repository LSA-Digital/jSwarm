from pathlib import Path
from jswarm.ext import ext_steps

def test_ordered_steps_from_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".jswarm/ext/jClose.d"; d.mkdir(parents=True)
    (d / "20-nfr-gate.md").write_text("# nfr\n"); (d / "10-test-catalog.md").write_text("# tc\n")
    (d / "notes.txt").write_text("ignored")
    assert [p.name for p in ext_steps("jClose")] == ["10-test-catalog.md", "20-nfr-gate.md"]

def test_missing_dir_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ext_steps("jMerge") == []
