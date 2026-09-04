#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jswarm.host import current as _current_host


DEFAULT_MANIFEST_PATH = _current_host().project_dir(Path(".")) / "project-command-injections.yaml"
DEFAULT_SNIPPET_ROOT = _current_host().project_dir(Path(".")) / "command-injections"
JPLAN_COMMAND_KEY = "jPlan.md"
# Keep the legacy alias composed so removing its temporary broad authorization later
# does not resurface the raw legacy token in repository bytes.
LEGACY_JPLAN_COMMAND_KEY = "new-" + "work.md"
JPLAN_COMMAND_KEYS = (JPLAN_COMMAND_KEY, LEGACY_JPLAN_COMMAND_KEY)
PHASE_ONE_MANAGED_COMMANDS = frozenset({JPLAN_COMMAND_KEY, "implement.md", "test.md"})
# Commands that MAY be declared `state: managed` in a project manifest (validation allowlist).
# Superset of the auto-bootstrap set. Two members are opt-in PER PROJECT and deliberately NOT
# auto-scaffolded (they stay out of PHASE_ONE_MANAGED_COMMANDS):
#   - close-ticket.md  — component-governance gate (only common wires it).
#   - code-overview.md — UAT-scenario engine adopters (already wired for hai-sim-engine).
#     Real global command named code-overview.md, in this host's commands directory
#     (`jswarm.host.claude_code.ClaudeCodeHost.commands_dir`), with three inject anchors;
#     added to the allowlist by WS4 so an adopter manifest validates and its
#     jPlan parameters resolve (the allowlist was stale relative to that adopter).
MANAGED_COMMAND_ALLOWLIST = PHASE_ONE_MANAGED_COMMANDS | frozenset(
    {LEGACY_JPLAN_COMMAND_KEY, "close-ticket.md", "code-overview.md", "uat-round.md"}
)
ANCHOR_PATTERN = re.compile(
    r"^\s*<!--\s*inject:(?P<name>[a-zA-Z0-9._-]+)\s*-->\s*$", re.MULTILINE
)


class CommandInjectionError(ValueError):
    """Raised when command injection config or render state is invalid."""


@dataclass(frozen=True)
class RenderResult:
    action: str
    mode: str
    text: str
    substitutions: dict[str, str] = field(default_factory=dict)
    applied_anchors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditResult:
    status: str
    messages: tuple[str, ...]


@dataclass(frozen=True)
class BootstrapResult:
    manifest_path: Path
    manifest_text: str
    snippet_texts: dict[str, str]
    commands: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class InspectResult:
    mode: str
    manifest_exists: bool
    manifest_path: Path
    configured_state: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class InspectSkillResult:
    """Phase 2: localization inspection result for a managed skill.

    Serialization (CLI JSON payload) yields exactly these six fields; keep this
    dataclass's field set aligned with the stable JSON contract.
    """

    mode: str
    manifest_exists: bool
    manifest_path: Path
    configured_state: str
    overlay_path: str | None = None
    warnings: tuple[str, ...] = ()


PROJECT_IDENTITIES: dict[str, dict[str, str]] = {
    "common": {
        "ticket_prefix": "COM",
        "jira_key": "COM",
        "colgrep_index": "common",
        "project_name": "common",
    },
    "hai-sim-engine": {
        "ticket_prefix": "HAS",
        "jira_key": "HAS",
        "colgrep_index": "hai-sim-engine",
        "project_name": "hai-sim-engine",
    },
    "epms": {
        "ticket_prefix": "EPMS",
        "jira_key": "LSARS",
        "colgrep_index": "epms",
        "project_name": "epms",
    },
    "haisim": {
        "ticket_prefix": "HAISIM",
        "jira_key": "HAISIM",
        "colgrep_index": "haisim",
        "project_name": "haisim",
    },
    "lsars-hra": {
        "ticket_prefix": "LSARS",
        "jira_key": "LSARS",
        "colgrep_index": "lsars-hra",
        "project_name": "lsars-hra",
    },
    "lsars-datalab": {
        "ticket_prefix": "LSARS",
        "jira_key": "LSARS",
        "colgrep_index": "lsars-datalab",
        "project_name": "lsars-datalab",
    },
    "lsars-pi": {
        "ticket_prefix": "LSARS",
        "jira_key": "LSARS",
        "colgrep_index": "lsars-pi",
        "project_name": "lsars-pi",
    },
}


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_lines = [line.rstrip() for line in normalized.split("\n")]
    normalized = "\n".join(normalized_lines).strip("\n")
    return f"{normalized}\n" if normalized else ""


def project_identity(project_name: str) -> dict[str, str]:
    normalized = project_name.strip()
    if normalized in PROJECT_IDENTITIES:
        return dict(PROJECT_IDENTITIES[normalized])

    fallback_key = normalized.upper().replace("-", "_")
    return {
        "ticket_prefix": fallback_key,
        "jira_key": fallback_key,
        "colgrep_index": normalized,
        "project_name": normalized,
    }


JIRA_KEY_PARAMETER = "jira_project_key"


def resolve_jira_project_key(project_root: Path) -> str | None:
    """Resolve the Jira project key for ``project_root``.

    Order: ``managed_commands.jPlan.md.parameters.jira_project_key`` in the
    project manifest (fail-open reader) -> the built-in ``PROJECT_IDENTITIES``
    table by directory name -> ``None`` (caller must ask the user).
    """
    project_root = Path(project_root)
    params = command_parameters_for_project(project_root, JPLAN_COMMAND_KEY)
    key = params.get(JIRA_KEY_PARAMETER) if isinstance(params, dict) else None
    if isinstance(key, str) and key.strip():
        return key.strip().upper()
    name = project_root.name.strip()
    if name in PROJECT_IDENTITIES:
        return PROJECT_IDENTITIES[name]["jira_key"]
    return None


