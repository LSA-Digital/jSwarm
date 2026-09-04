"""PM impact-question surface (COM-234 Phase 6, spec #295 S7.8).

Answers a free-text PM-style question ("What breaks if X can run before Y?")
by matching it against ``impact_tag`` records' ``product_question_terms``,
resolving the seam-spine records those tags apply to, and reporting the
paths/carriers/UI-surfaces/tests/scenarios those records touch. Coverage of
a relevant seam requires BOTH evidence halves: a curated ``impact_tag``
match AND confirmed trace-observation coverage. A seam missing either half
gets its own distinctly-worded ``coverage_caveats`` entry naming that
record -- the answer must never claim full coverage when some relevant
evidence is merely inferred from the raw seam-spine records, or when a
tag match exists but no trace observation ever confirmed it.

Layer A generic tooling: this module carries no project-specific
vocabulary. Feature tags, seam ids, carrier ids, and UI-surface ids are all
read from the caller-supplied fixture; nothing project-specific is
hard-coded here.
"""

from __future__ import annotations

from typing import Any

__all__ = ["answer_impact_question"]

SCHEMA_VERSION = "code_overview.impact.v1"


def _matched_impact_tags(impact_tags: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
    question_lower = question.lower()
    matched: list[dict[str, Any]] = []
    for tag in impact_tags:
        terms = [str(term) for term in (tag.get("product_question_terms") or [])]
        if terms and all(term.lower() in question_lower for term in terms):
            matched.append(tag)
    return matched


def _relevant_records(
    spine_records: list[dict[str, Any]],
    matched_tags: list[dict[str, Any]],
    impact_tags: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matched_tag_names = {tag.get("tag") for tag in matched_tags if tag.get("tag")}
    if matched_tag_names:
        return [
            record
            for record in spine_records
            if matched_tag_names & set(record.get("feature_tags") or [])
        ]
    # Fallback: the question matched no impact_tag by product_question_terms
    # (e.g. an unmapped question); fall back to any record any impact_tag
    # references at all, so a partial answer is still possible.
    referenced_ids = {
        record_id for tag in impact_tags for record_id in (tag.get("applies_to_record_ids") or [])
    }
    if referenced_ids:
        return [record for record in spine_records if record.get("record_id") in referenced_ids]
    return list(spine_records)


def _extend_unique(target: list[str], values: Any) -> None:
    for value in values or []:
        if value not in target:
            target.append(value)


def _trace_covered_record_ids(fixture: dict[str, Any]) -> set[str]:
    """Record ids confirmed as trace-observed by either evidence source.

    Coverage of a seam requires an actual trace-observation half, not merely
    an ``impact_tag`` match. A record is trace-covered only when the fixture
    explicitly confirms it -- via ``trace_coverage.covered_record_ids`` or a
    ``trace_observation_summaries`` entry with at least one observed run for
    that seam. Absent trace evidence entirely is never inferred as covered
    (fail-closed), matching the same fact-gating rule used for debt-overlay
    predicates.
    """

    covered: set[str] = set()
    trace_coverage = fixture.get("trace_coverage")
    if isinstance(trace_coverage, dict):
        covered.update(str(record_id) for record_id in (trace_coverage.get("covered_record_ids") or []))
    for summary in fixture.get("trace_observation_summaries") or []:
        if not isinstance(summary, dict):
            continue
        seam_id = summary.get("seam_id")
        observed_count = summary.get("observed_count")
        if seam_id and isinstance(observed_count, (int, float)) and observed_count > 0:
            covered.add(str(seam_id))
    return covered


def answer_impact_question(fixture: dict[str, Any], question: str) -> dict[str, Any]:
    spine_records = [record for record in (fixture.get("spine_records") or []) if isinstance(record, dict)]
    impact_tags = [tag for tag in (fixture.get("impact_tags") or []) if isinstance(tag, dict)]

    matched_tags = _matched_impact_tags(impact_tags, question)
    relevant_records = _relevant_records(spine_records, matched_tags, impact_tags)

    impact_tag_covered_ids = {
        record_id for tag in impact_tags for record_id in (tag.get("applies_to_record_ids") or [])
    }
    trace_covered_ids = _trace_covered_record_ids(fixture)

    impacted_paths: list[str] = []
    impacted_seams: list[str] = []
    impacted_carriers: list[str] = []
    impacted_ui_surfaces: list[str] = []
    impacted_tests_scenarios: list[str] = []
    known_debt_freeze_risks: list[str] = []
    impact_tag_uncovered_records: list[dict[str, Any]] = []
    trace_uncovered_records: list[dict[str, Any]] = []

    for record in relevant_records:
        record_id = record.get("record_id")
        if record_id and record_id not in impacted_seams:
            impacted_seams.append(record_id)
        _extend_unique(impacted_paths, record.get("path_ids"))
        _extend_unique(impacted_carriers, record.get("carrier_cell_ids"))
        _extend_unique(impacted_ui_surfaces, record.get("ui_surface_ids"))
        _extend_unique(impacted_tests_scenarios, record.get("scenario_ids"))
        _extend_unique(impacted_tests_scenarios, record.get("test_refs"))
        if record.get("record_status") == "legacy_frozen" or record.get("frozen_exception"):
            if record_id and record_id not in known_debt_freeze_risks:
                known_debt_freeze_risks.append(record_id)
        if record_id and record_id not in impact_tag_covered_ids:
            impact_tag_uncovered_records.append(record)
        if record_id and record_id not in trace_covered_ids:
            trace_uncovered_records.append(record)

    # Coverage of a relevant seam requires BOTH evidence halves -- an
    # impact_tag match AND confirmed trace-observation coverage. A seam
    # missing either half gets its own distinctly-worded caveat naming the
    # seam verbatim; coverage_claim can only be "full" when every relevant
    # seam has both halves confirmed.
    coverage_caveats: list[str] = []
    for record in impact_tag_uncovered_records:
        record_id = record.get("record_id")
        record_name = record.get("name") or record_id
        coverage_caveats.append(
            f"Seam {record_id} ({record_name}) has no impact_tag record covering it -- its downstream "
            "impact is missing/uncovered evidence, not a confirmed full answer."
        )
    for record in trace_uncovered_records:
        record_id = record.get("record_id")
        record_name = record.get("name") or record_id
        coverage_caveats.append(
            f"Seam {record_id} ({record_name}) has no confirmed trace-observation coverage -- its "
            "downstream impact is missing/uncovered runtime evidence, not a confirmed full answer."
        )

    if not relevant_records:
        confidence = "unknown"
        coverage_claim = "unknown"
    elif coverage_caveats:
        confidence = "partial"
        coverage_claim = "partial"
    else:
        confidence = "high"
        coverage_claim = "full"

    covered_summary_parts: list[str] = []
    if impacted_seams:
        covered_summary_parts.append(f"seams {', '.join(impacted_seams)}")
    if impacted_carriers:
        covered_summary_parts.append(f"carrier cells {', '.join(impacted_carriers)}")
    if impacted_ui_surfaces:
        covered_summary_parts.append(f"UI surfaces {', '.join(impacted_ui_surfaces)}")
    if impacted_tests_scenarios:
        covered_summary_parts.append(f"tests/scenarios {', '.join(impacted_tests_scenarios)}")

    answer_lines = [
        "Based on the seam-spine records matched to this question, the known impact touches: "
        + (", ".join(covered_summary_parts) if covered_summary_parts else "no matched seam-spine records") + "."
    ]
    if coverage_caveats:
        uncovered_ids: list[str] = []
        for record in impact_tag_uncovered_records + trace_uncovered_records:
            record_id = record.get("record_id")
            if record_id and str(record_id) not in uncovered_ids:
                uncovered_ids.append(str(record_id))
        answer_lines.append(
            f"This answer is partial: {', '.join(uncovered_ids)} lack impact_tag and/or trace-observation "
            "coverage, so their downstream impact is not confirmed here -- treat this as a starting point "
            "for further review, not a full accounting."
        )
    answer_text = " ".join(answer_lines)

    return {
        "schema_version": SCHEMA_VERSION,
        "question": question,
        "confidence": confidence,
        "coverage_claim": coverage_claim,
        "impacted_paths": impacted_paths,
        "impacted_seams": impacted_seams,
        "impacted_carriers": impacted_carriers,
        "impacted_ui_surfaces": impacted_ui_surfaces,
        "impacted_tests_scenarios": impacted_tests_scenarios,
        "known_debt_freeze_risks": known_debt_freeze_risks,
        "coverage_caveats": coverage_caveats,
        "answer_text": answer_text,
    }
