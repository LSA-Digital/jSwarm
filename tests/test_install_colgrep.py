"""`--with-colgrep` end to end: the Rust-toolchain check, installing/verifying
the `colgrep` binary, registering the MCP server with the agent host, and
gating the two ColGREP-dependent skills on all of that actually succeeding.

Runs the real `./install.sh install --with-colgrep` subprocess (the same
entry point a user runs) against a throwaway `HOME`, the same pattern as
`test_install_sh.py` and `test_install_smoke.py`. `cargo`, `colgrep`, and
`claude` are never assumed to be on the real machine's PATH: each scenario
builds its own PATH, either omitting them (to exercise the missing-toolchain
message) or pointing at small stub executables (to exercise the install/verify
and MCP-registration paths deterministically, without a real network `cargo
install` or a real `claude` CLI, neither of which CI can assume). The stubs
are real, executed subprocesses -- not Python-level mocks -- so what is
asserted is the installer's actual, observable behavior: files it wrote (or
didn't), exit codes, and the exact argv it invoked the host's MCP-registration
command with.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:" + str(REPO_ROOT / ".venv" / "bin")


def _write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _stub_colgrep(bindir: Path) -> None:
    """A stub `colgrep` binary: the installer only checks it exists and is
    executable (`_colgrep_binary`/`shutil.which`), so an empty successful
    program is a faithful stand-in without needing a real ColBERT model
    download.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    _write_stub(bindir / "colgrep", "exit 0")


