"""Regression coverage for the real bug a from-scratch clean-Mac run found:
`install --with-colgrep`, run a second time (or run once against a machine
that already has a `colgrep` MCP registration left over from anywhere --
another jSwarm session, a prior partial install, a leftover from an earlier
`uninstall` that itself failed to clean up), silently deployed neither
`colgrep-search` nor `code-overview`, with exit 0 and no warning.

Root cause (`jswarm.installer.cli._install_colgrep`): `claude mcp add` exits
non-zero both for a real registration failure and for "colgrep is already
registered" -- which is not a failure, it is the idempotency signal that
ColGREP is already correctly set up. Treating it as a failure set
`colgrep_ready = False`, which gated the two dependent skills off even
though colgrep itself was fine, and also meant `steps_completed` never
recorded "colgrep" -- which is also why `uninstall` had nothing to undo (see
`tests/test_install_colgrep_resume.py::test_uninstall_leaves_no_mcp_registration_behind`
below, and `jswarm.installer.cli._cmd_uninstall`'s `colgrep_registered`
check).

Commit 7850ed8 added a `skills_colgrep` lock-file marker specifically so a
resumed `install --with-colgrep` would redeploy the two skills once colgrep
was ready -- but it never got a chance to fire, because `colgrep_ready` was
wrong in exactly the common case a real `claude mcp add` hits second time
around. `tests/test_install_colgrep.py`'s existing coverage of this resume
path used a stub `claude` that always exits 0 for `mcp add`, so it never
reproduced the actual failure shape the real CLI produces on an
already-registered name, and passed the whole time regardless.

This module drives `./install.sh` for real, against the real `claude` CLI
(a stub cannot reproduce "already exists" -- it would just be told what to
say), with `HOME` scoped to a throwaway directory so nothing here can touch
the real machine's Claude configuration. Skips (not silently passes) when a
real `claude` CLI a stub cannot stand in for is not available on `PATH`.
"""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:" + str(REPO_ROOT / ".venv" / "bin")


def _real_claude_or_skip() -> str:
    """Resolve a `claude` on `PATH` that can actually register/remove an MCP
    server, or skip with a precise, named reason -- never trust a bare
    `claude --version` success (see the identical guard and rationale in
    `tests/test_install_containment.py::_real_claude_or_skip`, which this
    mirrors): a wrapper/shim can answer `--version` by forwarding to a real
    install and then fail `mcp add` once `PATH` is scoped down the way this
    module's tests scope it, which looks like a jSwarm bug rather than a
    local CLI quirk.
    """
    claude = shutil.which("claude")
    if not claude:
        pytest.skip(
            "no `claude` CLI on PATH -- this test proves the fix against the real host "
            "binary's `mcp add`/`mcp remove` exit-code behavior, which a stub cannot "
            "reproduce (a stub would just be told what to print)"
        )
    try:
        probe = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"`claude` on PATH ({claude}) did not run ({exc}); cannot exercise real MCP registration")
    if probe.returncode != 0:
        pytest.skip(f"`claude --version` ({claude}) failed (exit {probe.returncode}); cannot exercise real MCP registration")

    claude_dir = str(Path(claude).parent)
    probe_name = "jswarm-colgrep-resume-guard-probe"
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
                f"this test's scoped PATH ({exc}); likely a wrapper/shim, not the real CLI"
            )
        if add.returncode != 0:
            detail = (add.stderr or add.stdout or "").strip()[:300]
            pytest.skip(
                f"`claude` on PATH ({claude}) answers --version but `mcp add` failed under this "
                f"test's scoped PATH (exit {add.returncode}: {detail}) -- likely a wrapper/shim, "
                "not the real CLI"
            )
        subprocess.run(
            [claude, "mcp", "remove", "--scope", "user", probe_name],
            capture_output=True, text=True, timeout=20, env=probe_env,
        )
    return claude


def _write_stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _env(home: Path, claude_dir: str, bindir: Path) -> dict[str, str]:
    return {"HOME": str(home), "JSWARM_HOME": str(REPO_ROOT), "PATH": f"{bindir}:{claude_dir}:{BASE_PATH}"}


def _run(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "install.sh", *args], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120)


def _registered_mcp_servers(home: Path) -> dict:
    claude_json = home / ".claude.json"
    if not claude_json.is_file():
        return {}
    return json.loads(claude_json.read_text(encoding="utf-8")).get("mcpServers", {})


