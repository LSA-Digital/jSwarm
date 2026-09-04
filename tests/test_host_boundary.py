import re
from pathlib import Path
from jswarm.host import current
from jswarm.host.claude_code import ClaudeCodeHost

ALLOWED = {("jswarm", "host"), ("jswarm", "installer")}

def test_only_the_host_and_installer_layers_know_about_the_agent_host():
    pattern = re.compile(r"\.claude\b|~/\.claude|CLAUDE\.md")
    offenders = []
    for p in Path("jswarm").rglob("*.py"):
        if tuple(p.parts[:2]) in ALLOWED:
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{p}:{n}: {line.strip()}")
    assert offenders == [], "host-specific paths outside the host layer:\n" + "\n".join(offenders)

def test_claude_code_host_paths():
    h = current()
    assert h.name == "claude-code"
    assert h.skills_dir() == Path.home() / ".claude" / "skills"
    assert h.memory_path(Path("/x")) == Path("/x/CLAUDE.md")
    assert "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" == h.hook_interpreter()


def test_claude_code_host_mcp_add_argv():
    h = current()
    argv = h.mcp_add_argv("colgrep", "/path/to/python", ["/path/to/server.py"])
    assert argv == ["claude", "mcp", "add", "--scope", "user", "colgrep", "/path/to/python", "/path/to/server.py"]


def test_is_present_is_false_without_the_claude_binary_even_with_a_bare_claude_home(tmp_path, monkeypatch):
    # A bare ~/.claude directory does not mean the `claude` lifecycle binary
    # can actually be run -- it can be a leftover from an old install, a
    # dotfiles template, or anything else that creates the directory without
    # ever installing the CLI. `check` uses is_present() to tell a new user
    # "you can run Claude Code commands," so a directory alone must not
    # satisfy it. Driven with a real, controlled PATH and HOME (not by
    # mocking is_present() or shutil.which) because the original bug was
    # exactly this: on a genuinely clean machine with no `claude` on PATH,
    # is_present() still returned True.
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("PATH", str(empty_bin))
    assert ClaudeCodeHost().is_present() is False


def test_is_present_is_true_with_the_claude_binary_on_path(tmp_path, monkeypatch):
    # The positive case, for symmetry: a real `claude` on PATH is present
    # even when ~/.claude does not exist at all.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path / "home-without-dot-claude"))
    monkeypatch.setenv("PATH", str(bindir))
    assert ClaudeCodeHost().is_present() is True
