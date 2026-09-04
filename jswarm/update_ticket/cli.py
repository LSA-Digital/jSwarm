"""Deterministic subprocess wrapper for the plan-maintenance chain.

The wrapper and the engines it orchestrates are **common-owned tooling**: their
location is resolved from this file (``_TOOL_ROOT``), NOT from ``--repo-root``. ``--repo-root``
names only the TARGET project whose plans are maintained. This lets the live-global lifecycle
commands invoke the wrapper by an absolute common path and have it work in any project, whether
or not that project has the engine packages propagated locally (B2). In the test
fixtures the wrapper is copied into the tmp repo, so ``_TOOL_ROOT`` == the tmp repo == the target
``--repo-root`` and behavior is identical to the legacy chain.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# The repo that OWNS this wrapper + the engine packages (common in production; the tmp fixture
# repo under test). Engine scripts and the interpreter are resolved relative to this, so the
# wrapper is location-correct no matter which project's plans it is pointed at via --repo-root.
_TOOL_ROOT = Path(__file__).resolve().parents[2]
# Insert _TOOL_ROOT itself, not _TOOL_ROOT / "jswarm": jswarm/ on sys.path would shadow
# the stdlib for anything under jswarm/ sharing a name with it (e.g. jswarm/platform/
# vs the stdlib platform module). _TOOL_ROOT is already the repo root the jswarm
# package lives in, so this is what makes `jswarm.update_ticket...` importable below.
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))

from jswarm.update_ticket import promotions  # noqa: E402

PRESETS: dict[str, list[str]] = {
    "precompact-full": ["promotion-gate", "migrate", "rebuild-rows", "reconcile-status", "count"],
    "close-refresh": ["migrate", "rebuild-rows", "reconcile-status", "count", "audit"],
    "implement-gate": ["audit"],
    "new-work-lint": ["lint"],
}

# Canonical execution order for every section (M1). Explicit --sections are de-duped and
# re-ordered to this sequence before running, so a manual/maintenance invocation can never run a
# count before a migrate or a refresh after a terminal audit/lint.
CANONICAL_ORDER = [
    "promotion-gate", "apply-promotions", "migrate", "rebuild-rows",
    "reconcile-status", "count", "bind-session", "audit", "lint",
]

REFRESH_SECTIONS = {"migrate", "rebuild-rows", "reconcile-status", "count", "bind-session", "apply-promotions"}
FAIL_LOUD_SECTIONS = {"audit", "lint"}
VALID_SECTIONS = REFRESH_SECTIONS | FAIL_LOUD_SECTIONS | {"promotion-gate"}


def expand_preset(name: str) -> list[str]:
    """Expand a named preset to its deterministic section list."""
    try:
        return list(PRESETS[name])
    except KeyError as exc:
        raise ValueError(f"unknown preset: {name}") from exc


def _canonicalize(sections: list[str]) -> list[str]:
    """De-duplicate (first occurrence wins for membership) and order by CANONICAL_ORDER."""
    members = []
    for section in sections:
        if section not in members:
            members.append(section)
    return sorted(members, key=CANONICAL_ORDER.index)


def _tool_python() -> str:
    candidate = _TOOL_ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _section_argv(section: str, *, repo_root: Path, ticket: str, date: str | None, audit_stage: str) -> list[str]:
    # Engine scripts live in the TOOL repo (common); --repo-root targets the project's plans.
    py = _tool_python()

    def script(rel: str) -> str:
        return str(_TOOL_ROOT / "jswarm" / rel)

    if section == "migrate":
        return [py, script("precompact_reconcile/migrate_cli.py"), "--ticket", ticket, "--repo-root", str(repo_root)]
    if section == "rebuild-rows":
        return [py, script("precompact_reconcile/rows_cli.py"), "--ticket", ticket, "--repo-root", str(repo_root)]
    if section == "reconcile-status":
        return [py, script("precompact_reconcile/cli.py"), "--ticket", ticket, "--repo-root", str(repo_root)]
    if section == "count":
        argv = [py, script("update_plan/cli.py"), "--apply", "--ticket", ticket, "--repo-root", str(repo_root)]
        if date:
            argv.extend(["--date", date])
        return argv
    if section == "bind-session":
        return [py, script("plan_status/cli.py"), "--project-root", str(repo_root), "bind", ticket]
    if section == "audit":
        return [py, script("precompact_reconcile/lifecycle_audit.py"), "--ticket", ticket, "--repo-root", str(repo_root), "--stage", audit_stage]
    if section == "lint":
        return [py, script("precompact_reconcile/lifecycle_audit.py"), "--ticket", ticket, "--repo-root", str(repo_root), "--lint"]
    raise ValueError(f"unsupported executable section: {section}")


def _parse_sections(csv: str) -> list[str]:
    sections = [part.strip() for part in csv.split(",") if part.strip()]
    unknown = [section for section in sections if section not in VALID_SECTIONS]
    if unknown:
        raise ValueError("unknown section(s): " + ",".join(unknown))
    return sections


def _resolve_sections(args: argparse.Namespace) -> list[str]:
    raw = expand_preset(args.preset) if args.preset else _parse_sections(args.sections)
    return _canonicalize(raw)


def _default_audit_stage(args: argparse.Namespace) -> str:
    if args.audit_stage:
        return args.audit_stage
    if args.preset == "implement-gate":
        return "implement"
    return "close"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="update-ticket")
    parser.add_argument("--ticket", required=True)
    parser.add_argument("--repo-root", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preset", choices=tuple(PRESETS))
    group.add_argument("--sections")
    parser.add_argument("--date")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--audit-stage", choices=("close", "implement"))
    parser.add_argument("--promotions-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)
    try:
        sections = _resolve_sections(args)
    except ValueError as exc:
        parser.error(str(exc))

    if "promotion-gate" in sections and (not args.interactive or not sys.stdin.isatty()):
        sys.stdout.write("NON_INTERACTIVE_APPROVAL_UNAVAILABLE\n")
        return 2

    audit_stage = _default_audit_stage(args)
    exit_code = 0
    for section in sections:
        if section == "promotion-gate":
            continue  # interactive gate is skill-owned; the AC-6 guard above covers safety
        if section == "apply-promotions":
            code, summary = promotions.apply(repo_root, args.ticket, args.promotions_file)
            sys.stdout.write(summary + "\n")
            if code != 0:
                exit_code = code
                break  # aborted apply (stale/ambiguous/invalid): do not run later sections
            continue
        result = subprocess.run(
            _section_argv(section, repo_root=repo_root, ticket=args.ticket, date=args.date, audit_stage=audit_stage),
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if section in FAIL_LOUD_SECTIONS and result.returncode != 0:
            exit_code = 1
            break  # fail-loud (M1): a BLOCK is terminal — never run a later section after it
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
