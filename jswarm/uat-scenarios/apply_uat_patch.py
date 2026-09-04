#!/usr/bin/env python3
"""Apply a structured JSON Patch sidecar to canonical UAT scenario JSON."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, cast

# When invoked as ``.venv/bin/python jswarm/uat-scenarios/apply_uat_patch.py``,
# sys.path contains only this script directory. Bootstrap the repository root so
# shared dashboard helpers import the same way they do under module execution.
if __package__ in (None, ""):
    _REPO_ROOT = Path(__file__).resolve().parent.parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from jswarm.dashboard_ui import canonical_json, schema_validate  # noqa: E402
from jswarm.dashboard_ui.schema_validate import SchemaValidationError  # noqa: E402

JsonObject = dict[str, object]
JsonArray = list[object]
PatchOperation = dict[str, object]
_MISSING = object()


class PatchConflictError(ValueError):
    """Raised when a patch is stale or an RFC-6902 test operation fails."""


class PatchApplicationError(ValueError):
    """Raised when a sidecar or JSON Patch operation is malformed."""


def load_json_object(path: Path) -> JsonObject:
    """Load a JSON object from ``path`` with explicit operator-facing errors."""
    try:
        with path.open(encoding="utf-8") as input_file:
            data = json.load(input_file)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"JSON input does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return cast(JsonObject, data)


def validate_scenarios(data: JsonObject, schema: JsonObject) -> None:
    """Validate scenario data and raise one aggregated error on any defect."""
    errors = schema_validate.validate(data, schema, path="data")
    if errors:
        raise SchemaValidationError(errors)


def apply_sidecar(current: JsonObject, sidecar: JsonObject) -> JsonObject:
    """Return patched scenario data after applying all sidecar operations in memory."""
    base_sha256 = sidecar.get("base_sha256")
    if base_sha256 is not None:
        if not isinstance(base_sha256, str):
            raise PatchApplicationError("sidecar base_sha256 must be a string when present")
        current_sha256 = canonical_json.hash_canonical(current)
        if base_sha256 != current_sha256:
            raise PatchConflictError(
                "conflict: sidecar base_sha256 does not match current canonical hash "
                f"(expected {base_sha256}, current {current_sha256})"
            )

    operations_value = sidecar.get("operations")
    if not isinstance(operations_value, list):
        raise PatchApplicationError("sidecar operations must be a list")
    operations = [
        _require_operation(operation, index)
        for index, operation in enumerate(operations_value)
    ]

    patched = cast(JsonObject, copy.deepcopy(current))
    for index, operation in enumerate(operations):
        _apply_operation(patched, operation, index)
    return patched


def _require_operation(value: object, index: int) -> PatchOperation:
    if not isinstance(value, dict):
        raise PatchApplicationError(f"operations[{index}] must be an object")
    operation = cast(PatchOperation, value)
    op = operation.get("op")
    if op not in {"add", "replace", "remove", "test"}:
        raise PatchApplicationError(
            f"operations[{index}].op must be one of add, replace, remove, test; got {op!r}"
        )
    path = operation.get("path")
    if not isinstance(path, str):
        raise PatchApplicationError(f"operations[{index}].path must be a JSON Pointer string")
    if op in {"add", "replace", "test"} and "value" not in operation:
        raise PatchApplicationError(f"operations[{index}] with op {op!r} must include value")
    return operation


def _apply_operation(document: JsonObject, operation: PatchOperation, index: int) -> None:
    op = cast(str, operation["op"])
    path = cast(str, operation["path"])
    tokens = parse_json_pointer(path)
    value = operation.get("value", _MISSING)

    if op == "add":
        _set_value(document, tokens, value, replace=False, operation_index=index)
    elif op == "replace":
        _set_value(document, tokens, value, replace=True, operation_index=index)
    elif op == "remove":
        _remove_value(document, tokens, operation_index=index)
    elif op == "test":
        current_value = _get_value(document, tokens, operation_index=index)
        if current_value != value:
            raise PatchConflictError(
                f"conflict: operations[{index}] test failed at {path}; "
                f"expected {value!r}, current {current_value!r}"
            )
    else:  # pragma: no cover - _require_operation validates this first.
        raise PatchApplicationError(f"operations[{index}].op is unsupported: {op}")


def parse_json_pointer(pointer: str) -> list[str]:
    """Parse an RFC-6901 JSON Pointer into unescaped tokens."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchApplicationError(f"invalid JSON Pointer {pointer!r}: must be empty or start with '/'")
    return [_unescape_pointer_token(token) for token in pointer.split("/")[1:]]


