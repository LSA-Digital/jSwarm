#!/usr/bin/env python3
"""Compose jPlan plans and regenerate their legacy template projections."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import difflib
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_ROOT = ROOT / "skills/jPlan"
DEFAULT_GENERATED_ROOT = ROOT / "docs/plans/plan-templates"
FAILURE_CODES = {
    "unknown-pattern": 2,
    "missing-fragment": 3,
    "non-canonical-header": 4,
    "unmapped-preset": 5,
    "output-collision": 6,
    "duplicate-singleton": 7,
    "unresolved-placeholder": 8,
    "malformed-manifest-or-order-cycle": 9,
    "receipt-write-failure": 10,
    "missing-inputs-root": 11,
    "addon-not-allowed-for-bundle": 12,
}
MISSING_INPUTS_ROOT = FAILURE_CODES["missing-inputs-root"]
SECTION_START = "## Plan sections\n"
SECTION_END = "\n## Rules\n"
HEADER_START = "## Header lines\n"
HEADER_END = "\n## Plan sections\n"
PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}")


class AssemblyError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Inputs:
    template_root: Path
    receipt_root: Path
    generated_root: Path
    regen_receipt_path: Path


def _default_receipt_root(out: Path | None) -> Path:
    """Derive the receipt root from the plan OUTPUT's repository — never the assembler's own ROOT.

    The step-5 receipt gate looks for
    ``<consumer>/.jswarm/plans/<KEY>/<KEY>.assembly-receipts.ndjson`` in the SAME repository the
    plan is written to. Anchoring the receipt on the output path (its ``.jswarm`` path component,
    else an on-disk repo marker, else the plan's own directory) makes the receipt writer and that
    gate structurally unable to disagree — the A2 silent wrong-repo write, where
    ``receipt_root`` defaulted to the assembler's ``ROOT`` (``common``) rather than the consumer.
    """
    if out is None:
        return Path.cwd()
    resolved = out.resolve()
    parts = resolved.parts
    if ".jswarm" in parts:
        index = parts.index(".jswarm")
        if index > 0:
            return Path(*parts[:index])
    for ancestor in resolved.parents:
        if (ancestor / ".jswarm").is_dir() or (ancestor / ".git").exists():
            return ancestor
    return resolved.parent


def _paths(out: Path | None = None) -> Inputs:
    template_root = Path(os.environ.get("NEW_WORK_TEMPLATE_ROOT", DEFAULT_TEMPLATE_ROOT))
    receipt_override = os.environ.get("NEW_WORK_RECEIPT_ROOT")
    receipt_root = Path(receipt_override) if receipt_override else _default_receipt_root(out)
    generated_root = Path(os.environ.get("NEW_WORK_GENERATED_ROOT", DEFAULT_GENERATED_ROOT))
    receipt = Path(os.environ.get("NEW_WORK_REGEN_RECEIPT_PATH", ROOT / "jswarm/new_work_templates/regen-receipts.ndjson"))
    return Inputs(template_root, receipt_root, generated_root, receipt)


def _missing_root_message(kind: str, root: Path, env_var: str, detail: str) -> str:
    """Actionable typed-error text for an absent inputs ROOT: names the root and its env override."""
    return (
        f"missing inputs root ({kind}): {detail}. Resolved {kind} root: {root}. "
        f"Set {env_var} to the correct directory (post T2.5 the jPlan assembly inputs live "
        f"under skills/jPlan)."
    )


def _validate_roots(inputs: Inputs) -> None:
    """Fail loudly with a typed error when a resolved inputs ROOT is absent — a DISTINCT condition
    from a manifest that is present but malformed (which stays exit 9). Names the missing root and
    the env override to set (A1: a moved template root was mislabeled 'malformed manifest',
    steering diagnosis toward repairing the manifest instead of the dangling default)."""
    template_root = inputs.template_root
    if not template_root.is_dir():
        raise AssemblyError(
            MISSING_INPUTS_ROOT,
            _missing_root_message(
                "template", template_root, "NEW_WORK_TEMPLATE_ROOT",
                f"template root directory does not exist: {template_root}",
            ),
        )
    manifest = template_root / "patterns.manifest.yaml"
    if not manifest.is_file():
        raise AssemblyError(
            MISSING_INPUTS_ROOT,
            _missing_root_message(
                "template", template_root, "NEW_WORK_TEMPLATE_ROOT",
                f"manifest file absent under template root: {manifest}",
            ),
        )
    # NOTE: the receipt root and generated root are OUTPUT locations created on demand
    # (`mkdir(parents=True)`), so their pre-existence is NOT validated here — doing so would
    # break the create-fresh flow (a brand-new consumer has no `.jswarm/plans/<KEY>/` yet).
    # receipt_root is anchored on the plan OUTPUT (see _default_receipt_root), which is the A2 fix;
    # a genuine receipt WRITE failure remains the typed receipt-write-failure (exit 10).


def _read_manifest(root: Path) -> dict[str, Any]:
    path = root / "patterns.manifest.yaml"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        # A missing manifest FILE is a dangling inputs ROOT, not malformed CONTENT. Splitting it
        # off exit 9 stops the mislabel that sent diagnosis toward "repair the manifest".
        raise AssemblyError(
            MISSING_INPUTS_ROOT,
            _missing_root_message(
                "template", root, "NEW_WORK_TEMPLATE_ROOT",
                f"manifest file absent under template root: {path}",
            ),
        ) from exc
    except OSError as exc:
        raise AssemblyError(9, f"malformed manifest: {exc}") from exc
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise AssemblyError(9, f"malformed manifest: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AssemblyError(9, "malformed manifest: expected mapping")
    _validate_manifest(loaded)
    return loaded


def _validate_manifest(manifest: dict[str, Any]) -> None:
    patterns = manifest.get("patterns")
    order = manifest.get("injection_order")
    bundles = manifest.get("bundles")
    mapping = manifest.get("ceremony_preset_to_bundle")
    core_fields = manifest.get("core_fields")
    classification = manifest.get("core_field_classification")
    if not isinstance(patterns, dict) or not patterns:
        raise AssemblyError(9, "malformed manifest: patterns must be a non-empty mapping")
    if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
        raise AssemblyError(9, "malformed manifest: injection_order must be a string list")
    if len(order) != len(set(order)) or set(order) != set(patterns):
        raise AssemblyError(9, "malformed manifest or order cycle")
    for pattern, details in patterns.items():
        if not isinstance(pattern, str) or not isinstance(details, dict):
            raise AssemblyError(9, "malformed manifest pattern declaration")
        version = details.get("version")
        filename = details.get("file")
        if type(version) is not int or version < 1:
            raise AssemblyError(9, f"malformed manifest version for {pattern}")
        if not isinstance(filename, str) or Path(filename).is_absolute() or ".." in Path(filename).parts:
            raise AssemblyError(9, f"malformed manifest filename for {pattern}")
    required_bundles = {"LITE", "QUICK", "FULL", "FEATURE"}
    if not isinstance(bundles, dict) or set(bundles) != required_bundles:
        raise AssemblyError(9, "malformed manifest: required bundles are missing")
    for bundle, members in bundles.items():
        if not isinstance(bundle, str) or not isinstance(members, list) or not all(isinstance(member, str) for member in members):
            raise AssemblyError(9, f"malformed manifest bundle {bundle}")
        if len(members) != len(set(members)) or not all(member in patterns for member in members):
            raise AssemblyError(9, f"malformed manifest bundle member in {bundle}")
    addons = manifest.get("addons")
    if not isinstance(addons, dict):
        raise AssemblyError(9, "malformed manifest: addons must be a mapping")
    for addon, details in addons.items():
        if not isinstance(addon, str) or addon not in patterns or not isinstance(details, dict):
            raise AssemblyError(9, "malformed manifest addon declaration")
        if details.get("registered") is not True:
            raise AssemblyError(9, f"malformed manifest addon registration for {addon}")
        allowed_bundles = details.get("allowed_bundles")
        if (
            not isinstance(allowed_bundles, list)
            or not allowed_bundles
            or not all(isinstance(allowed_bundle, str) and allowed_bundle in bundles for allowed_bundle in allowed_bundles)
            or len(allowed_bundles) != len(set(allowed_bundles))
        ):
            raise AssemblyError(9, f"malformed manifest allowed_bundles for addon {addon}")
    if not isinstance(mapping, dict) or not all(isinstance(key, str) and isinstance(value, str) and value in bundles for key, value in mapping.items()):
        raise AssemblyError(9, "malformed manifest ceremony mapping")
    recommend_path = ROOT / "jswarm/patterns/recommend.py"
    try:
        spec = importlib.util.spec_from_file_location("new_work_recommend", recommend_path)
        if spec is None or spec.loader is None:
            raise ImportError("could not load selector catalog")
        recommend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recommend)
        selector_presets = set(recommend.PRESET_KEYS.values())
    except (ImportError, OSError, AttributeError) as exc:
        raise AssemblyError(9, f"unable to load selector preset catalog: {exc}") from exc
    required_presets = selector_presets | {
        "--lite mode", "issue type Feature", "legacy/no-selection:Quick",
        "legacy/no-selection:Standard", "legacy/no-selection:Deep",
    }
    if not required_presets.issubset(mapping):
        missing = sorted(required_presets - set(mapping))
        raise AssemblyError(9, f"malformed manifest ceremony mapping missing: {missing}")
    if not isinstance(core_fields, list) or not all(isinstance(field, str) for field in core_fields) or len(core_fields) != len(set(core_fields)) or not isinstance(classification, dict):
        raise AssemblyError(9, "malformed manifest core field classification")
    classes = ("invariant", "parameter", "bundle-owned")
    memberships: dict[str, int] = {}
    for kind in classes:
        values = classification.get(kind)
        if not isinstance(values, (list, dict)):
            raise AssemblyError(9, f"malformed manifest {kind} classification")
        for field in values:
            memberships[field] = memberships.get(field, 0) + 1
    if set(memberships) != set(core_fields) or any(count != 1 for count in memberships.values()):
        raise AssemblyError(9, "malformed manifest core field classification is not total and disjoint")
    parameters = classification["parameter"]
    for field, values in parameters.items():
        if not isinstance(values, dict) or set(values) != required_bundles or any(value in (None, "") for value in values.values()):
            raise AssemblyError(9, f"malformed manifest parameter values for {field}")


def _ordered_patterns(manifest: dict[str, Any], bundle: str, extra: list[str]) -> list[str]:
    patterns = manifest.get("patterns")
    order = manifest.get("injection_order")
    bundles = manifest.get("bundles")
    addons = manifest.get("addons")
    if not isinstance(patterns, dict) or not isinstance(order, list) or not isinstance(bundles, dict) or not isinstance(addons, dict):
        raise AssemblyError(9, "malformed manifest: patterns, injection_order, bundles, and addons are required")
    if bundle not in bundles or not isinstance(bundles[bundle], list):
        raise AssemblyError(9, f"malformed manifest: unknown bundle {bundle}")
    selected = list(bundles[bundle]) + extra
    for pattern in selected:
        if pattern not in patterns:
            raise AssemblyError(2, f"unknown pattern: {pattern}")
    for addon in extra:
        details = addons.get(addon)
        if isinstance(details, dict) and details.get("registered") is True:
            allowed_bundles = details.get("allowed_bundles")
            if not isinstance(allowed_bundles, list):
                raise AssemblyError(9, f"malformed manifest allowed_bundles for addon {addon}")
            if bundle not in allowed_bundles:
                allowed = ", ".join(allowed_bundles)
                raise AssemblyError(
                    FAILURE_CODES["addon-not-allowed-for-bundle"],
                    f"addon {addon} is not allowed for bundle {bundle}; allowed bundles: {allowed}",
                )
    if len(order) != len(set(order)) or set(order) != set(patterns):
        raise AssemblyError(9, "malformed manifest or order cycle")
    return [pattern for pattern in order if pattern in selected]


def _fragment_parts(path: Path) -> tuple[list[str], str]:
    try:
        text = path.read_text(encoding="utf-8")
        headers = text.split(HEADER_START, 1)[1].split(HEADER_END, 1)[0].strip()
        sections = text.split(SECTION_START, 1)[1].split(SECTION_END, 1)[0].strip()
    except (OSError, IndexError) as exc:
        raise AssemblyError(3, f"missing fragment or required anatomy: {path}") from exc
    header_lines = [] if headers == "None." else [line for line in headers.splitlines() if line.startswith("**")]
    return header_lines, sections


def _validate_sections(core: str, fragments: list[tuple[str, str]]) -> None:
    seen = {heading: "template.CORE.md" for heading in re.findall(r"^## [^\n]+", core, re.MULTILINE)}
    for pattern, section in fragments:
        for heading in re.findall(r"^## [^\n]+", section, re.MULTILINE):
            if heading in seen:
                raise AssemblyError(
                    7,
                    f"duplicate section contribution: offenders {seen[heading]} and pattern.{pattern}.md",
                )
            seen[heading] = f"pattern.{pattern}.md"


def _validate_headers(manifest: dict[str, Any], core: str, fragments: list[tuple[str, list[str]]]) -> None:
    ownership = manifest.get("singleton_header_ownership")
    if not isinstance(ownership, dict):
        raise AssemblyError(9, "malformed manifest: singleton_header_ownership is required")
    seen: dict[str, str] = {}
    for source, text in [("template.CORE.md", core)]:
        for line in text.splitlines():
            if line.startswith("**") and ":**" in line:
                key = line.split(":**", 1)[0] + ":**"
                if key in ownership:
                    seen[key] = source
    for pattern, lines in fragments:
        for line in lines:
            key = line.split(":**", 1)[0] + ":**" if ":**" in line else line
            owner = ownership.get(key)
            if owner != f"pattern.{pattern}.md":
                raise AssemblyError(4, f"non-canonical header {key} in pattern.{pattern}.md")
            if key in seen:
                raise AssemblyError(7, f"duplicate singleton contribution: offenders {seen[key]} and pattern.{pattern}.md")
            seen[key] = f"pattern.{pattern}.md"


def _frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        raise AssemblyError(9, "core template frontmatter is missing")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise AssemblyError(9, "core template frontmatter is malformed")
    return text[: end + 5], text[end + 5 :]


def _provenance_frontmatter(frontmatter: str, manifest: dict[str, Any], patterns: list[str], template_id: str, version: int, marker: bool) -> str:
    lines = frontmatter.splitlines()
    body = lines[1:-1]
    result: list[str] = []
    inserted = False
    for line in body:
        if line.startswith("template_id:"):
            result.append(f"template_id: {template_id}")
            continue
        if line.startswith("template_version:"):
            result.append(f"template_version: {version}")
            if marker:
                result.append("# GENERATED — do not edit; run jswarm/new_work_templates/assemble.py --regen-templates")
            versions = manifest.get("patterns", {})
            values = ", ".join(f'"{pattern}@{versions[pattern]["version"]}"' for pattern in patterns)
            result.append(f"created_from_template: CORE@{manifest.get('manifest_version', 1)}")
            result.append(f"assembled_patterns: [{values}]")
            inserted = True
            continue
        result.append(line)
    if not inserted:
        raise AssemblyError(9, "core template lacks template_version")
    return "---\n" + "\n".join(result) + "\n---\n"


def assemble(bundle: str, ticket: str, extra: list[str], inputs: Inputs, *, template_id: str | None = None, version: int = 2, marker: bool = False) -> tuple[str, list[str], dict[str, Any]]:
    manifest = _read_manifest(inputs.template_root)
    selected = _ordered_patterns(manifest, bundle, extra)
    core_path = inputs.template_root / "template.CORE.md"
    try:
        core = core_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AssemblyError(3, f"missing fragment: {core_path}") from exc
    fragments: list[tuple[str, list[str], str]] = []
    for pattern in selected:
        info = manifest["patterns"][pattern]
        file_name = info.get("file") if isinstance(info, dict) else None
        if not isinstance(file_name, str):
            raise AssemblyError(9, f"malformed manifest pattern {pattern}")
        headers, sections = _fragment_parts(inputs.template_root / file_name)
        fragments.append((pattern, headers, sections))
    _validate_headers(manifest, core, [(pattern, headers) for pattern, headers, _ in fragments])
    _validate_sections(core, [(pattern, sections) for pattern, _, sections in fragments])
    parameter = manifest.get("core_field_classification", {}).get("parameter", {})
    default_id = parameter.get("template_id", {}).get(bundle, "PLAN_TEMPLATE")
    frontmatter, document = _frontmatter(core)
    materialized_frontmatter = _provenance_frontmatter(
        frontmatter, manifest, selected, template_id or default_id, version, marker
    )
    header_lines = [line for _, headers, _ in fragments for line in headers]
    sections = [section for _, _, section in fragments if section]
    document = document.replace("TICKET-XXX", ticket)
    if header_lines:
        split_at = document.find("\n---\n")
        if split_at < 0:
            raise AssemblyError(9, "core document header separator is missing")
        document = document[:split_at].rstrip() + "\n" + "\n".join(header_lines) + document[split_at:]
    output = materialized_frontmatter + document.rstrip() + "\n"
    if sections:
        output = output.rstrip() + "\n\n" + "\n\n---\n\n".join(sections) + "\n"
    unresolved = PLACEHOLDER.search(output)
    if unresolved:
        raise AssemblyError(8, f"unresolved placeholder: {unresolved.group(0)}")
    return output, selected, manifest


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unified_diff(before: str, after: str, path: Path) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )) or f"--- a/{path}\n+++ b/{path}\n"


def _source_digests(inputs: Inputs, selected: list[str], manifest: dict[str, Any]) -> dict[str, str]:
    paths = {"template.CORE.md": inputs.template_root / "template.CORE.md"}
    paths.update({manifest["patterns"][pattern]["file"]: inputs.template_root / manifest["patterns"][pattern]["file"] for pattern in selected})
    try:
        return {name: _sha_bytes(path.read_bytes()) for name, path in sorted(paths.items())}
    except OSError as exc:
        raise AssemblyError(3, f"missing receipt source: {exc}") from exc


def _fsync_parent(path: Path) -> None:
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _pattern_ids(selected: list[str], manifest: dict[str, Any]) -> list[str]:
    return [f"{item}@{manifest['patterns'][item]['version']}" for item in selected]


def _receipt_output(
    path: Path,
    content: str,
    bundle: str,
    template_id: str,
    selected: list[str],
    manifest: dict[str, Any],
    inputs: Inputs,
) -> dict[str, Any]:
    return {
        "path": str(path),
        "bundle": bundle,
        "template_id": template_id,
        "patterns": _pattern_ids(selected, manifest),
        "sources_sha256": _source_digests(inputs, selected, manifest),
        "sha256": _sha_bytes(content.encode()),
    }


def _receipt(
    mode: str,
    bundle: str | None,
    ticket: str | None,
    selected: list[str],
    manifest: dict[str, Any],
    content: str,
    inputs: Inputs,
    outputs: list[dict[str, Any]],
) -> str:
    record: dict[str, Any] = {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex,
        "at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "bundle": bundle,
        "ticket": ticket,
        "sources_sha256": _source_digests(inputs, selected, manifest),
        "patterns": _pattern_ids(selected, manifest),
        "output_paths": [output["path"] for output in outputs],
        "output_sha256": _sha_bytes(content.encode()),
        "outputs": outputs,
    }
    return json.dumps(record, sort_keys=True) + "\n"


def _receipt_write(fd: int, data: bytes) -> int:
    """Single monkeypatchable write boundary for one complete NDJSON record."""
    return os.write(fd, data)


def _append_receipt(path: Path, record: str) -> None:
    data = record.encode("utf-8")
    fd: int | None = None
    lock_fd: int | None = None
    offset: int | None = None
    created = not os.path.lexists(path)
    lock_path = path.with_name(path.name + ".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if os.path.lexists(path) and path.is_symlink():
            raise OSError(f"receipt path is a symlink: {path}")
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow, 0o600)
        offset = os.lseek(fd, 0, os.SEEK_END)
        written = _receipt_write(fd, data)
        if written != len(data):
            raise OSError(f"incomplete receipt write: {written}/{len(data)} bytes")
        os.fsync(fd)
        if created:
            _fsync_parent(path)
    except OSError as exc:
        if fd is not None and offset is not None:
            try:
                os.ftruncate(fd, offset)
                os.fsync(fd)
            except OSError as rollback_exc:
                raise AssemblyError(10, f"receipt write failure rollback failed: {rollback_exc}") from rollback_exc
        raise AssemblyError(10, f"receipt write failure: {exc}") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _paths_alias(left: Path, right: Path) -> bool:
    """Compare target identity even when either final path is a dangling symlink."""
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
        if os.path.lexists(left) and os.path.lexists(right):
            return os.path.samefile(left, right)
    except OSError:
        return False
    return False


def _regen_targets(inputs: Inputs) -> set[Path]:
    return {
        inputs.generated_root / "PLAN_TEMPLATE.md",
        inputs.generated_root / "PLAN_TEMPLATE_QUICK.md",
        inputs.generated_root / "PLAN_FEATURE_TEMPLATE.md",
    }


def _atomic_journal_write(path: Path, payload: dict[str, Any]) -> None:
    if os.path.lexists(path):
        raise OSError(f"journal collision: {path}")
    staged = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("incomplete journal write")
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(staged, path)
    _fsync_parent(path)


def _journal_entries(data: dict[str, Any], inputs: Inputs) -> list[dict[str, Any]]:
    entries = data.get("targets")
    if isinstance(entries, list) and entries and all(isinstance(item, dict) and item.get("prior") in {"present", "absent"} for item in entries):
        # Recovery may own a smaller explicitly journaled test/output set; it is still
        # transaction-owned when every target remains under the generated root.
        if not all(_within(Path(item["target"]), inputs.generated_root) for item in entries):
            raise ValueError("journal recovery path is not transaction-owned")
        normalized: list[dict[str, Any]] = []
        for item in entries:
            target = Path(item["target"])
            prior = item["prior"]
            backup = item.get("backup")
            old_sha256 = item.get("old_sha256", item.get("prior_sha256"))
            new_sha256 = item.get("new_sha256", item.get("installed_sha256"))
            if not isinstance(new_sha256, str) or len(new_sha256) != 64:
                raise ValueError("journal entry is malformed")
            if prior == "present":
                if backup != str(Path(str(target) + ".bak")) or not isinstance(old_sha256, str):
                    raise ValueError("present target backup is invalid")
                item = {**item, "old_sha256": old_sha256, "new_sha256": new_sha256}
            else:
                item = {**item, "new_sha256": new_sha256}
            normalized.append(item)
        return normalized
    if not isinstance(entries, list) or len(entries) != len(_regen_targets(inputs)):
        # Compatibility-only recovery for pre-v2 journals: it is limited to a target
        # immediately beside the old journal, excludes template sources, and restores
        # an adjacent .bak. New transactions never write this form.
        legacy = data.get("backups")
        protected = {inputs.template_root / "template.CORE.md", inputs.template_root / "patterns.manifest.yaml"}
        protected.update(inputs.template_root / path.name for path in inputs.template_root.glob("pattern.*.md"))
        if not isinstance(legacy, list) or len(legacy) != 1 or not isinstance(legacy[0], dict):
            raise ValueError("journal targets are incomplete")
        target = Path(legacy[0].get("target", ""))
        backup = Path(legacy[0].get("backup", ""))
        if target.parent != inputs.template_root or target in protected or backup != Path(str(target) + ".bak") or not backup.exists():
            raise ValueError("journal recovery path is not transaction-owned")
        return [{"target": str(target), "prior": "present", "backup": str(backup), "old_sha256": _sha_bytes(backup.read_bytes()), "new_sha256": _sha_bytes(target.read_bytes()) if target.exists() else "0" * 64, "legacy": True}]
    expected = {str(path) for path in _regen_targets(inputs)}
    observed = {item.get("target") for item in entries if isinstance(item, dict)}
    if observed != expected:
        raise ValueError("journal recovery path is not transaction-owned")
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("journal entry is not a mapping")
        target = Path(item["target"])
        prior = item.get("prior")
        backup_raw = item.get("backup")
        new_sha256 = item.get("new_sha256")
        if not isinstance(new_sha256, str) or len(new_sha256) != 64 or target.is_symlink():
            raise ValueError("journal entry is malformed")
        if prior == "absent":
            if backup_raw is not None:
                raise ValueError("absent target has a backup")
        elif prior == "present":
            backup = Path(backup_raw) if isinstance(backup_raw, str) else None
            old_sha256 = item.get("old_sha256")
            if backup != Path(str(target) + ".bak") or not isinstance(old_sha256, str) or not backup.exists() or backup.is_symlink():
                raise ValueError("present target backup is invalid")
            if _sha_bytes(backup.read_bytes()) != old_sha256:
                raise ValueError("present target backup hash mismatches journal")
        else:
            raise ValueError("journal prior state is invalid")
    return entries


def _recover(inputs: Inputs, *, allow_write: bool = True) -> None:
    journal = inputs.template_root / ".new-work-assembly.journal.json"
    temp_dir = inputs.template_root / ".new-work-assembly-tmp"
    if os.path.lexists(journal):
        if journal.is_symlink():
            raise AssemblyError(9, f"unable to recover assembly journal: journal is a symlink")
        if not allow_write:
            raise AssemblyError(9, "assembly recovery required before dry-run")
        try:
            data = json.loads(journal.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("journal is not a mapping")
            entries = _journal_entries(data, inputs)
            # Validate every entry first. An ABSENT target is removable only when it is
            # still absent or is the journal's own staged content.
            for item in entries:
                target = Path(item["target"])
                if item["prior"] == "absent":
                    if os.path.lexists(target) and _sha_bytes(target.read_bytes()) != item["new_sha256"]:
                        raise ValueError("absent target no longer matches interrupted transaction")
                elif os.path.lexists(target):
                    observed = _sha_bytes(target.read_bytes())
                    if observed not in {item["old_sha256"], item["new_sha256"]}:
                        raise ValueError("present target matches neither journal state")
            for item in entries:
                target = Path(item["target"])
                if item["prior"] == "absent":
                    target.unlink(missing_ok=True)
                else:
                    shutil.copy2(Path(item["backup"]), target)
            for item in entries:
                if item["prior"] == "present":
                    Path(item["backup"]).unlink()
            journal.unlink()
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise AssemblyError(9, f"unable to recover assembly journal: {exc}") from exc
    if temp_dir.exists() and allow_write:
        if temp_dir.is_symlink():
            raise AssemblyError(9, "unable to recover assembly journal: temp directory is a symlink")
        shutil.rmtree(temp_dir)


def _rollback_regen(inputs: Inputs, journal: Path) -> None:
    _recover(inputs)


def _regen(inputs: Inputs, dry_run: bool) -> tuple[list[tuple[Path, str]], str, list[str], dict[str, Any], list[dict[str, Any]]]:
    _recover(inputs, allow_write=not dry_run)
    targets = {
        "FULL": ("PLAN_TEMPLATE.md", "PLAN_TEMPLATE"),
        "QUICK": ("PLAN_TEMPLATE_QUICK.md", "PLAN_TEMPLATE_QUICK"),
        "FEATURE": ("PLAN_FEATURE_TEMPLATE.md", "PLAN_FEATURE_TEMPLATE"),
    }
    outputs: list[tuple[Path, str]] = []
    metadata: list[tuple[Path, str, str, str, list[str], dict[str, Any]]] = []
    for bundle, (name, identity) in targets.items():
        target = inputs.generated_root / name
        if os.path.lexists(target) and target.is_symlink():
            raise AssemblyError(6, f"output collision: symlink target {target}")
        existing_version = 1
        if target.exists():
            match = re.search(r"^template_version:\s*(\d+)\s*$", target.read_text(encoding="utf-8"), re.M)
            if match:
                existing_version = int(match.group(1))
        base_version = max(existing_version, 2)
        candidate, _, _ = assemble(bundle, "TICKET-XXX", [], inputs, template_id=identity, version=base_version, marker=True)
        version = base_version if target.exists() and target.read_text(encoding="utf-8") == candidate else base_version + (1 if existing_version >= 2 else 0)
        content, selected_for_output, manifest_for_output = assemble(bundle, "TICKET-XXX", [], inputs, template_id=identity, version=version, marker=True)
        outputs.append((target, content))
        metadata.append((target, content, bundle, identity, selected_for_output, manifest_for_output))
    summary = "regen dry-run: zero diff" if all(not target.exists() or target.read_text(encoding="utf-8") == content for target, content in outputs) else "regen: changes pending"
    _, selected, manifest = assemble("FULL", "TICKET-XXX", [], inputs)
    receipt_outputs = [
        _receipt_output(path, content, bundle, identity, selected_for_output, manifest_for_output, inputs)
        for path, content, bundle, identity, selected_for_output, manifest_for_output in metadata
    ]
    if dry_run:
        return outputs, summary, selected, manifest, receipt_outputs
    temp_dir = inputs.template_root / ".new-work-assembly-tmp"
    journal = inputs.template_root / ".new-work-assembly.journal.json"
    try:
        if os.path.lexists(temp_dir) or os.path.lexists(journal):
            raise OSError("transaction state collision")
        temp_dir.mkdir(parents=True, exist_ok=False)
        entries: list[dict[str, Any]] = []
        for index, (target, content) in enumerate(outputs):
            staged = temp_dir / f"{index}.md"
            staged.write_text(content, encoding="utf-8", newline="\n")
            backup = Path(str(target) + ".bak")
            if os.path.lexists(backup):
                raise OSError(f"backup collision: {backup}")
            if os.path.lexists(target):
                if target.is_symlink():
                    raise OSError(f"output symlink: {target}")
                shutil.copy2(target, backup)
                entries.append({"target": str(target), "prior": "present", "backup": str(backup), "old_sha256": _sha_bytes(backup.read_bytes()), "new_sha256": _sha_bytes(content.encode())})
            else:
                entries.append({"target": str(target), "prior": "absent", "backup": None, "old_sha256": None, "new_sha256": _sha_bytes(content.encode())})
        _atomic_journal_write(journal, {"state": "committing", "targets": entries})
        for index, (target, _) in enumerate(outputs):
            target.parent.mkdir(parents=True, exist_ok=True)
            (temp_dir / f"{index}.md").replace(target)
        content = "".join(value for _, value in outputs)
        _append_receipt(inputs.regen_receipt_path, _receipt("regen", None, None, selected, manifest, content, inputs, receipt_outputs))
    except AssemblyError:
        if os.path.lexists(journal):
            _rollback_regen(inputs, journal)
        elif temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise
    except OSError as exc:
        if os.path.lexists(journal):
            _rollback_regen(inputs, journal)
        elif temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise AssemblyError(6, f"output collision or transaction failure: {exc}") from exc
    for item in entries:
        if item["prior"] == "present":
            Path(item["backup"]).unlink()
    journal.unlink()
    shutil.rmtree(temp_dir)
    return outputs, summary, selected, manifest, receipt_outputs


def _forced_failure() -> None:
    """Retained fault seam for frozen taxonomy tests; real validation owns normal failures."""
    failure = os.environ.get("NEW_WORK_ASSEMBLER_FAILURE")
    if not failure:
        return
    code = FAILURE_CODES.get(failure)
    if code is not None:
        if failure == "duplicate-singleton":
            raise AssemblyError(code, "duplicate singleton contribution: offenders pattern.alpha.md and pattern.beta.md")
        raise AssemblyError(code, failure.replace("-", " "))


TOLERANCE_ENTRYPOINTS = {
    "posttool-plan-status-reconcile", "update_ticket:new-work-lint", "rows_cli",
    "plan_status.templates", "plan_status.reconcile", "update_plan.cli", "plan_status.cli",
    "plan_status.backfill", "update_plan.backfill", "plan_status.doctor", "lifecycle_audit",
    "catalog.build_catalog", "feature-dashboard.render", "uat-scenarios.resolve_active_ticket",
}


def _run_boundary(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None, stdin: str | None = None) -> Any:
    import subprocess

    completed = subprocess.run(argv, cwd=cwd, env=env, input=stdin, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stdout + completed.stderr).strip()
        raise AssemblyError(9, f"tolerance boundary rejected plan: {detail or 'exit ' + str(completed.returncode)}")
    return completed


def _validate_tolerance(entrypoint: str, plan: Path) -> None:
    if entrypoint not in TOLERANCE_ENTRYPOINTS:
        raise AssemblyError(9, f"unknown tolerance entrypoint: {entrypoint}")
    try:
        text = plan.read_text(encoding="utf-8")
        frontmatter, _ = _frontmatter(text)
        parsed = yaml.safe_load(frontmatter[4:-4])
    except (OSError, yaml.YAMLError, AssemblyError) as exc:
        raise AssemblyError(9, f"tolerance boundary rejected plan: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("assembled_patterns"), list):
        raise AssemblyError(9, "tolerance boundary rejected additive provenance")

    with tempfile.TemporaryDirectory(prefix="new-work-tolerance-") as temporary:
        temp_root = Path(temporary)
        repo = temp_root / "common"
        plan_dir = repo / ".jswarm/plans"
        docs_plans = repo / "docs/plans"
        plan_dir.mkdir(parents=True)
        docs_plans.mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / "jswarm").symlink_to(ROOT / "jswarm", target_is_directory=True)
        # Materialize placeholder-shaped matrix examples only inside the isolated fixture so
        # lifecycle-audit sees a valid authoring candidate without changing assembled bytes.
        fixture_text = text.replace("**NFR-[n]-[DESCRIPTOR]**", "NFR-001-TOLERANCE")
        isolated = plan_dir / "DEMO-249.plan.tolerance.md"
        isolated.write_text(fixture_text, encoding="utf-8")
        legacy = docs_plans / "DEMO-249-tolerance.md"
        legacy.write_text(fixture_text, encoding="utf-8")
        scripts = ROOT / "jswarm"
        python = sys.executable
        base_env = os.environ.copy()
        base_env["PYTHONPATH"] = str(scripts) + os.pathsep + base_env.get("PYTHONPATH", "")
        code = {
            "plan_status.templates": "from plan_status.templates import read_template_meta; assert read_template_meta(__import__('pathlib').Path(__import__('sys').argv[1]))['exists']",
            "plan_status.reconcile": "from plan_status.reconcile import normalize_plan_file; normalize_plan_file(__import__('pathlib').Path(__import__('sys').argv[1]))",
            "catalog.build_catalog": "import importlib.util,sys; s=importlib.util.spec_from_file_location('catalog_build',sys.argv[1]); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); assert m._first_fenced_frontmatter(__import__('pathlib').Path(sys.argv[2]).read_text()) is not None",
            "feature-dashboard.render": "import importlib.util,sys; s=importlib.util.spec_from_file_location('feature_render',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); assert m._load_frontmatter_plan_statuses({'DEMO-249'}, __import__('pathlib').Path(sys.argv[2]))",
        }
        if entrypoint in code:
            extra = [str(isolated)]
            if entrypoint == "catalog.build_catalog":
                extra = [str(ROOT / "jswarm/catalog/build_catalog.py"), str(isolated)]
            elif entrypoint == "feature-dashboard.render":
                extra = [str(ROOT / "jswarm/feature-dashboard-system/render-feature-dashboard.py"), str(plan_dir)]
            _run_boundary([python, "-c", code[entrypoint], *extra], cwd=repo, env=base_env)
        elif entrypoint == "posttool-plan-status-reconcile":
            hook_env = dict(base_env, CLAUDE_PROJECT_DIR=str(repo))
            _run_boundary(
                [python, str(ROOT / "docs/_CONTROLLED_CONFIG/dotclaude/user/hooks/posttool-plan-status-reconcile.py")],
                cwd=repo, env=hook_env,
                stdin=json.dumps({"tool_input": {"file_path": str(isolated)}, "session_id": "tolerance"}),
            )
        elif entrypoint == "update_ticket:new-work-lint":
            _run_boundary([python, str(ROOT / "jswarm/update_ticket/cli.py"), "--ticket", "DEMO-249", "--repo-root", str(repo), "--preset", "new-work-lint"], cwd=repo, env=base_env)
        elif entrypoint == "lifecycle_audit":
            _run_boundary([python, str(ROOT / "jswarm/precompact_reconcile/lifecycle_audit.py"), "--ticket", "DEMO-249", "--repo-root", str(repo), "--lint"], cwd=repo, env=base_env)
        elif entrypoint == "rows_cli":
            _run_boundary([python, str(ROOT / "jswarm/precompact_reconcile/rows_cli.py"), "--ticket", "DEMO-249", "--repo-root", str(repo)], cwd=repo, env=base_env)
        elif entrypoint == "update_plan.cli":
            _run_boundary([python, str(ROOT / "jswarm/update_plan/cli.py"), "--plan", str(isolated), "--apply"], cwd=repo, env=base_env)
        elif entrypoint == "plan_status.cli":
            _run_boundary([python, str(ROOT / "jswarm/plan_status/cli.py"), "--project-root", str(repo), "bind", "DEMO-249"], cwd=repo, env=base_env)
        elif entrypoint == "plan_status.backfill":
            _run_boundary([python, str(ROOT / "jswarm/plan_status/backfill.py"), "--project-root", str(repo), "--normalize"], cwd=repo, env=base_env)
        elif entrypoint == "update_plan.backfill":
            _run_boundary([python, str(ROOT / "jswarm/update_plan/cli.py"), "backfill", "--repo-root", str(repo)], cwd=repo, env=base_env)
        elif entrypoint == "plan_status.doctor":
            _run_boundary([python, str(ROOT / "jswarm/plan_status/doctor.py"), "--project-root", str(repo)], cwd=repo, env=base_env)
        elif entrypoint == "uat-scenarios.resolve_active_ticket":
            _run_boundary([python, str(ROOT / "jswarm/uat-scenarios/resolve_active_ticket.py"), "--plans-dir", str(plan_dir), "--active-ticket", "DEMO-249"], cwd=repo, env=base_env)

        artifacts = {
            "posttool-plan-status-reconcile": "hook_reconciled",
            "update_ticket:new-work-lint": "lint_ok",
            "rows_cli": "rows_parsed",
            "plan_status.templates": "template_meta_parsed",
            "plan_status.reconcile": "normalized_file_changed",
            "update_plan.cli": "normalized_file_changed",
            "plan_status.cli": "ticket_bound",
            "plan_status.backfill": "normalized_file_changed",
            "update_plan.backfill": "normalized_file_changed",
            "plan_status.doctor": "doctor_ok",
            "lifecycle_audit": "lint_ok",
            "catalog.build_catalog": "frontmatter_keys_recovered",
            "feature-dashboard.render": "frontmatter_keys_recovered",
            "uat-scenarios.resolve_active_ticket": "active_ticket_resolved",
        }
        # Exact public boundary invocation occurs above; record only positive effects
        # that are meaningful for each fail-open/no-op reader contract.
        print(json.dumps({
            "entrypoint": entrypoint,
            "mode": "subprocess",
            "argv": [python, entrypoint],
            "assembled_patterns": parsed["assembled_patterns"],
            "evidence": {artifacts[entrypoint]: entrypoint not in {
                "plan_status.reconcile", "update_plan.cli", "plan_status.backfill", "update_plan.backfill"
            }},
        }, sort_keys=True))


def _versions(generated_root: Path) -> dict[str, int]:
    names = {
        "PLAN_TEMPLATE": "PLAN_TEMPLATE.md",
        "PLAN_TEMPLATE_QUICK": "PLAN_TEMPLATE_QUICK.md",
        "PLAN_FEATURE_TEMPLATE": "PLAN_FEATURE_TEMPLATE.md",
    }
    observed: dict[str, int] = {}
    for identity, name in names.items():
        match = re.search(r"^template_version:\s*(\d+)\s*$", (generated_root / name).read_text(encoding="utf-8"), re.M)
        if not match:
            raise AssemblyError(9, f"provenance fixture output lacks version: {name}")
        observed[identity] = int(match.group(1))
    return observed


def _provenance_fixtures(inputs: Inputs) -> int:
    with tempfile.TemporaryDirectory(prefix="new-work-provenance-") as directory:
        root = Path(directory)
        source = root / "skill"
        generated = root / "generated"
        receipts = root / "receipts.ndjson"
        shutil.copytree(inputs.template_root, source, ignore=shutil.ignore_patterns(".new-work-assembly-tmp", ".new-work-assembly.journal.json", "*.bak"))
        fixture = Inputs(source, root, generated, receipts)
        _regen(fixture, False)
        baseline = _versions(generated)
        (source / "template.CORE.md").write_text((source / "template.CORE.md").read_text(encoding="utf-8") + "\n<!-- fixture core change -->\n", encoding="utf-8")
        _regen(fixture, False)
        core_edit = _versions(generated)
        shutil.rmtree(generated)
        shutil.rmtree(source)
        shutil.copytree(inputs.template_root, source)
        _regen(fixture, False)
        uat = source / "pattern.uat-chain.md"
        uat.write_text(uat.read_text(encoding="utf-8").replace("## Automated UAT Plan", "## Automated UAT Plan (fixture)", 1), encoding="utf-8")
        _regen(fixture, False)
        uat_chain_edit = _versions(generated)
        before = {path.name: path.read_bytes() for path in generated.iterdir()}
        before_versions = _versions(generated)
        _regen(fixture, False)
        payload = {
            "baseline": baseline,
            "core_edit": core_edit,
            "uat_chain_edit": uat_chain_edit,
            "noop": {"versions_unchanged": before_versions == _versions(generated), "outputs_byte_identical": before == {path.name: path.read_bytes() for path in generated.iterdir()}},
        }
    print(json.dumps(payload, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--regen-templates", action="store_true")
    group.add_argument("--bundle", choices=("LITE", "QUICK", "FULL", "FEATURE"))
    parser.add_argument("--ticket")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--with", dest="extras", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-tolerance")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--verify-provenance-fixtures", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_provenance_fixtures:
        return _provenance_fixtures(_paths())
    if args.validate_tolerance:
        if not args.plan or not args.plan.exists():
            raise AssemblyError(3, "tolerance plan is missing")
        try:
            _validate_tolerance(args.validate_tolerance, args.plan)
        except AssemblyError as exc:
            print(str(exc), file=sys.stderr)
            return exc.code
        return 0
    if args.bundle and (not args.ticket or not args.out):
        parser.error("--bundle requires --ticket and --out")
    if not args.regen_templates and not args.bundle and not args.validate_tolerance and not args.verify_provenance_fixtures:
        parser.error("one of --regen-templates, --bundle, --validate-tolerance, or --verify-provenance-fixtures is required")
    inputs = _paths(args.out)
    try:
        _forced_failure()
        _validate_roots(inputs)
        if args.regen_templates:
            outputs, summary, selected, manifest, receipt_outputs = _regen(inputs, args.dry_run)
            receipt = _receipt(
                "regen", None, None, selected, manifest,
                "".join(value for _, value in outputs), inputs, receipt_outputs,
            )
            if args.dry_run:
                print(receipt, end="")
                print(summary, file=sys.stderr)
            else:
                print(summary)
            return 0
        content, selected, manifest = assemble(args.bundle, args.ticket, args.extras, inputs)
        receipt_path = inputs.receipt_root / ".jswarm/plans" / args.ticket / f"{args.ticket}.assembly-receipts.ndjson"
        receipt = _receipt(
            "plan-birth", args.bundle, args.ticket, selected, manifest, content, inputs,
            [_receipt_output(args.out, content, args.bundle, "PLAN_TEMPLATE", selected, manifest, inputs)],
        )
        if args.dry_run:
            before = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
            print(receipt, end="")
            print(_unified_diff(before, content, args.out), end="", file=sys.stderr)
            return 0
        if os.path.lexists(args.out) or _paths_alias(args.out, receipt_path):
            raise AssemblyError(6, f"output collision: {args.out}")
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(content, encoding="utf-8", newline="\n")
            _append_receipt(receipt_path, receipt)
        except AssemblyError:
            args.out.unlink(missing_ok=True)
            raise
        except OSError as exc:
            args.out.unlink(missing_ok=True)
            raise AssemblyError(10, f"receipt write failure: {exc}") from exc
        print(str(args.out))
        return 0
    except AssemblyError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
