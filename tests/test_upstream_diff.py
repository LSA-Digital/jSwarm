import os
import subprocess
import sys
import yaml
from pathlib import Path


def _install_script(repo: Path) -> None:
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    script = repo / "scripts/upstream-diff.sh"
    script.write_text(Path("scripts/upstream-diff.sh").read_text())
    script.chmod(0o755)


def _run_script(*args, cwd):
    # The scratch repos these tests build have no .venv of their own, so the
    # script's normal search (its own venv, then a bare python3) has nothing
    # reliable to find -- a bare python3 on the runner may well lack PyYAML.
    # Point it at the interpreter running this test suite, which is
    # guaranteed to have PyYAML (it's in requirements.txt), via the same
    # override a real user would use for the same reason.
    env = {**os.environ, "JSWARM_UPSTREAM_DIFF_PYTHON": sys.executable}
    return subprocess.run(
        ["bash", "scripts/upstream-diff.sh", *args], cwd=cwd, capture_output=True, text=True, env=env
    )


def _make_upstream(tmp_path: Path) -> tuple[Path, str]:
    up = tmp_path / "common"
    up.mkdir()
    subprocess.run(["git", "init", "-q", str(up)], check=True)
    (up / "a.py").write_text("v1\n")
    subprocess.run(["git", "-C", str(up), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(up), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one"],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(up), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (up / "a.py").write_text("v2\n")
    subprocess.run(
        ["git", "-C", str(up), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "two"],
        check=True,
    )
    return up, sha


def test_reports_changed_upstream_file(tmp_path):
    up, sha = _make_upstream(tmp_path)
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "PROVENANCE.yaml").write_text(
        yaml.safe_dump({"source_commit": sha, "files": [{"source": "a.py", "target": "jswarm/a.py", "sha256": "x"}]})
    )
    (repo / "scripts/upstream-diff.sh").write_text(Path("scripts/upstream-diff.sh").read_text())
    (repo / "scripts/upstream-diff.sh").chmod(0o755)
    out = _run_script(str(up), cwd=repo).stdout
    assert "jswarm/a.py" in out and "a.py |" in out


def test_groups_output_by_target_directory(tmp_path):
    up = tmp_path / "common"
    up.mkdir()
    subprocess.run(["git", "init", "-q", str(up)], check=True)
    (up / "a.py").write_text("v1\n")
    (up / "b.py").write_text("v1\n")
    subprocess.run(["git", "-C", str(up), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(up), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one"],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(up), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (up / "a.py").write_text("v2\n")
    (up / "b.py").write_text("v2\n")
    subprocess.run(
        ["git", "-C", str(up), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "two"],
        check=True,
    )

    repo = tmp_path / "repo"
    _install_script(repo)
    (repo / "PROVENANCE.yaml").write_text(
        yaml.safe_dump(
            {
                "source_commit": sha,
                "files": [
                    {"source": "a.py", "target": "jswarm/group_one/a.py", "sha256": "x"},
                    {"source": "b.py", "target": "jswarm/group_two/b.py", "sha256": "x"},
                ],
            }
        )
    )
    out = _run_script(str(up), cwd=repo).stdout
    assert "# jswarm/group_one" in out
    assert "# jswarm/group_two" in out
    assert out.index("# jswarm/group_one") < out.index("jswarm/group_one/a.py")
    assert out.index("# jswarm/group_two") < out.index("jswarm/group_two/b.py")


def test_missing_provenance_gives_clear_message(tmp_path):
    up, _sha = _make_upstream(tmp_path)
    repo = tmp_path / "repo"
    _install_script(repo)
    # No PROVENANCE.yaml written.
    result = _run_script(str(up), cwd=repo)
    assert result.returncode != 0
    assert "PROVENANCE.yaml" in result.stderr
    assert "Traceback" not in result.stderr


def test_unknown_source_commit_gives_clear_message(tmp_path):
    up, _sha = _make_upstream(tmp_path)
    repo = tmp_path / "repo"
    _install_script(repo)
    bogus_sha = "0" * 40
    (repo / "PROVENANCE.yaml").write_text(
        yaml.safe_dump({"source_commit": bogus_sha, "files": [{"source": "a.py", "target": "jswarm/a.py", "sha256": "x"}]})
    )
    result = _run_script(str(up), cwd=repo)
    assert result.returncode != 0
    assert bogus_sha in result.stderr
    assert "Traceback" not in result.stderr


def test_upstream_path_not_a_git_repo_gives_clear_message(tmp_path):
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    repo = tmp_path / "repo"
    _install_script(repo)
    (repo / "PROVENANCE.yaml").write_text(
        yaml.safe_dump({"source_commit": "f" * 40, "files": [{"source": "a.py", "target": "jswarm/a.py", "sha256": "x"}]})
    )
    result = _run_script(str(not_a_repo), cwd=repo)
    assert result.returncode != 0
    assert "not a git repository" in result.stderr
    assert "Traceback" not in result.stderr


def test_hand_authored_octal_source_commit_gives_clear_message(tmp_path):
    # PyYAML's default resolver parses an unquoted string of only octal digits
    # (e.g. 40 zeros) as an int, not a str. A hand-authored PROVENANCE.yaml
    # (as opposed to one written with yaml.safe_dump, which always quotes)
    # can trigger this. The script must catch it, not crash.
    up, _sha = _make_upstream(tmp_path)
    repo = tmp_path / "repo"
    _install_script(repo)
    (repo / "PROVENANCE.yaml").write_text(
        "source_commit: 0000000000000000000000000000000000000000\n"
        "files:\n"
        "  - source: a.py\n"
        "    target: jswarm/a.py\n"
        "    sha256: x\n"
    )
    result = _run_script(str(up), cwd=repo)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "source_commit" in result.stderr
    assert "invalid" in result.stderr.lower()


def test_hand_authored_non_sha_source_commit_gives_clear_message(tmp_path):
    up, _sha = _make_upstream(tmp_path)
    repo = tmp_path / "repo"
    _install_script(repo)
    (repo / "PROVENANCE.yaml").write_text(
        "source_commit: not-a-real-sha\n"
        "files:\n"
        "  - source: a.py\n"
        "    target: jswarm/a.py\n"
        "    sha256: x\n"
    )
    result = _run_script(str(up), cwd=repo)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "source_commit" in result.stderr
    assert "invalid" in result.stderr.lower()


def test_bare_target_groups_under_repo_root_label(tmp_path):
    up, sha = _make_upstream(tmp_path)
    repo = tmp_path / "repo"
    _install_script(repo)
    (repo / "PROVENANCE.yaml").write_text(
        yaml.safe_dump({"source_commit": sha, "files": [{"source": "a.py", "target": "a.py", "sha256": "x"}]})
    )
    out = _run_script(str(up), cwd=repo).stdout
    assert "# (repo root)" in out
    assert "# .\n" not in out


def test_deleted_upstream_file_reported_separately(tmp_path):
    up = tmp_path / "common"
    up.mkdir()
    subprocess.run(["git", "init", "-q", str(up)], check=True)
    (up / "a.py").write_text("v1\n")
    (up / "gone.py").write_text("v1\n")
    subprocess.run(["git", "-C", str(up), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(up), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one"],
        check=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(up), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (up / "a.py").write_text("v2\n")
    subprocess.run(["git", "-C", str(up), "rm", "-q", "gone.py"], check=True)
    subprocess.run(
        ["git", "-C", str(up), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "two"],
        check=True,
    )

    repo = tmp_path / "repo"
    _install_script(repo)
    (repo / "PROVENANCE.yaml").write_text(
        yaml.safe_dump(
            {
                "source_commit": sha,
                "files": [
                    {"source": "a.py", "target": "jswarm/a.py", "sha256": "x"},
                    {"source": "gone.py", "target": "jswarm/gone.py", "sha256": "x"},
                ],
            }
        )
    )
    result = _run_script(str(up), cwd=repo)
    assert result.returncode == 0
    assert "Deleted or renamed upstream" in result.stdout
    assert "jswarm/gone.py" in result.stdout
    # The deleted-file section is reported after (separately from) the changed-file groups.
    assert result.stdout.index("jswarm/a.py") < result.stdout.index("Deleted or renamed upstream")
