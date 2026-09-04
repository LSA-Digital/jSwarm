"""Coverage for `/jSetup` and `jswarm.installer.jsetup.bootstrap`.

Neither had a single test before this file. That gap is exactly how two
bugs shipped at once: the command was dead on arrival from where the
adopted-project-facing docs told a user to run it (see
tests/test_entry_points_smoke.py), and once actually reachable it depended
on a `requirements.lock` that nothing in this repository has ever produced
or committed -- so `status` always failed and `repair`'s own suggested
remedy failed identically before doing anything.

This file exercises both fixes for real:

- `test_status_from_adopted_project_reports_healthy` and
  `test_repair_preview_from_adopted_project_reports_healthy` run the
  literal documented command line (`skills/jSetup/SKILL.md`) via subprocess
  from a `tmp_path` standing in for an adopted project -- not `--help`
  (test_entry_points_smoke.py already covers that as a liveness probe),
  the real `status`/`repair` subcommands -- and assert they report a
  healthy environment, using this checkout's own already-provisioned
  `.venv` and `requirements.txt`. No lock file is involved anywhere.
- `test_repair_bootstrap_repairs_a_real_repairable_state_instead_of_bailing`
  drives `bootstrap.repair_bootstrap` directly against a synthetic
  repairable environment (valid venv, present requirements.txt, one
  dependency reported missing) with a fake runner standing in for
  subprocess calls, and asserts it reaches and issues a real
  `pip install -r requirements.txt` (not `-r requirements.lock`, which
  never exists) rather than bailing on the first check the way it did
  before this fix.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from jswarm.installer.jsetup import bootstrap

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def _adopted_project_env() -> dict[str, str]:
    """The environment a real user's shell has after following
    skills/jSetup/SKILL.md's documented invocation from an adopted project:
    PYTHONPATH naming the jSwarm clone, nothing else jSwarm-specific set.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


def _run_jsetup(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(VENV_PYTHON), "-m", "jswarm.installer.jsetup", *args],
        cwd=str(cwd),
        env=_adopted_project_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_status_from_adopted_project_reports_healthy(tmp_path):
    # tmp_path has no .venv, no jswarm/, nothing -- exactly the adopted
    # project install.sh adopt leaves a user in, and exactly the cwd
    # /jSetup's own docs say to run this from (never this checkout).
    result = _run_jsetup(["--json", "status"], cwd=tmp_path)
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    report = json.loads(result.stdout)
    assert report["healthy"] is True, report
    assert report["dep_source"]["present"] is True, report["dep_source"]
    assert report["dep_source"].get("source_path", "").endswith("requirements.txt")
    assert report["deps_installed"] is True, report


def test_repair_preview_from_adopted_project_reports_healthy(tmp_path):
    # repair without --yes is a read-only preview (same contract as
    # status); it must also resolve and report health from an adopted
    # project, not just the status subcommand.
    result = _run_jsetup(["--json", "repair"], cwd=tmp_path)
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    report = json.loads(result.stdout)
    assert report["healthy"] is True, report
    assert report["mutated"] is False, report


def test_dep_source_status_needs_no_lock_file(tmp_path):
    (tmp_path / "requirements.txt").write_text("pyyaml>=6.0\n", encoding="utf-8")
    status = bootstrap.dep_source_status(tmp_path)
    assert status.present is True
    assert status.source_path == str(tmp_path / "requirements.txt")
    assert not hasattr(status, "lock_path")
    assert not (tmp_path / "requirements.lock").exists()


def test_check_bootstrap_is_healthy_against_this_checkouts_own_venv():
    # Direct, unmocked call against the real, already-provisioned repo:
    # this is the exact environment `install.sh` produces, so jsetup's own
    # health check must call it healthy.
    report = bootstrap.check_bootstrap(REPO_ROOT)
    assert report.healthy is True, report.steps
    fail_steps = [s for s in report.steps if s["status"] == "fail"]
    assert not fail_steps, fail_steps


def test_repair_bootstrap_repairs_a_real_repairable_state_instead_of_bailing(tmp_path):
    # A "repairable state": a valid venv and a present requirements.txt,
    # but one required distribution reported missing. Before this fix,
    # repair_bootstrap bailed at the very first check ("dependency source
    # missing", because requirements.lock never exists) without ever
    # reaching venv or dependency logic. It must now reach real remediation
    # and issue a real pip install targeting requirements.txt.
    repo_root = tmp_path
    (repo_root / "requirements.txt").write_text("pyyaml>=6.0\n", encoding="utf-8")
    venv_dir = repo_root / ".venv"
    (venv_dir / "bin").mkdir(parents=True)
    venv_python = venv_dir / "bin" / "python"
    venv_python.write_text("#!/bin/sh\n", encoding="utf-8")
    (venv_dir / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")

    installed = {"deps": False}
    calls: list[tuple[str, ...]] = []

    class FakeResult:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_runner(cmd, *, cwd=None, timeout=None):
        calls.append(tuple(cmd))
        if "-c" in cmd:
            script = cmd[cmd.index("-c") + 1]
            if "sys.prefix" in script:
                payload = json.dumps(
                    {"prefix": str(venv_dir), "base_prefix": "/usr", "version": [3, 12, 5]}
                )
                return FakeResult(0, stdout=payload)
            if "importlib.metadata" in script:
                if installed["deps"]:
                    return FakeResult(0, stdout="required distributions ok")
                return FakeResult(1, stdout="pyyaml: not installed")
        if "pip" in cmd and "install" in cmd:
            installed["deps"] = True
            return FakeResult(0, stdout="Successfully installed pyyaml")
        if "pip" in cmd and "check" in cmd:
            return FakeResult(0, stdout="No broken requirements found.")
        raise AssertionError(f"unexpected subprocess call in fake runner: {cmd}")

    report = bootstrap.repair_bootstrap(repo_root, runner=fake_runner, allow_create=False)

    # The old bug bailed on the very first check ("dep-source" or
    # "lock-source" fail, because requirements.lock never exists) before
    # ever touching venv or dependency logic. Neither may appear as a fail
    # now; a mid-run "deps" fail (the missing-package detection that
    # *triggers* the repair) is expected and correct.
    bail_steps = [
        s for s in report.steps if s["status"] == "fail" and s["step"] in ("dep-source", "lock-source")
    ]
    assert not bail_steps, f"repair bailed instead of repairing; steps={report.steps}"

    install_calls = [c for c in calls if "pip" in c and "install" in c]
    assert install_calls, f"repair never attempted a pip install; calls={calls}"
    install_cmd = install_calls[0]
    expected_requirements_path = str(Path(repo_root).resolve() / "requirements.txt")
    assert expected_requirements_path in install_cmd, install_cmd
    assert not any("requirements.lock" in part for part in install_cmd), install_cmd

    assert any(s["step"] == "pip-install" and s["status"] == "ok" for s in report.steps), report.steps
    assert report.mutated is True
    assert report.healthy is True, report.steps
