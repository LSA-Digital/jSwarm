"""COM-389 Phase 4 — ticket-local /fix gate-settings resolver (SC-15).

Parses the fixed-name ``.fix-settings.yaml`` dotfile that lives beside a
ticket's plan artifacts (exemplar: HAS-617). The service only consumes the
gate-authorization subset:

    schema: jswarm.fix-settings/v1
    ticket: <key>
    gates:
      defect_contract: owner-authorized | auto
      fix_contract:    owner-authorized | auto

The optional ``gates.revisions_within_scope`` key independently authorizes
non-repair-authorizing informational revisions; older files without it retain
their prior gate-mode behavior. Other fields (contract_review, tail, ...) remain
orchestrator-side policy this service does not interpret. Parsing is strict
and fail-closed: unknown schema, missing gates, or a mode outside the enum is
a typed :class:`SettingsError` — a malformed settings file can never
authorize anything. Uses PyYAML (present in the project ``.venv``), which
handles the exemplar's comments natively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SETTINGS_SCHEMA_CONST = "jswarm.fix-settings/v1"
GATE_MODES = ("owner-authorized", "auto")
GATE_KEYS = ("defect_contract", "fix_contract")
REVISION_KEY = "revisions_within_scope"


class SettingsError(Exception):
    """Typed base for every settings-resolution failure (fail closed)."""


class SettingsNotFoundError(SettingsError):
    """The settings file does not exist / is not readable."""


class SettingsParseError(SettingsError):
    """The settings file is not valid YAML (or not a mapping)."""


class SettingsInvalidError(SettingsError):
    """The settings file parses but violates the gate subset contract."""


@dataclass(frozen=True)
class GateSettings:
    """The resolved gate-authorization subset of a ticket's fix settings."""

    schema: str
    ticket: str
    gates: dict = field(default_factory=dict)

    def gate_mode(self, gate_key: str) -> str:
        if gate_key not in GATE_KEYS:
            raise SettingsInvalidError(f"unknown gate key: {gate_key!r} (expected one of {GATE_KEYS})")
        return self.gates[gate_key]

    def within_scope_revision_mode(self, fallback_gate_key: str) -> str:
        """Resolve non-authorizing revision policy.

        Older settings files predate the explicit revisions key; those retain
        their prior gate-mode behavior. Current files may declare
        ``revisions_within_scope: auto`` independently from the owner-gated
        first repair-authorizing contract.
        """
        return self.gates.get(REVISION_KEY, self.gate_mode(fallback_gate_key))


def parse_gate_settings(text: str) -> GateSettings:
    """Parse and validate the gate subset of a settings document (str input).

    Raises SettingsParseError / SettingsInvalidError; never returns a partial
    result.
    """
    import yaml  # project .venv dependency; imported lazily to keep module import cheap

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SettingsParseError(f"settings document is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise SettingsParseError(f"settings document must be a YAML mapping; got {type(document).__name__}")
    schema = document.get("schema")
    if schema != SETTINGS_SCHEMA_CONST:
        raise SettingsInvalidError(f"unsupported settings schema: {schema!r} (expected {SETTINGS_SCHEMA_CONST!r})")
    ticket = document.get("ticket")
    if not isinstance(ticket, str) or not ticket.strip():
        raise SettingsInvalidError("ticket must be a non-empty string")
    gates = document.get("gates")
    if not isinstance(gates, dict):
        raise SettingsInvalidError("gates must be a mapping of gate key -> mode")
    resolved: dict = {}
    for key in GATE_KEYS:
        mode = gates.get(key)
        if mode not in GATE_MODES:
            raise SettingsInvalidError(
                f"gates.{key} must be one of {list(GATE_MODES)}; got {mode!r}"
            )
        resolved[key] = mode
    revision_mode = gates.get(REVISION_KEY)
    if revision_mode is not None:
        if revision_mode not in GATE_MODES:
            raise SettingsInvalidError(
                f"gates.{REVISION_KEY} must be one of {list(GATE_MODES)}; got {revision_mode!r}"
            )
        resolved[REVISION_KEY] = revision_mode
    return GateSettings(schema=schema, ticket=ticket, gates=resolved)


def load_gate_settings(path: Path | str) -> GateSettings:
    """Read + parse a settings file from disk.

    Raises SettingsNotFoundError (missing/unreadable), SettingsParseError, or
    SettingsInvalidError.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SettingsNotFoundError(f"settings file not found: {target}") from exc
    except OSError as exc:
        raise SettingsNotFoundError(f"settings file unreadable: {target}: {exc}") from exc
    return parse_gate_settings(text)
