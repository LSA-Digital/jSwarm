"""Generic (Layer A) seam-spine schema loader and validator.

This module is the runtime counterpart of ``spine-record.schema.json``: the
JSON manifest is the single source of truth for per-record-type required
fields, D-1 ID grammar type tokens, and the schema-major migration policy.
This module loads that manifest once at import time and never hard-codes a
project's own vocabulary (carrier names, scenario IDs, subsystem names, ...).
Project-specific values only ever appear in ``tests/`` and ``fixtures/``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "ValidationError",
    "ValidationResult",
    "load_spine",
    "validate_spine",
    "SUPPORTED_SCHEMA_MAJOR_VERSION",
    "RECORD_TYPES",
]

_MANIFEST_PATH = Path(__file__).resolve().parent / "spine-record.schema.json"
_MANIFEST: dict[str, Any] = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))

_RECORD_TYPE_DEFS: dict[str, dict[str, Any]] = _MANIFEST["$defs"]
RECORD_TYPES: frozenset[str] = frozenset(_RECORD_TYPE_DEFS.keys())

SUPPORTED_SCHEMA_MAJOR_VERSION: str = str(_MANIFEST["migration_policy"]["supported_major_version"])

_ID_SHAPE_PATTERN = re.compile(_MANIFEST["id_grammar"]["shape_pattern"])
_FORBIDDEN_ID_SEGMENT_PATTERN = re.compile(_MANIFEST["id_grammar"]["forbidden_segment_pattern"])

_ID_TOKEN_BY_RECORD_TYPE: dict[str, str] = {
    record_type: definition["id_token"]
    for record_type, definition in _RECORD_TYPE_DEFS.items()
    if definition.get("id_token")
}


@dataclass(frozen=True)
class ValidationError:
    code: str
    message: str
    record_id: str | None = None
    record_type: str | None = None
    field: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    exit_code: int
    errors: list[ValidationError]
    warnings: list[ValidationError]
    records: list[dict[str, Any]]


def load_spine(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSONL seam-spine file into an ordered list of record dicts.

    Blank lines are ignored; every non-blank line is parsed as one JSON
    record and returned as a plain dict, in file order.
    """
    spine_path = Path(path)
    records: list[dict[str, Any]] = []
    with spine_path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
    return records


def _record_identifier(record: dict[str, Any]) -> str | None:
    return record.get("record_id") or record.get("spine_id")


_NUMERIC_ORDINAL_PATTERN = re.compile(r"^[0-9]+$")


def _validate_record_id_grammar(
    record_id: str, record_type: str, errors: list[ValidationError]
) -> None:
    if not _ID_SHAPE_PATTERN.match(record_id):
        errors.append(
            ValidationError(
                code="malformed_record_id",
                message=(
                    f"Malformed record ID {record_id!r} for record_type {record_type!r}: "
                    "IDs must follow the D-1 grammar "
                    "PIPE-<TYPE>.<PROJECT>.<SUBSYSTEM_OR_KIND>.<CAPABILITY_OR_NAME>[.<NNN>]."
                ),
                record_id=record_id,
                record_type=record_type,
            )
        )
        return

    segments = record_id.split(".")
    for segment in segments[1:]:
        if _FORBIDDEN_ID_SEGMENT_PATTERN.match(segment):
            errors.append(
                ValidationError(
                    code="malformed_record_id",
                    message=(
                        f"Malformed record ID {record_id!r} for record_type {record_type!r}: "
                        f"segment {segment!r} looks like a forbidden version suffix. D-1 IDs carry no "
                        "version suffix; lineage travels through legacy_ids[]/supersedes[]/superseded_by[]."
                    ),
                    record_id=record_id,
                    record_type=record_type,
                )
            )
            return

    expected_token = _ID_TOKEN_BY_RECORD_TYPE.get(record_type)
    if expected_token:
        actual_token = segments[0].split("-", 1)[1]
        if actual_token != expected_token:
            errors.append(
                ValidationError(
                    code="malformed_record_id",
                    message=(
                        f"Malformed record ID {record_id!r}: type token {actual_token!r} does not match "
                        f"the expected token {expected_token!r} for record_type {record_type!r}."
                    ),
                    record_id=record_id,
                    record_type=record_type,
                )
            )

    # S1.4 per-record-type grammar: some record types pin an exact segment count
    # and whether the trailing .NNN numeric ordinal is mandatory. Record types
    # not covered by S1.4 (e.g. path_step, join, trace_event, ...) only carry
    # the generic shape/forbidden-segment/id_token checks above.
    definition = _RECORD_TYPE_DEFS.get(record_type, {})
    expected_segment_count = definition.get("id_segment_count")
    if expected_segment_count is not None and len(segments) != expected_segment_count:
        requires_ordinal = bool(definition.get("id_requires_numeric_ordinal"))
        reason = (
            "requires the trailing numeric .NNN ordinal segment"
            if requires_ordinal
            else f"must use exactly {expected_segment_count} dot-separated segments"
        )
        errors.append(
            ValidationError(
                code="malformed_record_id",
                message=(
                    f"Malformed record ID {record_id!r}: record_type {record_type!r} {reason} per S1.4 "
                    f"(expected {expected_segment_count} segments, found {len(segments)})."
                ),
                record_id=record_id,
                record_type=record_type,
            )
        )
    elif (
        expected_segment_count is not None
        and definition.get("id_requires_numeric_ordinal")
        and not _NUMERIC_ORDINAL_PATTERN.match(segments[-1])
    ):
        errors.append(
            ValidationError(
                code="malformed_record_id",
                message=(
                    f"Malformed record ID {record_id!r}: record_type {record_type!r} requires a purely "
                    f"numeric trailing .NNN ordinal segment per S1.4; found {segments[-1]!r}."
                ),
                record_id=record_id,
                record_type=record_type,
            )
        )


