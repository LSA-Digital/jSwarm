"""Fail-closed leak gate for COM-162 service-template renders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from jswarm.compliance.sanitizer import contains_sensitive, sanitize

from .schema import _ALLOWED_PLACEHOLDERS  # type: ignore[attr-defined]

_ALLOWED_PLACEHOLDERS_WITH_PORTS = set(_ALLOWED_PLACEHOLDERS)
_PLACEHOLDER_RE = re.compile(r"{{\s*([^{}]+?)\s*}}")
_PEM_PRIVATE_RE = re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----|-----END[ A-Z]*PRIVATE KEY-----", re.IGNORECASE)
_PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?:^|[^A-Za-z0-9_])/(?:Users|home)/[^\s\"'<>),\]}{/]+(?:/[^\s\"'<>),\]}{]*)?"),
    re.compile(r"(?:^|[^A-Za-z0-9_])/root(?:/[^\s\"'<>),\]}{]*)?"),
    re.compile(r"[A-Za-z]:\\Users\\[^\s\"'<>),\]}{]+(?:\\[^\s\"'<>),\]}{]*)?", re.IGNORECASE),
    re.compile(r"(?:^|[\\/])(?:\.ssh|\.gnupg|\.aws)(?:[\\/]|$)"),
    re.compile(r"(?:^|[\\/])\.config[\\/]gh(?:[\\/]|$)"),
)
_SECRET_ENV_ASSIGNMENT_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:_TOKEN|_KEY|_SECRET|_PASSWORD))\b\s*[:=]\s*([^\s\n\r\"']+|\"[^\"\r\n]*\"|'[^'\r\n]*')"
)
_SECRET_PLACEHOLDER_RE = re.compile(r"^{{\s*(?:[A-Z][A-Z0-9_]*|PORT_[A-Z0-9_]+)\s*}}$")
_ENV_FILE_REFERENCES = ("{{ENV_FILE}}", "$ENV_FILE", "%ENV_FILE%")
_DEFAULT_REDACTION_MARKER = "[REDACTED]"


@dataclass(frozen=True)
class LeakGateFailure:
    """Redacted leak-gate failure; never includes raw sensitive content."""

    relpath: str
    failure_class: str
    message: str


@dataclass(frozen=True)
class LeakGateResult:
    """Structured leak-gate result for all would-be rendered files."""

    passed: bool
    failures: tuple[LeakGateFailure, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "failures": [failure.__dict__ for failure in self.failures],
        }


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _path_failure(relpath: str, target_root: Path) -> LeakGateFailure | None:
    services_root = (target_root / ".jarviswarm" / "services").resolve()
    candidate = services_root / relpath
    try:
        relative = Path(relpath)
        if relative.is_absolute() or ".." in relative.parts:
            return LeakGateFailure(relpath, "output-path-escape", "output path must be relative within target .jarviswarm/services")
        if not _under(candidate, services_root):
            return LeakGateFailure(relpath, "output-path-escape", "output path resolves outside target .jarviswarm/services")
    except OSError:
        return LeakGateFailure(relpath, "output-path-escape", "output path could not be resolved safely")
    return None


def _secret_env_assignment_failures(relpath: str, text: str) -> list[LeakGateFailure]:
    failures: list[LeakGateFailure] = []
    for match in _SECRET_ENV_ASSIGNMENT_RE.finditer(text):
        if _is_allowed_secret_env_value(match.group(2)):
            continue
        failures.append(
            LeakGateFailure(
                relpath,
                "literal-secret-env-value",
                f"secret-like environment value for {match.group(1)} must use an approved placeholder or env-file reference",
            )
        )
    return failures


def _is_allowed_secret_env_value(value: str) -> bool:
    raw_value = value.strip().strip("'\"")
    if raw_value in _ENV_FILE_REFERENCES or any(ref in raw_value for ref in _ENV_FILE_REFERENCES):
        return True
    return _SECRET_PLACEHOLDER_RE.fullmatch(raw_value) is not None


def redact_sensitive_env_assignments(text: str, *, marker: str = _DEFAULT_REDACTION_MARKER) -> str:
    """Redact COM-162-local secret-like environment assignment values."""

    redacted_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.removesuffix("\n")
        newline = "\n" if line.endswith("\n") else ""
        if body.endswith("\r"):
            body = body[:-1]
            newline = "\r" + newline
        redacted_lines.append(_redact_sensitive_env_line(body, marker=marker) + newline)
    if text == "":
        return ""
    return "".join(redacted_lines)


def _redact_sensitive_env_line(line: str, *, marker: str) -> str:
    match = _SECRET_ENV_ASSIGNMENT_RE.search(line)
    if match is None or _is_allowed_secret_env_value(match.group(2)):
        return line
    value_start, value_end = match.span(2)
    value = line[value_start:value_end]
    leading_len = len(value) - len(value.lstrip())
    trailing_len = len(value) - len(value.rstrip())
    core = value.strip()
    quote = core[:1] if core[:1] in {"'", '"'} else ""
    suffix = quote if quote and core.endswith(quote) else ""
    replacement = f"{quote}{marker}{suffix}"
    return f"{line[: value_start + leading_len]}{replacement}{line[value_end - trailing_len:]}"


def _unknown_placeholder_failures(relpath: str, text: str) -> list[LeakGateFailure]:
    failures: list[LeakGateFailure] = []
    for match in _PLACEHOLDER_RE.finditer(text):
        token = match.group(1).strip()
        if token.startswith("PORT_") and len(token) > len("PORT_"):
            continue
        if token not in _ALLOWED_PLACEHOLDERS_WITH_PORTS:
            failures.append(
                LeakGateFailure(relpath, "unknown-placeholder", "render output contains an unknown service template placeholder")
            )
    return failures


def check_file(relpath: str, text: str, *, target_root: Path) -> tuple[LeakGateFailure, ...]:
    """Return redacted failures for one would-be output file."""

    failures: list[LeakGateFailure] = []
    path_failure = _path_failure(relpath, target_root)
    if path_failure is not None:
        failures.append(path_failure)

    if contains_sensitive(text):
        failures.append(
            LeakGateFailure(
                relpath,
                "sanitizer-sensitive-content",
                "canonical sanitizer detected sensitive content in render output",
            )
        )
    if sanitize(text) != text:
        failures.append(
            LeakGateFailure(
                relpath,
                "sanitizer-redaction-required",
                "render output would require redaction before publication",
            )
        )
    if _PEM_PRIVATE_RE.search(text):
        failures.append(LeakGateFailure(relpath, "pem-private-key", "PEM private-key material is forbidden"))
    if any(pattern.search(text) for pattern in _PRIVATE_PATH_PATTERNS):
        failures.append(
            LeakGateFailure(relpath, "private-host-path", "render output contains a private host path fragment")
        )
    failures.extend(_secret_env_assignment_failures(relpath, text))
    failures.extend(_unknown_placeholder_failures(relpath, text))
    return tuple(failures)


def run_leak_gate(content_by_relpath: Mapping[str, str], *, target_root: Path) -> LeakGateResult:
    """Run the fail-closed service-template leak gate over would-be outputs."""

    failures: list[LeakGateFailure] = []
    for relpath, text in content_by_relpath.items():
        failures.extend(check_file(relpath, text, target_root=target_root))
    return LeakGateResult(passed=not failures, failures=tuple(failures))
