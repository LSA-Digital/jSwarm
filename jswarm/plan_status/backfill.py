#!/usr/bin/env python3
"""A/C 13: one-time backfill of plan_status for in-flight plans (Oracle Concern #8).

SAFE BY DEFAULT:
  - dry-run unless --apply <tickets> or --apply-all --require-clean-tree
  - per-file working-tree-clean gate (never clobbers uncommitted edits)
  - confidence flags (high/medium/low); low-confidence rows are NOT applied unless
    --include-low, and are surfaced for manual review
  - writes a report to docs/plans/evidence/plan-status-backfill-YYYYMMDD.md
  - never mass-edits silently

Inference (spec §6), from the existing merge `status:` frontmatter + body phase markers:
  status DONE            -> 6.closed.merged             (high)
  status READY_FOR_MERGE -> 5.closed.ready_for_merge    (high)
  status WONT_DO         -> terminal.wont_do            (high)
  status DEFERRED        -> terminal.deferred           (high)
  status ACTIVE + phase progress markers -> 3.implementation.phase_{N}.backfilled (medium)
  status ACTIVE + no phase markers       -> 2.planning.detailed (high)
  no status: field                       -> (low, needs manual review)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# Insert the repository root (parent of jswarm/), not jswarm/ itself: this makes
# `import jswarm.plan_status...` resolve whether this file is run directly by path
# (as the lifecycle skills do) or imported as a module, without putting jswarm/'s
# own directory at the front of sys.path -- which would shadow the stdlib for
# anything under jswarm/ that happens to share a name with it (e.g. jswarm/platform/).
sys.path.insert(0, str(_HERE.parent.parent))

from jswarm.plan_status import config as C
from jswarm.plan_status import frontmatter as FM
from jswarm.plan_status import registry as R
from jswarm.plan_status import state as S

_PHASE_MARKER_RE = re.compile(r"^#{2,4}\s*Phase\s+(\d+)", re.IGNORECASE | re.MULTILINE)
_IN_PROGRESS_MARKER = "\U0001F7E1"  # 🟡
_DONE_MARKER = "\U0001F7E2"          # 🟢


@dataclass
class Inference:
    ticket: str
    plan_file: str
    current_status: str | None
    plan_status: str | None
    confidence: str
    reason: str


def infer(status: str | None, body: str) -> tuple[str | None, str, str]:
    """Return (plan_status, confidence, reason) from merge status + plan body."""
    if status is None:
        return None, "low", "no status: field — manual review required"
    s = status.strip().upper()
    if s == "DONE":
        return S.STATE_MERGED, "high", "status DONE -> 6.closed.merged"
    if s == "READY_FOR_MERGE":
        return S.STATE_READY_FOR_MERGE, "high", "status READY_FOR_MERGE -> 5.closed.ready_for_merge"
    if s == "WONT_DO":
        return S.STATE_WONT_DO, "high", "status WONT_DO -> terminal.wont_do"
    if s == "DEFERRED":
        return S.STATE_DEFERRED, "high", "status DEFERRED -> terminal.deferred"
    if s == "ACTIVE":
        phases = _PHASE_MARKER_RE.findall(body)
        has_progress = _IN_PROGRESS_MARKER in body or (_DONE_MARKER in body and phases)
        if phases and has_progress:
            # First phase number as a best-effort guess; explicitly marked backfilled.
            n = phases[0]
            return (f"3.implementation.phase_{n}.backfilled", "medium",
                    f"status ACTIVE + phase markers -> 3.implementation.phase_{n} (phase # is a guess)")
        return S.STATE_DETAILED, "high", "status ACTIVE + no phase progress -> 2.planning.detailed"
    return None, "low", f"unrecognized status {status!r} — manual review"


def _file_is_clean(repo_root: Path, path: Path) -> bool:
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        rel = path
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--", str(rel)],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return False
    return out.stdout.strip() == ""


def scan(plans_dir: Path) -> list[Inference]:
    rows: list[Inference] = []
    for ticket, path in C.iter_plan_files(plans_dir):
        fm = FM.read_frontmatter(path)
        if fm.get("plan_status"):
            continue  # already has plan_status
        body = path.read_text(encoding="utf-8")
        ps, conf, reason = infer(fm.get("status"), body)
        rows.append(Inference(
            ticket=ticket, plan_file=str(path.name), current_status=fm.get("status"),
            plan_status=ps, confidence=conf, reason=reason,
        ))
    return rows


def _last_change_ts(repo_root: Path, path: Path) -> float:
    """Epoch seconds of the plan file's last *git commit* date; fall back to mtime
    for untracked / no-history files (Critic: tolerate missing git history)."""
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        rel = path
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%ct", "--", str(rel)],
            capture_output=True, text=True, timeout=10,
        )
        s = out.stdout.strip()
        if s:
            return float(s)
    except Exception:
        pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def files_within(plans_dir: Path, repo_root: Path, since_days: int | None) -> set[Path]:
    """Resolved paths of real plan files changed within `since_days` (FILE-level, not
    ticket-level — collocated KEY-NNN-*.md files are decided independently). since_days
    None -> empty set (scope 'none'). Uses git last-commit date, mtime fallback."""
    if since_days is None:
        return set()
    cutoff = time.time() - since_days * 86400
    out: set[Path] = set()
    for _ticket, path in C.iter_plan_files(plans_dir):
        if _last_change_ts(repo_root, path) >= cutoff:
            out.add(path.resolve())
    return out


def detect_ticket_collisions(plans_dir: Path) -> dict[str, list[str]]:
    """Tickets mapping to >1 real plan file. Reported so file-level scope is auditable."""
    by_ticket: dict[str, list[str]] = {}
    for ticket, path in C.iter_plan_files(plans_dir):
        by_ticket.setdefault(ticket, []).append(path.name)
    return {t: names for t, names in by_ticket.items() if len(names) > 1}


def write_report(repo_root: Path, rows: list[Inference], applied: list[str]) -> Path:
    day = time.strftime("%Y%m%d")
    rpath = repo_root / "docs" / "plans" / "evidence" / f"plan-status-backfill-{day}.md"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Plan-status backfill — {time.strftime('%Y-%m-%d')}", "",
        f"Candidates: {len(rows)} | Applied this run: {len(applied)}", "",
        "| Ticket | current status | inferred plan_status | confidence | applied | reason |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.ticket} | {r.current_status or '-'} | {r.plan_status or '-'} | "
            f"{r.confidence} | {'yes' if r.ticket in applied else 'no'} | {r.reason} |"
        )
    rpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rpath


def backfill(plans_dir: Path, repo_root: Path, project_key: str, *,
             apply_tickets: set[str] | None = None, apply_files: set[Path] | None = None,
             apply_all: bool = False,
             require_clean_tree: bool = False, include_low: bool = False) -> dict:
    rows = scan(plans_dir)
    applied: list[str] = []
    skipped: list[dict] = []

    # apply_files (COM-88) is FILE-level recency scope; apply_tickets is ticket-level;
    # apply_all is the guarded mass path. None of these bypasses the clean-tree gate.
    resolved_files = {p.resolve() for p in apply_files} if apply_files is not None else None
    do_apply = apply_all or bool(apply_tickets) or bool(resolved_files)
    if apply_all and not require_clean_tree:
        return {"error": "apply-all requires --require-clean-tree", "candidates": len(rows)}

    for r in rows:
        if not do_apply:
            continue
        path = plans_dir / r.plan_file
        if apply_tickets is not None and r.ticket not in apply_tickets:
            continue
        if resolved_files is not None and path.resolve() not in resolved_files:
            continue
        if r.plan_status is None or (r.confidence == "low" and not include_low):
            skipped.append({"ticket": r.ticket, "why": f"confidence={r.confidence}"})
            continue
        if not _file_is_clean(repo_root, path):
            skipped.append({"ticket": r.ticket, "why": "working-tree-not-clean"})
            continue
        try:
            FM.update_keys(path, {
                "plan_status": r.plan_status,
                "status": S.derive_merge_status(r.plan_status),
                "plan_status_last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),  # COM-173: ISO-second precision
                "plan_status_actor": "backfill",
            })
            rel = str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else str(path)
            R.set_cache(plans_dir, ticket=r.ticket, plan_status=r.plan_status,
                        plan_file=rel, source="backfill", project_key=project_key)
            applied.append(r.ticket)
        except Exception as exc:
            skipped.append({"ticket": r.ticket, "why": f"error: {exc}"})

    report_path = write_report(repo_root, rows, applied)
    return {
        "candidates": len(rows),
        "by_confidence": {c: sum(1 for r in rows if r.confidence == c) for c in ("high", "medium", "low")},
        "applied": applied, "skipped": skipped,
        "ticket_collisions": detect_ticket_collisions(plans_dir),
        "dry_run": not do_apply, "report": str(report_path),
    }


def _write_normalize_report(repo_root: Path, candidates: list, changed: list,
                            skipped: list, *, applied: bool) -> Path:
    day = time.strftime("%Y%m%d")
    rpath = repo_root / "docs" / "plans" / "evidence" / f"plan-frontmatter-normalize-{day}.md"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    verb = "applied" if applied else "dry-run (would change)"
    lines = [
        f"# COM-138 plan-frontmatter normalize — {time.strftime('%Y-%m-%d')}", "",
        f"Canonical candidates: {len(candidates)} | {verb}: {len(changed)} | skipped: {len(skipped)}",
        "",
        "| Ticket | File | hoisted | fields written | removed |",
        "|---|---|---|---|---|",
    ]
    for c in changed:
        lines.append(
            f"| {c['ticket']} | {c['file']} | {'yes' if c.get('hoisted') else 'no'} | "
            f"{', '.join(c.get('fields') or []) or '-'} | {', '.join(c.get('removed') or []) or '-'} |"
        )
    if skipped:
        lines += ["", "## Skipped", ""]
        for s in skipped:
            lines.append(f"- {s['ticket']} ({s['file']}): {s['why']}")
    rpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rpath


def normalize_backfill(repo_root: Path, *, apply: bool = False,
                       require_clean_tree: bool = True) -> dict:
    """COM-138 one-time normalization of the CANONICAL ``.jswarm/plans`` masters.

    Canonical-only (``iter_canonical_plan_files`` — never legacy ``docs/plans``). Dry-run by
    default; ``apply`` writes via the single normalization invariant (reconcile.normalize_text)
    under the per-file clean-tree gate. Idempotent (re-run = empty change set).
    """
    from jswarm.plan_status import reconcile as RC

    repo_root = Path(repo_root)
    candidates: list[str] = []
    changed: list[dict] = []
    skipped: list[dict] = []
    for ticket, path in C.iter_canonical_plan_files(repo_root):
        candidates.append(ticket)
        try:
            original = path.read_text(encoding="utf-8")
        except OSError as exc:
            skipped.append({"ticket": ticket, "file": path.name, "why": f"unreadable: {exc}"})
            continue
        res = RC.normalize_text(original)
        if not res["changed"]:
            continue
        row = {"ticket": ticket, "file": path.name, "hoisted": res["hoisted"],
               "fields": res["fields_written"], "removed": res["removed"]}
        if apply:
            if require_clean_tree and not _file_is_clean(repo_root, path):
                skipped.append({"ticket": ticket, "file": path.name, "why": "working-tree-not-clean"})
                continue
            path.write_text(res["new_text"], encoding="utf-8")
        changed.append(row)
    report = _write_normalize_report(repo_root, candidates, changed, skipped, applied=apply)
    return {"mode": "normalize", "candidates": len(candidates),
            "changed": changed, "skipped": skipped, "applied": apply, "report": str(report)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Backfill plan_status for in-flight plans")
    p.add_argument("--project-root", default=None)
    p.add_argument("--normalize", action="store_true",
                   help="COM-138: normalize canonical .jswarm plans (dry-run unless --normalize-apply)")
    p.add_argument("--normalize-apply", action="store_true",
                   help="COM-138: apply canonical-plan normalization (per-file clean-tree gated)")
    p.add_argument("--apply", default="", help="Comma-separated tickets to apply")
    p.add_argument("--apply-all", action="store_true")
    p.add_argument("--scope", choices=("none", "10d", "30d", "all"), default="none",
                   help="Recency scope: none=report only; 10d/30d=file-level by last-change "
                        "date; all=mass apply (delegates to --apply-all; needs --require-clean-tree)")
    p.add_argument("--since-days", type=int, default=None,
                   help="Explicit recency window in days (overrides --scope day windows)")
    p.add_argument("--require-clean-tree", action="store_true")
    p.add_argument("--include-low", action="store_true")
    args = p.parse_args(argv)
    cfg = C.resolve_config(Path(args.project_root) if args.project_root else Path.cwd())
    if not cfg.plans_dir:
        print(json.dumps({"error": "config-unresolved"}))
        return 0
    # COM-138 normalize mode is canonical-only and distinct from the plan_status inference.
    if args.normalize or args.normalize_apply:
        out = normalize_backfill(cfg.repo_root, apply=args.normalize_apply)
        print(json.dumps(out, indent=2))
        return 0
    apply_tickets = {t.strip() for t in args.apply.split(",") if t.strip()} or None
    # --scope all routes through the guarded mass-apply path (preserves the
    # require-clean-tree gate); it must NOT be reachable by enumerating every file.
    apply_all = args.apply_all or args.scope == "all"
    since_days = args.since_days
    if since_days is None and args.scope in ("10d", "30d"):
        since_days = 10 if args.scope == "10d" else 30
    apply_files = files_within(cfg.plans_dir, cfg.repo_root, since_days) if since_days is not None else None
    out = backfill(
        cfg.plans_dir, cfg.repo_root, cfg.project_key or "",
        apply_tickets=apply_tickets, apply_files=apply_files, apply_all=apply_all,
        require_clean_tree=args.require_clean_tree, include_low=args.include_low,
    )
    out["scope"] = args.scope
    out["since_days"] = since_days
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
