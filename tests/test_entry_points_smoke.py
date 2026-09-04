"""Entry-point smoke test: every command a skill document tells a user to run
must actually be executable **from where the documentation says to run it**.

This is the test that would have caught `/jSetup` being dead on arrival
(`jswarm/installer/jsetup/` had no `__main__.py`, so `python -m
jswarm.installer.jsetup` failed with "is a package and cannot be directly
executed"). No existing test ran that command, or any other command out of
a skill document, before this file: everything else in this suite tests
source files and function calls, never the literal command line a new user
is told to type.

## Working-directory correction (public-repo rehearsal, 2026-09-04)

A clean-Mac rehearsal of the full lifecycle (`/jPlan` through `/jMerge`) found
that every one of thirteen `jswarm.*` invocations across seven lifecycle
skill documents (`jPlan/operations.md`, `jPlan/mode-lite.md`,
`jPlan/step-5-assemble-plan.md`, `jTest/SKILL.md`, `jUAT/SKILL.md`,
`jClose/SKILL.md`, `jFix/SKILL.md`, `jMerge/SKILL.md`, `jGo/completion.md`)
raised `ModuleNotFoundError` on first contact -- because an agent following
`/jPlan` runs its first command from the *adopted project*, not from the
jSwarm clone, and `python -m jswarm.X` only resolves when the process's cwd
is the jSwarm clone (or `PYTHONPATH` names it).

This test existed the whole time and never caught it, for two reasons, both
fixed here:

1. **Wrong cwd.** Every invocation ran with `cwd=REPO_ROOT` -- i.e. always
   from inside the jSwarm clone itself, which is exactly the one cwd every
   real lifecycle command is guaranteed to work from and exactly the one
   cwd a user never actually runs it from. A command that only resolves
   `${JSWARM_HOME:-$HOME/dev/jswarm}`-parameterized paths -- the form this
   repo uses everywhere to declare "this must work from an arbitrary
   adopted-project directory" -- is now run from a fresh `tmp_path`
   standing in for that adopted project instead.

   `/jSetup` was originally carved out of that rule here, on the reasoning
   that a bare `.venv/bin/python -m jswarm.installer.jsetup` (no
   `${JSWARM_HOME...}` token) is "genuinely install-time" and meant to run
   from inside a freshly-cloned jSwarm, before any project has been
   adopted -- so testing it with `cwd=REPO_ROOT` was treated as no weaker
   a check than testing it anywhere else. That reasoning was wrong: the
   product's own `install.sh adopt` ends by telling the user to run
   `/jSetup` next, *from the adopted project it just created* (see
   `jswarm/installer/adopt.py`'s "Next: /jSetup" message), which has no
   `.venv` of its own at all. A bare-relative `/jSetup` invocation is dead
   on arrival there (`zsh: no such file or directory: .venv/bin/python`),
   and this test's `cwd=REPO_ROOT` carve-out is exactly what let that ship.
   `/jSetup`'s own command now carries the `${JSWARM_HOME...}` token like
   every other lifecycle command. (This carve-out itself no longer exists --
   see "Second correction" below for why, and for what replaced it.)
2. **Wrong environment.** A `${JSWARM_HOME...}`-parameterized `-m jswarm.X`
   invocation needs `PYTHONPATH` naming the clone (`-m` puts cwd, not the
   script's own directory, on `sys.path[0]`); the correct spelling already
   existed in this repo (`jPrecompact/SKILL.md`'s harness-health line:
   `PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.X`).
   This test now looks for that exact `PYTHONPATH=` assignment in the
   statement and, only when present, sets it for the subprocess; a
   parameterized `-m jswarm.X` invocation missing it gets no `PYTHONPATH`
   help at all (not even from this test's own ambient environment, which is
   explicitly stripped) and so fails exactly the way a real user's shell
   would.

The file scan was also widened from `skills/*/SKILL.md` (top-level only) to
every `.md` under `skills/` (`skills/**/*.md`), matching the generic,
whole-tree philosophy `test_no_dangling_skill_refs.py` already uses: nine of
the thirteen ModuleNotFoundError commands the rehearsal found live in
companion files (`operations.md`, `mode-lite.md`, `step-5-assemble-plan.md`,
`completion.md`), not in a top-level `SKILL.md`, and a scan that only ever
looked at `SKILL.md` would keep missing them regardless of the cwd fix.

## Second correction: the carve-out itself was the defect class (2026-09-04)

The `/jSetup` carve-out above was doc-text-driven and generic: any command
whose doc text lacked the `${JSWARM_HOME...}` token fell back to
`cwd=REPO_ROOT`, on the unstated assumption that no other command in this
repo was ever documented in bare form. That assumption was false. Seven
other skills (`code-overview`, `jPlan.ceremony-selector`,
`uat-author-scenario`, `uat-extract-assets`, `uat-populate-content`,
`update-ticket`, `update-plan`) had commands written the exact same bare way
`/jSetup` originally was, so the same carve-out silently exempted them too,
and this suite stayed green while (per hand reproduction, matching
`code-overview/cli.py`'s failure) most of them were dead on arrival from an
adopted project the same way `/jSetup` was.

This is the fourth time a test in this repository reasoned its way to a
pass instead of matching what the product tells the user, so the fix this
time closes the class rather than the instance: the doc-text-driven
exemption is gone. Every command is now tested from `cwd=tmp_path` (the
adopted-project stand-in) by default, full stop -- doc phrasing has no say
in it any more. The only way out is `_RUNS_FROM_REPO_ROOT_ONLY` below, a
hand-maintained, hand-justified allowlist keyed by exact `Invocation.raw`
label; nothing populates it automatically, and nothing in a skill document
can add to it. As of this fix it is empty: every entry point this suite has
ever found turned out to belong at `cwd=tmp_path`, `/jSetup` included.

## What counts as a command here

Every ```bash fenced block, and every single-line single-backtick code span
(e.g. "Run `` `...python -m jswarm.ext jFix` ``."), in every `.md` file
under `skills/` is parsed (not hardcoded -- a future skill that adds a
broken command is caught by this same scan) for Python entry-point
invocations: `.venv/bin/python -m <module>` or `.venv/bin/python
<script.py>`, optionally prefixed with the `${JSWARM_HOME:-$HOME/dev/jswarm}/`
variable expansion every cross-repo command uses, or rooted at
`~/.claude/skills/<skill>/` for a script that ships alongside its own
skill. The inline-backtick shape matters: five of the rehearsal's thirteen
commands (every `/jTest`, `/jUAT`, `/jClose`, `/jFix`, `/jMerge` extension
step) are written as prose, not inside a fenced block, and a fence-only
scan never sees them. Non-Python commands (git, colgrep, mkdir, shell
conditionals) are out of scope: "module cannot be executed" is a Python
import/execution failure mode specifically, not something `git fetch` can
suffer from.

## Two ways a command can end non-zero, and why only one is a bug

Running the *actual* example command (with its placeholder `<ID>`,
`<PROJECT_ROOT>`, ticket-key arguments, or a real tracker) is neither
possible nor the point here. Instead every extracted target is invoked as
`<python> -m <module> --help` (or `<python> <script> --help`), which any
argparse-based CLI answers immediately, before touching a project, a
tracker, or the filesystem:

- **Exit 0, or a non-zero exit whose output is the module's own logic**
  (argparse's "the following arguments are required", a custom "not an
  adopted project" message, anything with its own traceback) -- the module
  loaded and ran. This is the "needs a real project or a tracker" case the
  task description says is fine, and it passes here.
- **A failure whose message is Python's own import/exec machinery talking**
  -- "No module named", "is a package and cannot be directly executed",
  "can't open file ... No such file or directory", `ModuleNotFoundError`,
  bare `ImportError` -- means the target was never reached at all. This is
  the `/jSetup` bug's failure class, and it is never acceptable: it fails
  this test.

## The one legitimate exception: enterprise-only scripts

A handful of commands (the ColGREP generation reaper, the agent-run ledger,
the test-catalog scripts) point at scripts that exist only in the private
enterprise repo and are correctly absent from this public core. Their
authors already knew this and wrote the command with a trailing `|| true`
(or an equivalent `|| echo '...'` fallback) specifically so a missing
script never blocks the surrounding skill. This scanner honors that same
signal instead of hardcoding the three script names: any statement whose
source text contains `|| true` or `|| echo` is treated as already
documented as allowed to fail, and is exercised but never asserted on.
Every other command has no such marker and is asserted strictly.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

_FENCE_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)
_STATEMENT_SPLIT_RE = re.compile(r"\n\s*\n")
_INVOKE_RE = re.compile(
    r"(?:\$\{JSWARM_HOME:-\$HOME/dev/jswarm\}/)?"
    r"\.venv/bin/python\"?\s+"
    r"(?:-m\s+(?P<module>[\w.]+)"
    r"|(?P<script>(?:\$\{JSWARM_HOME:-\$HOME/dev/jswarm\}/)?[~./\w-]+\.py))"
)
_FAIL_OPEN_MARKERS = ("|| true", "|| echo")

# The literal parameterized-interpreter token this repo uses everywhere to mean
# "resolve this from the jSwarm clone regardless of the caller's cwd" (see
# module docstring). Its presence anywhere in a statement is the signal this
# test uses to decide the command must be runnable from an arbitrary adopted
# project, not just from inside this checkout.
_JSWARM_HOME_TOKEN = "${JSWARM_HOME:-$HOME/dev/jswarm}/"
_JSWARM_HOME_RE = re.compile(r"\$\{JSWARM_HOME:-\$HOME/dev/jswarm\}")
# The exact spelling this repo already used (jPrecompact/SKILL.md's
# harness-health line) before this correction, for handing `-m jswarm.X` the
# PYTHONPATH it needs to resolve the package from an arbitrary cwd.
_PYTHONPATH_RE = re.compile(r'PYTHONPATH="\$\{JSWARM_HOME:-\$HOME/dev/jswarm\}"')

# Every command in this repo is, by default, tested as if run from an
# arbitrary adopted project (cwd=tmp_path, see Invocation.resolve below) --
# NOT from this checkout. That default used to be inverted: a command was
# only held to that standard when its doc text happened to carry the
# ${JSWARM_HOME...} token, so anything the docs described in bare form was
# silently tested from cwd=REPO_ROOT instead, which is exactly the one cwd
# every command in this repo is guaranteed to work from and exactly the one
# cwd a real lifecycle command never actually runs from. That doc-text-driven
# exemption is what let seven skills (code-overview, jPlan.ceremony-selector,
# uat-author-scenario, uat-extract-assets, uat-populate-content, update-ticket,
# update-plan) ship broken exactly the way `/jSetup` was broken, with this
# suite fully green throughout -- see module docstring, "2026-09-04" section.
#
# This dict is the ONLY escape hatch left. It is not doc-text-driven and
# cannot be widened by editing a skill document: an entry must be added here,
# by hand, keyed by the exact `Invocation.raw` label, with a comment stating
# the concrete, verified reason that specific command cannot run from an
# adopted project. A generic reason ("it's an install-time command") is not
# enough -- that was the reasoning that produced the original bug, and it
# was wrong even for the one command it was written for. Do not add an entry
# to make a failing test pass; add one only after confirming by hand that
# running the command from an adopted project is impossible in principle,
# not just currently undocumented.
_RUNS_FROM_REPO_ROOT_ONLY: dict[str, str] = {
    # (empty) -- as of 2026-09-04, every entry point this suite has ever
    # found, /jSetup included, is documented (or has been fixed) to resolve
    # via the ${JSWARM_HOME:-$HOME/dev/jswarm}/ token and therefore runs
    # correctly from an adopted project. No command has yet demonstrated a
    # genuine need for this escape hatch.
}

# Python's own import/exec machinery talking, before the target module's own
# code (argparse included) ever got a chance to run.
_DEAD_ON_ARRIVAL_SIGNATURES = (
    "No module named",
    "is a package and cannot be directly executed",
    "can't open file",
    "ModuleNotFoundError",
    "ImportError",
)


@dataclass(frozen=True)
class Invocation:
    skill_md: Path
    raw: str  # "module:<dotted.path>" or "script:<original text, prefix included>"
    fail_open: bool
    # True when at least one occurrence of this target names it via the
    # ${JSWARM_HOME...} token -- i.e. the docs claim this command works from
    # an arbitrary (adopted-project) cwd, not just from this checkout. This
    # no longer decides cwd (see _RUNS_FROM_REPO_ROOT_ONLY above); it is kept
    # only to drive test_parameterized_module_invocations_all_carry_pythonpath,
    # a doc-consistency lint distinct from the cwd this test actually runs
    # the command from.
    parameterized: bool
    # True only when every parameterized occurrence of this target also sets
    # PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" ahead of the interpreter.
    pythonpath_set: bool

    @property
    def label(self) -> str:
        return f"{self.skill_md.relative_to(REPO_ROOT)}: {self.raw}"

    def resolve(self, tmp_path: Path) -> tuple[list[str], dict[str, str], Path]:
        """Build (command, env, cwd) the way a real user's shell would see it."""
        kind, target = self.raw.split(":", 1)
        env = dict(os.environ)
        if self.raw in _RUNS_FROM_REPO_ROOT_ONLY:
            # Explicit, hand-added, hand-justified exception -- see the
            # comment on _RUNS_FROM_REPO_ROOT_ONLY above for what it takes to
            # add one. Ambient env is left alone, same as this checkout's own
            # shell would have it.
            cwd = REPO_ROOT
        else:
            # The strict default for every command in this repo: stand in
            # for the adopted project a real lifecycle command runs from --
            # deliberately NOT this checkout. Doc text (the ${JSWARM_HOME...}
            # token, or lack of it) no longer has any say over this; only
            # _RUNS_FROM_REPO_ROOT_ONLY does.
            cwd = tmp_path
            if self.pythonpath_set:
                env["PYTHONPATH"] = str(REPO_ROOT)
            else:
                # Never let this test's own ambient shell mask a doc that
                # forgot PYTHONPATH -- a real user's shell would not have it.
                env.pop("PYTHONPATH", None)

        if kind == "module":
            return [str(VENV_PYTHON), "-m", target, "--help"], env, cwd

        script = target
        if script.startswith(_JSWARM_HOME_TOKEN):
            resolved = (REPO_ROOT / script[len(_JSWARM_HOME_TOKEN):]).resolve()
        elif script.startswith("~/.claude/skills/"):
            # A script that ships alongside its own skill -- pre-install, its
            # source lives at skills/<name>/... in this checkout.
            resolved = (REPO_ROOT / "skills" / script[len("~/.claude/skills/"):]).resolve()
        else:
            # Bare script path (no ${JSWARM_HOME...} token, no ~/.claude/skills/
            # prefix): resolve it exactly the way a real user's shell would,
            # relative to cwd. For an unlisted command that is tmp_path, so a
            # bare-relative script path surfaces as "can't open file" here
            # exactly as it would for a real user -- this is the check that
            # catches the defect class, not a special case to route around.
            resolved = (cwd / script).resolve()
        return [str(VENV_PYTHON), str(resolved), "--help"], env, cwd


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def _record(seen: dict, skill_md: Path, statement: str) -> None:
    m = _INVOKE_RE.search(statement)
    if not m:
        return
    if m.group("module"):
        raw = f"module:{m.group('module')}"
    else:
        raw = f"script:{m.group('script')}"
    fail_open = any(marker in statement for marker in _FAIL_OPEN_MARKERS)
    parameterized = bool(_JSWARM_HOME_RE.search(statement))
    pythonpath_set = bool(_PYTHONPATH_RE.search(statement))
    key = (skill_md, raw)
    bucket = seen.setdefault(key, {"fail_open": [], "parameterized": [], "pythonpath_set": []})
    bucket["fail_open"].append(fail_open)
    bucket["parameterized"].append(parameterized)
    bucket["pythonpath_set"].append(pythonpath_set)


