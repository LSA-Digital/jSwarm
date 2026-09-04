"""Strict parsing boundary for resolver documents."""

from __future__ import annotations

from typing import TypeAlias, TypeGuard

JsonObject: TypeAlias = dict[str, object]


def is_object(value: object) -> TypeGuard[JsonObject]:
    return type(value) is dict and all(type(key) is str for key in value)


def is_strings(value: object) -> TypeGuard[list[str]]:
    return type(value) is list and all(type(item) is str for item in value)


class _Json:
    def __init__(self, text: str) -> None:
        self.text, self.index = text, 0

    def parse(self) -> object:
        value = self.value()
        self.space()
        if self.index != len(self.text): raise ValueError("trailing JSON")
        return value

    def space(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace(): self.index += 1

    def value(self) -> object:
        self.space(); char = self.text[self.index]
        if char == "{": return self.mapping()
        if char == "[": return self.items()
        if char == '"': return self.string()
        for token, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(token, self.index): self.index += len(token); return value
        start = self.index
        while self.index < len(self.text) and self.text[self.index] not in ",]} \t\r\n": self.index += 1
        number = self.text[start:self.index]
        return float(number) if any(mark in number for mark in ".eE") else int(number)

    def string(self) -> str:
        self.index += 1; output = ""
        while self.index < len(self.text) and self.text[self.index] != '"':
            output += self.text[self.index]; self.index += 1
        if self.index == len(self.text): raise ValueError("unterminated JSON string")
        self.index += 1; return output

    def mapping(self) -> JsonObject:
        self.index += 1; result: JsonObject = {}; self.space()
        while self.index < len(self.text) and self.text[self.index] != "}":
            key = self.string(); self.space()
            if self.text[self.index] != ":": raise ValueError("JSON object separator")
            self.index += 1; result[key] = self.value(); self.space()
            if self.text[self.index] == ",": self.index += 1; self.space()
            elif self.text[self.index] != "}": raise ValueError("JSON object delimiter")
        self.index += 1; return result

    def items(self) -> list[object]:
        self.index += 1; result: list[object] = []; self.space()
        while self.index < len(self.text) and self.text[self.index] != "]":
            result.append(self.value()); self.space()
            if self.text[self.index] == ",": self.index += 1; self.space()
            elif self.text[self.index] != "]": raise ValueError("JSON array delimiter")
        self.index += 1; return result


def parse_document(text: str) -> JsonObject:
    value = _Json(text).parse() if text.lstrip().startswith("{") else _yaml(text)
    if not is_object(value): raise ValueError("document must be an object")
    return value


def _yaml(text: str) -> JsonObject:
    lines = [(len(line) - len(line.lstrip()), line.strip()) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines: raise ValueError("empty document")
    value, index = _map(lines, 0, lines[0][0])
    if index != len(lines): raise ValueError("invalid YAML")
    return value


def _map(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[JsonObject, int]:
    result: JsonObject = {}
    while index < len(lines) and lines[index][0] == indent:
        _, line = lines[index]; key, marker, raw = line.partition(":")
        if not marker: raise ValueError("invalid YAML line")
        index += 1
        if raw.strip(): result[key] = _atom(raw); continue
        if index < len(lines) and lines[index][0] > indent: result[key], index = _map(lines, index, lines[index][0]); continue
        result[key] = {}
    return result, index


def _atom(raw: str) -> object:
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"): return [_atom(item) for item in value[1:-1].split(",") if item.strip()]
    if value.startswith("{") and value.endswith("}"):
        if not value[1:-1].strip(): return {}
        return {key.strip(): _atom(item_value) for key, _, item_value in (item.partition(":") for item in value[1:-1].split(","))}
    if len(value) > 1 and value[0] in "\"'" and value[-1] == value[0]: return value[1:-1]
    if value in {"true", "false"}: return value == "true"
    if value in {"null", "~"}: return None
    return int(value) if value.isdigit() else value
