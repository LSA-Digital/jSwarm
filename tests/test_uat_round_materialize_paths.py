"""jswarm.uat_round_materialize.CONTROLLED_SKILL_ROOT must resolve to a real directory.

Found stale after the skill directory was renamed from ``skills/test`` to
``skills/jTest``: the constant still pointed at the old, now-nonexistent
path, so every function that reads from it (``_validate_receipt_pointers``,
pattern-template resolution, the current-round template loader) would fail
at the first real read, not at import time. A reviewer could only validate
``UAT_RULES.json`` against the real validator by patching the constant by
hand. This guards against the same silent breakage recurring on the next
rename.
"""
from jswarm.uat_round_materialize import CONTROLLED_SKILL_ROOT


def test_controlled_skill_root_resolves_to_an_existing_directory():
    assert CONTROLLED_SKILL_ROOT.is_dir(), (
        f"CONTROLLED_SKILL_ROOT ({CONTROLLED_SKILL_ROOT}) does not exist; "
        "every reader that depends on it (receipt-pointer validation, "
        "pattern-template resolution, the current-round template loader) "
        "would fail on first real use."
    )


def test_controlled_skill_root_contains_the_files_it_is_read_for():
    # These are the two concrete files jswarm/uat_round_materialize.py reads
    # out of CONTROLLED_SKILL_ROOT; a directory that exists but is missing
    # them would still be a silent-breakage trap.
    assert (CONTROLLED_SKILL_ROOT / "UAT_RULE_RECEIPTS_REFERENCE.md").is_file()
    assert (CONTROLLED_SKILL_ROOT / "UAT_CURRENT_ROUND_TEMPLATE.md").is_file()