def _unescape_pointer_token(token: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            result.append(char)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise PatchApplicationError(f"invalid JSON Pointer escape in token {token!r}")
        result.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(result)


def _get_value(document: object, tokens: list[str], *, operation_index: int) -> object:
    current = document
    for token in tokens:
        current = _resolve_child(current, token, operation_index=operation_index)
    return current


def _resolve_parent(document: object, tokens: list[str], *, operation_index: int) -> tuple[object, str | None]:
    if not tokens:
        return document, None
    return _get_value(document, tokens[:-1], operation_index=operation_index), tokens[-1]


def _resolve_child(container: object, token: str, *, operation_index: int) -> object:
    if isinstance(container, dict):
        if token not in container:
            raise PatchApplicationError(f"operations[{operation_index}] path target does not exist: {token!r}")
        return cast(dict[str, object], container)[token]
    if isinstance(container, list):
        list_value = cast(JsonArray, container)
        list_index = _parse_array_index(token, len(list_value), allow_append=False, operation_index=operation_index)
        return list_value[list_index]
    raise PatchApplicationError(
        f"operations[{operation_index}] cannot traverse into non-container value at token {token!r}"
    )


def _set_value(
    document: JsonObject,
    tokens: list[str],
    value: object,
    *,
    replace: bool,
    operation_index: int,
) -> None:
    if not tokens:
        if not isinstance(value, dict):
            raise PatchApplicationError(
                f"operations[{operation_index}] cannot replace document root with non-object UAT scenario data"
            )
        document.clear()
        document.update(cast(JsonObject, copy.deepcopy(value)))
        return

    parent, token = _resolve_parent(document, tokens, operation_index=operation_index)
    assert token is not None
    copied_value = copy.deepcopy(value)
    if isinstance(parent, dict):
        object_parent = cast(dict[str, object], parent)
        if replace and token not in object_parent:
            raise PatchApplicationError(f"operations[{operation_index}] replace target does not exist: {token!r}")
        object_parent[token] = copied_value
        return
    if isinstance(parent, list):
        list_parent = cast(JsonArray, parent)
        list_index = _parse_array_index(
            token,
            len(list_parent),
            allow_append=not replace,
            operation_index=operation_index,
        )
        if replace:
            list_parent[list_index] = copied_value
        elif token == "-":
            list_parent.append(copied_value)
        else:
            list_parent.insert(list_index, copied_value)
        return
    raise PatchApplicationError(f"operations[{operation_index}] target parent is not an object or array")


def _remove_value(document: JsonObject, tokens: list[str], *, operation_index: int) -> None:
    if not tokens:
        raise PatchApplicationError(f"operations[{operation_index}] cannot remove the document root")
    parent, token = _resolve_parent(document, tokens, operation_index=operation_index)
    assert token is not None
    if isinstance(parent, dict):
        object_parent = cast(dict[str, object], parent)
        if token not in object_parent:
            raise PatchApplicationError(f"operations[{operation_index}] remove target does not exist: {token!r}")
        del object_parent[token]
        return
    if isinstance(parent, list):
        list_parent = cast(JsonArray, parent)
        list_index = _parse_array_index(token, len(list_parent), allow_append=False, operation_index=operation_index)
        del list_parent[list_index]
        return
    raise PatchApplicationError(f"operations[{operation_index}] target parent is not an object or array")


def _parse_array_index(token: str, length: int, *, allow_append: bool, operation_index: int) -> int:
    if token == "-":
        if allow_append:
            return length
        raise PatchApplicationError(f"operations[{operation_index}] '-' is only valid for add array append")
    if token == "" or not token.isdigit():
        raise PatchApplicationError(f"operations[{operation_index}] array index must be a non-negative integer; got {token!r}")
    if len(token) > 1 and token.startswith("0"):
        raise PatchApplicationError(f"operations[{operation_index}] array index has invalid leading zero: {token!r}")
    index = int(token)
    upper_bound = length if allow_append else length - 1
    if index < 0 or index > upper_bound:
        raise PatchApplicationError(
            f"operations[{operation_index}] array index {index} out of bounds for length {length}"
        )
    return index


def render_markdown(scenarios: JsonObject, template_path: Path) -> str:
    """Render Markdown with the existing renderer contract without writing output."""
    from importlib import util

    renderer_path = Path(__file__).resolve().with_name("render-uat-scenarios.py")
    spec = util.spec_from_file_location("uat_scenarios_renderer", renderer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load renderer module at {renderer_path}")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(str, module.render(scenarios, template_path))  # type: ignore[attr-defined]


def write_markdown_atomic(rendered: str, output_path: Path) -> None:
    """Write rendered Markdown via temp-file + replace so partial output never lands."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        tmp.write_text(rendered, encoding="utf-8", newline="\n")
        tmp.replace(output_path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--scenarios", required=True, type=Path, help="input canonical UAT scenarios JSON path")
    _ = parser.add_argument("--schema", required=True, type=Path, help="input UAT scenarios schema JSON path")
    _ = parser.add_argument("--patch", required=True, type=Path, help="JSON Patch sidecar path")
    mode = parser.add_mutually_exclusive_group(required=True)
    _ = mode.add_argument("--out", type=Path, help="write patched canonical JSON to this path")
    _ = mode.add_argument("--in-place", action="store_true", help="replace --scenarios after all checks pass")
    _ = parser.add_argument("--template", type=Path, help="Jinja2 Markdown template path for regeneration")
    _ = parser.add_argument("--output", type=Path, help="Markdown output path regenerated from patched JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if (args.template is None) != (args.output is None):
        parser.error("--template and --output must be provided together")

    try:
        scenarios_path = cast(Path, args.scenarios)
        scenarios = load_json_object(scenarios_path)
        schema = load_json_object(cast(Path, args.schema))
        sidecar = load_json_object(cast(Path, args.patch))

        patched = apply_sidecar(scenarios, sidecar)
        validate_scenarios(patched, schema)
        canonical_obj = cast(JsonObject, json.loads(canonical_json.canonicalize(patched)))

        rendered_markdown: str | None = None
        if args.template is not None and args.output is not None:
            rendered_markdown = render_markdown(canonical_obj, cast(Path, args.template))

        output_json_path = scenarios_path if args.in_place else cast(Path, args.out)
        canonical_json.write_canonical(canonical_obj, output_json_path)
        _ = sys.stderr.write(f"Applied UAT scenario patch to {output_json_path}\n")

        if rendered_markdown is not None:
            markdown_output_path = cast(Path, args.output)
            write_markdown_atomic(rendered_markdown, markdown_output_path)
            _ = sys.stderr.write(f"Regenerated UAT scenario Markdown to {markdown_output_path}\n")
        return 0
    except PatchConflictError as error:
        _ = sys.stderr.write(f"CONFLICT: {error}\n")
        return 1
    except Exception as error:
        _ = sys.stderr.write(f"ERROR: {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
