import re
from pathlib import Path
from jswarm.host import current

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
