#!/usr/bin/env python3
"""A/C 12: plan-status health check. Writes docs/devops-maint/plan-status-maint.{json,md}.

Reports: orphan registry entries, invalid frontmatter values, registry/frontmatter
mismatch (drift), missing-frontmatter plans, schema-version mismatch, and stale Jira
candidates (cached jira_synced_status != intended for the current plan_status).

Follows the menu-22 output-contract precedent (paired JSON + Markdown).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
from jswarm.plan_status import jira_sync as J
from jswarm.plan_status import reconcile as RC
from jswarm.plan_status import registry as R
from jswarm.plan_status import state as S

MAINT_JSON = "docs/devops-maint/plan-status-maint.json"
MAINT_MD = "docs/devops-maint/plan-status-maint.md"

#: derived projections compared by the frontmatter-health pass.
_DERIVED_FIELDS = ("status", "phase", "ac_complete")


def _frontmatter_health(repo_root: Path) -> dict:
    """AC-11: frontmatter-accuracy health over canonical ``.jswarm/plans`` masters.

    Three advisory findings, distinct from ``missing_frontmatter`` (which means "no
    parseable ``plan_status``"):

      * ``frontmatter_position`` — block is not at line 1 (normalize would hoist it);
      * ``derived_field_drift`` — ``status``/``phase``/``ac_complete`` differ from what the
        reconcile normalizer would derive (``[{ticket, fields: [...]}]``);
      * ``malformed_yaml`` — a frontmatter block is present but does not parse.

    Pure PREVIEW via ``reconcile.normalize_text`` — never writes. Mirrors what a menu-25
    ``normalize`` run would self-heal. Per-file failures are swallowed (the doctor is
    advisory and must never raise).
    """
    position: list[str] = []
    drift: list[dict] = []
    malformed: list[str] = []
    for ticket, path in C.iter_canonical_plan_files(repo_root):
        try:
            original = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            hoisted = FM.hoist_to_top(original)
            # A block is present but YAML-invalid -> malformed (distinct from absent).
            if FM.find_block(hoisted) is not None and not FM.read_frontmatter_text(hoisted):
                malformed.append(ticket)
                continue
            res = RC.normalize_text(original)
            if res["hoisted"]:
                position.append(ticket)
            cur = FM.read_frontmatter_text(hoisted)
            norm = FM.read_frontmatter_text(res["new_text"])
            diff = [k for k in _DERIVED_FIELDS if cur.get(k) != norm.get(k)]
            if diff:
                drift.append({"ticket": ticket, "fields": diff})
        except Exception:
            continue  # advisory pass — never raise on a single bad file
    return {
        "frontmatter_position": position,
        "derived_field_drift": drift,
        "malformed_yaml": malformed,
    }


def run_doctor(repo_root: Path, plans_dir: Path, project_key: str) -> dict:
    reg = R.load_registry(plans_dir, project_key=project_key)
    findings = {
        "orphans": [], "invalid_frontmatter": [], "drift": [],
        "missing_frontmatter": [], "schema_mismatch": [], "stale_jira": [],
    }
    seen = set()
    for ticket, path in C.iter_plan_files(plans_dir):
        seen.add(ticket)
        fm = FM.read_frontmatter(path)
        ps = fm.get("plan_status")
        if ps is None:
            findings["missing_frontmatter"].append(ticket)
            continue
        if not isinstance(ps, str) or not S.is_valid_state(ps):
            findings["invalid_frontmatter"].append({"ticket": ticket, "value": ps})
            continue
        cached = reg["tickets"].get(ticket, {}).get("plan_status")
        if cached is not None and cached != ps:
            findings["drift"].append({"ticket": ticket, "registry": cached, "frontmatter": ps})
        # status: should equal derive_merge_status(plan_status)
        want_status = S.derive_merge_status(ps)
        if fm.get("status") and fm.get("status") != want_status:
            findings["drift"].append({
                "ticket": ticket, "status_field": fm.get("status"),
                "expected_status": want_status, "kind": "derived-status-mismatch",
            })

    for ticket, entry in reg["tickets"].items():
        if ticket not in seen:
            findings["orphans"].append(ticket)
        ps = entry.get("plan_status")
        if ps and S.is_valid_state(ps):
            intended = J.intended_jira_transition(ps, project_key or "COM").transition_name
            synced = entry.get("jira_synced_status")
            if intended and synced and synced != intended:
                findings["stale_jira"].append({
                    "ticket": ticket, "synced": synced, "intended": intended,
                })

    if reg.get("schema_version") != R.SCHEMA_VERSION:
        findings["schema_mismatch"].append({
            "registry_version": reg.get("schema_version"), "expected": R.SCHEMA_VERSION,
        })

    # AC-11: frontmatter-accuracy health over the canonical .jswarm/plans masters
    # (position / derived-field drift / malformed YAML). Advisory — folds into issue_count.
    findings.update(_frontmatter_health(repo_root))

    total = sum(len(v) for v in findings.values())
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project_key": project_key,
        "clean": total == 0,
        "issue_count": total,
        "findings": findings,
    }


def _write_reports(repo_root: Path, report: dict) -> tuple[Path, Path]:
    jpath = repo_root / MAINT_JSON
    mpath = repo_root / MAINT_MD
    jpath.parent.mkdir(parents=True, exist_ok=True)
    jpath.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    status_line = "CLEAN" if report["clean"] else f"{report['issue_count']} issue(s)"
    lines = [
        "# Plan-Status Maintenance (Doctor)", "",
        f"**Generated:** {report['generated_at']}",
        f"**Project:** {report['project_key']}",
        f"**Status:** {status_line}",
        "",
    ]
    for kind, items in report["findings"].items():
        if items:
            lines.append(f"## {kind} ({len(items)})")
            for it in items:
                lines.append(f"- {json.dumps(it) if not isinstance(it, str) else it}")
            lines.append("")
    if report["clean"]:
        lines.append("No plan-status issues detected.")
    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Plan-status health check")
    p.add_argument("--project-root", default=None)
    p.add_argument("--write", action="store_true", help="Write docs/devops-maint/plan-status-maint.{json,md}")
    args = p.parse_args(argv)
    cfg = C.resolve_config(Path(args.project_root) if args.project_root else Path.cwd())
    if not cfg.plans_dir:
        print(json.dumps({"error": "config-unresolved"}))
        return 0
    report = run_doctor(cfg.repo_root, cfg.plans_dir, cfg.project_key or "")
    if args.write:
        jpath, mpath = _write_reports(cfg.repo_root, report)
        report["_written"] = [str(jpath), str(mpath)]
    print(json.dumps(report, indent=2))
    return 0 if report["clean"] else 0  # report-only; never non-zero (advisory)


if __name__ == "__main__":
    sys.exit(main())
