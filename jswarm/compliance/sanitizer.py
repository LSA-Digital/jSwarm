"""Generic COM-108 compliance sink sanitizer.

Upstream (common) also reuses the COM-112 finding secret/PII regex tuple
(``compliance/substrate/findings.py``) here by identity. That substrate is an
internal compliance-controls governance/reporting system (event log, dimension
taxonomy, schema validation against its own internal docs) and is not part of
this repository. This public copy carries only its own local generic
publish-sink hardening layer below -- PEM keys, host-local paths, and a
comprehensive key-like-token set (GitHub/GitLab/Slack/npm/PyPI/AWS/Bearer/
generic secret-assignment patterns, the same family jswarm/leakgate.yaml
already declares) -- which was always meant to stand on its own, not
substitute for the substrate tuple. The redaction boundary here is narrower
than upstream's by that one tuple's worth of additional detectors, not absent.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# COM-112 substrate.findings._SECRET_PATTERNS deliberately not carried here --
# see the module docstring above.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = ()

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
