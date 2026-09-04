"""Trace distiller (Phase 5).

Ingests heterogeneous raw runtime-observation sources (SSE samples, outbox
rows, a Temporal-history adapter, and generic preview/UI evidence), normalizes
them to one raw-event shape, and resolves each event against a declarative
set of resolution rules into a canonical ``resolution_status``:

* ``resolved_exact`` -- a direct claim in the raw event itself names an
  active, declared spine record with no rule needed.
* ``resolved_rule`` -- exactly one resolution rule matched.
* ``ambiguous`` -- more than one resolution rule matched; no seam/edge is
  chosen and every candidate is preserved in ``candidate_resolutions``.
* ``unmapped`` -- no resolution rule matched.
* ``conflicts`` -- a matching rule's target disagrees with a direct claim
  already present in the raw event's own payload.

Every input event produces exactly one output event -- nothing is ever
dropped, and an event that cannot be resolved is retained as ``unmapped``
rather than discarded. A rule whose ``truth_authority`` is
``presentation_only`` (a preview/SSE presentation surface) can never assign a
seam or terminal edge, regardless of what the rule itself claims -- it is
capped to a low-confidence ``ui_surface_id`` only. A rule that does resolve
to an edge is only honored when that edge is an ACTIVE, declared edge in the
spine (reusing ``audits._active``); otherwise the event is treated as
unmapped rather than fabricating a resolved edge.

Layer A generic tooling: this module carries no project-specific vocabulary.
All project-specific values (event types, reason codes, record ids) live in
the caller-supplied ``raw_sources``/``rules`` inputs and in tests/fixtures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from audits._active import is_active_edge
from schema import load_spine

__all__ = ["distill"]

SCHEMA_VERSION = "code_overview.trace_distill.v1"

RESOLUTION_STATUSES: tuple[str, ...] = (
    "resolved_exact",
    "resolved_rule",
    "ambiguous",
    "unmapped",
    "conflicts",
)

_PRESENTATION_ONLY = "presentation_only"
_PRESENTATION_CONFIDENCE_CEILING = 0.5

_SEGMENT_SANITIZER = re.compile(r"[^A-Za-z0-9_]+")


# ---------------------------------------------------------------------------
# Record-id helpers (D-1 grammar: PIPE-<TYPE>.<...>.<NNN>)
# ---------------------------------------------------------------------------


def _sanitize_segment(value: str) -> str:
    cleaned = _SEGMENT_SANITIZER.sub("_", str(value).strip()).strip("_")
    return cleaned.upper() or "X"


def _run_id_segments(run_id: str, limit: int = 3) -> list[str]:
    raw_segments = [segment for segment in str(run_id).split(".") if segment]
    sanitized = [_sanitize_segment(segment) for segment in raw_segments] or ["RUN"]
    return sanitized[:limit]


def _next_record_id(type_token: str, run_id: str, ordinal: int) -> str:
    tail = ".".join(_run_id_segments(run_id))
    return f"PIPE-{type_token}.{tail}.{ordinal:03d}"


# ---------------------------------------------------------------------------
# Dotted-path lookup shared by rule matching and normalization.
# ---------------------------------------------------------------------------


def _dotted_get(obj: Any, dotted_path: str) -> Any:
    current = obj
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# Collectors: read one raw_sources[] entry into a list of raw rows.
# ---------------------------------------------------------------------------


@dataclass
class _SourceRead:
    status: str  # "ok" | "source_unavailable" | "parse_error"
    rows: list[dict[str, Any]] = field(default_factory=list)
    detail: str | None = None


def _read_ndjson_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(json.loads(stripped))
    return rows


def _navigate_json_path(payload: Any, json_path: str | None) -> Any:
    if not json_path:
        return payload
    current = payload
    for part in json_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError(
                f"json_path {json_path!r} did not resolve inside the source document"
            )
    return current


def _read_json_list_rows(path: Path, json_path: str | None) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = _navigate_json_path(payload, json_path)
    if not isinstance(events, list):
        raise ValueError(
            f"source at {path} did not resolve to a list at json_path={json_path!r}"
        )
    return [event for event in events if isinstance(event, dict)]


def _lower_first(value: str) -> str:
    return value[:1].lower() + value[1:] if value else value


def _read_temporal_history_rows(path: Path) -> list[dict[str, Any]]:
    """Q-1-inferred Temporal history adapter.

    Reads a ``history.events[]`` array and derives each event's attribute
    key generically from Temporal's own naming convention
    (``lowerCamel(eventType) + "EventAttributes"``) rather than hardcoding
    any one event type. Failure payloads are flattened into a normalized
    ``payload`` dict; the resulting rows share the same common shape as the
    sse/outbox collectors so a single normalizer can treat all sources
    uniformly downstream.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    workflow_execution = payload.get("workflowExecution") or {}
    workflow_id = workflow_execution.get("workflowId", "unknown")
    run_id = workflow_execution.get("runId", "unknown")
    events = _dotted_get(payload, "history.events") or []

    rows: list[dict[str, Any]] = []
    for entry in events:
        if not isinstance(entry, dict):
            continue
        event_id = entry.get("eventId", "unknown")
        event_type = entry.get("eventType")
        attrs_key = f"{_lower_first(event_type)}EventAttributes" if event_type else None
        attrs = entry.get(attrs_key) if attrs_key else None

        flattened: dict[str, Any] = {}
        if isinstance(attrs, dict):
            activity_type = attrs.get("activityType")
            if isinstance(activity_type, dict) and activity_type.get("name"):
                flattened["activity_type"] = activity_type["name"]
            if attrs.get("activityId"):
                flattened["activity_id"] = attrs["activityId"]
            failure = attrs.get("failure")
            if isinstance(failure, dict):
                if failure.get("message"):
                    flattened["failure_message"] = failure["message"]
                application_failure_info = failure.get("applicationFailureInfo")
                if isinstance(application_failure_info, dict):
                    if application_failure_info.get("type"):
                        flattened["failure_type"] = application_failure_info["type"]
                    details = application_failure_info.get("details") or {}
                    payloads = details.get("payloads") if isinstance(details, dict) else None
                    if isinstance(payloads, list) and payloads:
                        data = payloads[0].get("data") if isinstance(payloads[0], dict) else None
                        if isinstance(data, dict):
                            flattened.update(data)

        rows.append(
            {
                "raw_event_id": f"temporal.{workflow_id}.{run_id}.{event_id}",
                "source": "temporal_history",
                "event_type": f"temporal.{event_type}" if event_type else "temporal.unknown",
                "created_at": entry.get("eventTime"),
                "payload": flattened,
            }
        )
    return rows


