"""Guards against dangling cross-references in skills/.

A reference that walks a reader into nothing -- a `` `/command` `` whose
skills/ directory doesn't exist, a relative markdown link to a file that
isn't there, a citation of a step number a skill no longer has -- is the
same class of defect as a step invoking a script that was never copied.
Found while cleaning this repo for publication (task R6b): skills/jGo/ still
carried `` `/jCheckin` ``, `` `/jInfra` ``, and `` `/nfr` `` references to
skills this repo doesn't ship, and skills/jPlan/operations.md cited
`/jClose`'s old `Step 3.5` / `Step 3.6` numbering from a since-replaced
34-step version of that skill.

Two of the three checks below are generic (they scan every file under
skills/, not just the ones already fixed) so a *new* dangling reference
trips them too. The third is a targeted regression check for the specific
strings this task removed, in the files they were removed from.

Known gap (not enforced here): skills/jTest/'s UAT/certification docs and
several skills/jPlan/pattern.*.md files still reference enterprise-only
machinery (jInfra, jArchitect, jDebug, joptimize, nfr-catalog, test-catalog
-- see tests/test_core_skills_have_no_ent_calls.py's ENT list for the
authoritative name list). That sweep was not completed under this task; see
the task report.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"

RELATIVE_LINK_RE = re.compile(r"\]\((\.\./[^)#\s]+)\)")
SLASH_COMMAND_RE = re.compile(r"`/([a-zA-Z][a-zA-Z0-9_-]*)`")


def _known_skill_names(skills_root: Path) -> set[str]:
    names = {p.name for p in skills_root.iterdir() if p.is_dir() and not p.name.startswith("_")}
    shims = skills_root / "_shims"
    if shims.is_dir():
        names |= {p.name for p in shims.iterdir()}
    # Claude Code's own built-in slash commands, not jSwarm skills.
    names |= {"compact", "help", "rename"}
    # `/uat-round` is a documented-deprecated alias (skills/jTest/uat.md
    # explains, in the same sentence that names it, that it forwards to
    # `/jTest uat prepare`) rather than a reference to a missing skill.
    names |= {"uat-round"}
    # `/retros` appears only in jPrecompact/SKILL.md's changelog, narrating
    # what an old revision matched -- historical prose, not a live pointer.
    names |= {"retros"}
    return names


# Worked-example files whose own header says their paths/ids are
# illustrative -- not real cross-references, so a "broken" link inside one
# is by design, not a defect.
_ILLUSTRATIVE_FILES = {"UAT_CURRENT_ROUND_EXAMPLE.md"}


def find_broken_relative_links(skills_root: Path) -> list[str]:
    """Relative markdown links (`../...`) whose target does not exist.

    Links that ultimately resolve under docs/ (owned by a separate,
    concurrently-edited workstream) or under a runtime-only `.jswarm/` path
    are not actionable here and are excluded.
    """
    broken = []
    for md in sorted(skills_root.rglob("*.md")):
        if md.name in _ILLUSTRATIVE_FILES:
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for m in RELATIVE_LINK_RE.finditer(line):
                href = m.group(1)
                resolved = (md.parent / href).resolve()
                parts = resolved.parts
                if "docs" in parts or ".jswarm" in parts:
                    continue
                if not resolved.exists():
                    broken.append(f"{md.relative_to(skills_root.parent)}:{n}: {href}")
    return broken


def find_unresolved_slash_commands(root: Path, known: set[str]) -> list[str]:
    """Backtick-quoted `` `/command` `` references not in `known`."""
    broken = []
    for md in sorted(root.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for m in SLASH_COMMAND_RE.finditer(line):
                name = m.group(1)
                if name not in known:
                    broken.append(f"{md.relative_to(root.parent.parent)}:{n}: /{name}")
    return broken


def test_helpers_catch_a_planted_dangling_reference(tmp_path):
    """Prove the checkers actually bite before trusting them repo-wide."""
    skills = tmp_path / "skills"
    real_skill = skills / "jReal"
    real_skill.mkdir(parents=True)
    (real_skill / "SKILL.md").write_text("# real\n", encoding="utf-8")

    caller = skills / "jCaller"
    caller.mkdir()
    (caller / "SKILL.md").write_text(
        "See [the ghost skill](../jGhost/SKILL.md) and call `/jGhost`.\n"
        "This one is fine: [real](../jReal/SKILL.md) and `/jReal`.\n",
        encoding="utf-8",
    )

    broken_links = find_broken_relative_links(skills)
    assert any("jGhost/SKILL.md" in b for b in broken_links)
    assert not any("jReal/SKILL.md" in b for b in broken_links)

    known = _known_skill_names(skills)
    broken_commands = find_unresolved_slash_commands(skills, known)
    assert any(b.endswith("/jGhost") for b in broken_commands)
    assert not any(b.endswith("/jReal") for b in broken_commands)


def test_skill_relative_links_resolve():
    broken = find_broken_relative_links(SKILLS_ROOT)
    assert not broken, "dangling relative link(s) in skills/:\n" + "\n".join(broken)


def test_swept_directories_have_no_unresolved_slash_commands():
    """Directories already cleaned of this defect class must stay clean.

    Repo-wide enforcement is the known gap documented in this module's
    docstring -- skills/jTest/ and several skills/jPlan/pattern.*.md files
    still fail this check today.
    """
    known = _known_skill_names(SKILLS_ROOT)
    broken: list[str] = []
    for d in ("jGo", "jPlan", "jPrecompact"):
        broken += find_unresolved_slash_commands(SKILLS_ROOT / d, known)
    assert not broken, "dangling /command reference(s):\n" + "\n".join(broken)


def test_known_dangling_references_stay_fixed():
    """Regression guard for the exact strings this task removed."""
    checks = [
        ("jGo/SKILL.md", ("jCheckin", "jInfra", "joptimize")),
        ("jGo/session.md", ("jCheckin", "jInfra")),
        ("jGo/task-cycle.md", ("jCheckin", "jInfra", "subagent-environment")),
        ("jGo/phase-exit.md", ("jInfra", "feature-reconcile")),
        ("jPlan/operations.md", ("Step 3.5", "Step 3.6", "close-ticket.md")),
        ("jPlan/SKILL.md", ("jCheckin", "joptimize", "checkin-review.md")),
        # jCheckin still appears in this file's changelog, narrating past
        # revisions in the past tense -- that is not a live pointer.
        ("jPrecompact/SKILL.md", ("close-ticket.md", "§3.5")),
    ]
    for relpath, forbidden in checks:
        text = (SKILLS_ROOT / relpath).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{relpath}: dangling reference {token!r} reappeared"
