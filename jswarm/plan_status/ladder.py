"""Definition-of status ladder helpers shared by plan tooling.

Phase 8: UAT/NFR matrix status cells use a four-state ladder where
automation can advance rows to Ready, while Done requires explicit approval.
"""
from __future__ import annotations

BACKLOGGED = "backlogged"
DRAFTED = "drafted"
READY = "ready"
DONE = "done"
FAIL = "fail"

LABELS: dict[int, str] = {
    0: "🔴 Backlogged",
    1: "🟠 Drafted",
    2: "🟡 Ready",
    3: "🟢 Done",
}
TARGET_WEIGHTS: dict[str, int] = {
    BACKLOGGED: 0,
    DRAFTED: 1,
    READY: 2,
    DONE: 3,
}
GLYPH_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("🟢", 3),
    ("🟡", 2),
    ("🟠", 1),
    ("🔴", 0),
)


def weight_from_status(status: str) -> int:
    """Return ladder weight from a matrix Status cell; unknown is backlog weight."""
    for glyph, weight in GLYPH_WEIGHTS:
        if glyph in status:
            return weight
    return 0


def label_for_weight(weight: int) -> str:
    """Return canonical cell label for a ladder weight."""
    return LABELS[max(0, min(3, weight))]


def band_glyph(avg: float) -> str:
    """Return weighted-average color band glyph for UAT/NFR HUD color fields."""
    if avg < 0.5:
        return "🔴"
    if avg < 1.5:
        return "🟠"
    if avg < 2.5:
        return "🟡"
    return "🟢"
