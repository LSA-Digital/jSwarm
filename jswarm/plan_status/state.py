"""plan-status state machine — canonical vocabulary + transition rules.

Authoritative design: docs/plans/TICKET-XXX.specs.md (Transition Matrix, Oracle Concern #2).
Decisions: docs/plans/evidence/TICKET-XXX/00-decisions.md (D3 states, D4 merge mapping).

A `plan_status` value is the canonical lifecycle state of a plan. Story/Task/Bug
plans carry one of the values below; Feature plans carry a derived high-level value
(handled elsewhere). The merge-state-machine `status:` field (ACTIVE / READY_FOR_MERGE /
DONE / WONT_DO / DEFERRED) is DERIVED from plan_status via ``derive_merge_status``.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

# --- Canonical state values (long form per plan A/C 6) ----------------------

STATE_LITE_INIT = "0.planning.lite_init"
STATE_LITE_REFINE = "1.planning.lite_refine"
STATE_DETAILED = "2.planning.detailed"
STATE_IMPLEMENTATION_PREFIX = "3.implementation.phase_"
STATE_ALL_AC_MET = "4.closed.all_ac_met"
STATE_READY_FOR_MERGE = "5.closed.ready_for_merge"
STATE_MERGED = "6.closed.merged"
STATE_WONT_DO = "terminal.wont_do"
STATE_DEFERRED = "terminal.deferred"

#: Sentinel for "no registry/frontmatter entry yet".
NULL_STATE = "null"

# --- Transition classification ---------------------------------------------

Classification = Literal["allowed", "idempotent", "manual", "illegal"]

# Internal categories (collapse the dynamic 3.implementation.phase_N.<descr>
# family into a single IMPL category for matrix lookup).
_CAT_NULL = "NULL"
_CAT_LITE_INIT = "LITE_INIT"
_CAT_LITE_REFINE = "LITE_REFINE"
_CAT_DETAILED = "DETAILED"
_CAT_IMPL = "IMPL"
_CAT_ALL_AC_MET = "ALL_AC_MET"
_CAT_READY = "READY"
_CAT_MERGED = "MERGED"
_CAT_WONT_DO = "WONT_DO"
_CAT_DEFERRED = "DEFERRED"

_TERMINAL_CATS = frozenset({_CAT_MERGED, _CAT_WONT_DO, _CAT_DEFERRED})


class InvalidTransitionError(ValueError):
    """Raised when a transition is illegal, or manual-only without override."""


class InvalidStateError(ValueError):
    """Raised when a state string is not a recognized plan_status value."""


def category_of(state: str) -> str:
    """Map a plan_status value to its internal transition category.

    Raises InvalidStateError for unrecognized values.
    """
    if state == NULL_STATE:
        return _CAT_NULL
    if state == STATE_LITE_INIT:
        return _CAT_LITE_INIT
    if state == STATE_LITE_REFINE:
        return _CAT_LITE_REFINE
    if state == STATE_DETAILED:
        return _CAT_DETAILED
    if state.startswith(STATE_IMPLEMENTATION_PREFIX):
        return _CAT_IMPL
    if state == STATE_ALL_AC_MET:
        return _CAT_ALL_AC_MET
    if state == STATE_READY_FOR_MERGE:
        return _CAT_READY
    if state == STATE_MERGED:
        return _CAT_MERGED
    if state == STATE_WONT_DO:
        return _CAT_WONT_DO
    if state == STATE_DEFERRED:
        return _CAT_DEFERRED
    raise InvalidStateError(f"Unrecognized plan_status value: {state!r}")


def is_valid_state(state: str) -> bool:
    """True when ``state`` is a recognized plan_status value (incl. null sentinel)."""
    try:
        category_of(state)
        return True
    except InvalidStateError:
        return False


# Transition matrix keyed by (from_category, to_category) -> classification.
# Mirrors docs/plans/TICKET-XXX.specs.md § Transition Matrix exactly.
# Same-category (from == to) is resolved separately: identical string -> idempotent,
# differing string within IMPL (phase advance) -> allowed.
_MATRIX: dict[tuple[str, str], Classification] = {}


def _build_matrix() -> None:
    a = "allowed"
    m = "manual"
    x = "illegal"
    # Rows: from-category. Columns in spec order:
    # LITE_INIT, LITE_REFINE, DETAILED, IMPL, ALL_AC_MET, READY, MERGED, WONT_DO, DEFERRED
    rows: dict[str, dict[str, Classification]] = {
        _CAT_NULL: {
            _CAT_LITE_INIT: a, _CAT_LITE_REFINE: x, _CAT_DETAILED: a, _CAT_IMPL: x,
            _CAT_ALL_AC_MET: x, _CAT_READY: x, _CAT_MERGED: x, _CAT_WONT_DO: x,
            _CAT_DEFERRED: x,
        },
        _CAT_LITE_INIT: {
            _CAT_LITE_REFINE: a, _CAT_DETAILED: a, _CAT_IMPL: x, _CAT_ALL_AC_MET: x,
            _CAT_READY: x, _CAT_MERGED: x, _CAT_WONT_DO: a, _CAT_DEFERRED: a,
        },
        _CAT_LITE_REFINE: {
            _CAT_LITE_INIT: x, _CAT_DETAILED: a, _CAT_IMPL: x, _CAT_ALL_AC_MET: x,
            _CAT_READY: x, _CAT_MERGED: x, _CAT_WONT_DO: a, _CAT_DEFERRED: a,
        },
        _CAT_DETAILED: {
            _CAT_LITE_INIT: x, _CAT_LITE_REFINE: x, _CAT_IMPL: a, _CAT_ALL_AC_MET: m,
            _CAT_READY: x, _CAT_MERGED: x, _CAT_WONT_DO: a, _CAT_DEFERRED: a,
        },
        _CAT_IMPL: {
            _CAT_LITE_INIT: x, _CAT_LITE_REFINE: x, _CAT_DETAILED: m, _CAT_ALL_AC_MET: a,
            _CAT_READY: a, _CAT_MERGED: x, _CAT_WONT_DO: a, _CAT_DEFERRED: a,
        },
        _CAT_ALL_AC_MET: {
            _CAT_LITE_INIT: x, _CAT_LITE_REFINE: x, _CAT_DETAILED: m, _CAT_IMPL: m,
            _CAT_READY: a, _CAT_MERGED: x, _CAT_WONT_DO: a, _CAT_DEFERRED: a,
        },
        _CAT_READY: {
            _CAT_LITE_INIT: x, _CAT_LITE_REFINE: x, _CAT_DETAILED: m, _CAT_IMPL: m,
            _CAT_ALL_AC_MET: m, _CAT_MERGED: a, _CAT_WONT_DO: a, _CAT_DEFERRED: a,
        },
        _CAT_MERGED: {
            _CAT_LITE_INIT: x, _CAT_LITE_REFINE: x, _CAT_DETAILED: x, _CAT_IMPL: m,
            _CAT_ALL_AC_MET: x, _CAT_READY: x, _CAT_WONT_DO: m, _CAT_DEFERRED: m,
        },
        _CAT_WONT_DO: {
            _CAT_LITE_INIT: x, _CAT_LITE_REFINE: x, _CAT_DETAILED: m, _CAT_IMPL: x,
            _CAT_ALL_AC_MET: x, _CAT_READY: x, _CAT_MERGED: x, _CAT_DEFERRED: m,
        },
        _CAT_DEFERRED: {
            _CAT_LITE_INIT: x, _CAT_LITE_REFINE: x, _CAT_DETAILED: m, _CAT_IMPL: a,
            _CAT_ALL_AC_MET: x, _CAT_READY: x, _CAT_MERGED: x, _CAT_WONT_DO: m,
        },
    }
    for from_cat, cols in rows.items():
        for to_cat, cls in cols.items():
            _MATRIX[(from_cat, to_cat)] = cls


_build_matrix()


def classify_transition(from_state: str, to_state: str) -> Classification:
    """Classify a transition as allowed / idempotent / manual / illegal.

    Both states must be recognized (InvalidStateError otherwise).
    """
    from_cat = category_of(from_state)
    to_cat = category_of(to_state)

    if from_cat == to_cat:
        # Same category. Identical string is a pure no-op. Within IMPL, a
        # differing phase string (phase advance / same-phase descr update) is allowed.
        if from_state == to_state:
            return "idempotent"
        if from_cat == _CAT_IMPL:
            return "allowed"
        # Any other same-category differing string shouldn't occur (single value
        # per category) but treat as idempotent for safety.
        return "idempotent"

    return _MATRIX.get((from_cat, to_cat), "illegal")


def derive_merge_status(plan_status: str) -> str:
    """Map a plan_status value to the merge state-machine ``status:`` field.

    See docs/merge/state-machine.md and decision D4.
    """
    cat = category_of(plan_status)
    # Spec §1: 4.closed.all_ac_met stays ACTIVE (A/C met but NOT yet merge-proven);
    # only 5.closed.ready_for_merge derives READY_FOR_MERGE. This 4->5 gate is
    # load-bearing for /jMerge + dashboards (Critic B1).
    if cat in (_CAT_NULL, _CAT_LITE_INIT, _CAT_LITE_REFINE, _CAT_DETAILED, _CAT_IMPL, _CAT_ALL_AC_MET):
        return "ACTIVE"
    if cat == _CAT_READY:
        return "READY_FOR_MERGE"
    if cat == _CAT_MERGED:
        return "DONE"
    if cat == _CAT_WONT_DO:
        return "WONT_DO"
    if cat == _CAT_DEFERRED:
        return "DEFERRED"
    raise InvalidStateError(f"No merge-status mapping for {plan_status!r}")


def binds_session_hud(plan_status: str) -> bool:
    """True ONLY for active-work implementation states (3.implementation.*), which
    legitimately bind the live terminal's HUD to this ticket (Phase 6).

    Creation/planning-seed states (0/1/2.*) and closeout/terminal states
    (4/5/6, wont_do, deferred) do NOT auto-bind — so a /jPlan ticket-creation
    (often run by a planner SUBAGENT that inherits the parent's CLAUDE_CODE_SESSION_ID)
    can never rebind another terminal's HUD. Explicit binding (cli.py `bind` /
    /jPrecompact) remains the only other binder. Fail-closed: unrecognized → False."""
    try:
        return category_of(plan_status) == _CAT_IMPL
    except InvalidStateError:
        return False


def validate_transition(from_state: str, to_state: str, *, manual: bool = False) -> Classification:
    """Validate a transition; raise InvalidTransitionError if not permitted.

    Returns the classification ('allowed' | 'idempotent') on success. A 'manual'
    transition is permitted only when ``manual=True`` (developer override), and is
    returned as 'allowed'. 'illegal' always raises.
    """
    cls = classify_transition(from_state, to_state)
    if cls == "illegal":
        raise InvalidTransitionError(
            f"Illegal transition: {from_state!r} -> {to_state!r}"
        )
    if cls == "manual":
        if not manual:
            raise InvalidTransitionError(
                f"Transition {from_state!r} -> {to_state!r} requires an explicit "
                f"manual override (reopen/manual actor + reason)."
            )
        return "allowed"
    return cls


def is_terminal(state: str) -> bool:
    """True when ``state`` is a terminal/closed lifecycle value (6/wont_do/deferred)."""
    return category_of(state) in _TERMINAL_CATS


# --- Derived frontmatter fields (phase, ac_complete) ----------------
#
# These are PURE plan-body/plan_status parsing helpers (no registry, no IO). They
# live beside derive_merge_status because, like ``status:``, the new ``phase:`` and
# ``ac_complete:`` frontmatter fields are DERIVED projections — never hand-authored
# source of truth. Callers (reconcile.normalize_plan_file) apply the §6.0 guard:
# derive_phase is only meaningful for valid, non-null states.

#: Stage label for non-implementation valid states (spec §6.1). Implementation
#: states are handled by the phase regex below, not this map.
STAGE_LABEL: dict[str, str] = {
    STATE_LITE_INIT: "0.planning",
    STATE_LITE_REFINE: "1.planning",
    STATE_DETAILED: "2.planning",
    STATE_ALL_AC_MET: "4.closed",
    STATE_READY_FOR_MERGE: "5.ready_for_merge",
    STATE_MERGED: "6.merged",
    STATE_WONT_DO: "wont_do",
    STATE_DEFERRED: "deferred",
}

# 3.implementation.phase_<N>[.<slug>]
_IMPL_PHASE_RE = re.compile(r"^3\.implementation\.phase_(\d+)(?:\.(.+))?$")
# First emoji / pictographic char — used to trim trailing status markers from a
# phase heading ("Backfill all plans 🟢 DONE" -> "Backfill all plans").
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emoji & pictographs (incl. 🟢 🟡 🔴)
    "←-⇿"           # arrows
    "⌀-➿"           # misc technical, dingbats
    "⬀-⯿"           # misc symbols & arrows
    "️‍"            # variation selector-16, zero-width joiner
    "]"
)


def _humanize_slug(slug: str) -> str:
    s = slug.replace("_", " ").replace("-", " ").strip()
    if not s:
        return ""
    return s[:1].upper() + s[1:]


def _strip_trailing_status(desc: str) -> str:
    """Drop a trailing status marker / emoji run from a phase heading description."""
    m = _EMOJI_RE.search(desc)
    if m:
        desc = desc[: m.start()]
    return desc.strip().rstrip("·-–—").strip()


def _phase_heading_desc(body: str, n: str) -> str:
    """First ``## Phase <n>: <desc>`` heading description in the plan body (## .. ####)."""
    if not body:
        return ""
    pat = re.compile(rf"^#{{2,4}}\s*Phase\s+{re.escape(n)}\s*:\s*(.+?)\s*$", re.MULTILINE)
    m = pat.search(body)
    if not m:
        return ""
    return _strip_trailing_status(m.group(1).strip())


