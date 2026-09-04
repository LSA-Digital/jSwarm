"""`verify` must check that what the lock file records as installed is
actually present on disk -- not just that the top-level `skills` directory
exists and *something* is in it.

Before this fix, `verify` only checked `host.skills_dir().is_dir()`: true
as long as any one skill survived, regardless of which ones the lock file's
own `steps_completed` (`skills`, and once ColGREP is enabled,
`skills_colgrep`) actually promised were deployed. That is precisely how a
from-scratch clean-Mac run got `verify: install complete, all artefacts
present.` immediately after `install --with-colgrep` had silently failed to
deploy `colgrep-search` and `code-overview` (see
`tests/test_install_colgrep_resume.py` for the installer-side bug that
produced that state) -- `verify` never looked for those two skills by name,
so their absence was invisible to it.

This module never needs a real `claude` CLI: `verify` itself makes no
subprocess calls, and a stub `claude` (exit 0 for `mcp add`) is enough to
get `install --with-colgrep` to a "complete" lock state so its ColGREP-gated
skills are the ones the missing-artifact check exercises.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:" + str(REPO_ROOT / ".venv" / "bin")


def _write_stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(args: list[str], *, home: Path, path: str) -> subprocess.CompletedProcess[str]:
    env = {"HOME": str(home), "JSWARM_HOME": str(REPO_ROOT), "PATH": path}
    return subprocess.run(["bash", "install.sh", *args], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120)


def test_verify_fails_when_a_recorded_skill_is_missing_from_disk(tmp_path):
    home = tmp_path / "home"
    r = _run(["install"], home=home, path=BASE_PATH)
    assert r.returncode == 0, r.stdout + r.stderr

    ok = _run(["verify"], home=home, path=BASE_PATH)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "all artefacts present" in ok.stdout

    # Simulate exactly the drift this check exists for: the lock file still
    # says "skills" completed, but a skill it deployed is gone from disk
    # (deleted by hand, a failed partial copy, anything) -- not something
    # verify's old "is the skills directory a directory" check could ever
    # notice.
    victim = home / ".claude" / "skills" / "jGo"
    assert victim.is_dir(), "test setup: expected jGo to have been deployed by the plain install"
    import shutil as _shutil

    _shutil.rmtree(victim)

    broken = _run(["verify"], home=home, path=BASE_PATH)
    assert broken.returncode == 1, broken.stdout + broken.stderr
    out = broken.stdout + broken.stderr
    assert "all artefacts present" not in out
    assert "MISSING" in out
    assert "jGo" in out, f"verify did not name the missing skill:\n{out}"
    assert "some deployed artefacts are missing" in out


def test_verify_fails_when_a_colgrep_gated_skill_is_missing_from_disk(tmp_path):
    """The exact shape of the reported defect: `skills_colgrep` recorded as
    a completed step, but `colgrep-search`/`code-overview` absent from disk.
    """
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    _write_stub(bindir / "colgrep", "exit 0")
    _write_stub(bindir / "claude", "exit 0")  # stands in for a successful `mcp add`
    path = f"{bindir}:{BASE_PATH}"

    r = _run(["install", "--with-colgrep"], home=home, path=path)
    assert r.returncode == 0, r.stdout + r.stderr

    skills = home / ".claude" / "skills"
    assert (skills / "colgrep-search" / "SKILL.md").is_file()
    assert (skills / "code-overview" / "SKILL.md").is_file()

    ok = _run(["verify"], home=home, path=path)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # Delete only code-overview -- colgrep-search must still be reported
    # present, proving this is a per-skill check, not an all-or-nothing one.
    import shutil as _shutil

    _shutil.rmtree(skills / "code-overview")

    broken = _run(["verify"], home=home, path=path)
    assert broken.returncode == 1, broken.stdout + broken.stderr
    out = broken.stdout + broken.stderr
    assert "all artefacts present" not in out
    assert "code-overview" in out, f"verify did not name the missing ColGREP-gated skill:\n{out}"


def test_verify_passes_when_skills_only_completed_no_colgrep_and_nothing_missing(tmp_path):
    # A plain (no --with-colgrep) install must not have verify go looking
    # for colgrep-search/code-overview at all -- they were never supposed to
    # be deployed, so their absence is correct, not a defect.
    home = tmp_path / "home"
    r = _run(["install"], home=home, path=BASE_PATH)
    assert r.returncode == 0, r.stdout + r.stderr

    ok = _run(["verify"], home=home, path=BASE_PATH)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    out = ok.stdout + ok.stderr
    assert "MISSING" not in out
    assert "colgrep-search" not in out
    assert "code-overview" not in out
