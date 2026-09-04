#!/usr/bin/env python3
"""Produce and verify DEMO-391 local-process certification receipts.

The ``verify`` operation deliberately rechecks live local evidence; a receipt's
``certification.verdict`` is never authority by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

_REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
if _REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, _REPOSITORY_ROOT)

SCHEMA = "jswarm.uat.local-process-certification/v1"
SOURCE = "local-process-v1"
TICKET = "DEMO-391"
IDENTITY_ALGORITHM = "sha256-path-manifest-v1"
CURRENT_SCRIPT = "CURRENT_SCRIPT_JQATESTER_WALK"
ROUND_REVIEW_ID = "DEMO-391/round-demo-391-001"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
TOP_LEVEL_KEYS = frozenset((
    "schema", "source", "producer", "ticket", "project_root", "started_at", "finished_at",
    "force_requested", "force_applied", "exit_code", "selected_instrument", "workflow_replay",
    "identity", "build", "fixture", "runtime", "served_bytes", "smoke", "certification", "privacy",
))
IDENTITY_KEYS = frozenset((
    "algorithm", "api_source_manifest_sha256", "ui_source_manifest_sha256", "fixture_source_manifest_sha256",
    "ui_dist_manifest_sha256", "fixture_state_sha256", "registration_sha256", "config_sha256",
    "current_round_sha256", "feedback_sha256",
))


class LocalCertificationError(ValueError):
    """A local-process receipt or its live evidence is not certified."""


_LISTENER_READINESS_TIMEOUT_SECONDS = 20.0
_LISTENER_READINESS_INTERVAL_SECONDS = 0.25


def wait_for_listener_readiness(
    *,
    children: Mapping[str, object],
    discover: Callable[[int], object],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    timeout_seconds: float = _LISTENER_READINESS_TIMEOUT_SECONDS,
    interval_seconds: float = _LISTENER_READINESS_INTERVAL_SECONDS,
) -> dict[str, object]:
    """Wait for both owned listeners, failing on an early child exit or deadline."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise LocalCertificationError("listener readiness timeout must be finite and positive")
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise LocalCertificationError("listener readiness interval must be finite and positive")

    deadline = monotonic() + timeout_seconds
    ports = {"api": 8765, "ui": 4321}
    observed: dict[str, object] = {}
    while True:
        for component in ports:
            poll = getattr(children.get(component), "poll", None)
            if not callable(poll):
                raise LocalCertificationError(f"component={component} child process is unavailable")
            exit_status = poll()
            if exit_status is not None:
                raise LocalCertificationError(f"component={component} exit_status={exit_status}")

        for component, port in ports.items():
            if component in observed:
                continue
            identity = discover(port)
            if identity is not None:
                if not isinstance(identity, Mapping):
                    raise LocalCertificationError(f"component={component} listener identity is malformed")
                observed[component] = dict(identity)
        if len(observed) == len(ports):
            return observed

        now = monotonic()
        if now >= deadline:
            missing = ",".join(component for component in ports if component not in observed)
            raise LocalCertificationError(f"missing-listener timeout components={missing}")
        sleep(min(interval_seconds, deadline - now))


