"""CLI entry point for ``python -m jswarm.installer.jsetup``.

Guided day-0 front door for a fresh JarviSWARM clone: inspects the
repository-local ``.venv`` and dependency-source state and reports whether
the environment is healthy, using the checks in
:mod:`jswarm.installer.jsetup.bootstrap`.

Per the safety contract in ``skills/jSetup/SKILL.md``, the default
invocation (no args, ``--help``, or the explicit ``status`` subcommand) is
strictly read-only: it only calls :func:`bootstrap.check_bootstrap` and
never writes to disk. The one state-changing action -- creating or
repairing ``.venv`` and installing the required dependencies -- lives behind
the ``repair`` subcommand and requires an explicit ``--yes`` flag; without
it, ``repair`` prints the same read-only preview as ``status``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jswarm.installer.jsetup.bootstrap import (
    BootstrapReport,
    check_bootstrap,
    repair_bootstrap,
)

# jswarm/installer/jsetup/__main__.py -> repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_STATUS_MARKER = {"ok": "ok  ", "fail": "FAIL", "skip": "skip", "warn": "warn"}


def _print_report(report: BootstrapReport) -> None:
    print(f"jSetup: {report.repo_root}")
    for step in report.steps:
        marker = _STATUS_MARKER.get(step["status"], step["status"])
        print(f"  [{marker}] {step['step']}: {step['detail']}")
    print()
    if report.healthy:
        print("jSetup: environment healthy.")
    else:
        print("jSetup: environment is NOT healthy (see FAIL lines above).")
        print(
            'Next: PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" '
            '"${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" '
            "-m jswarm.installer.jsetup repair --yes"
            "   (mutates: creates/repairs .venv and installs deps)"
        )


def _emit(report: BootstrapReport, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_json_dict(), indent=2, sort_keys=True))
    else:
        _print_report(report)


def _cmd_status(args: argparse.Namespace) -> int:
    report = check_bootstrap(_REPO_ROOT, dev=args.dev)
    _emit(report, as_json=args.json)
    return 0 if report.healthy else 1


def _cmd_repair(args: argparse.Namespace) -> int:
    if not args.yes:
        preview = check_bootstrap(_REPO_ROOT, dev=args.dev)
        _emit(preview, as_json=args.json)
        if not args.json:
            print()
            print("repair: read-only preview only, nothing was changed. Re-run with --yes to apply.")
        return 0 if preview.healthy else 1
    report = repair_bootstrap(_REPO_ROOT, dev=args.dev, replace_invalid=args.replace_invalid)
    _emit(report, as_json=args.json)
    return 0 if report.healthy else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jsetup",
        description="Guided day-0 front door for a fresh JarviSWARM clone.",
    )
    parser.add_argument("--dev", action="store_true", help="also check dev dependencies (requirements-dev.txt)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON instead of text")
    parser.set_defaults(func=_cmd_status)

    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="read-only environment check (default)")
    status.set_defaults(func=_cmd_status)

    repair = sub.add_parser(
        "repair",
        help="create/repair .venv and install required deps (mutates; requires --yes)",
    )
    repair.add_argument("--yes", action="store_true", help="confirm the mutation; without it this is a read-only preview")
    repair.add_argument(
        "--replace-invalid",
        dest="replace_invalid",
        action="store_true",
        help="recreate an existing but invalid .venv",
    )
    repair.set_defaults(func=_cmd_repair)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
