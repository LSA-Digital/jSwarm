"""Explicit, pinned update steps for /jUpgrade. No background updates or force resets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from jswarm.installer.fsops import WriteContext
from jswarm.paths import jswarm_home

ORIGINS = {
    "https://github.com/LSA-Digital/jSwarm",
    "https://github.com/LSA-Digital/jSwarm.git",
    "git@github.com:LSA-Digital/jSwarm.git",
    "ssh://git@github.com/LSA-Digital/jSwarm.git",
}


class UpgradeError(RuntimeError):
    pass


def environment(source: Path):
    env = WriteContext(False, Path.home()).env()
    # Inherited Git overrides must not redirect commands to the user's application.
    env = {key: value for key, value in env.items() if not key.startswith("GIT_")}
    env.update(JSWARM_HOME=str(source), PYTHONPATH=str(source),
               PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def git(source: Path, *args: str, allowed=(0,)):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(source), *args],
                            env=environment(source), capture_output=True, text=True, timeout=90)
    if result.returncode not in allowed:
        # Do not echo remote credentials or unrestricted Git diagnostics.
        raise UpgradeError(f"Git {args[0]} failed. No force reset or automatic recovery was attempted.")
    return result


def local_state(source: Path):
    source = source.resolve()
    root = git(source, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(root).resolve() != source or not (source / "install.sh").is_file():
        raise UpgradeError("Use the jSwarm clone, not an application folder.")
    if git(source, "remote", "get-url", "origin").stdout.strip() not in ORIGINS:
        raise UpgradeError("Origin is not the public LSA-Digital/jSwarm repository. Review your custom update path manually.")
    if git(source, "branch", "--show-current").stdout.strip() != "main":
        raise UpgradeError("This checkout is pinned to a tag or a custom branch. It will not be switched automatically. Choose your update channel manually.")
    if git(source, "status", "--porcelain", "--untracked-files=normal").stdout.strip():
        raise UpgradeError("The jSwarm clone has local changes. Preserve them before updating; nothing was discarded or stashed.")
    if not (Path.home() / ".jswarm/install.lock.yaml").is_file():
        raise UpgradeError("No existing installation record. Follow the install guide instead of upgrading.")
    return git(source, "rev-parse", "HEAD").stdout.strip()


def check(source: Path):
    current = local_state(source)
    lines = git(source, "ls-remote", "origin", "refs/heads/main", "refs/tags/v*").stdout.splitlines()
    refs = dict((ref, sha) for sha, ref in (line.split() for line in lines))
    target = refs.get("refs/heads/main", "")
    if not re.fullmatch(r"[a-f0-9]{40}", target):
        raise UpgradeError("Could not resolve public main. Nothing was updated.")
    versions = [tuple(map(int, match.groups())) for ref in refs
                if (match := re.fullmatch(r"refs/tags/v(\d+)\.(\d+)\.(\d+)", ref))]
    return {"channel": "main (may contain unreleased changes)", "current": current, "target": target,
            "update_available": current != target,
            "latest_tag": "v" + ".".join(map(str, max(versions))) if versions else None,
            "note": "Different commits are not proof of a safe upgrade. Prepare checks fast-forward ancestry before changing source."}


def prepare(source: Path, expected_current: str, expected_target: str):
    """Approved source update, then print installer preview. Never install skills here."""
    state = check(source)
    if state["current"] != expected_current or state["target"] != expected_target:
        raise UpgradeError("The checkout or remote changed since review. Check again and request new approval.")
    if expected_current == expected_target:
        print("Already at latest main. No source changes made.")
        return
    git(source, "fetch", "--no-tags", "origin", expected_target)
    if local_state(source) != expected_current:
        raise UpgradeError("The checkout changed during fetch. Stop and inspect it.")
    ancestry = git(source, "merge-base", "--is-ancestor", expected_current, expected_target, allowed=(0, 1))
    if ancestry.returncode:
        raise UpgradeError("History diverged or this would go backward. Fetch metadata changed, but source and installed skills were left alone.")
    git(source, "merge", "--ff-only", expected_target)
    print(f"Source updated to {expected_target}. Installed skills are not updated yet.", flush=True)
    installer(source, "check")
    installer(source, "upgrade", "--dry-run")
    print("Review the preview above. Only after approval, run the install step with this exact target.")


def installer(source: Path, *args: str):
    result = subprocess.run(["bash", str(source / "install.sh"), *args], cwd=source,
                            env=environment(source), timeout=900)
    if result.returncode:
        raise UpgradeError(f"Installer {args[0]} failed. Source may be updated; installation is not confirmed. Preserve the output and stop.")


def install(source: Path, expected_target: str):
    if local_state(source) != expected_target:
        raise UpgradeError("Source changed since approval. Check and preview again before installing.")
    installer(source, "upgrade")
    installer(source, "verify")
    print("Upgrade and verification passed. Restart Claude Code in your application folder. No project was re-adopted and no HUD was enabled.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("check", help="Read-only local checks and remote version query")
    prep = sub.add_parser("prepare", help="After approval: fetch and fast-forward source, then preview installation")
    prep.add_argument("--expected-current", required=True)
    prep.add_argument("--expected-target", required=True)
    apply = sub.add_parser("install", help="After preview approval: install the exact reviewed source and verify")
    apply.add_argument("--expected-target", required=True)
    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 0
    for key in ("expected_current", "expected_target"):
        if hasattr(args, key) and not re.fullmatch(r"[a-f0-9]{40}", getattr(args, key)):
            parser.error("Expected commits must be full 40-character hashes from check.")
    source = jswarm_home().resolve()
    try:
        if args.action == "check":
            print(json.dumps(check(source), indent=2))
        elif args.action == "prepare":
            prepare(source, args.expected_current, args.expected_target)
        else:
            install(source, args.expected_target)
        return 0
    except (UpgradeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"jUpgrade stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
