"""Debt overlay classifier (COM-234 Phase 6, spec #296 S6).

Classifies removal-candidate targets into one of five canonical states using
multiple independent signal sources -- static-unused analysis (Knip/Vulture),
coverage.py contexts, trace-overlay observation, scenario/test joins, git
churn, UI-truth-source status, and freeze-ledger exceptions. Tier-1 adapters
(Knip, Vulture, coverage.py) are optional: when a required adapter is
unavailable, the affected signal group is reported as unavailable and any
candidate that depends on it widens toward ``needs_human_review`` rather than
being silently treated as clean.

NFR-234-006 (load-bearing): a candidate backed by only one independent
signal group can never reach ``safe_candidate`` -- it always widens to
``needs_human_review``. ``safe_candidate`` requires at least three
independent signal groups AND the full #296 S6.4 removal predicate set
satisfied with zero blocking signals. Absent or unavailable evidence is
never inferred as "clean"; it only ever widens uncertainty.

Layer A generic tooling: this module carries no project-specific vocabulary.
The five classification states and the seven S6.4 predicate names are part
of the generic debt-overlay contract (spec #296 S6.3/S6.4), not
project-specific values -- all project-specific ids/paths/symbols live only
in the caller-supplied fixture and in tests/fixtures.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "classify_debt",
    "DEBT_STATES",
    "REQUIRED_SAFE_CANDIDATE_PREDICATES",
]

SCHEMA_VERSION = "code_overview.debt.classification.v1"

# #296 S6.3 classification states, exact names and order.
DEBT_STATES: tuple[str, ...] = (
    "safe_candidate",
    "needs_human_review",
    "hot_unhealthy_do_not_delete_blindly",
    "legacy_but_observed",
    "declared_but_never_observed",
)

# #296 S6.4 multi-signal removal predicate -- every one of these signal_kind
# values must be present among a candidate's signals before it may ever reach
# safe_candidate. Any single missing predicate downgrades to
# needs_human_review (NFR-234-006).
REQUIRED_SAFE_CANDIDATE_PREDICATES: tuple[str, ...] = (
    "static_unused_candidate",
    "not_observed_in_trace",
    "no_active_scenario_join",
    "no_active_test_ref",
    "low_recent_churn",
    "not_primary_truth_source",
    "not_frozen_exception",
)

_MIN_INDEPENDENT_SIGNALS_FOR_SAFE = 3
_DEFAULT_LOW_CHURN_THRESHOLD = 0.25

# Signal kinds that independently mark a candidate as actively unhealthy
# (divergent audits, observed failures) regardless of any other predicate.
_HOT_UNHEALTHY_SIGNAL_KINDS = frozenset({"audit_divergence", "observed_failures"})


def _signal_kinds(signals: list[dict[str, Any]]) -> set[str]:
    return {str(signal.get("signal_kind")) for signal in signals if isinstance(signal, dict)}


def _independent_signal_groups(signals: list[dict[str, Any]]) -> set[str]:
    return {
        str(signal.get("independent_signal_group"))
        for signal in signals
        if isinstance(signal, dict) and signal.get("independent_signal_group")
    }


def _mean_confidence(signals: list[dict[str, Any]]) -> float:
    confidences = [
        float(signal["confidence"])
        for signal in signals
        if isinstance(signal, dict) and isinstance(signal.get("confidence"), (int, float))
    ]
    if not confidences:
        return 0.5
    return round(sum(confidences) / len(confidences), 3)


def _tier1_adapters_map(fixture: dict[str, Any]) -> dict[str, Any] | None:
    adapters = fixture.get("tier1_adapters")
    return adapters if isinstance(adapters, dict) else None


def _unavailable_adapters(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    adapters = _tier1_adapters_map(fixture)
    if adapters is None:
        return []
    unavailable: list[dict[str, Any]] = []
    for name, config in adapters.items():
        if isinstance(config, dict) and config.get("available") is False:
            unavailable.append(
                {
                    "adapter": name,
                    "required_signal_groups": list(config.get("required_signal_groups") or []),
                    "reason": config.get("reason", ""),
                }
            )
    return unavailable


def _dedupe_unavailable_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for entry in entries:
        adapter = str(entry.get("adapter"))
        if adapter in seen:
            continue
        seen.add(adapter)
        deduped.append(entry)
    return deduped


def _candidate_unavailable_signal_sources(
    candidate_groups: set[str],
    unavailable_adapters: list[dict[str, Any]],
) -> list[str]:
    """Adapters whose required signal group is absent from this candidate's signals.

    A candidate is only "affected" by an unavailable adapter when none of the
    signals it does carry belong to that adapter's required signal group --
    i.e. the adapter's absence is plausibly *why* that group is missing here,
    which is exactly the case that must widen the classification rather than
    be silently treated as a clean absence.
    """

    affected: list[str] = []
    for entry in unavailable_adapters:
        required_groups = entry.get("required_signal_groups") or []
        if not required_groups:
            continue
        if not any(group in candidate_groups for group in required_groups):
            affected.append(str(entry["adapter"]))
    return affected


def _scalar_predicate_status(
    predicate: str,
    candidate: dict[str, Any],
    *,
    low_churn_threshold: float,
) -> tuple[str, str]:
    """Validate one of the six scalar S6.4 predicates against its raw candidate fact.

    Returns (status, reason) where status is "satisfied", "contradicted", or
    "unconfirmed" (the raw fact is absent/unknown). A ``signal_kind`` label
    claiming the predicate is never itself sufficient -- only the raw fact
    decides.
    """

    if predicate == "not_observed_in_trace":
        fact = candidate.get("observed_in_trace")
        if fact is None:
            return "unconfirmed", "observed_in_trace is not reported"
        return ("satisfied", "") if fact is False else ("contradicted", f"observed_in_trace={fact!r}")

    if predicate == "no_active_scenario_join":
        fact = candidate.get("scenario_join_count")
        if fact is None:
            return "unconfirmed", "scenario_join_count is not reported"
        return ("satisfied", "") if fact == 0 else ("contradicted", f"scenario_join_count={fact!r}")

    if predicate == "no_active_test_ref":
        fact = candidate.get("test_ref_count")
        if fact is None:
            return "unconfirmed", "test_ref_count is not reported"
        return ("satisfied", "") if fact == 0 else ("contradicted", f"test_ref_count={fact!r}")

    if predicate == "not_primary_truth_source":
        fact = candidate.get("primary_truth_source")
        if fact is None:
            fact = candidate.get("is_primary_truth_source")
        if fact is None:
            return "unconfirmed", "primary_truth_source is not reported"
        return ("satisfied", "") if fact is False else ("contradicted", f"primary_truth_source={fact!r}")

    if predicate == "not_frozen_exception":
        fact = candidate.get("frozen_exception")
        if fact is None:
            return "unconfirmed", "frozen_exception is not reported"
        return ("satisfied", "") if fact is False else ("contradicted", f"frozen_exception={fact!r}")

    if predicate == "low_recent_churn":
        fact = candidate.get("recent_churn_score")
        if fact is None or not isinstance(fact, (int, float)):
            return "unconfirmed", "recent_churn_score is not reported"
        if fact < low_churn_threshold:
            return "satisfied", ""
        return "contradicted", f"recent_churn_score={fact!r} is not below low_churn_threshold={low_churn_threshold!r}"

    raise ValueError(f"_scalar_predicate_status does not handle predicate {predicate!r}")


def _static_unused_candidate_status(
    candidate: dict[str, Any],
    signals: list[dict[str, Any]],
    tier1_adapters_map: dict[str, Any] | None,
) -> tuple[str, str | None, str, str | None] | None:
    """Validate ``static_unused_candidate`` against Tier-1 adapter facts.

    A ``signal_kind=="static_unused_candidate"`` label is only trustworthy
    when the adapter that produced it (``signal["source"]``) is confirmed
    available AND itself reports a positive, target-matching unused finding
    for this candidate. ``available=True`` alone is never sufficient --
    absent, empty, malformed, or other-target-only findings leave the
    predicate unconfirmed (fail-closed), symmetric with the six scalar
    predicates in ``_scalar_predicate_status``. Returns ``None`` when no
    signal claims this predicate at all (the caller treats that as an
    ordinary missing predicate). Otherwise returns
    ``(status, adapter_name, reason, cause)`` where ``cause`` is one of
    ``"map_missing"``, ``"adapter_unlisted"``, ``"adapter_unavailable"``,
    ``"used_finding"``, ``"no_positive_finding"``, or ``None`` when
    satisfied.
    """

    static_signals = [signal for signal in signals if signal.get("signal_kind") == "static_unused_candidate"]
    if not static_signals:
        return None

    target_id = candidate.get("target_id")
    worst: tuple[str, str | None, str, str | None] = ("satisfied", None, "", None)
    for signal in static_signals:
        source = str(signal.get("source") or "unknown_adapter")
        if tier1_adapters_map is None:
            result: tuple[str, str | None, str, str | None] = (
                "unconfirmed",
                source,
                f"tier1_adapters availability map is missing; '{source}' availability is unknown",
                "map_missing",
            )
        else:
            adapter_config = tier1_adapters_map.get(source)
            if not isinstance(adapter_config, dict):
                result = (
                    "unconfirmed",
                    source,
                    f"adapter '{source}' is not listed in the tier1_adapters availability map",
                    "adapter_unlisted",
                )
            elif adapter_config.get("available") is False:
                result = (
                    "contradicted",
                    source,
                    str(adapter_config.get("reason") or f"adapter '{source}' is unavailable"),
                    "adapter_unavailable",
                )
            else:
                findings = adapter_config.get("findings") or []
                target_findings = [
                    finding
                    for finding in findings
                    if isinstance(finding, dict) and finding.get("target_id") == target_id
                ]
                used_finding = next(
                    (
                        finding
                        for finding in target_findings
                        if (
                            finding.get("status") == "used"
                            or finding.get("unused") is False
                            or finding.get("is_unused") is False
                        )
                    ),
                    None,
                )
                unused_finding = next(
                    (
                        finding
                        for finding in target_findings
                        if (
                            finding.get("status") == "unused"
                            or finding.get("unused") is True
                            or finding.get("is_unused") is True
                        )
                    ),
                    None,
                )
                if used_finding is not None:
                    result = (
                        "contradicted",
                        source,
                        f"adapter '{source}' reports {target_id} as used, not unused",
                        "used_finding",
                    )
                elif unused_finding is not None:
                    result = ("satisfied", source, "", None)
                else:
                    result = (
                        "unconfirmed",
                        source,
                        f"adapter '{source}' does not report a positive target-level unused finding for {target_id}",
                        "no_positive_finding",
                    )

        if result[0] == "contradicted":
            return result
        if result[0] == "unconfirmed" and worst[0] != "contradicted":
            worst = result
    return worst


def _classify_one(
    candidate: dict[str, Any],
    *,
    low_churn_threshold: float,
    unavailable_adapters: list[dict[str, Any]],
    tier1_adapters_map: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    signals = [signal for signal in (candidate.get("signals") or []) if isinstance(signal, dict)]
    kinds = _signal_kinds(signals)
    groups = _independent_signal_groups(signals)
    independent_signal_count = len(groups)

    # Every S6.4 predicate is fact-gated: a signal_kind label only claims the
    # predicate; satisfaction requires the candidate's raw structured facts to
    # confirm it (or, for static_unused_candidate, the backing Tier-1 adapter
    # to confirm it). A contradiction or an absent/unknown fact never counts
    # as satisfied -- it only ever produces a named blocking signal.
    satisfied_predicates: list[str] = []
    missing_predicates: list[str] = []
    fact_blocking_signals: list[str] = []
    extra_unavailable_sources: list[str] = []
    discovered_unavailable: list[dict[str, Any]] = []

    for predicate in REQUIRED_SAFE_CANDIDATE_PREDICATES:
        if predicate not in kinds:
            missing_predicates.append(predicate)
            continue

        if predicate == "static_unused_candidate":
            static_result = _static_unused_candidate_status(candidate, signals, tier1_adapters_map)
            assert static_result is not None  # predicate is in kinds, so a signal exists
            status, adapter_name, reason, cause = static_result
        else:
            status, reason = _scalar_predicate_status(predicate, candidate, low_churn_threshold=low_churn_threshold)
            adapter_name, cause = None, None

        if status == "satisfied":
            satisfied_predicates.append(predicate)
            continue

        fact_blocking_signals.append(f"{status}:{predicate}:{reason}")
        if cause in ("map_missing", "adapter_unlisted", "adapter_unavailable") and adapter_name:
            if adapter_name not in extra_unavailable_sources:
                extra_unavailable_sources.append(adapter_name)
                discovered_unavailable.append(
                    {"adapter": adapter_name, "required_signal_groups": [], "reason": reason}
                )

    satisfied_predicates.sort()
    unavailable_signal_sources = list(_candidate_unavailable_signal_sources(groups, unavailable_adapters))
    for adapter_name in extra_unavailable_sources:
        if adapter_name not in unavailable_signal_sources:
            unavailable_signal_sources.append(adapter_name)

    frozen_exception = bool(candidate.get("frozen_exception"))
    observed_in_trace = candidate.get("observed_in_trace")
    recent_churn_score = candidate.get("recent_churn_score")
    active_seam_dependency_count = candidate.get("active_seam_dependency_count") or 0
    scenario_join_count = candidate.get("scenario_join_count") or 0
    test_ref_count = candidate.get("test_ref_count") or 0

    target_id = candidate.get("target_id")

    blocking_signals: list[str] = [f"missing_predicate:{predicate}" for predicate in missing_predicates]
    blocking_signals.extend(fact_blocking_signals)
    blocking_signals.extend(f"unavailable_adapter:{adapter}" for adapter in unavailable_signal_sources)

    if frozen_exception:
        classification = "legacy_but_observed"
    elif kinds & _HOT_UNHEALTHY_SIGNAL_KINDS or (
        observed_in_trace is True
        and isinstance(recent_churn_score, (int, float))
        and recent_churn_score > low_churn_threshold
        and active_seam_dependency_count
    ):
        classification = "hot_unhealthy_do_not_delete_blindly"
    elif observed_in_trace is False and (active_seam_dependency_count or scenario_join_count or test_ref_count):
        classification = "declared_but_never_observed"
    elif (
        set(satisfied_predicates) == set(REQUIRED_SAFE_CANDIDATE_PREDICATES)
        and not unavailable_signal_sources
        and independent_signal_count >= _MIN_INDEPENDENT_SIGNALS_FOR_SAFE
    ):
        classification = "safe_candidate"
    else:
        classification = "needs_human_review"

    confidence = _mean_confidence(signals)

    if classification == "safe_candidate":
        suggested_owner_action = (
            "Route to jCoder to author the removal patch, then jCritic to independently review and "
            "approve before deletion; keep the removal reversible until jCritic signs off."
        )
        explanation = (
            f"safe_candidate: all {len(REQUIRED_SAFE_CANDIDATE_PREDICATES)} required S6.4 signals "
            f"({', '.join(REQUIRED_SAFE_CANDIDATE_PREDICATES)}) are satisfied across "
            f"{independent_signal_count} independent signal groups with no blocking signals."
        )
    elif classification == "legacy_but_observed":
        suggested_owner_action = (
            "Do not remove: this record is a declared legacy/frozen exception per the freeze ledger "
            "and remains observed. Route any change through the owning team and the freeze ledger."
        )
        explanation = (
            "legacy_but_observed: this candidate is a frozen/legacy exception per the freeze ledger "
            "and remains observed in trace; do not delete blindly."
        )
    elif classification == "hot_unhealthy_do_not_delete_blindly":
        suggested_owner_action = (
            "Do not remove: divergence/failure or high-churn-with-active-dependency signals indicate "
            "active instability. Route to a human owner (jDebugger/jOracle) to investigate first."
        )
        explanation = (
            "hot_unhealthy_do_not_delete_blindly: divergence, observed failures, or high recent churn "
            "combined with an active dependency indicate this target is actively evolving or "
            "unhealthy; do not delete blindly."
        )
    elif classification == "declared_but_never_observed":
        suggested_owner_action = (
            "Do not remove yet: this record is actively declared/joined (scenario, test, or seam "
            "references exist) but has never been observed in an accepted trace run. Investigate the "
            "resolution gap before any removal decision."
        )
        explanation = (
            "declared_but_never_observed: this record is actively referenced via scenario/test/seam "
            "joins but has never been observed in an accepted trace run."
        )
    elif independent_signal_count <= 1:
        suggested_owner_action = (
            "Needs human review: only a single independent signal backs this candidate; per "
            "NFR-234-006 a single signal can never license safe_candidate. Escalate to a human owner "
            "for a manual read before any removal decision."
        )
        explanation = (
            "needs_human_review: only a single independent signal group was found for "
            f"{target_id}; a single independent signal can never license safe_candidate per "
            f"NFR-234-006, so this candidate widens to needs_human_review. Missing predicates: "
            f"{', '.join(missing_predicates) if missing_predicates else 'none'}."
        )
    else:
        reasons = []
        if missing_predicates:
            reasons.append(f"missing predicates: {', '.join(missing_predicates)}")
        if fact_blocking_signals:
            reasons.append(f"contradicted/unconfirmed predicate facts: {'; '.join(fact_blocking_signals)}")
        if unavailable_signal_sources:
            reasons.append(f"unavailable Tier-1 adapters: {', '.join(unavailable_signal_sources)}")
        suggested_owner_action = (
            "Needs human review: required S6.4 evidence is not fully satisfied. Escalate to a human "
            "owner before any removal decision; absent signals never license removal."
        )
        explanation = (
            "needs_human_review: this candidate does not satisfy the full S6.4 removal predicate set "
            f"({'; '.join(reasons) if reasons else 'insufficient evidence'}); missing or unavailable "
            "evidence widens uncertainty rather than licensing removal."
        )

    record = {
        "target_id": target_id,
        "classification": classification,
        "confidence": confidence,
        "signals": signals,
        "independent_signal_count": independent_signal_count,
        "satisfied_predicates": satisfied_predicates,
        "blocking_signals": blocking_signals,
        "suggested_owner_action": suggested_owner_action,
        "source_anchors": [candidate["target_anchor_id"]] if candidate.get("target_anchor_id") else [],
        "impacted_scenarios_tests": {
            "scenario_join_count": candidate.get("scenario_join_count"),
            "test_ref_count": candidate.get("test_ref_count"),
            "active_seam_dependency_count": candidate.get("active_seam_dependency_count"),
            "active_ui_surface_dependency_count": candidate.get("active_ui_surface_dependency_count"),
        },
        "unavailable_signal_sources": unavailable_signal_sources,
        "explanation": explanation,
    }
    return record, discovered_unavailable


def classify_debt(fixture: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Classify every candidate in ``fixture`` per the #296 S6 debt-overlay spec.

    ``fixture`` supplies ``candidates`` (each with pre-computed ``signals``
    and per-target fields), an optional ``low_churn_threshold``, and an
    optional ``tier1_adapters`` availability map. Non-strict callers get
    ``exit_code=0`` even when Tier-1 adapters are unavailable (the widened
    classification itself carries the caveat); ``strict=True`` fails closed
    with ``exit_code=6``/``status="adapter_unavailable"`` whenever any
    required Tier-1 adapter is unavailable, so CI can gate on it explicitly.
    """

    candidates = [candidate for candidate in (fixture.get("candidates") or []) if isinstance(candidate, dict)]
    low_churn_threshold = float(fixture.get("low_churn_threshold", _DEFAULT_LOW_CHURN_THRESHOLD))
    tier1_adapters_map = _tier1_adapters_map(fixture)
    unavailable_adapters = _unavailable_adapters(fixture)

    classifications: list[dict[str, Any]] = []
    discovered_unavailable: list[dict[str, Any]] = []
    for candidate in candidates:
        record, candidate_discovered = _classify_one(
            candidate,
            low_churn_threshold=low_churn_threshold,
            unavailable_adapters=unavailable_adapters,
            tier1_adapters_map=tier1_adapters_map,
        )
        classifications.append(record)
        discovered_unavailable.extend(candidate_discovered)

    # A candidate's own signals can reveal an unavailable/unknown Tier-1
    # adapter (e.g. an omitted or non-dict tier1_adapters map, or an adapter
    # not listed in it) that the explicit availability map alone would miss.
    # Merge both sources so the report-level unavailable_signals list, and
    # the strict-mode fail-closed gate, reflect every adapter the fixture's
    # own candidates depend on -- never just the ones explicitly marked
    # available=False.
    all_unavailable = _dedupe_unavailable_entries(unavailable_adapters + discovered_unavailable)

    if strict and all_unavailable:
        exit_code, status = 6, "adapter_unavailable"
    else:
        exit_code, status = 0, "ok"

    return {
        "schema_version": SCHEMA_VERSION,
        "states": list(DEBT_STATES),
        "exit_code": exit_code,
        "status": status,
        "unavailable_signals": all_unavailable,
        "classifications": classifications,
    }
