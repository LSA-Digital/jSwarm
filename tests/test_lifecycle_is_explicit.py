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


# The install/lifecycle contract is not just "the next command is mentioned somewhere in
# a 500-line skill file" -- confusing the three operating contexts (the jSwarm clone's own
# shell, the target project's agent session, a plain terminal) is the most common way a
# first install fails, so the successor must be named AT THE CLOSE of the skill, together
# with which of those contexts it runs in. `test_each_skill_ends_by_naming_the_next_command`
# above only proves the substring appears somewhere; this proves it appears in the file's
# closing section and names a context.
_TAIL_LINES = 20
_CONTEXT_MARKERS = ("agent session", "jSwarm clone", "this terminal")


def _tail_text(name: str) -> str:
    lines = [ln for ln in (Path("skills") / name / "SKILL.md").read_text().splitlines() if ln.strip()]
    return "\n".join(lines[-_TAIL_LINES:])


def test_each_skill_names_its_successor_and_context_in_its_closing_lines():
    for name, nxt in NEXT.items():
        tail = _tail_text(name)
        assert nxt in tail, f"{name}: successor {nxt!r} must appear in the closing lines, not just earlier in the file"
        assert any(marker in tail for marker in _CONTEXT_MARKERS), (
            f"{name}: closing lines naming {nxt!r} must also say which context it runs in "
            f"(one of {_CONTEXT_MARKERS!r})"
        )


def test_jmerge_explicitly_states_it_is_the_end_of_the_loop():
    # jMerge has no successor -- NEXT deliberately omits it. That must be a stated fact,
    # not silence a reader could mistake for an unfinished skill.
    tail = _tail_text("jMerge")
    assert re.search(r"no next lifecycle command|last stage of the loop|end of the loop", tail, re.IGNORECASE), (
        "jMerge must explicitly say, in its closing lines, that there is no next lifecycle command"
    )
