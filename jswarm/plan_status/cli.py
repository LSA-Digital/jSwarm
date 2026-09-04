#!/usr/bin/env python3
"""COM-84 plan-status CLI — the interface lifecycle commands invoke after proof.

Slash commands (/jPlan, /jGo, /jClose, /jMerge) call this to record
a FACT transition once the command has proven it (plan written, phase started, A/C
met, merge green). It updates the canonical plan-file frontmatter AND the registry
cache AND emits an NDJSON event. Jira sync is split by criticality:
  - planning transitions: the CALLING AGENT pushes the (fire-and-forget) Jira
    transition via the Atlassian MCP; this CLI only reports the intended transition.
  - closeout transitions (6.closed.merged + terminals): pass --sync-jira to delegate
    to the retry-safe jswarm/jira_mcp_closeout.py helper.

Advisory-only: if project config is unresolved, prints a notice and exits 0 without
writing (so the command flow never breaks on a misconfigured project).

Subcommands:
  record <ticket> <to_status>   record a fact transition (after proof)
  set    <ticket> <to_status>   manual override (--reason required)
  show   [<ticket>]             print current registry state (JSON)
  derive <plan_status>          print the derived merge status:
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from plan_status import config as C
from plan_status import frontmatter as FM
from plan_status import jira_sync as J
from plan_status import registry as R
from plan_status import session_binding
from plan_status import state as S
from plan_status import transition_log as TL


def _resolve(args) -> "C.ProjectConfig":
    root = Path(args.project_root) if getattr(args, "project_root", None) else Path.cwd()
    return C.resolve_config(root)


def _emit(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False))


class PlanFileAmbiguousError(Exception):
    """A ticket resolves to >1 master plan; refuse to silently mutate the wrong one."""

    def __init__(self, ticket: str, candidates: list[str]):
        self.ticket = ticket
        self.candidates = candidates
        super().__init__(
            f"{ticket}: {len(candidates)} master plans match ({', '.join(candidates)}); "
            f"pass --plan-file to disambiguate"
        )


def _find_plan_path(cfg, ticket: str, plan_file: str) -> Path | None:
    """Resolve the plan file path WITHOUT writing it (Critic atomicity fix).

    Canonical-first (COM-138 BLOCK-3 / AC-10): with no explicit ``--plan-file``, prefer the
    canonical ``.jswarm/plans/<ticket>.plan.*.md`` master, then legacy ``docs/plans/<ticket>-*.md``.
    A ticket with >1 canonical (or >1 legacy when no canonical) raises PlanFileAmbiguousError
    rather than silently picking — a real case exists (COM-109 has two canonical plans).
    """
    if plan_file:
        path = Path(plan_file)
        if not path.is_absolute():
            path = (cfg.repo_root / plan_file).resolve()
        return path if path.exists() else None

    canon = sorted(p for t, p in C.iter_canonical_plan_files(cfg.repo_root) if t == ticket)
    if len(canon) == 1:
        return canon[0]
    if len(canon) > 1:
        raise PlanFileAmbiguousError(ticket, [p.name for p in canon])

    legacy: list[Path] = []
    if cfg.plans_dir:
        legacy = sorted(p for p in cfg.plans_dir.glob(f"{ticket}-*.md") if p.exists())
    if len(legacy) == 1:
        return legacy[0]
    if len(legacy) > 1:
        raise PlanFileAmbiguousError(ticket, [p.name for p in legacy])
    return None


def _rel(cfg, path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path.relative_to(cfg.repo_root)) if path.is_relative_to(cfg.repo_root) else str(path)


def _write_frontmatter(path: Path | None, to_status: str, actor: str) -> None:
    """Update plan-file frontmatter canonically. ONLY call AFTER the registry
    transition has validated, so an illegal transition never regresses the file.

    Routes through the COM-138 single normalization invariant after the transition
    write so ``status``/``phase``/``ac_complete`` are derived + the block is canonicalized.
    """
    if path is None or not path.exists():
        return
    try:
        FM.update_keys(path, {
            "plan_status": to_status,
            "status": S.derive_merge_status(to_status),
            # COM-173: ISO-second precision so same-day transitions order deterministically
            # by time (the HUD resolver ranks ACTIVE plans by this stamp; a date-only stamp
            # collapses to midnight and forces an unreliable mtime tiebreak).
            "plan_status_last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "plan_status_actor": actor,
        })
    except FM.FrontmatterError:
        pass
    try:
        from plan_status import reconcile as RC
        RC.normalize_plan_file(path)
    except Exception:
        pass  # normalization is best-effort; the transition write already landed.


def _write_session_binding(repo_root: Path, ticket: str) -> None:
    """COM-174: record `session → ticket` for the live terminal session so the
    ccstatusline HUD shows the ticket THIS session is working (per-session), rather
    than the global-freshest ACTIVE plan. Keyed by CLAUDE_CODE_SESSION_ID, which the
    Claude Code statusline payload also carries as `session_id`. Fail-open and a
    no-op outside a Claude Code session (cron/CI), where the env var is unset."""
    session_binding.write(
        repo_root,
        os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip(),
        ticket,
        "plan-status-record",
    )


def cmd_record(args) -> int:
    cfg = _resolve(args)
    if not cfg.enabled:
        _emit({"action": "advisory-skip", "reason": "config-unresolved", "project": cfg.project_id})
        return 0
    if not C.ticket_matches(cfg, args.ticket):
        _emit({"action": "advisory-skip", "reason": "ticket-not-for-project",
               "ticket": args.ticket, "project_key": cfg.project_key})
        return 0
    if not S.is_valid_state(args.to_status):
        _emit({"action": "error", "reason": "invalid-state", "to": args.to_status})
        return 1

    # COM-174 (+ /jPrecompact follow-up): bind THIS session to the ticket as soon as a
    # lifecycle command touches it — BEFORE transition validation — so the HUD resolves
    # the session's own ticket even when the transition is rejected/idempotent (e.g. a
    # registry that lags the plan after a tooling outage). The binding is a per-session
    # "working on X" fact, independent of the transition outcome.
    # COM-174 Phase 6: only auto-bind the live terminal's HUD for active-work
    # (3.implementation.*) transitions. Creation/planning-seed states (0/1/2.*) are
    # frequently recorded by a /jPlan planner SUBAGENT that inherits the parent's
    # CLAUDE_CODE_SESSION_ID — auto-binding there hijacks the parent's HUD. Closeout
    # states keep the binding already written at 3.* (bindings are never deleted).
    # Explicit `cmd_bind` (/jPrecompact) is unaffected and still binds at any state.
    if S.binds_session_hud(args.to_status):
        _write_session_binding(cfg.repo_root, args.ticket)

    # Resolve the plan file but DO NOT write it yet — the registry must validate the
    # transition first, otherwise an illegal transition regresses frontmatter (Critic B).
    try:
        path = _find_plan_path(cfg, args.ticket, args.plan_file or "")
    except PlanFileAmbiguousError as exc:
        _emit({"action": "error", "reason": "ambiguous-plan-file", "ticket": exc.ticket,
               "candidates": exc.candidates, "hint": "pass --plan-file to disambiguate"})
        return 1
    rel_plan = _rel(cfg, path)

    try:
        res = R.record_transition(
            cfg.plans_dir, ticket=args.ticket, to_status=args.to_status,
            actor=args.actor, proof_source=args.proof_source,
            plan_file=rel_plan or (args.plan_file or ""),
            from_status_override=args.from_status or None,
            manual=False, project_key=cfg.project_key or "",
        )
    except S.InvalidTransitionError as exc:
        # No frontmatter mutation occurred — file is left at its prior state.
        _emit({"action": "error", "reason": "invalid-transition", "detail": str(exc)})
        return 1

    # Validation passed: now write canonical frontmatter.
    _write_frontmatter(path, args.to_status, args.actor)

    TL.emit_event(
        cfg.repo_root, ticket_key=args.ticket, from_status=res["from"],
        to_status=res["to"], actor=args.actor, proof_source=args.proof_source,
        transition_event_id=res.get("transition_event_id", ""),
        outcome=res.get("action", "applied"),
    )

    intent = J.intended_jira_transition(args.to_status, cfg.project_key or "COM")
    jira_result = None
    if args.sync_jira and intent.criticality == "retry-safe":
        jira_result = J.sync_closeout(cfg.repo_root, ticket=args.ticket, plan_status=args.to_status)

    _emit({
        **res,
        "ticket": args.ticket,
        "plan_file": rel_plan,
        "jira_transition": intent.transition_name,
        "jira_criticality": intent.criticality,
        "jira_sync": jira_result,
    })
    return 0


def cmd_bind(args) -> int:
    """Write the per-session HUD binding WITHOUT recording a transition. Used by
    /jPrecompact (and any checkpoint that knows the active ticket but is NOT advancing
    plan_status) so the ccstatusline HUD resolves the session's own ticket at every
    checkpoint. Registry-independent + fail-open; a no-op when CLAUDE_CODE_SESSION_ID
    is unset (cron/CI) or the ticket is not for this project."""
    cfg = _resolve(args)
    if not cfg.enabled:
        _emit({"action": "advisory-skip", "reason": "config-unresolved", "project": cfg.project_id})
        return 0
    if not C.ticket_matches(cfg, args.ticket):
        _emit({"action": "advisory-skip", "reason": "ticket-not-for-project",
               "ticket": args.ticket, "project_key": cfg.project_key})
        return 0
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    _write_session_binding(cfg.repo_root, args.ticket)
    _emit({"action": "bound" if sid else "advisory-skip-no-session",
           "ticket": args.ticket, "session_id": sid or None})
    return 0


def cmd_set(args) -> int:
    cfg = _resolve(args)
    if not cfg.enabled:
        _emit({"action": "advisory-skip", "reason": "config-unresolved"})
        return 0
    if not args.reason:
        _emit({"action": "error", "reason": "manual-override-requires-reason"})
        return 1
    if not S.is_valid_state(args.to_status):
        _emit({"action": "error", "reason": "invalid-state", "to": args.to_status})
        return 1
    try:
        path = _find_plan_path(cfg, args.ticket, args.plan_file or "")
    except PlanFileAmbiguousError as exc:
        _emit({"action": "error", "reason": "ambiguous-plan-file", "ticket": exc.ticket,
               "candidates": exc.candidates, "hint": "pass --plan-file to disambiguate"})
        return 1
    rel_plan = _rel(cfg, path)
    try:
        res = R.record_transition(
            cfg.plans_dir, ticket=args.ticket, to_status=args.to_status,
            actor="manual", proof_source="cli-set", plan_file=rel_plan or "",
            manual=True, reason=args.reason, project_key=cfg.project_key or "",
        )
    except S.InvalidTransitionError as exc:
        _emit({"action": "error", "reason": "invalid-transition", "detail": str(exc)})
        return 1
    _write_frontmatter(path, args.to_status, "manual")
    TL.emit_event(
        cfg.repo_root, ticket_key=args.ticket, from_status=res["from"],
        to_status=res["to"], actor="manual", proof_source="cli-set",
        transition_event_id=res.get("transition_event_id", ""),
        outcome=res.get("action", "applied"),
    )
    _emit({**res, "ticket": args.ticket, "reason": args.reason})
    return 0


def cmd_show(args) -> int:
    cfg = _resolve(args)
    if not cfg.plans_dir:
        _emit({"action": "advisory-skip", "reason": "config-unresolved"})
        return 0
    data = R.load_registry(cfg.plans_dir, project_key=cfg.project_key or "")
    if args.ticket:
        _emit(data["tickets"].get(args.ticket, {}))
    else:
        _emit(data)
    return 0


def cmd_derive(args) -> int:
    if not S.is_valid_state(args.plan_status):
        _emit({"action": "error", "reason": "invalid-state"})
        return 1
    print(S.derive_merge_status(args.plan_status))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="COM-84 plan-status CLI")
    p.add_argument("--project-root", default=None, help="Project root (default: cwd)")
    sub = p.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="Record a fact transition after proof")
    rec.add_argument("ticket")
    rec.add_argument("to_status")
    rec.add_argument("--actor", required=True)
    rec.add_argument("--proof-source", required=True, dest="proof_source")
    rec.add_argument("--plan-file", default=None, dest="plan_file")
    rec.add_argument("--from", default=None, dest="from_status",
                     help="Assert canonical pre-state (honored only if it matches real state)")
    rec.add_argument("--sync-jira", action="store_true",
                     help="For closeout states, fire the retry-safe Jira Done transition")
    rec.set_defaults(func=cmd_record)

    st = sub.add_parser("set", help="Manual override (requires --reason)")
    st.add_argument("ticket")
    st.add_argument("to_status")
    st.add_argument("--reason", required=True)
    st.add_argument("--plan-file", default=None, dest="plan_file")
    st.set_defaults(func=cmd_set)

    sh = sub.add_parser("show", help="Print registry state")
    sh.add_argument("ticket", nargs="?", default=None)
    sh.set_defaults(func=cmd_show)

    dv = sub.add_parser("derive", help="Print derived merge status")
    dv.add_argument("plan_status")
    dv.set_defaults(func=cmd_derive)

    bd = sub.add_parser("bind", help="Write the per-session HUD binding (no transition)")
    bd.add_argument("ticket")
    bd.set_defaults(func=cmd_bind)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
