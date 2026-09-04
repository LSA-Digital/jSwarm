"""Generic COM-108 compliance sink sanitizer.

Upstream (common) also reuses the COM-112 finding secret/PII regex tuple
(``compliance/substrate/findings.py``) here by identity. That substrate is an
internal compliance-controls governance/reporting system (event log, dimension
taxonomy, schema validation against its own internal docs) and is not part of
this repository, so this public copy does not import it. Instead this module
carries its own local, self-contained pattern set: PEM key blocks, host-local
paths, a key-like-token set (GitHub/GitLab/Slack/npm/PyPI/AWS/Bearer/generic
secret-assignment patterns), plus bare email addresses, ``sk-``-prefixed keys,
and JWT-shaped tokens -- ordinary, generic regexes with no coupling to the
enterprise substrate, restored here from the same family jswarm/leakgate.yaml
already declares for commits (this module is the equivalent check at install
time: jswarm/installer/preflight/__init__.py gates on it). This is a local
pattern set, not a governance-backed one: it detects what is listed above and
nothing the upstream substrate additionally covers beyond that.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# COM-112 substrate.findings._SECRET_PATTERNS is not imported here (see the
# module docstring) but three of its detectors have no local equivalent and
# are restored below as ordinary, uncoupled regexes: bare email/PII, sk-
# prefixed keys, and JWT-shaped tokens. The other four upstream detectors
# (AKIA, PEM private key, host-local paths, Bearer) are already covered by
# this module's own patterns further down.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", re.IGNORECASE),
)

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
    """Redact PEM keys, host-local paths, this module's own key-like-token set,
    bare email addresses, ``sk-``-prefixed keys, and JWT-shaped tokens.

    A local pattern set (see the module docstring), not a governance-backed one.
    """

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
