#!/usr/bin/env python3
"""COM-233 Phase 2 — pure ColGREP index-lag / ETA estimator.

This module is the single shared, deterministic contract consumed by both the
ColGREP MCP search formatter and the ticket-scoped precompact health check
(COM-233 upgrades #2 and #3). It performs NO filesystem, HTTP, git, or
wall-clock IO of its own: every fact the estimator reasons about arrives via a
fully-collected ``IndexLagSnapshot``. Real-world collectors (worktree
resolver, registry reader, heartbeat/manifest readers, live :3280 probes) are
responsible for gathering evidence and constructing the snapshot; this module
only performs deterministic arithmetic/classification over that evidence.

Fail-open invariant: no code path here may raise. Any unexpected/malformed
input degrades to ``health_state="unknown"``, ``confidence="none"`` (or
``"low"``), with the problem recorded in ``errors`` — never an exception.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

try:  # Fail-open import: the shared lifecycle classifier is pure, but if the
    # sibling module is ever unavailable/broken we must still degrade to
    # "no observation" rather than let an import error propagate.
    from colgrep_index_lifecycle import observe_build_state as _observe_build_state
except Exception:  # pragma: no cover - defensive fail-open import guard.
    _observe_build_state = None  # type: ignore[assignment]

try:  # Fail-open import: the shared folder-grouping renderer is pure, but if
    # the sibling module is ever unavailable/broken the self-explaining
    # human_summary must still degrade rather than raise.
    from colgrep_file_grouping import render_grouped_file_list as _render_grouped_file_list
except Exception:  # pragma: no cover - defensive fail-open import guard.
    _render_grouped_file_list = None  # type: ignore[assignment]


SCHEMA_VERSION = "colgrep.index-lag-eta.v1"

# A sane upper bound so a degenerate rate never yields an absurd ETA (30 days).
_ETA_SANITY_CAP_SECONDS = 30 * 24 * 3600

_MANIFEST_TIME_FIELDS: tuple[str, ...] = (
    "queryability_proven_at",
    "last_verified_at",
    "updated_at",
    "last_indexed_at",
)

# A/C10 REVISE BLOCKER 2: strict genuine indexing-completion timestamp
# fields ONLY. Distinct from `_MANIFEST_TIME_FIELDS` above (which feeds the
# broad `last_add_time` "most recent of anything" contract). `last_verified_at`
# is deliberately excluded — a verification pass is not an indexing pass, and
# treating it as indexing evidence lets a stale index read as recently active.
_LAST_INDEXED_EXPLICIT_FIELDS: tuple[str, ...] = (
    "last_indexed_at",
    "indexed_mtime",
)

# A/C10 Phase A liveness thresholds/formatting.
_LIVENESS_UNKNOWN_SUMMARY = "indexer liveness unknown; no last-indexed timestamp"
_LIVENESS_IDLE_SUMMARY = "index up to date; idle"

_ENCODER_NOT_RUNNING_BUILD_STATES = frozenset(
    {
        "queued-needs-builder",
        "registered-unbuilt",
        "stuck-indexing",
        "crashlooping",
        "awaiting-reload",
        "building-healthy",
    }
)

_STALLED_HEALTH_STATES = frozenset({"stalled", "encoder-dead"})


# --------------------------------------------------------------------------- #
# Public dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProgressSample:
    """A single observed progress point, collected by a caller (never this module)."""

    at: datetime
    indexed_file_count: int | None = None
    verified_file_count: int | None = None
    indexed_doc_count: int | None = None


@dataclass(frozen=True)
class OutstandingFile:
    """A single file the estimator considers outstanding (unindexed/stale/etc.)."""

    path: str
    reason: Literal["unindexed", "stale", "deleted-stale", "excluded", "unknown"]
    current_mtime: str | None = None
    indexed_mtime: str | None = None
    indexed: bool = False


@dataclass(frozen=True)
class IndexLagConfig:
    """Injectable windows/thresholds so the estimator stays deterministic under test."""

    recency_window_seconds: int = 600
    min_rate_samples: int = 2
    outstanding_render_cap: int = 25


DEFAULT_CONFIG = IndexLagConfig()


@dataclass(frozen=True)
class IndexLagSnapshot:
    """Fully-collected evidence for one index. The estimator only reads this."""

    now: datetime
    repo_root: Path | None
    worktree_path: Path | None
    ticket: str | None
    branch: str | None
    index_name: str
    index_kind: Literal["base", "worktree-mode-a", "worktree-full", "unknown"]
    expected_files: tuple[str, ...]
    indexed_files: tuple[str, ...]
    stale_files: tuple[str, ...]
    unindexed_files: tuple[str, ...]
    excluded_files: tuple[str, ...]
    manifest: Mapping[str, Any] | None
    registry_entry: Mapping[str, Any] | None
    heartbeat: Mapping[str, Any] | None
    refresh_queue: Mapping[str, Any] | None
    restart_history: tuple[Mapping[str, Any], ...]
    physical_index_exists: bool
    builder_live: bool
    live_index_stats: Mapping[str, Any] | None
    progress_samples: tuple[ProgressSample, ...]
    source_errors: tuple[str, ...]
    # COM-233 A/C10 REVISE BLOCKER 1: True when expected-file evidence (git
    # ls-files) could not be collected. Callers must never mistake this for a
    # complete expected-file set — `expected_files` is not "everything is
    # indexed" evidence when this flag is set.
    expected_files_unknown: bool = False


@dataclass(frozen=True)
class IndexLagEstimate:
    """Deterministic output of :func:`estimate_lag`."""

    schema_version: Literal["colgrep.index-lag-eta.v1"]
    index_name: str
    index_kind: str
    health_state: Literal[
        "fresh",
        "lagging",
        "building",
        "queued",
        "stalled",
        "encoder-dead",
        "unknown",
    ]
    encoder_running: bool | None
    build_state: str | None
    build_state_reason: str | None
    outstanding_files: tuple[OutstandingFile, ...]
    outstanding_file_count: int
    outstanding_files_truncated: bool
    last_add_time: str | None
    last_indexed_at: str | None
    liveness: Mapping[str, Any]
    recent_rate_files_per_min: float | None
    eta_seconds: int | None
    eta: str | None
    confidence: Literal["high", "medium", "low", "none"]
    advisory: str
    fail_open: Literal[True]
    evidence: Mapping[str, Any]
    errors: tuple[str, ...]


# --------------------------------------------------------------------------- #
# Internal helpers — outstanding files
# --------------------------------------------------------------------------- #


def _outstanding_entries(snapshot: IndexLagSnapshot) -> tuple[OutstandingFile, ...]:
    reasons: dict[str, str] = {}
    for path in snapshot.unindexed_files:
        reasons.setdefault(path, "unindexed")
    for path in snapshot.stale_files:
        reasons[path] = "stale"
    ordered_paths = list(snapshot.unindexed_files) + [
        path for path in snapshot.stale_files if path not in snapshot.unindexed_files
    ]
    indexed_set = set(snapshot.indexed_files)
    return tuple(
        OutstandingFile(
            path=path,
            reason=reasons[path],  # type: ignore[arg-type]
            current_mtime=None,
            indexed_mtime=None,
            indexed=path in indexed_set,
        )
        for path in ordered_paths
    )


# --------------------------------------------------------------------------- #
# Internal helpers — time parsing (defensive: never raises)
# --------------------------------------------------------------------------- #


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)


def _parse_time_safe(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _compute_last_add_time(snapshot: IndexLagSnapshot) -> str | None:
    candidates: list[datetime] = []
    for sample in snapshot.progress_samples:
        if isinstance(sample.at, datetime):
            candidates.append(_as_utc(sample.at))
    manifest = snapshot.manifest
    if isinstance(manifest, Mapping):
        for key in _MANIFEST_TIME_FIELDS:
            try:
                raw = manifest.get(key)
            except Exception:  # pragma: no cover - defensive
                raw = None
            parsed = _parse_time_safe(raw)
            if parsed is not None:
                candidates.append(parsed)
    if not candidates:
        return None
    return _iso(max(candidates))


def _compute_last_indexed_at(snapshot: IndexLagSnapshot) -> str | None:
    """A/C10 REVISE BLOCKER 2: strict genuine indexing-completion evidence only.

    Only an explicit ``last_indexed_at``/``indexed_mtime`` manifest field (in
    that order) counts as proof a file was actually indexed. There is
    deliberately NO fallback to ``_compute_last_add_time`` here: that helper's
    broad candidate set (``updated_at``/``last_verified_at``/
    ``queryability_proven_at``/progress samples) does not mean "a file was
    indexed" and must not be able to fabricate a recent/active
    ``last_indexed_at``. Missing explicit evidence -> ``None`` (liveness then
    degrades to "unknown", never "active"/"stalled"/"idle"). Fail-open:
    malformed manifest values are simply skipped, never raised.
    """

    manifest = snapshot.manifest
    if isinstance(manifest, Mapping):
        for key in _LAST_INDEXED_EXPLICIT_FIELDS:
            try:
                raw = manifest.get(key)
            except Exception:  # pragma: no cover - defensive
                raw = None
            parsed = _parse_time_safe(raw)
            if parsed is not None:
                return _iso(parsed)
    return None


def _format_liveness_age(age_seconds: int) -> str:
    """Human age formatting for liveness summaries: seconds -> Nm/Nh/Nd."""

    if age_seconds < 3600:
        return f"{max(age_seconds // 60, 0)}m"
    if age_seconds < 86400:
        return f"{age_seconds // 3600}h"
    return f"{age_seconds // 86400}d"


def classify_index_currency(
    last_indexed_at: str | None,
    *,
    now: datetime,
    config: IndexLagConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """A/C11: pure, deterministic index-currency age classification.

    No IO, no clock reads — ``now`` is injected by the MCP probe/cache layer.
    Reuses ``_parse_time_safe``, ``_format_liveness_age``, and
    ``config.recency_window_seconds``. Returns only the neutral buckets
    ``current``/``aging``/``unknown`` — the loud "STALE" wording (which
    additionally depends on serve-layer outstanding coverage) is decided by
    the MCP-layer formatter, never here. Fail-open: a missing/unparseable
    ``last_indexed_at`` degrades to ``unknown``, never "aging"/"current".
    """

    parsed = _parse_time_safe(last_indexed_at)
    if parsed is None:
        return {"state": "unknown", "age_seconds": None, "age_token": None}
    age_seconds = max(int((_as_utc(now) - parsed).total_seconds()), 0)
    age_token = _format_liveness_age(age_seconds)
    state = "current" if age_seconds <= config.recency_window_seconds else "aging"
    return {"state": state, "age_seconds": age_seconds, "age_token": age_token}


def freshness_age_report(
    *,
    last_indexed_at: str | None,
    now: datetime,
    budget_seconds: float | None,
    budget_source: str,
) -> dict[str, Any]:
    """COM-349 Phase 3: per-index freshness age + budget, never fabricated.

    Missing or malformed ``last_indexed_at`` yields ``freshness_state=unknown``
    and null ages. Unknown is never coerced to fresh. A numeric age is compared
    to ``budget_seconds`` when that budget is a finite positive number.
    """
    currency = classify_index_currency(last_indexed_at, now=now)
    age_seconds = currency.get("age_seconds")
    valid_budget = (
        isinstance(budget_seconds, (int, float))
        and not isinstance(budget_seconds, bool)
        and math.isfinite(float(budget_seconds))
        and float(budget_seconds) > 0
    )
    if age_seconds is None or not valid_budget:
        freshness_state = "unknown"
        last_indexed_out = None if age_seconds is None else last_indexed_at
        budget_out = float(budget_seconds) if valid_budget else budget_seconds
        return {
            "last_indexed_at": last_indexed_out,
            "age_seconds": age_seconds,
            "budget_seconds": budget_out if valid_budget else None,
            "budget_source": budget_source,
            "freshness_state": freshness_state,
        }
    age_i = int(age_seconds)
    budget_f = float(budget_seconds)
    return {
        "last_indexed_at": last_indexed_at,
        "age_seconds": age_i,
        "budget_seconds": budget_f,
        "budget_source": budget_source,
        "freshness_state": "within_budget" if age_i <= budget_f else "over_budget",
    }


def _compute_liveness(
    *,
    last_indexed_at: str | None,
    now: datetime,
    outstanding_count: int,
    encoder_running: bool | None,
    config: IndexLagConfig,
    expected_files_unknown: bool = False,
) -> dict[str, Any]:
    """A/C10 Phase A: deterministic liveness verdict for the base-index estimator.

    Fail-open: a missing/unparseable ``last_indexed_at`` NEVER classifies as
    "stalled" — it degrades to "unknown" (missing evidence != a dead indexer).
    Age is derived from ``now`` (injected via the snapshot), never wall clock.

    A/C10 REVISE BLOCKER 1: when ``expected_files_unknown`` is True (git
    ls-files evidence unavailable), liveness is unconditionally "unknown" —
    an empty outstanding-file count computed from incomplete evidence must
    never be read as "idle".
    """

    if expected_files_unknown:
        return {
            "state": "unknown",
            "last_indexed_at": None,
            "age_seconds": None,
            "summary": _LIVENESS_UNKNOWN_SUMMARY,
        }

    parsed = _parse_time_safe(last_indexed_at) if last_indexed_at else None
    if parsed is None:
        return {
            "state": "unknown",
            "last_indexed_at": None,
            "age_seconds": None,
            "summary": _LIVENESS_UNKNOWN_SUMMARY,
        }

    age_seconds = max(int((_as_utc(now) - parsed).total_seconds()), 0)

    if outstanding_count == 0:
        return {
            "state": "idle",
            "last_indexed_at": last_indexed_at,
            "age_seconds": age_seconds,
            "summary": _LIVENESS_IDLE_SUMMARY,
        }

    age_str = _format_liveness_age(age_seconds)
    if age_seconds <= config.recency_window_seconds and encoder_running is True:
        return {
            "state": "active",
            "last_indexed_at": last_indexed_at,
            "age_seconds": age_seconds,
            "summary": f"indexing active; last indexed {age_str} ago",
        }

    return {
        "state": "stalled",
        "last_indexed_at": last_indexed_at,
        "age_seconds": age_seconds,
        "summary": f"last indexed {age_str} ago — likely stalled",
    }


# --------------------------------------------------------------------------- #
# Internal helpers — build-state observation (fail-open wrapper)
# --------------------------------------------------------------------------- #


def _safe_observe_build_state(snapshot: IndexLagSnapshot):
    if _observe_build_state is None:
        return None
    try:
        return _observe_build_state(
            entry=dict(snapshot.registry_entry) if isinstance(snapshot.registry_entry, Mapping) else None,
            heartbeat=dict(snapshot.heartbeat) if isinstance(snapshot.heartbeat, Mapping) else None,
            manifest=dict(snapshot.manifest) if isinstance(snapshot.manifest, Mapping) else None,
            physical_index_exists=bool(snapshot.physical_index_exists),
            restart_history=tuple(
                dict(item) for item in snapshot.restart_history if isinstance(item, Mapping)
            ),
            now=snapshot.now,
            builder_live=bool(snapshot.builder_live),
        )
    except Exception:  # pragma: no cover - defensive fail-open
        return None


# --------------------------------------------------------------------------- #
# Internal helpers — encoder liveness (MAJOR-2 guard)
# --------------------------------------------------------------------------- #


def _live_worker_pid_evidence(heartbeat: Mapping[str, Any] | None) -> bool:
    if not isinstance(heartbeat, Mapping):
        return False
    worker = heartbeat.get("worker")
    if not isinstance(worker, Mapping):
        return False
    pid = worker.get("worker_pid")
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0


def _fresh_progress_evidence(
    progress_samples: tuple[ProgressSample, ...], now: datetime, window_seconds: int
) -> bool:
    for sample in progress_samples:
        if not isinstance(sample.at, datetime):
            continue
        age = (now - _as_utc(sample.at)).total_seconds()
        if 0 <= age <= window_seconds:
            return True
    return False


def _encoder_running(
    snapshot: IndexLagSnapshot, build_state: str | None, config: IndexLagConfig, now: datetime
) -> bool | None:
    # Authoritative negative build-state evidence wins outright: a classifier
    # that has already determined the watcher/worker is stuck, crashlooping,
    # queued-without-a-builder, unbuilt, or awaiting reload means the encoder
    # is definitively NOT running right now — even if a progress sample
    # happens to fall inside the recency window (e.g. progress made just
    # before the crash/stall). Only "building-healthy" (fresh lease) or "no
    # build-state evidence at all" fall through to independent liveness
    # checks below.
    if build_state in _ENCODER_NOT_RUNNING_BUILD_STATES and build_state != "building-healthy":
        return False

    # Independent live-liveness evidence proves the encoder is running,
    # regardless of what (if anything) the lifecycle classifier reports.
    if snapshot.builder_live is True:
        return True
    if _live_worker_pid_evidence(snapshot.heartbeat):
        return True
    if _fresh_progress_evidence(snapshot.progress_samples, now, config.recency_window_seconds):
        return True

    # No independent evidence: a "building-healthy" fresh lease ALONE is
    # insufficient (MAJOR-2 guard) — it degrades to "not proven running".
    if build_state == "building-healthy":
        return False
    if build_state is None:
        return None
    return False


# --------------------------------------------------------------------------- #
# Internal helpers — recent rate (file-level deltas only)
# --------------------------------------------------------------------------- #


def _file_level_count(sample: ProgressSample) -> int | None:
    if sample.indexed_file_count is not None:
        return sample.indexed_file_count
    if sample.verified_file_count is not None:
        return sample.verified_file_count
    return None


def _valid_progress_points(
    progress_samples: tuple[ProgressSample, ...], now: datetime, window_seconds: int
) -> list[tuple[datetime, int]]:
    points: list[tuple[datetime, int]] = []
    for sample in progress_samples:
        count = _file_level_count(sample)
        if count is None or not isinstance(sample.at, datetime):
            continue
        at = _as_utc(sample.at)
        age = (now - at).total_seconds()
        if age < 0 or age > window_seconds:
            continue
        points.append((at, count))
    points.sort(key=lambda point: point[0])
    return points


def _compute_rate(
    points: list[tuple[datetime, int]], min_rate_samples: int
) -> tuple[float | None, int]:
    if len(points) < max(min_rate_samples, 2):
        return None, 0
    deltas: list[tuple[int, float]] = []
    for (prev_at, prev_count), (cur_at, cur_count) in zip(points, points[1:]):
        delta_seconds = (cur_at - prev_at).total_seconds()
        delta_files = cur_count - prev_count
        if delta_seconds > 0 and delta_files > 0:
            deltas.append((delta_files, delta_seconds))
    if not deltas:
        return None, 0
    total_files = sum(delta[0] for delta in deltas)
    total_seconds = sum(delta[1] for delta in deltas)
    if total_seconds <= 0:
        return None, len(deltas)
    return (total_files / total_seconds) * 60.0, len(deltas)


# --------------------------------------------------------------------------- #
# Internal helpers — health/eta/confidence/advisory classification
# --------------------------------------------------------------------------- #


def _health_state(outstanding_count: int, build_state: str | None, encoder_running: bool | None) -> str:
    if outstanding_count == 0:
        return "fresh"
    if build_state == "crashlooping":
        return "encoder-dead"
    if build_state == "stuck-indexing":
        return "stalled"
    if build_state == "queued-needs-builder":
        return "queued"
    if build_state == "registered-unbuilt":
        return "stalled"
    if build_state == "awaiting-reload":
        return "building"
    if build_state == "building-healthy":
        return "building"
    if build_state is None:
        return "lagging" if encoder_running is True else "unknown"
    return "unknown"


def _compute_eta_seconds(
    outstanding_count: int, encoder_running: bool | None, rate_per_min: float | None
) -> int | None:
    if outstanding_count == 0:
        return 0
    if encoder_running is not True:
        return None
    if rate_per_min is None or rate_per_min <= 0:
        return None
    rate_per_second = rate_per_min / 60.0
    seconds = math.ceil(outstanding_count / rate_per_second)
    seconds = max(seconds, 1)
    return min(seconds, _ETA_SANITY_CAP_SECONDS)


def _eta_string(eta_seconds: int | None, health_state: str) -> str:
    if eta_seconds is not None:
        return "0s" if eta_seconds == 0 else f"{eta_seconds}s"
    return "stalled" if health_state in _STALLED_HEALTH_STATES else "unknown"


def _confidence(
    outstanding_count: int,
    build_state: str | None,
    encoder_running: bool | None,
    rate_per_min: float | None,
    valid_delta_count: int,
    source_errors: tuple[str, ...],
) -> str:
    if outstanding_count == 0:
        return "high"
    if encoder_running is not True:
        if build_state is None and encoder_running is None:
            return "none"
        return "low"
    if rate_per_min is None or rate_per_min <= 0:
        return "low"
    if source_errors:
        return "low"
    return "high" if valid_delta_count >= 2 else "medium"


def _advisory(
    health_state: str,
    outstanding_count: int,
    build_state_reason: str | None,
    eta_seconds: int | None,
    encoder_running: bool | None,
    rate_per_min: float | None,
) -> str:
    if health_state == "fresh":
        return "index is fully synced; no outstanding files."
    if health_state == "encoder-dead":
        reason = build_state_reason or "restart threshold exceeded"
        return f"encoder crashlooping/dead: {reason}."
    if health_state == "stalled":
        reason = build_state_reason or "build lease/deadline expired"
        return f"index build appears stalled: {reason}."
    if health_state == "queued":
        reason = build_state_reason or "no active builder observed"
        return f"index build is queued; no active builder observed: {reason}."
    if eta_seconds is not None and eta_seconds > 0:
        return f"indexing in progress; estimated {eta_seconds}s remaining for {outstanding_count} outstanding file(s)."
    if encoder_running is True and (rate_per_min is None or rate_per_min <= 0):
        return "indexing observed but recent per-file rate unavailable."
    return "no liveness evidence available; index health is unknown."


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _estimate_lag_inner(snapshot: IndexLagSnapshot, config: IndexLagConfig) -> IndexLagEstimate:
    now = _as_utc(snapshot.now)
    expected_files_unknown = bool(getattr(snapshot, "expected_files_unknown", False))

    outstanding_entries = _outstanding_entries(snapshot)
    outstanding_count = len(outstanding_entries)
    cap = max(config.outstanding_render_cap, 0)
    rendered = outstanding_entries[:cap]
    truncated = outstanding_count > len(rendered)

    observation = _safe_observe_build_state(snapshot)
    build_state = observation.build_state if observation is not None else None
    build_state_reason = observation.reason if observation is not None else None

    encoder_running = _encoder_running(snapshot, build_state, config, now)

    valid_points = _valid_progress_points(snapshot.progress_samples, now, config.recency_window_seconds)
    rate_per_min, valid_delta_count = _compute_rate(valid_points, config.min_rate_samples)

    health_state = _health_state(outstanding_count, build_state, encoder_running)
    eta_seconds = _compute_eta_seconds(outstanding_count, encoder_running, rate_per_min)
    confidence = _confidence(
        outstanding_count, build_state, encoder_running, rate_per_min, valid_delta_count, snapshot.source_errors
    )

    # A/C10 REVISE BLOCKER 1: missing expected-file evidence (git ls-files
    # failed) disqualifies fresh/high/idle outright — an outstanding count of
    # zero computed from incomplete evidence is not proof the index is fresh.
    if expected_files_unknown:
        health_state = "unknown"
        confidence = "none"
        eta_seconds = None

    eta = _eta_string(eta_seconds, health_state)
    last_add_time = _compute_last_add_time(snapshot)
    last_indexed_at = _compute_last_indexed_at(snapshot)
    liveness = _compute_liveness(
        last_indexed_at=last_indexed_at,
        now=now,
        outstanding_count=outstanding_count,
        encoder_running=encoder_running,
        config=config,
        expected_files_unknown=expected_files_unknown,
    )
    advisory = _advisory(health_state, outstanding_count, build_state_reason, eta_seconds, encoder_running, rate_per_min)
    errors = tuple(snapshot.source_errors)

    evidence: dict[str, Any] = {
        "build_state": build_state,
        "build_state_reason": build_state_reason,
        "valid_progress_points": len(valid_points),
        "valid_deltas": valid_delta_count,
        "builder_live": bool(snapshot.builder_live),
        "live_worker_pid_evidence": _live_worker_pid_evidence(snapshot.heartbeat),
        "fresh_progress_evidence": _fresh_progress_evidence(
            snapshot.progress_samples, now, config.recency_window_seconds
        ),
    }

    return IndexLagEstimate(
        schema_version=SCHEMA_VERSION,
        index_name=snapshot.index_name,
        index_kind=snapshot.index_kind,
        health_state=health_state,  # type: ignore[arg-type]
        encoder_running=encoder_running,
        build_state=build_state,
        build_state_reason=build_state_reason,
        outstanding_files=rendered,
        outstanding_file_count=outstanding_count,
        outstanding_files_truncated=truncated,
        last_add_time=last_add_time,
        last_indexed_at=last_indexed_at,
        liveness=liveness,
        recent_rate_files_per_min=rate_per_min,
        eta_seconds=eta_seconds,
        eta=eta,
        confidence=confidence,  # type: ignore[arg-type]
        advisory=advisory,
        fail_open=True,
        evidence=evidence,
        errors=errors,
    )


def estimate_lag(snapshot: IndexLagSnapshot, *, config: IndexLagConfig = DEFAULT_CONFIG) -> IndexLagEstimate:
    """Pure, deterministic index-lag/ETA estimate for a fully-collected snapshot.

    No filesystem/HTTP/git/wall-clock IO occurs here. Never raises: any
    unexpected failure degrades to an ``unknown``/``none``-confidence,
    ``fail_open=True`` estimate with the problem recorded in ``errors``.
    """

    try:
        return _estimate_lag_inner(snapshot, config)
    except Exception as exc:  # pragma: no cover - defensive fail-open net
        try:
            source_errors = tuple(snapshot.source_errors)
        except Exception:
            source_errors = ()
        try:
            index_name = snapshot.index_name
        except Exception:
            index_name = "unknown"
        try:
            index_kind = snapshot.index_kind
        except Exception:
            index_kind = "unknown"
        return IndexLagEstimate(
            schema_version=SCHEMA_VERSION,
            index_name=index_name,
            index_kind=index_kind,
            health_state="unknown",
            encoder_running=None,
            build_state=None,
            build_state_reason=None,
            outstanding_files=(),
            outstanding_file_count=0,
            outstanding_files_truncated=False,
            last_add_time=None,
            last_indexed_at=None,
            liveness={
                "state": "unknown",
                "last_indexed_at": None,
                "age_seconds": None,
                "summary": _LIVENESS_UNKNOWN_SUMMARY,
            },
            recent_rate_files_per_min=None,
            eta_seconds=None,
            eta="unknown",
            confidence="none",
            advisory="estimator failed unexpectedly; degrading to unknown (fail-open).",
            fail_open=True,
            evidence={},
            errors=source_errors + (f"estimator error: {exc}",),
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _json_safe(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _json_safe(getattr(value, f.name)) for f in fields(value)}
    return value


def estimate_to_dict(estimate: IndexLagEstimate) -> dict[str, Any]:
    """Faithful plain-dict/JSON-safe serialization of an :class:`IndexLagEstimate`.

    ``None`` values (e.g. ``eta_seconds``) are preserved as ``None``/JSON-null;
    nothing is coerced to a sentinel.
    """

    return _json_safe(estimate)


# --------------------------------------------------------------------------- #
# A/C10 Phase A — fail-open Docker probe for the base-index last-indexed time.
#
# This is a standalone collector-side helper, NOT called from `estimate_lag`
# (the estimator stays pure/IO-free). A later phase wires this into the live
# snapshot collector; Phase A only needs the helper itself. The `run` param is
# injected so tests never touch a real Docker daemon.
# --------------------------------------------------------------------------- #

_DOCKER_STAT_TIMESTAMP_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?\s+(?P<tz>[+-]\d{4})$"
)


def _parse_docker_stat_timestamp(line: str) -> str | None:
    """Parse a GNU ``stat -c %y`` line into an ISO-8601 UTC timestamp (µs precision).

    Example: ``"2026-06-24 16:12:03.123456789 +0000"`` ->
    ``"2026-06-24T16:12:03.123456Z"``. Fail-open: any unparseable input
    returns ``None`` rather than raising.
    """

    match = _DOCKER_STAT_TIMESTAMP_RE.match(line.strip())
    if not match:
        return None
    frac = match.group("frac") or ""
    microsecond = int((frac + "000000")[:6]) if frac else 0
    try:
        naive_with_tz = datetime.strptime(
            f"{match.group('date')} {match.group('time')} {match.group('tz')}",
            "%Y-%m-%d %H:%M:%S %z",
        )
        resolved = naive_with_tz.replace(microsecond=microsecond).astimezone(timezone.utc)
    except Exception:  # pragma: no cover - defensive fail-open
        return None
    return resolved.isoformat(timespec="microseconds").replace("+00:00", "Z")


def resolve_base_index_last_indexed(
    index_name: str,
    *,
    run: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
) -> str | None:
    """Fail-open Docker probe for a base index's on-disk last-modified time.

    Runs ``docker exec next-plaid-api stat -c %y /data/indices/<index_name>``
    via the injected ``run`` callable (defaults to ``subprocess.run`` for real
    use; tests inject a fake to avoid touching a real Docker daemon). Never
    raises: a non-zero exit, timeout, any raised exception, empty stdout, or
    unparseable output all degrade to ``None``.
    """

    argv = ["docker", "exec", "next-plaid-api", "stat", "-c", "%y", f"/data/indices/{index_name}"]
    try:
        result = run(argv, capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return None
    try:
        if getattr(result, "returncode", None) != 0:
            return None
        stdout = getattr(result, "stdout", None)
        if not isinstance(stdout, str) or not stdout.strip():
            return None
        return _parse_docker_stat_timestamp(stdout.strip())
    except Exception:  # pragma: no cover - defensive fail-open
        return None


# =============================================================================
# COM-233 Phase 3 — ticket-scoped ColGREP index-health IO collectors + CLI.
#
# Everything below this line performs real IO (filesystem, git subprocess,
# env reads). ``estimate_lag`` above stays pure: collectors here gather
# evidence into an ``IndexLagSnapshot`` and hand it to ``estimate_lag`` for
# the actual (pure) classification. Fail-open invariant applies end-to-end:
# no path here may raise or cause a non-zero process exit.
# =============================================================================

HEALTH_SCHEMA_VERSION = "colgrep.ticket-index-health.v1"

_DEFAULT_INDICES_ROOT = Path("~/dev/colgrep-idx/code")
_TICKET_RE = re.compile(r"([A-Z][A-Z0-9]*-\d+)")
_TITLE_ENV_VARS = ("CLAUDE_CODE_SESSION_TITLE", "CLAUDE_SESSION_TITLE", "JSWARM_SESSION_TITLE")
_VALID_INDEX_KINDS = frozenset({"base", "worktree-mode-a", "worktree-full", "unknown"})


@dataclass(frozen=True)
class TicketContext:
    """Ticket -> index resolution result (the ``resolution`` object of the health payload)."""

    ticket: str | None
    source: Literal["explicit-ticket", "session-binding", "session-title", "git-branch", "none"]
    confidence: Literal["high", "medium", "low", "none"]
    branch: str | None
    worktree_path: str | None
    project_slug: str | None
    index_name: str | None
    index_dir: Path | None
    index_kind: str
    mismatch_reason: str | None
    errors: tuple[str, ...]


# --------------------------------------------------------------------------- #
# Internal helpers — git/plan/binding/title evidence (each fail-open)
# --------------------------------------------------------------------------- #


def _plan_exists(repo_root: Path, ticket: str) -> bool:
    try:
        plans_dir = Path(repo_root) / ".jswarm" / "plans"
        if not plans_dir.is_dir():
            return False
        return any(plans_dir.glob(f"{ticket}*.md"))
    except Exception:  # pragma: no cover - defensive fail-open
        return False


def _current_branch(repo_root: Path) -> str | None:
    for args in (["git", "symbolic-ref", "--short", "HEAD"], ["git", "rev-parse", "--abbrev-ref", "HEAD"]):
        try:
            result = subprocess.run(
                args, cwd=str(repo_root), capture_output=True, text=True, timeout=10, check=False,
            )
        except Exception:  # pragma: no cover - defensive fail-open
            continue
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
    return None


def _ticket_from_text(text: str | None) -> str | None:
    if not text:
        return None
    match = _TICKET_RE.search(text)
    return match.group(1) if match else None


def _read_binding(repo_root: Path, session_id: str | None) -> tuple[str | None, str | None]:
    """Read ``.jswarm/state/sessions/<id>/active-ticket.json``. Returns (ticket, error)."""

    if not session_id:
        return None, None
    binding_path = Path(repo_root) / ".jswarm" / "state" / "sessions" / session_id / "active-ticket.json"
    try:
        if not binding_path.is_file():
            return None, None
    except Exception:  # pragma: no cover - defensive fail-open
        return None, None
    try:
        raw = binding_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        return None, f"malformed active-ticket.json: {exc}"
    if not isinstance(data, dict):
        return None, "malformed active-ticket.json: not a JSON object"
    ticket = data.get("ticket")
    if not isinstance(ticket, str) or not ticket.strip():
        return None, None
    return ticket.strip(), None


def _read_session_title(repo_root: Path, session_id: str | None) -> str | None:
    for env_var in _TITLE_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            return value
    try:
        registry_path = Path.home() / ".claude" / "session-name-registry.ndjson"
        if session_id and registry_path.is_file():
            title: str | None = None
            for line in registry_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if isinstance(record, dict) and record.get("session_id") == session_id:
                    name = record.get("session_name")
                    if isinstance(name, str) and name:
                        title = name
            if title:
                return title
    except Exception:  # pragma: no cover - defensive fail-open
        pass
    try:
        if session_id:
            cache_path = Path(repo_root) / ".jswarm" / "state" / "sessions" / session_id / "hud-stdin-cache.json"
            if cache_path.is_file():
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    name = data.get("session_name")
                    if isinstance(name, str) and name:
                        return name
    except Exception:  # pragma: no cover - defensive fail-open
        pass
    return None


def _select_base_index(*, repo_root: Path, indices_root: Path) -> dict[str, Any] | None:
    """Scan ``indices_root/*/project.json`` for the entry whose ``project_path``
    resolves to ``repo_root``. Never scans for/returns anything else, and never
    returns an entry that does not match — callers must not fall back to an
    all-index scan for health confidence."""

    try:
        repo_root_resolved = str(Path(repo_root).resolve())
    except Exception:  # pragma: no cover - defensive fail-open
        return None
    try:
        entries = sorted(p for p in Path(indices_root).iterdir() if p.is_dir())
    except Exception:
        return None
    for entry in entries:
        try:
            data = json.loads((entry / "project.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        project_path = data.get("project_path")
        if not isinstance(project_path, str):
            continue
        try:
            if str(Path(project_path).resolve()) != repo_root_resolved:
                continue
        except Exception:
            continue
        return {
            "index_name": entry.name,
            "project_slug": data.get("project_name"),
            "index_dir": entry,
            "index_kind": "base",
            "worktree_path": None,
        }
    return None


def _is_worktree_checkout(repo_root: Path) -> bool:
    try:
        git_marker = Path(repo_root) / ".git"
        return git_marker.is_file()  # a worktree's `.git` is a file (gitdir pointer), not a directory.
    except Exception:  # pragma: no cover - defensive fail-open
        return False


def _select_worktree_index(*, repo_root: Path, indices_root: Path) -> dict[str, Any] | None:
    """Best-effort worktree index lookup via ``colgrep_worktree`` (COM-171/172 manifest).

    Fail-open: any import/resolution failure falls back to ``None`` so callers
    degrade to the base-index scan / unknown estimate rather than raising."""

    try:  # pragma: no cover - exercised only for real worktree checkouts
        try:
            from jswarm import colgrep_worktree as _worktree  # type: ignore[import-not-found]
        except ImportError:
            import colgrep_worktree as _worktree  # type: ignore[no-redef]
        overlay_path = Path(repo_root) / ".colgrep-overlay.json"
        if not overlay_path.is_file():
            return None
        manifest = json.loads(overlay_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return None
        index_name = manifest.get("overlay_index") or manifest.get("index_name")
        if not isinstance(index_name, str) or not index_name:
            return None
        index_dir = Path(indices_root) / index_name
        return {
            "index_name": index_name,
            "project_slug": manifest.get("project_slug"),
            "index_dir": index_dir if index_dir.is_dir() else None,
            "index_kind": "worktree-mode-a",
            "worktree_path": str(repo_root),
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Public collector #1 — ticket -> index resolution (all IO; fail-open)
# --------------------------------------------------------------------------- #


def collect_ticket_context(
    *,
    repo_root: Path,
    explicit_ticket: str | None,
    session_id: str | None,
    indices_root: Path,
) -> TicketContext:
    """Resolve the ticket-scoped index for ``repo_root`` (real IO; never raises).

    Resolution order: explicit ``--ticket`` > validated session binding >
    validated session title > validated git branch > ``none``. Each source is
    validated against a repo-local plan (``.jswarm/plans/<TICKET>*.md``); a
    disagreement between two independently-resolvable signals, or a resolved
    ticket lacking a repo-local plan, degrades to no index picked rather than
    guessing or scanning every index.
    """

    errors: list[str] = []
    try:
        branch = _current_branch(repo_root)
    except Exception:  # pragma: no cover - defensive fail-open
        branch = None

    def _select_index() -> dict[str, Any] | None:
        try:
            if _is_worktree_checkout(repo_root):
                worktree_hit = _select_worktree_index(repo_root=repo_root, indices_root=indices_root)
                if worktree_hit is not None:
                    return worktree_hit
        except Exception:  # pragma: no cover - defensive fail-open
            pass
        try:
            return _select_base_index(repo_root=repo_root, indices_root=indices_root)
        except Exception:  # pragma: no cover - defensive fail-open
            return None

    def _no_index(*, source: str, confidence: str, reason: str | None) -> TicketContext:
        return TicketContext(
            ticket=None,
            source=source,  # type: ignore[arg-type]
            confidence=confidence,  # type: ignore[arg-type]
            branch=branch,
            worktree_path=None,
            project_slug=None,
            index_name=None,
            index_dir=None,
            index_kind="unknown",
            mismatch_reason=reason,
            errors=tuple(errors),
        )

    def _resolved(*, ticket: str, source: str, confidence: str) -> TicketContext:
        index_info = _select_index()
        if index_info is None:
            return TicketContext(
                ticket=ticket,
                source=source,  # type: ignore[arg-type]
                confidence=confidence,  # type: ignore[arg-type]
                branch=branch,
                worktree_path=None,
                project_slug=None,
                index_name=None,
                index_dir=None,
                index_kind="unknown",
                mismatch_reason=f"no matching index found under {indices_root} for repo_root {repo_root}",
                errors=tuple(errors),
            )
        index_kind = index_info.get("index_kind") if index_info.get("index_kind") in _VALID_INDEX_KINDS else "base"
        return TicketContext(
            ticket=ticket,
            source=source,  # type: ignore[arg-type]
            confidence=confidence,  # type: ignore[arg-type]
            branch=branch,
            worktree_path=index_info.get("worktree_path"),
            project_slug=index_info.get("project_slug"),
            index_name=index_info.get("index_name"),
            index_dir=index_info.get("index_dir"),
            index_kind=index_kind,
            mismatch_reason=None,
            errors=tuple(errors),
        )

    # 1. Explicit --ticket always wins outright; only its own plan is validated.
    explicit_ticket = (explicit_ticket or "").strip() or None
    if explicit_ticket:
        if _plan_exists(repo_root, explicit_ticket):
            return _resolved(ticket=explicit_ticket, source="explicit-ticket", confidence="high")
        return _no_index(
            source="explicit-ticket",
            confidence="none",
            reason=f"explicit ticket {explicit_ticket!r} has no plan under {repo_root}/.jswarm/plans",
        )

    # 2. Session binding (validated against repo-local plan; checked for
    #    conflict against an independently-resolvable, validated git branch).
    binding_ticket, binding_error = _read_binding(repo_root, session_id)
    if binding_error:
        errors.append(binding_error)

    branch_ticket = _ticket_from_text(branch)
    branch_valid = bool(branch_ticket) and _plan_exists(repo_root, branch_ticket)

    if binding_ticket:
        binding_valid = _plan_exists(repo_root, binding_ticket)
        if not binding_valid:
            return _no_index(
                source="session-binding",
                confidence="low",
                reason=f"session-bound ticket {binding_ticket!r} has no plan under {repo_root}/.jswarm/plans",
            )
        if branch_valid and branch_ticket != binding_ticket:
            return _no_index(
                source="session-binding",
                confidence="none",
                reason=(
                    f"session-bound ticket {binding_ticket!r} conflicts with git branch "
                    f"ticket {branch_ticket!r}"
                ),
            )
        return _resolved(ticket=binding_ticket, source="session-binding", confidence="high")

    # 3. Session title (conservative pattern match; checked for conflict
    #    against an independently-resolvable git branch before validation).
    title_ticket = _ticket_from_text(_read_session_title(repo_root, session_id))
    if title_ticket and branch_ticket and title_ticket != branch_ticket:
        return _no_index(
            source="session-title",
            confidence="none",
            reason=f"session title ticket {title_ticket!r} conflicts with git branch ticket {branch_ticket!r}",
        )
    if title_ticket:
        if _plan_exists(repo_root, title_ticket):
            return _resolved(ticket=title_ticket, source="session-title", confidence="medium")
        return _no_index(
            source="session-title",
            confidence="none",
            reason=f"session title ticket {title_ticket!r} has no plan under {repo_root}/.jswarm/plans",
        )

    # 4. Git branch.
    if branch_ticket:
        if branch_valid:
            return _resolved(ticket=branch_ticket, source="git-branch", confidence="low")
        return _no_index(
            source="git-branch",
            confidence="none",
            reason=f"git branch ticket {branch_ticket!r} has no plan under {repo_root}/.jswarm/plans",
        )

    # 5. Nothing resolves: do NOT fall back to scanning every index.
    return _no_index(source="none", confidence="none", reason="; ".join(errors) if errors else None)


# --------------------------------------------------------------------------- #
# Public collector #2 — index-lag snapshot IO (real files/git; fail-open)
# --------------------------------------------------------------------------- #


def collect_index_lag_snapshot(
    *,
    index_dir: Path,
    repo_root: Path,
    ticket: str | None,
    branch: str | None,
    index_name: str,
    index_kind: str = "base",
    base_docker_index_name: str | None = None,
    now: datetime | None = None,
) -> IndexLagSnapshot:
    """Collect real evidence (state.json + git ls-files) into an ``IndexLagSnapshot``.

    Best-effort/degradable: any read failure is recorded in ``source_errors``
    rather than raised, so the pure ``estimate_lag`` still receives a usable
    (if partial) snapshot. This performs NO live :3280 probing — that plane is
    optional evidence not required for the resolution-focused health contract.
    """

    resolved_now = now if now is not None else datetime.now(timezone.utc)
    source_errors: list[str] = []

    indexed_files: tuple[str, ...] = ()
    try:
        state_data = json.loads((Path(index_dir) / "state.json").read_text(encoding="utf-8"))
        files = state_data.get("files") if isinstance(state_data, dict) else None
        if isinstance(files, dict):
            indexed_files = tuple(sorted(str(p) for p in files.keys()))
    except Exception as exc:
        source_errors.append(f"state.json read failed: {exc}")

    # A/C10 REVISE BLOCKER 1: no expected-file evidence until git ls-files
    # actually succeeds. On failure `expected_files` stays empty rather than
    # silently falling back to `indexed_files` — that fallback previously made
    # `unindexed_files` compute to `()` on any git failure, false-greening a
    # stale index as fully synced.
    expected_files: tuple[str, ...] = ()
    expected_files_unknown = False
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode == 0:
            expected_files = tuple(sorted(line.strip() for line in result.stdout.splitlines() if line.strip()))
        else:
            source_errors.append(f"git ls-files exited {result.returncode}: {result.stderr.strip()}")
            expected_files_unknown = True
    except Exception as exc:
        source_errors.append(f"git ls-files failed: {exc}")
        expected_files_unknown = True

    indexed_set = set(indexed_files)
    unindexed_files = tuple(path for path in expected_files if path not in indexed_set)

    index_kind_value = index_kind if index_kind in _VALID_INDEX_KINDS else "base"

    manifest: dict[str, Any] | None = None
    if index_kind_value == "base":
        try:
            probed_last_indexed_at = resolve_base_index_last_indexed(base_docker_index_name or index_name)
        except Exception as exc:  # pragma: no cover - defensive fail-open
            source_errors.append(f"base last-indexed probe failed: {exc}")
        else:
            manifest = {"last_indexed_at": probed_last_indexed_at}

    return IndexLagSnapshot(
        now=resolved_now,
        repo_root=Path(repo_root),
        worktree_path=Path(repo_root) if index_kind_value != "base" else None,
        ticket=ticket,
        branch=branch,
        index_name=index_name,
        index_kind=index_kind_value,  # type: ignore[arg-type]
        expected_files=expected_files,
        indexed_files=indexed_files,
        stale_files=(),
        unindexed_files=unindexed_files,
        excluded_files=(),
        manifest=manifest,
        registry_entry=None,
        heartbeat=None,
        refresh_queue=None,
        restart_history=(),
        physical_index_exists=Path(index_dir).is_dir(),
        builder_live=False,
        live_index_stats=None,
        progress_samples=(),
        source_errors=tuple(source_errors),
        expected_files_unknown=expected_files_unknown,
    )


# --------------------------------------------------------------------------- #
# Internal helper — minimal "unknown" estimate dict (no snapshot to score)
# --------------------------------------------------------------------------- #


def _unknown_estimate_dict(reason: str | None) -> dict[str, Any]:
    advisory = reason or "no ticket-scoped index resolved."
    return {
        "schema_version": SCHEMA_VERSION,
        "index_name": None,
        "index_kind": "unknown",
        "health_state": "unknown",
        "encoder_running": None,
        "build_state": None,
        "build_state_reason": None,
        "outstanding_files": [],
        "outstanding_file_count": 0,
        "outstanding_files_truncated": False,
        "last_add_time": None,
        "recent_rate_files_per_min": None,
        "eta_seconds": None,
        "eta": "unknown",
        "confidence": "none",
        "advisory": advisory,
        "fail_open": True,
        "evidence": {},
        "errors": [reason] if reason else [],
    }


def _health_advisory(context: TicketContext, estimate_dict: Mapping[str, Any]) -> str:
    if context.mismatch_reason:
        return f"ticket-index resolution degraded: {context.mismatch_reason}"
    if context.index_name is None:
        return "no ticket-scoped index resolved."
    health_state = estimate_dict.get("health_state") or "unknown"
    ticket_label = context.ticket or "unresolved ticket"
    return f"index {health_state} for {ticket_label} scoped repo/worktree (index={context.index_name})."


# --------------------------------------------------------------------------- #
# CLI: health --command <lifecycle> --ticket <KEY> --repo-root <path> --json
# --------------------------------------------------------------------------- #

# A/C10 Phase C2: per-field plain-language glosses, keyed to the CURRENT
# resolved value of each field. `ok=True` is intentionally glossed as
# "the health check executed", never as "the index is healthy" — index
# health is a separate concept sourced from `estimate.health_state`/
# `estimate.liveness`.
_HEALTH_OK_GLOSS_TRUE = "health check ran; see estimate"
_HEALTH_OK_GLOSS_UNKNOWN = "health check status unknown"
# A/C10 REVISE MINOR 1: cover every real TicketContext confidence/source enum
# value with a specific, non-generic gloss (never "unknown"/"uncertain" — a
# real enum value is not the same as malformed/unrecognized input).
_HEALTH_CONFIDENCE_GLOSSARY: dict[str, str] = {
    "high": "ticket→index match certain",
    "medium": "ticket inferred from title",
    "low": "ticket inferred from branch",
    "none": "no index resolved",
}
_HEALTH_CONFIDENCE_GLOSS_UNKNOWN = "ticket→index match uncertain"
_HEALTH_SOURCE_GLOSSARY: dict[str, str] = {
    "explicit-ticket": "ticket passed explicitly",
    "session-binding": "bound to ticket at /jPlan",
    "session-title": "parsed from session title",
    "git-branch": "parsed from git branch",
    "none": "no ticket resolved",
}
_HEALTH_SOURCE_GLOSS_UNKNOWN = "ticket resolution source unknown"
_HEALTH_OVERLAY_GLOSS_WORKTREE = "overlay manifest is authoritative"
_HEALTH_OVERLAY_GLOSS_BASE = "base index; no overlay"
_HEALTH_OVERLAY_GLOSS_UNKNOWN = "index scope unknown; overlay status unclear"


def build_health_glossary(payload: Mapping[str, Any]) -> dict[str, str]:
    """A/C10 Phase C2: per-field plain-language meanings for a health payload.

    Pure and fail-open (never raises): keyed to the CURRENT resolved value of
    ``ok``, ``resolution.confidence``, ``resolution.source``, and
    ``resolution.index_kind`` so the gloss changes with the value rather than
    restating a generic schema description.
    """
    try:
        resolution = payload.get("resolution") if isinstance(payload, Mapping) else None
        resolution = resolution if isinstance(resolution, Mapping) else {}
        ok_value = payload.get("ok") if isinstance(payload, Mapping) else None
        confidence = resolution.get("confidence")
        source = resolution.get("source")
        index_kind = resolution.get("index_kind")

        if isinstance(index_kind, str) and index_kind.startswith("worktree"):
            overlay_gloss = _HEALTH_OVERLAY_GLOSS_WORKTREE
        elif index_kind == "base":
            overlay_gloss = _HEALTH_OVERLAY_GLOSS_BASE
        else:
            overlay_gloss = _HEALTH_OVERLAY_GLOSS_UNKNOWN

        return {
            "ok": _HEALTH_OK_GLOSS_TRUE if ok_value else _HEALTH_OK_GLOSS_UNKNOWN,
            "confidence": _HEALTH_CONFIDENCE_GLOSSARY.get(confidence, _HEALTH_CONFIDENCE_GLOSS_UNKNOWN),
            "resolution.source": _HEALTH_SOURCE_GLOSSARY.get(source, _HEALTH_SOURCE_GLOSS_UNKNOWN),
            "overlay_resolution": overlay_gloss,
        }
    except Exception:  # pragma: no cover - defensive fail-open net
        return {
            "ok": _HEALTH_OK_GLOSS_UNKNOWN,
            "confidence": _HEALTH_CONFIDENCE_GLOSS_UNKNOWN,
            "resolution.source": _HEALTH_SOURCE_GLOSS_UNKNOWN,
            "overlay_resolution": _HEALTH_OVERLAY_GLOSS_UNKNOWN,
        }


def render_health_human_summary(payload: Mapping[str, Any]) -> str:
    """A/C10 Phase C2: printable, explanatory digest of a health payload.

    Pure and fail-open (never raises): degrades gracefully when optional
    ``estimate`` fields (``last_indexed_at``, ``liveness``,
    ``outstanding_files``) are absent, never renders a bogus "Not yet
    indexed" list when there are no outstanding files, and never leaks a
    traceback into the returned text.
    """
    try:
        payload = payload if isinstance(payload, Mapping) else {}
        glossary = build_health_glossary(payload)

        resolution = payload.get("resolution")
        resolution = resolution if isinstance(resolution, Mapping) else {}
        estimate = payload.get("estimate")
        estimate = estimate if isinstance(estimate, Mapping) else {}

        ok_value = payload.get("ok")
        confidence = resolution.get("confidence")
        source = resolution.get("source")

        lines: list[str] = []

        last_indexed_at = estimate.get("last_indexed_at")
        if isinstance(last_indexed_at, str) and last_indexed_at:
            lines.append(f"Last indexed: {last_indexed_at}")

        liveness = estimate.get("liveness")
        liveness = liveness if isinstance(liveness, Mapping) else {}
        liveness_summary = liveness.get("summary")
        if isinstance(liveness_summary, str) and liveness_summary:
            lines.append(f"Liveness: {liveness_summary}")

        lines.append(f"ok={ok_value} — {glossary['ok']}")
        lines.append(f"confidence={confidence} — {glossary['confidence']}")
        lines.append(f"resolution.source={source} — {glossary['resolution.source']}")
        lines.append(f"Overlay resolution: {glossary['overlay_resolution']}")

        outstanding_files = estimate.get("outstanding_files")
        if isinstance(outstanding_files, list) and outstanding_files and _render_grouped_file_list is not None:
            paths = [
                entry.get("path")
                for entry in outstanding_files
                if isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
            ]
            # A/C10 REVISE MAJOR 1: cap locally (matching the footer's
            # max_folders=25/max_files_per_folder=25 convention) so this
            # public/pure renderer can never explode into thousands of lines
            # even when handed a raw, uncapped payload — it must not rely
            # solely on the upstream estimator's `outstanding_render_cap`.
            grouped_lines = _render_grouped_file_list(paths, max_folders=25, max_files_per_folder=25)
            if grouped_lines:
                lines.append("Not yet indexed:")
                lines.extend(grouped_lines)

        return "\n".join(lines)
    except Exception as exc:  # pragma: no cover - defensive fail-open net
        return f"health summary unavailable (fail-open): {exc}"


def _empty_health_payload(*, command: str, repo_root_display: str, reason: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "ticket": None,
        "repo_root": repo_root_display,
        "branch": None,
        "resolution": {
            "source": "none",
            "confidence": "none",
            "worktree_path": None,
            "project_slug": None,
            "index_name": None,
            "index_kind": "unknown",
            "mismatch_reason": reason,
        },
        "estimate": _unknown_estimate_dict(reason),
        "advisory": f"health check failed unexpectedly; degrading to unknown (fail-open): {reason}",
        "fail_open": True,
    }
    payload["glossary"] = build_health_glossary(payload)
    payload["human_summary"] = render_health_human_summary(payload)
    return payload


def build_health_payload(
    *,
    command: str,
    ticket: str | None,
    repo_root: str,
    session_id: str | None,
) -> dict[str, Any]:
    """Build the ``colgrep.ticket-index-health.v1`` payload. Never raises (fail-open)."""

    try:
        repo_root_path = Path(repo_root)
        try:
            repo_root_abs = str(repo_root_path.resolve())
        except Exception:
            repo_root_abs = str(repo_root_path)

        indices_root = Path(os.environ.get("COLGREP_INDICES", str(_DEFAULT_INDICES_ROOT.expanduser()))).expanduser()

        context = collect_ticket_context(
            repo_root=repo_root_path,
            explicit_ticket=ticket,
            session_id=session_id,
            indices_root=indices_root,
        )

        if context.index_dir is not None and context.index_name is not None:
            try:
                snapshot = collect_index_lag_snapshot(
                    index_dir=context.index_dir,
                    repo_root=repo_root_path,
                    ticket=context.ticket,
                    branch=context.branch,
                    index_name=context.index_name,
                    index_kind=context.index_kind,
                    base_docker_index_name=context.project_slug,
                )
                estimate_dict = estimate_to_dict(estimate_lag(snapshot))
            except Exception as exc:  # pragma: no cover - defensive fail-open
                estimate_dict = _unknown_estimate_dict(f"snapshot collection failed: {exc}")
        else:
            estimate_dict = _unknown_estimate_dict(context.mismatch_reason)

        advisory = _health_advisory(context, estimate_dict)

        payload: dict[str, Any] = {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "ok": True,
            "command": command,
            "ticket": context.ticket,
            "repo_root": repo_root_abs,
            "branch": context.branch,
            "resolution": {
                "source": context.source,
                "confidence": context.confidence,
                "worktree_path": context.worktree_path,
                "project_slug": context.project_slug,
                "index_name": context.index_name,
                "index_kind": context.index_kind,
                "mismatch_reason": context.mismatch_reason,
            },
            "estimate": estimate_dict,
            "advisory": advisory,
            "fail_open": True,
        }
        payload["glossary"] = build_health_glossary(payload)
        payload["human_summary"] = render_health_human_summary(payload)
        return payload
    except Exception as exc:  # pragma: no cover - top-level fail-open net
        try:
            repo_root_display = str(Path(repo_root).resolve())
        except Exception:
            repo_root_display = str(repo_root)
        return _empty_health_payload(command=command, repo_root_display=repo_root_display, reason=f"health CLI error: {exc}")


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="COM-233 ticket-scoped ColGREP index-lag health CLI (read-only, fail-open).",
    )
    # NB: dest must NOT be "command" — a "--command" option on the "health"
    # subparser below needs its own distinct dest so it cannot clobber this
    # subparsers dest (mirrors jswarm/colgrep_index_lifecycle.py:944-950).
    sub = parser.add_subparsers(dest="verb", required=True)

    p_health = sub.add_parser("health", help="Ticket-scoped index health snapshot (read-only, fail-open).")
    p_health.add_argument(
        "--command",
        dest="invoking_command",
        required=True,
        help="The lifecycle command invoking this health check (e.g. precompact, close-ticket).",
    )
    p_health.add_argument("--ticket", default=None)
    p_health.add_argument("--repo-root", required=True)
    p_health.add_argument("--session-id", default=None)
    p_health.add_argument("--json", action="store_true", help="Emit JSON (default; kept for CLI consistency).")

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        # Fail-open even on invalid CLI invocation: never let a bad argv
        # produce a non-zero process exit or missing-JSON stdout.
        print(json.dumps(_empty_health_payload(
            command="unknown", repo_root_display="", reason="invalid CLI arguments",
        )))
        return 0

    if args.verb == "health":
        session_id = args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID")
        payload = build_health_payload(
            command=args.invoking_command,
            ticket=args.ticket,
            repo_root=args.repo_root,
            session_id=session_id,
        )
        print(json.dumps(payload, sort_keys=True))
        return 0

    return 0  # pragma: no cover - unreachable: subparsers dest is required.


if __name__ == "__main__":
    sys.exit(_cli_main())
