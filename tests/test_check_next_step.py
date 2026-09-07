"""`check`'s closing "Next:" line must match what actually happened.

`check` exits non-zero when a *required* prerequisite is missing, but until
now its final line still read `Next: ./install.sh install --dry-run` --
the happy-path promise, printed regardless of whether install could
possibly succeed. A new user with no Homebrew, no Python 3.12, no `gh`,
and no `claude` on PATH (a genuinely clean machine -- see the module docs
in `jswarm/host/claude_code.py`) would be told, in the very next line, to
go run an install that is guaranteed to fail.

Both scenarios here drive the real `./install.sh check` subprocess (the
same entry point a user runs), with `HOME` and `PATH` fully controlled, so
the missing/present prerequisite state is genuine rather than simulated or
mocked. `HOME` is always a pytest `tmp_path`, never the real machine home.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# A minimal, real system PATH with none of Homebrew, python3.12, gh, cargo,
# or claude on it -- and no .venv/bin either, so nothing this repo installed
# leaks in and quietly satisfies a check it shouldn't. `install.sh check`
# does not need PATH to contain python: it resolves its own venv by an
# absolute path (`$JSWARM_HOME/.venv/bin/python`, see `install.sh`'s
# `resolve_py_readonly`), so this PATH only controls what the *prerequisite
# checks themselves* (`jswarm.platform.macos.MacOSPlatform`) can find.
BARE_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _write_stub(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_check(*, home: Path, path: str):
    env = {"HOME": str(home), "JSWARM_HOME": str(REPO_ROOT), "PATH": path}
    return subprocess.run(
        ["bash", "install.sh", "check"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=60,
    )


def _next_lines(result) -> list[str]:
    out = result.stdout + result.stderr
    return [ln for ln in out.splitlines() if ln.startswith("Next:")]


def test_check_with_a_required_prerequisite_missing_exits_nonzero_and_points_at_fixing_it(tmp_path):
    # Reproduces the clean-machine repro: no Homebrew, no Python 3.12, no
    # gh, no claude anywhere on PATH, and no leftover ~/.claude either, so
    # the agent-host check is genuinely absent too (see
    # test_host_boundary.py for is_present() itself). git and the Xcode
    # command line tools come from the real test machine, exactly as they
    # would on the clean MacBook this was found on.
    home = tmp_path / "home"
    home.mkdir()
    result = _run_check(home=home, path=BARE_SYSTEM_PATH)

    assert result.returncode == 1, (result.stdout, result.stderr)
    out = result.stdout + result.stderr
    assert "[miss]" in out, out

    next_lines = _next_lines(result)
    assert next_lines, f"no 'Next:' line at all:\n{out}"
    final = next_lines[-1]
    # The wrong, old behavior: telling the user to proceed to an install
    # that cannot succeed.
    assert "install --dry-run" not in final, f"still points at the happy path: {final!r}"
    # The right behavior: resolve the missing prerequisites (the fix
    # commands already printed above) and re-check.
    assert "fix" in final.lower() and "check" in final.lower(), f"doesn't direct to fixing prerequisites: {final!r}"
    assert "jswarm clone" in final.lower(), f"missing operating context: {final!r}"


def test_check_with_only_an_optional_item_missing_exits_zero_and_points_at_install(tmp_path):
    # Stub every *required* prerequisite (Homebrew, Python 3.12, gh, the
    # claude-code agent host) so only the optional Rust toolchain
    # (--with-colgrep) is missing. This is the one case where proceeding to
    # `install --dry-run` really is correct, and it must stay that way.
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("git", "gh", "claude"):
        _write_stub(bindir / name)
    path = f"{bindir}:{BARE_SYSTEM_PATH}"

    result = _run_check(home=home, path=path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    out = result.stdout + result.stderr
    assert "[miss]" not in out, out
    assert "[opt " in out, out  # the Rust toolchain, still reported as missing-but-optional

    next_lines = _next_lines(result)
    assert next_lines, f"no 'Next:' line at all:\n{out}"
    final = next_lines[-1]
    assert "install --dry-run" in final, f"optional-only miss should still point at install: {final!r}"