def _stub_claude(bindir: Path, log: Path) -> None:
    """A stub `claude` binary that records its argv, standing in for the real
    `claude mcp add` -- see `jswarm.host.claude_code.ClaudeCodeHost.mcp_add_argv`.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    _write_stub(bindir / "claude", f'echo "$@" >> "{log}"\nexit 0')


def _run(args, *, home: Path, path: str, extra_env: dict | None = None):
    env = {"HOME": str(home), "JSWARM_HOME": str(REPO_ROOT), "PATH": path}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "install.sh", *args], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


def test_missing_rust_toolchain_is_actionable_not_a_traceback(tmp_path):
    # Neither `cargo` nor `colgrep` on PATH: the installer must name the real
    # blocker and a runnable fix, never raise, and never write the two
    # ColGREP-dependent skills or record a "colgrep" step.
    home = tmp_path / "home"
    r = _run(["install", "--with-colgrep"], home=home, path=BASE_PATH)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "Traceback" not in out
    assert "Rust toolchain not found" in out
    assert "rustup.rs" in out

    lock = yaml.safe_load((home / ".jswarm" / "install.lock.yaml").read_text())
    assert "colgrep" not in lock["steps_completed"]
    assert lock["state"] == "complete"  # colgrep is optional; it never gates completeness

    skills = home / ".claude" / "skills"
    assert not (skills / "colgrep-search").exists()
    assert not (skills / "code-overview").exists()


def test_dry_run_with_colgrep_writes_nothing(tmp_path):
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    _stub_colgrep(bindir)
    path = f"{bindir}:{BASE_PATH}"

    before = set(tmp_path.rglob("*"))
    r = _run(["install", "--with-colgrep", "--dry-run"], home=home, path=path)
    after = set(tmp_path.rglob("*"))
    assert r.returncode == 0, r.stdout + r.stderr

    # Only the stub binary from setup exists; the dry run itself created nothing.
    assert after - before == set(), f"dry-run created: {after - before}"

    out = r.stdout + r.stderr
    assert "found existing binary" in out
    assert "would run" in out and "mcp" in out and "add" in out
    assert "code-overview skipped" not in out
    assert "colgrep-search skipped" not in out


def test_real_run_installs_colgrep_registers_mcp_server_and_records_step(tmp_path):
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "claude-mcp-add.log"
    _stub_colgrep(bindir)
    _stub_claude(bindir, log)
    path = f"{bindir}:{BASE_PATH}"

    r = _run(["install", "--with-colgrep"], home=home, path=path)
    assert r.returncode == 0, r.stdout + r.stderr

    lock = yaml.safe_load((home / ".jswarm" / "install.lock.yaml").read_text())
    assert "colgrep" in lock["steps_completed"]
    assert lock["state"] == "complete"

    assert log.is_file(), "the installer never invoked the stub `claude mcp add`"
    call = log.read_text().strip()
    venv_python = str(REPO_ROOT / ".venv" / "bin" / "python")
    mcp_server = str(REPO_ROOT / "jswarm" / "colgrep_mcp_server.py")
    assert call == f"mcp add --scope user colgrep {venv_python} {mcp_server}"

    skills = home / ".claude" / "skills"
    assert (skills / "colgrep-search" / "SKILL.md").is_file()
    assert (skills / "code-overview" / "SKILL.md").is_file()


def test_without_the_flag_colgrep_is_skipped_and_dependent_skills_are_not_installed(tmp_path):
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "claude-mcp-add.log"
    _stub_colgrep(bindir)
    _stub_claude(bindir, log)
    path = f"{bindir}:{BASE_PATH}"

    r = _run(["install"], home=home, path=path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not log.exists(), "no --with-colgrep: the MCP server must never be registered"

    lock = yaml.safe_load((home / ".jswarm" / "install.lock.yaml").read_text())
    assert "colgrep" not in lock["steps_completed"]

    skills = home / ".claude" / "skills"
    assert not (skills / "colgrep-search").exists()
    assert not (skills / "code-overview").exists()


def test_missing_host_cli_during_mcp_registration_is_actionable_not_a_traceback(tmp_path):
    # colgrep is present (so registration is attempted) but the host's `claude` CLI
    # is not on PATH at all: `subprocess.run` raises FileNotFoundError for a missing
    # executable, which WriteContext.run must turn into an actionable failure, not
    # let escape as a bare traceback that aborts the whole install.
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    _stub_colgrep(bindir)  # no stub `claude` here, deliberately
    path = f"{bindir}:{BASE_PATH}"

    r = _run(["install", "--with-colgrep"], home=home, path=path)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "Traceback" not in out
    assert "MCP registration failed" in out

    lock = yaml.safe_load((home / ".jswarm" / "install.lock.yaml").read_text())
    assert "colgrep" not in lock["steps_completed"]
    skills = home / ".claude" / "skills"
    assert not (skills / "colgrep-search").exists()
    assert not (skills / "code-overview").exists()


def test_check_reports_rust_toolchain_as_optional(tmp_path):
    # `check` must report the Rust toolchain like any other prerequisite, with a
    # runnable remedy -- but never fail the whole run over it (it gates only
    # `--with-colgrep`, not the core loop). Isolate this from every *other*
    # prerequisite `check` reports (Xcode CLT, Homebrew, gh, ...), which vary by
    # machine, by exercising the platform layer directly.
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from jswarm.platform.base import Check
    from jswarm.platform.base import ToolPlatform

    plat = ToolPlatform("test")
    checks = plat.check_prerequisites()
    rust = [c for c in checks if "rust" in c.name.lower()]
    assert len(rust) == 1, f"expected exactly one Rust toolchain check, got: {checks}"
    assert rust[0].remedy, "the Rust toolchain check must carry a runnable remedy"
    assert rust[0].optional is True


def test_rust_toolchain_check_reflects_path(tmp_path, monkeypatch):
    from jswarm.platform.base import ToolPlatform

    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    assert ToolPlatform("test")._rust_toolchain_check().ok is False

    cargo_bin = tmp_path / "cargo-bin"
    cargo_bin.mkdir()
    _write_stub(cargo_bin / "cargo", "exit 0")
    monkeypatch.setenv("PATH", str(cargo_bin))
    assert ToolPlatform("test")._rust_toolchain_check().ok is True


def test_optional_check_failing_does_not_fail_cmd_check(monkeypatch, capsys):
    # A focused unit test of the `check` <-> `Check.optional` contract itself,
    # independent of what Rust/Xcode/Homebrew/etc. happen to be on this
    # machine: a fabricated platform with one failing *required* check and one
    # failing *optional* check must still report failure only for the
    # required one.
    import argparse

    from jswarm.installer import cli as installer_cli
    from jswarm.platform.base import Check

    class _FakePlatform:
        name = "fake"

        def is_supported(self):
            return True

        def check_prerequisites(self):
            return [Check("required thing", True, "n/a"), Check("optional thing", False, "brew install optional-thing", optional=True)]

        def unsupported_message(self):
            return ""

    monkeypatch.setattr("jswarm.platform.current", lambda: _FakePlatform())
    rc = installer_cli._cmd_check(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "all prerequisites present" in out
    assert "optional thing" in out and "brew install optional-thing" in out


def test_with_colgrep_after_a_plain_install_deploys_the_gated_skills(tmp_path):
    """docs/getting-started.md documents the resume path in these words: "If you
    already ran `install` without `--with-colgrep`, re-run it with the flag added
    -- `install` resumes from the first incomplete optional step, the same way it
    resumes any partial install." Before this fix, `install` marked "skills" done
    on the first (no-flag) run and never revisited it, so a second `install
    --with-colgrep` installed colgrep itself but silently left colgrep-search and
    code-overview undeployed -- exit 0, no warning. This drives that exact
    documented sequence end to end and proves both skills land on the second run.
    """
    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    log = tmp_path / "claude-mcp-add.log"
    _stub_colgrep(bindir)
    _stub_claude(bindir, log)
    path = f"{bindir}:{BASE_PATH}"

    r1 = _run(["install"], home=home, path=path)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    skills = home / ".claude" / "skills"
    assert not (skills / "colgrep-search").exists()
    assert not (skills / "code-overview").exists()
    lock1 = yaml.safe_load((home / ".jswarm" / "install.lock.yaml").read_text())
    assert "skills" in lock1["steps_completed"]
    assert "colgrep" not in lock1["steps_completed"]
    assert "skills_colgrep" not in lock1["steps_completed"]

    r2 = _run(["install", "--with-colgrep"], home=home, path=path)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    out2 = r2.stdout + r2.stderr
    assert "install: skills (already done)" not in out2, (
        "the resume run must not silently skip redeploying the colgrep-gated skills"
    )

    assert (skills / "colgrep-search" / "SKILL.md").is_file(), "colgrep-search was not deployed on resume"
    assert (skills / "code-overview" / "SKILL.md").is_file(), "code-overview was not deployed on resume"

    lock2 = yaml.safe_load((home / ".jswarm" / "install.lock.yaml").read_text())
    assert "colgrep" in lock2["steps_completed"]
    assert "skills_colgrep" in lock2["steps_completed"]

    # A third run (still --with-colgrep) must go back to being a genuine no-op:
    # both skills already present, nothing re-copied, "already done" printed.
    r3 = _run(["install", "--with-colgrep"], home=home, path=path)
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert "install: skills (already done)" in (r3.stdout + r3.stderr)
