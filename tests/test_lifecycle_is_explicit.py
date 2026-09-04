import re
from pathlib import Path

LIFECYCLE = ["jPlan", "jGo", "jTest", "jUAT", "jFix", "jClose", "jMerge"]
NEXT = {"jPlan": "/jGo", "jGo": "/jTest", "jTest": "/jUAT", "jUAT": "/jFix", "jFix": "/jTest", "jClose": "/jMerge"}

def test_no_core_skill_instructs_running_another_lifecycle_command():
    # Naming the next command is required. Instructing the agent to RUN it is not allowed.
    bad = re.compile(r"(?:^|\b)(?:now |then |automatically )?(?:run|invoke|execute|call)\s+/(?:" + "|".join(LIFECYCLE) + r")\b", re.IGNORECASE | re.MULTILINE)
    offenders = []
    for name in LIFECYCLE:
        for p in (Path("skills") / name).rglob("*.md"):
            for n, line in enumerate(p.read_text().splitlines(), 1):
                if bad.search(line):
                    offenders.append(f"{p}:{n}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)

def test_each_skill_ends_by_naming_the_next_command():
    for name, nxt in NEXT.items():
        text = (Path("skills") / name / "SKILL.md").read_text()
        assert nxt in text, f"{name} must tell the user to type {nxt}"
