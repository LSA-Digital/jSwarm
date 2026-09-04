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
   standing in for that adopted project instead. A command that is
   genuinely install-time (bare `.venv/bin/python`, no `${JSWARM_HOME...}`
   token anywhere in the statement -- e.g. `/jSetup`, meant to run from
   inside a freshly-cloned jSwarm itself, before any project has been
   adopted) keeps the original `cwd=REPO_ROOT`; that is where its own docs
   say to run it, so testing it there is not a weaker check.
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
    # an arbitrary (adopted-project) cwd, not just from this checkout.
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
        if self.parameterized:
            # Stand in for the adopted project a real lifecycle command runs
            # from -- deliberately NOT this checkout.
            cwd = tmp_path
            if self.pythonpath_set:
                env["PYTHONPATH"] = str(REPO_ROOT)
            else:
                # Never let this test's own ambient shell mask a doc that
                # forgot PYTHONPATH -- a real user's shell would not have it.
                env.pop("PYTHONPATH", None)
        else:
            # Bare form: this repo's own convention for "run me from inside
            # the jSwarm clone itself" (e.g. /jSetup, pre-adoption). Same cwd
            # this test always used for these -- not weakened.
            cwd = REPO_ROOT

        if kind == "module":
            return [str(VENV_PYTHON), "-m", target, "--help"], env, cwd

        script = target
        if script.startswith(_JSWARM_HOME_TOKEN):
            resolved = (REPO_ROOT / script[len(_JSWARM_HOME_TOKEN):]).resolve()
        elif script.startswith("~/.claude/skills/"):
            # A script that ships alongside its own skill -- pre-install, its
            # source lives at skills/<name>/... in this checkout.
            resolved = (REPO_ROOT / "skills" / script[len("~/.claude/skills/"):]).resolve()
        elif self.parameterized:
            # Statement was parameterized elsewhere (e.g. the interpreter) but
            # this script path itself was left bare -- a real user's shell,
            # cwd'd into the adopted project, would look for it there and not
            # find it. Resolve it exactly that way so the bug surfaces.
            resolved = (cwd / script).resolve()
        else:
            resolved = (REPO_ROOT / script).resolve()
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
