"""Generated diagram views over a seam-spine (COM-234 Phase 7A, spec #295 S7.10).

Renders the seam path declared by a seam-spine's ``path``/``path_step``
records into human-facing diagram formats:

* ``render_mermaid`` -- a Mermaid ``flowchart`` diagram. Output is built
  from STABLE SORT KEYS -- ``path`` records sorted by ``record_id``, each
  path's steps sorted by their own explicit ``path_step.order`` field (or
  list position when absent), and a channel's edges sorted by
  ``record_id`` -- never from raw JSONL/list input order, dict/set
  iteration order, a timestamp, or a random id. This is what makes two
  runs over the SAME record set produce byte-identical stdout even when
  the records arrive in a different order (e.g. two JSONL files with the
  same records serialized in a different sequence): sorting removes input
  order as a variable before the graph is ever accumulated.
* ``render_structurizr`` -- an optional/Tier-2 export in a small
  ``code_overview.structurizr.v1`` JSON shape. When the spine has no
  derivable seam-path graph this raises ``StructurizrNotAdopted`` instead of
  emitting an empty or misleading diagram; callers must degrade gracefully
  (a documented exit code and message), never crash with a stack trace.
* ``render_summary`` -- a small JSON summary of the same graph, for callers
  that want counts rather than a diagram.

Layer A generic tooling: this module carries no project-specific
vocabulary. Record ids, names, and structure are all read from the
caller-supplied seam-spine; nothing project-specific is hard-coded here.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "StructurizrNotAdopted",
    "render_mermaid",
    "render_structurizr",
    "render_summary",
]

_ID_PREFIX = "PIPE-"


class StructurizrNotAdopted(Exception):
    """Raised when a Structurizr export cannot be derived from the spine.

    This is not adopted as a hard requirement (Tier-2/optional): callers
    must catch this and degrade gracefully -- a documented, non-zero exit
    code and an explicit "not adopted" message -- rather than let this
    propagate as a stack trace.
    """


def _record_id(record: dict[str, Any]) -> str | None:
    value = record.get("record_id")
    return value if isinstance(value, str) else None


def _display_name(record: dict[str, Any] | None, fallback: str) -> str:
    if not record:
        return fallback
    for key in ("name", "symbol_name", "surface_name"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def _escape_label(text: str) -> str:
    return text.replace('"', "'")


class _Graph:
    """Insertion-ordered node/edge accumulator (first-seen order is kept)."""

    def __init__(self) -> None:
        self.node_order: list[str] = []
        self.node_labels: dict[str, str] = {}
        self.edges: list[tuple[str, str, str, str | None]] = []
        self._edge_seen: set[tuple[str, str, str | None]] = set()
        self.path_headers: list[tuple[str, str]] = []

    def add_node(self, node_id: str, label: str) -> None:
        if node_id not in self.node_labels:
            self.node_order.append(node_id)
            self.node_labels[node_id] = label

    def add_edge(self, from_id: str, to_id: str, relationship: str, edge_record_id: str | None) -> None:
        key = (from_id, to_id, edge_record_id)
        if key in self._edge_seen:
            return
        self._edge_seen.add(key)
        self.edges.append((from_id, to_id, relationship, edge_record_id))

    def add_path_header(self, path_id: str, name: str) -> None:
        self.path_headers.append((path_id, name))

    def is_empty(self) -> bool:
        return not self.node_order


def _sorted_by_record_id(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort by ``record_id`` -- the generic, schema-agnostic tie-break
    used everywhere a graph-construction step needs a deterministic order
    that does not depend on the input records' own list/file order."""
    return sorted(records, key=lambda record: record.get("record_id") or "")


