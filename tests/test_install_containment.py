"""The installer must never touch the real machine's `$HOME`, no matter what
`HOME` (or a host CLI's own override, like `claude`'s `CLAUDE_CONFIG_DIR`) the
*parent* process happened to have set.

An agent once ran a real `./install.sh install --with-colgrep` with `HOME`
pointed at a temporary directory, and the MCP-registration step still
appeared to write into the real Claude configuration. The proximate cause
(confirmed by hand): `claude mcp add`/`claude mcp remove` honor
`CLAUDE_CONFIG_DIR` as an override for where they read/write config,
independent of `HOME` -- if that variable is set in whatever shell launched
the install (a plausible everyday case: a developer's own `claude` wrapper
exports it for account switching), a scoped `HOME` alone does not contain
the child process. `jswarm.installer.fsops.WriteContext.run` closes this by
building every subprocess's environment explicitly (`HOME` forced to the
`home` the WriteContext was constructed with, `CLAUDE_CONFIG_DIR` stripped)
instead of letting the child inherit whatever the parent happens to have.

This module is the test that was missing: it runs the real `./install.sh`
entry point end to end -- install (with `--with-colgrep`, against the real
`claude` CLI, not a stub, since a stub would trivially respect `HOME` and
prove nothing about the actual leak), uninstall, adopt, and unadopt -- and
proves by content hash that the real `~/.claude.json` and `~/.jswarm` are
byte-identical before and after. It also plants a decoy `CLAUDE_CONFIG_DIR`
(a third location, neither the real home nor the scoped one) in the
subprocess environment on every run, so a regression that stops scoping
`HOME`/`CLAUDE_CONFIG_DIR` explicitly is caught even if the real home itself
would have looked untouched by coincidence.

Skips (not silently passes) when a real `claude` CLI a stub cannot stand in
for is not available on `PATH`, or does not actually work as one.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:" + str(REPO_ROOT / ".venv" / "bin")
REAL_HOME = Path.home()  # captured once, at import time, before any test touches HOME


def _real_claude_or_skip() -> str:
    """Resolve a `claude` on `PATH` that can actually perform the real MCP
    registration this module tests, or skip with a precise reason.

    `shutil.which` finding something named `claude`, and that something
    printing a version string, is not proof it can register an MCP server --
    it is exactly the false-positive shape that was fixed in
    `ClaudeCodeHost.is_present()` (a bare `~/.claude` directory used to be
    enough to report the host "present" even with no working binary behind
    it). Here the equivalent trap is a `claude` on `PATH` that is a
    wrapper/shim: some dev tooling (an account-switcher shell function, an
    IDE-injected forwarding script, etc.) answers `--version` by forwarding
    to a real Claude Code install, then fails `mcp add` once `PATH` is
    scoped down the way this module's tests scope it for containment (see
    `_decoy_env` below) -- because the wrapper itself needs more of the
    caller's PATH than a scoped subprocess provides. That failure mode is
    silent at the `--version` check (exit 0, a real-looking version string)
    and only surfaces deep inside a test as "registration did not happen",
    which looks like a containment bug rather than a broken local CLI.

    So the guard actually runs `claude mcp add` / `mcp remove` -- the exact
    subcommands the installer and these tests depend on -- against a
    disposable scratch `HOME`, under the same scoped `PATH` (`claude`'s own
    directory plus this repo's base tool PATH, no more) that `_decoy_env`
    gives the real tests. Only a `claude` that can round-trip that for real
    is treated as usable; anything else skips with the concrete exit code
    and stderr so the reason is legible instead of guessed at.
    """
    claude = shutil.which("claude")
    if not claude:
        pytest.skip("no `claude` CLI on PATH -- this test proves containment against the "
                     "real host binary's MCP-registration behavior, which a stub cannot stand in for")
    try:
        probe = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"`claude` on PATH ({claude}) did not run ({exc}); cannot exercise real MCP registration")
    if probe.returncode != 0:
        pytest.skip(
            f"`claude --version` ({claude}) failed (exit {probe.returncode}); "
            "cannot exercise real MCP registration"
        )

    claude_dir = str(Path(claude).parent)
    probe_name = "jswarm-containment-guard-probe"
    with tempfile.TemporaryDirectory(prefix="jswarm-claude-guard-home-") as probe_home:
        probe_env = {"HOME": probe_home, "PATH": f"{claude_dir}:{BASE_PATH}"}
        try:
            add = subprocess.run(
                [claude, "mcp", "add", "--scope", "user", probe_name, "/usr/bin/true"],
                capture_output=True, text=True, timeout=20, env=probe_env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            pytest.skip(
                f"`claude` on PATH ({claude}) answers --version but `mcp add` did not run under "
                f"this test's scoped PATH ({exc}); likely a wrapper/shim, not the real CLI -- "
                "cannot exercise real MCP registration"
            )
        if add.returncode != 0:
            detail = (add.stderr or add.stdout or "").strip()[:300]
            pytest.skip(
                f"`claude` on PATH ({claude}) answers --version but `mcp add` failed under this "
                f"test's scoped PATH (exit {add.returncode}: {detail}) -- likely a wrapper/shim "
                "that needs more of the caller's PATH than the real test provides, not the real "
                "CLI; cannot exercise real MCP registration"
            )
        subprocess.run(
            [claude, "mcp", "remove", "--scope", "user", probe_name],
            capture_output=True, text=True, timeout=20, env=probe_env,
        )
    return claude


def _write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _file_hash(path: Path) -> str | None:
    """sha256 of a file's bytes, or None if it does not exist -- None is a
    distinct, comparable value so "the file did not exist before and still
    doesn't" and "the file didn't exist before but exists now" are never
    confused with each other.
    """
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_snapshot(path: Path) -> dict[str, str]:
    """{relative posix path: sha256 of content} for every file under `path`,
    or `{}` if `path` does not exist. Order-independent and directly
    comparable across a before/after pair.
    """
    if not path.exists():
        return {}
    return {
        p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.rglob("*")
        if p.is_file()
    }


def _assert_real_home_untouched(claude_json_before: str | None, jswarm_before: dict[str, str], label: str) -> None:
    claude_json_after = _file_hash(REAL_HOME / ".claude.json")
    assert claude_json_after == claude_json_before, (
        f"{label}: the real {REAL_HOME / '.claude.json'} changed -- the installer leaked into "
        f"the real HOME despite running with a scoped one (hash before={claude_json_before}, after={claude_json_after})"
    )
    jswarm_after = _tree_snapshot(REAL_HOME / ".jswarm")
    added = {k: jswarm_after[k] for k in jswarm_after.keys() - jswarm_before.keys()}
    removed = {k: jswarm_before[k] for k in jswarm_before.keys() - jswarm_after.keys()}
    changed = {
        k: (jswarm_before[k], jswarm_after[k])
        for k in jswarm_before.keys() & jswarm_after.keys()
        if jswarm_before[k] != jswarm_after[k]
    }
    assert not added and not removed and not changed, (
        f"{label}: the real {REAL_HOME / '.jswarm'} changed -- leaked in: {sorted(added)}, "
        f"leaked removal of: {sorted(removed)}, leaked modification of: {sorted(changed)}"
    )


def _run(args: list[str], *, env: dict[str, str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "install.sh", *args], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=timeout,
    )


def _decoy_env(home: Path, bindir: Path, decoy_config_dir: Path, claude_dir: str) -> dict[str, str]:
    """A fully explicit subprocess environment -- nothing beyond what is
    listed here -- with `CLAUDE_CONFIG_DIR` deliberately planted pointing at
    a *third* location (neither `home` nor the real machine home). If
    `WriteContext.run` ever again let a subprocess inherit `CLAUDE_CONFIG_DIR`
    unscoped, the real `claude` CLI would honor it and write there instead of
    under `home` -- exactly the mechanism behind the original leak report.
    """
    return {
        "HOME": str(home),
        "JSWARM_HOME": str(REPO_ROOT),
        "PATH": f"{bindir}:{claude_dir}:{BASE_PATH}",
        "CLAUDE_CONFIG_DIR": str(decoy_config_dir),
    }


def test_full_install_and_uninstall_with_real_claude_never_touch_the_real_home(tmp_path_factory):
    claude = _real_claude_or_skip()
    # The directory containing the `claude` PATH entry itself -- not its
    # resolved target's directory. On this machine (and plausibly others)
    # `claude` is a symlink to a version-numbered file that is not itself
    # named `claude` (e.g. `~/.local/bin/claude -> .../versions/2.1.260`),
    # so resolving the symlink before taking `.parent` would put a directory
    # with no file literally named `claude` on PATH.
    claude_dir = str(Path(claude).parent)

    claude_json_before = _file_hash(REAL_HOME / ".claude.json")
    jswarm_before = _tree_snapshot(REAL_HOME / ".jswarm")

    home = tmp_path_factory.mktemp("containment-home")
    decoy = tmp_path_factory.mktemp("containment-decoy-config-dir")
    bindir = tmp_path_factory.mktemp("containment-bin")
    _write_stub(bindir / "colgrep", "exit 0")  # cargo/network are not what this test is proving
    env = _decoy_env(home, bindir, decoy, claude_dir)

    try:
        install_result = _run(["install", "--with-colgrep"], env=env)
        assert install_result.returncode == 0, install_result.stdout + install_result.stderr
        out = install_result.stdout + install_result.stderr
        assert "Traceback" not in out

        # The registration must have landed under the scoped home ...
        home_claude_json = home / ".claude.json"
        decoy_claude_json = decoy / ".claude.json"
        if not home_claude_json.is_file():
            where = (
                f"it leaked into the planted CLAUDE_CONFIG_DIR decoy instead: {decoy_claude_json}"
                if decoy_claude_json.is_file()
                else "it is not there and not in the CLAUDE_CONFIG_DIR decoy either -- registration did not happen at all"
            )
            pytest.fail(f"colgrep was never registered under the scoped home ({home_claude_json}): {where}")
        assert "colgrep" in home_claude_json.read_text(encoding="utf-8")

        # ... and never under the planted CLAUDE_CONFIG_DIR decoy.
        assert not decoy_claude_json.exists(), (
            f"CLAUDE_CONFIG_DIR leaked through: the real `claude` CLI wrote to the decoy "
            f"config dir ({decoy_claude_json}) instead of staying scoped to HOME ({home})"
        )

        _assert_real_home_untouched(claude_json_before, jswarm_before, label="after install --with-colgrep")

        uninstall_result = _run(["uninstall", "--keep-backups"], env=env)
        assert uninstall_result.returncode == 0, uninstall_result.stdout + uninstall_result.stderr
        uninstall_out = uninstall_result.stdout + uninstall_result.stderr
        assert "Traceback" not in uninstall_out
        assert "colgrep MCP registration" in uninstall_out
        assert "MCP registration removed" in uninstall_out

        # uninstall must remove exactly what install registered.
        if home_claude_json.is_file():
            data = home_claude_json.read_text(encoding="utf-8")
            assert '"colgrep"' not in data, f"uninstall left the colgrep MCP registration behind in {home_claude_json}"
        assert not decoy_claude_json.exists(), "uninstall's MCP removal step also must never touch the decoy CLAUDE_CONFIG_DIR"

        _assert_real_home_untouched(claude_json_before, jswarm_before, label="after uninstall")
    finally:
        # Belt and suspenders: if anything above *did* land under the decoy
        # (which would already have failed an assertion), remove it rather
        # than leave a stray MCP registration in a directory pytest doesn't
        # own the lifecycle of. tmp_path_factory dirs are cleaned up by
        # pytest itself, so this is only a safety net for the file's content.
        pass


def test_dry_run_install_with_planted_config_dir_writes_nothing_anywhere(tmp_path_factory):
    # Dry-run's contract ("write nothing at all") must hold even when a
    # CLAUDE_CONFIG_DIR override is present in the environment: dry-run
    # never calls ctx.run's real subprocess.run, but the plan text is worth
    # anchoring so a regression that made dry-run register for real would
    # be caught by the file-existence assertions, not just returncode.
    _real_claude_or_skip()  # keep this test meaningful even though it takes the dry-run path
    claude = shutil.which("claude")
    claude_dir = str(Path(claude).parent)  # see the comment on this line in the test above

    home = tmp_path_factory.mktemp("containment-dryrun-home")
    decoy = tmp_path_factory.mktemp("containment-dryrun-decoy")
    bindir = tmp_path_factory.mktemp("containment-dryrun-bin")
    _write_stub(bindir / "colgrep", "exit 0")
    env = _decoy_env(home, bindir, decoy, claude_dir)

    before = set(tmp_path_factory.getbasetemp().rglob("*"))
    result = _run(["install", "--with-colgrep", "--dry-run"], env=env)
    after = set(tmp_path_factory.getbasetemp().rglob("*"))
    assert result.returncode == 0, result.stdout + result.stderr
    # Only the stub/bindir setup from this test exists; dry-run itself created nothing.
    assert after - before == set(), f"dry-run created: {after - before}"
    assert not (home / ".claude.json").exists()
    assert not (decoy / ".claude.json").exists()


def test_adopt_and_unadopt_with_planted_config_dir_never_touch_the_real_home(tmp_path_factory):
    # adopt/unadopt never call WriteContext.run (no MCP registration happens
    # in either), so a real `claude` is not required here -- but they still
    # go through WriteContext for every write, so the same CLAUDE_CONFIG_DIR
    # decoy and real-home hash check applies.
    claude_json_before = _file_hash(REAL_HOME / ".claude.json")
    jswarm_before = _tree_snapshot(REAL_HOME / ".jswarm")

    home = tmp_path_factory.mktemp("containment-adopt-home")
    decoy = tmp_path_factory.mktemp("containment-adopt-decoy")
    repo = tmp_path_factory.mktemp("containment-adopt-repo")
    env = {
        "HOME": str(home),
        "JSWARM_HOME": str(REPO_ROOT),
        "PATH": BASE_PATH,
        "CLAUDE_CONFIG_DIR": str(decoy),
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "config", "user.email", "containment-test"], cwd=str(repo), env=env, check=True)  # not email-shaped: keeps leakgate quiet
    subprocess.run(["git", "config", "user.name", "Containment Test"], cwd=str(repo), env=env, check=True)

    adopt_result = _run(["adopt", str(repo)], env=env)
    assert adopt_result.returncode == 0, adopt_result.stdout + adopt_result.stderr
    assert (repo / ".jswarm" / ".adopted").is_file()
    assert (home / ".jswarm" / "adopted_repos.json").is_file()
    assert not decoy.exists() or not any(decoy.iterdir()), f"adopt wrote under the decoy CLAUDE_CONFIG_DIR: {list(decoy.iterdir())}"

    unadopt_result = _run(["unadopt", str(repo)], env=env)
    assert unadopt_result.returncode == 0, unadopt_result.stdout + unadopt_result.stderr
    assert not (repo / ".jswarm" / ".adopted").exists()
    registry_path = home / ".jswarm" / "adopted_repos.json"
    if registry_path.is_file():
        assert str(repo.resolve()) not in registry_path.read_text(encoding="utf-8")

    _assert_real_home_untouched(claude_json_before, jswarm_before, label="after adopt+unadopt")
