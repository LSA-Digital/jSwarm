"""Install-and-assert: run the real installer end to end and check what it
actually put on disk.

Every other test in this suite exercises source files and function calls in
isolation. None of them ever ran `install.sh install` and looked at the
result, which is exactly why a real install could (and did) produce a
broken layout -- `skills/_shims/` landing as a single directory named
`_shims` instead of being flattened -- while every unit test stayed green.

This module runs the actual installer subprocess (`./install.sh install`,
the same entry point a new user runs) against a throwaway `HOME`, then
asserts against the real filesystem result: every skill directory present
at its own top-level name, every shim flattened to its own discoverable
top-level name, no literal `_shims` directory anywhere in the destination,
and the lock file / portal config at the paths the installer's own contract
(`jswarm.installer.lockfile`, `jswarm.installer.cli._install_portal_config`)
promises.

Safety: `HOME` is always the pytest `tmp_path` fixture, never the real
machine home. `JSWARM_HOME` is pinned to this checked-out repo so the
installer reuses its already-provisioned `.venv` (skipping the `python3.12
-m venv` / `pip install` step, which nothing here needs to re-verify) and
only ever reads from `skills/`, `templates/`, and `requirements.txt` --
never writes to the repo itself. The one thing installer writes anywhere
outside `$HOME` is nothing: skills, the lock file, and the portal config
all live under `$HOME`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

SHIM_NAMES = {"close-ticket", "fix", "implement", "jsetup", "merge", "new-work", "test", "uat"}

# colgrep-search and code-overview both lean on the colgrep_search /
# colgrep_list_dev_indices MCP tools, which only exist once ColGREP is actually
# installed and its MCP server registered (jswarm.installer.cli._install_colgrep).
# Without --with-colgrep (or if that install fails), _install_skills excludes both
# rather than installing a silently non-functional command; see
# test_neither_colgrep_dependent_skill_is_installed and, below, installed_home_with_colgrep.
COLGREP_DEPENDENT_SKILLS = {"colgrep-search", "code-overview"}


def _real_skill_names() -> set[str]:
    skills_dir = REPO_ROOT / "skills"
    return {
        p.name
        for p in skills_dir.iterdir()
        if p.is_dir() and p.name != "_shims" and p.name not in COLGREP_DEPENDENT_SKILLS
    }


@pytest.fixture(scope="module")
def installed_home(tmp_path_factory):
    """Run `./install.sh install` once against a throwaway HOME, return it."""
    home = tmp_path_factory.mktemp("jswarm-install-home")
    env = {
        "HOME": str(home),
        "JSWARM_HOME": str(REPO_ROOT),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:" + str(REPO_ROOT / ".venv" / "bin"),
    }
    result = subprocess.run(
        ["./install.sh", "install"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"install.sh install failed (exit {result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return home


def test_every_real_skill_lands_at_its_own_top_level_directory(installed_home):
    dest = installed_home / ".claude" / "skills"
    for name in _real_skill_names():
        skill_md = dest / name / "SKILL.md"
        assert skill_md.is_file(), f"skill '{name}' did not land at {skill_md}"


def test_neither_colgrep_dependent_skill_is_installed(installed_home):
    # Leaving these installed but silently non-functional (they call MCP tools that do
    # not exist without ColGREP) is not acceptable, so the installer excludes them
    # entirely rather than installing them with a "requires X" banner.
    dest = installed_home / ".claude" / "skills"
    for name in COLGREP_DEPENDENT_SKILLS:
        assert not (dest / name).exists(), f"'{name}' should not be installed (requires --with-colgrep)"


@pytest.fixture(scope="module")
def installed_home_with_colgrep(tmp_path_factory):
    """Run `./install.sh install --with-colgrep` once against a throwaway HOME.

    A stub `colgrep` binary goes on PATH so this is deterministic regardless of
    whether the machine running the tests happens to have Rust/colgrep installed
    (see tests/test_install_colgrep.py for the missing-toolchain path, and for
    the real MCP-registration assertion against a stub `claude`).
    """
    home = tmp_path_factory.mktemp("jswarm-install-home-colgrep")
    bindir = tmp_path_factory.mktemp("jswarm-install-bin")
    for name in ("colgrep", "claude"):
        stub = bindir / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    env = {
        "HOME": str(home),
        "JSWARM_HOME": str(REPO_ROOT),
        "PATH": f"{bindir}:/usr/bin:/bin:/usr/sbin:/sbin:" + str(REPO_ROOT / ".venv" / "bin"),
    }
    result = subprocess.run(
        ["./install.sh", "install", "--with-colgrep"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"install.sh install --with-colgrep failed (exit {result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return home


def test_both_colgrep_dependent_skills_land_with_the_flag(installed_home_with_colgrep):
    dest = installed_home_with_colgrep / ".claude" / "skills"
    for name in COLGREP_DEPENDENT_SKILLS:
        skill_md = dest / name / "SKILL.md"
        assert skill_md.is_file(), f"'{name}' should be installed with --with-colgrep, missing at {skill_md}"


def test_colgrep_step_is_recorded_with_the_flag(installed_home_with_colgrep):
    lock_path = installed_home_with_colgrep / ".jswarm" / "install.lock.yaml"
    data = yaml.safe_load(lock_path.read_text())
    assert "colgrep" in data["steps_completed"], data


def test_every_shim_is_discoverable_at_its_own_top_level_name(installed_home):
    dest = installed_home / ".claude" / "skills"
    for name in SHIM_NAMES:
        if name == "jsetup":
            # jsetup -> jSetup is a case-only rename: dest/jsetup and
            # dest/jSetup are the same path on the case-insensitive
            # filesystem every supported host uses, so the installer
            # deliberately skips this one shim rather than clobber (or be
            # clobbered by) the real jSetup skill at the identical
            # destination. See test_real_jsetup_skill_survives_the_case_collision.
            continue
        skill_md = dest / name / "SKILL.md"
        assert skill_md.is_file(), (
            f"shim '{name}' did not land at its own discoverable top level "
            f"({skill_md}) -- a real install must flatten skills/_shims/* "
            "into the destination, not nest it under a literal '_shims' dir."
        )
        text = skill_md.read_text()
        assert f"name: {name}" in text, f"{skill_md} does not look like the '{name}' shim"


def test_real_jsetup_skill_survives_the_case_collision(installed_home):
    # jsetup (deprecated shim) and jSetup (real skill) collide at the same
    # destination path on a case-insensitive filesystem. The installer must
    # resolve that in favor of the real skill, never the deprecation stub --
    # a new user whose first command is /jSetup must reach the real skill,
    # not a redirect notice pointing back at itself.
    skill_md = installed_home / ".claude" / "skills" / "jSetup" / "SKILL.md"
    assert skill_md.is_file()
    text = skill_md.read_text()
    assert "name: jSetup" in text
    assert "jswarm.installer.jsetup" in text


def test_no_literal_shims_directory_exists_anywhere_in_the_destination(installed_home):
    dest = installed_home / ".claude" / "skills"
    offenders = [p for p in dest.rglob("_shims") if p.is_dir()]
    assert not offenders, f"a literal '_shims' directory must never appear in an install: {offenders}"


def test_lock_file_is_where_the_contract_says(installed_home):
    lock_path = installed_home / ".jswarm" / "install.lock.yaml"
    assert lock_path.is_file(), f"install lock file missing at {lock_path}"
    data = yaml.safe_load(lock_path.read_text())
    assert data["state"] == "complete", f"install did not complete: {data}"
    for step in ("venv", "skills", "portal_config"):
        assert step in data["steps_completed"], f"'{step}' missing from steps_completed: {data}"


def test_portal_config_is_where_the_contract_says(installed_home):
    config_path = installed_home / ".jswarm" / "decision-review" / "config.json"
    assert config_path.is_file(), f"portal config missing at {config_path}"
