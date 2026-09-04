"""Generic COM-108 compliance sink sanitizer.

This module intentionally reuses the COM-112 finding secret/PII regex tuple by
identity so every COM-108 sink has the same fail-closed leak boundary as the
shipped substrate.  COM-108 also owns a local generic publish-sink hardening
layer for common key-like tokens; the upstream COM-112 substrate patterns remain
consume-only for this phase.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

try:  # package import when called as jswarm.compliance.*
    from .substrate.findings import _SECRET_PATTERNS
except ImportError:  # top-level import used by legacy jswarm/tests
    from substrate.findings import _SECRET_PATTERNS  # type: ignore[no-redef]

_REDACTION_MARKER = "[REDACTED]"
_PEM_PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_PEM_PRIVATE_KEY_END_MARKER_PATTERN = re.compile(r"-----END[ A-Z]*PRIVATE KEY-----", re.IGNORECASE)
_HOST_LOCAL_PATH_TOKEN_PATTERN = re.compile(
    r"(?:/Users/|/home/|/root/)[^\s\"'<>),\]}]*|[A-Za-z]:\\[^\s\"'<>),\]}]*|[^\s\"'<>),\]}]*(?:\.ssh/[^\s\"'<>),\]}]+)"
)
_KEY_LIKE_TOKEN_PATTERNS = (
    # GitHub classic + server + OAuth/user/refresh tokens (ghp_/gho_/ghu_/ghs_/ghr_) and fine-grained PATs.
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    # Common SaaS/package-manager tokens.
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bpypi-[A-Za-z0-9_\-]{20,}\b"),
    # AWS access key IDs.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Bearer tokens in logs/config fields.
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE),
    # Assignment forms such as api_key=..., secret: ..., token = ... with a
    # high-entropy-looking value.  Keep this substring-based so secrets are
    # caught inside larger field values without treating ordinary prose as a
    # secret.
    re.compile(
        r"\b(?:api[_-]?key|secret|token|access[_-]?token|client[_-]?secret)\b"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=:\-]{20,}['\"]?",
        re.IGNORECASE,
    ),
)


def _sanitize_string(value: str) -> str:
    """Redact complete secret tokens while keeping COM-112 detectors authoritative."""

    sanitized = _PEM_PRIVATE_KEY_BLOCK_PATTERN.sub(_REDACTION_MARKER, value)
    sanitized = _PEM_PRIVATE_KEY_END_MARKER_PATTERN.sub(_REDACTION_MARKER, sanitized)
    sanitized = _HOST_LOCAL_PATH_TOKEN_PATTERN.sub(_REDACTION_MARKER, sanitized)
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(_REDACTION_MARKER, sanitized)
    for pattern in _KEY_LIKE_TOKEN_PATTERNS:
        sanitized = pattern.sub(_REDACTION_MARKER, sanitized)
    return sanitized


def contains_sensitive(value: Any) -> bool:
    """Return True exactly when ``sanitize(value)`` would redact ``value``."""

    return sanitize(value) != value


def sanitize(value: Any) -> Any:
    """Redact secret/PII/path material while preserving container shape."""

    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, bytes):
        return value
    if isinstance(value, Mapping):
        return {sanitize(key): sanitize(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return tuple(sanitize(nested) for nested in value)
    if isinstance(value, list):
        return [sanitize(nested) for nested in value]
    if isinstance(value, frozenset):
        return frozenset(sanitize(nested) for nested in value)
    if isinstance(value, set):
        return {sanitize(nested) for nested in value}
    return value


__all__ = ["_SECRET_PATTERNS", "contains_sensitive", "sanitize"]
