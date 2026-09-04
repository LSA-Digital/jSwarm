"""Guards against dangling cross-references in skills/.

A reference that walks a reader into nothing -- a `` `/command` `` whose
skills/ directory doesn't exist, a relative path (markdown-link or bare
backtick-quoted) to a file that isn't there, a citation of a step number a
skill no longer has -- is the same class of defect as a step invoking a
script that was never copied. Found while cleaning this repo for
publication (task R6b): skills/jGo/ still carried `` `/jCheckin` ``,
`` `/jInfra` ``, and `` `/nfr` `` references to skills this repo doesn't
ship, and skills/jPlan/operations.md cited `/jClose`'s old `Step 3.5` /
`Step 3.6` numbering from a since-replaced 34-step version of that skill.

A follow-up pre-publication review (task R6b fix round 1) found that the
generic checks below only swept skills/jGo/, skills/jPlan/, and
skills/jPrecompact/ -- a scoped guard that missed eight more unresolved
`/command` references elsewhere in skills/ (including two in
skills/jStatus/, maintainer scaffolding for the private deploy pipeline)
and, separately, that the relative-link check only understood markdown
`[text](../path)` syntax, missing bare backtick-quoted paths such as
`` `../jDebug/runtime-probes.md` ``. Both checks were widened to sweep the
whole of skills/ and check both reference shapes; every finding from that
review was fixed rather than allowlisted.

A second fix round (task R6b fix round 2) found a third dangling shape the
widened check still missed: a backtick-quoted command name written without
the leading slash, e.g. `` `jregister` `` / `` `jdeploy` `` in
skills/jPlan.ceremony-selector/SKILL.md -- maintainer-only commands that do
not exist in this repo. The command check now also catches a bare
lowercase `j`-prefixed word (this repo's real skills are always camelCase
after the `j`, e.g. `jPlan`, `jStatus`, `jCritic`; a bare all-lowercase
`jsomething` word is never a real skill name here) that resolves to no
known skill.

All checks below are generic (they scan every file under skills/, not just
files known to have needed fixing) so a *new* dangling reference trips them
too. The last is a targeted regression check for specific strings removed
across all three rounds.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"

MARKDOWN_LINK_RE = re.compile(r"\]\((\.\./[^)#\s]+)\)")
BACKTICK_RELATIVE_RE = re.compile(r"`(\.\./[^`\s]+)`")
SLASH_COMMAND_RE = re.compile(r"`/([a-zA-Z][a-zA-Z0-9_-]*)`")
# A bare (no leading slash) all-lowercase `j...` word in backticks. Every real
# skill in this repo is camelCase right after the `j` (jPlan, jStatus,
# jCritic, ...); a fully-lowercase `jsomething` never is, so this shape is a
# reliable signal for a maintainer-only command name that leaked in without
# its slash (e.g. `jregister`, `jdeploy`).
BARE_COMMAND_RE = re.compile(r"`(j[a-z]{2,})`")


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
    # `/tmp` is a filesystem path mentioned in prose ("not `/tmp`"), not a
    # command -- a false positive of the "`/word`" shape this regex looks for.
    names |= {"tmp"}
    # `json` is the data format, bare-backtick-quoted as inline code -- a
    # false positive of BARE_COMMAND_RE's "`j` + lowercase letters" shape,
    # not a dangling reference to a command named "json".
    names |= {"json"}
    return names


# Worked-example files whose own header says their paths/ids are
# illustrative -- not real cross-references, so a "broken" link or a
# `/command`-shaped mention of some other system inside one is by design,
# not a defect.
_ILLUSTRATIVE_FILES = {"UAT_CURRENT_ROUND_EXAMPLE.md"}


def find_broken_relative_links(skills_root: Path) -> list[str]:
    """Relative paths (`[text](../...)` or bare `` `../...` ``) that don't resolve.

    Links that ultimately resolve under a runtime-only `.jswarm/` path are
    not actionable here and are excluded -- that tree only exists once a
    project has been initialized, not in this repo's own checkout.

    Links resolving under docs/ are NOT excluded: skills instruct readers to
    open those documents, so a skill pointing at a docs/ page that doesn't
    exist is exactly the class of dangling reference this test exists to
    catch (see docs/merge/preflight.md and docs/merge/state-machine.md,
    which went missing under the old exclusion for a long time undetected).
    """
    broken = []
    for md in sorted(skills_root.rglob("*.md")):
        if md.name in _ILLUSTRATIVE_FILES:
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            hrefs = [m.group(1) for m in MARKDOWN_LINK_RE.finditer(line)]
            hrefs += [m.group(1) for m in BACKTICK_RELATIVE_RE.finditer(line)]
            for href in hrefs:
                resolved = (md.parent / href).resolve()
                parts = resolved.parts
                if ".jswarm" in parts:
                    continue
                if not resolved.exists():
                    broken.append(f"{md.relative_to(skills_root.parent)}:{n}: {href}")
    return broken


# A skill instructing an agent to open "``${JSWARM_HOME:-$HOME/dev/jswarm}/docs/X``" is
# citing this repo's own docs/ tree by an absolute-install-path spelling rather than a
# relative link -- the broken-relative-link check above can't see it (no `../`), but it
# is exactly the same defect: a reader (or agent) sent to open a file that isn't there.
# This repo consistently uses this exact spelling for its own docs (never for a path in
# the *consuming* project, which is written bare, e.g. `docs/plans/TICKET-XXX...md`), so
# the prefix is an unambiguous, mechanical signal -- no risk of flagging a project-relative
# path by mistake.
JSWARM_HOME_DOCS_RE = re.compile(r"`\$\{JSWARM_HOME[^}]*\}/(docs/[A-Za-z0-9_./-]+\.(?:md|json))`")
# Per-project runtime paths spelled out as a naming *convention* (a ticket key, a category,
# a date), never a literal file in this repo -- same idea as excluding `.jswarm/` above.
_PLACEHOLDER_TOKENS = ("TICKET-XXX", "FEATURE-XXX", "TOOLCATEGORY", "YYYYMMDD")


def find_broken_jswarm_home_docs_refs(skills_root: Path) -> list[str]:
    """Backtick-quoted ``${JSWARM_HOME:-...}/docs/...`` references that don't resolve.

    Found via a broader sweep after the docs/ exclusion above was removed: skills sent
    readers to `docs/jplan/*`, `docs/agent-system/*`, and `docs/templates/*` documents
    that only ever existed in the private monorepo this repo was split from, never
    copied here. A `## Changelog` section (or a file literally named CHANGELOG.md) is
    exempt: it narrates past revisions in the past tense, not a live pointer.
    """
    broken = []
    for md in sorted(skills_root.rglob("*.md")):
        if md.name in _ILLUSTRATIVE_FILES or md.name == "CHANGELOG.md":
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        changelog_from = next(
            (i for i, line in enumerate(lines) if line.strip().lower() == "## changelog"),
            None,
        )
        for n, line in enumerate(lines, 1):
            if changelog_from is not None and n - 1 >= changelog_from:
                break
            for m in JSWARM_HOME_DOCS_RE.finditer(line):
                relpath = m.group(1)
                if any(token in relpath for token in _PLACEHOLDER_TOKENS):
                    continue
                if not (REPO_ROOT / relpath).exists():
                    broken.append(f"{md.relative_to(skills_root.parent)}:{n}: {relpath}")
    return broken


def find_unresolved_slash_commands(root: Path, known: set[str]) -> list[str]:
    """Backtick-quoted command references not in `known`.

    Two shapes: `` `/command` `` (leading slash) and a bare, all-lowercase
    `` `jsomething` `` word matching this repo's `j`-prefixed command shape
    but missing the camelCase second letter every real skill here has (see
    BARE_COMMAND_RE) -- e.g. `` `jregister` ``, `` `jdeploy` ``.
    """
    broken = []
    for md in sorted(root.rglob("*.md")):
        if md.name in _ILLUSTRATIVE_FILES:
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for m in SLASH_COMMAND_RE.finditer(line):
                name = m.group(1)
                if name not in known:
                    broken.append(f"{md.relative_to(root.parent.parent)}:{n}: /{name}")
            for m in BARE_COMMAND_RE.finditer(line):
                name = m.group(1)
                if name not in known:
                    broken.append(f"{md.relative_to(root.parent.parent)}:{n}: {name} (bare, no leading slash)")
    return broken


PY_SYMBOL_REF_RE = re.compile(r"`([\w./-]+\.py)::([A-Za-z_][A-Za-z0-9_]*)`")
_SYMBOL_DEF_RE_TEMPLATE = r"^(?:async\s+def|def|class)\s+{name}\b|^{name}\s*="


def find_dangling_py_symbol_refs(root: Path, repo_root: Path) -> list[str]:
    """Backtick-quoted `` `path/to/file.py::symbol_name` `` references (this
    repo's own shape for "the exact function a step must call", e.g.
    `` `jswarm/uat_trigger.py::evaluate_phase_exit` `` in
    skills/jGo/phase-exit.md) whose file is missing, or whose file exists
    but never defines that symbol.

    A guard for "no lifecycle skill references a Python module or entry
    point absent from this repository": `implement_progress_contract`
    (skills/jGo/{SKILL,session,phase-exit,completion}.md, removed in the
    same change that added this check -- see
    test_known_dangling_references_stay_fixed) was never written in this
    `file.py::symbol` shape, which is exactly why nothing caught it: it was
    a bare, unformatted word claiming to be "the existing ... contract",
    not a citation of a specific file and symbol. A fully generic bare
    "any snake_case backtick word must be a real Python symbol" check was
    considered and rejected -- skills/ is full of legitimate bare
    snake_case backtick words that are JSON/YAML field names, not Python
    symbols (`plan_status`, `ac_complete`, `judgment_rubric`, dozens more
    across jPlan/, jTest/, jPrecompact/), so that shape would either drown
    in false positives or need a large, growing allowlist. This check
    instead covers the one unambiguous shape this repo already uses for a
    real code citation, and `implement_progress_contract`'s reappearance is
    separately pinned as a targeted regression below.
    """
    broken = []
    for md in sorted(root.rglob("*.md")):
        if md.name in _ILLUSTRATIVE_FILES:
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for m in PY_SYMBOL_REF_RE.finditer(line):
                relpath, symbol = m.group(1), m.group(2)
                target = (repo_root / relpath).resolve()
                if not target.is_file():
                    broken.append(f"{md.relative_to(root.parent)}:{n}: {relpath}::{symbol} (file not found)")
                    continue
                pattern = re.compile(_SYMBOL_DEF_RE_TEMPLATE.format(name=re.escape(symbol)), re.MULTILINE)
                if not pattern.search(target.read_text(encoding="utf-8", errors="replace")):
                    broken.append(f"{md.relative_to(root.parent)}:{n}: {relpath}::{symbol} (symbol not defined in file)")
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
        "Also see the bare-backtick form `../jGhost/other.md`.\n"
        "Then run `jghostbare` (no slash).\n"
        "This one is fine: [real](../jReal/SKILL.md) and `/jReal` and "
        "`../jReal/SKILL.md`.\n",
        encoding="utf-8",
    )

    broken_links = find_broken_relative_links(skills)
    assert any("jGhost/SKILL.md" in b for b in broken_links)
    assert any("jGhost/other.md" in b for b in broken_links)
    assert not any("jReal/SKILL.md" in b for b in broken_links)

    known = _known_skill_names(skills)
    broken_commands = find_unresolved_slash_commands(skills, known)
    assert any(b.endswith("/jGhost") for b in broken_commands)
    assert any("jghostbare" in b for b in broken_commands)
    assert not any(b.endswith("/jReal") for b in broken_commands)

    # A file that doesn't exist, and a real file missing the cited symbol.
    py_root = tmp_path / "pyroot"
    (py_root / "jswarm").mkdir(parents=True)
    (py_root / "jswarm" / "real_module.py").write_text("def real_function():\n    pass\n", encoding="utf-8")
    caller_py = skills / "jPyCaller"
    caller_py.mkdir()
    (caller_py / "SKILL.md").write_text(
        "Call `jswarm/real_module.py::real_function` (fine) and "
        "`jswarm/real_module.py::ghost_function` (missing symbol) and "
        "`jswarm/no_such_module.py::whatever` (missing file).\n",
        encoding="utf-8",
    )
    broken_symbols = find_dangling_py_symbol_refs(skills, py_root)
    assert any("ghost_function" in b and "symbol not defined" in b for b in broken_symbols)
    assert any("no_such_module.py" in b and "file not found" in b for b in broken_symbols)
    assert not any("real_function" in b for b in broken_symbols)


def test_skill_relative_links_resolve():
    broken = find_broken_relative_links(SKILLS_ROOT)
    assert not broken, "dangling relative link(s) in skills/:\n" + "\n".join(broken)


def test_no_broken_jswarm_home_docs_references():
    broken = find_broken_jswarm_home_docs_refs(SKILLS_ROOT)
    assert not broken, "dangling ${JSWARM_HOME}/docs/... reference(s):\n" + "\n".join(broken)


def test_no_dangling_py_symbol_references_anywhere_in_skills():
    """Every `` `path/to/file.py::symbol` `` under skills/ must cite a real
    file and a real symbol in this repository (see find_dangling_py_symbol_refs
    for what this does and does not cover, and why).
    """
    broken = find_dangling_py_symbol_refs(SKILLS_ROOT, REPO_ROOT)
    assert not broken, "dangling file.py::symbol reference(s):\n" + "\n".join(broken)


def test_no_unresolved_slash_commands_anywhere_in_skills():
    """Every `` `/command` `` under all of skills/ must resolve.

    Previously scoped to skills/jGo/, skills/jPlan/, and skills/jPrecompact/
    only -- a scoped guard that read as covered while eight unresolved
    references sat elsewhere in skills/ undetected. Now sweeps the whole
    tree; every finding from widening this check was fixed (not
    allowlisted) in the same task that widened it.
    """
    known = _known_skill_names(SKILLS_ROOT)
    broken = find_unresolved_slash_commands(SKILLS_ROOT, known)
    assert not broken, "dangling /command reference(s):\n" + "\n".join(broken)


def test_known_dangling_references_stay_fixed():
    """Regression guard for the exact strings removed across both rounds."""
    checks = [
        ("jGo/SKILL.md", ("jCheckin", "jInfra", "joptimize")),
        ("jGo/session.md", ("jCheckin", "jInfra")),
        ("jGo/task-cycle.md", ("jCheckin", "jInfra", "subagent-environment")),
        ("jGo/phase-exit.md", ("jInfra", "feature-reconcile")),
        ("jPlan/operations.md", ("Step 3.5", "Step 3.6", "close-ticket.md")),
        ("jPlan/SKILL.md", ("jCheckin", "joptimize", "checkin-review.md")),
        # jCheckin still appears in this file's changelog, narrating past
        # revisions in the past tense -- that is not a live pointer.
        ("jPrecompact/SKILL.md", ("close-ticket.md", "§3.5", "jDebug")),
        # jTest/diagnose.md and jTest/execution-protocol.md were deleted in
        # the jTest/jUAT public-contract cut (they carried the jDebug
        # reference this checked); their entries here are removed with them,
        # not weakened -- the generic sweeps above still cover skills/jTest/.
        ("jStatus/SKILL.md", ("jregister", "jdeploy", "governed catalog lifecycle")),
        ("jStatus/README.md", ("jregister", "jdeploy", "governed catalog lifecycle")),
        ("jPlan.ceremony-selector/SKILL.md", ("jregister", "jdeploy")),
        # clean-mac-walk.md finding 7: jGo's session/phase-exit/completion
        # companions referenced a "dashboard" mechanism -- including the bare
        # word `implement_progress_contract` -- with zero implementation
        # anywhere in this repo (see find_dangling_py_symbol_refs' docstring
        # for why that shape needed a targeted regression rather than a
        # generic check). Concluded dead/enterprise-only, per
        # pattern.dashboard-projections.md's own precedent for the same
        # family of stripped enterprise dashboard features; removed, with
        # completion.md gaining the same `jswarm.ext jGo` extension-point
        # hook the other lifecycle skills already use.
        ("jGo/SKILL.md", ("implement_progress_contract", "dashboard contract", "ready-for-merge event")),
        ("jGo/session.md", ("implement_progress_contract", "dashboard-facts", "dashboard `started`")),
        ("jGo/phase-exit.md", ("implement_progress_contract", "phase-advanced` dashboard")),
        ("jGo/completion.md", ("implement_progress_contract", "ready-for-merge` dashboard")),
    ]
    for relpath, forbidden in checks:
        text = (SKILLS_ROOT / relpath).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{relpath}: dangling reference {token!r} reappeared"
