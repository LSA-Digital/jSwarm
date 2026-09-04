"""Guards against a skill's docs claiming a named preset/gate is a mandatory
enforcement point while no step in that same skill actually invokes it.

Found twice in this repo (task: public-split loose-end sweep). `/jClose`'s
own SKILL.md, and `skills/jPlan/step-5-assemble-plan.md` describing
`/jClose`, both said `## Necessity Gate` enforcement is "reached by
`/jClose` ... through the `new-work-lint` preset" -- but `/jClose`'s actual
steps never ran `jswarm/update_ticket/cli.py --preset new-work-lint`
anywhere. A ticket could close and merge missing a required section with no
warning; the lint only ever fired if someone happened to run it by hand
afterward. Fixed by adding the invocation as `/jClose` Step 2 (mandatory,
blocking).

`skills/jPrecompact/SKILL.md` was checked for the same pattern (it is a
context checkpoint, not a closing gate, and the earlier `step-5-assemble-
plan.md` text used to also attribute this enforcement to `/jPrecompact`
before that line was corrected to name only `/jClose`) and does not make
this claim about itself; it should not, since a mid-session checkpoint
blocking on full plan-completeness would trap a plan that has not yet
reached implementation.

This is only machine-detectable because the claim names the actual preset:
a sentence of the shape "reached by `/Command` ... through the `preset-
name` preset" is a concrete, checkable assertion that `--preset preset-
name` is invoked somewhere under `skills/Command/`. Softer prose claims
("this is enforced", "must" with no named mechanism) are not reliably
machine-checkable and are not covered here -- see the sweep notes in this
task's report rather than a test that would only appear to guard them.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"

# "reached by `/Command` ... through the `preset-name` preset" -- the exact
# shape both real instances of this defect used. `.*?` is non-greedy and
# line-scoped (re.finditer over splitlines()), so an intervening
# backtick-quoted path (e.g. `` `skills/jClose/SKILL.md` ``) between the
# command and the preset name does not confuse the match: it cannot itself
# satisfy `` `([a-z0-9-]+)` preset `` (slashes/dots aren't in the character
# class), so the lazy match skips past it to the real preset mention.
GATE_CLAIM_RE = re.compile(
    r"reached by `/([A-Za-z][A-Za-z0-9_.-]*)`.*?`([a-z0-9-]+)` preset"
)


def find_uninvoked_preset_gate_claims(skills_root: Path) -> list[str]:
    """Every "reached by `/Command` ... `preset` preset" claim must be backed
    by a real `--preset <preset>` invocation somewhere under `skills/Command/`.
    """
    violations = []
    for md in sorted(skills_root.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for match in GATE_CLAIM_RE.finditer(line):
                command, preset = match.group(1), match.group(2)
                command_dir = skills_root / command
                if not command_dir.is_dir():
                    # A dangling `/command` reference is a different check's
                    # job (test_no_dangling_skill_refs.py); don't double-flag.
                    continue
                invocation_re = re.compile(rf"--preset[ '\"]*{re.escape(preset)}\b")
                invoked = any(
                    invocation_re.search(f.read_text(encoding="utf-8", errors="replace"))
                    for f in command_dir.rglob("*.md")
                )
                if not invoked:
                    violations.append(
                        f"{md.relative_to(skills_root.parent)}:{n}: claims `/{command}` "
                        f"reaches the `{preset}` preset, but no file under "
                        f"skills/{command}/ invokes `--preset {preset}`"
                    )
    return violations


def test_no_preset_gate_claims_go_uninvoked():
    """Every named-preset "enforcement point" claim anywhere in skills/ must
    be backed by a real invocation in the command it names -- generic sweep
    of the whole tree, not just the files known to have needed fixing.
    """
    violations = find_uninvoked_preset_gate_claims(SKILLS_ROOT)
    assert not violations, "uninvoked preset gate claim(s):\n" + "\n".join(violations)


def test_jclose_necessity_gate_lint_stays_wired():
    """Regression guard for the exact defect fixed in this repo: `/jClose`
    must keep actually invoking the `new-work-lint` preset it claims is the
    Necessity Gate's enforcement point, as a mandatory, blocking step -- not
    just describe it in prose.
    """
    text = (SKILLS_ROOT / "jClose" / "SKILL.md").read_text(encoding="utf-8")
    assert "--preset new-work-lint" in text, (
        "jClose/SKILL.md dropped the new-work-lint invocation while still "
        "claiming it as the Necessity Gate's mandatory enforcement point"
    )
    assert "mandatory, blocking" in text


def test_jprecompact_does_not_falsely_claim_the_necessity_gate():
    """`/jPrecompact` is a context checkpoint, not a closing gate; it must
    not claim to reach the Necessity Gate lint (it doesn't, and blocking a
    mid-session checkpoint on full plan-completeness would be wrong -- see
    this file's module docstring). If this ever starts failing because
    `/jPrecompact` legitimately took on the lint, wire the invocation first,
    then update this guard.
    """
    text = (SKILLS_ROOT / "jPrecompact" / "SKILL.md").read_text(encoding="utf-8")
    assert "new-work-lint" not in text
    assert "Necessity Gate" not in text
