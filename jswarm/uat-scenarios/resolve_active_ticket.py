#!/usr/bin/env python3
"""Resolve and validate the active in-progress ticket for /uat option 3.

Mid-ticket guided scenario authoring writes ticket-local artifacts under the *active* ticket,
so it must first resolve that ticket safely and refuse to proceed when the answer is ambiguous
or the ticket is not in an editable state. This tool centralizes that gate so the skill (and
its CLI-smoke tests) share one deterministic, fail-loud resolver.

Resolution sources (the skill supplies whichever it found):
- ``--active-ticket`` — the per-session ``active-ticket.json`` binding (authoritative HUD).
- ``--branch`` — ``git branch --show-current`` (ticket key extracted by pattern).
- ``--session-title`` — the session/tmux title (ticket key extracted by pattern).

Rules (every failure exits non-zero and writes nothing — there is no fallback substitution):
- No source yields a key            -> unresolved.
- Sources yield more than one key    -> disagreement (do NOT silently pick one).
- Resolved key has no plan file      -> missing plan.
- Plan is not ``status: ACTIVE`` with a ``2.planning.*`` / ``3.implementation.*`` ``plan_status``
  -> rejected (terminal/closed/unknown state).
- ``--scenarios`` given but absent   -> unresolved scenarios path.

On success it prints a one-line summary (or JSON with ``--json``) describing the resolved
ticket, its plan file, ticket folder, and scenarios path, and exits 0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import cast

TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")
# A plan is editable only in a planning or implementation lifecycle state.
EDITABLE_PLAN_STATUS_RE = re.compile(r"^(2\.planning|3\.implementation)\b")


def _extract_key(value: str | None) -> str | None:
    if not value:
        return None
    match = TICKET_RE.search(value)
    return match.group(0) if match else None


def _read_frontmatter(plan_path: Path) -> dict[str, str]:
    """Minimal, dependency-free read of the leading ``---`` frontmatter scalar lines."""
    text = plan_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    block = text[3 : end if end != -1 else len(text)]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        value = raw.strip().strip('"').strip("'")
        if key.strip() and value:
            fields.setdefault(key.strip(), value)
    return fields


class ResolutionError(ValueError):
    """A fail-loud resolution failure (the caller writes nothing)."""


def resolve(
    plans_dir: Path,
    *,
    active_ticket: str | None,
    branch: str | None,
    session_title: str | None,
    scenarios: Path | None,
) -> dict[str, str]:
    """Return the validated active-ticket resolution, or raise :class:`ResolutionError`."""
    candidates: dict[str, str] = {}
    if active_ticket:
        key = _extract_key(active_ticket)
        if key:
            candidates["active-ticket"] = key
    if branch:
        key = _extract_key(branch)
        if key:
            candidates["branch"] = key
    if session_title:
        key = _extract_key(session_title)
        if key:
            candidates["session-title"] = key

    distinct = set(candidates.values())
    if not distinct:
        raise ResolutionError(
            "no active ticket could be resolved from active-ticket binding, git branch, or session title"
        )
    if len(distinct) > 1:
        detail = ", ".join(f"{source}={key}" for source, key in sorted(candidates.items()))
        raise ResolutionError(
            f"active-ticket sources disagree ({detail}); refusing to pick one — resolve the disagreement first"
        )
    key = distinct.pop()

    plan_files = sorted(plans_dir.glob(f"{key}.plan.*.md"))
    if not plan_files:
        raise ResolutionError(f"no plan file found for {key} under {plans_dir} (expected {key}.plan.*.md)")
    plan_path = plan_files[0]

    front = _read_frontmatter(plan_path)
    status = front.get("status", "")
    plan_status = front.get("plan_status", "")
    if status != "ACTIVE":
        raise ResolutionError(f"ticket {key} is not editable: status={status!r} (expected ACTIVE)")
    if not EDITABLE_PLAN_STATUS_RE.match(plan_status):
        raise ResolutionError(
            f"ticket {key} is not editable: plan_status={plan_status!r} "
            "(expected a 2.planning.* or 3.implementation.* state)"
        )

    if scenarios is not None and not scenarios.is_file():
        raise ResolutionError(f"scenarios JSON path does not exist: {scenarios}")

    return {
        "ticket": key,
        "plan_file": str(plan_path),
        "ticket_dir": str(plans_dir / key),
        "status": status,
        "plan_status": plan_status,
        "scenarios": str(scenarios) if scenarios is not None else "",
        "resolved_from": ",".join(sorted(candidates)),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--plans-dir", required=True, type=Path, help="directory holding <KEY>.plan.*.md masters (.jswarm/plans)")
    _ = parser.add_argument("--active-ticket", help="ticket key from the per-session active-ticket.json binding")
    _ = parser.add_argument("--branch", help="current git branch (ticket key extracted by pattern)")
    _ = parser.add_argument("--session-title", help="session/tmux title (ticket key extracted by pattern)")
    _ = parser.add_argument("--scenarios", type=Path, help="explicit canonical scenarios JSON path (validated to exist)")
    _ = parser.add_argument("--json", action="store_true", help="emit the resolution as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        resolution = resolve(
            cast(Path, args.plans_dir),
            active_ticket=cast("str | None", args.active_ticket),
            branch=cast("str | None", args.branch),
            session_title=cast("str | None", args.session_title),
            scenarios=cast("Path | None", args.scenarios),
        )
    except ResolutionError as error:
        sys.stderr.write(f"ERROR: {error}\n")
        return 1
    except Exception as error:  # pragma: no cover - unexpected IO
        sys.stderr.write(f"ERROR: {error}\n")
        return 1

    if args.json:
        sys.stdout.write(json.dumps(resolution, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(
            f"Resolved active ticket {resolution['ticket']} "
            f"(status={resolution['status']}, plan_status={resolution['plan_status']}, "
            f"from={resolution['resolved_from']}); ticket dir {resolution['ticket_dir']}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
