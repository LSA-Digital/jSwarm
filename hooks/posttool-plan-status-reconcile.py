#!/usr/bin/env python3
# Canonical source: hooks/posttool-plan-status-reconcile.py in this repo.
# Install by copying or symlinking into your project's .claude/hooks/ (or
# globally into ~/.claude/hooks/) and registering it as a PostToolUse(Edit|Write)
# hook; edit the canonical copy, not a deployed one.

# runtime: claude-only
"""PostToolUse(Edit|Write) child hook: reconcile registry + lite-refine.

On an Edit/Write to a plan file (docs/plans/KEY-NNN-*.md):
  1. Reconcile the registry CACHE to the frontmatter plan_status (canonical).
  2. Detect ONLY the 0/1 -> 1.planning.lite_refine auto-transition: when a lite
     plan at 0.planning.lite_init is edited and no active /jPlan intent exists
     for this session (i.e. this is a developer refine, not the initial creation),
     flip frontmatter + registry to 1.planning.lite_refine and emit an event.

Does NOT parse phase markers (Oracle Concern #11). Does NOT guess any other
transition. Dedupes via transition_event_id (in record_transition / set_cache).

CONTRACT: ALWAYS exits 0. The dispatcher blocks on non-zero post-edit child exit
(hook-dispatcher.py dispatch_post_edit), so this hook must fail open.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

def _bootstrap_plan_status_path() -> None:
    """Add the ACTIVE project's repo root (containing jswarm/plan_status) to sys.path.

    The dispatcher runs hooks with cwd = the active project, and this hook may live
    either in <repo>/.claude/hooks/ OR the global ~/.claude/hooks/ when dispatched
    globally. Resolving from the hook's own location (parents[N]) is wrong in the
    global case (it would point at the wrong repo). Resolve from CLAUDE_PROJECT_DIR /
    cwd first, then fall back to the hook's own repo (the in-repo deployment case).

    The repo root (not jswarm/ itself) goes on sys.path because jswarm.plan_status's
    own submodules import each other as ``jswarm.plan_status.<name>`` (absolute,
    matching the rest of this package), so ``jswarm`` itself must be importable.
    """
    bases = []
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        bases.append(Path(project_dir))
    bases.append(Path.cwd())
    bases.append(Path(__file__).resolve())  # hook-location fallback (in-repo)
    for base in bases:
        try:
            cur = base.resolve()
        except Exception:
            continue
        for d in [cur, *cur.parents]:
            if (d / "jswarm" / "plan_status").is_dir():
                repo_root = str(d)
                if repo_root not in sys.path:
                    sys.path.insert(0, repo_root)
                return


_bootstrap_plan_status_path()

INTENT_DIR = Path(os.environ.get("PLAN_STATUS_INTENT_DIR", "/tmp"))
INTENT_TTL_SECONDS = 1800
# Ticketless /jPlan intent only suppresses the immediate initial-creation
# write burst, not every lite edit for the full TTL (Critic M4).
INTENT_TICKETLESS_WINDOW_SECONDS = 120
# In-flight intent values from the compatibility alias. Retain until alias retirement
# and an explicit TTL-drain gate prove no session can still carry these values.
_TRANSITIONAL_INTENT_BASE = "new" + "-work"
_TRANSITIONAL_INTENT_COMPAT = (
    _TRANSITIONAL_INTENT_BASE,
    f"{_TRANSITIONAL_INTENT_BASE}-lite",
)
_LOG = Path("/tmp/plan-status-hook.log")

# Detection widened to canonical .jswarm/plans/ — the load-bearing fix
# (previously this hook matched only legacy docs/plans/, so .jswarm masters got no
# maintenance). A plan file's PARENT dir must be exactly docs/plans or .jswarm/plans;
# artifact subfolders (.jswarm/plans/KEY/...) are excluded by the parent check, and
# filename->ticket is delegated to config.ticket_from_plan_filename so this hook shares
# the canonical regex instead of re-implementing a third divergent one (MINOR-3).
_EXCLUDED_SUBPATHS = ("/docs/plans/evidence/", "docs/plans/evidence/")
_PLAN_PARENTS = ("docs/plans", ".jswarm/plans")


def _debug(msg: str) -> None:
    try:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} reconcile: {msg}\n")
    except Exception:
        pass


def _edited_file(data: dict) -> str | None:
    tool_input = data.get("tool_input") or data.get("toolInput") or {}
    fp = tool_input.get("file_path") or tool_input.get("filePath")
    if isinstance(fp, str) and fp:
        return fp
    return None


def _plan_file_parent_ok(file_path: str) -> bool:
    """True when ``file_path``'s parent dir is exactly docs/plans or .jswarm/plans.

    Cheap path pre-filter before the lazy plan_status import. Excludes evidence subtrees
    and per-ticket artifact subfolders (.jswarm/plans/KEY/...). The precise filename->ticket
    decision is delegated to config.ticket_from_plan_filename after the import.
    """
    norm = file_path.replace("\\", "/")
    if any(ex in norm for ex in _EXCLUDED_SUBPATHS):
        return False
    parent = norm.rsplit("/", 1)[0] if "/" in norm else ""
    return any(parent.endswith(p) for p in _PLAN_PARENTS)


def _active_new_work_intent(session_id: str, ticket: str | None) -> bool:
    """True when a recent /jPlan(-lite) intent exists for this session/ticket."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")
    path = INTENT_DIR / f"plan-status-intent-{safe}.json"
    if not path.exists():
        return False
    try:
        intent = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    age = time.time() - intent.get("ts", 0)
    if age > INTENT_TTL_SECONDS:
        return False
    if intent.get("command") not in ("jplan", "jplan-lite", *_TRANSITIONAL_INTENT_COMPAT):
        return False
    intent_ticket = intent.get("ticket")
    if intent_ticket:
        # Ticket known: suppress only the matching ticket's initial write (M4).
        return intent_ticket == ticket
    # Ticketless intent: suppress only the immediate creation burst, so later
    # edits to any lite plan still flip to 1.lite_refine (Critic M4).
    return age <= INTENT_TICKETLESS_WINDOW_SECONDS


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            return 0

        file_path = _edited_file(data)
        if not file_path:
            return 0
        if not _plan_file_parent_ok(file_path):
            return 0  # Not in a plan directory.

        # Bind-on-plan-edit must run before the heavy reconcile imports
        # below. Editing a master plan is the strongest per-session ticket signal, and
        # the binding write is fail-open so hook execution still always exits 0.
        from jswarm.plan_status import config as C

        # Normalize separators before taking the basename so a Windows-style payload
        # (backslashes) still yields the right filename for ticket extraction (MINOR-1).
        norm_name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
        ticket = C.ticket_from_plan_filename(norm_name)
        if not ticket:
            return 0  # Not a master/legacy plan file (artifact, README, etc.).

        cfg = C.resolve_config(Path.cwd())
        if not cfg.enabled or not cfg.plans_dir:
            return 0  # Advisory-only when config missing.
        if not C.ticket_matches(cfg, ticket):
            return 0

        abs_path = Path(file_path)
        if not abs_path.is_absolute():
            abs_path = (cfg.repo_root / file_path).resolve()
        if not abs_path.exists():
            return 0
        if not abs_path.is_relative_to(cfg.repo_root):
            return 0

        # Refresh an EXISTING same-ticket binding for this session
        # using the PostToolUse PAYLOAD session_id (NOT env), before the heavy reconcile
        # imports so a registry/transition_log import failure can't suppress the
        # keep-alive. This never creates or switches a binding; it only refreshes the
        # edited plan's ticket when this session is already bound to that same ticket. The
        # refresh is an ENHANCEMENT, never a hard prerequisite: it is wrapped so a project
        # that has not yet vendored plan_status.session_binding (or any other error) skips
        # ONLY the refresh and STILL runs the reconcile/normalize below — otherwise
        # deploying this hook globally would break reconcile in any repo lacking
        # session_binding. A non-str payload (JSON null) passes "" so the helper's
        # blank-guard no-ops rather than writing a "None" session dir.
        try:
            from jswarm.plan_status import session_binding
            _sid = data.get("session_id")
            session_binding.refresh_if_bound_to(cfg.repo_root, _sid if isinstance(_sid, str) else "", ticket, "post-edit-hook")
        except Exception as exc:
            _debug(f"bind-on-edit skipped for {ticket}: {exc}")

        # Import lazily so any import error after the binding still fails open.
        from jswarm.plan_status import frontmatter as FM
        from jswarm.plan_status import registry as R
        from jswarm.plan_status import state as S
        from jswarm.plan_status import transition_log as TL

        fm = FM.read_frontmatter(abs_path)
        plan_status = fm.get("plan_status")
        rel_plan = str(abs_path.relative_to(cfg.repo_root)) if abs_path.is_relative_to(cfg.repo_root) else str(abs_path)
        session_id = str(data.get("session_id", "unknown"))

        # Registry-sync branches run only when a VALID plan_status is readable at the top
        # of the file. A mis-positioned plan (block not at line 1) reads as no plan_status
        # here and falls through to normalize_plan_file below, which hoists + derives.
        if isinstance(plan_status, str) and S.is_valid_state(plan_status):
            # 0/1 -> 1.lite_refine auto-transition (the ONLY transition this hook makes).
            if plan_status == S.STATE_LITE_INIT and not _active_new_work_intent(session_id, ticket):
                try:
                    # Single honest 0->1 fact transition. from_status_override asserts the
                    # frontmatter-canonical pre-state so the transition is valid even when
                    # the registry cache lags, without a synthetic seed entry (Critic M3).
                    res = R.record_transition(
                        cfg.plans_dir, ticket=ticket, to_status=S.STATE_LITE_REFINE,
                        actor="post-edit-hook", proof_source="lite-edit",
                        plan_file=rel_plan, project_key=cfg.project_key or "",
                        from_status_override=S.STATE_LITE_INIT,
                    )
                    FM.update_keys(abs_path, {
                        "plan_status": S.STATE_LITE_REFINE,
                        "plan_status_last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),  # ISO-second precision
                        "plan_status_actor": "post-edit-hook",
                    })
                    TL.emit_event(
                        cfg.repo_root, ticket_key=ticket, from_status=S.STATE_LITE_INIT,
                        to_status=S.STATE_LITE_REFINE, actor="post-edit-hook",
                        proof_source="lite-edit",
                        transition_event_id=res.get("transition_event_id", ""),
                        outcome=res.get("action", "applied"),
                    )
                except Exception as exc:
                    _debug(f"lite-refine flip failed for {ticket}: {exc}")
            else:
                # Reconcile cache to frontmatter (canonical wins, no validation).
                try:
                    R.set_cache(
                        cfg.plans_dir, ticket=ticket, plan_status=plan_status,
                        plan_file=rel_plan, source="post-edit-reconcile",
                        project_key=cfg.project_key or "",
                    )
                except Exception as exc:
                    _debug(f"cache sync failed for {ticket}: {exc}")

        # Single normalization invariant — runs once for ANY detected plan file
        # (incl. mis-positioned ones the top-read above could not parse), AFTER both
        # registry branches. Individually wrapped; the hook still ALWAYS exits 0.
        try:
            from jswarm.plan_status import reconcile as RC
            RC.normalize_plan_file(abs_path)
        except Exception as exc:
            _debug(f"normalize failed for {ticket}: {exc}")
        return 0
    except Exception as exc:
        _debug(f"unhandled: {exc}")
        return 0  # Fail open.


if __name__ == "__main__":
    sys.exit(main())
