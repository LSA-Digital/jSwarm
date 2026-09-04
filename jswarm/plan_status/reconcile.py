#!/usr/bin/env python3
"""A/C 11: rebuild the plan-status registry CACHE from plan-file frontmatter.

Frontmatter is canonical (Oracle Concern #3); the registry is a derived cache.
Reconcile scans plan files, syncs each cached `plan_status` to its frontmatter
value (preserving existing `history` and `jira_retry_queue`), and reports drift,
invalid values, and orphan registry entries (no backing plan file).

Default is APPLY (rebuild the cache). Pass dry_run=True for a report-only run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
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
from jswarm.plan_status import ladder as L
from jswarm.plan_status import registry as R
from jswarm.plan_status import state as S


_SECTION_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_NFR_SECTION_HEADING = "## A/C-to-NFR Traceability Matrix"
_NFR_STRATEGY_HEADING = "### NFR Validation Strategy"
_NFR_DERIVED_LINE_RE = re.compile(r"^\*\*NFR status \(derived\):\*\*\s*\d+/\d+\s*$", re.MULTILINE)
# AC-9: the UAT-scenario-keyed matrix (one row per UAT scenario; status
# aggregates that scenario's tests — 1:many). Distinct from the A/C-keyed
# "## A/C-to-Test Traceability Matrix".
_UAT_SECTION_HEADING = "## UAT-Scenario Traceability Matrix"


def _heading_line_pos(text: str, heading: str) -> int:
    """Offset of the first line that IS ``heading`` (allowing trailing spaces), or -1.

    Anchored to a full LINE so a prose/code mention of the heading text never binds
    (critic-xhigh F4/recheck-F3). Shared by section + strategy-subsection lookups.
    """
    match = re.compile(r"^" + re.escape(heading) + r"[ \t]*$", re.MULTILINE).search(text)
    return match.start() if match else -1


def _find_section_bounds(text: str, heading: str) -> tuple[int, int] | None:
    start = _heading_line_pos(text, heading)
    if start == -1:
        return None
    heading_level = len(heading) - len(heading.lstrip("#"))
    body_start = text.find("\n", start)
    search_start = len(text) if body_start == -1 else body_start + 1
    for match in _SECTION_HEADING_RE.finditer(text, search_start):
        if len(match.group(1)) <= heading_level:
            return start, match.start()
    return start, len(text)


def _iter_nfr_matrix_status_cells(section: str):
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue  # a status-bearing data row needs an id column + a status column
        first_cell_marker = cells[0].replace("-", "").replace(":", "").strip()
        if cells[0] == "A/C" or first_cell_marker == "":
            continue
        yield cells[-1]


def _count_nfr_matrix_rows(section: str) -> tuple[int, int]:
    total = 0
    green = 0
    for status in _iter_nfr_matrix_status_cells(section):
        total += 1
        if "🟢" in status:
            green += 1
    return green, total


def _iter_matrix_status_cells(section: str):
    seen_separator = False
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue  # a status-bearing data row needs an id column + a status column
        is_separator = all(cell != "" and set(cell) <= set("-: ") for cell in cells)
        if not seen_separator:
            # Still in the header region: the first separator row ends it.
            if is_separator:
                seen_separator = True
            continue
        if is_separator:
            continue  # defensive: a stray separator never counts as data
        yield cells[-1]


def _count_matrix_status_rows(section: str) -> tuple[int, int]:
    """(green, total) of a markdown table's DATA rows, identifying the data region by the
    header separator row (``| --- | --- |``).

    Header rows (above the separator) and the separator itself are skipped regardless of
    their first-cell label, so this works for any status table — including the
    UAT-scenario matrix whose header first cell is ``UAT scenario`` (not ``A/C``).
    A row is *green* when its last cell contains 🟢. (NFR keeps its own
    counter ``_count_nfr_matrix_rows`` so that contract is untouched.)
    """
    total = 0
    green = 0
    for status in _iter_matrix_status_cells(section):
        total += 1
        if "🟢" in status:
            green += 1
    return green, total


def _insert_or_replace_nfr_derived_line(strategy: str, derived_line: str) -> str:
    lines = strategy.splitlines(keepends=True)
    if not lines:
        return f"{derived_line}\n"

    kept = [line for line in lines if not _NFR_DERIVED_LINE_RE.match(line.strip())]
    if not kept:
        return f"{derived_line}\n"

    insert_at = 1
    while insert_at < len(kept) and kept[insert_at].strip() == "":
        insert_at += 1
    kept[insert_at:insert_at] = [f"{derived_line}\n"]
    return "".join(kept)


def _weighted_color_from_statuses(statuses) -> str | None:
    total = 0
    weight_sum = 0
    for status in statuses:
        total += 1
        weight_sum += L.weight_from_status(status)
    if total == 0:
        return None
    return L.band_glyph(weight_sum / total)


def _nfr_counts(body: str) -> tuple[int, int] | None:
    """(green, total) of the A/C-to-NFR matrix rows. When a ``### NFR Validation Strategy``
    subsection is present it bounds the counted region (rows *before* it); when absent the
    whole matrix section is counted (AC-17 F2 — a present-but-empty matrix derives
    0/0, not omitted). Returns None only when the matrix SECTION itself is absent.

    Single source of truth for BOTH the body-derived ``**NFR status (derived):**`` line
    and the ``nfr_complete`` frontmatter count (AC-9) — so the two can never
    disagree.
    """
    matrix_bounds = _find_section_bounds(body, _NFR_SECTION_HEADING)
    if matrix_bounds is None:
        return None
    matrix_start, matrix_end = matrix_bounds
    matrix_section = body[matrix_start:matrix_end]
    # Anchored (line-level) — a prose mention of the strategy heading inside the matrix
    # section must not truncate the counted region (critic-xhigh recheck-F3).
    strategy_rel_start = _heading_line_pos(matrix_section, _NFR_STRATEGY_HEADING)
    if strategy_rel_start == -1:
        # No strategy subsection to bound the region — count the whole matrix section (there is
        # no strategy table to mis-count). A present-but-empty NFR matrix then derives 0/0 (HUD
        # shows the metric) instead of being omitted as `—` (AC-17 F2; matches UAT).
        return _count_nfr_matrix_rows(matrix_section)
    return _count_nfr_matrix_rows(matrix_section[:strategy_rel_start])


def _nfr_matrix_count_section(body: str) -> str | None:
    matrix_bounds = _find_section_bounds(body, _NFR_SECTION_HEADING)
    if matrix_bounds is None:
        return None
    matrix_start, matrix_end = matrix_bounds
    matrix_section = body[matrix_start:matrix_end]
    strategy_rel_start = _heading_line_pos(matrix_section, _NFR_STRATEGY_HEADING)
    if strategy_rel_start == -1:
        return matrix_section
    return matrix_section[:strategy_rel_start]


def _derive_nfr_complete(body: str) -> str | None:
    """``nfr_complete`` value (``green/total``) mirroring the body NFR line, or None when
    no NFR matrix is present (field omitted ⇒ HUD fail-safe dash)."""
    counts = _nfr_counts(body)
    return None if counts is None else f"{counts[0]}/{counts[1]}"


def _derive_nfr_color(body: str) -> str | None:
    section = _nfr_matrix_count_section(body)
    if section is None:
        return None
    return _weighted_color_from_statuses(_iter_nfr_matrix_status_cells(section))


def _derive_uat_complete(body: str) -> str | None:
    """``uat_complete`` value (``green/total``) over the UAT-scenario matrix — one row per
    UAT scenario, status aggregates that scenario's tests (1:many). None when the matrix
    is absent (field omitted ⇒ HUD fail-safe dash)."""
    bounds = _find_section_bounds(body, _UAT_SECTION_HEADING)
    if bounds is None:
        return None
    start, end = bounds
    green, total = _count_matrix_status_rows(body[start:end])
    return f"{green}/{total}"


def _derive_uat_color(body: str) -> str | None:
    bounds = _find_section_bounds(body, _UAT_SECTION_HEADING)
    if bounds is None:
        return None
    start, end = bounds
    return _weighted_color_from_statuses(_iter_matrix_status_cells(body[start:end]))


def _reconcile_nfr_status(body: str) -> str:
    """Derive the body-only NFR matrix status line without touching frontmatter."""
    counts = _nfr_counts(body)
    if counts is None:
        return body
    green, total = counts

    matrix_start, matrix_end = _find_section_bounds(body, _NFR_SECTION_HEADING)
    matrix_section = body[matrix_start:matrix_end]
    strategy_rel_start = _heading_line_pos(matrix_section, _NFR_STRATEGY_HEADING)
    strategy_abs_start = matrix_start + strategy_rel_start
    strategy_bounds = _find_section_bounds(body[strategy_abs_start:matrix_end], _NFR_STRATEGY_HEADING)
    if strategy_bounds is None:
        return body
    strategy_start = strategy_abs_start + strategy_bounds[0]
    strategy_end = strategy_abs_start + strategy_bounds[1]
    strategy_section = body[strategy_start:strategy_end]
    new_strategy = _insert_or_replace_nfr_derived_line(
        strategy_section,
        f"**NFR status (derived):** {green}/{total}",
    )
    return f"{body[:strategy_start]}{new_strategy}{body[strategy_end:]}"


def normalize_plan_file(path) -> dict:
    """single normalization invariant for a plan file's frontmatter.

    Every frontmatter mutator (CLI write, post-edit hook, backfill) routes through this
    so ``status``/``phase``/``ac_complete`` self-heal and the block is forced to line 1 +
    canonical order. Pipeline (spec §4):

        hoist_to_top
          → if is_valid_state(plan_status) and not null:  status = derive_merge_status,
                                                           phase  = derive_phase(.., body)
          → ac_complete = derive_ac_complete(body)         (any canonical plan)
          → upsert derivable fields / remove non-derivable ones
          → canonicalize
          → write-if-changed

    Feature/legacy/absent ``plan_status`` plans keep authored ``status``/``phase`` (§6.0
    guard) but still get position/order + ``ac_complete``. Returns a summary dict; raises
    nothing for ordinary content (callers still wrap for IO safety / fail-open).
    """
    path = Path(path)
    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        return {"changed": False, "hoisted": False, "fields_written": [], "removed": [],
                "reason": "unreadable"}
    res = normalize_text(original)
    if res["changed"]:
        path.write_text(res["new_text"], encoding="utf-8")
    return {k: res[k] for k in ("changed", "hoisted", "fields_written", "removed")}


def normalize_text(original: str) -> dict:
    """Pure (no IO) core of ``normalize_plan_file`` — compute the normalized text + summary.

    Exposed so the backfill can preview changes (dry-run) without writing. Same pipeline
    and §6.0 guard as ``normalize_plan_file``; returns ``new_text`` plus change metadata.
    """
    # FAIL-OPEN on a present-but-invalid frontmatter block: never rewrite a plan whose
    # leading YAML is broken (it would derive from empty defaults and mangle the block).
    # No destructive write — return the input unchanged (NFR-012).
    if FM.frontmatter_is_invalid(original):
        return {"new_text": original, "changed": False, "hoisted": False,
                "fields_written": [], "removed": [], "reason": "invalid-frontmatter"}

    text = FM.hoist_to_top(original)
    hoisted = text != original
    if FM.frontmatter_is_invalid(text):  # hoist could surface a broken block too
        return {"new_text": original, "changed": False, "hoisted": False,
                "fields_written": [], "removed": [], "reason": "invalid-frontmatter"}
    fm = FM.read_frontmatter_text(text)
    _fm_lines, body_lines, _all = FM.split_frontmatter(text)
    body = "".join(body_lines)

    updates: dict[str, object] = {}
    removals: set[str] = set()

    plan_status = fm.get("plan_status")
    phase = None
    if (isinstance(plan_status, str) and plan_status != S.NULL_STATE
            and S.is_valid_state(plan_status)):
        phase = S.derive_phase(plan_status, body)
    if phase:
        # Well-formed state: derive status + phase ATOMICALLY (§6.0). phase is
        # non-None for every well-formed valid state; it is None only for a malformed
        # implementation string (e.g. "3.implementation.phase_x" — a non-numeric phase that
        # category_of still admits). Tying both to derive_phase keeps the guard's intent —
        # never half-derive (status written while phase left authored).
        updates["status"] = S.derive_merge_status(plan_status)
        updates["phase"] = phase
    # else: Feature/legacy/absent OR malformed-impl plan_status -> leave authored
    #       status/phase untouched (no raise, no half-derive).

    ac = S.derive_ac_complete(body)
    if ac is not None:
        updates["ac_complete"] = ac
    elif "ac_complete" in fm:
        removals.add("ac_complete")

    # AC-9: derived completion counts from the plan's own traceability matrices.
    # Counting the matrices already in the plan body (NOT the ticket-local test files)
    # keeps this deterministic + project-agnostic — the determinism boundary. Keeping
    # those matrix rows truthful from local assets is /jPrecompact's job (AC-10).
    for field_name, value in (("nfr_complete", _derive_nfr_complete(body)),
                              ("nfr_color", _derive_nfr_color(body)),
                              ("uat_complete", _derive_uat_complete(body)),
                              ("uat_color", _derive_uat_color(body))):
        if value is not None:
            updates[field_name] = value
        elif field_name in fm:
            removals.add(field_name)

    new_body = _reconcile_nfr_status(body)
    if new_body != body:
        _fm_lines, _body_lines, all_lines = FM.split_frontmatter(text)
        fm_end = len(all_lines) - len(body_lines)
        text = "".join(all_lines[:fm_end]) + new_body

    new_text = text
    if updates:
        new_text = FM.upsert_keys_text(new_text, updates)
    if removals:
        new_text = FM.remove_keys_text(new_text, removals)
    new_text = FM.canonicalize(new_text)

    return {"new_text": new_text, "changed": new_text != original, "hoisted": hoisted,
            "fields_written": sorted(updates.keys()), "removed": sorted(removals)}


@dataclass
class ReconcileReport:
    project_key: str
    scanned: int = 0
    synced: list = field(default_factory=list)       # tickets whose cache was updated
    drift: list = field(default_factory=list)        # registry != frontmatter (pre-sync)
    invalid: list = field(default_factory=list)      # invalid frontmatter plan_status
    missing_frontmatter: list = field(default_factory=list)  # plan file lacks plan_status
    orphans: list = field(default_factory=list)      # registry ticket with no plan file
    pruned: list = field(default_factory=list)        # orphans removed (opt-in)
    dry_run: bool = False
    report_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "project_key": self.project_key, "scanned": self.scanned,
            "synced": self.synced, "drift": self.drift, "invalid": self.invalid,
            "missing_frontmatter": self.missing_frontmatter, "orphans": self.orphans,
            "pruned": self.pruned, "dry_run": self.dry_run, "report_path": self.report_path,
        }


def reconcile(plans_dir: Path, *, project_key: str = "", dry_run: bool = False,
              prune_orphans: bool = False, repo_root: Path | None = None) -> ReconcileReport:
    plans_dir = Path(plans_dir)
    rep = ReconcileReport(project_key=project_key, dry_run=dry_run)
    existing = R.load_registry(plans_dir, project_key=project_key)
    seen_tickets = set()

    for ticket, path in C.iter_plan_files(plans_dir):
        rep.scanned += 1
        seen_tickets.add(ticket)
        fm = FM.read_frontmatter(path)
        plan_status = fm.get("plan_status")
        if plan_status is None:
            rep.missing_frontmatter.append(ticket)
            continue
        if not isinstance(plan_status, str) or not S.is_valid_state(plan_status):
            rep.invalid.append({"ticket": ticket, "value": plan_status})
            continue
        cached = existing["tickets"].get(ticket, {}).get("plan_status")
        if cached != plan_status:
            rep.drift.append({"ticket": ticket, "registry": cached, "frontmatter": plan_status})
        if not dry_run:
            rel = str(path.relative_to(plans_dir.parent.parent)) if path.is_relative_to(plans_dir.parent.parent) else str(path)
            res = R.set_cache(plans_dir, ticket=ticket, plan_status=plan_status,
                              plan_file=rel, source="reconcile", project_key=project_key)
            if res["action"] == "synced":
                rep.synced.append(ticket)

    for ticket in existing["tickets"]:
        if ticket not in seen_tickets:
            rep.orphans.append(ticket)

    # Orphans = registry entries with no backing plan file. Reported always; pruned
    # only on explicit opt-in (history is valuable, so default keeps them — Critic A/C 11).
    if prune_orphans and not dry_run and rep.orphans:
        data = R.load_registry(plans_dir, project_key=project_key)
        for ticket in rep.orphans:
            data["tickets"].pop(ticket, None)
        R.save_registry(plans_dir, data)
        rep.pruned = list(rep.orphans)

    if not dry_run:
        rep.report_path = str(_write_report(repo_root or plans_dir.parent.parent, rep))
    return rep


def _write_report(repo_root: Path, rep: "ReconcileReport") -> Path:
    rpath = Path(repo_root) / "docs" / "plans" / "evidence" / f"plan-status-reconcile-{time.strftime('%Y%m%d')}.md"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Plan-status reconcile — {time.strftime('%Y-%m-%d')}", "",
        f"Project: {rep.project_key} | scanned: {rep.scanned} | synced: {len(rep.synced)} | "
        f"drift: {len(rep.drift)} | invalid: {len(rep.invalid)} | "
        f"missing_frontmatter: {len(rep.missing_frontmatter)} | orphans: {len(rep.orphans)} | "
        f"pruned: {len(rep.pruned)}", "",
    ]
    for title, items in [("Drift (registry != frontmatter)", rep.drift),
                         ("Invalid frontmatter", rep.invalid),
                         ("Missing frontmatter (no plan_status)", rep.missing_frontmatter),
                         ("Orphans (registry, no plan file)", rep.orphans),
                         ("Pruned orphans", rep.pruned)]:
        if items:
            lines.append(f"## {title} ({len(items)})")
            for it in items:
                lines.append(f"- {json.dumps(it) if not isinstance(it, str) else it}")
            lines.append("")
    rpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rpath


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Rebuild plan-status registry from frontmatter")
    p.add_argument("--project-root", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--prune-orphans", action="store_true",
                   help="Remove registry entries with no backing plan file (default: keep + report)")
    args = p.parse_args(argv)
    cfg = C.resolve_config(Path(args.project_root) if args.project_root else Path.cwd())
    if not cfg.plans_dir:
        print(json.dumps({"error": "config-unresolved"}))
        return 0
    rep = reconcile(cfg.plans_dir, project_key=cfg.project_key or "", dry_run=args.dry_run,
                    prune_orphans=args.prune_orphans, repo_root=cfg.repo_root)
    print(json.dumps(rep.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