def _extract_invocations() -> list[Invocation]:
    # key -> (fail_open flags seen, parameterized flags seen, pythonpath flags seen)
    seen: dict[tuple[Path, str], dict[str, list[bool]]] = {}
    for skill_md in sorted(REPO_ROOT.glob("skills/**/*.md")):
        text = skill_md.read_text(encoding="utf-8")
        for block in _FENCE_RE.findall(text):
            for statement in _STATEMENT_SPLIT_RE.split(block):
                _record(seen, skill_md, statement)
        # A command can also be given as prose ("Run `` `...python -m X` ``.")
        # rather than inside a ```bash fence -- e.g. every /jTest, /jUAT,
        # /jClose, /jFix, /jMerge extension-step line ("Run `.../jswarm.ext
        # jFix`."). A fenced-block-only scan would silently never see these
        # five of the rehearsal's thirteen commands at all. Each single-line,
        # single-backtick code span is its own statement.
        for line in text.splitlines():
            for span in _INLINE_CODE_RE.findall(line):
                _record(seen, skill_md, span)

    invocations = []
    for (skill_md, raw), flags in seen.items():
        # Strict wins for fail_open, same as before: a target seen even once
        # without the marker stays strict.
        fail_open = all(flags["fail_open"])
        # This flag is now only a doc-consistency signal (see the comment on
        # Invocation.parameterized above) -- it no longer decides cwd, so it
        # no longer needs a regression-guard pin the way it used to.
        parameterized = any(flags["parameterized"])
        # Strict wins here too: PYTHONPATH must be set on every parameterized
        # occurrence, or a real user hitting the unset one would still fail.
        pythonpath_set = all(flags["pythonpath_set"]) if any(flags["pythonpath_set"]) else False
        invocations.append(
            Invocation(
                skill_md=skill_md,
                raw=raw,
                fail_open=fail_open,
                parameterized=parameterized,
                pythonpath_set=pythonpath_set,
            )
        )
    return sorted(invocations, key=lambda i: (str(i.skill_md), i.raw))


