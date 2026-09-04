import re
from pathlib import Path

from jswarm.installer.cli import _install_skills
from jswarm.installer.fsops import WriteContext

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


def test_install_flattens_shims_to_discoverable_top_level_names(tmp_path, monkeypatch):
    # Regression for the actual installed layout: `_install_skills` used to
    # iterate `skills/`'s top-level directories and copy each one verbatim,
    # so `skills/_shims/` landed at `<dest>/_shims/`, a single directory
    # nothing that discovers skills by top-level name (like this SKILL.md
    # alias mechanism itself) would ever look inside. Checking only that the
    # shim source files exist under `skills/_shims/` (the rest of this file)
    # passed the whole time regardless, because it never looked at what an
    # install actually produces. This exercises the real installer function
    # against a throwaway destination and asserts the on-disk result.
    #
    # `_install_skills` resolves its destination through
    # `jswarm.host.current().skills_dir()`, which is `Path.home() /
    # ".claude" / "skills"` -- it does NOT use the `home` argument for the
    # destination (only for where backups of anything it overwrites go).
    # `Path.home()` reads the `HOME` environment variable, so `HOME` itself
    # must be redirected to `tmp_path`, or this test would install real
    # skill directories into the machine's actual `~/.claude/skills`.
    monkeypatch.setenv("HOME", str(tmp_path))
    dest_root = tmp_path / ".claude" / "skills"
    ctx = WriteContext(dry_run=False, home=tmp_path)
    _install_skills(ctx, home=tmp_path, source=Path.cwd(), timestamp="19700101T000000.000000Z")

    for old in RENAMES:
        if old == "jsetup":
            # jsetup -> jSetup is a case-only rename: on the case-insensitive
            # filesystem every supported host uses, dest_root/jsetup and
            # dest_root/jSetup are literally the same directory, so the
            # installer deliberately skips this one shim rather than let it
            # clobber (or be clobbered by) the real jSetup skill at the
            # identical path. Covered instead by the RENAMES.values() loop
            # below, which asserts the real jSetup skill landed intact.
            continue
        skill_md = dest_root / old / "SKILL.md"
        assert skill_md.is_file(), (
            f"shim '{old}' did not land at its own discoverable top level "
            f"({skill_md}); an install must flatten skills/_shims/* into the "
            "destination alongside the real skills."
        )

    assert not (dest_root / "_shims").exists(), (
        "a literal '_shims' directory must never appear in the installed "
        "destination; every shim belongs at its own top-level name"
    )

    for new in RENAMES.values():
        assert (dest_root / new / "SKILL.md").is_file(), new
