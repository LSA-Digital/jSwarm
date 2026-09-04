"""The `feat/<ID>` branch: who creates it, and end to end proof it works.

`/jClose`, `/jMerge`, and `/jUAT` all *resolve* against `feat/<id>` (current
branch, falling back to the newest `.jswarm/work/*/state.json`), but before
this fix nothing in the shipped skills ever *created* it -- a previous agent
deliberately left the decision open rather than hardcode a branching policy
blind (see clean-mac-walk.md finding 4).

Decision made here: `/jGo`'s session companion (skills/jGo/session.md, step
3, "**Branch:**") creates `feat/<ID>` exactly when the current branch is
still the repository's own default branch (`main`/`master`) and `feat/<ID>`
does not already exist yet. Any other current branch means the user
deliberately checked it out for this work, so `/jGo` leaves it alone --
satisfying "must work when the user is already on a branch of their own"
and "must not move a user off a branch they deliberately checked out."

This test drives that rule for real against scratch git repositories (not a
reimplementation the doc could drift away from unnoticed -- the branch-create
command itself is extracted from session.md's own bash fence, and a
doc-content check pins the prose rule's key phrases), then proves the branch
it creates is exactly what `/jClose` and `/jMerge`'s own documented
resolution (current branch against `feat/<id>`) finds.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_MD = REPO_ROOT / "skills" / "jGo" / "session.md"
JCLOSE_SKILL = REPO_ROOT / "skills" / "jClose" / "SKILL.md"
JMERGE_SKILL = REPO_ROOT / "skills" / "jMerge" / "SKILL.md"

_BASH_FENCE_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)


def _branch_create_command() -> str:
    """The literal branch-creation command, extracted from session.md's own
    "**Branch:**" step rather than re-typed here.
    """
    text = SESSION_MD.read_text(encoding="utf-8")
    fences = _BASH_FENCE_RE.findall(text)
    assert len(fences) == 1, "expected exactly one bash fence in session.md (the branch-create command)"
    return fences[0].strip()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _current_branch(repo: Path) -> str:
    return _git(repo, "branch", "--show-current").stdout.strip()


def _scratch_repo(tmp_path: Path, default_branch: str = "main") -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", default_branch, str(repo)], check=True)
    _git(repo, "config", "user.email", "test@localhost")
    _git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("hello\n")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _maybe_create_branch(repo: Path, work_item_id: str, default_branch: str) -> None:
    """Exercise the documented decision rule for real: default branch and no
    existing `feat/<ID>` -> create and switch; anything else -> leave the
    current branch alone. The actual `git checkout -b` line comes straight
    out of session.md; only the surrounding "when" condition -- prose in the
    doc, not a fenced command -- is reproduced here, in the same words the
    doc uses.
    """
    current = _current_branch(repo)
    feat_branch = f"feat/{work_item_id}"
    if current == feat_branch:
        return
    branch_exists = (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{feat_branch}"]
        ).returncode
        == 0
    )
    if current == default_branch and not branch_exists:
        command = _branch_create_command().replace("<ID>", work_item_id)
        result = subprocess.run(["bash", "-c", command], cwd=str(repo), capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    # else: some branch the user deliberately checked out -- untouched.


def test_session_md_states_the_default_branch_only_rule():
    normalized = " ".join(SESSION_MD.read_text(encoding="utf-8").split())
    assert "the repository's own default branch" in normalized
    assert "Never switch a user off a branch they are already on." in normalized
    assert 'git checkout -b "feat/<ID>"' in normalized


def test_default_branch_gets_the_feature_branch_created(tmp_path):
    repo = _scratch_repo(tmp_path)
    assert _current_branch(repo) == "main"

    _maybe_create_branch(repo, "add-multiply", default_branch="main")

    assert _current_branch(repo) == "feat/add-multiply"


def test_already_on_the_feature_branch_is_left_alone(tmp_path):
    repo = _scratch_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feat/add-multiply")

    _maybe_create_branch(repo, "add-multiply", default_branch="main")

    assert _current_branch(repo) == "feat/add-multiply"


def test_a_branch_the_user_deliberately_checked_out_is_never_switched_away_from(tmp_path):
    repo = _scratch_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "my-own-experiment")

    _maybe_create_branch(repo, "add-multiply", default_branch="main")

    # Still on the user's own branch; feat/add-multiply was never created.
    assert _current_branch(repo) == "my-own-experiment"
    branch_exists = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", "refs/heads/feat/add-multiply"]
    ).returncode
    assert branch_exists != 0


def test_default_branch_works_with_master_too(tmp_path):
    repo = _scratch_repo(tmp_path, default_branch="master")
    _maybe_create_branch(repo, "add-multiply", default_branch="master")
    assert _current_branch(repo) == "feat/add-multiply"


def test_created_branch_is_what_jclose_and_jmerge_resolve_against(tmp_path):
    """End to end: the branch /jGo creates is exactly the branch /jClose and
    /jMerge's own documented resolution (current branch against
    `feat/<id>`) finds -- proving the branch decision actually closes the
    gap, not just that a branch gets created.
    """
    for skill in (JCLOSE_SKILL, JMERGE_SKILL):
        text = skill.read_text(encoding="utf-8")
        assert "feat/<id>" in text or "feat/<ID>" in text, (
            f"{skill}: no longer resolves against feat/<id>; the branch "
            "decision this test proves may have gone out of sync with it"
        )

    repo = _scratch_repo(tmp_path)
    _maybe_create_branch(repo, "add-multiply", default_branch="main")

    current = _current_branch(repo)
    # This is the literal resolution rule /jClose and /jMerge document:
    # "the current branch name against feat/<id>".
    m = re.fullmatch(r"feat/(.+)", current)
    assert m is not None, f"current branch {current!r} does not resolve against feat/<id>"
    assert m.group(1) == "add-multiply"
