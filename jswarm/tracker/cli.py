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
from dataclasses import asdict
from pathlib import Path

from jswarm.tracker.resolve import load
from jswarm.tracker.base import HostRequest
from jswarm.tracker.jira import complete_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jswarm.tracker.cli")
    parser.add_argument("action", choices=["describe", "is-configured", "resolve", "comment", "transition"])
    parser.add_argument("work_id", nargs="?", default=None)
    parser.add_argument("--repo", default=".", help="repository root holding .jswarm/config.yaml")
    parser.add_argument("--text", help="comment body, for action=comment")
    parser.add_argument("--state", help="target state name, for action=transition")
    parser.add_argument("--result-file", type=Path, help="host-observed Atlassian result for this exact request; see docs/jira-host-bridge.md")
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
        result = tracker.resolve(args.work_id)
    elif args.action == "comment":
        if not args.text:
            parser.error("action 'comment' requires --text")
        result = tracker.comment(args.work_id, args.text)
    else:  # transition
        if not args.state:
            parser.error("action 'transition' requires --state")
        result = tracker.transition(args.work_id, args.state)

    if isinstance(result, HostRequest) and args.result_file:
        try:
            result = complete_request(result, json.loads(args.result_file.read_text(encoding="utf-8")))
        except (OSError, ValueError) as error:
            print(json.dumps({"ok": False, "skipped": False, "message": f"Invalid host result: {error}"}))
            return 2
    elif args.result_file:
        parser.error("--result-file requires a configured host-backed tracker operation")
    print(json.dumps(asdict(result) if result is not None else None))
    # Exit 0 means valid JSON, not remote success. The skill must handle
    # requires_host and failed reads before continuing its lifecycle.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
