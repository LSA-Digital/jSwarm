"""`install.sh adopt`'s `.gitignore` handling.

Before this fix, `adopt` wrote `.jswarm/` and `.claude/` into a project and
added no `.gitignore` entries at all (clean-mac-walk.md finding 6): the
first thing a user saw afterward was untracked noise, `.jswarm/`,
`.claude/`, and `__pycache__/` all unignored, and a `git add -A` would have
swept the whole jswarm work-state tree into the project's history alongside
genuine caches and installer backups.

Decision, recorded in `jswarm/installer/adopt.py` (`_GITIGNORE_LINES`):
`.jswarm/backups/` (adopt's own pre-write safety copies) and
`.jswarm/.adopted` (a machine-local "this checkout ran adopt" marker) are
ignored, along with `__pycache__/` and `*.pyc`. Everything else under
`.jswarm/` -- `plans/`, `work/` (state.json, retro.md, close.json,
uat-round/*), `config.yaml` -- and all of `.claude/` stay trackable: they are
the product's own record of a work item's lifecycle and portable project
configuration, exactly what a team wants in history and code review.

`adopt` follows the same rule it already uses for `CLAUDE.md` and
`.claude/settings.json`: never clobber, merge into an existing file, and
back it up first.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from jswarm.installer.adopt import adopt, merge_gitignore, _gitignore_block

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_install_sh(*args: str, home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "JSWARM_HOME": str(REPO_ROOT)}
    return subprocess.run(
        ["bash", "install.sh", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, env=env
    )


def _scratch_repo(tmp_path: Path, name: str = "proj") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_adopt_writes_a_gitignore_when_none_existed(tmp_path):
    repo = _scratch_repo(tmp_path)
    home = tmp_path / "home"

    adopt(repo, home=home)

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert ".jswarm/backups/" in gitignore
    assert ".jswarm/.adopted" in gitignore
    assert "__pycache__/" in gitignore
    assert "*.pyc" in gitignore


def test_adopt_never_ignores_the_meaningful_project_state(tmp_path):
    """plans/, work/, config.yaml, and .claude/ are the product's own
    lifecycle record and portable config -- adopt must never tell git to
    ignore them.
    """
    repo = _scratch_repo(tmp_path)
    home = tmp_path / "home"

    adopt(repo, home=home)

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    for meaningful in (".jswarm/plans", ".jswarm/work", ".jswarm/config.yaml", ".claude"):
        assert meaningful not in gitignore, f"{meaningful} must stay tracked, not ignored"


def test_adopt_preserves_an_existing_gitignore_content_intact(tmp_path):
    """The exact requirement from the brief: an existing .gitignore survives
    with its content intact, merged (not clobbered), and backed up first --
    the same rule adopt already applies to CLAUDE.md and settings.json.
    """
    repo = _scratch_repo(tmp_path)
    existing = "node_modules/\ndist/\n*.log\n"
    (repo / ".gitignore").write_text(existing)
    home = tmp_path / "home"

    adopt(repo, home=home)

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gitignore
    assert "dist/" in gitignore
    assert "*.log" in gitignore
    assert ".jswarm/backups/" in gitignore
    # Backed up before the write, same as CLAUDE.md and settings.json.
    backups_dir = repo / ".jswarm" / "backups"
    assert backups_dir.is_dir()
    sessions = list(backups_dir.iterdir())
    assert sessions, "adopt must back up an existing .gitignore before writing it"
    backed_up_gitignore = sessions[0] / ".gitignore"
    assert backed_up_gitignore.exists()
    assert backed_up_gitignore.read_text(encoding="utf-8") == existing


def test_readopt_replaces_the_managed_gitignore_block_not_appends(tmp_path):
    repo = _scratch_repo(tmp_path)
    (repo / ".gitignore").write_text("keep-me/\n")
    home = tmp_path / "home"

    adopt(repo, home=home)
    adopt(repo, home=home)  # re-run, e.g. to pick up a newer jSwarm checkout

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count("# jswarm:begin") == 1
    assert gitignore.count("# jswarm:end") == 1
    assert "keep-me/" in gitignore


def test_merge_gitignore_appends_after_user_content_when_no_existing_file():
    block = _gitignore_block()
    merged = merge_gitignore("", block)
    assert merged == block


def test_merge_gitignore_replaces_in_place_when_a_managed_block_already_exists():
    block = _gitignore_block()
    first = merge_gitignore("mine/\n", block)
    second = merge_gitignore(first, block)
    assert second.count("# jswarm:begin") == 1
    assert "mine/" in second


def test_install_sh_adopt_writes_the_gitignore_through_the_real_cli(tmp_path):
    """Same behavior, driven through the actual `install.sh adopt` entry
    point a user runs, matching how the rest of this repo's adopt tests
    (test_adopt_modes.py) exercise it -- not just the Python function.
    """
    repo = _scratch_repo(tmp_path)
    (repo / ".gitignore").write_text("keep-me/\n")

    result = _run_install_sh("adopt", str(repo), home=tmp_path)
    assert result.returncode == 0, result.stderr

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "keep-me/" in gitignore
    assert ".jswarm/backups/" in gitignore
    assert "__pycache__/" in gitignore