ALL_INVOCATIONS = _extract_invocations()
STRICT_INVOCATIONS = [i for i in ALL_INVOCATIONS if not i.fail_open]
FAIL_OPEN_INVOCATIONS = [i for i in ALL_INVOCATIONS if i.fail_open]


def _run(inv: Invocation, tmp_path: Path) -> subprocess.CompletedProcess:
    command, env, cwd = inv.resolve(tmp_path)
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _dead_on_arrival(result: subprocess.CompletedProcess) -> str | None:
    """The matching signature if `result` looks like the target was never
    reached at all (Python's import/exec machinery, not the target's own
    code, produced the failure); None otherwise.
    """
    if result.returncode == 0:
        return None
    combined = result.stdout + result.stderr
    for sig in _DEAD_ON_ARRIVAL_SIGNATURES:
        if sig in combined:
            return sig
    return None


def test_scan_found_at_least_the_known_entry_points():
    # A sanity floor on the scanner itself: if this drops to zero, the
    # regex broke, not every skill stopped shipping commands.
    assert len(ALL_INVOCATIONS) >= 20, (
        f"only found {len(ALL_INVOCATIONS)} python invocations across "
        "skills/**/*.md -- the extraction regex may have broken"
    )
    labels = {i.raw for i in ALL_INVOCATIONS}
    assert "module:jswarm.installer.jsetup" in labels, (
        "the scanner did not find /jSetup's own command "
        "(jswarm.installer.jsetup) -- it must, since that command is the "
        "one this whole test exists to catch"
    )


