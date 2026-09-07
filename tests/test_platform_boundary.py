import re, sys
from pathlib import Path
from jswarm.platform import current

def test_platform_detection_lives_only_in_the_platform_layer():
    pattern = re.compile(r"sys\.platform|platform\.system\(\)|\bdarwin\b|Homebrew|/opt/homebrew|xcode-select|launchctl", re.IGNORECASE)
    offenders = []
    for p in Path("jswarm").rglob("*.py"):
        if p.parts[1] == "platform":
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{p}:{n}: {line.strip()}")
    assert offenders == [], "platform specifics outside the platform layer:\n" + "\n".join(offenders)

def test_os_does_not_add_a_prerequisite_gate(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/tools/{name}")
    for name in ("darwin", "linux", "win32"):
        monkeypatch.setattr(sys, "platform", name)
        checks = current().check_prerequisites()
        assert {check.name for check in checks} == {
            "Python 3.12+", "git", "gh", "claude-code", "Rust toolchain (for --with-colgrep)"
        }
        assert not hasattr(current(), "daemon_install")
        assert not hasattr(current(), "shell_profile")