def _resolve_steps_in_order(
    step_record_ids: list[Any],
    records_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve a path's ``step_record_ids`` to step records in a stable order.

    Sorted by each step's own explicit ``order`` field when present (the
    schema's declared sequencing signal), falling back to the step's
    position within ``step_record_ids`` when ``order`` is absent, with
    ``record_id`` as a final tie-break. This never depends on the global
    records list's own order.
    """
    resolved: list[tuple[Any, str, dict[str, Any]]] = []
    for position, step_id in enumerate(step_record_ids or []):
        step = records_by_id.get(step_id) if isinstance(step_id, str) else None
        if not step:
            continue
        order_key = step.get("order", position)
        resolved.append((order_key, step.get("record_id") or "", step))
    resolved.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in resolved]


def _build_seam_path_graph(records: list[dict[str, Any]]) -> _Graph:
    records_by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        if isinstance(record, dict):
            record_id = _record_id(record)
            if record_id:
                records_by_id[record_id] = record

    graph = _Graph()

    paths = _sorted_by_record_id(
        [record for record in records if isinstance(record, dict) and record.get("record_type") == "path"]
    )
    all_edges = _sorted_by_record_id(
        [record for record in records if isinstance(record, dict) and record.get("record_type") == "edge"]
    )

    for record in paths:
        path_id = _record_id(record)
        if not path_id:
            continue
        graph.add_path_header(path_id, _display_name(record, path_id))

        for step in _resolve_steps_in_order(record.get("step_record_ids") or [], records_by_id):
            seam_id = step.get("seam_id")
            seam = records_by_id.get(seam_id) if isinstance(seam_id, str) else None
            if not seam_id or not seam:
                continue

            graph.add_node(seam_id, _display_name(seam, seam_id))

            for anchor_id in list(seam.get("producer_anchor_ids") or []):
                anchor = records_by_id.get(anchor_id)
                if anchor:
                    graph.add_node(anchor_id, _display_name(anchor, anchor_id))
                    graph.add_edge(anchor_id, seam_id, "produces", None)

            for anchor_id in list(seam.get("consumer_anchor_ids") or []):
                anchor = records_by_id.get(anchor_id)
                if anchor:
                    graph.add_node(anchor_id, _display_name(anchor, anchor_id))
                    graph.add_edge(seam_id, anchor_id, "consumed_by", None)

            for channel_id in list(seam.get("channel_ids") or []):
                channel = records_by_id.get(channel_id)
                if channel:
                    graph.add_node(channel_id, _display_name(channel, channel_id))
                    graph.add_edge(seam_id, channel_id, "carries_on", None)

                for edge in all_edges:
                    if edge.get("channel_id") != channel_id:
                        continue
                    edge_record_id = _record_id(edge)
                    from_id = edge.get("from_record_id")
                    to_id = edge.get("to_record_id")
                    for anchor_id in list(edge.get("anchor_ids") or []):
                        anchor = records_by_id.get(anchor_id)
                        if anchor:
                            graph.add_node(anchor_id, _display_name(anchor, anchor_id))
                    if isinstance(from_id, str):
                        from_record = records_by_id.get(from_id)
                        graph.add_node(from_id, _display_name(from_record, from_id))
                    if isinstance(to_id, str):
                        to_record = records_by_id.get(to_id)
                        graph.add_node(to_id, _display_name(to_record, to_id))
                    if isinstance(from_id, str) and isinstance(to_id, str):
                        graph.add_edge(from_id, to_id, edge.get("edge_kind") or "edge", edge_record_id)

    return graph


def render_mermaid(records: list[dict[str, Any]]) -> str:
    """Render the seam-path graph as a deterministic Mermaid flowchart.

    Two calls over the same ``records`` value always produce the identical
    string: node/edge order follows first-seen order while walking
    ``records`` itself (never a dict/set iteration order), and nothing
    time- or randomness-derived is ever emitted.
    """
    graph = _build_seam_path_graph(records)

    lines = [
        "%% Mermaid diagram: generated seam-path view (deterministic, no timestamps)",
        "flowchart TD",
    ]
    for path_id, name in graph.path_headers:
        lines.append(f"%% path {path_id}: {name}")
    for node_id in graph.node_order:
        label = _escape_label(graph.node_labels[node_id])
        lines.append(f'  {node_id}["{label}"]')
    for from_id, to_id, relationship, edge_record_id in graph.edges:
        suffix = f" %% edge {edge_record_id}: {relationship}" if edge_record_id else f" %% {relationship}"
        lines.append(f"  {from_id} --> {to_id}{suffix}")

    return "\n".join(lines) + "\n"


def render_structurizr(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Render an optional Structurizr JSON export of the seam-path graph.

    Raises ``StructurizrNotAdopted`` when no seam-path graph can be derived
    from ``records`` (e.g. a spine with no ``path``/``path_step`` chain) so
    callers can degrade gracefully instead of emitting an empty diagram.
    """
    graph = _build_seam_path_graph(records)
    if graph.is_empty():
        raise StructurizrNotAdopted(
            "Structurizr export is not adopted for this spine: no seam-path graph "
            "could be derived (no path/path_step/seam chain present)."
        )

    elements = [
        {"id": node_id, "name": graph.node_labels[node_id]}
        for node_id in graph.node_order
    ]
    relationships = [
        {
            "source": from_id,
            "destination": to_id,
            "description": relationship,
            "edge_record_id": edge_record_id,
        }
        for from_id, to_id, relationship, edge_record_id in graph.edges
    ]

    return {
        "schema_version": "code_overview.structurizr.v1",
        "elements": elements,
        "relationships": relationships,
    }


def render_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """A small deterministic JSON summary of the seam-path graph."""
    graph = _build_seam_path_graph(records)
    return {
        "schema_version": "code_overview.render_summary.v1",
        "path_count": len(graph.path_headers),
        "node_count": len(graph.node_order),
        "edge_count": len(graph.edges),
        "node_ids": list(graph.node_order),
    }
