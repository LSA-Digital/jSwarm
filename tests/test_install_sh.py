import os, subprocess
from pathlib import Path

def run(*args, home, env=None):
    full_env = {**(env or os.environ), "HOME": str(home), "JSWARM_HOME": str(Path.cwd())}
    return subprocess.run(["bash", "install.sh", *args], capture_output=True, text=True, env=full_env)

def test_no_provider_flag(tmp_path):
    r = run("install", "--provider", "yaml", "--dry-run", home=tmp_path)
    assert r.returncode != 0 and "unknown option" in (r.stdout + r.stderr).lower()

def test_dry_run_install_writes_nothing_and_names_the_lock(tmp_path):
    r = run("install", "--dry-run", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "install.lock.yaml" in r.stdout
    assert not (tmp_path / ".jswarm").exists()

def test_with_colgrep_is_accepted(tmp_path):
    # --with-colgrep is real now. A stub `colgrep` binary is put on PATH so the
    # outcome does not depend on whether this machine happens to have Rust/colgrep
    # installed (see tests/test_install_colgrep.py for PATH-controlled coverage of
    # the missing-toolchain path, and of a real, non-dry-run install). This dry run
    # must describe a real plan -- never the old "not implemented" stub text -- and
    # must never claim the two ColGREP-dependent skills are skipped when the flag is
    # given and the toolchain is actually present.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "colgrep"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '')}"}
    r = run("install", "--with-colgrep", "--dry-run", home=tmp_path / "home", env=env)
    out = r.stdout.lower()
    assert r.returncode == 0
    assert "not implemented" not in out
    assert "code-overview skipped" not in out
    assert "colgrep-search skipped" not in out
    assert "found existing binary" in out

def test_every_mutating_subcommand_has_a_dry_run(tmp_path):
    for args in (["install"], ["adopt", str(tmp_path)], ["unadopt", str(tmp_path)], ["upgrade"], ["uninstall"]):
        r = run(*args, "--dry-run", home=tmp_path)
        assert "--dry-run" not in (r.stdout + r.stderr).lower() or r.returncode == 0, args
        assert not (tmp_path / ".jswarm" / "install.lock.yaml").exists(), args

def test_every_command_names_the_next_step_and_where_to_run_it(tmp_path):
    r = run("check", home=tmp_path)
    out = r.stdout + r.stderr
    assert "Next:" in out


# The install.sh docstring names three operating contexts an agent/user can be confused
# between: the jSwarm clone's own shell, the target project's agent session, and a plain
# terminal. Confusing them is the most common way a first install fails, so every
# subcommand a user runs in sequence (not just `check`) must close by naming the next
# command AND which of those contexts it runs in -- not just "Next:" in isolation.
_CONTEXT_MARKERS = ("jswarm clone", "agent session", "this terminal")


def _assert_names_next_step_and_context(result, label):
    out = result.stdout + result.stderr
    assert "Next:" in out, f"{label}: no 'Next:' line in output:\n{out}"
    next_lines = [ln for ln in out.splitlines() if "Next:" in ln]
    assert any(any(marker in ln.lower() for marker in _CONTEXT_MARKERS) for ln in next_lines), (
        f"{label}: 'Next:' line(s) don't name an operating context "
        f"(one of {_CONTEXT_MARKERS!r}):\n" + "\n".join(next_lines)
    )


def test_every_installer_subcommand_names_next_step_and_context(tmp_path):
    # A fresh, never-installed HOME: every one of these subcommands, run in sequence the
    # way a first-time user would try them, must still close with an explicit "Next:"
    # line naming both the next command and the context to run it in -- whether it
    # succeeds, refuses because nothing is installed yet, or (uninstall) has nothing to
    # do. `--dry-run` throughout so the sequence has no side effects and needs no real
    # venv/npm build.
    repo = str(tmp_path / "some-project")
    Path(repo).mkdir()
    subprocess.run(["git", "init", "-q", repo], check=True)
    cases = [
        (["check"], "check"),
        (["install", "--dry-run"], "install --dry-run"),
        (["install", "--with-colgrep", "--dry-run"], "install --with-colgrep --dry-run"),
        (["verify"], "verify (nothing installed)"),
        (["adopt", repo, "--dry-run"], "adopt --dry-run (nothing installed)"),
        (["unadopt", repo, "--dry-run"], "unadopt --dry-run (nothing installed)"),
        (["upgrade", "--dry-run"], "upgrade --dry-run (nothing installed)"),
        (["uninstall", "--dry-run"], "uninstall --dry-run (nothing installed)"),
        (["portal", "--dry-run"], "portal --dry-run (nothing installed)"),
    ]
    for args, label in cases:
        r = run(*args, home=tmp_path)
        _assert_names_next_step_and_context(r, label)
