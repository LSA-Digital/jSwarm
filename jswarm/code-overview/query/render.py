"""Generic (Layer A) token-budgeted ``code_overview.query.v1`` packet renderer.

Wraps :mod:`query.index` ranking into the stable query-output contract and
enforces NFR-234-002 (default 1,200-token budget): if the serialized packet
would exceed budget, the lowest-ranked paths are dropped one at a time --
never silently truncated -- and every drop is recorded in ``omissions``.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .index import SpineIndex, build_full_pathway_records, rank_paths

__all__ = ["DEFAULT_BUDGET_TOKENS", "count_tokens", "render_query_result"]

DEFAULT_BUDGET_TOKENS = 1200

_WORD_PATTERN = re.compile(r"\S+")


def count_tokens(text: str) -> tuple[int, str]:
    """Return ``(estimated_tokens, counter_name)`` for ``text``.

    Uses ``tiktoken`` (``cl100k_base``) when importable in the active
    environment; otherwise falls back to a deterministic, conservative
    heuristic: ``max(non_whitespace_word_count, ceil(character_count / 4))``.
    """

    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text)), "tiktoken_cl100k_base"
    except Exception:
        word_count = len(_WORD_PATTERN.findall(text))
        char_estimate = math.ceil(len(text) / 4)
        return max(word_count, char_estimate), "word_char_fallback_v1"


def _serialize(packet: dict[str, Any]) -> str:
    return json.dumps(packet, ensure_ascii=False, indent=2) + "\n"


def _attach_l4_expansion(
    index: SpineIndex,
    ranked_paths: list[dict[str, Any]],
    *,
    full_index_source_excerpts: dict[str, dict[str, Any]],
) -> None:
    """Add ``source_excerpts``/``full_pathway_records`` to each ranked path (LOD4 only).

    A/C 6: ``source_excerpts`` text comes exclusively from the precomputed
    ``generated/full-index.json`` (``full_index_source_excerpts``, keyed by
    anchor ``record_id``) -- never live-resolved from source here. An
    anchor absent from the persisted index (e.g. an out-of-range/stale
    anchor `build` already dropped -- see ADV-1) is simply omitted from
    ``source_excerpts`` rather than emitted with empty text.
    """

    for ranked_path in ranked_paths:
        ranked_path["full_pathway_records"] = build_full_pathway_records(index, ranked_path)
        excerpts: list[dict[str, Any]] = []
        for step in ranked_path.get("pathway") or []:
            anchor_id = step.get("record_id")
            if not anchor_id:
                continue
            excerpt = full_index_source_excerpts.get(anchor_id)
            if excerpt is not None:
                excerpts.append(excerpt)
        ranked_path["source_excerpts"] = excerpts


def render_query_result(
    index: SpineIndex,
    *,
    query_text: str,
    lod: int,
    spine_path: str,
    resolution_mode: str,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    scenario_id: str | None = None,
    carrier: str | None = None,
    l4_source_excerpts: dict[str, dict[str, Any]] | None = None,
    allowed_path_ids: set[str] | None = None,
    token_index: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build the full ``code_overview.query.v1`` packet, enforcing ``budget_tokens``.

    ``resolution_mode`` (one of ``"project_anchor"`` or ``"fixture_pilot"``,
    see ``cli._resolve_spine_path``) is surfaced verbatim in
    ``packet["source"]["resolution_mode"]`` alongside ``spine_path`` so a
    caller can always tell whether a query answer came from a project's own
    configured spine or from the packaged pilot fixture.

    At ``lod=4`` (and only then), and only when ``l4_source_excerpts`` is
    supplied, every ranked path is augmented with ``source_excerpts``
    (anchor source lines, sourced exclusively from the caller's precomputed
    ``generated/full-index.json`` -- see A/C 6 and ``query.build.
    load_full_index_for_query``) and ``full_pathway_records`` (the expanded
    seam/channel/ui_surface/anchor records behind them) -- see NFR-234-002.

    Ranking (:func:`query.index.rank_paths`) is driven by
    ``allowed_path_ids`` and ``token_index`` at every LOD, not just LOD4: when
    the caller found a precomputed ``generated/summary-index.json`` query
    index, ``allowed_path_ids`` (see ``query.build.
    resolve_ranking_candidate_ids``) restricts *which* ``path`` records are
    ever candidates, and ``token_index`` (the same query index's inverted
    token index) drives *scoring and order* -- both membership and ranking
    come from the persisted index, never from re-tokenizing spine text. This
    is the A/C 6 "L0-L4 query over precomputed indexes" contract.
    ``allowed_path_ids=None``/``token_index=None`` (no ``generated/`` index
    available) falls back to the original in-memory behavior: every ``path``
    record in the loaded spine stays a candidate and is scored by
    re-tokenizing its own and its linked seam/reason-code text directly.
    """

    ranked_paths = rank_paths(
        index,
        query_text,
        scenario_id=scenario_id,
        carrier=carrier,
        allowed_path_ids=allowed_path_ids,
        token_index=token_index,
    )
    omissions: list[dict[str, Any]] = []

    if lod < 4:
        omissions.append(
            {
                "kind": "lod_source_excerpts_omitted",
                "detail": f"Source excerpts are omitted below LOD 4 (requested lod={lod}).",
            }
        )
    elif l4_source_excerpts is not None:
        _attach_l4_expansion(index, ranked_paths, full_index_source_excerpts=l4_source_excerpts)

    packet: dict[str, Any] = {
        "schema_version": "code_overview.query.v1",
        "query": query_text,
        "lod": lod,
        "source": {"spine_path": spine_path, "resolution_mode": resolution_mode},
        "budget": {"limit_tokens": budget_tokens, "counter": "unset", "estimated_tokens": 0},
        "ranked_paths": ranked_paths,
        "omissions": omissions,
    }

    while True:
        if lod == 4:
            # Recompute the excerpt-accounting fields every iteration (not
            # just once at the end): they must be present *inside* the
            # serialized packet that estimated_tokens below measures, or a
            # later drop could invalidate an already-reported "under budget"
            # verdict.
            excerpt_lists = [path.get("source_excerpts") or [] for path in packet["ranked_paths"]]
            excerpt_text_blob = "\n".join(
                excerpt.get("text", "") for excerpts in excerpt_lists for excerpt in excerpts
            )
            excerpt_tokens, _ = count_tokens(excerpt_text_blob)
            packet["budget"]["source_excerpt_count"] = sum(len(excerpts) for excerpts in excerpt_lists)
            packet["budget"]["source_excerpt_token_estimate"] = excerpt_tokens

        # `budget.counter`/`budget.estimated_tokens` are themselves part of the
        # serialized packet being measured, so writing a freshly computed
        # value can change the packet's own length (an "unset"/0 placeholder
        # is a different width than e.g. "word_char_fallback_v1"/1198).
        # Re-measure until the stored fields and the freshly computed values
        # agree, so the reported estimate matches what is actually emitted.
        estimated_tokens, counter_name = count_tokens(_serialize(packet))
        for _ in range(4):
            if packet["budget"].get("counter") == counter_name and packet["budget"].get("estimated_tokens") == estimated_tokens:
                break
            packet["budget"]["counter"] = counter_name
            packet["budget"]["estimated_tokens"] = estimated_tokens
            estimated_tokens, counter_name = count_tokens(_serialize(packet))
        packet["budget"]["counter"] = counter_name
        packet["budget"]["estimated_tokens"] = estimated_tokens
        if estimated_tokens <= budget_tokens or not packet["ranked_paths"]:
            break

        last_path = packet["ranked_paths"][-1]
        excerpts = last_path.get("source_excerpts")
        if isinstance(excerpts, list) and excerpts:
            # Prefer trimming source excerpts (lowest-ranked path first, last
            # excerpt first) over dropping a whole ranked path: NFR-234-002
            # requires disclosure either way, never a silent truncation.
            omitted = excerpts.pop()
            packet["omissions"].append(
                {
                    "kind": "budget_source_excerpt_omitted",
                    "detail": (
                        f"Dropped source excerpt for anchor {omitted.get('anchor_id')!r} from ranked "
                        f"path {last_path.get('path_id')!r} to stay within the {budget_tokens}-token budget."
                    ),
                }
            )
            continue

        dropped = packet["ranked_paths"].pop()
        packet["omissions"].append(
            {
                "kind": "budget_path_dropped",
                "detail": (
                    f"Dropped ranked path {dropped.get('path_id')!r} to stay within the "
                    f"{budget_tokens}-token budget."
                ),
            }
        )

    return packet