def test_parameterized_module_invocations_all_carry_pythonpath():
    # A targeted regression guard for the exact bug the rehearsal found:
    # a ${JSWARM_HOME...}-parameterized `-m jswarm.X` invocation with no
    # PYTHONPATH is dead on arrival from any cwd but this checkout's own.
    # (test_entry_point_is_not_dead_on_arrival below would also catch this,
    # by actually running the command -- this is a cheaper, earlier signal.)
    offenders = [
        i.label
        for i in ALL_INVOCATIONS
        if i.raw.startswith("module:") and i.parameterized and not i.pythonpath_set
    ]
    assert not offenders, (
        "parameterized `-m jswarm.X` invocation(s) missing "
        'PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}":\n' + "\n".join(offenders)
    )


@pytest.mark.parametrize("inv", STRICT_INVOCATIONS, ids=lambda i: i.label)
def test_entry_point_is_not_dead_on_arrival(inv: Invocation, tmp_path):
    result = _run(inv, tmp_path)
    signature = _dead_on_arrival(result)
    assert signature is None, (
        f"{inv.label} could not be reached at all (matched {signature!r}); "
        f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n\n"
        "It is fine for a command to fail because it needs a real project "
        "or a tracker (a non-zero exit with the module's own error/usage "
        "text) -- it is not fine for the module or script itself to be "
        "unreachable. If this command is genuinely optional (an "
        "enterprise-only script), mark it fail-open in the doc with a "
        "trailing `|| true` the way the other optional commands are."
    )


@pytest.mark.parametrize("inv", FAIL_OPEN_INVOCATIONS, ids=lambda i: i.label)
def test_fail_open_entry_point_still_runs_without_hanging_or_crashing_the_test(inv: Invocation, tmp_path):
    # Documented-optional commands (`|| true` / `|| echo` in the doc itself,
    # e.g. the enterprise-only ColGREP reaper and test-catalog scripts) are
    # exercised -- a hang or a crash of the *test harness* itself would still
    # be worth knowing about -- but never asserted on: any exit code,
    # dead-on-arrival or not, is accepted for these.
    _run(inv, tmp_path)
