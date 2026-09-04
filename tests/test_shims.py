import re
from pathlib import Path

RENAMES = {"test": "jTest", "fix": "jFix", "uat": "jUAT", "jsetup": "jSetup",
           "new-work": "jPlan", "implement": "jGo", "close-ticket": "jClose", "merge": "jMerge"}


def _case_preserved_child_exists(parent: Path, name: str) -> bool:
    # Path.exists() resolves case-insensitively on this machine's filesystem
    # (confirmed: skills/jsetup and skills/jSetup are the same inode), so a
    # naive `(parent / name).exists()` is always True once the case-only
    # rename target (jSetup) exists. Check the directory's actual
    # case-preserved entries instead, which reflects what git tracks.
    if not parent.is_dir():
        return False
    return name in {child.name for child in parent.iterdir()}


def test_every_new_name_exists_and_every_old_name_is_a_shim():
    for old, new in RENAMES.items():
        assert (Path("skills") / new / "SKILL.md").exists(), new
        shim = Path("skills/_shims") / old / "SKILL.md"
        assert shim.exists(), old
        text = shim.read_text()
        assert f"name: {old}" in text and f"/{new}" in text and "deprecat" in text.lower()
        assert not _case_preserved_child_exists(Path("skills"), old), f"{old} must exist only as a shim"


def test_no_skill_references_an_old_command_name():
    # Trailing boundary excludes a following hyphen (not just \b) because the
    # old command words also appear as prefixes of unrelated, legitimately
    # unrenamed hyphenated path/filename fragments in this corpus: the
    # "uat-round" alias, "uat-results"/"uat-assets" directory names, the
    # "merge-step-10.6" log-dir name, and the "test-uat.lifecycle.md" filename
    # (only its containing directory was renamed, not the file itself). A
    # bare \b treats those identically to real command references and cannot
    # tell them apart; excluding a following hyphen removes that whole class
    # of false positive while still catching every unqualified /old use.
    old = re.compile(r"(?<![\w/])/(test|fix|uat|jsetup|new-work|implement|close-ticket|merge)(?![A-Za-z0-9_-])")
    for p in Path("skills").rglob("*.md"):
        if "_shims" in p.parts:
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            assert not old.search(line), f"{p}:{n}: {line.strip()}"
