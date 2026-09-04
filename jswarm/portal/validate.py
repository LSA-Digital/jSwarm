"""Validate fix-decision JSON documents against the COM-389 schemas.

Usage:
    .venv/bin/python -m jswarm.portal.validate PATH [--schema {envelope,defect,fix,manifest,receipt}]

The schema is auto-detected from the document's ``schema``/$id constant when
--schema is omitted. Exit codes: 0 valid, 1 invalid, 2 usage error.
"""

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from jswarm.portal import cross_validate

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "fix-decisions"

SCHEMA_FILES = {
    "envelope": "fix-decisions.contract-envelope.schema.json",
    "defect": "fix-decisions.defect-contract.schema.json",
    "fix": "fix-decisions.fix-contract.schema.json",
    "manifest": "fix-decisions.publication-manifest.schema.json",
    "receipt": "fix-decisions.decision-receipt.schema.json",
    "qa": "fix-decisions.qa-thread.schema.json",
}

# Document "schema" const -> schema key, for auto-detection.
SCHEMA_CONSTS = {
    "jswarm.fix-decisions.contract/v1": "envelope",
    "jswarm.fix-decisions.defect-contract/v1": "defect",
    "jswarm.fix-decisions.fix-contract/v1": "fix",
    "jswarm.fix-decisions.publication-manifest/v1": "manifest",
    "jswarm.fix-decisions.decision-receipt/v1": "receipt",
    "jswarm.fix-decisions.qa-thread/v1": "qa",
}


def _registry():
    resources = {}
    for key, name in SCHEMA_FILES.items():
        doc = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
        resources[doc["$id"]] = Resource.from_contents(doc, default_specification=DRAFT202012)
        resources[key] = doc
    return Registry().with_resources(resources.items()), resources


REGISTRY, SCHEMAS = _registry()


def detect_schema_key(document):
    const = document.get("schema")
    if const in SCHEMA_CONSTS:
        return SCHEMA_CONSTS[const]
    raise ValueError(f"cannot auto-detect schema from 'schema' value: {const!r}")


def validate_document(document, schema_key=None):
    """Validate an in-memory document dict; returns (valid, messages).

    Shared by the path-based :func:`validate` and the Phase 3 service modules
    that already hold parsed documents.
    """
    if schema_key is None:
        schema_key = detect_schema_key(document)
    schema = SCHEMAS[schema_key]
    validator = Draft202012Validator(schema, registry=REGISTRY)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    messages = []
    for err in errors:
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        messages.append(f"{location}: {err.message}")
    if schema_key in ("envelope", "defect", "fix"):
        messages.extend(cross_validate.validate_pm_anchor_refs(document.get("body", document)))
    return (not messages), messages


def validate(path, schema_key=None):
    target = Path(path)
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        return False, [f"invalid JSON: {exc}"]
    if schema_key is None:
        schema_key = detect_schema_key(document)
    schema = SCHEMAS[schema_key]
    validator = Draft202012Validator(schema, registry=REGISTRY)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    messages = []
    for err in errors:
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        messages.append(f"{location}: {err.message}")
    # Cross-field referential check JSON Schema cannot express: pm_anchor_map
    # technical_sections must resolve to real section ids (applies to contracts).
    if schema_key in ("envelope", "defect", "fix"):
        messages.extend(cross_validate.validate_pm_anchor_refs(document.get("body", document)))
    return (not messages), messages


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="jswarm.portal.validate",
        description="Validate a fix-decision JSON document.",
    )
    parser.add_argument("path", help="path to the JSON document")
    parser.add_argument(
        "--schema",
        choices=sorted(SCHEMA_FILES),
        default=None,
        help="schema to validate against (default: auto-detect from the document)",
    )
    args = parser.parse_args(argv)

    target = Path(args.path)
    if not target.is_file():
        print(f"error: no such file: {target}", file=sys.stderr)
        return 2
    try:
        valid, messages = validate(target, args.schema)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"invalid: {target}: not valid JSON: {exc}")
        return 1

    if valid:
        print(f"valid: {target}")
        return 0
    for message in messages:
        print(f"invalid: {message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
