import json
import os
import socket
from pathlib import Path

import pytest

from jswarm.feedback import draft, main


def test_report_redacts_selected_content_and_collects_only_safe_metadata(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("feedback must not make network calls"))
    result = draft({"summary": "Install failed", "expected": "A working server", "actual": "Traceback",
                    "excerpt": "token=abcdefghijklmnopqrst123456"})
    assert "[REDACTED]" in result["report"]["excerpt"]
    assert "abcdefghijklmnopqrst123456" not in json.dumps(result)
    assert set(result["environment"]) == {"commit", "os", "architecture", "python"}
    assert result["schema"] == "jswarm.feedback.v1"


@pytest.mark.parametrize("data", [None, [], {"summary": "x"},
    {"summary": "x", "expected": "y", "actual": "z", "credentials": "no"},
    {"summary": "x", "expected": "y", "actual": ["z"]},
    {"summary": "x", "expected": "y", "actual": "z" * 24001}])
def test_rejects_invalid_input(data):
    with pytest.raises(ValueError): draft(data)


def test_cli_never_overwrites_and_writes_private_local_file(tmp_path, capsys):
    source, output = tmp_path / "input.json", tmp_path / "draft.json"
    source.write_text(json.dumps({"summary": "Problem", "expected": "Works", "actual": "Fails"}))
    assert main(["--input", str(source), "--output", str(output)]) == 0
    original = output.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    assert "Nothing sent" in capsys.readouterr().out
    assert main(["--input", str(source), "--output", str(output)]) == 1
    assert output.read_bytes() == original
    assert main([]) == 0


def test_symlink_output_is_not_followed(tmp_path):
    source, target, output = tmp_path / "input", tmp_path / "target", tmp_path / "output"
    source.write_text(json.dumps({"summary": "Problem", "expected": "Works", "actual": "Fails"}))
    target.write_text("keep me")
    output.symlink_to(target)
    assert main(["--input", str(source), "--output", str(output)]) == 1
    assert target.read_text() == "keep me"
