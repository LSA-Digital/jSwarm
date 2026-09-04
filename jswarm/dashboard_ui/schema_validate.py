"""COM-119 P2 — stdlib-only JSON Schema (Draft-07 subset) validator.

Extracted-and-neutralized from ``scripts/feature-dashboard-system/render-feature-dashboard.py``
(``_validate_json_schema`` and its helpers, lines 1578–1789 in that module).

**Why this is hand-rolled and not ``jsonschema``:** the common ``.venv`` intentionally
has no ``jsonschema`` dependency today (feature-dashboard-system code comment +
COM-119 retro Action Item #1). Adding it would expand the supply-chain surface
without buying us anything for the Draft-07 subset that the dashboard data
objects actually use. If a future ticket proves we need ``$ref``-heavy schemas
or full Draft-2020-12 semantics, swap implementations behind this module's
``validate(data, schema)`` function — callers don't need to change.

Supported Draft-07 features (sufficient for COM-119/120/121/COM-77 dashboards):
  - type (single value or list)
  - const, enum
  - minLength, pattern, format=date (YYYY-MM-DD)
  - properties, required, additionalProperties (false | dict)
  - items (single schema), minItems
  - oneOf, allOf with if/then
  - $ref to ``#/definitions/...``

Returns a list of human-readable error strings rooted at ``path``; empty list
means valid. Public API:

  - :func:`validate_dashboard_data(data, schema)` — convenience that raises
    :exc:`SchemaValidationError` aggregating all errors into one message.
  - :func:`validate(data, schema, path)` — returns ``list[str]`` of errors.

Acceptance criteria coverage:
  - AC-119.4 — schema-validate the data object before render.
"""
from __future__ import annotations

import re
from typing import cast

JsonObject = dict[str, object]


class SchemaValidationError(ValueError):
    """Raised when one or more schema errors are detected.

    The full error list is preserved on :attr:`errors` for programmatic use;
    ``str(exc)`` shows a newline-joined human-readable summary.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("schema validation failed:\n  - " + "\n  - ".join(self.errors))


def validate(data: object, schema: JsonObject, path: str = "data") -> list[str]:
    """Validate ``data`` against ``schema``; return list of error strings."""
    return _validate_json_schema(data, schema, schema, path)


def validate_dashboard_data(data: object, schema: JsonObject) -> None:
    """Validate dashboard data; raise :exc:`SchemaValidationError` on any error."""
    errors = validate(data, schema, path="data")
    if errors:
        raise SchemaValidationError(errors)


def _validate_json_schema(
    value: object, schema: JsonObject, root_schema: JsonObject, path: str
) -> list[str]:
    schema_value: object = value
    errors: list[str] = []
    ref = schema.get("$ref")
    if isinstance(ref, str):
        target_schema = _resolve_schema_ref(ref, root_schema)
        if target_schema is None:
            return [f"{path}: unsupported schema reference {ref}"]
        return _validate_json_schema(value, target_schema, root_schema, path)

    allowed_types = _schema_types(schema.get("type"))
    if allowed_types and not _matches_json_type(value, allowed_types):
        expected = ", ".join(sorted(allowed_types))
        errors.append(f"{path}: expected type {expected}")
        return errors

    if "const" in schema and value != schema.get("const"):
        errors.append(f"{path}: expected constant {schema.get('const')!r}")
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        errors.append(f"{path}: {value!r} is not one of {enum_values!r}")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: must contain at least {min_length} character(s)")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: must match pattern {pattern}")
        if (
            schema.get("format") == "date"
            and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
        ):
            errors.append(f"{path}: must use YYYY-MM-DD date format")

    if isinstance(value, list):
        list_value = cast(list[object], value)
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(list_value) < min_items:
            errors.append(f"{path}: must contain at least {min_items} item(s)")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            item_schema_object = cast(JsonObject, item_schema)
            for item_index, item in enumerate(list_value):
                errors.extend(
                    _validate_json_schema(
                        item, item_schema_object, root_schema, f"{path}/{item_index}"
                    )
                )

    if isinstance(value, dict):
        object_value = cast(JsonObject, value)
        properties = _object(schema.get("properties"))
        required = _string_set(schema.get("required"))
        for field_name in sorted(required - set(object_value)):
            errors.append(f"{path}/{field_name}: required property is missing")
        additional = schema.get("additionalProperties")
        extra_fields = set(object_value) - set(properties)
        if additional is False:
            for field_name in sorted(extra_fields):
                errors.append(f"{path}/{field_name}: additional property is not allowed")
        elif isinstance(additional, dict):
            additional_schema = cast(JsonObject, additional)
            for field_name in sorted(extra_fields):
                errors.extend(
                    _validate_json_schema(
                        object_value[field_name],
                        additional_schema,
                        root_schema,
                        f"{path}/{field_name}",
                    )
                )
        for field_name, property_schema in properties.items():
            if field_name in object_value and isinstance(property_schema, dict):
                errors.extend(
                    _validate_json_schema(
                        object_value[field_name],
                        cast(JsonObject, property_schema),
                        root_schema,
                        f"{path}/{field_name}",
                    )
                )

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        match_count = 0
        for option in cast(list[object], one_of):
            if isinstance(option, dict):
                option_schema = cast(JsonObject, option)
                if not _validate_json_schema(schema_value, option_schema, root_schema, path):
                    match_count += 1
        if match_count != 1:
            errors.append(f"{path}: must match exactly one allowed schema option")

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in cast(list[object], all_of):
            if not isinstance(item, dict):
                continue
            subschema = cast(JsonObject, item)
            condition = subschema.get("if")
            if isinstance(condition, dict):
                condition_schema = cast(JsonObject, condition)
                if _validate_json_schema(schema_value, condition_schema, root_schema, path):
                    continue
                then_schema = subschema.get("then")
                if isinstance(then_schema, dict):
                    then_schema_object = cast(JsonObject, then_schema)
                    errors.extend(
                        _validate_json_schema(
                            schema_value, then_schema_object, root_schema, path
                        )
                    )
            else:
                errors.extend(
                    _validate_json_schema(schema_value, subschema, root_schema, path)
                )

    return errors


def _resolve_schema_ref(ref: str, root_schema: JsonObject) -> JsonObject | None:
    if not ref.startswith("#/"):
        return None
    value: object = root_schema
    for token in ref[2:].split("/"):
        value = _mapping_get(value, token)
        if value is None:
            return None
    return cast(JsonObject, value) if isinstance(value, dict) else None


def _mapping_get(value: object, key: str) -> object | None:
    if not isinstance(value, dict):
        return None
    mapping = cast(dict[str, object], value)
    return mapping.get(key)


def _schema_types(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in cast(list[object], value) if isinstance(item, str)}
    return set()


def _matches_json_type(value: object, allowed_types: set[str]) -> bool:
    return any(
        (
            allowed_type == "object" and isinstance(value, dict)
            or allowed_type == "array" and isinstance(value, list)
            or allowed_type == "string" and isinstance(value, str)
            or allowed_type == "null" and value is None
            or allowed_type == "boolean" and isinstance(value, bool)
            or allowed_type == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            or allowed_type == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        for allowed_type in allowed_types
    )


def _object(value: object) -> JsonObject:
    return cast(JsonObject, value) if isinstance(value, dict) else {}


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in cast(list[object], value) if isinstance(item, str)}