def project_command_substitutions(
    source_project_name: str, target_project_name: str
) -> dict[str, str]:
    source = project_identity(source_project_name)
    target = project_identity(target_project_name)

    substitutions = {
        f'"project_key": "{source["jira_key"]}"': f'"project_key": "{target["jira_key"]}"',
        f"{source['ticket_prefix']}-XXX": f"{target['ticket_prefix']}-XXX",
        f"{source['ticket_prefix']}-{{NUMBER}}": f"{target['ticket_prefix']}-{{NUMBER}}",
        f'"index": "{source["colgrep_index"]}"': f'"index": "{target["colgrep_index"]}"',
        '"project_key": "COM"': f'"project_key": "{target["jira_key"]}"',
        "TICKET-XXX": f"{target['ticket_prefix']}-XXX",
        "TICKET-{NUMBER}": f"{target['ticket_prefix']}-{{NUMBER}}",
        '"index": "<project>"': f'"index": "{target["colgrep_index"]}"',
    }
    return substitutions


def _parse_key(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        raise CommandInjectionError("Empty YAML key")
    if raw[0] in {'"', "'"}:
        quote = raw[0]
        if len(raw) < 2 or raw[-1] != quote:
            raise CommandInjectionError(f"Unterminated quoted key: {raw}")
        return raw[1:-1]
    return raw


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value == "{}":
        return {}
    if value == "[]":
        return []
    if value[0] in {'"', "'"} and value[-1] == value[0]:
        return value[1:-1]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2 != 0:
            raise CommandInjectionError(
                f"Invalid indentation at line {line_number}: use 2-space indentation"
            )

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        current = stack[-1][1]
        if ":" not in stripped:
            raise CommandInjectionError(f"Invalid YAML entry at line {line_number}: {raw_line}")

        key_part, value_part = stripped.split(":", 1)
        key = _parse_key(key_part)
        value = value_part.strip()

        if not value:
            next_mapping: dict[str, Any] = {}
            current[key] = next_mapping
            stack.append((indent, next_mapping))
            continue

        current[key] = _parse_scalar(value)

    return root


def load_manifest(project_root: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    root = Path(project_root)
    relative_manifest = manifest_path or DEFAULT_MANIFEST_PATH
    candidate = relative_manifest if relative_manifest.is_absolute() else root / relative_manifest
    if not candidate.exists():
        return {}

    text = candidate.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        data = json.loads(text)
    else:
        data = parse_simple_yaml(text)

    if not isinstance(data, dict):
        raise CommandInjectionError("Manifest must parse to a mapping")
    validate_manifest(data)
    return data


def validate_manifest(manifest: dict[str, Any]) -> None:
    if not manifest:
        return

    version = manifest.get("version")
    if version != 1:
        raise CommandInjectionError("Manifest version must be exactly 1")

    managed_commands = manifest.get("managed_commands")
    # Phase 2: managed_skills is an additive, optional sibling block.
    # managed_commands may be absent/{} only when managed_skills is present —
    # a manifest declaring neither block is still structurally invalid.
    managed_skills = manifest.get("managed_skills")
    if managed_commands is None and managed_skills is None:
        raise CommandInjectionError("managed_commands must be a mapping")
    if managed_commands is not None and not isinstance(managed_commands, dict):
        raise CommandInjectionError("managed_commands must be a mapping")

    for command_name, config in (managed_commands or {}).items():
        if not isinstance(config, dict):
            raise CommandInjectionError(
                f"Command config for {command_name} must be a mapping"
            )

        state = str(config.get("state", "")).strip()
        if state not in {"unmanaged", "legacy-merge", "managed", "global-only"}:
            raise CommandInjectionError(
                f"Unsupported command state '{state}' for {command_name}"
            )

        # WS4: optional per-project parameter defaults. Structurally a
        # `parameters:` block, when present, must be a mapping of scalar defaults.
        # This is the fail-LOUD structural gate (caught by audit-project); the
        # fail-OPEN resolution layer is command_parameters_for_project().
        parameters = config.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            raise CommandInjectionError(
                f"parameters for {command_name} must be a mapping if present"
            )

        # WS1+WS4 governance: per-project defaults must NOT reopen the
        # over-escalation hole WS1 closes. The jPlan alias group may not pre-default
        # the review tier to critic-xhigh or the architecture tier to architect-master —
        # both are trigger-gated by the agent-team rubric, never a standing default.
        # Fail-loud here (structural governance rule); the live reader still fails open.
        if command_name in JPLAN_COMMAND_KEYS and isinstance(parameters, dict):
            if str(parameters.get("default_review_tier", "")).strip() == "critic-xhigh":
                raise CommandInjectionError(
                    f"{command_name} default_review_tier may not be 'critic-xhigh' — "
                    "critic-xhigh is trigger-gated by the agent-team rubric, not a default"
                )
            if str(parameters.get("default_arch_tier", "")).strip() == "architect-master":
                raise CommandInjectionError(
                    f"{command_name} default_arch_tier may not be 'architect-master' — "
                    "architect-master is gated (CHK-AM), never a default"
                )

        if state != "managed":
            continue

        if command_name not in MANAGED_COMMAND_ALLOWLIST:
            raise CommandInjectionError(
                f"{command_name} is outside the managed-command allowlist"
            )

        anchors = config.get("anchors")
        if not isinstance(anchors, dict) or not anchors:
            raise CommandInjectionError(
                f"Managed command {command_name} must declare a non-empty anchors mapping"
            )

        for anchor_name, anchor_config in anchors.items():
            if not isinstance(anchor_config, dict):
                raise CommandInjectionError(
                    f"Anchor config for {command_name}:{anchor_name} must be a mapping"
                )

            has_content = "content" in anchor_config
            has_snippet_path = "snippet_path" in anchor_config
            if has_content == has_snippet_path:
                raise CommandInjectionError(
                    f"Anchor {command_name}:{anchor_name} must specify exactly one of content or snippet_path"
                )

    # Phase 2: managed_skills structural gate (fail-loud here; the live
    # resolution layer — inspect_skill_for_project — stays fail-open per spec).
    if managed_skills is not None:
        if not isinstance(managed_skills, dict):
            raise CommandInjectionError("managed_skills must be a mapping")

        for skill_name, skill_config in managed_skills.items():
            if not isinstance(skill_config, dict):
                raise CommandInjectionError(
                    f"Skill config for {skill_name} must be a mapping"
                )

            skill_state = str(skill_config.get("state", "unmanaged")).strip()
            if skill_state not in {"unmanaged", "managed"}:
                raise CommandInjectionError(
                    f"Unsupported skill state '{skill_state}' for {skill_name}"
                )


def apply_substitutions(text: str, substitutions: dict[str, str] | None = None) -> str:
    rendered = text
    for old, new in (substitutions or {}).items():
        rendered = rendered.replace(old, new)
    return rendered


def _command_config(manifest: dict[str, Any], command_name: str) -> dict[str, Any]:
    validate_manifest(manifest)
    managed_commands = manifest.get("managed_commands") or {}
    if not isinstance(managed_commands, dict):
        raise CommandInjectionError("managed_commands must be a mapping")

    candidates = JPLAN_COMMAND_KEYS if command_name in JPLAN_COMMAND_KEYS else (command_name,)
    for candidate in candidates:
        config = managed_commands.get(candidate)
        if isinstance(config, dict):
            return config
    return {}


def command_mode_for_project(
    project_root: Path, command_name: str, manifest: dict[str, Any] | None = None
) -> str:
    manifest_data = manifest if manifest is not None else load_manifest(project_root)
    if not manifest_data:
        return "unmanaged"
    return str(_command_config(manifest_data, command_name).get("state", "unmanaged"))


def command_parameters_for_project(
    project_root: Path, command_name: str, manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Fail-open reader for per-project command parameter defaults (WS4).

    Returns the `parameters:` mapping declared under
    ``managed_commands.<command_name>`` in the project's manifest at
    ``DEFAULT_MANIFEST_PATH`` (this host's project command-injection file),
    or an empty dict when the manifest is absent, the parameters block is
    missing, or the manifest cannot be loaded/validated.

    This NEVER raises. A broken or missing manifest must not break a live lifecycle
    command — the command simply falls back to its global defaults. The fail-LOUD
    structural validation lives in ``validate_manifest`` (surfaced by
    ``audit-project``); this resolution layer is deliberately fail-OPEN per spec
    NFR3.
    """
    try:
        manifest_data = manifest if manifest is not None else load_manifest(project_root)
        if not manifest_data:
            return {}
        config = _command_config(manifest_data, command_name)
    except (CommandInjectionError, OSError, ValueError, TypeError):
        # Fail-open across the full failure surface, not just CommandInjectionError:
        #   OSError    — unreadable/permission-denied manifest, path-is-a-directory;
        #   ValueError — UnicodeDecodeError (binary file) and json.JSONDecodeError
        #                (a '{'-prefixed but malformed manifest), both subclasses;
        #   TypeError  — non-PathLike inputs.
        # A broken manifest must never abort a live /jPlan run — it falls back to
        # global defaults. The fail-LOUD surface for these is audit-project.
        return {}

    parameters = config.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    return dict(parameters)


def inspect_command_for_project(
    project_root: Path, command_name: str, manifest: dict[str, Any] | None = None
) -> InspectResult:
    project_root = Path(project_root)
    manifest_path = project_root / DEFAULT_MANIFEST_PATH
    manifest_exists = manifest_path.exists()

    if manifest is not None:
        manifest_data = manifest
    elif manifest_exists:
        try:
            manifest_data = load_manifest(project_root)
        except (CommandInjectionError, OSError, ValueError, TypeError):
            # Broken-but-present manifest: fail open for the live resolution path
            # (the staged /jPlan body calls this CLI). audit-project
            # manifest-validity remains the fail-loud surface for the same defect.
            return InspectResult(
                mode="missing-manifest",
                manifest_exists=True,
                manifest_path=manifest_path,
                configured_state=None,
                parameters={},
            )
    else:
        manifest_data = {}

    if not manifest_exists or not manifest_data:
        return InspectResult(
            mode="missing-manifest",
            manifest_exists=False,
            manifest_path=manifest_path,
            configured_state=None,
        )

    try:
        config = _command_config(manifest_data, command_name)
    except (CommandInjectionError, OSError, ValueError, TypeError):
        return InspectResult(
            mode="missing-manifest",
            manifest_exists=manifest_exists,
            manifest_path=manifest_path,
            configured_state=None,
            parameters={},
        )
    configured_state = str(config.get("state", "unmanaged")) if config else "unmanaged"
    parameters = command_parameters_for_project(
        project_root, command_name, manifest=manifest_data
    )
    anchors_config = config.get("anchors")
    anchors = (
        tuple(anchors_config.keys()) if isinstance(anchors_config, dict) else ()
    )
    return InspectResult(
        mode=configured_state,
        manifest_exists=True,
        manifest_path=manifest_path,
        configured_state=configured_state,
        parameters=parameters,
        anchors=anchors,
    )


def _skill_config(manifest: dict[str, Any], skill_name: str) -> dict[str, Any]:
    """Fail-loud low-level reader for ``managed_skills.<skill_name>``.

    Mirrors ``_command_config`` but for the additive skill-localization block.
    Does not itself apply overlay-path safety filtering — see
    ``inspect_skill_for_project`` for the fail-open resolution layer.
    """
    managed_skills = manifest.get("managed_skills") or {}
    if not isinstance(managed_skills, dict):
        raise CommandInjectionError("managed_skills must be a mapping")

    config = managed_skills.get(skill_name)
    if isinstance(config, dict):
        return config
    return {}


def skill_config_for_project(
    project_root: Path, skill_name: str, manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return the raw ``managed_skills.<skill_name>`` mapping for a project.

    Returns ``{}`` when the manifest is absent, has no ``managed_skills`` block,
    or has no entry for ``skill_name``. This is the low-level reader; it may
    raise ``CommandInjectionError`` on a malformed manifest — the fail-open
    resolution layer for live/optional localization is
    ``inspect_skill_for_project``.
    """
    manifest_data = manifest if manifest is not None else load_manifest(project_root)
    return _skill_config(manifest_data, skill_name)


def _safe_overlay_path(project_root: Path, raw_path: str) -> str | None:
    """Fail-open safety filter for ``managed_skills.<skill>.overlay_path``.

    Accepts only non-empty, repository-relative paths. Rejects absolute paths,
    home-relative paths (``~/...``), parent traversal (``../...``), and any
    path whose resolved location escapes the project root — including via a
    symlink. Never raises; returns ``None`` for anything unsafe or unresolvable.
    """
    if raw_path is None:
        return None
    candidate = raw_path.strip()
    if not candidate:
        return None
    if candidate.startswith("~"):
        return None

    candidate_path = Path(candidate)
    if candidate_path.is_absolute():
        return None
    if ".." in candidate_path.parts:
        return None

    try:
        project_root_resolved = Path(project_root).resolve()
        resolved = (project_root_resolved / candidate_path).resolve()
    except (OSError, ValueError, RuntimeError):
        return None

    if resolved != project_root_resolved and project_root_resolved not in resolved.parents:
        return None

    return candidate


def inspect_skill_for_project(
    project_root: Path, skill_name: str, manifest: dict[str, Any] | None = None
) -> InspectSkillResult:
    """Fail-open localization inspection for a managed skill (Phase 2).

    This NEVER raises. An absent, unreadable, malformed, or unknown-version
    manifest falls back to ``configured_state="unmanaged"`` with a warning —
    optional skill localization must never abort a live command. The fail-LOUD
    structural surface for ``managed_skills`` remains ``validate_manifest``
    (exercised via ``load_manifest`` / ``audit-project``).
    """
    project_root = Path(project_root)
    manifest_path = project_root / DEFAULT_MANIFEST_PATH
    manifest_exists = manifest_path.exists()

    if manifest is not None:
        manifest_data = manifest
    elif manifest_exists:
        try:
            manifest_data = load_manifest(project_root)
        except (CommandInjectionError, OSError, ValueError, TypeError):
            return InspectSkillResult(
                mode="unmanaged",
                manifest_exists=True,
                manifest_path=manifest_path,
                configured_state="unmanaged",
                overlay_path=None,
                warnings=(
                    "Project manifest is unreadable, malformed, or an unknown "
                    "version; skill localization falls back to unmanaged.",
                ),
            )
    else:
        manifest_data = {}

    if not manifest_exists or not manifest_data:
        return InspectSkillResult(
            mode="unmanaged",
            manifest_exists=manifest_exists,
            manifest_path=manifest_path,
            configured_state="unmanaged",
            overlay_path=None,
            warnings=(
                "No project command injection manifest found; skill "
                "localization is unmanaged.",
            ),
        )

    try:
        config = _skill_config(manifest_data, skill_name)
    except (CommandInjectionError, OSError, ValueError, TypeError):
        return InspectSkillResult(
            mode="unmanaged",
            manifest_exists=True,
            manifest_path=manifest_path,
            configured_state="unmanaged",
            overlay_path=None,
            warnings=(
                f"managed_skills entry for {skill_name} is malformed; skill "
                "localization falls back to unmanaged.",
            ),
        )

    warnings: list[str] = []
    # Phase 2 round-2 (R5): managed_skills.<skill> permits exactly
    # `state` and `overlay_path`. Any other declared field is noise the seam
    # never reads for anything — warn (fail-open) so the jOptimize audit
    # surface can flag it, rather than silently accepting arbitrary keys.
    if config:
        allowed_skill_fields = {"state", "overlay_path"}
        for field_name in config:
            if field_name not in allowed_skill_fields:
                warnings.append(f"unknown managed_skills field '{field_name}' ignored")

    configured_state = str(config.get("state", "unmanaged")).strip() if config else "unmanaged"
    if configured_state not in {"managed", "unmanaged"}:
        warnings.append(
            f"Unsupported skill state '{configured_state}' for {skill_name}; "
            "treating as unmanaged."
        )
        configured_state = "unmanaged"

    overlay_path: str | None = None
    raw_overlay_path = config.get("overlay_path") if config else None
    if configured_state == "managed" and raw_overlay_path:
        overlay_path = _safe_overlay_path(project_root, str(raw_overlay_path))
        if overlay_path is None:
            warnings.append(
                f"overlay_path '{raw_overlay_path}' for {skill_name} is unsafe "
                "or escapes the project root; ignoring."
            )

    return InspectSkillResult(
        mode=configured_state,
        manifest_exists=True,
        manifest_path=manifest_path,
        configured_state=configured_state,
        overlay_path=overlay_path,
        warnings=tuple(warnings),
    )


def anchor_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in ANCHOR_PATTERN.finditer(normalize_text(text)):
        anchor = match.group("name")
        counts[anchor] = counts.get(anchor, 0) + 1
    return counts


def _extract_heading_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    start = None
    heading_lower = heading.strip().lower()

    for idx, line in enumerate(lines):
        if line.strip().lower() == heading_lower:
            start = idx + 1
            break

    if start is None:
        return ""

    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        collected.append(line)
    return "\n".join(collected)


def _extract_markdown_links(text: str) -> list[str]:
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    cleaned: list[str] = []
    for link in links:
        candidate = link.strip()
        if candidate.startswith("http://") or candidate.startswith("https://"):
            continue
        cleaned.append(candidate)
    return cleaned


def _project_knowledge_file(project_root: Path) -> Path | None:
    for candidate in (_current_host().memory_path(project_root), project_root / "AGENTS.md"):
        if candidate.exists():
            return candidate
    return None


def _preferred_required_reading(command_name: str) -> list[str]:
    if command_name in JPLAN_COMMAND_KEYS:
        return [
            "docs/architecture/arch-principles.md",
            "docs/architecture/README.architecture.md",
            "docs/architecture/architecture-infra.md",
            "docs/architecture/architecture.uat-scenarios.md",
            "docs/dev-guide.structured-evidence.md",
        ]
    if command_name == "implement.md":
        return [
            "docs/architecture/architecture-infra.md",
            "docs/dev-guide.structured-evidence.md",
            "tests/e2e/helpers/evidence-collector.ts",
            "jswarm/agent-e2e.sh",
            "docs/architecture/architecture-logging.md",
        ]
    return []


def _inferred_required_reading(project_root: Path, command_name: str) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()

    def add_if_exists(relative_path: str) -> None:
        normalized = relative_path.strip()
        if not normalized or normalized in seen:
            return
        if (project_root / normalized).exists():
            results.append(normalized)
            seen.add(normalized)

    for candidate in _preferred_required_reading(command_name):
        add_if_exists(candidate)

    knowledge_file = _project_knowledge_file(project_root)
    if knowledge_file is not None:
        section = _extract_heading_section(
            knowledge_file.read_text(encoding="utf-8"),
            "## REQUIRED READING (every session)",
        )
        for link in _extract_markdown_links(section):
            add_if_exists(link)

    return results


def _inferred_advisories(project_root: Path, command_name: str) -> list[str]:
    advisories: list[str] = []

    if command_name in JPLAN_COMMAND_KEYS:
        if (project_root / "docs/architecture/arch-principles.md").exists():
            advisories.append(
                "Treat `docs/architecture/arch-principles.md` as load-bearing design guidance when writing the spec and plan."
            )
        if (project_root / "docs/plans").exists():
            advisories.append(
                "If feature-local docs exist in `docs/plans/<TICKET>*`, read those before writing the spec/plan."
            )

    if command_name == "implement.md":
        if (project_root / "jswarm/agent-e2e.sh").exists():
            advisories.append(
                "Use `jswarm/agent-e2e.sh` instead of raw `npx playwright` for local E2E execution."
            )
        if (project_root / "tests/e2e/helpers/evidence-collector.ts").exists():
            advisories.append(
                "Use structured E2E evidence (`EvidenceCollector`) when acceptance-style E2E coverage is in scope."
            )
        if (project_root / "docs/architecture/architecture-logging.md").exists():
            advisories.append(
                "Correlate runtime debugging with `docs/architecture/architecture-infra.md` and `docs/architecture/architecture-logging.md`."
            )

    if command_name == "test.md":
        if (project_root / "jswarm/agent-e2e.sh").exists():
            advisories.append(
                "Use `jswarm/agent-e2e.sh` instead of raw `npx playwright` for local E2E, regression, and scripted Live Show UAT execution."
            )
        if (project_root / ".jswarm/e2e-manifest.json").exists():
            advisories.append(
                "Read `.jswarm/e2e-manifest.json` for runner commands, base URLs, browser project, evidence directories, and project-specific UAT state policy."
            )
        if (project_root / "tests/e2e/helpers/evidence-collector.ts").exists():
            advisories.append(
                "Use structured E2E evidence (`EvidenceCollector`) when acceptance-style E2E coverage is in scope."
            )
        if (project_root / "tests/TEST_CATALOG.md").exists():
            advisories.append(
                "Search `tests/TEST_CATALOG.md` for relevant prior tests before creating new E2E or regression coverage."
            )

    return advisories


def _snippet_text_from_paths(paths: list[str]) -> str:
    if not paths:
        return ""
    return normalize_text("\n".join(f"- Read `{path}`" for path in paths))


def _advisory_text(advisories: list[str]) -> str:
    if not advisories:
        return ""
    return normalize_text("\n".join(f"- {item}" for item in advisories))


def canonical_command_source(common_root: Path, command_name: str) -> Path | None:
    """Resolve command content from skills first, then legacy command masters."""
    root = Path(common_root)
    stem = "jPlan" if command_name in JPLAN_COMMAND_KEYS else Path(command_name).stem
    candidates = (
        root
        / "docs/_CONTROLLED_CONFIG/dotclaude/user/skills"
        / stem
        / "SKILL.md",
        _current_host().project_dir(root) / "commands" / (
            LEGACY_JPLAN_COMMAND_KEY if command_name == JPLAN_COMMAND_KEY else command_name
        ),
        _current_host().project_dir(root) / "commands" / command_name,
        root / "docs/_CONTROLLED_CONFIG/dotclaude/user/commands" / command_name,
        root / "docs/_CONTROLLED_CONFIG/dotclaude/repo.common/commands" / command_name,
    )
    return next((candidate for candidate in candidates if candidate.exists()), None)


def bootstrap_project_injections(
    *,
    project_root: Path,
    common_root: Path,
    write: bool = False,
    force: bool = False,
    jira_key: str | None = None,
) -> BootstrapResult:
    project_root = Path(project_root)
    common_root = Path(common_root)
    manifest_path = project_root / DEFAULT_MANIFEST_PATH
    snippet_root = project_root / DEFAULT_SNIPPET_ROOT

    command_entries: list[str] = []
    snippet_texts: dict[str, str] = {}
    warnings: list[str] = []
    bootstrapped_commands: list[str] = []

    for command_name in sorted(PHASE_ONE_MANAGED_COMMANDS):
        canonical_file = canonical_command_source(common_root, command_name)
        if canonical_file is None:
            warnings.append(f"Skipping {command_name}: canonical file missing")
            continue

        anchors = anchor_counts(canonical_file.read_text(encoding="utf-8"))
        if not anchors:
            warnings.append(f"Skipping {command_name}: canonical file has no injection anchors")
            continue

        required_reading = _inferred_required_reading(project_root, command_name)
        advisories = _inferred_advisories(project_root, command_name)

        required_snippet_rel = f"{DEFAULT_SNIPPET_ROOT.as_posix()}/{command_name.replace('.md', '')}-required-reading.md"
        advisory_snippet_rel = f"{DEFAULT_SNIPPET_ROOT.as_posix()}/{command_name.replace('.md', '')}-advisories.md"

        command_lines = [f"  {command_name}:", "    state: managed", "    anchors:"]

        if command_name == JPLAN_COMMAND_KEY and jira_key:
            # parameters: sits at the same level as anchors:, so insert before anchors:
            command_lines = [
                f"  {command_name}:",
                "    state: managed",
                "    parameters:",
                f"      {JIRA_KEY_PARAMETER}: {jira_key.strip().upper()}",
                "    anchors:",
            ]

        if "project-required-reading" in anchors:
            if not required_reading:
                warnings.append(
                    f"{command_name}: no project-local required-reading docs inferred; scaffolded snippet will be empty"
                )
            command_lines.extend(
                [
                    '      "project-required-reading":',
                    f"        required: {'true' if bool(required_reading) else 'false'}",
                    f'        snippet_path: "{required_snippet_rel}"',
                ]
            )
            snippet_texts[required_snippet_rel] = _snippet_text_from_paths(required_reading)

        if "project-advisories" in anchors:
            if not advisories:
                warnings.append(
                    f"{command_name}: no project-local advisories inferred; scaffolded snippet will be empty"
                )
            command_lines.extend(
                [
                    '      "project-advisories":',
                    f"        required: {'true' if bool(advisories) else 'false'}",
                    f'        snippet_path: "{advisory_snippet_rel}"',
                ]
            )
            snippet_texts[advisory_snippet_rel] = _advisory_text(advisories)

        command_entries.append("\n".join(command_lines))
        bootstrapped_commands.append(command_name)

    command_entries.append("  close-ticket.md:\n    state: unmanaged")
    manifest_text = normalize_text(
        "\n".join(["version: 1", "managed_commands:"] + command_entries)
    )

    if write:
        write_targets: dict[Path, str] = {manifest_path: manifest_text}
        for relative_path, text in snippet_texts.items():
            write_targets[project_root / relative_path] = text

        collisions = [
            path for path in write_targets if path.exists() and not force
        ]
        if collisions:
            raise CommandInjectionError(
                "Bootstrap would overwrite existing files: "
                + ", ".join(str(path) for path in sorted(collisions))
                + ". Use force=True to overwrite."
            )

        temp_paths: list[tuple[Path, Path]] = []
        try:
            for destination, text in write_targets.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=destination.parent,
                    delete=False,
                    prefix=f".{destination.name}.",
                    suffix=".tmp",
                ) as handle:
                    handle.write(text)
                    temp_path = Path(handle.name)
                temp_paths.append((temp_path, destination))

            for temp_path, destination in temp_paths:
                temp_path.replace(destination)
        except Exception:
            for temp_path, _destination in temp_paths:
                if temp_path.exists():
                    temp_path.unlink()
            raise

    return BootstrapResult(
        manifest_path=manifest_path,
        manifest_text=manifest_text,
        snippet_texts=snippet_texts,
        commands=tuple(bootstrapped_commands),
        warnings=tuple(warnings),
    )


def audit_project_check(
    *,
    project_root: Path,
    common_root: Path,
    check_name: str,
) -> AuditResult:
    project_root = Path(project_root)
    common_root = Path(common_root)
    manifest_path = project_root / DEFAULT_MANIFEST_PATH

    if check_name == "manifest-validity":
        if not manifest_path.exists():
            return AuditResult(
                status="warn",
                messages=("WARN: no project command injection manifest present",),
            )
        try:
            manifest = load_manifest(project_root)
        except CommandInjectionError as exc:
            return AuditResult(status="fail", messages=(f"FAIL: {exc}",))
        managed = manifest.get("managed_commands", {})
        return AuditResult(
            status="ok",
            messages=(f"OK: manifest valid with {len(managed)} managed command entries",),
        )

    if check_name == "anchor-health":
        messages: list[str] = []
        status = "ok"
        for command_name in sorted(PHASE_ONE_MANAGED_COMMANDS):
            canonical_file = canonical_command_source(common_root, command_name)
            if canonical_file is None:
                status = "fail"
                messages.append(
                    f"FAIL: canonical command missing: "
                    f"{_current_host().project_dir(common_root) / 'commands' / command_name}"
                )
                continue

            counts = anchor_counts(canonical_file.read_text(encoding="utf-8"))
            if not counts:
                status = "fail"
                messages.append(f"FAIL: no injection anchors found in {command_name}")
                continue
            duplicates = [name for name, count in counts.items() if count > 1]
            if duplicates:
                status = "fail"
                messages.append(
                    f"FAIL: duplicate anchors in {command_name}: {', '.join(sorted(duplicates))}"
                )
            else:
                messages.append(
                    f"OK: {command_name} anchors healthy ({', '.join(sorted(counts))})"
                )

        return AuditResult(status=status, messages=tuple(messages))

    if manifest_path.exists():
        try:
            manifest = load_manifest(project_root)
        except CommandInjectionError as exc:
            return AuditResult(status="fail", messages=(f"FAIL: {exc}",))
    else:
        manifest = {}

    if check_name == "applicability-drift":
        if not manifest:
            return AuditResult(status="warn", messages=("WARN: no manifest present",))

        drift_messages: list[str] = []
        status = "ok"
        for command_name, config in (manifest.get("managed_commands") or {}).items():
            if not isinstance(config, dict):
                continue
            state = str(config.get("state", "unmanaged"))
            if state == "managed" and command_name not in MANAGED_COMMAND_ALLOWLIST:
                status = "fail"
                drift_messages.append(
                    f"FAIL: {command_name} is managed but outside the managed-command allowlist"
                )

        if not drift_messages:
            drift_messages.append("OK: managed command applicability matches Phase 1 scope")
        return AuditResult(status=status, messages=tuple(drift_messages))

    if check_name == "render-freshness":
        if not manifest:
            return AuditResult(status="warn", messages=("WARN: no manifest present",))

        freshness_messages: list[str] = []
        status = "ok"
        source_project_name = common_root.name
        target_project_name = project_root.name
        substitutions = project_command_substitutions(source_project_name, target_project_name)

        for command_name, config in (manifest.get("managed_commands") or {}).items():
            if not isinstance(config, dict) or str(config.get("state", "")) != "managed":
                continue

            canonical_file = canonical_command_source(common_root, command_name)
            physical_name = (
                LEGACY_JPLAN_COMMAND_KEY
                if command_name == JPLAN_COMMAND_KEY
                else command_name
            )
            target_file = _current_host().project_dir(project_root) / "commands" / physical_name
            if canonical_file is None or not target_file.exists():
                status = "fail"
                freshness_messages.append(
                    f"FAIL: managed command missing rendered target file: {target_file}"
                )
                continue

            result = render_command_file(
                source_file=canonical_file,
                project_root=project_root,
                target_file=target_file,
                command_name=command_name,
                substitutions=substitutions,
                manifest=manifest,
                write=False,
            )
            if result.action != "unchanged":
                status = "fail"
                freshness_messages.append(
                    f"FAIL: stale rendered output for {command_name} (action={result.action})"
                )
            else:
                freshness_messages.append(f"OK: rendered output fresh for {command_name}")

        if not freshness_messages:
            freshness_messages.append("WARN: no managed commands to audit for render freshness")
            status = "warn"

        return AuditResult(status=status, messages=tuple(freshness_messages))

    raise CommandInjectionError(f"Unknown audit check: {check_name}")


def _snippet_path(project_root: Path, raw_path: str) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute():
        raise CommandInjectionError("snippet_path must be repo-relative, not absolute")
    expected_root = DEFAULT_SNIPPET_ROOT.as_posix().rstrip("/") + "/"
    normalized = relative.as_posix()
    if not (normalized == DEFAULT_SNIPPET_ROOT.as_posix() or normalized.startswith(expected_root)):
        raise CommandInjectionError(
            f"snippet_path must live under {DEFAULT_SNIPPET_ROOT.as_posix()}/"
        )

    resolved = (project_root / relative).resolve()
    repo_root = project_root.resolve()
    if repo_root not in resolved.parents and resolved != repo_root:
        raise CommandInjectionError("snippet_path escapes the project root")
    return resolved


def _content_for_anchor(project_root: Path, anchor_config: dict[str, Any]) -> str:
    if "content" in anchor_config and "snippet_path" in anchor_config:
        raise CommandInjectionError("Anchor config cannot contain both content and snippet_path")
    if "content" in anchor_config:
        return normalize_text(str(anchor_config["content"]))
    if "snippet_path" in anchor_config:
        snippet = _snippet_path(project_root, str(anchor_config["snippet_path"]))
        if not snippet.exists():
            raise CommandInjectionError(f"snippet_path not found: {snippet}")
        return normalize_text(snippet.read_text(encoding="utf-8"))
    raise CommandInjectionError(
        "Anchor config must contain exactly one of content or snippet_path"
    )


def _validate_anchor_presence(canonical_text: str, anchors: dict[str, Any]) -> None:
    canonical_anchors = {
        match.group("name") for match in ANCHOR_PATTERN.finditer(normalize_text(canonical_text))
    }
    for anchor_name in anchors:
        if anchor_name not in canonical_anchors:
            raise CommandInjectionError(
                f"Unknown anchor '{anchor_name}' for canonical command"
            )


def render_command_text(
    *,
    canonical_text: str,
    command_name: str,
    project_root: Path,
    substitutions: dict[str, str] | None = None,
    existing_text: str | None = None,
    manifest: dict[str, Any] | None = None,
) -> RenderResult:
    manifest_data = manifest if manifest is not None else load_manifest(project_root)
    rendered = normalize_text(apply_substitutions(canonical_text, substitutions))
    config = _command_config(manifest_data, command_name)
    state = str(config.get("state", "unmanaged")) if config else "unmanaged"

    if state not in {"unmanaged", "legacy-merge", "managed", "global-only"}:
        raise CommandInjectionError(f"Unsupported command state '{state}' for {command_name}")

    applied_anchors: list[str] = []
    if state == "managed":
        anchors = config.get("anchors") or {}
        if not isinstance(anchors, dict):
            raise CommandInjectionError(f"anchors must be a mapping for {command_name}")
        _validate_anchor_presence(rendered, anchors)

        rendered_lines: list[str] = []
        for line in rendered.splitlines():
            match = ANCHOR_PATTERN.match(line)
            if not match:
                rendered_lines.append(line)
                continue

            anchor_name = match.group("name")
            anchor_config = anchors.get(anchor_name)
            if not isinstance(anchor_config, dict):
                rendered_lines.append(line)
                continue

            content = _content_for_anchor(Path(project_root), anchor_config)
            if bool(anchor_config.get("required")) and not content.strip():
                raise CommandInjectionError(
                    f"Anchor '{anchor_name}' is required but resolved to empty content"
                )
            if content:
                rendered_lines.extend(content.rstrip("\n").split("\n"))
            applied_anchors.append(anchor_name)

        rendered = normalize_text("\n".join(rendered_lines))

    existing_normalized = normalize_text(existing_text) if existing_text is not None else None
    action = "inserted" if existing_normalized is None else "updated"
    if existing_normalized == rendered:
        action = "unchanged"

    return RenderResult(
        action=action,
        mode=state,
        text=rendered,
        substitutions=dict(substitutions or {}),
        applied_anchors=tuple(applied_anchors),
        warnings=(),
    )


def render_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def render_command_file(
    *,
    source_file: Path,
    project_root: Path,
    target_file: Path | None = None,
    command_name: str | None = None,
    substitutions: dict[str, str] | None = None,
    manifest: dict[str, Any] | None = None,
    write: bool = False,
) -> RenderResult:
    source_path = Path(source_file)
    if not source_path.exists():
        raise CommandInjectionError(f"source_file not found: {source_path}")

    target_path = Path(target_file) if target_file is not None else None
    existing_text = None
    if target_path is not None and target_path.exists():
        existing_text = target_path.read_text(encoding="utf-8")

    result = render_command_text(
        canonical_text=source_path.read_text(encoding="utf-8"),
        command_name=command_name or source_path.name,
        project_root=Path(project_root),
        substitutions=substitutions,
        existing_text=existing_text,
        manifest=manifest,
    )

    if write:
        if target_path is None:
            raise CommandInjectionError("target_file is required when write=True")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(result.text, encoding="utf-8")

    return result


def _parse_substitutions(raw_pairs: list[str]) -> dict[str, str]:
    substitutions: dict[str, str] = {}
    for pair in raw_pairs:
        if "=" not in pair:
            raise CommandInjectionError(
                f"Invalid substitution '{pair}'. Expected OLD=NEW format"
            )
        old, new = pair.split("=", 1)
        substitutions[old] = new
    return substitutions


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render deterministic project command injections")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_file = subparsers.add_parser("render-file", help="Render a command file")
    render_file.add_argument("--source-file", required=True)
    render_file.add_argument("--project-root", required=True)
    render_file.add_argument("--target-file")
    render_file.add_argument("--command-name")
    render_file.add_argument("--source-project-name")
    render_file.add_argument("--target-project-name")
    render_file.add_argument(
        "--substitute",
        action="append",
        default=[],
        help="Substitution pair in OLD=NEW format; may be repeated",
    )
    render_file.add_argument("--write", action="store_true")

    inspect_command = subparsers.add_parser(
        "inspect-command", help="Inspect manifest state for a command"
    )
    inspect_command.add_argument("--project-root", required=True)
    inspect_command.add_argument("--command-name", required=True)

    inspect_skill = subparsers.add_parser(
        "inspect-skill", help="Inspect manifest localization state for a skill"
    )
    inspect_skill.add_argument("--project-root", required=True)
    inspect_skill.add_argument("--skill-name", required=True)

    audit_project = subparsers.add_parser(
        "audit-project", help="Audit project command injection health"
    )
    audit_project.add_argument("--project-root", required=True)
    audit_project.add_argument("--common-root", required=True)
    audit_project.add_argument("--check", required=True)

    bootstrap_project = subparsers.add_parser(
        "bootstrap-project",
        help="Analyze a project and scaffold managed command injection files",
    )
    bootstrap_project.add_argument("--project-root", required=True)
    bootstrap_project.add_argument("--common-root", required=True)
    bootstrap_project.add_argument("--write", action="store_true")
    bootstrap_project.add_argument("--force", action="store_true")
    bootstrap_project.add_argument("--jira-key", default=None)

    jira_key = subparsers.add_parser(
        "jira-key", help="print the resolved Jira project key or UNRESOLVED"
    )
    jira_key.add_argument("--project-root", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "render-file":
            substitutions = _parse_substitutions(args.substitute)
            if args.source_project_name and args.target_project_name:
                auto_substitutions = project_command_substitutions(
                    args.source_project_name, args.target_project_name
                )
                auto_substitutions.update(substitutions)
                substitutions = auto_substitutions
            result = render_command_file(
                source_file=Path(args.source_file),
                project_root=Path(args.project_root),
                target_file=Path(args.target_file) if args.target_file else None,
                command_name=args.command_name,
                substitutions=substitutions,
                write=args.write,
            )
            payload = {
                "action": result.action,
                "mode": result.mode,
                "text": result.text,
                "substitutions": result.substitutions,
                "applied_anchors": list(result.applied_anchors),
                "warnings": list(result.warnings),
                "render_hash": render_hash(result.text),
            }
            print(json.dumps(payload, indent=2))
            return 0
        if args.command == "inspect-command":
            inspect = inspect_command_for_project(
                Path(args.project_root),
                args.command_name,
            )
            print(
                json.dumps(
                    {
                        "mode": inspect.mode,
                        "manifest_exists": inspect.manifest_exists,
                        "manifest_path": str(inspect.manifest_path),
                        "configured_state": inspect.configured_state,
                        "parameters": inspect.parameters,
                        "anchors": list(inspect.anchors),
                    }
                )
            )
            return 0
        if args.command == "inspect-skill":
            skill_inspection = inspect_skill_for_project(
                Path(args.project_root),
                args.skill_name,
            )
            print(
                json.dumps(
                    {
                        "mode": skill_inspection.mode,
                        "manifest_exists": skill_inspection.manifest_exists,
                        "manifest_path": str(skill_inspection.manifest_path),
                        "configured_state": skill_inspection.configured_state,
                        "overlay_path": skill_inspection.overlay_path,
                        "warnings": list(skill_inspection.warnings),
                    }
                )
            )
            return 0
        if args.command == "audit-project":
            result = audit_project_check(
                project_root=Path(args.project_root),
                common_root=Path(args.common_root),
                check_name=args.check,
            )
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "messages": list(result.messages),
                    },
                    indent=2,
                )
            )
            return 0 if result.status != "fail" else 1
        if args.command == "jira-key":
            print(resolve_jira_project_key(Path(args.project_root)) or "UNRESOLVED")
            return 0
        if args.command == "bootstrap-project":
            result = bootstrap_project_injections(
                project_root=Path(args.project_root),
                common_root=Path(args.common_root),
                write=args.write,
                force=args.force,
                jira_key=args.jira_key,
            )
            print(
                json.dumps(
                    {
                        "manifest_path": str(result.manifest_path),
                        "manifest_text": result.manifest_text,
                        "snippet_texts": result.snippet_texts,
                        "commands": list(result.commands),
                        "warnings": list(result.warnings),
                    },
                    indent=2,
                )
            )
            return 0
    except CommandInjectionError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1

    parser.print_help()
    return 1


# Internal alias kept for callers/tests that address the CLI entry point as a
# "private" module function.
_main = main


if __name__ == "__main__":
    sys.exit(main())
