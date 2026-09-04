"""Parse a work item id: a tracker key or a local slug, nothing else.

A tracker key is only meaningful once a tracker is configured (see
``jswarm.tracker``); a slug is always available. Parsing itself does not care
whether a tracker exists, it only recognises the two accepted shapes.
"""
from __future__ import annotations

import re
from typing import NamedTuple

TRACKER_KEY = r"^[A-Z][A-Z0-9]+-\d+$"
SLUG = r"^[a-z0-9][a-z0-9-]*$"

_TRACKER_KEY_RE = re.compile(TRACKER_KEY)
_SLUG_RE = re.compile(SLUG)


class WorkItemIdError(ValueError):
    """Raised when a raw string is neither a tracker key nor a slug."""


class WorkItemId(NamedTuple):
    value: str  # "PS-14" or "add-csv-export"
    kind: str  # "tracker-key" | "slug"


def parse(raw: str) -> WorkItemId:
    """Recognise ``raw`` as a tracker key or a slug, or raise ``WorkItemIdError``."""
    if _TRACKER_KEY_RE.match(raw):
        return WorkItemId(value=raw, kind="tracker-key")
    if _SLUG_RE.match(raw):
        return WorkItemId(value=raw, kind="slug")
    raise WorkItemIdError(
        f"{raw!r} is not a work item id. Use a tracker key like PS-14 "
        f"(pattern {TRACKER_KEY}) or a slug like add-csv-export "
        f"(pattern {SLUG})."
    )
