#!/usr/bin/env python3
"""The sole executable authority for COM-393 Forward-Intelligence Packet v1."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

MARKER = "<!-- fix-forward-intelligence.v1 metadata -->"
ARTIFACT_KIND = "fix-forward-intelligence"
SCHEMA = "fix-forward-intelligence.v1"
HEADINGS = (
    "Named Recurrence Class And Authorized Terminal Outcome", "First Visible Break And Masked Downstream Legs",
    "Changed Contract Surfaces", "Producer / Writer Inventory", "Consumer / Reader Inventory",
    "Caller / Route Inventory", "Authority And Correlation Matrix", "Presence And Shape Semantics",
    "Replay / History Matrix", "Exception / Retry Matrix", "Trace Receipt And Gaps", "Discovery Receipts",
    "Counterexample Packet", "Inclusions, Exclusions, And Adjacent-Risk Dispositions",
    "Evidence Budget And Stop Condition", "Review Receipts", "Downstream Leverage Criterion",
)
TRIGGERS = (
    "shared representation or transport changed", "scheduling, marker, or replay decision changed",
    "more than one material reader or writer exists", "identity, cardinality, or presence semantics are involved",
    "deterministic/transient exception behavior crosses a retry boundary",
    "the same class has already caused an expensive late miss",
)
DISCOVERY_OPERATIONS = {"trace", "ColGREP", "exact search", "LSP", "AST"}
DISCOVERY_STATUSES = {"assessed", "incomplete", "unassessed"}
ROLES = {"producer", "writer", "consumer", "validator", "translator", "terminalizer", "prompt_builder"}
DISPOSITIONS = {"included", "accepted outside scope", "unresolved"}
PACKET_PATH = re.compile(r"^\.jswarm/plans/(?P<ticket>[A-Z]+-\d+)/(?P=ticket)\.fix-forward-intelligence\.[a-z0-9]+-[a-z0-9]+-[a-z0-9]+\.\d{8}(?:\.v(?P<revision>[2-9]\d*))?\.md$")
NA = re.compile(r"^N/A\s+—\s+[^;]+;\s*evidence:\s*.+$", re.I)
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|placeholder)\b", re.I)
def _discover_repo_root() -> Path | None:
    explicit = os.environ.get("JSWARM_COMMON_ROOT")
    candidates = [Path(explicit).expanduser()] if explicit else []
    candidates.extend((Path(__file__).resolve().parent, Path.cwd().resolve(), Path.home() / "dev" / "common"))
    for candidate in candidates:
        for ancestor in (candidate, *candidate.parents):
            if (ancestor / ".git").exists() and (ancestor / "docs" / "_CONTROLLED_CONFIG").is_dir():
                return ancestor
    return None


REPO_ROOT = _discover_repo_root()


class ContractViolation(ValueError):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f"PACKET_CONTRACT_VIOLATION: {code}: {detail}")


@dataclass(frozen=True)
class Packet:
    path: str
    metadata: dict[str, Any]
    sections: dict[str, str]
    raw: bytes


@dataclass(frozen=True)
class ActivationDecision:
    allowed: bool
    reason: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fail(code: str, detail: str) -> None:
    raise ContractViolation(code, detail)


def _root(context: Mapping[str, Any] | None = None) -> Path:
    configured = (context or {}).get("repo_root") or REPO_ROOT
    if configured is None:
        _fail("REPO_ROOT", "set JSWARM_COMMON_ROOT or pass --repo-root for repository operations")
    return Path(configured).resolve()


def _safe_path(value: str, root: Path, *, require_exists: bool = True) -> Path:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in Path(value).parts:
        _fail("PATH", "path must be a non-empty repository-relative non-traversing path")
    candidate = root / value
    # Never resolve through a symlink: every existing component must be ordinary.
    current = root
    for part in Path(value).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            _fail("PATH", f"symlink is forbidden: {value}")
    if require_exists and not candidate.is_file():
        _fail("FILE", f"required file does not exist: {value}")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError:
        _fail("PATH", f"path escapes repository: {value}")
    return candidate


def _read_once(value: str, root: Path) -> tuple[bytes, str]:
    path = _safe_path(value, root)
    data = path.read_bytes()
    return data, sha256_bytes(data)


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("INPUT_JSON", f"{label} is not strict UTF-8 JSON: {exc}")
    if not isinstance(parsed, dict):
        _fail("INPUT_JSON", f"{label} must be a JSON object")
    return parsed


def parse_packet_bytes(path: str, data: bytes) -> Packet:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("ENCODING", str(exc))
    if not text.startswith(MARKER + "\n"):
        _fail("METADATA_MARKER", "exact metadata marker must be the first line")
    remainder = text[len(MARKER):].lstrip("\r\n")
    decoder = json.JSONDecoder()
    try:
        metadata, consumed = decoder.raw_decode(remainder)
    except json.JSONDecodeError as exc:
        _fail("METADATA_JSON", str(exc))
    if not isinstance(metadata, dict):
        _fail("METADATA_JSON", "metadata must be one JSON object")
    body = remainder[consumed:]
    if body and not body.startswith("\n"):
        _fail("METADATA_JSON", "metadata must end before a newline")
    found = re.findall(r"^##\s+(.+?)\s*$", body, flags=re.M)
    if tuple(found) != HEADINGS:
        _fail("HEADINGS", "level-two headings must be the frozen 17 headings in exact order")
    if len(found) != len(set(found)):
        _fail("HEADINGS", "duplicate level-two heading")
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.M))
    for index, match in enumerate(matches):
        content = body[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(body)].strip()
        sections[match.group(1)] = content
    return Packet(path=path, metadata=metadata, sections=sections, raw=data)


def _verify_identity(metadata: Mapping[str, Any], name: str, root: Path) -> bytes:
    item = metadata.get(name)
    if not isinstance(item, dict):
        _fail("RECEIPT", f"missing {name} identity")
    data, actual = _read_once(item.get("path", ""), root)
    if item.get("sha256") != actual:
        _fail("DIGEST", f"{name} digest does not match retained bytes")
    return data


def _required_string(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail("METADATA", f"{key} must be a non-empty string")
    return value


def _receipt_verdict(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    matches = re.findall(r"^\s*(?:\*\*)?Verdict(?:\*\*)?\s*:\s*`?([A-Z_]+)`?\s*$", text, flags=re.M)
    return matches[0] if len(matches) == 1 else None


def _verify_receipt_verdict(metadata: Mapping[str, Any], name: str, expected: str, root: Path, code: str) -> bytes:
    data = _verify_identity(metadata, name, root)
    if _receipt_verdict(data) != expected:
        _fail(code, f"{name} must have exact Verdict: {expected}")
    return data


def _activation_receipt_matches(receipt: Any, expected: str, root: Path) -> bool:
    if not isinstance(receipt, Mapping) or receipt.get("verdict") != expected:
        return False
    digest = receipt.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    try:
        data, actual = _read_once(receipt.get("path", ""), root)
    except ContractViolation:
        return False
    return digest == actual and _receipt_verdict(data) == expected


def _validate_discovery_rows(content: str) -> None:
    rows: dict[str, list[str]] = {}
    in_table = False
    for line in content.splitlines():
        if not line.startswith("|"):
            if in_table:
                break
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells == ["Operation", "Status", "Evidence", "Disposition", "Boundary Impact"]:
            in_table = True
            continue
        if not in_table or all(re.fullmatch(r"-+", cell) for cell in cells):
            continue
        if len(cells) != 5:
            _fail("DISCOVERY", "discovery rows must have five cells")
        operation, status, evidence, disposition, boundary = cells
        if operation not in DISCOVERY_OPERATIONS:
            continue
        if operation in rows:
            _fail("DISCOVERY", f"duplicate required discovery operation {operation}")
        if status not in DISCOVERY_STATUSES:
            _fail("DISCOVERY", f"unsupported discovery status {status}")
        if not evidence or disposition not in DISPOSITIONS and not disposition.startswith(("excluded — ", "deferred — ")):
            _fail("DISCOVERY", "discovery evidence and disposition are required")
        if boundary not in {"yes", "no", "unknown"}:
            _fail("DISCOVERY", "discovery boundary impact must be yes, no, or unknown")
        if (status != "assessed" or disposition == "unresolved") and boundary in {"yes", "unknown"}:
            _fail("DISCOVERY", "incomplete or unresolved boundary-changing discovery blocks")
        rows[operation] = cells
    if set(rows) != DISCOVERY_OPERATIONS:
        _fail("DISCOVERY", "all five required discovery operations are required exactly once")


def validate_packet(packet: Packet, context: Mapping[str, Any] | None = None) -> list[ContractViolation]:
    root = _root(context)
    metadata = packet.metadata
    try:
        if metadata.get("artifact_kind") != ARTIFACT_KIND or metadata.get("schema") != SCHEMA:
            _fail("SCHEMA", "artifact kind or schema is wrong")
        packet_path = _required_string(metadata, "path")
        match = PACKET_PATH.fullmatch(packet_path)
        if not match or packet_path != packet.path:
            _fail("PATH", "Packet path must match frozen grammar and parsed path")
        if metadata.get("ticket") != match.group("ticket"):
            _fail("TICKET", "metadata ticket must agree with Packet path")
        revision = int(match.group("revision") or "1")
        if metadata.get("revision") != revision:
            _fail("REVISION", "metadata revision must equal filename revision")
        if "packet_sha256" in metadata:
            _fail("SELF_DIGEST", "Packet metadata must not embed its own digest")
        if metadata.get("requested_shape") not in {"narrow", "proportionate", "broad"}:
            _fail("SHAPE", "unknown requested shape")
        triggers = metadata.get("automatic_broad_triggers")
        if not isinstance(triggers, list) or any(not isinstance(trigger, str) or trigger not in TRIGGERS for trigger in triggers):
            _fail("TRIGGER", "triggers must be an exact subset of the frozen six")
        if metadata.get("effective_shape") != ("broad" if triggers else "narrow"):
            _fail("SHAPE", "effective shape must be broad exactly when triggers exist")
        if metadata.get("implementation_scope_widened") is not False:
            _fail("SCOPE", "implementation_scope_widened must be false")
        _required_string(metadata, "caller_authorized_implementation_scope")
        for heading, content in packet.sections.items():
            stripped = content.strip()
            if not stripped:
                _fail("HEADING_BODY", f"empty body: {heading}")
            if stripped == "N/A" or (stripped.startswith("N/A") and not NA.fullmatch(stripped)):
                _fail("N_A", f"malformed N/A in {heading}")
            if PLACEHOLDER.search(stripped):
                _fail("PLACEHOLDER", f"placeholder in {heading}")
        # The assembly output retains the producer candidate table verbatim. Validate
        # those shared role/disposition/boundary facts again at consumer time.
        candidate_rows = [line for line in packet.sections[HEADINGS[13]].splitlines() if line.startswith("|")]
        seen_roles: set[str] = set()
        in_candidate_table = False
        for line in candidate_rows:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells and cells[0] == "Role":
                in_candidate_table = True
                continue
            if cells and cells[0] == "Operation":
                in_candidate_table = False
                continue
            if not in_candidate_table or len(cells) != 5 or cells[0] == "---":
                continue
            if cells[0] not in ROLES:
                _fail("ROLE", f"unknown candidate role {cells[0]}")
            if not cells[2] or not cells[3] or cells[4] not in {"yes", "no"}:
                _fail("STATUS", "candidate evidence, disposition, and boundary status are required")
            seen_roles.add(cells[0])
            if cells[3] not in DISPOSITIONS and not cells[3].startswith(("excluded — ", "deferred — ")):
                _fail("DISPOSITION", f"unsupported disposition {cells[3]}")
            if cells[3] == "unresolved" and cells[4] == "yes":
                _fail("BOUNDARY", "boundary-changing candidate is unresolved")
        if seen_roles != ROLES:
            _fail("ROLE", "all seven frozen roles require a candidate disposition")
        _validate_discovery_rows(packet.sections[HEADINGS[11]])
        producer = _verify_identity(metadata, "producer_report", root)
        _verify_identity(metadata, "fix_contract", root)
        _verify_receipt_verdict(metadata, "producer_readiness", "PRODUCER_READY", root, "PRODUCER_RECEIPT")
        _verify_receipt_verdict(metadata, "gate_a", "APPROVED_FOR_IMPLEMENTATION", root, "REVIEW")
        _verify_receipt_verdict(metadata, "pre_review", "APPROVED_FOR_IMPLEMENTATION", root, "REVIEW")
        # Require producer bytes to be a qualified Forward Impact report, not a bare receipt.
        if b"## Forward Impact Map" not in producer or b"qualification: qualifying" not in producer:
            _fail("PRODUCER_REPORT", "producer report is not a qualifying Forward Impact report")
        return []
    except ContractViolation as violation:
        return [violation]


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s|\Z)", text, flags=re.M | re.S)
    if not match or not match.group(1).strip():
        _fail("PRODUCER_REPORT", f"missing producer section {heading}")
    return match.group(1).strip()


def _map_block(report: str, name: str) -> str:
    map_body = _section(report, "Forward Impact Map")
    match = re.search(rf"^{re.escape(name)}:\s*(.*?)(?=^[a-z_]+:|\Z)", map_body, flags=re.M | re.S)
    if not match or not match.group(1).strip():
        _fail("PRODUCER_REPORT", f"missing Forward Impact Map field {name}")
    return match.group(1).strip()


def _rows_for_roles(candidates: str, roles: set[str]) -> str:
    rows = []
    for line in candidates.splitlines():
        if not line.startswith("|"):
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) == 5 and parts[0] in ROLES:
            rows.append((line, parts))
    selected = [line for line, parts in rows if parts[0] in roles]
    if not selected:
        _fail("ROLE", "required candidate roles are absent")
    for _, parts in rows:
        if not parts[2] or not parts[3] or parts[4] not in {"yes", "no"}:
            _fail("ROLE", "candidate row has invalid evidence, disposition, or boundary impact")
        disposition = parts[3]
        if disposition not in DISPOSITIONS and not disposition.startswith(("excluded — ", "deferred — ")):
            _fail("DISPOSITION", f"unsupported disposition {disposition}")
        if parts[4] == "yes" and disposition == "unresolved":
            _fail("BOUNDARY", "boundary-changing candidate is unresolved")
    return "\n".join(selected)


def _load_input(path: str, root: Path) -> tuple[dict[str, Any], bytes, str]:
    data, digest = _read_once(path, root)
    return _json_object(data, path), data, digest


def assemble_packet(inputs: Mapping[str, Any]) -> bytes:
    root = _root(inputs)
    report_bytes, report_digest = _read_once(str(inputs.get("producer_report", "")), root)
    report = report_bytes.decode("utf-8")
    if "## Forward Impact Map" not in report or "qualification: qualifying" not in report:
        _fail("PRODUCER_REPORT", "assemble requires a qualifying producer report")
    contract, _, contract_digest = _load_input(str(inputs.get("fix_contract", "")), root)
    cycle, _, _ = _load_input(str(inputs.get("fix_cycle", "")), root)
    paths = {name: str(inputs.get(name, "")) for name in ("producer_readiness", "gate_a", "pre_review")}
    receipt_digests = {name: _read_once(value, root)[1] for name, value in paths.items()}
    required_contract = ("ticket", "recurrence_class", "terminal_outcome", "first_visible_break", "counterexamples", "leverage_criterion")
    required_cycle = ("requested_shape", "caller_authorized_implementation_scope", "caller_routes", "discovery_receipts", "evidence_budget")
    for source, keys in ((contract, required_contract), (cycle, required_cycle)):
        for key in keys:
            if not isinstance(source.get(key), str) or not source[key].strip():
                _fail("SEMANTIC_INPUT", f"missing semantic input {key}")
    if not isinstance(cycle.get("automatic_broad_triggers"), list):
        _fail("SEMANTIC_INPUT", "missing semantic input automatic_broad_triggers")
    ticket = contract["ticket"]
    output = str(inputs.get("output", ""))
    match = PACKET_PATH.fullmatch(output)
    if not match or match.group("ticket") != ticket:
        _fail("PATH", "output path must match frozen grammar and fix-contract ticket")
    candidates = _map_block(report, "candidates")
    sections = {
        HEADINGS[0]: f"{contract['recurrence_class']}\n\nAuthorized terminal outcome: {contract['terminal_outcome']}",
        HEADINGS[1]: f"{contract['first_visible_break']}\n\nProducer observation: {_section(report, 'Observation')}",
        HEADINGS[2]: _map_block(report, "changed_surfaces"),
        HEADINGS[3]: _rows_for_roles(candidates, {"producer", "writer"}),
        HEADINGS[4]: _rows_for_roles(candidates, {"consumer", "validator", "translator", "terminalizer", "prompt_builder"}),
        HEADINGS[5]: cycle["caller_routes"],
        HEADINGS[6]: _map_block(report, "authority_variants") + "\n\n" + _section(report, "Asset Usage Receipt"),
        HEADINGS[7]: _map_block(report, "presence_shape_cases"),
        HEADINGS[8]: _map_block(report, "replay_history_behavior"),
        HEADINGS[9]: _map_block(report, "exception_retry_families") + "\n\n" + _section(report, "Retry / Poisoned Verdict"),
        HEADINGS[10]: _section(report, "Asset Usage Receipt"),
        HEADINGS[11]: _section(report, "Probe Ledger") + "\n\n" + cycle["discovery_receipts"],
        HEADINGS[12]: contract["counterexamples"],
        HEADINGS[13]: candidates + "\n\n" + cycle["discovery_receipts"],
        HEADINGS[14]: cycle["evidence_budget"] + "\n\n" + _map_block(report, "evidence_stop_condition"),
        HEADINGS[15]: f"Gate A: {paths['gate_a']}\n\nPer-fix pre-review: {paths['pre_review']}",
        HEADINGS[16]: contract["leverage_criterion"],
    }
    triggers = cycle["automatic_broad_triggers"]
    if not isinstance(triggers, list) or any(not isinstance(trigger, str) or trigger not in TRIGGERS for trigger in triggers):
        _fail("TRIGGER", "fix-cycle triggers are not frozen values")
    metadata = {
        "artifact_kind": ARTIFACT_KIND, "schema": SCHEMA, "ticket": ticket, "path": output,
        "revision": int(match.group("revision") or "1"), "requested_shape": cycle["requested_shape"],
        "effective_shape": "broad" if triggers else "narrow", "automatic_broad_triggers": triggers,
        "caller_authorized_implementation_scope": cycle["caller_authorized_implementation_scope"],
        "implementation_scope_widened": False,
        "producer_report": {"path": inputs["producer_report"], "sha256": report_digest},
        "fix_contract": {"path": inputs["fix_contract"], "sha256": contract_digest},
        "producer_readiness": {"path": paths["producer_readiness"], "sha256": receipt_digests["producer_readiness"]},
        "gate_a": {"path": paths["gate_a"], "sha256": receipt_digests["gate_a"]},
        "pre_review": {"path": paths["pre_review"], "sha256": receipt_digests["pre_review"]},
    }
    rendered = MARKER + "\n" + json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n\n" + "\n\n".join(f"## {heading}\n\n{sections[heading]}" for heading in HEADINGS) + "\n"
    packet = parse_packet_bytes(output, rendered.encode())
    errors = validate_packet(packet, {"repo_root": root})
    if errors:
        raise errors[0]
    return rendered.encode()


def validate_activation(context: Mapping[str, Any]) -> ActivationDecision:
    if context.get("qualification") == "non-qualifying":
        return ActivationDecision(True, "ordinary-non-qualifying-local-fix")
    if context.get("qualification") != "qualifying":
        _fail("QUALIFICATION", "qualification must be qualifying or non-qualifying")
    config = context.get("activation")
    if not isinstance(config, Mapping):
        _fail("ACTIVATION", "activation configuration is required")
    root = _root(context)
    readiness, gate = config.get("producer_readiness"), config.get("gate_a")
    if not _activation_receipt_matches(readiness, "PRODUCER_READY", root):
        return ActivationDecision(False, "missing-or-stale-producer-readiness-v2")
    if not _activation_receipt_matches(gate, "APPROVED_FOR_IMPLEMENTATION", root):
        return ActivationDecision(False, "absent-or-nonapproving-gate-a-v2")
    return ActivationDecision(True, "dual-receipt-gate-satisfied")


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    assemble = commands.add_parser("assemble")
    for option in ("producer-report", "fix-contract", "fix-cycle", "producer-readiness", "gate-a", "pre-review", "output"):
        assemble.add_argument(f"--{option}", required=True)
    assemble.add_argument("--repo-root", default="")
    validate = commands.add_parser("validate")
    validate.add_argument("--packet", required=True); validate.add_argument("--expected-packet-sha256", required=True)
    validate.add_argument("--for-implementation", action="store_true"); validate.add_argument("--repo-root", default="")
    activation = commands.add_parser("activation-check")
    activation.add_argument("--qualification", required=True, choices=("qualifying", "non-qualifying")); activation.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "assemble":
            payload = {key.replace("_", "-").replace("-", "_"): value for key, value in vars(args).items()}
            root = _root({"repo_root": args.repo_root} if args.repo_root else None)
            output = _safe_path(args.output, root, require_exists=False)
            if output.exists():
                _fail("CREATE", f"output already exists: {args.output}")
            data = assemble_packet(payload)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as handle: handle.write(data)
            persisted = output.read_bytes()
            if persisted != data: _fail("PERSIST", "read-back bytes differ")
            errors = validate_packet(parse_packet_bytes(args.output, persisted), {"repo_root": root})
            if errors: raise errors[0]
            print(sha256_bytes(persisted)); return 0
        if args.command == "validate":
            root = _root({"repo_root": args.repo_root} if args.repo_root else None); data, digest = _read_once(args.packet, root)
            if digest != args.expected_packet_sha256: _fail("DIGEST", "Packet digest does not match retained bytes")
            errors = validate_packet(parse_packet_bytes(args.packet, data), {"repo_root": root})
            if errors: raise errors[0]
            print("valid"); return 0
        config = _json_object(Path(args.config).read_bytes(), args.config)
        decision = validate_activation({"qualification": args.qualification, "activation": config})
        print(json.dumps({"allowed": decision.allowed, "reason": decision.reason}, sort_keys=True))
        return 0
    except ContractViolation as exc:
        print(exc, file=sys.stderr); return 1
    except OSError as exc:
        print(f"I/O: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