def _validate_supersession_refs(
    record: dict[str, Any],
    record_id: str,
    record_type: str,
    known_ids: set[str],
    errors: list[ValidationError],
) -> None:
    for field_name in ("supersedes", "superseded_by"):
        for target_id in record.get(field_name) or []:
            if target_id not in known_ids:
                errors.append(
                    ValidationError(
                        code="dangling_supersession_ref",
                        message=(
                            f"Record {record_id!r} ({record_type}) field {field_name!r} references "
                            f"missing supersession target {target_id!r}; no record with that record_id "
                            "exists in the spine."
                        ),
                        record_id=record_id,
                        record_type=record_type,
                        field=field_name,
                    )
                )


def _validate_schema_version(
    record: dict[str, Any],
    record_id: str | None,
    record_type: str | None,
    errors: list[ValidationError],
) -> None:
    schema_version = record.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        errors.append(
            ValidationError(
                code="missing_schema_version",
                message=f"Record {record_id!r} ({record_type}) is missing a schema_version field.",
                record_id=record_id,
                record_type=record_type,
                field="schema_version",
            )
        )
        return

    major = schema_version.split(".", 1)[0]
    if major != SUPPORTED_SCHEMA_MAJOR_VERSION:
        errors.append(
            ValidationError(
                code="unsupported_schema_version",
                message=(
                    f"Unsupported schema major version {schema_version!r} in record {record_id!r} "
                    f"(record_type={record_type!r}); only major version "
                    f"{SUPPORTED_SCHEMA_MAJOR_VERSION!r} is supported by this validator. See "
                    "migration_policy in schema/spine-record.schema.json for how to migrate a new major."
                ),
                record_id=record_id,
                record_type=record_type,
                field="schema_version",
            )
        )


def _validate_required_fields(
    record: dict[str, Any],
    record_id: str | None,
    record_type: str,
    errors: list[ValidationError],
) -> None:
    definition = _RECORD_TYPE_DEFS[record_type]
    for required_field in definition["required"]:
        if required_field not in record or record[required_field] in (None, ""):
            errors.append(
                ValidationError(
                    code="missing_required_field",
                    message=(
                        f"Record {record_id!r} ({record_type}) is missing required field "
                        f"{required_field!r}."
                    ),
                    record_id=record_id,
                    record_type=record_type,
                    field=required_field,
                )
            )


def _type_name(value: Any) -> str:
    return type(value).__name__


def _validate_field_types_and_enums(
    record: dict[str, Any],
    record_id: str | None,
    record_type: str,
    errors: list[ValidationError],
) -> None:
    """BLOCKING-1: enforce manifest-declared field types/enums per record type.

    Driven entirely by the ``fields`` block in spine-record.schema.json
    (``{"type": "string"|"integer"|"boolean", "enum": [...]?}``); this
    function carries no per-project vocabulary of its own.
    """
    field_specs: dict[str, dict[str, Any]] = _RECORD_TYPE_DEFS[record_type].get("fields", {})
    for field_name, spec in field_specs.items():
        if field_name not in record or record[field_name] is None:
            continue
        value = record[field_name]
        expected_type = spec.get("type")

        if expected_type == "string":
            type_ok = isinstance(value, str)
        elif expected_type == "integer":
            type_ok = isinstance(value, int) and not isinstance(value, bool)
        elif expected_type == "boolean":
            type_ok = isinstance(value, bool)
        else:
            type_ok = True

        if not type_ok:
            errors.append(
                ValidationError(
                    code="invalid_field_type",
                    message=(
                        f"Record {record_id!r} ({record_type}) field {field_name!r} must be of type "
                        f"{expected_type!r}, found {_type_name(value)} ({value!r})."
                    ),
                    record_id=record_id,
                    record_type=record_type,
                    field=field_name,
                )
            )
            continue

        enum_values = spec.get("enum")
        if enum_values is not None and value not in enum_values:
            errors.append(
                ValidationError(
                    code="invalid_enum",
                    message=(
                        f"Record {record_id!r} ({record_type}) field {field_name!r} has value {value!r}, "
                        f"which is not one of the allowed values {enum_values!r}."
                    ),
                    record_id=record_id,
                    record_type=record_type,
                    field=field_name,
                )
            )


