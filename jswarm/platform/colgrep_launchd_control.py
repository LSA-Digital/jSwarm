#!/usr/bin/env python3
"""Agent-runnable launchd control for the ColGREP daemon fleet (COM-241 AC-6).

Raw ``launchctl`` prose is error-prone for an autonomous agent (exact label
match, plist path resolution, before/after verification). This CLI wraps
status/start/stop/restart for the three ColGREP launchd components with:

- an EXACT label ``com.colgrep.<component>`` and plist path
  ``~/Library/LaunchAgents/com.colgrep.<component>.plist`` (never a substring
  or prefix match — see ``parse_launchd_loaded``, which reuses the tested,
  exact-match parser from ``jswarm/colgrep_status_report.py``);
- a missing-plist ``start``/``restart`` that returns a loud, actionable error
  pointing at the installer (``deploy/launchd/install-colgrep-launchd.sh``)
  rather than silently succeeding or crashing;
- every mutating verb capturing a before-state and an after-state and setting
  ``ok`` from a proven after-state check (not from the mutating command's own
  exit code, which `launchctl` reports unreliably for idempotent operations).

All ``launchctl`` invocations are behind the injectable ``Runner`` seam so
tests exercise the full status/start/stop/restart logic against a faked
``launchctl`` without ever touching the real binary or a real daemon.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    # Insert the repository root (parents[2]: jswarm/platform/ -> jswarm/ ->
    # repo root), not jswarm/ itself (parents[1]). `jswarm/` on sys.path
    # would shadow the stdlib for anything under jswarm/ that happens to
    # share a name with it -- notably jswarm/platform/ itself against the
    # stdlib `platform` module. The `jswarm.colgrep_status_report` import
    # below needs the repo root on the path anyway.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

COMPONENTS = ("overlay-fleet-supervisor", "watcher", "health-check")
INSTALLER_HINT = "deploy/launchd/install-colgrep-launchd.sh"

# COM-241 AC-6 finding #6: `launchctl kickstart`/`load` can leave a label
# LOADED while the actual daemon process has exited or is crash-looping
# (`launchctl list` shows the label with pid `-`). For a LONG-RUNNING daemon,
# "loaded" alone is not proof of "up" — a live pid is required. A PERIODIC job
# (health-check) is expected to be loaded-without-a-persistent-pid between
# runs, so `loaded` alone is sufficient for it.
LONG_RUNNING_COMPONENTS = frozenset({"overlay-fleet-supervisor", "watcher"})
PERIODIC_COMPONENTS = frozenset({"health-check"})
NO_LIVE_PID_REASON = "loaded but no live pid — daemon may be crash-looping"


def label_for(component: str) -> str:
    """EXACT launchd label for a ColGREP component (never a substring match)."""
    return f"com.colgrep.{component}"


def plist_path_for(component: str) -> Path:
    """EXACT source-of-truth plist path for a ColGREP component."""
    return Path.home() / "Library" / "LaunchAgents" / f"{label_for(component)}.plist"


def _uid() -> int:
    return os.getuid()


@dataclass
class Runner:
    """Injectable subprocess seam. ``run`` takes an argv list and returns a
    ``subprocess.CompletedProcess``-like object with ``.stdout``/``.returncode``.
    Tests substitute a fake ``run`` that never touches the real ``launchctl``."""

    run: Callable[[list], subprocess.CompletedProcess]

    @staticmethod
    def real() -> "Runner":
        def _run(argv: list) -> subprocess.CompletedProcess:
            return subprocess.run(argv, capture_output=True, text=True, timeout=10)

        return Runner(run=_run)


def parse_launchd_loaded(output: Any, component: str) -> dict:
    """Delegate to the tested, exact-label-match parser in
    ``jswarm.colgrep_status_report`` so both tools share one parsing contract
    (a `com.colgrep.watcher.backup` row must never mark `watcher` loaded)."""
    from jswarm.colgrep_status_report import _parse_launchd_loaded

    return _parse_launchd_loaded(output, component)


def _is_live_pid(pid: Any) -> bool:
    """A live pid is a real integer (`isinstance(..., bool)` excluded so a
    stray boolean can never be mistaken for a live pid)."""
    return isinstance(pid, int) and not isinstance(pid, bool)


def _evaluate_up(component: str, after: dict, *, success_reason: str, not_loaded_reason: str) -> tuple:
    """Decide `(ok, reason)` for a `start`/`restart` after-state.

    For a LONG_RUNNING_COMPONENTS member, `loaded` alone is not sufficient —
    the daemon must also have a live pid, or it may be crash-looping while
    launchd still reports the label as loaded. PERIODIC_COMPONENTS (e.g.
    health-check) only need `loaded`.
    """
    if after.get("loaded") is not True:
        return False, not_loaded_reason
    if component in LONG_RUNNING_COMPONENTS and not _is_live_pid(after.get("pid")):
        return False, NO_LIVE_PID_REASON
    return True, success_reason


def get_status(runner: Runner, component: str) -> dict:
    """Return {"loaded": bool|None, "pid": int|None}. Any Runner exception
    (binary missing, timeout, ...) degrades to the honest "unknown" state
    rather than a fabricated healthy/absent default."""
    try:
        completed = runner.run(["launchctl", "list"])
    except Exception:  # noqa: BLE001 - fail-open to unknown, never crash the agent CLI.
        return {"loaded": None, "pid": None}
    return parse_launchd_loaded(getattr(completed, "stdout", None), component)


def start(runner: Runner, component: str) -> dict:
    """`launchctl load <plist>`, verifying the after-state actually shows loaded.

    Idempotent-ok: if the component is already loaded, `load` is still issued
    (it is a no-op against an already-loaded label) and `ok` is True because the
    after-state proves the intent (loaded) is satisfied either way.
    """
    plist = plist_path_for(component)
    before = get_status(runner, component)
    if not plist.exists():
        return {
            "component": component,
            "action": "start",
            "plist_path": str(plist),
            "before": before,
            "after": before,
            "ok": False,
            "reason": f"plist missing at {plist}; run the installer: {INSTALLER_HINT}",
        }
    runner.run(["launchctl", "load", str(plist)])
    after = get_status(runner, component)
    ok, reason = _evaluate_up(
        component,
        after,
        success_reason="loaded",
        not_loaded_reason="load did not result in a loaded state",
    )
    return {
        "component": component,
        "action": "start",
        "plist_path": str(plist),
        "before": before,
        "after": after,
        "ok": ok,
        "reason": reason,
    }


def stop(runner: Runner, component: str) -> dict:
    """`launchctl unload <plist>`, verifying the after-state shows not-loaded."""
    plist = plist_path_for(component)
    before = get_status(runner, component)
    runner.run(["launchctl", "unload", str(plist)])
    after = get_status(runner, component)
    ok = after.get("loaded") is False
    reason = "unloaded" if ok else "unload did not result in a not-loaded state"
    return {
        "component": component,
        "action": "stop",
        "plist_path": str(plist),
        "before": before,
        "after": after,
        "ok": ok,
        "reason": reason,
    }


def restart(runner: Runner, component: str) -> dict:
    """`launchctl kickstart -k gui/$UID/<label>`; if the after-state does not
    prove `loaded`, fall back to `unload` + `load` and re-check."""
    plist = plist_path_for(component)
    label = label_for(component)
    before = get_status(runner, component)
    if not plist.exists():
        return {
            "component": component,
            "action": "restart",
            "plist_path": str(plist),
            "before": before,
            "after": before,
            "ok": False,
            "reason": f"plist missing at {plist}; run the installer: {INSTALLER_HINT}",
        }
    runner.run(["launchctl", "kickstart", "-k", f"gui/{_uid()}/{label}"])
    after = get_status(runner, component)
    if after.get("loaded") is not True:
        runner.run(["launchctl", "unload", str(plist)])
        runner.run(["launchctl", "load", str(plist)])
        after = get_status(runner, component)
    ok, reason = _evaluate_up(
        component,
        after,
        success_reason="restarted",
        not_loaded_reason="restart did not result in a loaded state",
    )
    return {
        "component": component,
        "action": "restart",
        "plist_path": str(plist),
        "before": before,
        "after": after,
        "ok": ok,
        "reason": reason,
    }


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Agent-runnable launchd control for the ColGREP daemon fleet (exact labels, before/after verification)."
    )
    parser.add_argument("verb", choices=("status", "start", "stop", "restart"))
    parser.add_argument("--component", required=True, choices=COMPONENTS)
    parser.add_argument("--json", action="store_true", help="emit JSON (the only supported output format; kept for CLI consistency)")
    return parser


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runner = Runner.real()

    if args.verb == "status":
        status = get_status(runner, args.component)
        result = {
            "component": args.component,
            "action": "status",
            "plist_path": str(plist_path_for(args.component)),
            **status,
        }
        ok = status.get("loaded") is not None
    elif args.verb == "start":
        result = start(runner, args.component)
        ok = result["ok"]
    elif args.verb == "stop":
        result = stop(runner, args.component)
        ok = result["ok"]
    else:
        result = restart(runner, args.component)
        ok = result["ok"]

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