def _expect_keys(value: object, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise LocalCertificationError(f"{label} has unsupported keys")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise LocalCertificationError(f"{label} must be a string")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LocalCertificationError(f"{label} must be an integer")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise LocalCertificationError(f"{label} must be a boolean")
    return value


def _digest(value: object, label: str) -> str:
    value = _string(value, label)
    if not SHA256_RE.fullmatch(value):
        raise LocalCertificationError(f"{label} must be lowercase sha256")
    return value


def _timestamp(value: object, label: str) -> str:
    value = _string(value, label)
    if not UTC_RE.fullmatch(value):
        raise LocalCertificationError(f"{label} must be RFC3339 UTC Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise LocalCertificationError(f"{label} is not a timestamp") from error
    return value


def _path(value: object, label: str) -> str:
    value = _string(value, label)
    if not Path(value).is_absolute():
        raise LocalCertificationError(f"{label} must be absolute")
    return value


def _sequence_of_strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise LocalCertificationError(f"{label} must be a non-empty string array")
    return value


def _validate_runtime(value: object, label: str, port: int) -> None:
    row = _expect_keys(value, frozenset(("pid", "ppid", "listener_port", "argv", "cwd", "started_at")), label)
    if _integer(row["pid"], f"{label}.pid") <= 0 or _integer(row["ppid"], f"{label}.ppid") <= 0:
        raise LocalCertificationError(f"{label} pid must be positive")
    if _integer(row["listener_port"], f"{label}.listener_port") != port:
        raise LocalCertificationError(f"{label} has wrong listener port")
    _sequence_of_strings(row["argv"], f"{label}.argv")
    _path(row["cwd"], f"{label}.cwd")
    _timestamp(row["started_at"], f"{label}.started_at")


def validate_receipt(receipt: Mapping[str, Any]) -> None:
    """Validate the closed typed receipt vocabulary without trusting live facts."""
    data = _expect_keys(receipt, TOP_LEVEL_KEYS, "receipt")
    if data["schema"] != SCHEMA or data["source"] != SOURCE or data["ticket"] != TICKET:
        raise LocalCertificationError("unsupported local certification source")
    _path(data["project_root"], "project_root")
    _timestamp(data["started_at"], "started_at"); _timestamp(data["finished_at"], "finished_at")
    producer = _expect_keys(data["producer"], frozenset(("path", "version")), "producer")
    if producer["path"] != "jswarm/uat_prepare_local_process.py" or _integer(producer["version"], "producer.version") != 1:
        raise LocalCertificationError("unsupported producer")
    if _boolean(data["force_requested"], "force_requested") or _boolean(data["force_applied"], "force_applied") or _integer(data["exit_code"], "exit_code") != 0:
        raise LocalCertificationError("receipt is forced or nonzero")
    if data["selected_instrument"] != CURRENT_SCRIPT:
        raise LocalCertificationError("local receipt requires current-script instrument")
    replay = _expect_keys(data["workflow_replay"], frozenset(("status", "reason")), "workflow_replay")
    if replay["status"] != "N/A" or replay["reason"] != "UAT-D4 selected the current-script jQATester walk":
        raise LocalCertificationError("invalid current-script replay disposition")
    identity = _expect_keys(data["identity"], IDENTITY_KEYS, "identity")
    if identity["algorithm"] != IDENTITY_ALGORITHM:
        raise LocalCertificationError("unsupported identity algorithm")
    for key in IDENTITY_KEYS - {"algorithm"}: _digest(identity[key], f"identity.{key}")
    build = _expect_keys(data["build"], frozenset(("command", "cwd", "started_at", "finished_at", "exit_code", "node_path", "dist_root")), "build")
    _sequence_of_strings(build["command"], "build.command"); _path(build["cwd"], "build.cwd")
    _timestamp(build["started_at"], "build.started_at"); _timestamp(build["finished_at"], "build.finished_at")
    if _integer(build["exit_code"], "build.exit_code") != 0: raise LocalCertificationError("build is nonzero")
    _path(build["node_path"], "build.node_path"); _path(build["dist_root"], "build.dist_root")
    fixture = _expect_keys(data["fixture"], frozenset(("state_path", "registration_path", "config_path", "root", "round_review_id", "round_state", "feedback_state", "verified_at")), "fixture")
    for key in ("state_path", "registration_path", "config_path", "root"): _path(fixture[key], f"fixture.{key}")
    if fixture["round_review_id"] != ROUND_REVIEW_ID or fixture["round_state"] != "ISSUED" or fixture["feedback_state"] != "UNPROCESSED":
        raise LocalCertificationError("fixture identity or lifecycle mismatch")
    _timestamp(fixture["verified_at"], "fixture.verified_at")
    runtime = _expect_keys(data["runtime"], frozenset(("api", "ui")), "runtime")
    _validate_runtime(runtime["api"], "runtime.api", 8765); _validate_runtime(runtime["ui"], "runtime.ui", 4321)
    served = _expect_keys(data["served_bytes"], frozenset(("ui_root_sha256", "ui_uat_sha256", "asset_manifest_sha256")), "served_bytes")
    for key in served: _digest(served[key], f"served_bytes.{key}")
    smoke = _expect_keys(data["smoke"], frozenset(("publications_status", "uat_list_status", "uat_detail_status", "ui_root_status", "ui_uat_status", "round_review_id", "view_schema", "current_round_writable", "feedback_writable")), "smoke")
    for key in ("publications_status", "uat_list_status", "uat_detail_status", "ui_root_status", "ui_uat_status"):
        if _integer(smoke[key], f"smoke.{key}") != 200: raise LocalCertificationError("smoke status is not 200")
    if smoke["round_review_id"] != ROUND_REVIEW_ID or smoke["view_schema"] != "jswarm.test-uat.active-round-view/v1" or _boolean(smoke["current_round_writable"], "smoke.current_round_writable") or not _boolean(smoke["feedback_writable"], "smoke.feedback_writable"):
        raise LocalCertificationError("smoke identity/capability mismatch")
    certification = _expect_keys(data["certification"], frozenset(("verdict", "reason", "hash")), "certification")
    if certification["verdict"] != "certified" or certification["reason"] is not None: raise LocalCertificationError("not certified")
    _digest(certification["hash"], "certification.hash")
    privacy = _expect_keys(data["privacy"], frozenset(("payload_bodies_persisted", "owner_feedback_logged", "secrets_persisted")), "privacy")
    if any(_boolean(privacy[key], f"privacy.{key}") for key in privacy): raise LocalCertificationError("privacy claim is unsafe")


def path_manifest_sha256(root: Path) -> str:
    """Return the deterministic sha256-path-manifest-v1 digest for a real tree."""
    if root.is_symlink() or not root.is_dir(): raise LocalCertificationError(f"manifest root is not a real directory: {root}")
    root = root.resolve(strict=True); rows: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink(): raise LocalCertificationError(f"manifest contains symlink: {path}")
        if path.is_file(): rows.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256("".join(f"{digest}  {relative}\n" for relative, digest in rows).encode()).hexdigest()


def _manifest_paths(root: Path, paths: Sequence[Path]) -> str:
    real_root = root.resolve(strict=True); rows: list[tuple[str, str]] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise LocalCertificationError(f"manifest member is not a regular file: {path}")
        real = path.resolve(strict=True)
        if real_root not in real.parents:
            raise LocalCertificationError(f"manifest member escapes project root: {path}")
        rows.append((real.relative_to(real_root).as_posix(), hashlib.sha256(real.read_bytes()).hexdigest()))
    if len({name for name, _digest_value in rows}) != len(rows):
        raise LocalCertificationError("duplicate manifest path")
    return hashlib.sha256("".join(f"{digest}  {relative}\n" for relative, digest in sorted(rows)).encode()).hexdigest()


def _regular_tree(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise LocalCertificationError(f"manifest root is not real: {root}")
    result: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink(): raise LocalCertificationError(f"manifest contains symlink: {path}")
        if path.is_file(): result.append(path)
    return result


def _identity_manifests(project_root: Path) -> dict[str, str]:
    api_root = project_root / "jswarm/fix_decisions"
    api = [path for path in _regular_tree(api_root) if "tests" not in path.parts and "__pycache__" not in path.parts]
    api.extend((project_root / "jswarm/uat_feedback.py", project_root / "jswarm/uat_round_materialize.py"))
    schema_root = project_root / "schemas/fix-decisions"
    api.extend(_regular_tree(schema_root) if schema_root.exists() else [])
    ui_root = project_root / "decision-review-ui"
    ui = [ui_root / name for name in ("package.json", "package-lock.json", "astro.config.mjs", "tailwind.config.mjs", "tsconfig.json")]
    public_root = ui_root / "public"
    ui.extend(_regular_tree(ui_root / "src")); ui.extend(_regular_tree(public_root) if public_root.exists() else [])
    fixture = [project_root / "jswarm/portal/tests/uat_round_live_fixture.py", project_root / "jswarm/tests/fixtures/uat_round_package/healthy-manifest.json", project_root / "jswarm/tests/fixtures/uat_round_feedback/healthy-ledger.md"]
    dist_root = ui_root / "dist"; dist = _regular_tree(dist_root)
    if not (dist_root / "index.html").is_file() or not (dist_root / "uat/index.html").is_file():
        raise LocalCertificationError("production dist lacks required pages")
    return {"api_source_manifest_sha256": _manifest_paths(project_root, api), "ui_source_manifest_sha256": _manifest_paths(project_root, ui), "fixture_source_manifest_sha256": _manifest_paths(project_root, fixture), "ui_dist_manifest_sha256": _manifest_paths(project_root, dist)}


def certification_hash(receipt: Mapping[str, Any]) -> str:
    identity = receipt["identity"]; build = receipt["build"]; fixture = receipt["fixture"]
    value = {"schema": "jswarm.uat.local-process-build-identity/v1", "identity": {key: identity[key] for key in sorted(IDENTITY_KEYS - {"algorithm"})}, "build": {key: build[key] for key in ("command", "node_path", "dist_root")}, "fixture": {key: fixture[key] for key in ("round_review_id", "round_state", "feedback_state")}}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _real_regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file(): raise LocalCertificationError(f"{label} is not a regular file")
    return path.resolve(strict=True)


def _no_redirect_opener() -> Any:
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None: raise LocalCertificationError("redirect rejected")
    return build_opener(_NoRedirect())


def _get(url: str) -> tuple[int, bytes, str]:
    parsed = urlparse(url)
    if parsed.hostname != "127.0.0.1": raise LocalCertificationError("non-loopback URL")
    try:
        with _no_redirect_opener().open(Request(url, method="GET"), timeout=5) as response:
            return response.status, response.read(), response.headers.get("Content-Encoding", "")
    except (HTTPError, URLError, OSError) as error: raise LocalCertificationError(f"GET failed: {parsed.path}") from error


def _verify_fixture(receipt: Mapping[str, Any]) -> None:
    fixture = receipt["fixture"]; state_path = Path(fixture["state_path"])
    try:
        from jswarm.portal.tests.uat_round_live_fixture import load_state, preflight_state
        state = preflight_state(load_state(state_path))
    except Exception as error:
        raise LocalCertificationError("fixture preflight failed") from error
    if state["round_review_id"] != ROUND_REVIEW_ID or state["lifecycle"] != {"round_state": "ISSUED", "feedback_state": "UNPROCESSED"}:
        raise LocalCertificationError("fixture state is not DEMO-391 fresh state")
    if Path(state["root"]).resolve() != Path(fixture["root"]).resolve(): raise LocalCertificationError("fixture root mismatch")
    config = json.loads(Path(fixture["config_path"]).read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("bind_host") != "127.0.0.1" or config.get("port") != 8765:
        raise LocalCertificationError("fixture config is not loopback API")
    current, feedback = Path(state["paths"]["current_round"]), Path(state["paths"]["feedback"])
    expected = receipt["identity"]
    for path, key in ((current, "current_round_sha256"), (feedback, "feedback_sha256")):
        _real_regular(path, key)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected[key]: raise LocalCertificationError(f"{key} mismatch")


def served_asset_manifest(
    dist_root: Path,
    documents: Mapping[str, bytes],
    get: Callable[[str], tuple[int, bytes, str | None]],
) -> dict[str, str]:
    """Bind only build-produced stylesheet/module assets served with the HTML documents."""
    if dist_root.is_symlink() or not dist_root.is_dir():
        raise LocalCertificationError("dist root is not real")
    root = dist_root.resolve(strict=True)
    targets: set[str] = set()

    def attribute(tag: str, name: str) -> str | None:
        match = re.search(rf"\b{name}=[\"']([^\"']+)[\"']", tag, flags=re.IGNORECASE)
        return match.group(1) if match else None

    for document in documents.values():
        try:
            html = document.decode("utf-8")
        except UnicodeDecodeError as error:
            raise LocalCertificationError("served document is not UTF-8") from error
        for tag in re.findall(r"<link\b[^>]*>", html, flags=re.IGNORECASE):
            rel, href = attribute(tag, "rel"), attribute(tag, "href")
            if href and rel and {part.casefold() for part in rel.split()}.intersection({"stylesheet", "modulepreload"}):
                targets.add(href)
        for tag in re.findall(r"<script\b[^>]*>", html, flags=re.IGNORECASE):
            script_type, source = attribute(tag, "type"), attribute(tag, "src")
            if source and script_type and script_type.casefold() == "module":
                targets.add(source)

    assets: dict[str, str] = {}
    for target in sorted(targets):
        parsed = urlparse(target)
        if parsed.scheme or parsed.netloc or target.startswith("//") or not target.startswith("/") or parsed.query or parsed.fragment:
            raise LocalCertificationError("referenced asset escapes dist")
        relative = Path(parsed.path.lstrip("/"))
        local = root / relative
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise LocalCertificationError("referenced asset escapes dist")
        try:
            resolved = local.resolve(strict=True)
        except FileNotFoundError as error:
            raise LocalCertificationError("referenced asset escapes dist") from error
        if root not in resolved.parents or not resolved.is_file():
            raise LocalCertificationError("referenced asset escapes dist")
        status, observed, encoding = get(target)
        if status != 200 or encoding or observed != resolved.read_bytes():
            raise LocalCertificationError("referenced asset mismatch")
        assets[target] = hashlib.sha256(observed).hexdigest()
    return assets


def _asset_manifest_digest(assets: Mapping[str, str]) -> str:
    encoded = "".join(f"{digest}  {path}\n" for path, digest in sorted(assets.items())).encode()
    return hashlib.sha256(encoded).hexdigest()


def _verify_served_bytes(receipt: Mapping[str, Any]) -> None:
    root = Path(receipt["build"]["dist_root"]); _real_regular(root / "index.html", "dist index"); _real_regular(root / "uat/index.html", "dist uat index")
    documents: dict[str, bytes] = {}
    for route, relative, expected in (("/", "index.html", "ui_root_sha256"), ("/uat/", "uat/index.html", "ui_uat_sha256")):
        status, body, encoding = _get("http://127.0.0.1:4321" + route)
        if status != 200 or encoding or hashlib.sha256(body).hexdigest() != hashlib.sha256((root / relative).read_bytes()).hexdigest() or hashlib.sha256(body).hexdigest() != receipt["served_bytes"][expected]: raise LocalCertificationError(f"served bytes mismatch: {route}")
        documents[route] = body
    assets = served_asset_manifest(root, documents, lambda path: _get("http://127.0.0.1:4321" + path))
    if _asset_manifest_digest(assets) != receipt["served_bytes"]["asset_manifest_sha256"]: raise LocalCertificationError("asset manifest mismatch")


def verify_receipt(receipt: Mapping[str, Any]) -> None:
    """Revalidate an issued receipt against its local filesystem and HTTP endpoints."""
    validate_receipt(receipt)
    root = Path(receipt["project_root"])
    if root.is_symlink() or not root.is_dir() or root.resolve() != root: raise LocalCertificationError("project root is not real")
    if receipt["certification"]["hash"] != certification_hash(receipt): raise LocalCertificationError("certification hash mismatch")
    fixture = receipt["fixture"]
    for key in ("state_path", "registration_path", "config_path"): _real_regular(Path(fixture[key]), f"fixture.{key}")
    for key, field in (("state_path", "fixture_state_sha256"), ("registration_path", "registration_sha256"), ("config_path", "config_sha256")):
        if hashlib.sha256(Path(fixture[key]).read_bytes()).hexdigest() != receipt["identity"][field]: raise LocalCertificationError(f"{key} digest mismatch")
    for key, digest in _identity_manifests(root).items():
        if receipt["identity"][key] != digest: raise LocalCertificationError(f"{key} mismatch")
    _verify_fixture(receipt)
    input_paths = [path for group in (root / "jswarm/fix_decisions", root / "portal/dist") for path in _regular_tree(group)]
    input_paths.extend(Path(fixture[key]) for key in ("state_path", "registration_path", "config_path"))
    input_mtimes = {str(path): datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z") for path in input_paths}
    validate_process_lineage(receipt, input_mtimes=input_mtimes)
    _verify_served_bytes(receipt)
    def api_get(path: str) -> tuple[int, object]:
        status, body, encoding = _get("http://127.0.0.1:8765" + path)
        if encoding: raise LocalCertificationError(f"API content encoding rejected: {path}")
        try: return status, json.loads(body)
        except json.JSONDecodeError as error: raise LocalCertificationError(f"API smoke is not JSON: {path}") from error
    smoke = smoke_api(get=api_get, expected_current_sha256=receipt["identity"]["current_round_sha256"], expected_feedback_sha256=receipt["identity"]["feedback_sha256"])
    for key, value in smoke.items():
        if receipt["smoke"].get(key) != value: raise LocalCertificationError(f"recorded smoke mismatch: {key}")


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=10)


def discover_loopback_listener(port: int, *, runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run) -> dict[str, object]:
    """Discover precisely one macOS loopback listener and bind it to ps identity."""
    result = runner(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    rows: list[int] = []
    for line in result.stdout.splitlines()[1:]:
        match = re.search(r"\s(\d+)\s+.*\sTCP\s+([^:]+):(\d+)\s+\(LISTEN\)$", line)
        if match is None:
            continue
        pid, host, observed_port = int(match.group(1)), match.group(2), int(match.group(3))
        if observed_port != port:
            continue
        if host != "127.0.0.1":
            raise LocalCertificationError("non-loopback listener")
        rows.append(pid)
    if not rows:
        raise LocalCertificationError("no listener")
    if len(set(rows)) != 1:
        raise LocalCertificationError("duplicate listener")
    pid = rows[0]
    process = runner(["ps", "-p", str(pid), "-o", "pid=,ppid=,lstart=,command="])
    lines = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        raise LocalCertificationError("process identity unavailable")
    first = lines[0].split(maxsplit=3)
    if len(first) != 4 or not first[0].isdigit() or not first[1].isdigit():
        raise LocalCertificationError("process identity malformed")
    # The deterministic test seam returns ISO-start/cwd on line one. Native macOS
    # ps returns lstart plus command; obtain cwd from lsof only in that native shape.
    if UTC_RE.fullmatch(first[2]):
        if len(lines) < 2:
            raise LocalCertificationError("process identity unavailable")
        started_at, cwd, argv_text = first[2], first[3], lines[1]
    else:
        native = re.match(r"([A-Z][a-z]{2} [A-Z][a-z]{2} +\d+ \d\d:\d\d:\d\d \d{4})\s+(.+)$", " ".join(first[2:]))
        if native is None: raise LocalCertificationError("native process start/argv unavailable")
        try: started_at = datetime.strptime(native.group(1), "%a %b %d %H:%M:%S %Y").astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError as error: raise LocalCertificationError("native process start malformed") from error
        cwd_result = runner(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
        cwd_lines = [line[1:] for line in cwd_result.stdout.splitlines() if line.startswith("n")]
        if len(cwd_lines) != 1: raise LocalCertificationError("process cwd unavailable")
        cwd, argv_text = cwd_lines[0], native.group(2)
    _timestamp(started_at, "process.started_at")
    if not Path(cwd).is_absolute(): raise LocalCertificationError("process cwd is not absolute")
    argv = shlex.split(argv_text)
    if not argv: raise LocalCertificationError("process argv missing")
    return {"pid": pid, "ppid": int(first[1]), "listener_port": port, "argv": argv, "cwd": cwd, "started_at": started_at}


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(_timestamp(value, "timestamp")[:-1] + "+00:00")


def validate_process_lineage(receipt: Mapping[str, Any], *, runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run, input_mtimes: Mapping[str, str] | None = None) -> None:
    """Reject PID reuse, changed process identity, or an input newer than a listener."""
    validate_receipt(receipt)
    latest_input = max((_parse_utc(value) for value in (input_mtimes or {}).values()), default=datetime.min.replace(tzinfo=timezone.utc))
    for name, port in (("api", 8765), ("ui", 4321)):
        observed = discover_loopback_listener(port, runner=runner)
        expected = receipt["runtime"][name]
        if any(observed[field] != expected[field] for field in ("pid", "listener_port", "argv", "cwd", "started_at")):
            raise LocalCertificationError(f"{name} listener identity changed")
        observed_ppid = observed["ppid"]
        if isinstance(observed_ppid, bool) or not isinstance(observed_ppid, int) or observed_ppid not in (expected["ppid"], 1):
            raise LocalCertificationError(f"{name} listener identity changed")
        observed_start = _parse_utc(str(observed["started_at"]))
        start_upper_bound = observed_start + timedelta(seconds=1) if "." not in str(observed["started_at"]) else observed_start
        if start_upper_bound <= latest_input:
            raise LocalCertificationError(f"{name} listener predates input")


def smoke_api(*, get: Callable[[str], tuple[int, object]], expected_current_sha256: str, expected_feedback_sha256: str) -> dict[str, object]:
    """Assert the exact DEMO-391 list/detail capabilities without logging payloads."""
    target = ROUND_REVIEW_ID
    for path in ("/api/publications", "/api/uat-rounds", "/api/uat-rounds/DEMO-391%2Fround-demo-391-001"):
        status, payload = get(path)
        if status != 200 or not isinstance(payload, dict):
            raise LocalCertificationError(f"API smoke failed: {path}")
        if path == "/api/uat-rounds":
            rows, warnings = payload.get("rounds"), payload.get("warnings")
            if not isinstance(rows, list) or not isinstance(warnings, list): raise LocalCertificationError("round list malformed")
            matches = [row for row in rows if isinstance(row, dict) and row.get("round_review_id") == target]
            target_warning = any(
                isinstance(warning, str) and target in warning and ("invalid" in warning or "skipped" in warning)
                for warning in warnings
            )
            if len(matches) != 1 or target_warning:
                raise LocalCertificationError("target registration missing, invalid, or duplicate")
        elif path.endswith("001"):
            current, feedback = payload.get("current_round"), payload.get("feedback")
            if (payload.get("schema") != "jswarm.test-uat.active-round-view/v1" or payload.get("ticket") != TICKET or payload.get("round_review_id") != target or not isinstance(current, dict) or not isinstance(feedback, dict) or current.get("writable") is not False or feedback.get("writable") is not True or current.get("sha256") != expected_current_sha256 or feedback.get("sha256") != expected_feedback_sha256):
                raise LocalCertificationError("target detail identity/capability mismatch")
    return {"publications_status": 200, "uat_list_status": 200, "uat_detail_status": 200, "round_review_id": target, "view_schema": "jswarm.test-uat.active-round-view/v1", "current_round_writable": False, "feedback_writable": True}


def smoke_with_ui_statuses(
    api_smoke: Mapping[str, object], *, ui_root_status: int, ui_uat_status: int
) -> dict[str, object]:
    """Complete the closed receipt smoke shape with real UI page status observations."""
    api_keys = frozenset(("publications_status", "uat_list_status", "uat_detail_status", "round_review_id", "view_schema", "current_round_writable", "feedback_writable"))
    if set(api_smoke) != api_keys:
        raise LocalCertificationError("API smoke has unsupported keys")
    for label, status in (("ui_root_status", ui_root_status), ("ui_uat_status", ui_uat_status)):
        if _integer(status, label) != 200:
            raise LocalCertificationError(f"{label} is not 200")
    return {**api_smoke, "ui_root_status": ui_root_status, "ui_uat_status": ui_uat_status}


def _atomic_receipt(destination: Path, value: Mapping[str, object]) -> None:
    if destination.exists() and destination.is_symlink(): raise LocalCertificationError("receipt destination is a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        if temporary.exists(): temporary.unlink()
        temporary.write_bytes(json.dumps(value, sort_keys=True).encode("utf-8") + b"\n")
        os.replace(temporary, destination)
    finally:
        if temporary.exists(): temporary.unlink()


def mint_receipt(*, destination: Path, fixture_preflight: Callable[[], object], production_build: Callable[[], object], start_api: Callable[[], object], start_ui: Callable[[], object], discover: Callable[[], object], smoke: Callable[[], object]) -> dict[str, object]:
    """Run the producer sequence and atomically emit only success-derived facts."""
    started: list[object] = []
    try:
        preflight = fixture_preflight()
        build = production_build()
        started.append(start_api())
        started.append(start_ui())
        listeners = discover()
        smoke_result = smoke()
        result: dict[str, object] = {"schema": SCHEMA, "source": SOURCE, "producer": {"path": "jswarm/uat_prepare_local_process.py", "version": 1}}
        for facts in (preflight, build, listeners, smoke_result):
            if isinstance(facts, Mapping): result.update(facts)
        _atomic_receipt(destination, result)
        return result
    except Exception:
        for process in reversed(started):
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                try: terminate()
                except Exception: pass
        if destination.exists(): destination.unlink()
        raise


def connected_harness() -> dict[str, object]:
    """Describe the later real-subprocess seam without claiming its canary ran."""
    return {"kind": "real-subprocess-harness", "claim": "not-run", "order": ["fixture-preflight", "production-build", "api-start", "ui-start", "mint", "verify"]}


def _default_dependencies(args: argparse.Namespace) -> Mapping[str, Callable[..., object]]:
    """Real subprocess dependencies for the CLI; tests replace this mapping only."""
    logs = args.log_dir; logs.mkdir(parents=True, exist_ok=True)
    root = args.project_root.resolve(strict=True)
    state_path, registration, config, fixture_root = (args.fixture_state, args.registration, args.config, args.fixture_root)
    def fixture_preflight(_parsed_args: argparse.Namespace) -> object:
        try:
            from jswarm.portal.tests.uat_round_live_fixture import load_state, preflight_state
            state = preflight_state(load_state(state_path))
            if Path(state["paths"]["registration"]).resolve() != registration.resolve() or Path(state["paths"]["config"]).resolve() != config.resolve() or Path(state["root"]).resolve() != fixture_root.resolve():
                raise LocalCertificationError("fixture CLI paths disagree with preflight state")
        except Exception as error:
            raise LocalCertificationError("fixture preflight failed") from error
        return {"fixture": {"state_path": str(state_path.resolve()), "registration_path": str(registration.resolve()), "config_path": str(config.resolve()), "root": str(fixture_root.resolve()), "round_review_id": state["round_review_id"], "round_state": state["lifecycle"]["round_state"], "feedback_state": state["lifecycle"]["feedback_state"], "verified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}, "_state": state}
    def production_build(_parsed_args: argparse.Namespace) -> object:
        npm = Path("/opt/example-user/.local/share/fnm/node-versions/v24.14.0/installation/bin/npm")
        command = [str(npm), "--prefix", "decision-review-ui", "run", "build"]
        started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with (logs / "build.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=120, check=False)
        if result.returncode != 0: raise LocalCertificationError("production build failed")
        return {"build": {"command": command, "cwd": str(root), "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "exit_code": 0, "node_path": str(npm.with_name("node")), "dist_root": str((root / "portal/dist").resolve())}}
    def start_api(_parsed_args: argparse.Namespace) -> object:
        log = (logs / "api.log").open("w", encoding="utf-8")
        return subprocess.Popen([str(root / ".venv/bin/python"), "-m", "jswarm.portal.server", "--config", str(config.resolve())], cwd=root, stdout=log, stderr=subprocess.STDOUT, text=True)
    def start_ui(_parsed_args: argparse.Namespace) -> object:
        log = (logs / "ui.log").open("w", encoding="utf-8")
        node = "/opt/example-user/.local/share/fnm/node-versions/v24.14.0/installation/bin/node"
        astro = root / "portal/node_modules/astro/astro.js"
        return subprocess.Popen([node, str(astro), "preview", "--host", "127.0.0.1", "--port", "4321"], cwd=root / "decision-review-ui", stdout=log, stderr=subprocess.STDOUT, text=True)
    def discover(_parsed_args: argparse.Namespace) -> object: return {"api": discover_loopback_listener(8765), "ui": discover_loopback_listener(4321)}
    def smoke(_parsed_args: argparse.Namespace) -> object:
        def api_get(path: str) -> tuple[int, object]:
            status, body, encoding = _get("http://127.0.0.1:8765" + path)
            if encoding: raise LocalCertificationError("API content encoding rejected")
            return status, json.loads(body)
        state = json.loads(state_path.read_text(encoding="utf-8")); digests = state["digests"]
        return {"smoke": smoke_api(get=api_get, expected_current_sha256=digests["current_round"], expected_feedback_sha256=digests["feedback"])}
    return {"fixture_preflight": fixture_preflight, "production_build": production_build, "start_api": start_api, "start_ui": start_ui, "discover": discover, "smoke": smoke}


def _complete_cli_receipt(args: argparse.Namespace, result: dict[str, object], state: Mapping[str, object]) -> None:
    """Fill the exact receipt only from observed filesystem, process, and HTTP facts."""
    root = args.project_root.resolve(strict=True); fixture = result["fixture"]
    paths = state["paths"]
    if not isinstance(paths, Mapping): raise LocalCertificationError("fixture paths malformed")
    current, feedback = Path(str(paths["current_round"])), Path(str(paths["feedback"]))
    identity = _identity_manifests(root)
    identity.update({"algorithm": IDENTITY_ALGORITHM, "fixture_state_sha256": hashlib.sha256(args.fixture_state.read_bytes()).hexdigest(), "registration_sha256": hashlib.sha256(args.registration.read_bytes()).hexdigest(), "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(), "current_round_sha256": hashlib.sha256(current.read_bytes()).hexdigest(), "feedback_sha256": hashlib.sha256(feedback.read_bytes()).hexdigest()})
    dist = root / "portal/dist"
    documents: dict[str, bytes] = {}
    served: dict[str, str] = {}
    ui_statuses: dict[str, int] = {}
    for route, relative, key in (("/", "index.html", "ui_root_sha256"), ("/uat/", "uat/index.html", "ui_uat_sha256")):
        status, body, encoding = _get("http://127.0.0.1:4321" + route)
        if status != 200 or encoding or body != (dist / relative).read_bytes(): raise LocalCertificationError(f"served bytes mismatch: {route}")
        served[key] = hashlib.sha256(body).hexdigest()
        documents[route] = body
        ui_statuses[route] = status
    served["asset_manifest_sha256"] = _asset_manifest_digest(
        served_asset_manifest(dist, documents, lambda path: _get("http://127.0.0.1:4321" + path))
    )
    api_smoke = result.get("smoke")
    if not isinstance(api_smoke, Mapping): raise LocalCertificationError("API smoke is unavailable")
    result["smoke"] = smoke_with_ui_statuses(
        api_smoke, ui_root_status=ui_statuses["/"], ui_uat_status=ui_statuses["/uat/"]
    )
    result.update({"ticket": TICKET, "project_root": str(root), "started_at": fixture["verified_at"], "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "force_requested": False, "force_applied": False, "exit_code": 0, "selected_instrument": CURRENT_SCRIPT, "workflow_replay": {"status": "N/A", "reason": "UAT-D4 selected the current-script jQATester walk"}, "identity": identity, "served_bytes": served, "privacy": {"payload_bodies_persisted": False, "owner_feedback_logged": False, "secrets_persisted": False}})
    result["certification"] = {"verdict": "certified", "reason": None, "hash": certification_hash(result)}


def mint_from_cli(args: argparse.Namespace, *, dependencies: Mapping[str, Callable[..., object]] | None = None) -> dict[str, object]:
    """Orchestrate real local certification, using discovered rather than supplied runtime facts."""
    real_dependencies = dependencies is None
    dependencies = _default_dependencies(args) if dependencies is None else dependencies
    started: list[object] = []
    try:
        preflight = dependencies["fixture_preflight"](args)
        build = dependencies["production_build"](args)
        api_child = dependencies["start_api"](args)
        started.append(api_child)
        ui_child = dependencies["start_ui"](args)
        started.append(ui_child)

        waiter = dependencies.get("wait_for_listeners")
        if callable(waiter):
            runtime = waiter(args)
        elif real_dependencies:
            def discover_when_available(port: int) -> object:
                try:
                    return discover_loopback_listener(port)
                except LocalCertificationError as error:
                    if str(error) == "no listener":
                        return None
                    raise

            runtime = wait_for_listener_readiness(
                children={"api": api_child, "ui": ui_child},
                discover=discover_when_available,
            )
        else:
            runtime = dependencies["discover"](args)

        smoke = dependencies["smoke"](args)
        result: dict[str, object] = {"schema": SCHEMA, "source": SOURCE, "producer": {"path": "jswarm/uat_prepare_local_process.py", "version": 1}, "runtime": runtime}
        for facts in (preflight, build, smoke):
            if isinstance(facts, Mapping): result.update(facts)
        state = result.pop("_state", None)
        if real_dependencies:
            if not isinstance(state, Mapping): raise LocalCertificationError("fixture state unavailable for receipt")
            _complete_cli_receipt(args, result, state)
        _atomic_receipt(args.receipt, result)
        return result
    except Exception:
        for process in reversed(started):
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                try: terminate()
                except Exception: pass
        if args.receipt.exists(): args.receipt.unlink()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(epilog="mint requires --receipt --project-root --fixture-state --registration --config --fixture-root --log-dir"); sub = parser.add_subparsers(dest="operation", required=True)
    verify = sub.add_parser("verify"); verify.add_argument("--receipt", required=True, type=Path)
    mint = sub.add_parser("mint"); mint.add_argument("--receipt", required=True, type=Path); mint.add_argument("--project-root", required=True, type=Path); mint.add_argument("--fixture-state", required=True, type=Path); mint.add_argument("--registration", required=True, type=Path); mint.add_argument("--config", required=True, type=Path); mint.add_argument("--fixture-root", required=True, type=Path); mint.add_argument("--log-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "verify":
            receipt = json.loads(args.receipt.read_text(encoding="utf-8")); verify_receipt(receipt)
            print(json.dumps({"verdict": "certified", "receipt": str(args.receipt)}, sort_keys=True)); return 0
        result = mint_from_cli(args)
        _atomic_receipt(args.receipt, result)
        return 0
    except Exception as error:
        print(json.dumps({"verdict": "not-certified", "reason": str(error)}, sort_keys=True), file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