def _validate_graph_refs(
    record: dict[str, Any],
    record_id: str | None,
    record_type: str,
    known_ids: set[str],
    errors: list[ValidationError],
) -> None:
    """BLOCKING-1: cross-record graph references must resolve within the spine.

    Driven entirely by the ``ref_fields`` block in spine-record.schema.json
    (``field_name -> "single"|"list"``); reuses the same ``known_ids`` set the
    existing supersession check relies on, so a reference is valid as soon as
    *some* record in the spine carries that record_id (target-type agnostic,
    matching the polymorphic nature of fields like edge.from_record_id/
    to_record_id and join.from_id/to_id).
    """
    ref_specs: dict[str, str] = _RECORD_TYPE_DEFS[record_type].get("ref_fields", {})
    for field_name, cardinality in ref_specs.items():
        if field_name not in record or record[field_name] in (None, "", []):
            continue
        value = record[field_name]

        if cardinality == "list":
            if not isinstance(value, list):
                errors.append(
                    ValidationError(
                        code="invalid_field_type",
                        message=(
                            f"Record {record_id!r} ({record_type}) field {field_name!r} must be a list of "
                            f"record IDs, found {_type_name(value)} ({value!r})."
                        ),
                        record_id=record_id,
                        record_type=record_type,
                        field=field_name,
                    )
                )
                continue
            for target_id in value:
                if not isinstance(target_id, str):
                    errors.append(
                        ValidationError(
                            code="invalid_field_type",
                            message=(
                                f"Record {record_id!r} ({record_type}) field {field_name!r} contains a "
                                f"non-string entry {target_id!r}."
                            ),
                            record_id=record_id,
                            record_type=record_type,
                            field=field_name,
                        )
                    )
                    continue
                if target_id not in known_ids:
                    errors.append(
                        ValidationError(
                            code="dangling_graph_ref",
                            message=(
                                f"Record {record_id!r} ({record_type}) field {field_name!r} references "
                                f"missing record {target_id!r}; no record with that record_id exists in "
                                "the spine."
                            ),
                            record_id=record_id,
                            record_type=record_type,
                            field=field_name,
                        )
                    )
        elif cardinality == "single":
            if not isinstance(value, str):
                errors.append(
                    ValidationError(
                        code="invalid_field_type",
                        message=(
                            f"Record {record_id!r} ({record_type}) field {field_name!r} must be a single "
                            f"record ID string, found {_type_name(value)} ({value!r})."
                        ),
                        record_id=record_id,
                        record_type=record_type,
                        field=field_name,
                    )
                )
                continue
            if value not in known_ids:
                errors.append(
                    ValidationError(
                        code="dangling_graph_ref",
                        message=(
                            f"Record {record_id!r} ({record_type}) field {field_name!r} references missing "
                            f"record {value!r}; no record with that record_id exists in the spine."
                        ),
                        record_id=record_id,
                        record_type=record_type,
                        field=field_name,
                    )
                )


def validate_spine(records_or_path: str | Path | Sequence[dict[str, Any]]) -> ValidationResult:
    """Validate a loaded seam-spine (or a path to one) against the schema.

    Supports only ``SUPPORTED_SCHEMA_MAJOR_VERSION`` for the ``schema_version``
    major component; any other major is rejected with ``exit_code == 7``
    (``code == "unsupported_schema_version"``). Other schema/record failures
    (unknown record_type, missing required field, malformed D-1 record_id,
    dangling supersession reference) use ``exit_code == 2``. A clean spine
    returns ``exit_code == 0`` with empty ``errors``.
    """
    if isinstance(records_or_path, (str, Path)):
        records = load_spine(records_or_path)
    else:
        records = list(records_or_path)

    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    known_ids: set[str] = {
        record["record_id"]
        for record in records
        if record.get("record_type") != "spine_metadata" and record.get("record_id")
    }

    for record in records:
        record_type = record.get("record_type")
        record_id = _record_identifier(record)

        if record_type is None:
            errors.append(
                ValidationError(
                    code="missing_record_type",
                    message=f"Record {record_id!r} is missing a record_type field.",
                    record_id=record_id,
                )
            )
            continue

        if record_type not in RECORD_TYPES:
            errors.append(
                ValidationError(
                    code="unknown_record_type",
                    message=f"Record {record_id!r} declares unknown record_type {record_type!r}.",
                    record_id=record_id,
                    record_type=record_type,
                )
            )
            continue

        _validate_schema_version(record, record_id, record_type, errors)
        _validate_required_fields(record, record_id, record_type, errors)
        _validate_field_types_and_enums(record, record_id, record_type, errors)

        if record_type != "spine_metadata" and record_id:
            _validate_record_id_grammar(record_id, record_type, errors)
            _validate_supersession_refs(record, record_id, record_type, known_ids, errors)
            _validate_graph_refs(record, record_id, record_type, known_ids, errors)

    if any(error.code == "unsupported_schema_version" for error in errors):
        exit_code = 7
    elif errors:
        exit_code = 2
    else:
        exit_code = 0

    return ValidationResult(
        ok=not errors,
        exit_code=exit_code,
        errors=errors,
        warnings=warnings,
        records=records,
    )
