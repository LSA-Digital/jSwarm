"""The tracker CLI boundary: what a skill's Markdown calls when it needs
tracker state, instead of inventing tracker behaviour (field names, REST
shapes, Jira-specific verbs) in prose. A skill runs this and acts on the
printed JSON; it never has to know which adapter is configured, or none.

This never raises for a tracker outage or a "no tracker configured" repo --
those are ordinary results, printed as JSON, exit 0. An unrecognised adapter
name in .jswarm/config.yaml is a real configuration error and is allowed to
raise, same as jswarm.tracker.resolve.load does.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from jswarm.tracker.resolve import load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jswarm.tracker.cli")
    parser.add_argument("action", choices=["describe", "is-configured", "resolve", "comment", "transition"])
    parser.add_argument("work_id", nargs="?", default=None)
    parser.add_argument("--repo", default=".", help="repository root holding .jswarm/config.yaml")
    parser.add_argument("--text", help="comment body, for action=comment")
    parser.add_argument("--state", help="target state name, for action=transition")
    args = parser.parse_args(argv)

    tracker = load(Path(args.repo))

    if args.action == "describe":
        print(json.dumps({"describe": tracker.describe(), "configured": tracker.is_configured()}))
        return 0
    if args.action == "is-configured":
        print(json.dumps({"configured": tracker.is_configured()}))
        return 0

    if not args.work_id:
        parser.error(f"action {args.action!r} requires a work item id")

    if args.action == "resolve":
        item = tracker.resolve(args.work_id)
        print(json.dumps(asdict(item) if item is not None else None))
        return 0

    if args.action == "comment":
        if not args.text:
            parser.error("action 'comment' requires --text")
        result = tracker.comment(args.work_id, args.text)
    else:  # transition
        if not args.state:
            parser.error("action 'transition' requires --state")
        result = tracker.transition(args.work_id, args.state)

    print(json.dumps({"ok": result.ok, "skipped": result.skipped, "message": result.message}))
    # Never a gate: a skill reads ok/skipped/message and continues either way.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
