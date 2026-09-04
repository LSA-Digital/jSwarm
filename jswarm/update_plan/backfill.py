"""Active-plan backfill sweep (AC-6 gate / Oracle F3).

Before the HUD's file/body fallbacks are removed (Phase 5), every **active** plan
must already carry derived ``phase``/``ac_complete`` in its frontmatter — otherwise
the HUD would blank to ``—`` for in-flight tickets that relied on the fallback.

This module discovers active plans, normalizes each (reusing the single
``normalize_plan_file`` engine), and records before/after to a durable evidence
artifact. The decisive Phase-5 gate is ``blocking`` == 0: no in-scope plan where
removing the fallback would *lose* a value the fallback currently shows.

Fail-open: a malformed/unreadable plan is skipped, never aborts the sweep.
"""
from __future__ import annotations

import re
from pathlib import Path

from jswarm.plan_status import config
from jswarm.plan_status import frontmatter as FM
from jswarm.plan_status import reconcile as RC
from jswarm.plan_status import state as ST

ACTIVE_STATUSES = {"ACTIVE", "READY_FOR_MERGE"}

# Mirror the exact fallback sources in the two HUD widgets, so we can tell whether
# removing them would lose information for any active plan.
_AC_FALLBACK_RE = re.compile(r"^\s*-\s*\[([ xX~])\]\s*\*\*AC-", re.MULTILINE)
_PHASE_FALLBACK_RE = re.compile(r"^\s*Phase:\s*(?P<phase>.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _derived_status(path: Path) -> str | None:
    try:
        fm = FM.read_frontmatter(path)
    except Exception:
        return None
    st = fm.get("status")
    if isinstance(st, str) and st.strip():
        return st.strip().upper()
    ps = fm.get("plan_status")
    if isinstance(ps, str) and ps.strip():
        try:
            return ST.derive_merge_status(ps.strip())
        except Exception:
            return None
    return None


def active_plans(repo_root: Path) -> list[Path]:
    """Top-level ``*.plan.*.md`` whose derived status is ACTIVE/READY_FOR_MERGE.

    The non-recursive glob naturally excludes per-ticket subfolders and ``_archive/``.
    """
    plans_dir = repo_root / ".jswarm" / "plans"
    try:
        candidates = sorted(plans_dir.glob("*.plan.*.md"))
    except Exception:
        return []
    return [p for p in candidates if _derived_status(p) in ACTIVE_STATUSES]


def _fallback_phase_value(repo_root: Path, key: str) -> bool:
    """Whether the carryforward fallback (state.md / precompact-state.md) would yield a phase."""
    for path in (
        repo_root / ".jswarm" / "plans" / key / f"{key}.state.md",
        repo_root / ".jswarm" / "state" / "precompact-state.md",
    ):
        try:
            if _PHASE_FALLBACK_RE.search(path.read_text(encoding="utf-8")):
                return True
        except Exception:
            continue
    return False


def run_backfill(repo_root: Path, evidence_path: Path | None = None, today: str | None = None) -> dict:
    repo_root = Path(repo_root)
    rows: list[dict] = []
    normalized_count = 0

    for plan in active_plans(repo_root):
        key = config.ticket_from_plan_filename(plan.name) or plan.name
        try:
            before = plan.read_text(encoding="utf-8")
            RC.normalize_plan_file(plan)
            after = plan.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — fail-open per-plan
            rows.append({
                "key": key, "status": _derived_status(plan), "phase": None,
                "ac_complete": None, "normalized": "ERR", "note": str(exc)[:80],
                "loses_phase": False, "loses_ac": False,
            })
            continue

        if after != before:
            normalized_count += 1

        try:
            fm = FM.read_frontmatter(plan)
        except Exception:
            fm = {}
        has_phase = bool(fm.get("phase"))
        has_ac = bool(fm.get("ac_complete"))

        # "loses info" = the removed fallback currently produces a value the
        # normalized frontmatter does NOT carry. That — not mere absence — is what
        # Oracle F3 forbids before fallback removal.
        fb_ac = bool(_AC_FALLBACK_RE.search(after))
        fb_phase = _fallback_phase_value(repo_root, key)
        loses_phase = (not has_phase) and fb_phase
        loses_ac = (not has_ac) and fb_ac

        missing_fields = [f for f, present in (("phase", has_phase), ("ac", has_ac)) if not present]
        if not missing_fields:
            note = ""
        elif loses_phase or loses_ac:
            note = "missing " + "+".join(missing_fields) + " — FALLBACK-LOSS (blocks Phase 5)"
        else:
            note = "missing " + "+".join(missing_fields) + " (no fallback value — `—` is correct)"

        rows.append({
            "key": key, "status": fm.get("status"), "phase": fm.get("phase"),
            "ac_complete": fm.get("ac_complete"), "normalized": "Y" if after != before else "N",
            "note": note, "loses_phase": loses_phase, "loses_ac": loses_ac,
        })

    missing = [r for r in rows if not r["phase"] or not r["ac_complete"]]
    blocking = [r for r in rows if r["loses_phase"] or r["loses_ac"]]
    result = {
        "active": len(rows),
        "normalized": normalized_count,
        "rows": rows,
        "missing": missing,
        "blocking": blocking,
    }

    if evidence_path is not None:
        _write_evidence(Path(evidence_path), result, today or "")
    return result


def _write_evidence(path: Path, result: dict, today: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = "UNBLOCKED" if not result["blocking"] else "BLOCKED"
    lines = [
        "# Backfill Evidence — Active-Plan Frontmatter Sweep",
        "",
        f"**Generated:** {today or 'n/a'}",
        "**Scope:** `.jswarm/plans/*.plan.*.md` with derived status ∈ {ACTIVE, READY_FOR_MERGE} "
        "(DONE/WONT_DO/DEFERRED and `_archive/` excluded).",
        "",
        "## Summary",
        f"- Active plans in scope: {result['active']}",
        f"- Normalized this run (frontmatter changed): {result['normalized']}",
        f"- Missing phase or ac_complete after normalize: {len(result['missing'])}",
        f"- **Blocking (fallback would lose info): {len(result['blocking'])}**",
        "",
        "## Per-plan",
        "",
        "| Plan | status | phase | ac_complete | normalized | note |",
        "|------|--------|-------|-------------|------------|------|",
    ]
    for r in sorted(result["rows"], key=lambda r: r["key"]):
        lines.append(
            f"| {r['key']} | {r.get('status') or '—'} | {r.get('phase') or '—'} | "
            f"{r.get('ac_complete') or '—'} | {r['normalized']} | {r.get('note') or ''} |"
        )
    lines += [
        "",
        "## Gate",
        "Phase 5 (HUD fallback removal) is UNBLOCKED iff **Blocking == 0** — i.e. no active "
        "plan where the removed file/body fallback currently shows a value the normalized "
        "frontmatter lacks. A plan with no derivable source legitimately shows `—` with or "
        "without the fallback, so it is not blocking.",
        "",
        f"**Result: {verdict}**",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
