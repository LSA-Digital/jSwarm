"""COM-119 P2 — canonical JSON serialization for deterministic builds.

The canonical form is the input the Astro build consumes. Determinism (AC-119.5
+ R4) requires byte-stable serialization across runs and across mutation-
equivalent inputs (key reordering, equivalent whitespace).

Canonical-form rules:
  - Keys sorted at every depth (``json.dumps(sort_keys=True)``).
  - ``ensure_ascii=False`` — UTF-8 in the file, not ``\\uXXXX`` escapes.
  - Separators ``(",", ": ")`` — single-space after colon, no space after comma.
    Compact + diff-stable; matches the feature-dashboard-system contract.
  - Final newline terminator (POSIX).
  - **No BOM**: the file is written with UTF-8 encoding without a byte-order
    mark. ``str.encode("utf-8")`` is BOM-less by default — we never use
    ``utf-8-sig``.

The output is consumed by Astro's ``import data from '../.tmp-build/data.canonical.json'``
build-time import (P3). Hash stability is what the ``--check`` drift gate (P4)
will diff against.

Public API:
  - :func:`canonicalize(data) -> str` — return the canonical form.
  - :func:`write_canonical(data, out_path)` — write the canonical form
    BOM-less UTF-8 to ``out_path`` (creating parents as needed).
  - :func:`hash_canonical(data) -> str` — SHA-256 of the canonical bytes;
    callers use this for drift detection without re-canonicalizing.

Acceptance criteria coverage:
  - AC-119.4 — canonical data object handed to the build.
  - AC-119.5 (P4 will consume) — byte-stable output for ``--check`` drift gate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonicalize(data: object) -> str:
    """Return the canonical JSON form of ``data`` as a UTF-8 string.

    ``allow_nan=False`` rejects NaN/Infinity/-Infinity outright (raises
    ``ValueError``). Standard JSON has no representation for non-finite
    numbers; emitting Python's non-standard ``NaN``/``Infinity`` tokens
    would break Astro's ``import data from '...canonical.json'`` at build
    time. Reject at the canonical-form boundary, not downstream.
    """
    return (
        json.dumps(
            data,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ": "),
            allow_nan=False,
        )
        + "\n"
    )


def write_canonical(data: object, out_path: Path) -> Path:
    """Write the canonical form to ``out_path`` (UTF-8, BOM-less, final newline).

    Parents are created if missing. The file is written atomically via a
    temp-file + ``Path.replace`` so a partial write never lands on disk.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonicalize(data).encode("utf-8")  # BOM-less by default.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(out_path)
    return out_path


def hash_canonical(data: object) -> str:
    """SHA-256 hex digest of the canonical bytes (drift-gate helper)."""
    return hashlib.sha256(canonicalize(data).encode("utf-8")).hexdigest()
