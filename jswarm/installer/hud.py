"""Optional statusline configuration with explicit ownership and rollback."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from jswarm.host import current
from jswarm.installer.fsops import WriteContext
from jswarm.paths import jswarm_home


def command(source: Path):
    return {"type": "command", "command": shlex.join([
        "env", f"PYTHONPATH={source.resolve()}", "PYTHONDONTWRITEBYTECODE=1", "JSWARM_HUD_COLOR=1",
        str(source.resolve() / ".venv/bin/python"), "-m", "jswarm.hud"]), "padding": 0}


def locations(home):
    settings = current().claude_home() / "settings.json"
    receipt = home / ".jswarm/hud-install.json"
    for path in (settings, receipt):
        if path.is_symlink() or not path.resolve().is_relative_to(home.resolve()):
            raise ValueError("HUD paths must stay inside this home without file symlinks.")
    return settings, receipt


def load(path):
    if not path.exists():
        return {}
    if path.stat().st_size > 1_000_000:
        raise ValueError("Configuration is too large. Inspect it before continuing.")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object.")
    return data


def save(ctx, path, data):
    """Owner-only atomic writes: never expose a previous statusline's secrets mid-write."""
    if ctx.dry_run:
        ctx.write_json(path, data)
        return
    print(f"  write {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify():
    try:
        home = Path.home()
        settings, receipt = locations(home)
        expected = command(jswarm_home())
        saved = load(receipt)
        if saved.get("installed") != expected or load(settings).get("statusLine") != expected:
            return False, "HUD registration or receipt changed; inspect before repairing"
        result = subprocess.run(shlex.split(expected["command"]), cwd=home,
            env=WriteContext(False, home).env(), input=json.dumps({"model": {"display_name": "HUD verification"}}),
            capture_output=True, text=True, timeout=5)
        if result.returncode != 0 or "jSwarm" not in result.stdout or "HUD verification" not in result.stdout:
            return False, "HUD renderer did not produce the expected output"
        return True, "HUD launcher rendered a test payload; live session and quota values still need a visual check"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, "HUD verification failed; inspect the configuration and Python environment"


def configure(action, *, dry_run=False, replace_existing=False):
    home = Path.home()
    ctx = WriteContext(dry_run=dry_run, home=home)
    settings, receipt = locations(home)
    data, saved = load(settings), load(receipt)
    expected = command(jswarm_home())
    if action == "status":
        print("HUD enabled" if saved and data.get("statusLine") == saved.get("installed") else "HUD disabled or changed; no settings modified")
        return 0
    if saved and (saved.get("schema") != "jswarm.hud-install.v1" or saved.get("installed") != expected):
        raise ValueError("HUD belongs to a different clone or has an invalid receipt. Inspect it before changing settings.")
    if action == "enable":
        if saved:
            # Recover a receipt-first interrupted write without replacing a user's later edit.
            previous_matches = ((saved.get("had_previous") and data.get("statusLine") == saved.get("previous")) or
                                (not saved.get("had_previous") and "statusLine" not in data))
            if data.get("statusLine") != expected and not previous_matches:
                raise ValueError("Your status line changed after installation. Inspect it before re-enabling the HUD.")
            if previous_matches:
                data["statusLine"] = expected
                save(ctx, settings, data)
                print("HUD repair preview complete" if dry_run else "HUD enabled. Restart Claude Code to see it.")
                return 0
            print("HUD already enabled. Restart Claude if it is not visible.")
            return 0
        if "statusLine" in data and not replace_existing:
            raise ValueError("A status line already exists. Preview again with --replace-existing to approve replacing it.")
        saved = {"schema": "jswarm.hud-install.v1", "installed": expected,
                 "had_previous": "statusLine" in data, "previous": data.get("statusLine")}
        save(ctx, receipt, saved)
        data["statusLine"] = expected
        save(ctx, settings, data)
        print("HUD preview complete" if dry_run else "HUD enabled. Restart Claude Code to see it.")
    elif action == "disable":
        if not saved:
            print("No jSwarm HUD installation recorded. Existing status line left untouched.")
            return 0
        previous_matches = ((saved.get("had_previous") and data.get("statusLine") == saved.get("previous")) or
                            (not saved.get("had_previous") and "statusLine" not in data))
        if previous_matches:
            ctx.remove(receipt)
            print("Previous status line already restored; would remove the jSwarm receipt." if dry_run else
                  "Previous status line already restored; removed the jSwarm receipt.")
            return 0
        if "statusLine" in data and data["statusLine"] != expected:
            raise ValueError("Your status line changed after installation. Restore the recorded value or inspect it before uninstalling.")
        if saved.get("had_previous"):
            data["statusLine"] = saved.get("previous")
        else:
            data.pop("statusLine", None)
        save(ctx, settings, data)
        ctx.remove(receipt)
        print("HUD removal preview complete" if dry_run else "HUD disabled. Previous status line restored; other settings preserved.")
    return 0


def run(args):
    try:
        return configure(args.action, dry_run=args.dry_run, replace_existing=args.replace_existing)
    except (ValueError, OSError) as exc:
        print(f"HUD not changed or incomplete: {exc}")
        return 1
