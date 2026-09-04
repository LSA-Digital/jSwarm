"""Small fail-open CLI for normalizing a single plan file."""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

# Self-bootstrap: this module is invoked both as `python jswarm/update_plan/cli.py`
# (script path; cwd=repo root) and `python -m update_plan.cli`. In the script-path case
# `jswarm/` is NOT on sys.path, so the sibling `plan_status` package would fail to import.
# Ensure the parent `jswarm/` dir is importable regardless of cwd / invocation style.
# Insert the repository root (parents[2]: <pkg>/ -> jswarm/ -> repo root), not
# jswarm/ itself (parents[1]). jswarm/ on sys.path would shadow the stdlib for
# anything under jswarm/ sharing a name with it (e.g. jswarm/platform/ vs the
# stdlib platform module).
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from jswarm.plan_status import config
from jswarm.plan_status import frontmatter as FM
from jswarm.plan_status import reconcile as RC
from jswarm.update_plan import backfill, hygiene
from jswarm.workitem import identity as _workitem_identity


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="update-plan")
    parser.add_argument("--plan", help="Path to the plan file to normalize")
    parser.add_argument("--ticket", help="Ticket key used to locate a canonical plan")
    parser.add_argument("--repo-root", help="Repository root used with --ticket")
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply content moves (relocate tech-spec, archive historic). Default is propose-only.",
    )
    parser.add_argument(
        "--propose", action="store_true",
        help="Explicitly request propose mode (the default): normalize + list candidate moves, move nothing.",
    )
    parser.add_argument("--date", help="Archive date stamp YYYYMMDD (default: today). Used by --apply.")
    return parser


# A work item id, tracker key OR slug (jswarm.workitem.identity; see docs/superpowers/
# specs/2026-09-03-jswarm-public-repo-split-design.md section 4). Used to validate
# --ticket BEFORE it ever reaches a glob, so metacharacters (`*`, `?`, `[`) cannot
# match and mutate an arbitrary plan -- the slug alphabet excludes those too.
_TICKET_RE = re.compile(
    f"^(?:{_workitem_identity.TRACKER_KEY[1:-1]}|{_workitem_identity.SLUG[1:-1]})$"
)


def _resolve_plan(args: argparse.Namespace) -> tuple[Path | None, str | None, str | None]:
    """Resolve (plan_path, display_key, reason). reason is set only when no plan is
    resolved, for a fail-open diagnostic; the caller never writes when plan_path is None."""
    if args.plan:
        path = Path(args.plan)
        return path, config.ticket_from_plan_filename(path.name), None

    if args.ticket:
        # Preserve the ticket as the display key even on the no-plan paths below.
        if not _TICKET_RE.match(args.ticket):
            return None, args.ticket, "invalid ticket key"
        if not args.repo_root:
            return None, args.ticket, "missing --repo-root"
        repo_root = Path(args.repo_root)
        matches = sorted((repo_root / ".jswarm" / "plans").glob(f"{args.ticket}.plan.*.md"))
        if len(matches) > 1:
            return None, args.ticket, "ambiguous: multiple matching plans"
        return (matches[0] if matches else None), args.ticket, None

    return None, None, None


def _value(frontmatter: dict, key: str) -> str:
    value = frontmatter.get(key)
    if value is None:
        return "-"
    return str(value)


def _backfill_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="update-plan backfill")
    parser.add_argument("--repo-root", default=".", help="Repository root to sweep")
    parser.add_argument("--evidence", help="Path to write the backfill evidence artifact")
    parser.add_argument("--date", help="Generated-on date stamp YYYYMMDD (default: today)")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0
    today = args.date or datetime.date.today().strftime("%Y%m%d")
    try:
        res = backfill.run_backfill(
            Path(args.repo_root or "."),
            Path(args.evidence) if args.evidence else None,
            today,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        print(f"update-plan backfill: warning: {exc}")
        return 0
    print(
        f"update-plan backfill: active={res['active']} normalized={res['normalized']} "
        f"missing={len(res['missing'])} blocking={len(res['blocking'])}"
    )
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "backfill":
        return _backfill_main(argv[1:])

    try:
        args = _build_parser().parse_args(argv)
    except SystemExit:
        return 0

    plan_path, key, reason = _resolve_plan(args)
    display_key = key or "-"

    if plan_path is None or not plan_path.exists():
        suffix = f" ({reason})" if reason else ""
        print(f"update-plan {display_key}: no plan{suffix}")
        return 0

    try:
        before = plan_path.read_text(encoding="utf-8")
        RC.normalize_plan_file(plan_path)
        after = plan_path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"update-plan {display_key}: warning: {exc}")
        return 0

    normalized = "Y" if after != before else "N"

    try:
        fm = FM.read_frontmatter(plan_path)
    except Exception as exc:
        print(f"update-plan {display_key}: warning: {exc}")
        return 0

    # Non-silent fail-open: a present-but-invalid frontmatter block is skipped (no write),
    # not normalized — say so rather than reporting a misleading dash row (NFR-012).
    invalid_suffix = " invalid-frontmatter=skipped(no-write)" if FM.frontmatter_is_invalid(after) else ""
    print(
        f"update-plan {display_key}: "
        f"status={_value(fm, 'status')} "
        f"phase={_value(fm, 'phase')} "
        f"ac={_value(fm, 'ac_complete')} "
        f"nfr={_value(fm, 'nfr_complete')} "
        f"uat={_value(fm, 'uat_complete')} "
        f"normalized={normalized}{invalid_suffix}"
    )

    # Content hygiene. Fail-open: any error here leaves the (already-normalized) plan
    # untouched and never changes the exit code.
    _hygiene(plan_path, key, args)
    return 0


def _hygiene(plan_path: Path, key: str | None, args: argparse.Namespace) -> None:
    if key is None:
        return  # need a ticket key to resolve specs/archive targets
    try:
        if args.apply:
            today = args.date or datetime.date.today().strftime("%Y%m%d")
            report = hygiene.apply(plan_path, key, today)
            moved = report["moved_spec"] + report["moved_archive"]
            if moved:
                print(
                    f"applied: relocated {report['moved_spec']} to specs, "
                    f"archived {report['moved_archive']} (--apply)"
                )
            skipped = report.get("skipped") or []
            if skipped:
                # Never silent: a same-title collision keeps the source in the plan.
                print(
                    f"warning: {len(skipped)} section(s) NOT moved (same-title collision; "
                    f"kept in plan, resolve manually): {', '.join(skipped)}"
                )
            return
        # propose / default: move nothing, list candidates only.
        text = plan_path.read_text("utf-8")
        cands = hygiene.proposals(text)
        if not cands:
            return
        spec = sum(1 for k, _ in cands if k == "spec")
        arch = sum(1 for k, _ in cands if k == "archive")
        print(
            f"proposals: {len(cands)} (spec={spec} archive={arch}) "
            f"— re-run with --apply to apply"
        )
        for kind, title in cands:
            print(f"  {kind:<7} → {title}")
    except Exception as exc:  # noqa: BLE001 — live-global path is strictly fail-open
        print(f"update-plan {key}: hygiene warning: {exc}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
