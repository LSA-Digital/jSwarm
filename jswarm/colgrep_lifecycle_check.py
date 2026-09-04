#!/usr/bin/env python3
"""COM-204 Phase 4 — no-badgering ColGREP lifecycle check.

The certain-only evictor (Phase 3) silently handles stale/orphan cases. This check
computes the remaining AMBIGUOUS set and asks ONE consolidated, actionable question
only when that set is non-empty AND has not already been surfaced for this lifecycle
command (a TTL'd receipt policy keyed by command + candidate-set hash). Used at
/jPlan, /jGo, /jPrecompact, and /jClose so operators are never badgered
about the same unresolved set twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX advisory locks (macOS + Linux) for receipt check-and-set.
except ImportError:  # pragma: no cover - non-POSIX fallback.
    fcntl = None  # type: ignore

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LIFECYCLE_COMMANDS = ("jPlan", "implement", "precompact", "close-ticket")
DEFAULT_RECEIPTS_PATH = Path.home() / "dev" / "colgrep-idx" / "state" / "lifecycle-check-receipts.json"
DEFAULT_TTL_SECONDS = 86400.0
# Actionable options for the single consolidated question.
QUESTION_ACTIONS = ("keep-protect", "delete-now", "defer", "inspect-details")


def candidate_set_hash(names: Any) -> str:
    """Stable, order-independent hash of a set of index names."""
    payload = "\n".join(sorted({str(n) for n in names}))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_identity_hash(candidates: list[dict[str, Any]]) -> str:
    """Receipt identity over the FULL actionable-candidate payload (critic MAJOR-1).

    Hashes index_name + family + normalized reasons + protected + lifecycle_class so a
    candidate whose REASON or protected state changed (same index name) re-asks rather
    than being falsely suppressed. Order-independent (candidates sorted by index_name).
    """
    canonical = sorted(
        ({
            "index_name": c.get("index_name"),
            "family": c.get("family"),
            "reasons": sorted(str(r) for r in (c.get("reasons") or [])),
            "protected": bool(c.get("protected", False)),
            "lifecycle_class": c.get("lifecycle_class"),
        } for c in candidates),
        key=lambda c: str(c.get("index_name")),
    )
    blob = json.dumps({"schema": "com204.lifecycle-check.v1", "candidates": canonical},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class CheckResult:
    command: str
    ambiguous: list[dict[str, Any]]        # ALL ambiguous (actionable + protected holds)
    auto_resolved: list[dict[str, Any]]    # certain Superseded/Orphan omitted (evictor owns)
    question: dict[str, Any] | None
    suppressed: bool
    candidate_hash: str | None = None
    protected_holds: list[dict[str, Any]] = field(default_factory=list)  # non-actionable

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "ambiguous": self.ambiguous,
            "auto_resolved": self.auto_resolved,
            "protected_holds": self.protected_holds,
            "question": self.question,
            "suppressed": self.suppressed,
            "candidate_hash": self.candidate_hash,
        }


def _read_receipts(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing/unreadable receipts store just means "ask".
        return {}
    return data if isinstance(data, dict) else {}


def _write_receipts(path: str | Path, data: dict[str, Any]) -> None:
    """Atomic write (temp file + os.replace) so an interrupted write never truncates
    the receipts store (critic MAJOR-3)."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".lifecycle-receipts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True, indent=2)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _receipt_lock(path: str | Path):
    """Exclusive advisory lock around the receipt read-modify-write so concurrent
    lifecycle commands cannot both emit the same question / lose a receipt (critic
    MAJOR-3). No-op if fcntl is unavailable (non-POSIX)."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_file = p.with_name(p.name + ".lock")
    if fcntl is None:
        yield
        return
    handle = open(lock_file, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def lifecycle_check(
    *,
    report: Any,
    command: str,
    receipts_path: str | Path = DEFAULT_RECEIPTS_PATH,
    now: float,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    session_id: str | None = None,
) -> CheckResult:
    """Partition classifier output into auto-resolved vs ambiguous, then emit at most
    one consolidated question for the ambiguous set, suppressing an unchanged set that
    was already surfaced for this command (no-badgering)."""
    if command not in LIFECYCLE_COMMANDS:
        raise ValueError(f"unknown lifecycle command: {command!r} (expected one of {LIFECYCLE_COMMANDS})")

    ambiguous: list[dict[str, Any]] = []
    auto_resolved: list[dict[str, Any]] = []
    for c in report.classified:
        if getattr(c, "plane", "code") != "code":
            continue
        cls = getattr(c, "lifecycle_class", "")
        if cls == "Ambiguous":
            ambiguous.append({
                "index_name": c.index_name,
                "family": getattr(c, "family", None),
                "reasons": list(getattr(c, "reasons", []) or []),
                "protected": bool(getattr(c, "protected", False)),
                "lifecycle_class": cls,
            })
        elif getattr(c, "evictable", False) and cls in ("Superseded-generation", "Orphan"):
            auto_resolved.append({
                "index_name": c.index_name,
                "family": getattr(c, "family", None),
                "lifecycle_class": cls,
            })

    # MAJOR-2: a protected (base-shaped) Ambiguous entry is a NON-actionable hold — it is
    # never deletable, so it must not appear in an actionable `delete-now` question.
    actionable = [a for a in ambiguous if not a["protected"]]
    protected_holds = [a for a in ambiguous if a["protected"]]

    if not actionable:
        # Nothing actionable to ask about (protected holds are informational only).
        return CheckResult(command=command, ambiguous=ambiguous, auto_resolved=auto_resolved,
                           question=None, suppressed=False, candidate_hash=None,
                           protected_holds=protected_holds)

    # MAJOR-1: receipt identity hashes the full actionable payload (names + reasons +
    # family + protected + class), so a changed decision context re-asks.
    chash = candidate_identity_hash(actionable)
    key = f"{command}:{chash}" + (f":{session_id}" if session_id else "")

    # MAJOR-3: the suppress-decision and the receipt write are one atomic, locked
    # transaction so concurrent lifecycle commands cannot both ask / lose a receipt.
    with _receipt_lock(receipts_path):
        receipts = _read_receipts(receipts_path)
        existing = receipts.get(key)
        if isinstance(existing, dict):
            ts = existing.get("ts")
            # Suppress only for a receipt that is in the PAST and within TTL. A future
            # timestamp (clock skew / tampering) is not trusted -> ask.
            if isinstance(ts, (int, float)) and 0 <= (now - ts) < ttl_seconds:
                return CheckResult(command=command, ambiguous=ambiguous, auto_resolved=auto_resolved,
                                   question=None, suppressed=True, candidate_hash=chash,
                                   protected_holds=protected_holds)
        receipts[key] = {"hash": chash, "command": command, "ts": now}
        _write_receipts(receipts_path, receipts)

    question = {
        "prompt": (f"ColGREP lifecycle: {len(actionable)} ambiguous index candidate(s) need a "
                   f"decision before /{command}. {len(auto_resolved)} certain stale/orphan "
                   f"candidate(s) were omitted (eligible for the certain-only evictor via "
                   f"`cleanup`); {len(protected_holds)} protected base(s) are held, not asked."),
        "candidates": actionable,
        "actions": list(QUESTION_ACTIONS),
        "auto_resolved_count": len(auto_resolved),
        "protected_hold_count": len(protected_holds),
    }
    return CheckResult(command=command, ambiguous=ambiguous, auto_resolved=auto_resolved,
                       question=question, suppressed=False, candidate_hash=chash,
                       protected_holds=protected_holds)