def test_with_colgrep_after_plain_install_deploys_both_skills_even_when_already_registered(tmp_path):
    """The exact documented resume sequence from a from-scratch clean-Mac
    run: a plain `install`, then `install --with-colgrep` -- except this
    machine (like the one in that report, after enough prior install/
    uninstall cycles) already has a leftover `colgrep` MCP registration
    before the second command ever runs. Before the fix, that made
    `claude mcp add` exit non-zero with "already exists", which
    `_install_colgrep` mis-read as ColGREP being unavailable, which silently
    skipped deploying `colgrep-search` and `code-overview`. Both must
    genuinely land on disk here (a real `SKILL.md` file, not just an empty
    directory), and the lock file must record `colgrep` as done so
    `uninstall` later knows there is a registration to remove.
    """
    claude = _real_claude_or_skip()
    claude_dir = str(Path(claude).parent)

    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    _write_stub(bindir / "colgrep", "exit 0")  # only presence-on-PATH is checked; no real cargo/network needed

    env = _env(home, claude_dir, bindir)

    plain = _run(["install"], env=env)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    skills = home / ".claude" / "skills"
    assert not (skills / "colgrep-search").exists()
    assert not (skills / "code-overview").exists()

    # Plant the leftover registration `install --with-colgrep` will collide
    # with -- exactly what a prior partial/aborted colgrep setup (or this
    # same bug's uninstall side, see the sibling test below) leaves behind.
    leftover = subprocess.run(
        [claude, "mcp", "add", "--scope", "user", "colgrep", "/usr/bin/true"],
        capture_output=True, text=True, timeout=20, env={"HOME": str(home), "PATH": f"{claude_dir}:{BASE_PATH}"},
    )
    assert leftover.returncode == 0, f"test setup: could not plant a leftover colgrep registration: {leftover.stdout + leftover.stderr}"
    assert "colgrep" in _registered_mcp_servers(home)

    resumed = _run(["install", "--with-colgrep"], env=env)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    out = resumed.stdout + resumed.stderr
    assert "Traceback" not in out
    assert "already exists" in out.lower() or "already registered" in out.lower(), (
        f"expected the real CLI's already-registered message to surface, got:\n{out}"
    )

    assert (skills / "colgrep-search" / "SKILL.md").is_file(), (
        f"colgrep-search was not deployed on resume even though ColGREP was already "
        f"registered:\n{out}"
    )
    assert (skills / "code-overview" / "SKILL.md").is_file(), (
        f"code-overview was not deployed on resume even though ColGREP was already "
        f"registered:\n{out}"
    )

    lock = yaml.safe_load((home / ".jswarm" / "install.lock.yaml").read_text())
    assert "colgrep" in lock["steps_completed"], (
        "an already-registered MCP server must still count as ColGREP being ready, "
        "and be recorded as such -- otherwise uninstall never knows to remove it"
    )
    assert "skills_colgrep" in lock["steps_completed"]

    verify = _run(["verify"], env=env)
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "MISSING" not in (verify.stdout + verify.stderr)


def test_uninstall_leaves_no_mcp_registration_behind(tmp_path):
    """`uninstall` already contains the code to remove the `colgrep` MCP
    registration it created (`_cmd_uninstall`'s `colgrep_registered` branch)
    -- but that branch only fires when the lock file's `steps_completed`
    records `colgrep`, and the bug above meant a `claude mcp add` that
    failed with "already exists" never recorded it. The net effect on a
    from-scratch clean-Mac run: after `uninstall`, `claude mcp list` (or the
    user config file) still showed a `colgrep` entry, now pointing at a venv
    that uninstall had just deleted. Fixing `_install_colgrep` to treat
    already-registered as success is what makes uninstall's existing removal
    branch actually run -- this asserts the end state, not a second removal
    call.
    """
    claude = _real_claude_or_skip()
    claude_dir = str(Path(claude).parent)

    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    _write_stub(bindir / "colgrep", "exit 0")
    env = _env(home, claude_dir, bindir)

    install = _run(["install", "--with-colgrep"], env=env)
    assert install.returncode == 0, install.stdout + install.stderr
    assert "colgrep" in _registered_mcp_servers(home)

    lock = yaml.safe_load((home / ".jswarm" / "install.lock.yaml").read_text())
    assert "colgrep" in lock["steps_completed"]

    uninstall = _run(["uninstall", "--keep-backups"], env=env)
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    out = uninstall.stdout + uninstall.stderr
    assert "colgrep MCP registration" in out
    assert "MCP registration removed" in out
    assert "MCP removal failed" not in out

    assert "colgrep" not in _registered_mcp_servers(home), (
        f"uninstall completed but left a dangling 'colgrep' MCP registration behind:\n{out}"
    )
