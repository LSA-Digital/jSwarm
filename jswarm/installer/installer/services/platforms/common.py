"""Shared pure formatting helpers for service-template platform renderers."""

from __future__ import annotations

import json
import shlex
import xml.sax.saxutils as xml_escape
from typing import Iterable, Mapping


def render_placeholders(value: str, replacements: Mapping[str, str]) -> str:
    result = value
    for token, replacement in replacements.items():
        result = result.replace("{{" + token + "}}", replacement)
    return result


def argv(descriptor: object, replacements: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(render_placeholders(part, replacements) for part in descriptor.command.argv)


def env_lines(env: Mapping[str, str], replacements: Mapping[str, str], *, separator: str = "=") -> list[str]:
    return [f"{key}{separator}{render_placeholders(value, replacements)}" for key, value in sorted(env.items())]


def shell_join(parts: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) if "{{" not in part else part for part in parts)


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def xml(value: str) -> str:
    return xml_escape.escape(value, {'"': "&quot;", "'": "&apos;"})


def plist_string(value: str) -> str:
    return xml(value)


def json_list(parts: Iterable[str]) -> str:
    return json.dumps(list(parts), ensure_ascii=False)
