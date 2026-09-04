"""COM-167 R2–R5 — lifecycle drift audit for the reconcile-engine inputs.

The reconcile engine (migrate → rebuild → reconcile → count) is deliberately **fail-open at
the /jPrecompact checkpoint**: a missing result doc, an un-seedable matrix, or a still-0/N
count never blocks a checkpoint. That is correct *there* — a checkpoint must always succeed.
But the same silence at a **lifecycle boundary** (closing a ticket, promoting to merge, a fleet
health sweep) is how COM-176/COM-197 reached ``READY_FOR_MERGE``/``DONE`` showing ``0/19``
UAT and ``0/12`` NFR with nobody alerted (retro 2026-06-22, "silent 0/N from un-gated inputs").

This module is the **fail-loud-at-the-boundary** half of that design. ``audit_ticket`` gathers
the raw facts about one ticket's reconcile inputs; the ``gate_*`` / ``scan_fleet`` / ``lint_*``
helpers turn those facts into the four guardrails:

  * **R2** ``lint_slices`` — /jPlan authoring gate: a declared-applicable ticket must author
    its UAT/NFR index (or matrix) as a *parseable* table, not prose — so the engine never
    silently fail-safe-refuses it later (failure mode M1).
  * **R3** ``gate_close`` — /jClose pre-flight: BLOCK an applicable, matrix-bearing ticket
    at a closing stage that is silent-0/N or un-seedable.
  * **R4** ``gate_implement`` — /jGo plan-completion: BLOCK promotion of an applicable,
    matrix-bearing ticket whose result docs (``KEY.uat-scenario-steps.md`` — legacy
    ``KEY.uat-test.md`` — / ``KEY.nfr-test.md``) are absent (the M2 root: no result docs →
    nothing to reconcile → 0/N) unless deferral is logged.
  * **R5** ``scan_fleet`` — /devops-maint metric: make silent-0/N / un-seedable drift VISIBLE
    across the fleet.

★ KEY CORRECTNESS LESSON (R6, retro 2026-06-22): the audit must **NOT flag pre-implementation
tickets (BACKLOG / planning) as drift** — 0/N is EXPECTED before implementation. A ticket is
flagged ONLY when its ``lifecycle_stage`` is one where the counts SHOULD be non-zero
(implementation+, all-AC-met, ready-for-merge, merged). R6 scouting proved 4 of 6 apparent
"silent 0/N" tickets were merely pre-implementation, not drift. Stage is read from the
canonical ``plan_status`` (COM-84) — never guessed.

Ids are handled with the **permissive reconcile grammar** (``rows.validate_index_rows`` /
``matrices.extract_*``), NOT the strict ``nfr-catalog/nfr_common._REF_RE``: R1 made the engine a
deliberate permissive superset so legacy/bare ids (``NFR-4``) reconcile, and these gates must not
re-introduce a false flag on them. Canonical grammar enforcement lives in nfr-catalog + the R2
authoring lint, not in this audit (docs/standards/catalog-id-convention.md, COM-198).

Fail-open by construction: any unreadable/ambiguous input degrades to a non-blocking verdict
(``unknown`` / ``ok``), never a spurious block. The gates block only on a *positively confirmed*
drift at a should-have-counts stage.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Importable both as ``python jswarm/precompact_reconcile/lifecycle_audit.py`` and ``-m``.
# Insert the repository root (parents[2]: <pkg>/ -> jswarm/ -> repo root), not
# jswarm/ itself (parents[1]). jswarm/ on sys.path would shadow the stdlib for
# anything under jswarm/ sharing a name with it (e.g. jswarm/platform/ vs the
# stdlib platform module).
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2])
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from jswarm.precompact_reconcile import migrate as MG  # noqa: E402
from jswarm.precompact_reconcile import rows as RW  # noqa: E402
from jswarm.precompact_reconcile.matrices import _TICKET_RE, resolve_uat_doc  # noqa: E402
from jswarm.plan_status import config as C  # noqa: E402
from jswarm.plan_status import frontmatter as FM  # noqa: E402
from jswarm.plan_status import reconcile as RC  # noqa: E402
from jswarm.plan_status import state as S  # noqa: E402
# NOTE: ``joptimize`` is deliberately NOT imported at module scope. This package is a
# whole-directory managed-copy deployment (docs/_CONTROLLED_CONFIG/managed-deployments.yaml,
# id: precompact-matrix-reconcile) declared "self-contained" and shipped WITHOUT
# jswarm/joptimize/. A module-scope import would make every operation here — --stage close,
# --scan, ordinary UAT/NFR lint — ImportError in the downstream project, for a dependency
# only two artifact-specific lints ever need. The validators are therefore imported at their
# seams, once an artifact is actually present. ImportError is NOT caught: a present artifact
# whose validator cannot load must still fail the lint loud, never be silently skipped.

# ── stage classification (the R6 guardrail) ──────────────────────────────────

#: Stage classes. Only ``should_have_counts`` tickets are ever flagged as drift.
PRE_IMPLEMENTATION = "pre_implementation"
SHOULD_HAVE_COUNTS = "should_have_counts"
TERMINAL_ABANDONED = "terminal_abandoned"
UNKNOWN = "unknown"

# Canonical plan_status (COM-84) → stage class. Pre-implementation (planning/lite) is
# expected-empty; abandoned terminals carry no evidence obligation; merged/ready/all-AC-met/
# in-implementation SHOULD have counts. NULL is intentionally "unknown" (no info ⇒ never flag).
_PRE_IMPL_CATS = frozenset({S._CAT_LITE_INIT, S._CAT_LITE_REFINE, S._CAT_DETAILED})
_SHOULD_HAVE_CATS = frozenset({S._CAT_IMPL, S._CAT_ALL_AC_MET, S._CAT_READY, S._CAT_MERGED})
_ABANDONED_CATS = frozenset({S._CAT_WONT_DO, S._CAT_DEFERRED})

# Legacy merge-state `status:` fallback when plan_status is absent/unrecognized. Conservative:
# ACTIVE/BACKLOG span planning AND implementation, so they map to "unknown" (never flagged) to
# honor the R6 lesson — only the unambiguously-closing/closed states are should-have-counts.
_STATUS_FALLBACK = {
    "READY_FOR_MERGE": SHOULD_HAVE_COUNTS,
    "DONE": SHOULD_HAVE_COUNTS,
    "WONT_DO": TERMINAL_ABANDONED,
    "DEFERRED": TERMINAL_ABANDONED,
}


def classify_stage(plan_status: str | None, status: str | None = None) -> str:
    """Map a ticket's canonical ``plan_status`` (COM-84) to a drift stage class.

    Falls back to the legacy merge-state ``status:`` only when ``plan_status`` is absent or
    unrecognized, and even then resolves ambiguous states (ACTIVE/BACKLOG) to ``unknown`` so a
    pre-implementation ticket is never flagged (R6). Fail-open: anything indeterminate ⇒
    ``unknown``.
    """
    ps = (plan_status or "").strip().strip('"').strip("'")
    if ps and ps != S.NULL_STATE:
        try:
            cat = S.category_of(ps)
        except S.InvalidStateError:
            cat = None
        if cat is not None:
            if cat in _PRE_IMPL_CATS:
                return PRE_IMPLEMENTATION
            if cat in _SHOULD_HAVE_CATS:
                return SHOULD_HAVE_COUNTS
            if cat in _ABANDONED_CATS:
                return TERMINAL_ABANDONED
            return UNKNOWN
        # Unrecognized but non-empty — best-effort prefix read (legacy/raw values).
        if ps.startswith(("0.", "1.", "2.")) or "planning" in ps:
            return PRE_IMPLEMENTATION
        if ps.startswith(("3.", "4.", "5.", "6.")):
            return SHOULD_HAVE_COUNTS
        if ps.startswith("terminal"):
            return TERMINAL_ABANDONED
        return UNKNOWN
    return _STATUS_FALLBACK.get((status or "").strip().strip('"').strip("'").upper(), UNKNOWN)


# ── matrix-count helpers (reuse the reconcile derivations — single source) ────

def _uat_counts(plan_text: str) -> tuple[int, int] | None:
    bounds = RC._find_section_bounds(plan_text, RC._UAT_SECTION_HEADING)
    if bounds is None:
        return None
    start, end = bounds
    return RC._count_matrix_status_rows(plan_text[start:end])


def _nfr_counts(plan_text: str) -> tuple[int, int] | None:
    return RC._nfr_counts(plan_text)


def _fmt_count(counts: tuple[int, int] | None) -> str | None:
    return None if counts is None else f"{counts[0]}/{counts[1]}"


def _parse_count(count: str | None) -> tuple[int, int] | None:
    if not count or "/" not in count:
        return None
    try:
        g, t = count.split("/", 1)
        return int(g), int(t)
    except ValueError:
        return None


def _is_zero_of_n(counts: tuple[int, int] | None) -> bool:
    """True only for a genuine silent 0/N: N>0 rows, 0 green. A 0/0 (empty) matrix is NOT
    drift — there is nothing to be green about."""
    return counts is not None and counts[1] > 0 and counts[0] == 0


def _is_incomplete(counts: tuple[int, int] | None) -> bool:
    """True when a matrix has rows that are not all green (0 ≤ green < total). A fully-green
    matrix is its own evidence that reconciliation happened — so a missing result-doc FILE on
    such a dimension is moot, not drift."""
    return counts is not None and counts[1] > 0 and counts[0] < counts[1]


def _table_present_under_heading(text: str, heading: str) -> bool:
    """True when a markdown table (a ``|``-led line) appears under ``heading`` before the next
    section heading. Fence-aware. Distinguishes an authored table (even an empty header-only
    one) from prose under the heading."""
    lines = text.splitlines()
    mask = RW._fence_mask(lines)
    i = next((n for n in range(len(lines)) if not mask[n] and lines[n].strip() == heading), None)
    if i is None:
        return False
    i += 1
    while i < len(lines):
        if mask[i]:
            i += 1
            continue
        s = lines[i].strip()
        if s.startswith("## ") or s.startswith("### "):
            break
        if s.startswith("|"):
            return True
        i += 1
    return False


# ── the audit ────────────────────────────────────────────────────────────────

@dataclass
class TicketAudit:
    ticket: str
    found: bool                     # a canonical plan file was located
    plan_path: str | None
    lifecycle_stage: str            # raw plan_status (or merge status fallback), "" if none
    stage_class: str                # pre_implementation | should_have_counts | terminal_abandoned | unknown
    nfr_applicable: bool
    uat_applicable: bool
    applicable: bool
    matrix_bearing: bool            # plan has a UAT or NFR matrix SECTION
    has_uat_results: bool           # KEY.uat-scenario-steps.md (legacy KEY.uat-test.md) present
    has_nfr_results: bool           # KEY.nfr-test.md present
    uat_count: str | None           # "green/total" or None (no UAT matrix)
    nfr_count: str | None
    uat_at_0_of_N: bool
    nfr_at_0_of_N: bool
    at_0_of_N: bool
    unseedable_reasons: list[str] = field(default_factory=list)
    missing_result_docs: list[str] = field(default_factory=list)
    verdict: str = "unknown"        # not_applicable | expected_empty | abandoned | unknown | ok
                                    #   | drift_zero_of_n | drift_unseedable | drift_missing_results | drift_no_matrix
    notes: list[str] = field(default_factory=list)

    @property
    def is_drift(self) -> bool:
        return self.verdict.startswith("drift_")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_drift"] = self.is_drift
        return d


def audit_plan_text(
    plan_text: str,
    *,
    ticket: str = "TICKET",
    has_uat_results: bool = False,
    has_nfr_results: bool = False,
    uat_slice_text: str | None = None,
    nfr_slice_text: str | None = None,
    test_slice_text: str | None = None,
) -> TicketAudit:
    """Pure audit over plan text + result-doc presence + (optional) slice texts.

    Slice texts are used only to detect *un-seedable* matrices via the migrate dry-run: a
    matrix with rows but no slice index that the engine would fail-safe-refuse to seed. When a
    slice already carries the index, that kind is simply not "un-seedable" (it is already
    seeded) — so passing the real slice text avoids false un-seedable flags.
    """
    fm = FM.read_frontmatter_text(plan_text)
    lifecycle_stage = str(fm.get("plan_status") or "").strip()
    stage_class = classify_stage(fm.get("plan_status"), fm.get("status"))

    nfr_applicable = MG._is_applicable(plan_text, "nfr")
    uat_applicable = MG._is_applicable(plan_text, "uat")
    nfr_matrix = MG._has_heading(plan_text, RW._NFR_MATRIX)
    uat_matrix = MG._has_heading(plan_text, RW._UAT_MATRIX)
    matrix_bearing = nfr_matrix or uat_matrix

    uat_c = _uat_counts(plan_text)
    nfr_c = _nfr_counts(plan_text)
    uat_zero = _is_zero_of_n(uat_c)
    nfr_zero = _is_zero_of_n(nfr_c)

    # Un-seedable detection via the pure migrate dry-run (no IO). A matrix with rows but no
    # slice index that the seeder would refuse surfaces here as a "*_skipped: unseedable-*".
    # SCOPED TO UAT + NFR: the regression A/C-to-Test matrix is a separate, optional dimension
    # outside COM-167 R2–R5 (which is about the count-bearing uat_complete/nfr_complete silent
    # 0/N). It is noncanonical/absent on the vast majority of tickets, so including it would
    # false-flag nearly the whole fleet — including the healthy canonical reference COM-194.
    unseedable: list[str] = []
    try:
        _p, _u, _n, _t, report = MG.migrate_text(
            plan_text,
            uat_slice_text=uat_slice_text,
            nfr_slice_text=nfr_slice_text,
            test_slice_text=test_slice_text,
            key=ticket,
            include_test=True,
        )
        for kind in ("uat", "nfr"):
            reason = report.get(f"{kind}_skipped")
            if reason and "unseedable" in reason:
                unseedable.append(f"{kind}: {reason}")
    except Exception as exc:  # noqa: BLE001 — audit is strictly fail-open
        # A migrate failure must never make the audit itself raise; record + carry on.
        unseedable = []  # leave empty; the count/result-doc signals still stand
        _ = exc

    # Missing result docs — meaningful only for an applicable+matrix-bearing dimension that is
    # NOT already fully green. A 100%-green matrix is its own evidence: the result-doc FILE may
    # have been cleaned up post-merge (COM-194 has nfr 12/12 but no nfr-test.md on disk), and
    # blocking promotion/close on that is a false positive. The gap that matters is a missing
    # doc on a dimension whose counts are still short — that is the M2 root (no doc → 0/N).
    missing: list[str] = []
    if uat_applicable and uat_matrix and not has_uat_results and not _is_fully_green(uat_c):
        missing.append(f"{ticket}.uat-scenario-steps.md")
    if nfr_applicable and nfr_matrix and not has_nfr_results and not _is_fully_green(nfr_c):
        missing.append(f"{ticket}.nfr-test.md")

    audit = TicketAudit(
        ticket=ticket,
        found=True,
        plan_path=None,
        lifecycle_stage=lifecycle_stage,
        stage_class=stage_class,
        nfr_applicable=nfr_applicable,
        uat_applicable=uat_applicable,
        applicable=nfr_applicable or uat_applicable,
        matrix_bearing=matrix_bearing,
        has_uat_results=has_uat_results,
        has_nfr_results=has_nfr_results,
        uat_count=_fmt_count(uat_c),
        nfr_count=_fmt_count(nfr_c),
        uat_at_0_of_N=uat_zero,
        nfr_at_0_of_N=nfr_zero,
        at_0_of_N=uat_zero or nfr_zero,
        unseedable_reasons=unseedable,
        missing_result_docs=missing,
    )
    audit.verdict, audit.notes = _compute_verdict(audit)
    return audit


def _compute_verdict(a: TicketAudit) -> tuple[str, list[str]]:
    """Roll the raw facts into an advisory verdict. Drift verdicts are emitted ONLY at a
    should-have-counts stage (the R6 guardrail); every other stage is non-flagging."""
    notes: list[str] = []
    if not a.applicable:
        return "not_applicable", notes
    if a.stage_class == PRE_IMPLEMENTATION:
        notes.append("pre-implementation stage — 0/N is expected, not drift (R6)")
        return "expected_empty", notes
    if a.stage_class == TERMINAL_ABANDONED:
        notes.append("abandoned terminal stage — no evidence obligation")
        return "abandoned", notes
    if a.stage_class != SHOULD_HAVE_COUNTS:
        notes.append("indeterminate lifecycle stage — not flagged (fail-open)")
        return "unknown", notes
    # should_have_counts — positively confirmed drift wins, in severity order.
    # (1) Active silent 0/N is the headline drift (COM-176/COM-197).
    if a.at_0_of_N:
        notes.append("silent 0/N at a should-have-counts stage — counts never moved off 🔴")
        return "drift_zero_of_n", notes
    # (2) Un-seedable matrix that is NOT already fully green: a latent M1 silent-0/N risk. An
    # already-100%-green dimension is its own evidence (its rows were hand-maintained), so a
    # noncanonical header there is cosmetic, not active drift — don't false-flag it.
    active_unseedable = [r for r in a.unseedable_reasons
                         if not _is_fully_green(_parse_count(_count_for(a, r.split(":", 1)[0])))]
    if active_unseedable:
        notes.append("matrix authored in a form the reconcile engine refuses to seed (M1)")
        return "drift_unseedable", notes
    # (3) Missing result-doc FILES are drift only when the matrix is not already fully green: a
    # 100%-green dimension is its own evidence (files may have been cleaned up post-merge),
    # whereas missing docs on a still-incomplete dimension is a real gap. R4's gate_implement
    # uses missing_result_docs directly (presence-at-promotion), independent of this roll-up.
    if a.missing_result_docs and (_is_incomplete(_parse_count(a.uat_count))
                                  or _is_incomplete(_parse_count(a.nfr_count))):
        notes.append("result docs absent on an incomplete dimension → reconciliation gap (M2 root)")
        return "drift_missing_results", notes
    if a.applicable and not a.matrix_bearing:
        notes.append("declared applicable but carries no traceability matrix")
        return "drift_no_matrix", notes
    return "ok", notes


def _count_for(a: "TicketAudit", kind: str) -> str | None:
    return a.uat_count if kind == "uat" else a.nfr_count if kind == "nfr" else None


def _is_fully_green(counts: tuple[int, int] | None) -> bool:
    return counts is not None and counts[1] > 0 and counts[0] == counts[1]


# ── IO layer: resolve a ticket's plan + slices + result docs ─────────────────

def _resolve_paths(ticket: str, repo_root: Path):
    """Return (plan_path|None, ticket_dir, reason|None). plan_path None ⇒ no audit possible."""
    if not _TICKET_RE.match(ticket):
        return None, None, "invalid ticket key"
    plans = Path(repo_root) / ".jswarm" / "plans"
    matches = sorted(plans.glob(f"{ticket}.plan.*.md"))
    if len(matches) > 1:
        return None, None, "ambiguous: multiple matching plans"
    if not matches:
        return None, None, None
    return matches[0], plans / ticket, None


def _read(path: Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def audit_ticket(ticket: str, repo_root: Path | str = ".") -> TicketAudit:
    """Audit one ticket from disk. Always returns a ``TicketAudit`` — ``found=False`` when no
    canonical plan exists (fail-open, never raises)."""
    repo_root = Path(repo_root)
    plan_path, tdir, reason = _resolve_paths(ticket, repo_root)
    if plan_path is None:
        a = _empty_audit(ticket)
        if reason:
            a.notes.append(reason)
        return a
    plan_text = _read(plan_path)
    if plan_text is None:
        a = _empty_audit(ticket)
        a.notes.append("unreadable-plan")
        return a
    uat_doc = resolve_uat_doc(tdir, ticket)
    nfr_doc = tdir / f"{ticket}.nfr-test.md"
    uat_slice = _read(tdir / f"{ticket}.uat-scenarios.md")
    nfr_slice = _read(tdir / f"{ticket}.nfr.md")
    test_slice = _read(tdir / f"{ticket}.regression-tests.md")
    audit = audit_plan_text(
        plan_text,
        ticket=ticket,
        has_uat_results=uat_doc.exists(),
        has_nfr_results=nfr_doc.exists(),
        uat_slice_text=uat_slice,
        nfr_slice_text=nfr_slice,
        test_slice_text=test_slice,
    )
    audit.plan_path = str(plan_path)
    return audit


def _empty_audit(ticket: str) -> TicketAudit:
    return TicketAudit(
        ticket=ticket, found=False, plan_path=None, lifecycle_stage="", stage_class=UNKNOWN,
        nfr_applicable=False, uat_applicable=False, applicable=False, matrix_bearing=False,
        has_uat_results=False, has_nfr_results=False, uat_count=None, nfr_count=None,
        uat_at_0_of_N=False, nfr_at_0_of_N=False, at_0_of_N=False,
        verdict="not_applicable",
    )


# ── R3 / R4 gates (fail-loud at the boundary) ────────────────────────────────

@dataclass
class GateDecision:
    ok: bool
    mode: str       # pass | block-* | skip | warn
    reason: str
    ticket: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "mode": self.mode, "reason": self.reason, "ticket": self.ticket}


def gate_close(audit: TicketAudit) -> GateDecision:
    """R3 — /jClose pre-flight. BLOCK an applicable, matrix-bearing ticket at a
    should-have-counts stage that is silent-0/N or un-seedable. Everything else passes
    (fail-open). A pre-implementation/abandoned/unknown stage is never blocked (R6)."""
    if not audit.found:
        return GateDecision(True, "skip", "no plan found; fail-open skip", audit.ticket)
    if not audit.applicable:
        return GateDecision(True, "skip", "NFR/UAT not applicable; non-blocking", audit.ticket)
    if audit.stage_class != SHOULD_HAVE_COUNTS:
        return GateDecision(
            True, "skip",
            f"stage '{audit.stage_class}' is not a should-have-counts stage; non-blocking (R6)",
            audit.ticket,
        )
    # R3's documented contract: BLOCK on 0/N or un-seedable; the softer drifts (missing result
    # files on an incomplete dim, no matrix) WARN — visible but not blocking — so close-ticket
    # is not over-blocked. Verdict already encodes the R6 stage gate + the not-fully-green gate.
    if audit.verdict == "drift_zero_of_n":
        bits = []
        if audit.uat_at_0_of_N:
            bits.append(f"UAT {audit.uat_count}")
        if audit.nfr_at_0_of_N:
            bits.append(f"NFR {audit.nfr_count}")
        return GateDecision(
            False, "block-zero-of-n",
            "silent 0/N at a closing stage (" + ", ".join(bits)
            + ") — verify result docs reconcile before closing",
            audit.ticket,
        )
    if audit.verdict == "drift_unseedable":
        return GateDecision(
            False, "block-unseedable",
            "un-seedable matrix would silently reconcile to 0/N: " + "; ".join(audit.unseedable_reasons),
            audit.ticket,
        )
    if audit.is_drift:  # drift_missing_results / drift_no_matrix — visible, non-blocking
        return GateDecision(True, "warn", "; ".join(audit.notes) or audit.verdict, audit.ticket)
    return GateDecision(True, "pass", "matrix counts are non-zero / seedable", audit.ticket)


def gate_implement(audit: TicketAudit, *, promoting: bool = True) -> GateDecision:
    """R4 — /jGo plan-completion. BLOCK promotion to READY_FOR_MERGE of an applicable,
    matrix-bearing ticket whose result docs are absent (M2 root). When ``promoting`` is False
    (mid-phase), this is advisory only (a warn), never a block."""
    if not audit.found:
        return GateDecision(True, "skip", "no plan found; fail-open skip", audit.ticket)
    if not (audit.applicable and audit.matrix_bearing):
        return GateDecision(True, "skip", "not an applicable matrix-bearing ticket; non-blocking", audit.ticket)
    # R6 guardrail (defense-in-depth, must mirror gate_close): never block a ticket the audit
    # classifies as non-should-have-counts. At the real /jGo Step 5S the stage is
    # 3.implementation.* (should_have_counts), so this changes nothing in-flow — but it stops a
    # pre-implementation/planning ticket (0/N is EXPECTED there) or an abandoned terminal from
    # being blocked if the gate is invoked out of that flow. Relying on missing_result_docs
    # alone, without this guard, is exactly the R6 violation a 0/N planning ticket would hit.
    if audit.stage_class != SHOULD_HAVE_COUNTS:
        return GateDecision(
            True, "skip",
            f"stage '{audit.stage_class}' is not a should-have-counts stage; non-blocking (R6)",
            audit.ticket,
        )
    if not audit.missing_result_docs:
        return GateDecision(True, "pass", "result docs present", audit.ticket)
    detail = ("result docs absent (" + ", ".join(audit.missing_result_docs)
              + ") — the engine has nothing to reconcile (0/N). Author them or log an explicit deferral.")
    if not promoting:
        return GateDecision(True, "warn", detail, audit.ticket)
    return GateDecision(False, "block-missing-results", detail, audit.ticket)


# ── R2 authoring lint ────────────────────────────────────────────────────────

@dataclass
class LintResult:
    ok: bool
    ticket: str
    problems: list[tuple[str, str]] = field(default_factory=list)  # (kind, reason)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "ticket": self.ticket,
                "problems": [{"kind": k, "reason": r} for k, r in self.problems]}


_LINT_KINDS = {
    "uat": (RW._UAT_MATRIX, RW._UAT_INDEX_HEADING),
    "nfr": (RW._NFR_MATRIX, RW._NFR_INDEX_HEADING),
}


def lint_slices_text(
    plan_text: str,
    *,
    ticket: str = "TICKET",
    uat_slice_text: str | None = None,
    nfr_slice_text: str | None = None,
) -> LintResult:
    """R2 — pure /jPlan authoring lint. For each declared-applicable dimension:

      * if a slice index OR plan matrix is authored, its rows must be *parseable* (would be
        accepted by the reconcile engine) — catches prose-instead-of-table and malformed rows;
      * a declared-applicable dimension that authors NEITHER a parseable index NOR a matrix is
        reported so /jPlan can scaffold it.

    Counts are irrelevant here (a fresh ticket is legitimately 0/0) — this is a *format* gate.
    """
    problems: list[tuple[str, str]] = []
    slices = {"uat": uat_slice_text, "nfr": nfr_slice_text}
    for kind, (matrix_heading, index_heading) in _LINT_KINDS.items():
        if not MG._is_applicable(plan_text, kind):
            continue
        width = len(MG._MATRIX_COLUMNS[kind]) - 1
        slice_text = slices[kind]
        has_heading = bool(slice_text) and MG._has_heading(slice_text, index_heading)
        matrix = MG._extract_matrix(plan_text, matrix_heading)
        matrix_has_rows = matrix is not None and bool(matrix[1])
        authored_ok = False
        if has_heading:
            if not _table_present_under_heading(slice_text, index_heading):
                # Heading authored but the body is prose — the agent wrote scenarios as text,
                # not the fixed-format index the engine reads. This is the M1 silent-refuse root.
                problems.append((kind, f"{index_heading!r} present but no table authored (prose only)"))
            else:
                reason = RW.validate_index_rows(RW.parse_index(slice_text, index_heading), width, kind)
                if reason is not None:
                    problems.append((kind, f"slice index malformed: {reason}"))
                else:
                    authored_ok = True  # a valid table (even an empty scaffold) is acceptable
        if matrix_has_rows and not has_heading:
            # A plan matrix with no slice index must be canonically seedable, else the engine
            # fail-safe-refuses it at /jPrecompact (M1). Reuse the migrate dry-run verdict.
            _p, _u, _n, _t, report = MG.migrate_text(
                plan_text, uat_slice_text=uat_slice_text, nfr_slice_text=nfr_slice_text,
                test_slice_text=None, key=ticket, include_test=True,
            )
            skip = report.get(f"{kind}_skipped")
            if skip and "unseedable" in skip:
                problems.append((kind, f"matrix not seedable: {skip}"))
            else:
                authored_ok = True
        if not authored_ok and not has_heading and not matrix_has_rows:
            problems.append((
                kind,
                f"declared applicable but no {index_heading!r} index or "
                f"{matrix_heading!r} rows authored",
            ))
    return LintResult(ok=not problems, ticket=ticket, problems=problems)


def _resolve_evidence_path(pointer: str, ticket_dir: Path, repo_root: Path) -> Path:
    """Resolve a ``## Check-In Reviews`` row's Evidence-pointer cell to a filesystem path.
    An absolute pointer is used as-is; a relative pointer is tried ticket-dir-relative
    first (evidence living alongside the ticket's other artifacts is the common case),
    falling back to repo-root-relative."""
    cleaned = pointer.strip().strip("`")
    candidate = Path(cleaned)
    if candidate.is_absolute():
        return candidate
    ticket_relative = ticket_dir / candidate
    if ticket_relative.exists():
        return ticket_relative
    return repo_root / candidate


def lint_checkin_reviews(
    state_text: str | None,
    *,
    ticket_dir: Path,
    repo_root: Path | str = ".",
) -> list[tuple[str, str]]:
    """COM-300 F1 — validate every row of the ``## Check-In Reviews`` table in a ticket's
    ``.state.md`` (row shape per the canonical contract's "## Recording" section: trigger,
    causal surface, rounds burned, verdict, cut items, resulting scope delta, evidence
    pointer) against the ``checkin-review@1`` mandatory evidence-packet contract.

    A row's 7th cell (Evidence pointer) names a JSON file holding the full check-in
    dispatch record; that file is read and validated via
    ``joptimize.checkin.validate_dispatch`` — the real, unmodified validator, not a
    re-derived check. Fail-open on an ABSENT ``## Check-In Reviews`` heading: a ticket
    that has never recorded a check-in row costs nothing here. Fail-LOUD when the
    heading IS present but the table underneath it is missing or empty (prose-only or
    header/separator with zero data rows), and one problem per row, naming the
    offending field, on a PRESENT but malformed row (wrong cell count, an unreadable or
    non-JSON evidence pointer, or a dispatch record the contract itself rejects).
    """
    if not state_text:
        return []

    if not MG._has_heading(state_text, "## Check-In Reviews"):
        return []

    rows = RW.parse_index(state_text, "## Check-In Reviews")
    if not rows:
        if not _table_present_under_heading(state_text, "## Check-In Reviews"):
            return [(
                "checkin",
                "'## Check-In Reviews' heading present but no table authored (prose only)",
            )]
        return [(
            "checkin",
            "'## Check-In Reviews' table present but has no data rows (empty table)",
        )]

    repo_root = Path(repo_root)
    problems: list[tuple[str, str]] = []
    for n, cells in enumerate(rows, 1):
        if len(cells) != 7:
            problems.append((
                "checkin",
                f"row {n}: {len(cells)} cells, expected 7 (trigger, causal surface, rounds "
                "burned, verdict, cut items, resulting scope delta, evidence pointer)",
            ))
            continue

        pointer = cells[6].strip().strip("`")
        if not pointer:
            problems.append(("checkin", f"row {n}: empty evidence pointer"))
            continue

        evidence_path = _resolve_evidence_path(pointer, ticket_dir, repo_root)
        try:
            raw = evidence_path.read_text(encoding="utf-8")
        except OSError:
            problems.append(("checkin", f"row {n}: evidence pointer unreadable: {pointer}"))
            continue

        try:
            dispatch = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append((
                "checkin", f"row {n}: evidence pointer is not valid JSON ({pointer}): {exc}"
            ))
            continue

        if not isinstance(dispatch, dict):
            problems.append(("checkin", f"row {n}: evidence pointer JSON is not an object: {pointer}"))
            continue

        # Seam import: a check-in row has resolved to a real JSON object, so the validator
        # is genuinely required now. Deliberately un-caught — see the module-scope note.
        from joptimize import checkin

        try:
            checkin.validate_dispatch(dispatch)
        except checkin.CheckinValidationError as exc:
            problems.append(("checkin", f"row {n}: {exc}"))

    return problems


# ── COM-300 T4.3 advisor design-contract acceptance lint ────────────────────

def lint_advisor_acceptance(*, ticket_dir: Path) -> list[tuple[str, str]]:
    """COM-300 T4.3 — validate every ``advisor-acceptance-*.json`` artifact recorded in a
    ticket's folder against the ``advisor-contract@1`` §3 acceptance contract
    (``advisor_acceptance.validate_acceptance`` — the real, unmodified validator, not a
    re-derived check). Discovers artifacts by glob in ``ticket_dir``; multiple artifacts are
    each validated, sorted by filename so output is deterministic. Unlike
    ``lint_checkin_reviews``, this lint takes no ``repo_root``: it resolves no pointers, so
    discovery is entirely inside ``ticket_dir`` and a repo-root parameter would be a
    signature that lies about what the function needs.

    Fail-open, validate-if-present (NFR-300-1): no matching files ⇒ ``[]`` — a ticket that
    accepted no advisor design contract pays nothing here. Fail-LOUD, one problem per
    artifact, when a file is unreadable, is not valid JSON, or its JSON is not an object
    (each names the FILE), or when its record is one the validator rejects (names the file
    and carries the validator's message).
    """
    ticket_dir = Path(ticket_dir)
    problems: list[tuple[str, str]] = []
    for artifact in sorted(ticket_dir.glob("advisor-acceptance-*.json")):
        try:
            raw = artifact.read_text(encoding="utf-8")
        except OSError:
            problems.append(("advisor-acceptance", f"{artifact.name}: unreadable"))
            continue

        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(("advisor-acceptance", f"{artifact.name}: not valid JSON: {exc}"))
            continue

        if not isinstance(record, dict):
            problems.append(("advisor-acceptance", f"{artifact.name}: JSON is not an object"))
            continue

        # Seam import: an acceptance artifact has resolved to a real JSON object, so the
        # validator is genuinely required now. Deliberately un-caught — see the module note.
        from joptimize import advisor_acceptance

        try:
            advisor_acceptance.validate_acceptance(record)
        except advisor_acceptance.AdvisorAcceptanceValidationError as exc:
            problems.append(("advisor-acceptance", f"{artifact.name}: {exc}"))

    return problems


def lint_checkin_evaluation_rows(
    ticket: str, *, project_root: Path | str = ".",
) -> list[tuple[str, str]]:
    """Validate typed evaluation rows if a ticket ledger already contains them.

    Ledger absence and rows of other types are intentionally free: this lint
    validates recorded data but does not claim that every ticket recorded it.
    """

    checkin = sys.modules.get("jswarm.joptimize.checkin")
    if checkin is None:
        from joptimize import checkin

    ledger_path = checkin.dedicated_evaluation_ledger_path(project_root, ticket)
    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    problems: list[tuple[str, str]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(("checkin-evaluation", f"row {number}: invalid ledger JSON: {exc}"))
            continue
        if not isinstance(row, dict):
            problems.append(("checkin-evaluation", f"row {number}: ledger row is not an object"))
            continue
        try:
            checkin.validate_evaluation_row(row)
        except checkin.CheckinValidationError as exc:
            problems.append(("checkin-evaluation", f"row {number}: {exc}"))
    return problems


# ── COM-300 AC-1 necessity gate ─────────────────────────────────────────────

_NECESSITY_HEADING = "## Necessity Gate"
_NECESSITY_COLUMNS = (
    "A/C", "outcome_removed", "runtime_basis", "production_writer",
    "production_reader", "reaching_path", "adversary / harm", "verdict",
)
_NECESSITY_TRIO_COLUMNS = ("production_writer", "production_reader", "reaching_path")
_NECESSITY_VALID_VERDICTS = {"PROCEED", "MINOR RESCOPE", "RETHINK PREMISE"}
# "and the like" per the design's rule 3 — a fixed, deliberately small set; catching every
# possible deferral phrasing is a judgment call for /jPlan review, not this mechanical lint.
_NECESSITY_DEFERRAL_MARKERS = ("none yet", "future ticket", "tbd", "planned", "not yet built")
_NECESSITY_NA_RE = re.compile(r"^n/a\s*[-–—]\s*\S", re.IGNORECASE)
_NECESSITY_BARE_NA_RE = re.compile(r"^n/a$", re.IGNORECASE)
# COM-300 AC-7c — closed-choice "none-yet" auto-verdicts RETHINK PREMISE; distinct from the
# space-separated "none yet" deferral marker above.
_NECESSITY_NONE_YET_RE = re.compile(r"^none-yet\b", re.IGNORECASE)

# COM-300 AC-7 — presence-required-for-new-work cutover. The live event log holds 64 tickets
# with a first 3.implementation.* transition; the two most recent are COM-307
# (2026-07-27T03:33:48Z) and COM-300 itself (2026-07-27T05:12:03Z). 2026-07-28 grandfathers
# every in-flight ticket and binds only genuinely new work.
_NECESSITY_PRESENCE_CUTOVER = "2026-07-28T00:00:00Z"


def _necessity_is_deferral(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _NECESSITY_DEFERRAL_MARKERS)


def _necessity_presence_required(ticket: str | None, repo_root: Path) -> bool:
    """COM-300 AC-7 — eligible for presence-required-for-new-work when ``ticket``'s EARLIEST
    ``3.implementation.*`` transition in ``.jswarm/ops/plan-status-events.ndjson`` is on or
    after ``_NECESSITY_PRESENCE_CUTOVER``. Earliest, not latest, so in-flight work is
    grandfathered and a copied/incident-born plan can't evade it by a later transition.
    Fails open (NFR-300-1, ineligible work pays nothing) on a missing ticket, missing/empty/
    unreadable event log, or a ticket with no implementation transition; bad lines are
    skipped individually so one malformed line can't disable an otherwise eligible control."""
    if not ticket:
        return False
    events_path = repo_root / ".jswarm" / "ops" / "plan-status-events.ndjson"
    try:
        raw = events_path.read_text(encoding="utf-8")
    except OSError:
        return False

    earliest: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        record_ticket = record.get("ticket_key")
        to_status = record.get("to_status")
        timestamp = record.get("timestamp_utc")
        if not isinstance(to_status, str) or not to_status.startswith("3.implementation."):
            continue
        if record_ticket != ticket or not isinstance(timestamp, str):
            continue
        if earliest is None or timestamp < earliest:
            earliest = timestamp

    if earliest is None:
        return False
    return earliest >= _NECESSITY_PRESENCE_CUTOVER


def _necessity_is_test_path(path_str: str) -> bool:
    """True when ``path_str`` lives under a test tree (a dir named test/tests, or a
    test_*.py / *_test.py filename) — such a reader never resolves (design §4 rule 2)."""
    parts = Path(path_str).parts
    if any(part in ("test", "tests") for part in parts[:-1]):
        return True
    name = Path(path_str).name
    return bool(name.startswith("test_") or name.endswith("_test.py"))


def _resolve_necessity_reader(value: str, repo_root: Path) -> str | None:
    """Return ``None`` when ``production_reader`` resolves, else a one-line reason it does
    not: the path must exist and must not be a test-tree file, and when a ``:symbol`` is
    given it must appear (as a whole word) in that path's own file — a symbol declared by
    its own non-test definition legitimately resolves (design §4 rule 2 correction note).
    Mirrors ``_resolve_evidence_path``'s markdown code-span stripping: a plan cell commonly
    wraps the ``path:symbol`` locator in backticks for rendering."""
    value = value.strip().strip("`")
    path_part, _, symbol = value.partition(":")
    path_part = path_part.strip()
    symbol = symbol.strip()
    candidate = Path(path_part)
    if not candidate.is_absolute():
        candidate = Path(repo_root) / candidate
    if not candidate.is_file():
        return f"path does not exist: {path_part}"
    if _necessity_is_test_path(path_part):
        return f"path is under a test tree, not production: {path_part}"
    if symbol:
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            return f"path unreadable: {path_part}"
        if not re.search(rf"\b{re.escape(symbol)}\b", text):
            return f"symbol {symbol!r} not found in {path_part}"
    return None


def _validate_necessity_row(cells: list[str], row_num: int, repo_root: Path) -> list[tuple[str, str]]:
    """Validate one ``## Necessity Gate`` row against the four mechanical blocking rules.
    One problem per malformed cell, except a wrong cell count — which is unrecoverable —
    short-circuits the row with a single diagnostic naming every expected column."""
    problems: list[tuple[str, str]] = []
    if len(cells) != len(_NECESSITY_COLUMNS):
        problems.append((
            "necessity",
            f"row {row_num}: {len(cells)} cells, expected {len(_NECESSITY_COLUMNS)} "
            f"({', '.join(_NECESSITY_COLUMNS)})",
        ))
        return problems

    mapped = dict(zip(_NECESSITY_COLUMNS, cells))

    for column in _NECESSITY_COLUMNS:
        if not mapped[column]:
            problems.append(("necessity", f"row {row_num}: empty required cell '{column}'"))
    if problems:
        return problems

    for column in _NECESSITY_COLUMNS:
        if _NECESSITY_BARE_NA_RE.match(mapped[column]):
            problems.append((
                "necessity",
                f"row {row_num}: '{column}' is a bare 'n/a' — give the reason ('n/a — <reason>')",
            ))

    verdict = mapped["verdict"]
    if verdict not in _NECESSITY_VALID_VERDICTS:
        problems.append((
            "necessity",
            f"row {row_num}: verdict {verdict!r} is not one of PROCEED, MINOR RESCOPE, "
            "RETHINK PREMISE",
        ))
    elif verdict == "RETHINK PREMISE":
        problems.append((
            "necessity",
            f"row {row_num}: verdict is RETHINK PREMISE — the plan must be revised before this "
            "criterion can proceed; no implementation review can clear it",
        ))

    for column in _NECESSITY_TRIO_COLUMNS:
        value = mapped[column]
        if _NECESSITY_BARE_NA_RE.match(value) or _NECESSITY_NA_RE.match(value):
            continue  # explicitly opted out with a reason — nothing further to check
        if _NECESSITY_NONE_YET_RE.match(value):
            problems.append((
                "necessity",
                f"row {row_num}: '{column}' is 'none-yet' ({value!r}) — RETHINK PREMISE: this "
                "criterion has no resolving surface and the plan must be revised before it can "
                "proceed",
            ))
            continue
        if _necessity_is_deferral(value):
            problems.append((
                "necessity",
                f"row {row_num}: '{column}' defers instead of building ({value!r}) — drop the "
                "criterion from this ticket, don't record an intention",
            ))
            continue
        if column == "production_reader":
            reason = _resolve_necessity_reader(value, repo_root)
            if reason is not None:
                problems.append(("necessity", f"row {row_num}: 'production_reader' {reason}"))

    return problems


def lint_necessity_gate(
    plan_text: str, *, repo_root: Path | str = ".", ticket: str | None = None,
) -> list[tuple[str, str]]:
    """COM-300 AC-1 — validate every row of a plan's ``## Necessity Gate`` table (contract:
    ``.jswarm/plans/COM-300/designs/COM-300.design.ac1-necessity-gate.md``) against the four
    mechanical blocking rules: a RETHINK PREMISE verdict, an unresolved production_reader, a
    deferral value in the production_writer/production_reader/reaching_path trio, or a
    malformed row.

    Validate-if-present (NFR-300-1): fail-open on an ABSENT heading — a plan that authors no
    ``## Necessity Gate`` section costs nothing here, UNLESS ``ticket`` is supplied and is
    presence-required-for-new-work eligible (COM-300 AC-7: its earliest
    ``3.implementation.*`` transition is on or after ``_NECESSITY_PRESENCE_CUTOVER``), in
    which case the absent heading itself is the single blocking problem. ``ticket=None``
    preserves prior validate-if-present behavior exactly. Fail-LOUD when the heading IS
    present but the table underneath is missing or empty (prose-only, or header/separator
    with zero data rows), mirroring ``lint_checkin_reviews``. Nothing else gates: no
    threshold, no ratio, no score.
    """
    repo_root = Path(repo_root)
    if not MG._has_heading(plan_text, _NECESSITY_HEADING):
        if _necessity_presence_required(ticket, repo_root):
            return [(
                "necessity",
                f"{_NECESSITY_HEADING!r} is required — {ticket} entered implementation on or "
                "after the presence cutover and must author this section before new work "
                "proceeds",
            )]
        return []

    rows = RW.parse_index(plan_text, _NECESSITY_HEADING)
    if not rows:
        if not _table_present_under_heading(plan_text, _NECESSITY_HEADING):
            return [(
                "necessity",
                f"{_NECESSITY_HEADING!r} heading present but no table authored (prose only)",
            )]
        return [(
            "necessity",
            f"{_NECESSITY_HEADING!r} table present but has no data rows (empty table)",
        )]

    problems: list[tuple[str, str]] = []
    for n, cells in enumerate(rows, 1):
        problems.extend(_validate_necessity_row(cells, n, repo_root))
    return problems


def lint_slices(ticket: str, repo_root: Path | str = ".") -> LintResult:
    """R2 IO wrapper — resolve a ticket's plan + slices, then lint. Fail-open: a missing plan
    yields ok=True (nothing to lint). Also validates any ``## Check-In Reviews`` table present
    in the ticket's ``.state.md`` (COM-300 F1, ``lint_checkin_reviews``) — an absent table is
    free; a present malformed row fails this lint loud. Also validates any ``## Necessity
    Gate`` table present in the plan itself (COM-300 AC-1, ``lint_necessity_gate``). Also
    validates any ``advisor-acceptance-*.json`` artifacts present in the ticket's folder
    (COM-300 T4.3, ``lint_advisor_acceptance``) — validate-if-present, an absent artifact
    is free."""
    repo_root = Path(repo_root)
    plan_path, tdir, reason = _resolve_paths(ticket, repo_root)
    plan_text: str | None = None
    if plan_path is None:
        result = LintResult(ok=True, ticket=ticket,
                            problems=([("plan", reason)] if reason else []))
    else:
        plan_text = _read(plan_path)
        if plan_text is None:
            result = LintResult(ok=True, ticket=ticket, problems=[("plan", "unreadable-plan")])
        else:
            result = lint_slices_text(
                plan_text, ticket=ticket,
                uat_slice_text=_read(tdir / f"{ticket}.uat-scenarios.md"),
                nfr_slice_text=_read(tdir / f"{ticket}.nfr.md"),
            )

    if tdir is not None:
        checkin_problems = lint_checkin_reviews(
            _read(tdir / f"{ticket}.state.md"), ticket_dir=tdir, repo_root=repo_root,
        )
        if checkin_problems:
            result.problems = [*result.problems, *checkin_problems]
            result.ok = False

        advisor_problems = lint_advisor_acceptance(ticket_dir=tdir)
        if advisor_problems:
            result.problems = [*result.problems, *advisor_problems]
            result.ok = False

        evaluation_problems = lint_checkin_evaluation_rows(ticket, project_root=repo_root)
        if evaluation_problems:
            result.problems = [*result.problems, *evaluation_problems]
            result.ok = False

    if plan_text is not None:
        necessity_problems = lint_necessity_gate(plan_text, repo_root=repo_root, ticket=ticket)
        if necessity_problems:
            result.problems = [*result.problems, *necessity_problems]
            result.ok = False

    return result


# ── R5 fleet scan ────────────────────────────────────────────────────────────

@dataclass
class FleetReport:
    scanned: int
    drift: list[TicketAudit] = field(default_factory=list)
    by_verdict: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "drift_count": len(self.drift),
            "by_verdict": self.by_verdict,
            "drift": [a.to_dict() for a in self.drift],
        }


def scan_fleet(repo_root: Path | str = ".") -> FleetReport:
    """R5 — audit every canonical plan and surface should-have-counts tickets in drift. A
    pre-implementation ticket at 0/N is never reported (R6)."""
    repo_root = Path(repo_root)
    by_verdict: dict[str, int] = {}
    drift: list[TicketAudit] = []
    scanned = 0
    for ticket, _path in C.iter_canonical_plan_files(repo_root):
        scanned += 1
        try:
            audit = audit_ticket(ticket, repo_root)
        except Exception:  # noqa: BLE001 — one bad plan never kills the sweep
            by_verdict["error"] = by_verdict.get("error", 0) + 1
            continue
        by_verdict[audit.verdict] = by_verdict.get(audit.verdict, 0) + 1
        if audit.is_drift:
            drift.append(audit)
    drift.sort(key=lambda a: a.ticket)
    return FleetReport(scanned=scanned, drift=drift, by_verdict=by_verdict)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _print_audit(a: TicketAudit) -> None:
    print(
        f"audit {a.ticket}: stage={a.stage_class}({a.lifecycle_stage or '-'}) "
        f"applicable={a.applicable} matrix={a.matrix_bearing} "
        f"uat={a.uat_count or '-'} nfr={a.nfr_count or '-'} "
        f"results(uat={a.has_uat_results} nfr={a.has_nfr_results}) "
        f"verdict={a.verdict}"
    )
    for reason in a.unseedable_reasons:
        print(f"  unseedable: {reason}")
    for doc in a.missing_result_docs:
        print(f"  missing-result-doc: {doc}")
    for note in a.notes:
        print(f"  note: {note}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lifecycle-audit")
    parser.add_argument("--ticket", help="Audit/gate a single ticket")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--stage", choices=("implement", "close"),
                        help="Apply the R4 (implement) or R3 (close) gate and exit non-zero on block")
    parser.add_argument("--lint", action="store_true",
                        help="R2 authoring lint of --ticket; exit non-zero on a problem")
    parser.add_argument("--scan", action="store_true", help="R5 fleet drift scan")
    parser.add_argument("--json", action="store_true", help="JSON output")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 0

    try:
        if args.scan:
            rep = scan_fleet(args.repo_root)
            if args.json:
                print(json.dumps(rep.to_dict(), separators=(",", ":")))
            else:
                print(f"lifecycle-scan: scanned={rep.scanned} drift={len(rep.drift)} "
                      f"by_verdict={rep.by_verdict}")
                for a in rep.drift:
                    _print_audit(a)
            return 0  # the scan is a visibility metric — never fails the command

        if not args.ticket:
            print("lifecycle-audit: provide --ticket KEY or --scan")
            return 0

        if args.lint:
            res = lint_slices(args.ticket, args.repo_root)
            if args.json:
                print(json.dumps(res.to_dict(), separators=(",", ":")))
            else:
                if res.ok:
                    print(f"lint {args.ticket}: OK")
                else:
                    print(f"lint {args.ticket}: BLOCK")
                    for kind, reason in res.problems:
                        print(f"  {kind}: {reason}")
            return 0 if res.ok else 1

        audit = audit_ticket(args.ticket, args.repo_root)
        if args.stage:
            gate = gate_close(audit) if args.stage == "close" else gate_implement(audit)
            if args.json:
                out = gate.to_dict()
                out["audit"] = audit.to_dict()
                print(json.dumps(out, separators=(",", ":")))
            else:
                print(f"gate {args.stage} {args.ticket}: "
                      f"{'OK' if gate.ok else 'BLOCK'} {gate.mode}: {gate.reason}")
            return 0 if gate.ok else 1

        if args.json:
            print(json.dumps(audit.to_dict(), separators=(",", ":")))
        else:
            _print_audit(audit)
        return 0
    except ImportError as exc:
        # COM-300: deliberately NOT fail-open. Every module-scope import here resolves before
        # main() runs, so an ImportError reaching this point can only come from a validator
        # imported at its seam — i.e. an artifact that REQUIRES that validator is present.
        # Returning 0 with a warning would silently skip an authored control, which is exactly
        # the "control quietly disabled" failure these lints exist to catch. Fail loud instead.
        print(f"lifecycle-audit: FATAL: validator required by a present artifact "
              f"could not be loaded: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI is fail-open by contract
        print(f"lifecycle-audit: warning: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