def derive_phase(plan_status: str, body: str = "") -> Optional[str]:
    """Derive the ``phase:`` field from plan_status (+ body for impl headings).

    Implementation states -> ``"<N>.<heading-or-slug>"`` (e.g. "10.Cutover deployment"),
    or just ``"<N>"`` (str) when neither a body heading nor a slug is available.
    Non-impl valid states -> ``STAGE_LABEL``. Returns ``None`` for the null sentinel
    or any value without a stage label (caller leaves any authored ``phase`` intact).
    """
    m = _IMPL_PHASE_RE.match(plan_status)
    if m:
        n, slug = m.group(1), m.group(2)
        desc = _phase_heading_desc(body, n) or _humanize_slug(slug or "")
        return f"{n}.{desc}" if desc else n
    return STAGE_LABEL.get(plan_status)


_AC_HEADING_RE = re.compile(r"^(#{2,3})\s+Acceptance\s+Criteria\b", re.IGNORECASE)
_AC_BOX_RE = re.compile(r"^\s*[-*]\s+\[([ xX~])\]")


def derive_ac_complete(body: str) -> Optional[str]:
    """Derive ``ac_complete: "<done>/<total>"`` from the ``## Acceptance Criteria`` section.

    Scoped to that section only (scan stops at the next heading of equal-or-shallower
    level). ``[x]``/``[X]`` count as done; ``[ ]`` and ``[~]`` (in-progress) count toward
    total only. Returns ``"0/0"`` when the section exists but has no checkboxes, and
    ``None`` when there is no Acceptance Criteria section (caller then removes any stale
    ``ac_complete``). This is a progress HEURISTIC, not the acceptance source-of-truth.
    """
    lines = body.splitlines()
    start = None
    level = 0
    for i, line in enumerate(lines):
        m = _AC_HEADING_RE.match(line)
        if m:
            start = i
            level = len(m.group(1))
            break
    if start is None:
        return None
    next_heading_re = re.compile(rf"^#{{1,{level}}}\s")
    total = 0
    done = 0
    for line in lines[start + 1:]:
        if next_heading_re.match(line):
            break
        bm = _AC_BOX_RE.match(line)
        if bm:
            total += 1
            if bm.group(1) in ("x", "X"):
                done += 1
    return f"{done}/{total}"