def _read_source_rows(source: dict[str, Any]) -> _SourceRead:
    path = Path(source.get("path", ""))
    source_type = source.get("source_type", "")
    fmt = source.get("format", "ndjson")

    if not path.is_file():
        return _SourceRead(status="source_unavailable", detail=f"path not found: {path}")

    try:
        if source_type == "temporal_history":
            rows = _read_temporal_history_rows(path)
        elif fmt == "ndjson":
            rows = _read_ndjson_rows(path)
        elif fmt == "json":
            rows = _read_json_list_rows(path, source.get("json_path"))
        else:
            return _SourceRead(status="parse_error", detail=f"unsupported format {fmt!r}")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return _SourceRead(status="parse_error", detail=str(error))

    return _SourceRead(status="ok", rows=rows)


# ---------------------------------------------------------------------------
# Normalization: every collector's rows funnel through one common shape.
# ---------------------------------------------------------------------------


def _normalize_row(row: dict[str, Any], *, default_source: str) -> dict[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    event_type = row.get("event_type") or payload.get("event_type") or row.get("sse_event")
    return {
        "raw_event_id": row.get("raw_event_id"),
        "source": row.get("source") or default_source,
        "event_type": event_type,
        "timestamp": row.get("created_at") or row.get("timestamp"),
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Rule matching and the resolution decision tree.
# ---------------------------------------------------------------------------


def _matches_rule(normalized: dict[str, Any], rule: dict[str, Any]) -> bool:
    source_types = rule.get("source_types")
    if source_types and normalized.get("source") not in source_types:
        return False
    match_spec: dict[str, Any] = rule.get("match") or {}
    context = {"event_type": normalized.get("event_type"), "payload": normalized.get("payload") or {}}
    for dotted_path, expected in match_spec.items():
        if _dotted_get(context, dotted_path) != expected:
            return False
    return True


def _rule_evidence(rule: dict[str, Any]) -> list[str]:
    evidence = rule.get("resolution_evidence")
    if isinstance(evidence, list) and evidence:
        return list(evidence)
    match_spec = rule.get("match") or {}
    return [f"{key}={value}" for key, value in match_spec.items()]


def _candidate_from_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": rule.get("rule_id"),
        "seam_id": rule.get("target_seam_id"),
        "channel_id": rule.get("target_channel_id"),
        "edge_id": rule.get("target_edge_id"),
        "ui_surface_id": rule.get("target_ui_surface_id"),
        "confidence": rule.get("confidence"),
        "resolution_evidence": _rule_evidence(rule),
    }


def _direct_claims(payload: dict[str, Any]) -> dict[str, Any]:
    """Direct IDs the raw event's own payload claims (``seam_id``/``channel_id``/``edge_id``).

    Used both to detect a rule/direct-claim conflict (:func:`_resolve_event`)
    and, when no rule matches at all, to resolve the event directly via
    :func:`_resolve_direct_claim`.
    """

    claims: dict[str, Any] = {}
    if payload.get("seam_id"):
        claims["target_seam_id"] = payload["seam_id"]
    edge_claim = payload.get("resolved_edge_id") or payload.get("edge_id")
    if edge_claim:
        claims["target_edge_id"] = edge_claim
    if payload.get("channel_id"):
        claims["target_channel_id"] = payload["channel_id"]
    return claims


# Presentation-authority classifier (recall-first, named + extensible sets).
#
# Any one of the following independently marks a raw event as
# presentation-only evidence (a preview/rendered/UI surface) rather than a
# terminal-truth observation, regardless of whether any resolution rule
# matched it:
#
#   1. ``source`` is a known presentation-surface source.
#   2. ``event_type`` names the ``preview.`` namespace (any sub-event -- e.g.
#      ``preview.rendered``, ``preview.updated``, ``preview.*`` future
#      variants) or an explicitly enumerated presentation event type.
#   3. The payload itself carries a presentation-only signal (rendered
#      markup, a UI surface id, or a preview ``fact_key`` render marker) and
#      does not carry an explicit terminal-authority marker overriding it.
#
# Extend these sets as new presentation surfaces/event namespaces appear --
# do not special-case one exact shape.
_PRESENTATION_SOURCES: frozenset[str] = frozenset({"preview_outbox", "ui_preview"})
_PRESENTATION_EVENT_TYPE_PREFIXES: tuple[str, ...] = ("preview.",)
_PRESENTATION_EVENT_TYPES: frozenset[str] = frozenset()
_PRESENTATION_PAYLOAD_SIGNAL_FIELDS: tuple[str, ...] = ("rendered_html", "ui_surface_id", "fact_key")
_TERMINAL_AUTHORITY_MARKER = "terminal_truth"


def _is_presentation_shaped(normalized: dict[str, Any]) -> bool:
    """True when a raw event's own source/event_type/payload shape marks it
    as presentation-only evidence (e.g. a rendered preview surface) rather
    than a terminal-truth observation -- independent of whether any
    resolution rule matched it.

    Recall-first: this classifies by presentation-authority CLASS (source
    family, event-type namespace, or payload signal), not one exact
    source/event_type/field combination, so new presentation surfaces are
    caught without a code change to every call site. A payload that carries
    an explicit terminal-authority marker (``payload.truth_authority ==
    "terminal_truth"``) is exempted from the payload-signal check so a
    genuinely terminal event that happens to reuse a presentation-shaped
    field name is not misclassified.
    """

    source = normalized.get("source")
    if isinstance(source, str) and source in _PRESENTATION_SOURCES:
        return True

    event_type = normalized.get("event_type")
    if isinstance(event_type, str):
        if event_type in _PRESENTATION_EVENT_TYPES:
            return True
        if any(event_type.startswith(prefix) for prefix in _PRESENTATION_EVENT_TYPE_PREFIXES):
            return True

    payload = normalized.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if payload.get("truth_authority") == _TERMINAL_AUTHORITY_MARKER:
        return False
    return any(field_name in payload for field_name in _PRESENTATION_PAYLOAD_SIGNAL_FIELDS)


def _resolve_direct_claim(
    normalized: dict[str, Any],
    *,
    active_edge_ids: frozenset[str],
) -> _Resolution:
    """Resolve a raw event's own direct seam/channel/edge claim when no rule matched.

    A direct claim only reaches ``resolved_exact`` when its ``edge_id`` is a
    non-empty string naming an edge that is active and declared in the spine
    -- an inactive, superseded, undeclared, malformed (non-string), or
    absent edge claim downgrades to ``unmapped`` and fabricates no seam,
    channel, or edge; a raw event's own say-so is never sufficient by
    itself. A malformed (non-string) seam/channel claim is likewise dropped
    rather than propagated. A presentation-shaped raw event (see
    :func:`_is_presentation_shaped`) downgrades its own ``status`` to
    ``unmapped`` here regardless of the edge claim's validity, because a
    direct claim from presentation evidence is never sufficient authority
    for ``resolved_exact``. This function does NOT itself set
    ``truth_authority`` or clear seam/channel/edge/confidence -- that
    classification is applied exactly once, for every resolution path
    alike, by the single post-resolution chokepoint
    (:func:`_apply_presentation_authority`) that :func:`_resolve_event`
    routes every resolution through, so this branch cannot drift from the
    matched-rule/ambiguous/conflicts branches.
    """

    payload = normalized.get("payload") or {}
    claims = _direct_claims(payload)
    edge_claim = claims.get("target_edge_id")
    if not isinstance(edge_claim, str) or not edge_claim or edge_claim not in active_edge_ids:
        return _Resolution(status="unmapped")

    seam_claim = claims.get("target_seam_id")
    seam_claim = seam_claim if isinstance(seam_claim, str) and seam_claim else None
    channel_claim = claims.get("target_channel_id")
    channel_claim = channel_claim if isinstance(channel_claim, str) and channel_claim else None

    evidence = [f"raw payload direct claim payload.edge_id={edge_claim!r} names an active declared edge"]
    if seam_claim:
        evidence.append(f"raw payload direct claim payload.seam_id={seam_claim!r}")
    if channel_claim:
        evidence.append(f"raw payload direct claim payload.channel_id={channel_claim!r}")

    resolution = _Resolution(
        status="resolved_exact",
        seam_id=seam_claim,
        channel_id=channel_claim,
        edge_id=edge_claim,
        rule_id=None,
        confidence=1.0,
        evidence=evidence,
    )

    if _is_presentation_shaped(normalized):
        ui_surface_id = payload.get("ui_surface_id")
        resolution.ui_surface_id = ui_surface_id if isinstance(ui_surface_id, str) and ui_surface_id else None
        resolution.status = "unmapped"
        resolution.evidence = resolution.evidence + [
            f"raw event is presentation-shaped (source={normalized.get('source')!r}, "
            f"event_type={normalized.get('event_type')!r}); a direct claim from presentation "
            "evidence cannot assign terminal seam/edge truth"
        ]

    return resolution


@dataclass
class _Resolution:
    status: str
    seam_id: str | None = None
    channel_id: str | None = None
    edge_id: str | None = None
    ui_surface_id: str | None = None
    rule_id: str | None = None
    confidence: float | None = None
    evidence: list[str] = field(default_factory=list)
    truth_authority: str | None = None
    candidates: list[dict[str, Any]] | None = None


def _apply_presentation_guard(resolution: _Resolution) -> _Resolution:
    """A presentation-only rule can never claim terminal truth.

    Regardless of what the rule itself declares, a ``presentation_only``
    truth authority is forced to drop any seam/channel/edge target and is
    capped to a low confidence -- this guard is independent of rule content
    so a malformed or hostile rule cannot smuggle a presentation event into
    terminal-truth status. Clearing ``channel_id`` alongside ``seam_id``/
    ``edge_id`` matters because a channel id alone is still a terminal-truth
    routing claim -- leaving it set would let presentation evidence
    smuggle a channel binding past the guard even with no seam/edge.
    """

    if resolution.truth_authority == _PRESENTATION_ONLY:
        resolution.seam_id = None
        resolution.channel_id = None
        resolution.edge_id = None
        if resolution.confidence is None or resolution.confidence > _PRESENTATION_CONFIDENCE_CEILING:
            resolution.confidence = _PRESENTATION_CONFIDENCE_CEILING
    return resolution


def _apply_presentation_authority(normalized: dict[str, Any], resolution: _Resolution) -> _Resolution:
    """Single post-resolution presentation-authority chokepoint.

    :func:`_resolve_event` routes EVERY resolution it produces through this
    one function before returning it -- rule-matched (single or ambiguous),
    direct-claim, conflicts, and inactive-edge-downgrade branches alike.
    This is the ONLY place in the module that decides presentation-authority
    truth, so no per-branch code path can independently choose to skip it.

    If the raw event's own source, event-type namespace, or an
    un-countermanded payload signal marks it as presentation-shaped (see
    :func:`_is_presentation_shaped`), ``truth_authority`` is force-set to
    ``presentation_only`` and the resolution is routed through
    :func:`_apply_presentation_guard`, which strips any seam/channel/edge
    target and caps confidence. This OVERRIDES a matched rule's own
    ``truth_authority="terminal_truth"`` declaration and a raw payload's own
    claimed ``truth_authority="terminal_truth"`` whenever the
    presentation-shaping comes from source or event-type authority --
    :func:`_is_presentation_shaped` returns ``True`` unconditionally for
    those two classes, ignoring any terminal-authority marker, precisely so
    a preview source or a ``preview.*`` event can never smuggle terminal
    truth past this chokepoint by matching a terminal rule or by carrying a
    self-declared ``truth_authority="terminal_truth"`` payload field.

    The payload-signal-only escape hatch (a non-preview source/event whose
    ONLY presentation signal is a payload field such as ``rendered_html``,
    ``ui_surface_id``, or ``fact_key``, alongside an explicit
    ``payload.truth_authority == "terminal_truth"`` marker) is preserved
    because :func:`_is_presentation_shaped` itself returns ``False`` for
    that shape -- this chokepoint does not re-implement or special-case
    that exemption; it asks the one classifier once and applies the one
    guard.
    """

    if _is_presentation_shaped(normalized):
        resolution.truth_authority = _PRESENTATION_ONLY
        resolution = _apply_presentation_guard(resolution)
    return resolution


def _resolve_event_core(
    normalized: dict[str, Any],
    rules: Sequence[dict[str, Any]],
    *,
    active_edge_ids: frozenset[str],
) -> _Resolution:
    """Resolution decision tree, prior to the presentation-authority chokepoint.

    Do not call this directly outside :func:`_resolve_event` -- it wraps
    every value this function returns through the single presentation-
    authority chokepoint (:func:`_apply_presentation_authority`) so no
    branch here (rule-matched, ambiguous, conflicts, direct-claim, or
    inactive-edge downgrade) can bypass it.
    """

    payload = normalized.get("payload") or {}
    matching_rules = [rule for rule in rules if _matches_rule(normalized, rule)]

    if not matching_rules:
        return _resolve_direct_claim(normalized, active_edge_ids=active_edge_ids)

    if len(matching_rules) > 1:
        candidates = [_candidate_from_rule(rule) for rule in matching_rules]
        return _Resolution(
            status="ambiguous",
            evidence=[f"{len(matching_rules)} resolution rules matched this event; no auto-pick"],
            candidates=candidates,
        )

    rule = matching_rules[0]
    direct_claims = _direct_claims(payload)
    conflict_fields = [
        field_name
        for field_name, claimed_value in direct_claims.items()
        if rule.get(field_name) not in (None, claimed_value)
    ]
    if conflict_fields:
        evidence = [
            f"raw payload claims {field_name}={direct_claims[field_name]!r} but rule "
            f"{rule.get('rule_id')!r} targets {field_name}={rule.get(field_name)!r}"
            for field_name in conflict_fields
        ]
        return _Resolution(status="conflicts", rule_id=rule.get("rule_id"), evidence=evidence)

    resolution = _Resolution(
        status=rule.get("resolution_status") or "resolved_rule",
        seam_id=rule.get("target_seam_id"),
        channel_id=rule.get("target_channel_id"),
        edge_id=rule.get("target_edge_id"),
        ui_surface_id=rule.get("target_ui_surface_id"),
        rule_id=rule.get("rule_id"),
        confidence=rule.get("confidence"),
        evidence=_rule_evidence(rule),
        truth_authority=rule.get("truth_authority"),
    )

    if resolution.edge_id and resolution.edge_id not in active_edge_ids:
        return _Resolution(
            status="unmapped",
            evidence=resolution.evidence
            + [
                f"rule {resolution.rule_id!r} targets edge {resolution.edge_id!r}, "
                "which is not an active declared edge in the spine"
            ],
        )

    return resolution


def _resolve_event(
    normalized: dict[str, Any],
    rules: Sequence[dict[str, Any]],
    *,
    active_edge_ids: frozenset[str],
) -> _Resolution:
    """Resolve one normalized raw event to its canonical resolution.

    This is the single entry point callers must use (see :func:`distill`).
    The full decision tree lives in :func:`_resolve_event_core`; this
    wrapper is the one and only place that pipes every resolution it
    produces through :func:`_apply_presentation_authority` before handing
    it back -- structurally guaranteeing that presentation-authority
    classification runs on every resolved event (rule-matched,
    direct-claim, ambiguous, conflicts, and inactive-edge downgrade alike)
    with no path-dependent hole, because no branch inside
    :func:`_resolve_event_core` returns directly to :func:`distill` -- every
    return value flows back through this one wrapper first.
    """

    resolution = _resolve_event_core(normalized, rules, active_edge_ids=active_edge_ids)
    return _apply_presentation_authority(normalized, resolution)


def _load_active_edge_ids(spine_path: str | Path) -> frozenset[str]:
    try:
        records = load_spine(spine_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return frozenset()
    return frozenset(
        record["record_id"]
        for record in records
        if record.get("record_type") == "edge" and record.get("record_id") and is_active_edge(record)
    )


# ---------------------------------------------------------------------------
# Overlay record construction.
# ---------------------------------------------------------------------------


def _build_trace_event(
    normalized: dict[str, Any],
    resolution: _Resolution,
    *,
    run_id: str,
    source_path: str,
    ordinal: int,
) -> dict[str, Any]:
    payload = normalized.get("payload") or {}
    status_value = payload.get("status")
    return {
        "record_id": _next_record_id("TRACE", run_id, ordinal),
        "record_type": "trace_event",
        "record_status": "declared",
        "schema_version": "1.0",
        "run_id": run_id,
        "timestamp": normalized.get("timestamp"),
        "source": normalized.get("source"),
        "raw_event_ref": {
            "raw_event_id": normalized.get("raw_event_id"),
            "path": source_path,
        },
        "event_type": normalized.get("event_type"),
        "seam_id": resolution.seam_id,
        "channel_id": resolution.channel_id,
        "resolved_edge_id": resolution.edge_id,
        "ui_surface_id": resolution.ui_surface_id,
        "producer_anchor_id": None,
        "consumer_anchor_id": None,
        "carrier": payload.get("carrier"),
        "reason_code": payload.get("reason_code"),
        "status": status_value if isinstance(status_value, str) and status_value else "unknown",
        "resolution_status": resolution.status,
        "resolution_rule_id": resolution.rule_id,
        "resolution_evidence": resolution.evidence,
        "confidence": resolution.confidence,
        "truth_authority": resolution.truth_authority,
        "candidate_resolutions": resolution.candidates,
    }


def _build_observation_summaries(events: list[dict[str, Any]], *, run_id: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        seam_id = event.get("seam_id")
        if not seam_id:
            continue
        grouped.setdefault(seam_id, []).append(event)

    summaries: list[dict[str, Any]] = []
    for ordinal, seam_id in enumerate(sorted(grouped), start=1):
        seam_events = grouped[seam_id]
        observed_edge_ids = sorted({e["resolved_edge_id"] for e in seam_events if e.get("resolved_edge_id")})
        source_types = sorted({e["source"] for e in seam_events if e.get("source")})
        failed_count = sum(1 for e in seam_events if e.get("status") == "failed")
        summaries.append(
            {
                "record_id": _next_record_id("TRACESUMMARY", run_id, ordinal),
                "record_type": "trace_observation_summary",
                "record_status": "declared",
                "schema_version": "1.0",
                "seam_id": seam_id,
                "observed_count": len(seam_events),
                "failed_count": failed_count,
                "unpaired_count": 0,
                "dropped_count": 0,
                "last_observed_run_id": run_id,
                "source_types": source_types,
                "observed_edge_ids": observed_edge_ids,
            }
        )
    return summaries


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def distill(
    *,
    raw_sources: list[dict[str, Any]],
    spine_path: str | Path,
    rules: list[dict[str, Any]],
    run_id: str,
    overlay_scope: str = "branch",
) -> dict[str, Any]:
    """Collect, normalize, and resolve raw runtime-observation sources.

    Every raw row read from an available source produces exactly one output
    event -- unresolved events are retained as ``unmapped``/``ambiguous``,
    never dropped. A source that cannot be read is recorded in
    ``source_statuses`` as ``source_unavailable``/``parse_error`` with zero
    fabricated events; ``exit_code`` is ``5`` only when nothing usable was
    produced at all (no events and at least one source was not ``ok``).
    """

    active_edge_ids = _load_active_edge_ids(spine_path)

    source_statuses: list[dict[str, Any]] = []
    normalized_events: list[dict[str, Any]] = []

    for source in raw_sources:
        path_text = str(source.get("path", ""))
        read_result = _read_source_rows(source)
        source_statuses.append(
            {
                "source_type": source.get("source_type"),
                "path": path_text,
                "status": read_result.status,
                "event_count": len(read_result.rows) if read_result.status == "ok" else 0,
            }
        )
        if read_result.status != "ok":
            continue
        default_source = source.get("source_type", "unknown")
        for row in read_result.rows:
            normalized = _normalize_row(row, default_source=default_source)
            normalized["source_path"] = path_text
            normalized_events.append(normalized)

    raw_event_count = len(normalized_events)

    events: list[dict[str, Any]] = []
    for ordinal, normalized in enumerate(normalized_events, start=1):
        resolution = _resolve_event(normalized, rules, active_edge_ids=active_edge_ids)
        events.append(
            _build_trace_event(
                normalized,
                resolution,
                run_id=run_id,
                source_path=normalized.get("source_path", ""),
                ordinal=ordinal,
            )
        )

    summaries = _build_observation_summaries(events, run_id=run_id)
    overlay_records = list(events) + summaries

    exit_code = 0
    if raw_event_count == 0 and any(status["status"] != "ok" for status in source_statuses):
        exit_code = 5

    summary_counts = {status_name: 0 for status_name in RESOLUTION_STATUSES}
    for event in events:
        status_name = event.get("resolution_status")
        if status_name in summary_counts:
            summary_counts[status_name] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "overlay_scope": overlay_scope,
        "exit_code": exit_code,
        "source_statuses": source_statuses,
        "raw_event_count": raw_event_count,
        "normalized_event_count": len(normalized_events),
        "events": events,
        "overlay_records": overlay_records,
        "summary": summary_counts,
    }
