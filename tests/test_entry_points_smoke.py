"""Entry-point smoke test: every command a SKILL.md tells a user to run must
actually be executable.

This is the test that would have caught `/jSetup` being dead on arrival
(`jswarm/installer/jsetup/` had no `__main__.py`, so `python -m
jswarm.installer.jsetup` failed with "is a package and cannot be directly
executed"). No existing test ran that command, or any other command out of
a SKILL.md, before this file: everything else in this suite tests source
files and function calls, never the literal command line a new user is told
to type.

## What counts as a command here

Every ```bash fenced block in every `skills/*/SKILL.md` is parsed (not
hardcoded -- a future skill that adds a broken command is caught by this
same scan) for Python entry-point invocations: `.venv/bin/python -m
<module>` or `.venv/bin/python <script.py>`, optionally prefixed with the
`${JSWARM_HOME:-$HOME/dev/jswarm}/` variable expansion every cross-repo
command uses, or rooted at `~/.claude/skills/<skill>/` for a script that
ships alongside its own skill. Non-Python commands (git, colgrep, mkdir,
shell conditionals) are out of scope: "module cannot be executed" is a
Python import/execution failure mode specifically, not something `git
fetch` can suffer from.

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

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

_FENCE_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)
_STATEMENT_SPLIT_RE = re.compile(r"\n\s*\n")
_INVOKE_RE = re.compile(
    r"(?:\$\{JSWARM_HOME:-\$HOME/dev/jswarm\}/)?"
    r"\.venv/bin/python\s+"
    r"(?:-m\s+(?P<module>[\w.]+)"
    r"|(?P<script>(?:\$\{JSWARM_HOME:-\$HOME/dev/jswarm\}/)?[~./\w-]+\.py))"
)
_FAIL_OPEN_MARKERS = ("|| true", "|| echo")

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
    raw: str  # "module:<dotted.path>" or "script:<original text>"
    fail_open: bool

    @property
    def label(self) -> str:
        return f"{self.skill_md.relative_to(REPO_ROOT)}: {self.raw}"

    def command(self) -> list[str]:
        kind, target = self.raw.split(":", 1)
        if kind == "module":
            return [str(VENV_PYTHON), "-m", target, "--help"]
        script = target.replace("${JSWARM_HOME:-$HOME/dev/jswarm}/", "")
        if script.startswith("~/.claude/skills/"):
            # A script that ships alongside its own skill -- pre-install,
            # its source lives at skills/<name>/... in this checkout.
            script = "skills/" + script[len("~/.claude/skills/") :]
        resolved = (REPO_ROOT / script).resolve()
        return [str(VENV_PYTHON), str(resolved), "--help"]


def _extract_invocations() -> list[Invocation]:
    found: dict[tuple[Path, str], bool] = {}
    for skill_md in sorted(REPO_ROOT.glob("skills/*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        for block in _FENCE_RE.findall(text):
            for statement in _STATEMENT_SPLIT_RE.split(block):
                m = _INVOKE_RE.search(statement)
                if not m:
                    continue
                if m.group("module"):
                    raw = f"module:{m.group('module')}"
                else:
                    raw = f"script:{m.group('script')}"
                fail_open = any(marker in statement for marker in _FAIL_OPEN_MARKERS)
                key = (skill_md, raw)
                # A target already seen without a fail-open marker anywhere
                # stays strict even if a later duplicate happens to be
                # fail-open-marked (or vice versa) -- strict wins so a real
                # unconditional use is never silently downgraded.
                found[key] = found.get(key, True) and fail_open
    return [Invocation(skill_md=k[0], raw=k[1], fail_open=v) for k, v in sorted(found.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]))]


ALL_INVOCATIONS = _extract_invocations()
STRICT_INVOCATIONS = [i for i in ALL_INVOCATIONS if not i.fail_open]
FAIL_OPEN_INVOCATIONS = [i for i in ALL_INVOCATIONS if i.fail_open]


def _run(inv: Invocation) -> subprocess.CompletedProcess:
    return subprocess.run(
        inv.command(),
        cwd=str(REPO_ROOT),
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
        "skills/*/SKILL.md -- the extraction regex may have broken"
    )
    labels = {i.raw for i in ALL_INVOCATIONS}
    assert "module:jswarm.installer.jsetup" in labels, (
        "the scanner did not find /jSetup's own command "
        "(jswarm.installer.jsetup) -- it must, since that command is the "
        "one this whole test exists to catch"
    )


@pytest.mark.parametrize("inv", STRICT_INVOCATIONS, ids=lambda i: i.label)
def test_entry_point_is_not_dead_on_arrival(inv: Invocation):
    result = _run(inv)
    signature = _dead_on_arrival(result)
    assert signature is None, (
        f"{inv.label} could not be reached at all (matched {signature!r}); "
        f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n\n"
        "It is fine for a command to fail because it needs a real project "
        "or a tracker (a non-zero exit with the module's own error/usage "
        "text) -- it is not fine for the module or script itself to be "
        "unreachable. If this command is genuinely optional (an "
        "enterprise-only script), mark it fail-open in the SKILL.md with a "
        "trailing `|| true` the way the other optional commands are."
    )


@pytest.mark.parametrize("inv", FAIL_OPEN_INVOCATIONS, ids=lambda i: i.label)
def test_fail_open_entry_point_still_runs_without_hanging_or_crashing_the_test(inv: Invocation):
    # Documented-optional commands (`|| true` / `|| echo` in the SKILL.md
    # itself, e.g. the enterprise-only ColGREP reaper and test-catalog
    # scripts) are exercised -- a hang or a crash of the *test harness*
    # itself would still be worth knowing about -- but never asserted on:
    # any exit code, dead-on-arrival or not, is accepted for these.
    _run(inv)
