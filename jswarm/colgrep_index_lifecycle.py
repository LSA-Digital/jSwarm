#!/usr/bin/env python3
"""ColGREP :3280 code-search index lifecycle classifier + plane/base guard.

Phase 1 surface: classify every loaded code-search (:3280) index into exactly one
lifecycle class — ``Active``, ``Superseded-generation``, ``Orphan``, or
``Ambiguous`` — with machine-readable reasons and evidence, while keeping plane and
base protections fail-closed:

  * Only the ``:3280`` code-search plane is ever a mutation target. ``:3281``
    content-plane indices are enumerated ONLY as protected exclusions and are never
    given a lifecycle class or proposed for deletion.
  * Base indices (active-project bases, served base aliases, active-base
    generations, and any non-active bare-name base) are never eviction candidates.
  * Unreadable active-projects policy disables ALL eviction (every candidate becomes
    protected/Ambiguous) — fail closed, never fail open.

``evictable`` on a classification means "delete-ELIGIBLE candidate" (correct class +
worktree-generation/overlay shape + certain). It is the classifier's *proposal*. The
actual deletion still routes through the guarded chokepoint
``jswarm.colgrep_index_delete_guard.guarded_delete_next_plaid_index`` (active-base
refusal + ``expected_shape`` hold), and orphan/superseded eviction additionally
requires the evictor's stop-then-verify-no-live-owner step
(``stop_then_guarded_delete_orphan_family``) before any DELETE — implemented in the
Phase 3 evictor. The classifier never deletes.

This module mirrors the existing generation-family naming rules
(``colgrep_com171_modes`` / ``colgrep_worktree``) and REUSES the delete-guard base
protection so "protected" can never drift from the path that actually deletes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# Run both as `python -m jswarm.colgrep_index_lifecycle` AND as a direct script
# (`python jswarm/colgrep_index_lifecycle.py ...`, the documented UAT command); the
# latter needs the repo root on sys.path so the `scripts` package import resolves.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Reuse the guarded-delete base protection so "protected" stays consistent with the
# chokepoint that performs deletions.
from jswarm.colgrep_index_delete_guard import (  # noqa: E402
    ActiveProjectsUnavailable,
    DEFAULT_ACTIVE_PROJECTS_PATH,
    DEFAULT_BASE_ALIASES_PATH,
    active_project_base_names,
    is_active_project_base_index,
)

CODE_PLANE_URL_DEFAULT = "http://localhost:3280"
CONTENT_PLANE_URL_DEFAULT = "http://localhost:3281"
DEFAULT_REGISTRY_PATH = Path.home() / "dev" / "colgrep-idx" / "worktrees.json"

LIFECYCLE_CLASSES = ("Active", "Superseded-generation", "Orphan", "Ambiguous")
DELETE_SHAPE = "worktree-generation-or-overlay"
LIFECYCLE_ROLE = "colgrep-index-lifecycle"

# Canonical generation name (mirrors colgrep_com171_modes / colgrep_worktree):
#   {family}-{a|full}-g{NNNNNN}-{8hex}[-overlay]
# Mode-A carries the ``-overlay`` suffix; full-index does not.
_CANONICAL_GEN_RE = re.compile(
    r"^(?P<family>.+-wt-.+?)-(?P<mode>a|full)-g(?P<gen>\d{6})-(?P<hex>[0-9a-f]{8})(?P<overlay>-overlay)?$"
)


def _is_worktree_or_overlay_name(name: str) -> bool:
    """Mirror of colgrep_index_delete_guard._is_worktree_or_overlay_name."""
    return "-wt-" in name or name.endswith("-overlay")


# A worktree family is `{project}-wt-{ticket}[-wt-...]`. Each `-wt-`-delimited segment
# must be a safe slug — no path separators, no `..`, no whitespace/control chars — so a
# malformed/traversal name (e.g. `../evil-wt-x`) can NEVER parse as a canonical
# generation and therefore can never reach a guarded DELETE (Phase-3 BLOCKER-4).
_SAFE_FAMILY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")


def _is_safe_family(family: str | None) -> bool:
    if not family or "-wt-" not in family:
        return False
    if "/" in family or "\\" in family or ".." in family:
        return False
    if any(ch.isspace() or ord(ch) < 32 for ch in family):
        return False
    return all(_SAFE_FAMILY_SEGMENT_RE.match(seg) for seg in family.split("-wt-"))


@dataclass
class ParsedName:
    family: str
    mode: str            # "a" | "full"
    generation: int
    hex: str
    overlay: bool


def _parse_generation(name: str) -> ParsedName | None:
    """Parse a canonical worktree generation name, or None if not confidently parseable.

    Enforces mode/suffix COHERENCE (BLOCKER-1): mode-A names MUST carry the
    ``-overlay`` suffix and full-index names MUST NOT. A name that violates this contract
    (e.g. ``...-a-g000001-deadbeef`` without ``-overlay``, or ``...-full-...-overlay``) is
    malformed and returns None — so it can never become a certain eviction candidate.
    """
    m = _CANONICAL_GEN_RE.match(name)
    if not m:
        return None
    mode = m.group("mode")
    overlay = bool(m.group("overlay"))
    if (mode == "a") != overlay:  # mode-A <-> overlay must agree; full-index <-> no overlay
        return None
    if not _is_safe_family(m.group("family")):  # reject path-traversal / unsafe families
        return None
    return ParsedName(
        family=m.group("family"),
        mode=mode,
        generation=int(m.group("gen"), 10),
        hex=m.group("hex"),
        overlay=overlay,
    )


def _family_key(name: str) -> str | None:
    """Family key for any worktree-shaped name (canonical generation OR logical index).

    Returns None for unsafe families (path-traversal / malformed) so such names can
    never become an eviction family key (Phase-3 BLOCKER-4).
    """
    parsed = _parse_generation(name)
    if parsed is not None:
        return parsed.family
    if "-wt-" in name:
        family = name[: -len("-overlay")] if name.endswith("-overlay") else name
        return family if _is_safe_family(family) else None
    return None


def _base_project_of_family(family: str) -> str:
    """The base project slug a worktree family belongs to (the part before ``-wt-``).

    Naive prefix only; used for human-readable messaging. Ownership/protection
    decisions MUST use ``_owning_active_base`` so a base name that itself contains
    ``-wt-`` cannot mis-compute an active generation into an orphan (MAJOR-3).
    """
    return family.split("-wt-", 1)[0]


def _owning_active_base(family: str, active_names: set[str]) -> str | None:
    """Return the LONGEST active base name that owns ``family`` — i.e. ``family == N``
    or ``family.startswith(N + "-wt-")`` for an active base name ``N`` — or None.

    Longest-match is robust to base names that contain ``-wt-``: e.g. with active base
    ``a-wt-x``, family ``a-wt-x-wt-ticket`` is owned by ``a-wt-x`` (not mis-attributed to
    ``a`` and orphaned). Falls back to None only when no active base genuinely owns the
    family, which is the true-orphan precondition.
    """
    best: str | None = None
    for name in active_names:
        if family == name or family.startswith(name + "-wt-"):
            if best is None or len(name) > len(best):
                best = name
    return best


def _registry_row_families(row: dict[str, Any]) -> set[str]:
    """All distinct trustworthy family keys a registry row resolves to.

    The live registry has both the normal ``project:TICKET`` logical form and two writer
    variants: ``parent-worktree:TICKET`` (where the path basename is the child family),
    and ``project:full-worktree-family`` (sometimes accompanied by literal ``family``).
    ``api_index_name``, literal ``family``, a full-family ticket half, and a parseable
    ``worktree_path`` basename are independent identity sources and therefore contribute
    to the disagreement set.  The colon project's worktree-shaped half is only a
    cross-check that suppresses unsafe prefix minting: treating it as an identity source
    would fabricate a child family for the parent-worktree writer variant.

    A well-formed row resolves to exactly one family; an empty set (no family key) or a
    set of size > 1 (trustworthy sources disagree) is malformed and makes the registry
    untrusted (MAJOR-R3 malformation classes).
    """
    families: set[str] = set()

    def add_family(value: str) -> None:
        family = _family_key(value)
        if family:
            families.add(family)

    add_family(str(row.get("api_index_name") or "").strip())
    add_family(str(row.get("family") or "").strip())
    add_family(Path(str(row.get("worktree_path") or "").strip()).name)

    logical_raw = str(row.get("logical_worktree_index") or "").strip()
    if not logical_raw:
        return families
    if ":" not in logical_raw:
        add_family(logical_raw)
        return families

    project, _, ticket = logical_raw.partition(":")
    project = project.strip()
    ticket = ticket.strip()
    ticket_family = _family_key(ticket)
    if ticket_family:
        families.add(ticket_family)
    elif not _family_key(project):
        # Preserve the established plain-project colon normalization byte-for-byte.
        add_family(f"{project}-wt-{ticket}".lower())
    return families


@dataclass(frozen=True)
class BuildStateObservation:
    build_state: str
    reason: str
    lease_generation: int | None = None
    deadline_at: str | None = None
    parkable: bool = False


@dataclass
class IndexClassification:
    index_name: str
    plane: str                      # "code" | "content"
    lifecycle_class: str            # one of LIFECYCLE_CLASSES (code plane); "Protected" for exclusions
    certainty: str                  # "certain" | "ambiguous"
    protected: bool
    evictable: bool
    policy_source: str
    reasons: list[str] = field(default_factory=list)
    family: str | None = None
    generation: int | None = None
    delete_shape: str | None = None
    # Evictor handoff + trust evidence (populated for Phase 3's guarded-delete /
    # stop_then_guarded_delete_orphan_family handoff; schema-stable from Phase 1).
    worktree_path: str | None = None          # set when a trusted registry row exists
    worktree_path_hint: str | None = None     # resolver-filled (Phase 2/3); never guessed by the evictor
    registry_trusted: bool = True
    registry_error: str | None = None
    owner_state: str = "unknown"              # unknown | owner-probe-deferred | no-owner-probed | owner-present
    owner_pids: list[int] = field(default_factory=list)
    build_observation: BuildStateObservation | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegistryTrust:
    """Whether the worktree registry can be trusted as evidence for orphan/live decisions.

    A legitimately ABSENT registry (no worktrees) is trusted (orphans are possible). A
    present-but-unreadable/malformed registry is NOT trusted — its absence of a row is
    not evidence of "no worktree", so registry-dependent evictions are held Ambiguous.
    """

    trusted: bool
    present: bool
    error: str | None = None


@dataclass
class ClassificationReport:
    classified: list[IndexClassification]            # code plane, exactly one per :3280 index
    protected_exclusions: list[IndexClassification]  # content plane (:3281)
    input_anomalies: dict[str, int] = field(default_factory=lambda: {"blank_dropped": 0, "duplicates_collapsed": 0})

    def by_class(self) -> dict[str, list[IndexClassification]]:
        out: dict[str, list[IndexClassification]] = defaultdict(list)
        for c in self.classified:
            out[c.lifecycle_class].append(c)
        return dict(out)

    def evictable_candidates(self) -> list[IndexClassification]:
        return [c for c in self.classified if c.evictable]

    def summary(self) -> dict[str, Any]:
        counts = Counter(c.lifecycle_class for c in self.classified)
        return {
            "total_code_indices": len(self.classified),
            "content_plane_excluded": len(self.protected_exclusions),
            "counts_by_class": dict(counts),
            "protected": sum(1 for c in self.classified if c.protected),
            "evictable": sum(1 for c in self.classified if c.evictable),
            "ambiguous": counts.get("Ambiguous", 0),
            "input_anomalies": dict(self.input_anomalies),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "classified": [c.to_dict() for c in self.classified],
            "protected_exclusions": [c.to_dict() for c in self.protected_exclusions],
        }


# --- P0-7: accepted-generation-aware cleanup refusal guard -------------------
#
# A generation with ``normal_results_allowed: True`` is proven queryable/serving and
# must never be classified ``Orphan`` or recommended for cleanup by its family, even
# when the registry row for that family is stale/failed (registry status lags manifest
# provenance — see ``colgrep_worktree.classify_status_accepted_generation``). This is a
# dedicated, standalone guard (not routed through ``_classify_one``'s registry-trust
# machinery) so callers can ask "is this family's accepted generation protected?"
# without requiring a full classification pass.
def classify_accepted_generation_family(
    *,
    family: str,
    loaded_generations: list[str],
    registry_path: "str | Path",
    accepted_generation: str,
    normal_results_allowed: bool,
) -> dict[str, Any]:
    """Refuse orphan/cleanup classification for a family whose accepted generation serves.

    Returns a dict with ``lifecycle_class`` (never ``"Orphan"`` when
    ``normal_results_allowed`` is True and ``accepted_generation`` is a loaded member of
    the family), ``cleanup_recommended`` (``False`` in that case), and a
    ``refusal_reason`` naming why cleanup is forbidden.
    """
    del registry_path  # accepted (`normal_results_allowed`) provenance already wins over registry state
    protected = bool(normal_results_allowed) and accepted_generation in (loaded_generations or [])
    if protected:
        return {
            "family": family,
            "lifecycle_class": "Active",
            "certainty": "certain",
            "protected": True,
            "evictable": False,
            "cleanup_recommended": False,
            "cleanup_candidates": [],
            "refusal_reason": "accepted-generation-normal-results-allowed",
            "accepted_generation": accepted_generation,
        }
    return {
        "family": family,
        "lifecycle_class": "Ambiguous",
        "certainty": "ambiguous",
        "protected": False,
        "evictable": False,
        "cleanup_recommended": False,
        "cleanup_candidates": [],
        "refusal_reason": "accepted-generation-not-loaded-or-not-normal-results-allowed",
        "accepted_generation": accepted_generation,
    }


BUILD_STATES = (
    "building-healthy",
    "awaiting-reload",
    "registered-unbuilt",
    "stuck-indexing",
    "crashlooping",
    "queued-needs-builder",
)
_BUILDABLE_STATUSES = {"registered", "queued", "unbuilt", "full-index-failure", "failed", "terminal-degraded"}
_CRASHLOOP_FAILURE_THRESHOLD = 3
_CRASHLOOP_WINDOW = timedelta(minutes=10)
_BUILD_HEARTBEAT_WINDOW = timedelta(seconds=60)


def _parse_observation_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_generation(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value, 10)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _active_build_lease(heartbeat: dict[str, Any] | None, *, now: datetime) -> bool:
    if not isinstance(heartbeat, dict):
        return False
    generation = _as_generation(heartbeat.get("lease_generation"))
    token = heartbeat.get("lease_token")
    if generation is None or not isinstance(token, str) or not token:
        return False
    last = _parse_observation_time(heartbeat.get("last_event_at"))
    if last is None:
        return False
    age = now.astimezone(timezone.utc) - last
    return timedelta(0) <= age <= _BUILD_HEARTBEAT_WINDOW


def _deadline_from_manifest(manifest: dict[str, Any] | None) -> str | None:
    if not isinstance(manifest, dict):
        return None
    for key in ("worker_deadline_at", "deadline_at", "build_deadline_at"):
        value = manifest.get(key)
        if isinstance(value, str) and value:
            return value
    worker = manifest.get("worker")
    if isinstance(worker, dict):
        value = worker.get("deadline_at")
        if isinstance(value, str) and value:
            return value
    return None


def _manifest_awaiting_reload(manifest: dict[str, Any] | None) -> bool:
    if not isinstance(manifest, dict):
        return False
    if bool(manifest.get("candidate_awaiting_reload")):
        return True
    return str(manifest.get("refresh_status") or manifest.get("state") or "") == "full-index-awaiting-reload"


def _restart_event_time(event: object) -> datetime | None:
    if not isinstance(event, dict):
        return None
    for key in ("at", "failed_at", "timestamp", "created_at", "updated_at"):
        parsed = _parse_observation_time(event.get(key))
        if parsed is not None:
            return parsed
    return None


def _is_restart_failure(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    values = {str(value) for value in event.values() if isinstance(value, str)}
    return "watcher-supervision-failed" in values


def observe_build_state(
    *,
    entry: dict[str, Any] | None,
    heartbeat: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    physical_index_exists: bool,
    restart_history: Iterable[dict[str, Any]],
    now: datetime,
    builder_live: bool = False,
) -> BuildStateObservation | None:
    """Purely derive build state signals without changing lifecycle decisions."""
    if entry is None and not heartbeat and not manifest and not restart_history:
        return None
    now = now.astimezone(timezone.utc) if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    deadline_at = _deadline_from_manifest(manifest)
    deadline = _parse_observation_time(deadline_at)
    lease_generation = _as_generation((heartbeat or {}).get("lease_generation"))
    status = str((entry or {}).get("status") or "").strip().lower()
    lease_active = _active_build_lease(heartbeat, now=now)

    recent_failures = 0
    for event in restart_history:
        if not _is_restart_failure(event):
            continue
        when = _restart_event_time(event)
        if when is None or timedelta(0) <= now - when <= _CRASHLOOP_WINDOW:
            recent_failures += 1
    if recent_failures >= _CRASHLOOP_FAILURE_THRESHOLD:
        return BuildStateObservation(
            build_state="crashlooping",
            reason=f"{recent_failures} watcher-supervision-failed events within restart window",
            lease_generation=lease_generation,
            deadline_at=deadline_at,
            parkable=False,
        )

    if _manifest_awaiting_reload(manifest):
        return BuildStateObservation(
            build_state="awaiting-reload",
            reason="candidate build is awaiting next-plaid reload",
            lease_generation=lease_generation,
            deadline_at=deadline_at,
            parkable=False,
        )

    # A fresh heartbeat is watcher supervision evidence, not proof that a builder
    # remains alive.  Building health requires the fleet's exact owned-worker
    # predicate as well as the advisory lease freshness.
    if builder_live and lease_active and (deadline is None or now <= deadline):
        return BuildStateObservation(
            build_state="building-healthy",
            reason="live builder has a fresh owned-worker lease heartbeat before deadline",
            lease_generation=lease_generation,
            deadline_at=deadline_at,
            parkable=False,
        )

    if status == "indexing" or (deadline is not None and now > deadline):
        return BuildStateObservation(
            build_state="stuck-indexing",
            reason="indexing status has stale or expired build lease/deadline",
            lease_generation=lease_generation,
            deadline_at=deadline_at,
            parkable=False,
        )

    if entry is not None and not physical_index_exists:
        buildable = status in _BUILDABLE_STATUSES or not status
        if buildable and not builder_live:
            return BuildStateObservation(
                build_state="queued-needs-builder",
                reason="registered buildable index has no physical index and no live builder",
                lease_generation=lease_generation,
                deadline_at=deadline_at,
                parkable=False,
            )
        return BuildStateObservation(
            build_state="registered-unbuilt",
            reason="registered index has no physical index and no active build lease",
            lease_generation=lease_generation,
            deadline_at=deadline_at,
            parkable=bool(builder_live),
        )

    return None



def _load_registry(registry_path: str | Path) -> tuple[dict[str, dict[str, Any]], RegistryTrust]:
    """Load worktrees.json into a (family_key -> entry) map plus a trust verdict.

    Fail-closed distinction: a missing file is trusted-absent (no worktrees -> orphans
    possible); a present-but-unreadable/malformed file is UNTRUSTED (callers must hold
    registry-dependent decisions Ambiguous rather than treat the empty map as evidence).
    """
    path = Path(registry_path).expanduser()
    if not path.exists():
        return {}, RegistryTrust(trusted=True, present=False)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - an unreadable registry is NOT evidence of "no worktrees".
        return {}, RegistryTrust(trusted=False, present=True, error=f"worktrees.json unreadable: {exc}")
    # --- Registry-shape trust contract (fail-closed; MAJOR-1/R1/R2) ----------
    # A PRESENT registry is trusted ONLY if it has a single, unambiguous, well-formed
    # shape. Anything non-canonical is untrusted (NOT trusted-empty, which would orphan
    # /evict live generations). Trusted shapes:
    #   * top-level LIST (declared container), or
    #   * top-level OBJECT declaring EXACTLY ONE of {worktrees, entries} as a LIST.
    # Untrusted: non-object/array; object with no container; object with BOTH containers
    # (ambiguous/conflicting); null/non-list container; any malformed row; duplicate family.
    if isinstance(payload, dict):
        present = [k for k in ("worktrees", "entries") if k in payload]
        if not present:
            return {}, RegistryTrust(trusted=False, present=True,
                                     error="worktrees.json object declares no worktrees/entries rows container")
        if len(present) > 1:
            return {}, RegistryTrust(trusted=False, present=True,
                                     error=f"worktrees.json declares conflicting rows containers: {present}")
        rows = payload[present[0]]
    elif isinstance(payload, list):
        rows = payload
    else:
        return {}, RegistryTrust(trusted=False, present=True, error="worktrees.json is not an object or array")
    if not isinstance(rows, list):
        return {}, RegistryTrust(trusted=False, present=True,
                                 error=f"worktrees.json rows container is not a list (got {type(rows).__name__})")
    # Per-row structural validation: a non-dict row, a row with no resolvable family key,
    # a row missing a non-empty worktree_path, or two rows resolving to the SAME family is
    # uncertainty, not evidence of "no worktree" -> the whole present registry is untrusted.
    out: dict[str, dict[str, Any]] = {}
    malformed: list[str] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            malformed.append(f"row[{idx}] is not an object")
            continue
        families = _registry_row_families(row)
        if not families:
            malformed.append(f"row[{idx}] has no resolvable family key")
            continue
        if len(families) > 1:
            malformed.append(f"row[{idx}] conflicting families {sorted(families)}")
            continue
        family = families.pop()
        if not str(row.get("worktree_path") or "").strip():
            malformed.append(f"row[{idx}] ({family}) missing worktree_path")
            continue
        if family in out:
            existing_path = str(out[family].get("worktree_path") or "").strip()
            row_path = str(row.get("worktree_path") or "").strip()
            existing_api = str(out[family].get("api_index_name") or "").strip()
            row_api = str(row.get("api_index_name") or "").strip()
            if existing_path == row_path and existing_api == row_api:
                # Multiple live writers can record equivalent aliases for the same
                # family/path/API identity; neither row supplies a competing current
                # generation or ownership identity.
                continue
            malformed.append(f"row[{idx}] duplicate family key {family}")
            continue
        out[family] = row
    if malformed:
        return {}, RegistryTrust(
            trusted=False, present=True,
            error="worktrees.json has malformed rows: " + "; ".join(malformed[:3]),
        )
    return out, RegistryTrust(trusted=True, present=True)


def _retained_generations(generations: list[int], cap: int) -> set[int]:
    """The top ``cap`` distinct generation numbers (retained as current)."""
    if cap < 1:
        cap = 1
    return set(sorted(set(generations), reverse=True)[:cap])


def _default_current_generation(family: str, entry: dict[str, Any]) -> str | None:
    """Best-effort current-generation pointer for a live family from the registry row.

    Returns a generation index NAME only if the registry exposes one that parses as a
    canonical generation of this family. The live `worktrees.json` `api_index_name` is
    usually the logical worktree BASE (e.g. ``hai-sim-engine-wt-has-497``), which does
    NOT parse as a generation -> returns None, so the classifier holds the family
    Ambiguous rather than guess (BLOCKER-2). The Phase 3 evictor supplies a
    manifest-backed resolver to recover certain supersession.
    """
    for key in ("api_index_name", "current_generation", "active_generation", "served_index"):
        value = str(entry.get(key) or "").strip()
        if not value:
            continue
        parsed = _parse_generation(value)
        if parsed is not None and parsed.family == family:
            return value
    return None


def classify(
    *,
    code_indices: Iterable[str],
    content_indices: Iterable[str] = (),
    active_projects_path: str | Path = DEFAULT_ACTIVE_PROJECTS_PATH,
    base_aliases_path: str | Path = DEFAULT_BASE_ALIASES_PATH,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    worktree_exists: Callable[[str], bool] | None = None,
    current_generation_resolver: Callable[[str, dict[str, Any]], str | None] | None = None,
    max_live_generations: int = 1,
    policy_resolver: Callable[[str], Any] | None = None,
    now: datetime | None = None,
    build_observation_resolver: Callable[[str, dict[str, Any] | None], BuildStateObservation | None] | None = None,
) -> ClassificationReport:
    """Classify every code-search index into exactly one lifecycle class (fail-closed).

    ``current_generation_resolver(family, registry_entry) -> generation_name | None`` is
    the authoritative pointer to a live family's current generation. The default reads it
    from the registry row only when it parses as a canonical generation; the Phase 3
    evictor injects a manifest-backed resolver. When no trustworthy pointer is
    available, multi-generation live families are held Ambiguous (never guessed).

    ``policy_resolver(family) -> ColgrepPolicy | None`` is the layered policy
    seam: the per-family ``max_live_generations`` cap is resolved from the owning
    project's policy rather than from a single global scalar. When the resolver is
    absent (or returns no policy for a family), the global ``max_live_generations``
    default applies — preserving the Phase-1 behavior. A resolved cap < 1 is malformed
    and holds that family Ambiguous (fail-closed).
    """

    raw = [str(n) for n in code_indices]
    nonblank = [n.strip() for n in raw if n.strip()]
    code_names = sorted(set(nonblank))
    content_set = {str(n).strip() for n in content_indices if str(n).strip()}
    input_anomalies = {
        "blank_dropped": len(raw) - len(nonblank),
        "duplicates_collapsed": len(nonblank) - len(code_names),
    }
    worktree_exists = worktree_exists or (lambda p: bool(p) and Path(p).expanduser().is_dir())
    current_generation_resolver = current_generation_resolver or _default_current_generation

    # 1. Trust the active-projects policy? If not, fail closed for everything.
    policy_ok = True
    active_names: set[str] = set()
    try:
        active_names = active_project_base_names(active_projects_path)
    except ActiveProjectsUnavailable:
        policy_ok = False

    # 2. Live worktree registry (family -> row) + trust verdict (fail-closed).
    registry, registry_trust = _load_registry(registry_path)

    # 3. Group worktree-generation members by family (for current-vs-superseded ranking).
    family_members: dict[str, list[str]] = defaultdict(list)
    parsed_cache: dict[str, ParsedName | None] = {}
    for name in code_names:
        parsed = _parse_generation(name)
        parsed_cache[name] = parsed
        if parsed is not None:
            family_members[parsed.family].append(name)

    classified = []
    for name in code_names:
        classification = _classify_one(
            name,
            parsed=parsed_cache[name],
            family_members=family_members,
            registry=registry,
            registry_trust=registry_trust,
            active_names=active_names,
            policy_ok=policy_ok,
            active_projects_path=active_projects_path,
            base_aliases_path=base_aliases_path,
            worktree_exists=worktree_exists,
            current_generation_resolver=current_generation_resolver,
            max_live_generations=max_live_generations,
            policy_resolver=policy_resolver,
            content_set=content_set,
        )
        if build_observation_resolver is not None:
            family = classification.family or _family_key(name) or name
            classification.build_observation = build_observation_resolver(name, registry.get(family))
        classified.append(classification)

    protected_exclusions = [
        IndexClassification(
            index_name=name,
            plane="content",
            lifecycle_class="Protected",
            certainty="certain",
            protected=True,
            evictable=False,
            policy_source="content-plane-3281",
            reasons=["content-plane (:3281) index — out of scope for lifecycle classification or eviction"],
        )
        for name in sorted(content_set)
    ]

    return ClassificationReport(
        classified=classified,
        protected_exclusions=protected_exclusions,
        input_anomalies=input_anomalies,
    )


def _classify_one(
    name: str,
    *,
    parsed: ParsedName | None,
    family_members: dict[str, list[str]],
    registry: dict[str, dict[str, Any]],
    registry_trust: RegistryTrust,
    active_names: set[str],
    policy_ok: bool,
    active_projects_path: str | Path,
    base_aliases_path: str | Path,
    worktree_exists: Callable[[str], bool],
    current_generation_resolver: Callable[[str, dict[str, Any]], str | None],
    max_live_generations: int,
    policy_resolver: Callable[[str], Any] | None,
    content_set: set[str],
) -> IndexClassification:
    is_wt_overlay = _is_worktree_or_overlay_name(name)

    def make(lifecycle_class, *, certainty, protected, evictable, policy_source, reasons,
             family=None, generation=None, delete_shape=None,
             worktree_path=None, worktree_path_hint=None, owner_state="unknown"):
        return IndexClassification(
            index_name=name, plane="code", lifecycle_class=lifecycle_class, certainty=certainty,
            protected=protected, evictable=evictable, policy_source=policy_source, reasons=reasons,
            family=family, generation=generation, delete_shape=delete_shape,
            worktree_path=worktree_path, worktree_path_hint=worktree_path_hint,
            registry_trusted=registry_trust.trusted, registry_error=registry_trust.error,
            owner_state=owner_state,
        )

    # Defensive: a name that also appears on the content plane is protected, never evicted.
    if name in content_set:
        return make("Ambiguous", certainty="ambiguous", protected=True, evictable=False,
                    policy_source="content-plane-3281",
                    reasons=["name also present on content plane (:3281) — protected, never evicted"])

    # Fail-closed: unreadable active-projects policy disables ALL eviction.
    if not policy_ok:
        if is_wt_overlay:
            return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                        policy_source="unreadable-policy",
                        reasons=["active-projects policy unreadable — cannot prove live/orphan; held fail-closed"],
                        family=_family_key(name))
        return make("Ambiguous", certainty="ambiguous", protected=True, evictable=False,
                    policy_source="unreadable-policy",
                    reasons=["active-projects policy unreadable — base-shaped name protected, fail-closed"])

    # Base / served-alias / active-base-generation protection (reuses the delete guard).
    if is_active_project_base_index(name, active_projects_path=active_projects_path,
                                    base_aliases_path=base_aliases_path):
        return make("Active", certainty="certain", protected=True, evictable=False,
                    policy_source="active-projects",
                    reasons=["active-project base / served alias / active-base generation — protected, undeletable"])

    # Bare-name base that is NOT an active base (e.g. epms, em-lab) -> protected Ambiguous.
    if not is_wt_overlay:
        return make("Ambiguous", certainty="ambiguous", protected=True, evictable=False,
                    policy_source="name-shape",
                    reasons=["non-active / unconfigured base index — protected, never auto-deleted by lifecycle"])

    # Worktree / overlay generation name.
    #
    # Fail-closed: an UNTRUSTED (present-but-unreadable) registry is not evidence of
    # "no worktree row", so we cannot prove live-vs-orphan OR supersession. Hold every
    # worktree/overlay candidate Ambiguous rather than manufacture orphan/superseded
    # eviction from a corrupt registry.
    if not registry_trust.trusted:
        return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                    policy_source="registry",
                    reasons=["worktree registry (worktrees.json) is present but unreadable/untrusted — "
                             f"cannot prove live/orphan or supersession; held fail-closed ({registry_trust.error})"],
                    family=_family_key(name))

    # Fail-closed on invalid policy: a cap < 1 is malformed -> no generation-family
    # decision can be certain, so hold all worktree/overlay names Ambiguous (MAJOR-2).
    if max_live_generations < 1:
        return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                    policy_source="policy",
                    reasons=[f"invalid max_live_generations={max_live_generations} (must be >= 1) — "
                             "held fail-closed, no eviction"],
                    family=_family_key(name))

    if parsed is None:
        return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                    policy_source="name-shape",
                    reasons=["worktree/overlay name does not match a canonical generation shape — held, not auto-deleted"],
                    family=_family_key(name))

    family = parsed.family
    base_project = _base_project_of_family(family)
    # Robust active-base ownership (critic MAJOR-3): the owning base is the LONGEST
    # active base name N with family == N or family.startswith(N + "-wt-"), not a naive
    # split on the first "-wt-". This is correct even if a base name itself contains
    # "-wt-", so an active-project generation is never mis-computed into an orphan.
    owning_active_base = _owning_active_base(family, active_names)

    registry_entry = registry.get(family)
    wt_path = str(registry_entry.get("worktree_path") or "").strip() if registry_entry else ""
    dir_exists = bool(registry_entry) and bool(wt_path) and worktree_exists(wt_path)
    live_registered = bool(registry_entry) and dir_exists

    if live_registered:
        member_gens = {n: pg.generation for n in family_members[family]
                       if (pg := _parse_generation(n)) is not None}
        loaded_max = max(member_gens.values()) if member_gens else parsed.generation

        # Authoritative current-generation pointer. Supersession is only ever CERTAIN
        # when the pointer (a) resolves, (b) parses as a generation of this family,
        # (c) is actually loaded, and (d) is the highest loaded generation. If the
        # registry/API disagree, certainty is gone -> hold the WHOLE family Ambiguous
        # (BLOCKER-2). The default resolver returns None when api_index_name is
        # the worktree base, which is the common live case.
        current_name = current_generation_resolver(family, registry_entry)
        current_parsed = _parse_generation(current_name) if current_name else None
        valid_current = (
            current_name is not None
            and current_parsed is not None
            and current_parsed.family == family
            and current_name in member_gens
            and current_parsed.generation == loaded_max
        )
        if not valid_current:
            return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                        policy_source="registry",
                        reasons=[f"live worktree {family}: no trustworthy current-generation pointer "
                                 f"(resolved={current_name!r}, loaded_max=g{loaded_max:06d}) — held fail-closed; "
                                 "certain supersession requires the manifest resolver (Phase 3)"],
                        family=family, generation=parsed.generation,
                        worktree_path=wt_path or None, owner_state="owner-present")

        # layered policy seam (critic MAJOR-4): resolve the per-family cap ONLY
        # here — strictly downstream of the valid_current gate, and never for malformed
        # names, untrusted registry, orphan candidates, or families lacking a trustworthy
        # current pointer. Absent resolver/policy -> global default (Phase-1 behavior); a
        # resolved cap < 1 is malformed -> fail-closed Ambiguous.
        effective_cap = max_live_generations
        if policy_resolver is not None:
            resolved_policy = policy_resolver(family)
            resolved_cap = getattr(resolved_policy, "max_live_generations", None)
            if isinstance(resolved_cap, int) and not isinstance(resolved_cap, bool):
                effective_cap = resolved_cap
        if effective_cap < 1:
            return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                        policy_source="policy",
                        reasons=[f"resolved max_live_generations={effective_cap} for family {family} "
                                 "is invalid (must be >= 1) — held fail-closed, no eviction"],
                        family=family, generation=parsed.generation,
                        worktree_path=wt_path or None, owner_state="owner-present")

        current_gen = current_parsed.generation
        retained = _retained_generations(list(member_gens.values()), effective_cap)
        if name == current_name:
            return make("Active", certainty="certain", protected=True, evictable=False,
                        policy_source="registry",
                        reasons=[f"current served generation (g{current_gen:06d}) of live worktree {family}"],
                        family=family, generation=parsed.generation,
                        worktree_path=wt_path or None, owner_state="owner-present")
        if parsed.generation == current_gen:
            # Same generation as current, different physical member: a RAM-duplicate of the
            # current generation. Held (no Phase 1 duplicate-eviction policy; never auto-deleted).
            return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                        policy_source="registry",
                        reasons=[f"duplicate of current generation g{current_gen:06d} for live worktree {family} "
                                 "(same generation, different physical index) — held, not auto-evicted in Phase 1"],
                        family=family, generation=parsed.generation, worktree_path=wt_path or None)
        if parsed.generation in retained:
            # An older-but-retained generation (cap >= 2): kept, never evicted.
            return make("Active", certainty="certain", protected=True, evictable=False,
                        policy_source="registry",
                        reasons=[f"retained generation g{parsed.generation:06d} within max_live_generations="
                                 f"{effective_cap} for live worktree {family}"],
                        family=family, generation=parsed.generation,
                        worktree_path=wt_path or None, owner_state="owner-present")
        return make("Superseded-generation", certainty="certain", protected=False, evictable=True,
                    policy_source="registry",
                    reasons=[f"superseded generation g{parsed.generation:06d} below retained cap "
                             f"max_live_generations={effective_cap} for live worktree {family} "
                             f"(current=g{current_gen:06d}); evictor must verify no live owner before delete"],
                    family=family, generation=parsed.generation, delete_shape=DELETE_SHAPE,
                    worktree_path=wt_path or None, owner_state="owner-probe-deferred")

    if registry_entry and not dir_exists:
        return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                    policy_source="registry",
                    reasons=[f"registry row present for {family} but worktree dir missing — held (registry/dir mismatch)"],
                    family=family, generation=parsed.generation, worktree_path=wt_path or None)

    # No registry row. If an ACTIVE base owns this family, this may be a new/active
    # worktree pending registration -> Ambiguous (fleet repair decides, NOT orphan cleanup).
    if owning_active_base is not None:
        return make("Ambiguous", certainty="ambiguous", protected=False, evictable=False,
                    policy_source="active-projects",
                    reasons=[f"unregistered worktree generation owned by ACTIVE base '{owning_active_base}' — "
                             "may be a new/active worktree; held for fleet repair, not orphan cleanup"],
                    family=family, generation=parsed.generation)

    # No registry row, registry trusted, base project not active -> true orphan candidate.
    # worktree_path_hint is left for the Phase 2/3 fleet resolver; owner liveness is
    # probed by the evictor (stop_then_guarded_delete_orphan_family) before any delete.
    return make("Orphan", certainty="certain", protected=False, evictable=True,
                policy_source="registry",
                reasons=[f"no worktree registry row for family {family} (base project '{base_project}' not active) — "
                         "orphan candidate; evictor must stop owners + verify no live PID before delete"],
                family=family, generation=parsed.generation, delete_shape=DELETE_SHAPE,
                owner_state="unknown")


# --------------------------------------------------------------------------- #
# Enumeration (code plane only; content plane read-only for exclusion evidence)
# --------------------------------------------------------------------------- #
def _read_indices_http(api_url: str, *, timeout: float = 25.0) -> list[str]:
    """Best-effort GET {api_url}/indices -> list[str]. Tolerant of list/dict payloads."""
    import urllib.request

    url = f"{api_url.rstrip('/')}/indices"
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - localhost next-plaid.
        payload = json.loads(resp.read().decode("utf-8", errors="replace") or "[]")
    if isinstance(payload, list):
        return [str(x.get("name") if isinstance(x, dict) else x) for x in payload]
    if isinstance(payload, dict):
        for key in ("indices", "indexes", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [str(x.get("name") if isinstance(x, dict) else x) for x in value]
        return [str(k) for k in payload.keys()]
    return []


def enumerate_and_classify(
    *,
    code_api_url: str = CODE_PLANE_URL_DEFAULT,
    content_api_url: str = CONTENT_PLANE_URL_DEFAULT,
    list_code: Callable[[str], list[str]] | None = None,
    list_content: Callable[[str], list[str]] | None = None,
    enumerate_content: bool = True,
    **classify_kwargs: Any,
) -> ClassificationReport:
    """Read :3280 (and read-only :3281) and classify. Content fetch is best-effort."""
    list_code = list_code or _read_indices_http
    code = list_code(code_api_url)
    content: list[str] = []
    if enumerate_content:
        try:
            lister = list_content or _read_indices_http
            content = lister(content_api_url)
        except Exception:  # noqa: BLE001 - content plane is read-only evidence; never block on it.
            content = []
    return classify(code_indices=code, content_indices=content, **classify_kwargs)


# --------------------------------------------------------------------------- #
# ColGREP is an optional component (installer: --with-colgrep). The evictor
# (colgrep_index_evictor) is enterprise-only and is not part of this repo, so
# `check`/`cleanup` must fail OPEN on a missing import -- a clear, structured
# result on the documented contract, never an ImportError traceback -- since
# core-loop skills (jClose, jGo, jPlan, jPrecompact) invoke `check --json`
# verbatim with no error handling of their own.
# --------------------------------------------------------------------------- #
_COLGREP_UNAVAILABLE_DETAIL = (
    "ColGREP component is not installed in this checkout (it is optional; "
    "the installer's --with-colgrep flag enables index-lifecycle checks). "
    "Continuing without a lifecycle question."
)


def _colgrep_component_import_error() -> ImportError | None:
    """Import the ColGREP evictor; return the exception instead of raising it.

    ``None`` means the import succeeded (nothing to report)."""
    try:
        from jswarm import colgrep_index_evictor as _evictor  # noqa: F401
    except ImportError as exc:
        return exc
    return None


def _colgrep_unavailable_check_result(command: str) -> dict[str, Any]:
    """The `check --json` contract's shape (see CheckResult.to_dict in
    colgrep_lifecycle_check.py), degraded honestly: nothing ambiguous because
    nothing was checked, not because it was found clean."""
    return {
        "command": command,
        "ambiguous": [],
        "auto_resolved": [],
        "protected_holds": [],
        "question": None,
        "suppressed": True,
        "candidate_hash": None,
        "colgrep_available": False,
        "detail": _COLGREP_UNAVAILABLE_DETAIL,
    }


# --------------------------------------------------------------------------- #
# CLI: classify --dry-run --json  (read-only; never deletes)
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ColGREP :3280 index lifecycle classifier (read-only).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_classify = sub.add_parser("classify", help="Classify loaded :3280 indices.")
    p_classify.add_argument("--code-api-url", default=CODE_PLANE_URL_DEFAULT)
    p_classify.add_argument("--content-api-url", default=CONTENT_PLANE_URL_DEFAULT)
    p_classify.add_argument("--active-projects-path", default=str(DEFAULT_ACTIVE_PROJECTS_PATH))
    p_classify.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    p_classify.add_argument("--max-live-generations", type=int, default=1)
    p_classify.add_argument("--no-content", action="store_true", help="Skip :3281 enumeration.")
    p_classify.add_argument("--dry-run", action="store_true",
                            help="No-op: classify is always read-only (never deletes). Accepted for operator/UAT consistency.")
    p_classify.add_argument("--json", action="store_true", help="Emit full JSON report.")

    # Phase 2: fleet coverage planner (read-only; proposes repair, never delete).
    p_coverage = sub.add_parser("coverage",
                                help="Plan active-worktree index coverage from active-project policy.")
    p_coverage.add_argument("--active-projects", default=str(DEFAULT_ACTIVE_PROJECTS_PATH),
                            help="active-projects.yaml with optional per-project colgrep: policy blocks.")
    p_coverage.add_argument("--candidates", required=True,
                            help="JSON file: list of {path, family?, has_loaded_index?, has_registry_row?}.")
    p_coverage.add_argument("--json", action="store_true")
    p_coverage.add_argument("--dry-run", action="store_true",
                            help="No-op: coverage planning never mutates. Accepted for operator/UAT consistency.")

    # Phase 4: no-badgering lifecycle check for the four lifecycle commands.
    from jswarm.colgrep_lifecycle_check import LIFECYCLE_COMMANDS as _LIFECYCLE_COMMANDS
    p_check = sub.add_parser("check",
                             help="No-badgering lifecycle check: one consolidated question only for genuinely ambiguous candidates.")
    # NB: dest must NOT be "command" — that is the subparsers dest (the subcommand name).
    p_check.add_argument("--command", dest="lifecycle_command", required=True, choices=_LIFECYCLE_COMMANDS,
                         help="The lifecycle command invoking the check.")
    p_check.add_argument("--json", action="store_true")
    p_check.add_argument("--active-projects-path", default=str(DEFAULT_ACTIVE_PROJECTS_PATH))
    p_check.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    p_check.add_argument("--code-api-url", default=CODE_PLANE_URL_DEFAULT)
    p_check.add_argument("--receipts-path", default=None,
                         help="No-badgering receipts store (defaults to the ColGREP state tree).")
    p_check.add_argument("--session-id", default=None)

    # Phase 3: guarded cleanup (dry-run by default; deletes only via evictor chokepoints).
    p_cleanup = sub.add_parser("cleanup", help="Classify then guarded-evict safe ColGREP lifecycle candidates.")
    # MINOR-1: dry-run and apply are mutually exclusive; dry-run is the default.
    cleanup_mode = p_cleanup.add_mutually_exclusive_group()
    cleanup_mode.add_argument("--dry-run", dest="apply", action="store_false",
                              help="Plan cleanup without deleting (default).")
    cleanup_mode.add_argument("--apply", dest="apply", action="store_true",
                              help="Perform guarded deletes for eligible candidates against --code-api-url.")
    p_cleanup.set_defaults(apply=False)
    p_cleanup.add_argument("--json", action="store_true", help="Emit JSON summary (default; kept for CLI consistency).")
    p_cleanup.add_argument("--active-projects-path", default=str(DEFAULT_ACTIVE_PROJECTS_PATH))
    p_cleanup.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    p_cleanup.add_argument("--code-api-url", default=CODE_PLANE_URL_DEFAULT,
                           help="Code plane used for BOTH classification AND deletes (default :3280).")
    p_cleanup.add_argument("--worktree-root", dest="worktree_roots", action="append", default=[],
                           help="Operator-authorized worktree root to prove orphan absence (repeatable).")
    p_cleanup.add_argument("--audit-log-path", default=None,
                           help="Durable JSONL audit path (defaults to the ColGREP evictor audit log).")

    args = parser.parse_args(argv)
    # A/C 13: record mutating/advisory lifecycle command invocations in
    # the in-repo activity log (fail-open; writes to the repo log file, never to
    # stdout — the JSON contract is preserved). `classify` is a diagnosis-time
    # read-only probe and must not write receipts or activity.
    if args.command != "classify":
        try:
            from jswarm import colgrep_activity_log as _activity

            _activity.log_activity("lifecycle", str(getattr(args, "command", "?")),
                                   now=datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001 - activity logging never blocks the CLI.
            pass
    if args.command == "classify":
        report = enumerate_and_classify(
            code_api_url=args.code_api_url,
            content_api_url=args.content_api_url,
            enumerate_content=not args.no_content,
            active_projects_path=args.active_projects_path,
            registry_path=args.registry_path,
            max_live_generations=args.max_live_generations,
        )
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            print(json.dumps(report.summary(), indent=2, sort_keys=True))
        return 0
    if args.command == "coverage":
        # Lazy import keeps classify() import-light and avoids any import cycle.
        from jswarm import colgrep_index_policy as _policy

        try:
            raw = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
            candidates = _policy._candidates_from_json(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            # Fail closed + loud: malformed candidate JSON must never be coerced into a
            # false-green report. Clean message on stderr; stdout stays empty (contract).
            print(f"coverage: invalid candidates JSON ({args.candidates}): {exc}", file=sys.stderr)
            return 2
        report = _policy.coverage_report(active_projects_path=args.active_projects, candidates=candidates)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if args.command == "check":
        import_error = _colgrep_component_import_error()
        if import_error is not None:
            print(json.dumps(_colgrep_unavailable_check_result(args.lifecycle_command), indent=2, sort_keys=True))
            return 0
        from jswarm import colgrep_index_evictor as _evictor
        from jswarm import colgrep_lifecycle_check as _check

        try:
            report = enumerate_and_classify(
                code_api_url=args.code_api_url,
                enumerate_content=True,
                active_projects_path=args.active_projects_path,
                registry_path=args.registry_path,
                current_generation_resolver=_evictor.make_current_generation_resolver(),
            )
            result = _check.lifecycle_check(
                report=report, command=args.lifecycle_command,
                receipts_path=args.receipts_path or _check.DEFAULT_RECEIPTS_PATH,
                now=datetime.now(timezone.utc).timestamp(),
                session_id=args.session_id,
            )
        except Exception as exc:  # noqa: BLE001 - fail loud + clean; stdout stays empty.
            print(f"check: failed ({type(exc).__name__}): {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "cleanup":
        import_error = _colgrep_component_import_error()
        if import_error is not None:
            print(f"cleanup: {_COLGREP_UNAVAILABLE_DETAIL}", file=sys.stderr)
            return 2
        # Lazy import keeps classify() import-light and avoids any import cycle.
        from jswarm import colgrep_index_evictor as _evictor

        dry_run = not args.apply
        import time as _time

        def _probe_indices(url):
            """Bounded read-only GET /indices -> {indices_count, fetch_ms, error}."""
            t0 = _time.perf_counter()
            try:
                names = _read_indices_http(url)
                return {"indices_count": len(names), "fetch_ms": round((_time.perf_counter() - t0) * 1000, 2), "error": None}
            except Exception as exc:  # noqa: BLE001 - responsiveness probe never blocks; record the error.
                return {"indices_count": None, "fetch_ms": round((_time.perf_counter() - t0) * 1000, 2), "error": f"{type(exc).__name__}: {exc}"}

        started = _time.perf_counter()
        try:
            fetch_started = _time.perf_counter()
            report = enumerate_and_classify(
                code_api_url=args.code_api_url,
                enumerate_content=True,
                active_projects_path=args.active_projects_path,
                registry_path=args.registry_path,
                current_generation_resolver=_evictor.make_current_generation_resolver(),
            )
            fetch_ms = round((_time.perf_counter() - fetch_started) * 1000, 2)
            api_before = {"indices_count": len(report.classified), "fetch_ms": fetch_ms, "error": None}
            actions = _evictor.run_eviction(
                report=report,
                registry_path=args.registry_path,
                active_projects_path=args.active_projects_path,
                current_generation_resolver=_evictor.make_current_generation_resolver(),
                # BLOCKER-3: deletes target the SAME endpoint used for classification.
                code_api_url=args.code_api_url,
                worktree_roots=args.worktree_roots,
                dry_run=dry_run,
                # MAJOR-1: a real apply always leaves a durable audit row.
                log_path=args.audit_log_path or _evictor.DEFAULT_AUDIT_LOG,
                now=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 - fail loud + clean; stdout stays empty.
            print(f"cleanup: failed ({type(exc).__name__}): {exc}", file=sys.stderr)
            return 2
        # A/C 7: bounded before/after :3280 responsiveness around the (possibly mutating) run.
        api_after = _probe_indices(args.code_api_url)
        api_meta = {
            "before": api_before,
            "after": api_after,
            "content_plane_excluded": len(report.protected_exclusions),
        }
        summary = _evictor.cleanup_observability_summary(
            actions, report, dry_run=dry_run, code_api_url=args.code_api_url,
            elapsed_ms=round((_time.perf_counter() - started) * 1000, 2), api=api_meta)
        # If an apply ran and the post-cleanup probe failed, the operator cannot verify the
        # aftermath — surface it loudly in the summary's next step.
        if not dry_run and api_after.get("error"):
            summary["next_safe_step"] = (
                f"POST-CLEANUP :3280 probe FAILED ({api_after['error']}) — verify service health and "
                "reconcile against the audit log before trusting the deletion outcome. "
                + summary["next_safe_step"])
        print(json.dumps(summary, indent=2, sort_keys=True))
        return _evictor.cleanup_exit_code(actions)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
