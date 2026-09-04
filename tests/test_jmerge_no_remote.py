"""`/jMerge` on a repository with no `origin` remote.

`install.sh adopt` never adds a remote (see `test_adopt_gitignore.py` and
`jswarm/installer/adopt.py`'s own docstring) -- a purely local project, being
worked on by a stranger trying the product for the first time on a scratch
repository, is the ordinary case. Before this fix, `/jMerge` Step 1's first
command was an unconditional `git fetch origin`, which fails hard on such a
repository:

    $ git fetch origin
    fatal: 'origin' does not appear to be a git repository
    fatal: Could not read from remote repository.

Reproduced for real against a scratch repo while writing this fix (see
`test_reproduce_the_original_failure_for_real` below, which pins that exact
failure so it can never silently stop reproducing); the product's own adopt
path produced a project its own merge command could not handle.

The fix (skills/jMerge/SKILL.md) detects the missing remote up front and
does the sensible local thing instead: skip the fetch, integrate against the
local target branch, land the source branch onto it with a fast-forward, and
skip only the steps that genuinely need a remote (push, PR, remote branch
delete) -- each with a clear one-line reason instead of an error.

This test drives the actual bash `/jMerge` documents, extracted from the
skill file itself (not re-typed here) so a future edit to the doc that
reintroduces an unconditional `git fetch origin` fails this test rather than
silently drifting out of sync with it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JMERGE_SKILL = REPO_ROOT / "skills" / "jMerge" / "SKILL.md"

_STEP_BLOCK_RE = re.compile(r"^## (Step \d[^\n]*)\n(.*?)(?=^## |\Z)", re.DOTALL | re.MULTILINE)
_BASH_FENCE_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)


def _step_bash_blocks() -> dict[str, str]:
    """Map "Step N: ..." heading text -> the concatenated bash fences under it."""
    text = JMERGE_SKILL.read_text(encoding="utf-8")
    blocks = {}
    for heading, body in _STEP_BLOCK_RE.findall(text):
        fences = _BASH_FENCE_RE.findall(body)
        if fences:
            blocks[heading.strip()] = "\n".join(fences)
    return blocks


def _run_repo(repo: Path, script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )


def _scratch_repo(tmp_path: Path, name: str = "proj") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@localhost"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "file.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feat/add-multiply"], check=True)
    (repo / "file.txt").write_text("hello\nworld\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-am", "feat work"], check=True)
    return repo


def test_reproduce_the_original_failure_for_real(tmp_path):
    """Pin the exact failure this fix addresses, so this test would fail
    (telling us our fix or our understanding of the bug drifted) if
    `git fetch origin` ever stopped failing this way on a remote-less repo.
    """
    repo = _scratch_repo(tmp_path)
    result = subprocess.run(
        ["git", "fetch", "origin"], cwd=str(repo), capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "does not appear to be a git repository" in result.stderr


def test_skill_doc_step_1_no_longer_unconditionally_fetches_origin():
    blocks = _step_bash_blocks()
    step1 = next(v for k, v in blocks.items() if k.startswith("Step 1"))
    assert "HAS_REMOTE" in step1, (
        "Step 1 must detect whether an origin remote exists before deciding "
        "whether to fetch"
    )
    # The fetch itself must be conditional, not the bare first line it used to be.
    assert re.search(r'HAS_REMOTE.*&&\s*git fetch origin', step1) or (
        "git fetch origin" in step1 and "if" in step1
    )


def test_documented_jmerge_flow_completes_on_a_repo_with_no_remote(tmp_path):
    """Drive Step 1, Step 2, Step 3, and Step 6's actual documented bash,
    exactly as extracted from skills/jMerge/SKILL.md, against a real scratch
    git repository with no `origin` remote at all. It must complete (exit 0)
    instead of erroring out of the fetch, and land the feature branch's
    commit onto the target branch locally.
    """
    repo = _scratch_repo(tmp_path)
    blocks = _step_bash_blocks()
    step1 = next(v for k, v in blocks.items() if k.startswith("Step 1"))
    step2 = next(v for k, v in blocks.items() if k.startswith("Step 2"))
    step3 = next(v for k, v in blocks.items() if k.startswith("Step 3"))
    step6 = next(v for k, v in blocks.items() if k.startswith("Step 6"))

    preamble = 'ID="add-multiply"\nTARGET="main"\nMERGE_STRATEGY="rebase"\n'
    # The doc writes `feat/<ID>` as a human/agent-facing placeholder, not a
    # shell variable -- substitute it the way an agent following the doc
    # would, using the same $ID this script just set.
    body = "\n".join([step1, step2, step3, step6]).replace("<ID>", "${ID}")
    script = preamble + body

    result = _run_repo(repo, script)
    assert result.returncode == 0, (
        f"documented /jMerge flow did not complete on a no-remote repo\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "No origin remote" in result.stdout

    # The feature branch's commit really landed on main, locally, with no push.
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--oneline", "main"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "feat work" in log

    # And the feature branch was cleaned up locally (Step 6), same as the
    # remote-configured path, minus the remote-delete this repo has none for.
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch"], capture_output=True, text=True, check=True
    ).stdout
    assert "feat/add-multiply" not in branches


def test_documented_jmerge_flow_still_pushes_when_a_remote_exists(tmp_path):
    """The no-remote branch must not have broken the ordinary, remote-backed
    path: with an origin configured, the same documented steps still fetch,
    integrate against `origin/<target>`, and push.
    """
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)

    repo = _scratch_repo(tmp_path, name="proj-remote")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
    # Seed the bare remote's main from this repo's own initial commit.
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)

    blocks = _step_bash_blocks()
    step1 = next(v for k, v in blocks.items() if k.startswith("Step 1"))
    step2 = next(v for k, v in blocks.items() if k.startswith("Step 2"))
    step3 = next(v for k, v in blocks.items() if k.startswith("Step 3"))
    step6 = next(v for k, v in blocks.items() if k.startswith("Step 6"))

    preamble = 'ID="add-multiply"\nTARGET="main"\nMERGE_STRATEGY="rebase"\n'
    body = "\n".join([step1, step2, step3, step6]).replace("<ID>", "${ID}")
    script = preamble + body

    result = _run_repo(repo, script)
    assert result.returncode == 0, (
        f"documented /jMerge flow did not complete on a remote-backed repo\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "No origin remote" not in result.stdout

    remote_log = subprocess.run(
        ["git", "-C", str(remote), "log", "--oneline", "main"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "feat work" in remote_log
