"""Shared Phase 3 test fixtures: a live server on an ephemeral port."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from jswarm.portal import server as server_mod
from jswarm import uat_feedback
from jswarm.uat_round_materialize import (
    build_canonical_package,
    render_canonical_package_block_bytes,
    render_normalized_package_annex_bytes,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "schemas" / "fix-decisions" / "examples"
# The exemplar fix contract is ticket ; the fixture repo tree mirrors
# that ticket key so publication-load checks (settings/contract/receipt ticket
# binding, HIGH 2) hold truthfully.
TICKET = ""


def make_publication(root: Path, publication_id: str = "pub-test-001", contract_name: str = "contract.json", manifest_digest=None):
    """Create repo_root/contract.json + receipts dir + a manifest; return fixture dict.

    ``manifest_digest`` may be overridden to simulate drift or tampering.
    """
    contract_src = EXAMPLES / "fix-contract.has-617-preview-stage-truth.json"
    root.mkdir(parents=True, exist_ok=True)
    contract_path = root / contract_name
    shutil.copy(contract_src, contract_path)
    receipt_dir = root / ".jswarm/plans" / TICKET / "decision-reviews" / publication_id / "receipts"
    # Phase 4: every publication is bound to a real ticket-local fix-settings
    # file (owner-authorized by default); the manifest digest is its hash.
    settings_rel = f".jswarm/plans/{TICKET}/.fix-settings.yaml"
    settings_path = root / settings_rel
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_payload = (
        "# fixture settings\n"
        "schema: jswarm.fix-settings/v1\n"
        f"ticket: {TICKET}\n"
        "gates:\n"
        "  defect_contract: owner-authorized\n"
        "  fix_contract: owner-authorized\n"
    ).encode("utf-8")
    settings_path.write_bytes(settings_payload)
    settings_digest = server_mod.sha256_bytes(settings_payload)
    digest = manifest_digest if manifest_digest is not None else server_mod.sha256_bytes(contract_path.read_bytes())
    manifest = {
        "schema": "jswarm.fix-decisions.publication-manifest/v1",
        "schema_version": "1.0",
        "publication_id": publication_id,
        "contract_path": contract_name,
        "contract_sha256": digest,
        "contract_schema": "jswarm.fix-decisions.fix-contract/v1",
        "contract_version": "v2",
        "published_at_utc": "2026-08-27T00:00:00Z",
        "gate_kind": "fix-contract-digest",
        "receipt_directory": f".jswarm/plans/{TICKET}/decision-reviews/{publication_id}/receipts",
        "allowed_repo_root": str(root),
        "fix_settings_path": settings_rel,
        "fix_settings_sha256": settings_digest,
        "publication_kind": "gate",
    }
    contract = json.loads(contract_path.read_text())
    # Plant the parent defect contract where the fix contract declares it, so
    # the load-time parent cross-chain (round-2 HIGH 3) validates truthfully.
    parent_rel = contract["parent_defect_contract"]["path"]
    parent_dst = root / parent_rel
    parent_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLES / "defect-contract.has-617-charter.json", parent_dst)
    return {
        "root": root,
        "contract_path": contract_path,
        "contract": contract,
        "receipt_dir": receipt_dir,
        "manifest": manifest,
        "expected_vocabulary": server_mod.substitute_vocab_placeholder(
            contract["required_approval_vocabulary"], digest
        ),
    }


_UAT_MANIFEST = REPO_ROOT / "jswarm/tests/fixtures/com376_phase3_package/healthy-manifest.json"
_COM391_V2_PREPARE_REQUEST = (
    REPO_ROOT / ".jswarm/plans/DEMO-391/DEMO-391.uat-prepare-request.step-v2.json"
)
_COM391_SCENARIOS = REPO_ROOT / ".jswarm/plans/DEMO-391/DEMO-391.uat-scenarios.json"


def com391_canonical_walkthroughs() -> dict[str, dict[str, Any]]:
    """Return canonical scenario titles and owner-facing walkthrough steps."""
    document = json.loads(_COM391_SCENARIOS.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    scenarios = document.get("scenarios")
    assert isinstance(scenarios, list)
    selected = {}
    for scenario in scenarios:
        if not isinstance(scenario, dict) or scenario.get("id") not in {
            "UAT-391-1", "UAT-391-2", "UAT-391-3", "UAT-391-4",
        }:
            continue
        walkthrough = scenario.get("walkthrough")
        assert isinstance(walkthrough, dict)
        user_steps = walkthrough.get("user_steps")
        assert isinstance(user_steps, list) and all(
            isinstance(step, str) and step.strip() for step in user_steps
        )
        selected[str(scenario["id"])] = {
            "title": scenario["title"],
            "user_steps": deepcopy(user_steps),
        }
    assert set(selected) == {"UAT-391-1", "UAT-391-2", "UAT-391-3", "UAT-391-4"}
    return selected


def _gwt_array_digest(given: list[str], when: list[str], then: list[str]) -> str:
    payload = json.dumps(
        {"given": given, "when": when, "then": then},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def make_com391_v2_manifest() -> dict[str, Any]:
    """Load DEMO-391 content and project only the clarified nested v2 shape."""
    request = json.loads(_COM391_V2_PREPARE_REQUEST.read_text(encoding="utf-8"))
    assert isinstance(request, dict)
    raw = request.get("canonical_manifest")
    assert isinstance(raw, dict)
    manifest = deepcopy(raw)
    assert manifest.get("schema_version") == "uat-canonical-package@2"
    assert manifest.get("ticket") == "DEMO-391"
    journeys = manifest.get("journeys")
    assert isinstance(journeys, list)
    for journey in journeys:
        assert isinstance(journey, dict)
        scenarios = journey.get("scenarios")
        steps = journey.get("steps")
        assert isinstance(scenarios, list) and isinstance(steps, list)
        ref_map: dict[str, tuple[str, str]] = {}
        for scenario in scenarios:
            assert isinstance(scenario, dict) and isinstance(scenario.get("scenario_id"), str)
            blocks = scenario.get("gwt")
            assert isinstance(blocks, list)
            for block in blocks:
                assert isinstance(block, dict)
                old_ref = str(block["gwt_ref"])
                given = block["given"] if isinstance(block["given"], list) else [block["given"]]
                when = block["when"] if isinstance(block["when"], list) else [block["when"]]
                then = block["then"] if isinstance(block["then"], list) else [block["then"]]
                digest = _gwt_array_digest(given, when, then)
                block.update({
                    "gwt_ref": digest,
                    "sha256": digest,
                    "given": given,
                    "when": when,
                    "then": then,
                })
                ref_map[old_ref] = (str(scenario["scenario_id"]), digest)
        for step in steps:
            assert isinstance(step, dict)
            old_scenarios = step.get("scenario_links")
            old_refs = step.pop("gwt_refs", None)
            if old_refs is None and isinstance(old_scenarios, list) and all(isinstance(link, dict) for link in old_scenarios):
                continue
            assert isinstance(old_scenarios, list) and isinstance(old_refs, list)
            nested = []
            for scenario_id in old_scenarios:
                refs = [new_ref for old_ref, (owner, new_ref) in ref_map.items() if owner == scenario_id and old_ref in old_refs]
                nested.append({"scenario_id": scenario_id, "gwt_refs": refs})
            step["scenario_links"] = nested
    return manifest


def make_com391_multi_lineage_v2_manifest() -> dict[str, Any]:
    """Add reusable multiple-scenario/multiple-GWT lineage to one DEMO-391 step."""
    manifest = make_com391_v2_manifest()
    journey = manifest["journeys"][0]
    first = journey["scenarios"][0]
    given = ["The round is open.", "The owner selected a journey."]
    when = ["The owner expands the linked source."]
    then = ["The source opens.", "Clause order is preserved.", "Unlinked blocks stay hidden."]
    second_ref = _gwt_array_digest(given, when, then)
    first["gwt"].append({
        "gwt_ref": second_ref, "sha256": second_ref,
        "given": given, "when": when, "then": then,
    })
    identical = deepcopy(first["gwt"][0])
    journey["scenarios"].append({
        "scenario_id": "UAT-391-1-ALT",
        "title": "Alternative scenario with identical GWT content",
        "gwt": [identical],
    })
    journey["steps"][0]["scenario_links"] = [
        {"scenario_id": first["scenario_id"], "gwt_refs": [
            first["gwt"][0]["gwt_ref"], second_ref,
        ]},
        {"scenario_id": "UAT-391-1-ALT", "gwt_refs": [identical["gwt_ref"]]},
    ]
    return manifest


def make_active_uat_round(root: Path, *, generated_at: str | None = None, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Materialize DEMO-391 inputs from the real package/feedback renderers.

    This deliberately does not hand-author a Markdown acceptance lookalike. Each
    call derives a canonical package, current-round package block, feedback
    document, and execution receipt from the existing UAT producer functions.
    """
    source_manifest = manifest or json.loads(_UAT_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(source_manifest, dict)
    package = build_canonical_package(source_manifest)
    materialized_at = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    round_dir = root / ".jswarm/plans/DEMO-391"
    round_dir.mkdir(parents=True, exist_ok=True)
    current_round_path = round_dir / "DEMO-391.UAT-CURRENT-ROUND.md"
    feedback_path = round_dir / "DEMO-391.uat-feedback.md"
    current_round = (
        render_canonical_package_block_bytes(package, package_state="ISSUED")
        + b"\n"
        + render_normalized_package_annex_bytes(package)
    )
    feedback = uat_feedback.render_feedback_document(package, generated_at=materialized_at)
    current_round_path.write_bytes(current_round)
    feedback_path.write_bytes(feedback)
    identity = {
        field: package[field]
        for field in (
            "round_id", "package_id", "package_hash", "sealed_payload_sha256",
            "script_id", "script_hash", "certified_build_hash",
        )
    }
    registration = {
        "schema": "jswarm.test-uat.active-round-source/v1",
        "schema_version": "1.0",
        "round_review_id": "DEMO-391/round-fixture-001",
        "ticket": package["ticket"],
        "allowed_repo_root": str(root),
        "current_round_path": str(current_round_path.relative_to(root)),
        "feedback_path": str(feedback_path.relative_to(root)),
        "package_identity": identity,
        "x_extension": {},
    }
    receipt = {
        "schema": "jswarm.test-uat.producer-fixture-receipt/v1",
        "schema_version": "1.0",
        "materialized_at_utc": materialized_at,
        "producer": {
            "canonical_package": "jswarm.uat_round_materialize.build_canonical_package",
            "current_round": "jswarm.uat_round_materialize.render_canonical_package_block_bytes",
            "feedback": "jswarm.uat_feedback.render_feedback_document",
        },
        "round_state": "ISSUED",
        "feedback_state": "UNPROCESSED",
        "package_identity": deepcopy(identity),
        "current_round_sha256": hashlib.sha256(current_round).hexdigest(),
        "feedback_sha256": hashlib.sha256(feedback).hexdigest(),
    }
    return {
        "package": package,
        "registration": registration,
        "receipt": receipt,
        "current_round_path": current_round_path,
        "feedback_path": feedback_path,
    }


class Client:
    """Minimal stdlib urllib JSON client."""

    def __init__(self, host: str, port: int, origin: str | None = None):
        self.base = f"http://{host}:{port}"
        self.origin = origin

    def request(self, method: str, path: str, body: dict | None = None, raw_body: bytes | None = None, headers: dict | None = None):
        import http.client
        import time
        import urllib.error
        import urllib.request

        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        req_headers = {"Content-Type": "application/json"}
        if self.origin:
            req_headers["Origin"] = self.origin
        req_headers.update(headers or {})
        # Transport-level retry only: under synchronized connect storms the
        # macOS loopback stack occasionally drops a connection during the
        # simultaneous close race — the client may see RemoteDisconnected,
        # ConnectionReset, or a broken pipe during request send. The service
        # is decision-once idempotent BY DESIGN, so a fresh-connection retry
        # is the contract-correct client behavior — receipt immutability
        # guarantees no duplicate durable state either way.
        last_error = None
        for attempt in range(4):
            req = urllib.request.Request(self.base + path, data=data, headers=req_headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    payload = resp.read()
                    return resp.status, json.loads(payload) if payload else {}
            except urllib.error.HTTPError as exc:
                # Always close the HTTPError (it wraps an open response
                # object); leaving it for GC leaks an unclosed socket.
                try:
                    payload = exc.read()
                    return exc.code, json.loads(payload)
                except json.JSONDecodeError:
                    return exc.code, {"raw": payload.decode(errors="replace")}
                finally:
                    exc.close()
            except (
                http.client.RemoteDisconnected,
                ConnectionResetError,
                BrokenPipeError,
            ) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(0.05 * (attempt + 1))
            except urllib.error.URLError as exc:
                # urllib wraps raw transport errors (e.g. broken pipe during
                # send) in URLError; retry only transport causes.
                if isinstance(exc.reason, OSError):
                    last_error = exc
                    if attempt < 3:
                        time.sleep(0.05 * (attempt + 1))
                    continue
                raise
        raise last_error

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body):
        return self.request("POST", path, body)


class LiveServer:
    """A live loopback ThreadingHTTPServer on an ephemeral port (port 0).

    The approved root doubles as the manifest-declared repo root: the fixture
    contract/receipts tree lives directly inside it (BLOCKER 1 requires every
    manifest-controlled path to resolve inside an approved root AND the
    declared allowed_repo_root to BE an approved root).
    """

    def __init__(self, tmp: Path, origin: str | None = None, body_limit: int | None = None,
                 dist_dir: Path | None = None, approved_root: Path | None = None,
                 contract_size_limit: int | None = None):
        self.approved_root = (approved_root or (tmp / "approved")).resolve()
        self.approved_root.mkdir(parents=True, exist_ok=True)
        self.publications_dir = self.approved_root / "publications"
        self.threads_dir = self.approved_root / "threads"
        self.publications_dir.mkdir(parents=True, exist_ok=True)
        self.threads_dir.mkdir(parents=True, exist_ok=True)
        kwargs = dict(
            publications_dir=self.publications_dir,
            threads_dir=self.threads_dir,
            approved_roots=[self.approved_root],
            dist_dir=dist_dir,
            port=0,
            bind_host="127.0.0.1",
        )
        if body_limit is not None:
            kwargs["body_limit"] = body_limit
        if contract_size_limit is not None:
            kwargs["contract_size_limit"] = contract_size_limit
        self.httpd = server_mod.serve(**kwargs)
        self.host, self.port = self.httpd.server_address[:2]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(self.host, self.port, origin=origin)
        self.service = self.httpd.service
        assert self.host == "127.0.0.1"

    def register(self, fixture: dict, manifest_name: str | None = None):
        manifest_path = self.publications_dir / (manifest_name or f"{fixture['manifest']['publication_id']}.json")
        manifest_path.write_text(json.dumps(fixture["manifest"], indent=2), encoding="utf-8")
        self.service.publications[fixture["manifest"]["publication_id"]] = server_mod.Publication(
            fixture["manifest"], manifest_path, self.approved_root
        )
        return fixture["manifest"]["publication_id"]

    def register_active_round(self, fixture: dict[str, Any]) -> str:
        self.service.register_active_round(fixture["registration"])
        return fixture["registration"]["round_review_id"]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def restart(self):
        """Stop and restart the HTTP server on a new ephemeral port, same service state."""
        self.close()
        self.httpd = server_mod.serve(
            publications_dir=self.publications_dir,
            threads_dir=self.threads_dir,
            approved_roots=[self.approved_root],
            port=0,
            publications=self.service.publications,
        )
        self.host, self.port = self.httpd.server_address[:2]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(self.host, self.port, origin=self.client.origin)


@pytest.fixture()
def live_server(tmp_path):
    server = LiveServer(tmp_path)
    yield server
    server.close()
