"""Phase 3 — stdlib HTTP receipt service.

Serves the review UI dist directory (if present) plus a narrow API:

- ``GET  /api/publications``              — list validated publication manifests
- ``GET  /api/publications/<id>``         — view model + manifest + digest + summaries
- ``POST /api/publications/<id>/actions`` — approve / deny / comment (digest-bound)
- ``POST /api/publications/<id>/auto-publish`` — SERVER-minted settings-authorized
  receipt for an informational publication whose gate mode is ``auto``
  (Phase 4 / SC-15); 403 for owner-authorized gates or gate-kind publications
- ``POST /api/publications/<id>/threads`` — create a Q&A thread (anchored, digest-bound)
- ``POST /api/threads/<thread_id>/messages`` — append a message to an anchored thread
- ``POST /api/threads/<thread_id>/status``   — open->answered->closed transitions

Every decision write is digest-bound under a single-snapshot model (BLOCKER 2):
the contract file is opened once, its bytes read once, the SHA-256 computed
over exactly those bytes, the vocabulary checked against those same bytes, and
the open descriptor re-fstat'ed immediately before the receipt is published —
any change (dev/ino/size/mtime_ns) aborts with a stale-review error and no
receipt. Anchored thread writes (message append, status transition) verify the
thread's recorded digest still matches the live contract digest. Receipts are
immutable (no-replace publish); threads are mutable-with-history
(temp+replace). Only paths that resolve inside a configured approved root are
ever touched (BLOCKER 1). The service binds all host interfaces by default so
the configured port is reachable through the host's LAN and tailnet names.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import logging
import os
import re
import signal
import stat as _stat
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import unquote, urlsplit

# Direct-file invocation bootstrap (`python jswarm/portal/server.py`):
# make the repo root importable before the package-relative imports below.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from jswarm.portal import cross_validate as cross_validate_mod
from jswarm.portal import form_events as form_events_mod
from jswarm.portal import receipts as receipts_mod
from jswarm.portal import settings as settings_mod
from jswarm.portal import validate as validate_mod
from jswarm.portal import view_model
from jswarm import uat_feedback
from jswarm import uat_round_materialize
from jswarm.paths import jswarm_home


def schema_schema_name(schema_key: str) -> str:
    return {"defect": "defect-contract schema", "fix": "fix-contract schema"}.get(schema_key, schema_key)

MANIFEST_SCHEMA_CONST = "jswarm.fix-decisions.publication-manifest/v1"
VOCAB_PLACEHOLDER = "<reported-sha256>"
DEFAULT_BODY_LIMIT = 1024 * 1024  # 1 MiB
DEFAULT_CONTRACT_LIMIT = 10 * 1024 * 1024  # 10 MiB
DEFAULT_PORT = 8765
DEFAULT_BIND_HOST = "0.0.0.0"
ACTIVE_ROUND_SOURCE_SCHEMA = "jswarm.test-uat.active-round-source/v1"
ACTIVE_ROUND_SOURCE_SCHEMA_V2 = "jswarm.test-uat.active-round-source/v2"
ACTIVE_ROUND_VIEW_SCHEMA = "jswarm.test-uat.active-round-view/v1"
ACTIVE_ROUND_VIEW_SCHEMA_V2 = "jswarm.test-uat.active-round-view/v2"
FEEDBACK_UPDATE_SCHEMA = "jswarm.test-uat.feedback-update/v1"
FEEDBACK_UPDATE_SCHEMA_V2 = "jswarm.test-uat.feedback-update/v2"
ROUND_STATUS_UPDATE_SCHEMA = "jswarm.test-uat.round-status-update/v1"
ROUND_STATUSES = frozenset({"NOT_STARTED", "IN_PROGRESS", "COMPLETE", "FAILED"})
WALK_STOP_FIELDS = frozenset({"state", "halting_step_id", "unreached_step_ids", "trigger", "recorded_by", "note"})
ACTIVE_ROUND_ID_RE = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTITY_FIELDS = (
    "round_id", "package_id", "package_hash", "sealed_payload_sha256",
    "script_id", "script_hash", "certified_build_hash",
)
THREAD_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
STATUS_ORDER = {"open": 0, "answered": 1, "closed": 2}
# SC-15: gate_kind -> the settings file's gates key whose mode authorizes it.
GATE_KIND_TO_SETTINGS_KEY = {
    "defect-contract-scope": "defect_contract",
    "fix-contract-digest": "fix_contract",
}
# Phase 4 config file: allowed keys and their value check (fail closed on
# unknown keys — a config typo must never silently drop a setting).
CONFIG_KEYS = {
    "approved_roots": lambda v: isinstance(v, list) and v and all(isinstance(i, str) and i for i in v),
    "publications_dir": lambda v: isinstance(v, str) and bool(v),
    "service_store_root": lambda v: isinstance(v, str) and bool(v),
    "threads_dir": lambda v: isinstance(v, str) and bool(v),
    "dist_dir": lambda v: isinstance(v, str) and bool(v),
    "origin_allowlist": lambda v: isinstance(v, list) and v and all(isinstance(i, str) and i for i in v),
    "port": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 65535,
    "bind_host": lambda v: isinstance(v, str) and bool(v),
    "body_limit": lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
    "contract_size_limit": lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
    "active_round_sources": lambda v: isinstance(v, list) and all(isinstance(item, dict) for item in v),
}


class ConfigError(Exception):
    """The decision-review config file is malformed (fail closed)."""


def load_config(config_path: Path, cli_overrides: dict | None = None) -> dict:
    """Load a decision-review config JSON file and merge CLI overrides on top.

    Precedence: CLI overrides > config file. List-valued overrides REPLACE the
    file value (no concatenation). Unknown keys or invalid values raise
    :class:`ConfigError` — never silently ignored.
    """
    try:
        document = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise ConfigError(f"config file unreadable: {config_path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ConfigError(f"config file must be a JSON object: {config_path}")
    unknown = sorted(set(document) - set(CONFIG_KEYS))
    if unknown:
        raise ConfigError(f"unknown config keys: {unknown} (allowed: {sorted(CONFIG_KEYS)})")
    merged: dict = {}
    for key, value in document.items():
        if not CONFIG_KEYS[key](value):
            raise ConfigError(f"invalid value for config key {key!r}: {value!r}")
        merged[key] = value
    for key, value in (cli_overrides or {}).items():
        if key not in CONFIG_KEYS:
            raise ConfigError(f"unknown override key: {key!r}")
        if value is None:
            continue  # flag not given on the CLI
        if not CONFIG_KEYS[key](value):
            raise ConfigError(f"invalid override value for {key!r}: {value!r}")
        merged[key] = value
    merged.setdefault(
        "service_store_root",
        str(jswarm_home() / ".jswarm" / "decision-review"),
    )
    return merged

log = logging.getLogger("fix_decisions.server")


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, **detail):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def parse_web_origin(value: str) -> tuple[str, str, int] | None:
    """Return a normalized HTTP(S) origin, rejecting non-origin URL forms."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        return None
    return parsed.scheme.lower(), parsed.hostname.lower(), port if port is not None else (80 if parsed.scheme == "http" else 443)


def parse_host_header(value: str | None) -> tuple[str, int] | None:
    """Return a normalized Host header authority without accepting URL syntax."""
    if not value or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
    ):
        return None
    return parsed.hostname.lower(), port if port is not None else 80


# ---------------------------------------------------------------------------
# Path containment (BLOCKER 1)


def safe_relative(path_str: str, what: str) -> str:
    """Reject absolute paths and any '..' segment in a manifest-controlled
    relative path (defense in depth; schema-level patterns cannot be edited
    from this lane — schemas/ is read-only)."""
    if not isinstance(path_str, str) or not path_str:
        raise ServiceError(422, "unsafe_path", f"{what} must be a non-empty relative path")
    if path_str.startswith("/") or os.path.isabs(path_str):
        raise ServiceError(422, "unsafe_path", f"{what} must not be absolute: {path_str!r}")
    parts = path_str.replace("\\", "/").split("/")
    if ".." in parts:
        raise ServiceError(422, "unsafe_path", f"{what} must not contain '..' segments: {path_str!r}")
    return path_str


def write_all(fd: int, payload: bytes) -> None:
    """Write every byte or fail before the staging file can be replaced."""
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("incomplete staging write")
        offset += written


def contained_path(base: Path, relative: str, approved_root: Path, what: str) -> Path:
    """Join base+relative and require the RESOLVED result to stay inside the
    approved root (blocks symlink escapes and sibling-prefix confusion)."""
    candidate = (base / relative).resolve()
    if not candidate.is_relative_to(approved_root):
        raise ServiceError(422, "unsafe_path", f"{what} escapes the approved root: {relative!r}")
    return candidate


def open_dir_components(root_fd: int, components: list[str], create: bool = False) -> int:
    """Open a directory by walking components from an open root descriptor,
    refusing symlinks at EVERY step (O_NOFOLLOW), with no use of paths.

    Round 3 R1: containment established once at Publication construction is
    not enough — a component can be swapped for a symlink afterwards. This
    walk re-establishes descriptor identity at the point of use; the returned
    fd is the only handle callers use for writes. With ``create=True``, missing
    components are mkdir'd relative to the parent descriptor.
    """
    fd = root_fd
    opened = []
    success = False
    try:
        for component in components:
            if component in ("", ".", ".."):
                raise ServiceError(422, "unsafe_path", f"refusing path component: {component!r}")
            try:
                next_fd = os.open(component, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except FileExistsError as error:
                raise ServiceError(422, "path_containment", f"{component!r} is a symlink; refusing to follow") from error
            except OSError as error:
                # macOS reports ELOOP for O_NOFOLLOW-on-symlink; Linux uses
                # ELOOP too (O_NOFOLLOW is unspecified there, EEXIST is the
                # legacy expectation). Treat both as a refused symlink.
                if error.errno == errno.ELOOP:
                    raise ServiceError(422, "path_containment", f"{component!r} is a symlink; refusing to follow") from error
                if isinstance(error, FileNotFoundError) and create:
                    try:
                        os.mkdir(component, 0o755, dir_fd=fd)
                    except FileExistsError:
                        pass  # concurrent creator won the race; continue below
                    next_fd = os.open(component, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                elif isinstance(error, FileNotFoundError):
                    raise ServiceError(404, "contract_missing", f"missing directory component: {component!r}") from error
                else:
                    raise
            # Verify each component is a directory, not a file/O_NOFOLLOW leak.
            if not _stat.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                raise ServiceError(422, "path_containment", f"{component!r} is not a directory")
            opened.append(next_fd)
            os.close(fd)  # closes the parent (root_fd on the first step)
            fd = next_fd
        success = True
        return fd
    finally:
        # Ownership: the caller-supplied root_fd is CONSUMED by this walk —
        # closed on failure (round 4 fd-leak fix) and on success once the walk
        # has moved past it. Only the single returned fd stays open.
        if not success:
            for f in opened:
                try:
                    os.close(f)
                except OSError:
                    pass
            try:
                os.close(root_fd)
            except OSError:
                pass
        elif fd != root_fd:
            try:
                os.close(root_fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Publication registry


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def substitute_vocab_placeholder(vocabulary: str, digest: str) -> str:
    return vocabulary.replace(VOCAB_PLACEHOLDER, digest)


class ContractSnapshot:
    """Single-snapshot contract read (BLOCKER 2).

    The file is opened once and its bytes read once. Every decision the
    service makes (digest comparison, vocabulary check, receipt identity
    fields) is computed from THOSE bytes. The descriptor is retained so the
    publish step can re-stat and refuse if anything about the file changed
    since the read.
    """

    def __init__(self, path: Path, size_limit: int):
        self.path = path
        try:
            self._fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError as exc:
            raise ServiceError(404, "contract_missing", f"contract not found: {path}") from exc
        try:
            st = os.fstat(self._fd)
            if st.st_size > size_limit:
                os.close(self._fd)
                self._fd = None
                raise ServiceError(
                    413, "contract_too_large",
                    f"contract file is {st.st_size} bytes; limit is {size_limit}",
                )
            chunks = []
            while True:
                chunk = os.read(self._fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
        except BaseException:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            raise
        self.bytes = b"".join(chunks)
        self._stat_at_read = st
        self.digest = sha256_bytes(self.bytes)

    def document(self) -> dict:
        try:
            return json.loads(self.bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ServiceError(409, "contract_unreadable", f"contract is not valid JSON: {exc}")

    def unchanged_since_read(self) -> bool:
        """True when the open file still matches the stat taken at read time
        (same inode, size, mtime). Any drift -> the snapshot is stale."""
        if self._fd is None:
            return False
        st = os.fstat(self._fd)
        read_stat = self._stat_at_read
        return (
            st.st_dev == read_stat.st_dev
            and st.st_ino == read_stat.st_ino
            and st.st_size == read_stat.st_size
            and st.st_mtime_ns == read_stat.st_mtime_ns
        )

    def still_current_at_path(self, parent_fd: int, filename: str) -> bool:
        """Round 3 R2: an atomic rename-swap of the pathname defeats a pure
        fd-stat check (the retained fd still sees the OLD inode while the path
        now holds different bytes). Re-resolve the CURRENT identity through a
        contained parent descriptor: open the filename with O_NOFOLLOW and
        compare (dev, ino) AND the re-hashed bytes against the snapshot.
        Correctness first — re-hashing ≤10 MiB is cheap."""
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except (FileNotFoundError, FileExistsError, NotADirectoryError, OSError):
            return False  # path gone, became a symlink, or is unreadable
        try:
            st = os.fstat(fd)
            read_stat = self._stat_at_read
            if st.st_dev != read_stat.st_dev or st.st_ino != read_stat.st_ino:
                return False  # the pathname now names a different inode
            chunks = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return sha256_bytes(b"".join(chunks)) == self.digest
        finally:
            os.close(fd)

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class RoundFileSnapshot:
    """A bounded, descriptor-anchored snapshot of one registered UAT artifact."""

    def __init__(self, root: Path, components: tuple[str, ...], size_limit: int):
        self.components = components
        self.parent_fd = open_dir_components(os.open(root, os.O_RDONLY), list(components[:-1]))
        try:
            self._fd = os.open(
                components[-1],
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=self.parent_fd,
            )
        except OSError as exc:
            os.close(self.parent_fd)
            if getattr(exc, "errno", None) == errno.ELOOP or isinstance(exc, FileExistsError):
                raise ServiceError(422, "path_containment", "registered source is symlinked; refusing to follow") from exc
            raise ServiceError(422, "source_invalid", "registered source cannot be opened") from exc
        try:
            self._stat_at_read = os.fstat(self._fd)
            if not _stat.S_ISREG(self._stat_at_read.st_mode):
                raise ServiceError(422, "source_invalid", "registered source must be a regular file")
            if self._stat_at_read.st_size > size_limit:
                raise ServiceError(413, "source_too_large", "registered source exceeds service size limit")
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(self._fd, min(65536, size_limit - total + 1)):
                total += len(chunk)
                if total > size_limit:
                    raise ServiceError(413, "source_too_large", "registered source grew beyond service size limit")
                chunks.append(chunk)
            self.bytes = b"".join(chunks)
            self.digest = sha256_bytes(self.bytes)
        except BaseException:
            self.close()
            raise

    def unchanged_since_read(self) -> bool:
        try:
            current = os.fstat(self._fd)
        except OSError:
            return False
        original = self._stat_at_read
        return (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) == (
            original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns,
        )

    def still_current_at_path(self) -> bool:
        try:
            fd = os.open(self.components[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.parent_fd)
        except OSError:
            return False
        try:
            current = os.fstat(fd)
            original = self._stat_at_read
            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                return False
            chunks: list[bytes] = []
            while chunk := os.read(fd, 65536):
                chunks.append(chunk)
            return sha256_bytes(b"".join(chunks)) == self.digest
        finally:
            os.close(fd)

    def close(self) -> None:
        for name in ("_fd", "parent_fd"):
            fd = getattr(self, name, None)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, name, None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class ActiveRoundSource:
    """Validated registration assertions; source bytes stay server-derived."""

    def __init__(self, document: dict, root: Path):
        self.document = document
        self.root = root
        self.round_review_id = document["round_review_id"]
        self.current_components = tuple(document["current_round_path"].replace("\\", "/").split("/"))
        self.feedback_components = tuple(document["feedback_path"].replace("\\", "/").split("/"))
        self.fixture = document.get("x_extension", {}).get("fixture")

    @property
    def is_practice_fixture(self) -> bool:
        return self.fixture is not None

    def snapshot_current(self, limit: int) -> RoundFileSnapshot:
        return RoundFileSnapshot(self.root, self.current_components, limit)

    def snapshot_feedback(self, limit: int) -> RoundFileSnapshot:
        return RoundFileSnapshot(self.root, self.feedback_components, limit)


def parse_active_round_source(
    document: dict,
    approved_roots: list[Path] | None = None,
    size_limit: int = DEFAULT_CONTRACT_LIMIT,
) -> ActiveRoundSource:
    """Validate an active-round registration without granting it filesystem authority.

    ``approved_roots`` remains tolerated for call compatibility, but deliberately
    does not authorize active rounds. It still protects publication and thread
    handling elsewhere in the service. An active registration confines every
    artifact to its own resolved ``allowed_repo_root`` instead.
    """
    required = {
        "schema", "schema_version", "round_review_id", "ticket", "allowed_repo_root",
        "current_round_path", "feedback_path", "package_identity",
    }
    if not isinstance(document, dict) or not required.issubset(document) or set(document) - required - {"x_extension"}:
        raise ServiceError(422, "source_invalid", "active round registration has unsupported fields")
    if (document["schema"], document["schema_version"]) not in {
        (ACTIVE_ROUND_SOURCE_SCHEMA, "1.0"),
        (ACTIVE_ROUND_SOURCE_SCHEMA_V2, "2.0"),
    }:
        raise ServiceError(422, "source_invalid", "unsupported active round registration schema/version")
    if not all(isinstance(document[key], str) and document[key] for key in ("round_review_id", "ticket", "allowed_repo_root")):
        raise ServiceError(422, "source_invalid", "active round registration identity is invalid")
    if not ACTIVE_ROUND_ID_RE.fullmatch(document["round_review_id"]):
        raise ServiceError(422, "source_invalid", "round_review_id is invalid")
    claimed_root = Path(document["allowed_repo_root"]).resolve()
    for key in ("current_round_path", "feedback_path"):
        relative = safe_relative(document[key], key)
        contained_path(claimed_root, relative, claimed_root, key)
    identity = document["package_identity"]
    if not isinstance(identity, dict) or set(identity) != set(IDENTITY_FIELDS):
        raise ServiceError(422, "source_invalid", "package_identity fields are invalid")
    if not all(isinstance(identity[key], str) and identity[key] for key in IDENTITY_FIELDS):
        raise ServiceError(422, "source_invalid", "package_identity values are invalid")
    if not all(SHA256_RE.fullmatch(identity[key]) for key in ("package_hash", "sealed_payload_sha256", "script_hash")):
        raise ServiceError(422, "source_invalid", "package_identity digests are invalid")
    extension = document.get("x_extension", {})
    if not isinstance(extension, dict):
        raise ServiceError(422, "source_invalid", "x_extension must be an object")
    fixture = extension.get("fixture")
    if fixture is not None:
        fixture_fields = {"purpose", "root", "reset_generated_at"}
        if (set(extension) != {"fixture"} or not isinstance(fixture, dict)
                or set(fixture) != fixture_fields
                or fixture.get("purpose") != "safe-practice-round"
                or fixture.get("root") != "service-owned"
                or not isinstance(fixture.get("reset_generated_at"), str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fixture["reset_generated_at"])):
            raise ServiceError(422, "source_invalid", "fixture contract is invalid")
    source = ActiveRoundSource(document, claimed_root)
    try:
        with source.snapshot_current(size_limit) as current, source.snapshot_feedback(size_limit) as feedback:
            current_package = uat_round_materialize.parse_canonical_round_projection(current.bytes)["normalized_package"]
            feedback_package = uat_feedback.parse_feedback_projection(feedback.bytes)["normalized_package"]
    except (ServiceError, ValueError, uat_round_materialize.ValidationError, uat_feedback.PackageValidationError) as exc:
        raise ServiceError(422, "source_invalid", "registered UAT source failed canonical validation") from exc
    if not isinstance(current_package, dict) or not isinstance(feedback_package, dict) or any(
        current_package.get(field) != identity[field] or feedback_package.get(field) != identity[field]
        for field in IDENTITY_FIELDS
    ):
        raise ServiceError(422, "source_invalid", "registration identity assertion does not match canonical artifacts")
    return source


class Publication:
    """A validated manifest plus its resolved, approved-root-contained paths."""

    def __init__(self, manifest: dict, manifest_path: Path, approved_root: Path, contract_size_limit: int = DEFAULT_CONTRACT_LIMIT):
        self.manifest = manifest
        self.manifest_path = manifest_path
        self.approved_root = Path(approved_root).resolve()
        self.contract_size_limit = contract_size_limit
        self.publication_id = manifest["publication_id"]
        contract_rel = safe_relative(manifest["contract_path"], "contract_path")
        receipts_rel = safe_relative(manifest["receipt_directory"], "receipt_directory")
        settings_rel = safe_relative(manifest["fix_settings_path"], "fix_settings_path")
        # Manifest-controlled paths must resolve INSIDE the approved root even
        # when the manifest declares a different (attacker-chosen) repo root.
        self.contract_path = contained_path(self.approved_root, contract_rel, self.approved_root, "contract_path")
        self.receipt_dir = contained_path(self.approved_root, receipts_rel, self.approved_root, "receipt_directory")
        self.settings_path = contained_path(self.approved_root, settings_rel, self.approved_root, "fix_settings_path")
        # Components for the point-of-use O_NOFOLLOW descriptor walks (R1/R2,
        # and round-2 HIGH 1 for the settings path).
        self._contract_components = contract_rel.replace("\\", "/").split("/")
        self._receipts_components = receipts_rel.replace("\\", "/").split("/")
        self._settings_components = settings_rel.replace("\\", "/").split("/")

    @property
    def contract_ticket(self) -> str:
        """The contract's ticket key, read from the live contract snapshot.
        Round-2 HIGH 2: settings/receipts binding is against THIS value, not
        a manifest-declared ticket."""
        with self.snapshot() as snap:
            return snap.document().get("ticket", "")

    def read_settings_bytes(self) -> tuple[bytes, tuple[int, int]]:
        """Round-2 HIGH 1: read the settings file through the SAME descriptor
        discipline as contracts — a no-symlink component walk from the
        approved root (open_dir_components), O_NOFOLLOW on every component,
        bytes read from the open descriptor. Returns (bytes, (st_dev, st_ino))
        for the immediate pre-persistence identity recheck. No cached resolved
        path survives: the walk happens at EVERY authorization point, so a
        symlink retargeted after load is either refused (symlink component)
        or read fresh (and then fails the digest check)."""
        components = self._settings_components
        parent_fd = open_dir_components(self.open_root_fd(), components[:-1])
        file_fd = None
        try:
            file_fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except (FileExistsError, FileNotFoundError, OSError) as exc:
            errno_code = getattr(exc, "errno", None)
            if isinstance(exc, FileExistsError) or errno_code == errno.ELOOP:
                raise ServiceError(
                    422, "path_settings_mismatch",
                    f"fix settings path has a symlinked final component; refusing to follow: {self.manifest['fix_settings_path']!r}",
                ) from exc
            if isinstance(exc, FileNotFoundError):
                raise ServiceError(
                    422, "path_settings_mismatch",
                    f"fix settings file is missing: {self.manifest['fix_settings_path']!r}",
                ) from exc
            raise
        finally:
            os.close(parent_fd)
        try:
            st = os.fstat(file_fd)
            chunks = []
            while True:
                chunk = os.read(file_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks), (st.st_dev, st.st_ino)
        finally:
            os.close(file_fd)

    def verify_settings(self) -> "settings_mod.GateSettings":
        """Phase 4 / SC-15 + round-2 HIGH 1/2: read the settings file through
        the descriptor walk, require the recomputed SHA-256 to equal the
        manifest's fix_settings_sha256, parse the gate subset, and bind the
        settings to THIS contract's ticket (settings.ticket equality + the
        literal settings-path and receipt-directory ticket patterns). Any
        failure raises ServiceError ``path_settings_mismatch`` (digest/parse)
        or ``settings_ticket_mismatch`` (cross-ticket) — a tampered or foreign
        settings file can never authorize anything."""
        rel = self.manifest["fix_settings_path"]
        try:
            payload, _identity = self.read_settings_bytes()
        except ServiceError:
            raise
        except OSError as exc:
            raise ServiceError(422, "path_settings_mismatch", f"fix settings unreadable: {exc}") from exc
        if sha256_bytes(payload) != self.manifest["fix_settings_sha256"]:
            raise ServiceError(
                422, "path_settings_mismatch",
                "fix settings digest does not match the publication manifest "
                "(tampered or rotated settings); this publication cannot act",
            )
        try:
            parsed = settings_mod.parse_gate_settings(payload.decode("utf-8"))
        except (settings_mod.SettingsError, UnicodeDecodeError) as exc:
            raise ServiceError(
                422, "path_settings_mismatch",
                f"fix settings file failed validation: {exc}",
            ) from exc
        contract_ticket = self.contract_ticket
        if parsed.ticket != contract_ticket:
            raise ServiceError(
                422, "settings_ticket_mismatch",
                f"fix settings ticket {parsed.ticket!r} does not match contract ticket {contract_ticket!r}",
            )
        # v1.1 is a common-owned immutable bundle: runtime settings and
        # receipts deliberately live under .jswarm/decision-review, while the
        # source paths remain recorded separately for audit. v1.0 retains its
        # ticket-local layout checks unchanged.
        if self.manifest.get("schema_version") == "1.0":
            if rel != f".jswarm/plans/{contract_ticket}/.fix-settings.yaml":
                raise ServiceError(422, "settings_ticket_mismatch", f"fix_settings_path must be exactly .jswarm/plans/<ticket>/.fix-settings.yaml for ticket {contract_ticket!r}; got {rel!r}")
            receipts_rel = self.manifest["receipt_directory"]
            if not receipts_rel.startswith(f".jswarm/plans/{contract_ticket}/"):
                raise ServiceError(422, "settings_ticket_mismatch", f"receipt_directory must live under the contract's ticket segment .jswarm/plans/{contract_ticket}/; got {receipts_rel!r}")
        return parsed

    def validate_coherence(self) -> None:
        """Round-2 HIGH 3: at publication LOAD (and re-checked at action
        time), the contract bytes must validate against their declared
        specialization schema, whose consts already pin document_type /
        gate_kind / intent — and the manifest's gate_kind must equal the
        contract's gate_kind. A relabeled or corrupt contract is refused with
        ``gate_contract_mismatch`` (never served, never decided).

        Fix-contract publications additionally run the full parent cross-chain
        when the parent document is present; a missing parent yields a
        warning, never a silent pass."""
        with self.snapshot() as snap:
            document = snap.document()
        contract_schema = self.manifest["contract_schema"]
        schema_key = {"defect": "defect", "fix": "fix"}.get(
            contract_schema.rsplit("fix-decisions.", 1)[-1].split("/")[0].replace("-contract", ""),
            None,
        )
        if schema_key is None:
            raise ServiceError(
                422, "gate_contract_mismatch",
                f"unknown contract_schema for coherence validation: {contract_schema!r}",
            )
        valid, messages = validate_mod.validate_document(document, schema_key)
        if not valid:
            raise ServiceError(
                422, "gate_contract_mismatch",
                f"contract fails its {schema_schema_name(schema_key)}: {'; '.join(messages[:3])}",
            )
        if document.get("gate_kind") != self.manifest["gate_kind"]:
            raise ServiceError(
                422, "gate_contract_mismatch",
                f"manifest gate_kind {self.manifest['gate_kind']!r} != contract gate_kind "
                f"{document.get('gate_kind')!r} (reclassification refused)",
            )
        # Parent cross-chain for fix contracts (warning on absent parent —
        # never a silent pass, never a load blocker by itself).
        if schema_key == "fix":
            parent_rel = (document.get("parent_defect_contract") or {}).get("path")
            if parent_rel:
                try:
                    parent_rel = safe_relative(parent_rel, "parent_defect_contract.path")
                    parent_path = contained_path(self.approved_root, parent_rel, self.approved_root, "parent_defect_contract")
                    ok, chain_errors = cross_validate_mod.cross_validate(
                        self.contract_path, parent_path, self.manifest_path
                    )
                    if not ok:
                        raise ServiceError(
                            422, "gate_contract_mismatch",
                            f"parent defect-contract cross-chain failed: {'; '.join(chain_errors[:3])}",
                        )
                except ServiceError:
                    raise
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    log.warning(
                        "parent_chain_unavailable publication=%s error=%s",
                        self.publication_id, exc,
                    )
            else:
                log.warning("parent_absent publication=%s", self.publication_id)

    def gate_settings_key(self) -> str:
        key = GATE_KIND_TO_SETTINGS_KEY.get(self.manifest["gate_kind"])
        if key is None:
            raise ServiceError(
                422, "bad_gate_kind",
                f"gate_kind {self.manifest['gate_kind']!r} has no settings mapping",
            )
        return key

    def open_root_fd(self) -> int:
        return os.open(self.approved_root, os.O_RDONLY)

    def open_contract_parent_fd(self) -> tuple[int, str]:
        """(parent dir fd via no-symlink walk, contract filename) — the caller
        uses these for the R2 re-identity check; caller closes the fd."""
        components = self._contract_components
        parent_fd = open_dir_components(self.open_root_fd(), components[:-1])
        return parent_fd, components[-1]

    def open_receipts_fd(self, create: bool = False) -> int:
        """Receipt directory fd via a no-symlink walk (R1). Missing components
        are created relative to their parent descriptor only. Caller closes."""
        return open_dir_components(self.open_root_fd(), self._receipts_components, create=create)

    def snapshot(self) -> ContractSnapshot:
        return ContractSnapshot(self.contract_path, self.contract_size_limit)

    def recomputed_digest(self) -> str:
        with self.snapshot() as snap:
            return snap.digest


def load_publications(publications_dir: Path, approved_roots: list[Path], contract_size_limit: int = DEFAULT_CONTRACT_LIMIT) -> tuple[dict[str, Publication], int, list[str]]:
    """Validate every manifest; skip + log invalid ones, keep the rest.

    Returns (publications by id, count of invalid manifests, warning strings)
    so listings can fail loud about skipped files instead of silently hiding
    them. The manifest file itself must live inside an approved root, and the
    repo root it declares must itself be an approved root (BLOCKER 1).
    """
    publications: dict[str, Publication] = {}
    invalid = 0
    warnings: list[str] = []
    publications_dir = Path(publications_dir)
    if not publications_dir.is_dir():
        return publications, invalid, warnings
    resolved_roots = [Path(r).resolve() for r in approved_roots]
    # Legacy v1.0 stores manifests directly in this directory; publisher v1.1
    # stores each immutable bundle under publications/<publication-id>/.
    for path in sorted(list(publications_dir.glob("*.json")) + list(publications_dir.glob("pub-*/manifest.json"))):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("invalid_manifest path=%s error=not-json detail=%s", path.name, exc)
            invalid += 1
            warnings.append(f"{path.name}: not valid JSON")
            continue
        valid, messages = validate_mod.validate_document(document, "manifest")
        if not valid:
            log.warning("invalid_manifest path=%s detail=%s", path.name, "; ".join(messages))
            invalid += 1
            warnings.append(f"{path.name}: schema-invalid")
            continue
        # BLOCKER 1: the manifest file itself must live inside an approved root.
        manifest_resolved = path.resolve()
        matching = [root for root in resolved_roots if manifest_resolved.is_relative_to(root)]
        if not matching:
            log.warning("manifest_outside_approved_roots path=%s", path.name)
            invalid += 1
            warnings.append(f"{path.name}: manifest file is outside every approved root")
            continue
        # The declared repo root must itself be an approved root (a manifest
        # cannot smuggle in its own root). Use the tightest (deepest) match.
        approved_root = max(matching, key=lambda r: len(str(r)))
        declared_root = document.get("allowed_repo_root", "")
        if Path(declared_root).resolve() != approved_root:
            log.warning(
                "manifest_root_not_approved path=%s declared=%s approved=%s",
                path.name, declared_root, approved_root,
            )
            invalid += 1
            warnings.append(f"{path.name}: allowed_repo_root is not an approved root")
            continue
        try:
            publication = Publication(document, path, approved_root, contract_size_limit)
        except ServiceError as exc:
            log.warning("invalid_manifest path=%s detail=%s", path.name, exc.message)
            invalid += 1
            warnings.append(f"{path.name}: {exc.message}")
            continue
        # Round-2 HIGH 3: contract schema/coherence validation at LOAD — a
        # relabeled or corrupt contract is never served.
        try:
            publication.validate_coherence()
        except ServiceError as exc:
            log.warning("gate_contract_mismatch path=%s detail=%s", path.name, exc.message)
            invalid += 1
            warnings.append(f"{path.name}: {exc.code}: {exc.message}")
            continue
        # Phase 4 / SC-15 + round-2 HIGH 2 publication-time enforcement: the
        # settings file must exist, hash to fix_settings_sha256, parse, and be
        # ticket-bound to this contract. Any failure skips the publication.
        try:
            publication.verify_settings()
        except ServiceError as exc:
            log.warning("settings_mismatch path=%s code=%s detail=%s", path.name, exc.code, exc.message)
            invalid += 1
            warnings.append(f"{path.name}: {exc.code}: {exc.message}")
            continue
        publications[publication.publication_id] = publication
    return publications, invalid, warnings


# ---------------------------------------------------------------------------
# Q&A thread store (mutable-with-history: temp + replace allowed)


def _write_thread_atomic(thread_path: Path, thread: dict) -> None:
    thread_path.parent.mkdir(parents=True, exist_ok=True)
    temp = thread_path.parent / f".tmp-{uuid.uuid4().hex}-{thread_path.name}"
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(thread, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, thread_path)
        dir_fd = os.open(thread_path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def _validate_thread(thread: dict) -> None:
    valid, messages = validate_mod.validate_document(thread, "qa")
    if not valid:
        raise ServiceError(422, "thread_invalid", "; ".join(messages))


def read_thread(threads_dir: Path, thread_id: str) -> dict:
    path = Path(threads_dir) / f"{thread_id}.json"
    if not path.is_file():
        raise ServiceError(404, "thread_not_found", f"no such thread: {thread_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_thread(threads_dir: Path, thread: dict) -> Path:
    _validate_thread(thread)
    path = Path(threads_dir) / f"{thread['thread_id']}.json"
    _write_thread_atomic(path, thread)
    return path


def write_thread_fd(threads_fd: int, thread: dict) -> None:
    """Thread write relative to an already-validated threads directory fd
    (round 3 (c): the same contained-dir descriptor discipline as receipts —
    temp O_EXCL + fsync + os.replace by name relative to the fd + dir fsync;
    replace IS allowed for threads, which are mutable-with-history)."""
    _validate_thread(thread)
    filename = f"{thread['thread_id']}.json"
    temp_name = f".tmp-{uuid.uuid4().hex}-{filename}"
    fd = os.open(temp_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644, dir_fd=threads_fd)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(thread, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, filename, src_dir_fd=threads_fd, dst_dir_fd=threads_fd)
        os.fsync(threads_fd)
    except BaseException:
        try:
            os.unlink(temp_name, dir_fd=threads_fd)
        except FileNotFoundError:
            pass
        raise


def read_thread_fd(threads_fd: int, thread_id: str) -> dict:
    filename = f"{thread_id}.json"
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=threads_fd)
    except (FileNotFoundError, FileExistsError) as exc:
        raise ServiceError(404, "thread_not_found", f"no such thread: {thread_id}") from exc
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(409, "thread_unreadable", f"thread {thread_id} is not valid JSON: {exc}")


def new_thread_id() -> str:
    return f"thread-{uuid.uuid4().hex[:16]}"


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Decision service (pure logic; the HTTP handler is a thin shell)


class DecisionService:
    def __init__(
        self,
        publications_dir: Path,
        threads_dir: Path,
        approved_roots: list[Path],
        body_limit: int = DEFAULT_BODY_LIMIT,
        contract_size_limit: int = DEFAULT_CONTRACT_LIMIT,
        allowed_origins: list[str] | None = None,
        publications: dict[str, Publication] | None = None,
        invalid_manifests: int = 0,
        manifest_warnings: list[str] | None = None,
        active_round_sources: list[dict] | None = None,
        config_path: Path | None = None,
        dist_dir: Path | None = None,
    ):
        if not approved_roots:
            raise ValueError("at least one approved root is required before any manifest is served (BLOCKER 1)")
        self.publications_dir = Path(publications_dir)
        self.threads_dir = Path(threads_dir)
        self.approved_roots = [Path(r).resolve() for r in approved_roots]
        self.body_limit = body_limit
        self.contract_size_limit = contract_size_limit
        self.allowed_origins = list(allowed_origins or ["http://localhost", "http://127.0.0.1"])
        self._lock = threading.RLock()
        self._dist_dir = Path(dist_dir).resolve() if dist_dir is not None else None
        self.generation_id = None
        self.last_reload_status = "initial"
        self.last_reload_error = None
        # Publications may be injected (tests) or loaded from the directory.
        if publications is not None:
            self._publications = publications
            self._invalid_manifests = invalid_manifests
            self._manifest_warnings = list(manifest_warnings or [])
        else:
            self.reload_publications()
        self._idempotency: dict[str, dict] = {}
        self._active_round_sources: dict[str, ActiveRoundSource] = {}
        self._active_round_warnings: list[str] = []
        self._active_round_config_path = Path(config_path) if config_path is not None else None
        self._active_round_config_digest: str | None = None
        if self._active_round_config_path is not None:
            self._refresh_active_round_sources()
        else:
            for source in active_round_sources or []:
                try:
                    self.register_active_round(source)
                except ServiceError as exc:
                    round_review_id = source.get("round_review_id") if isinstance(source, dict) else None
                    round_identity = round_review_id if isinstance(round_review_id, str) and round_review_id else "<unknown>"
                    self._active_round_warnings.append(
                        f"invalid active round registration {round_identity}: {exc.code}: {exc.message}"
                    )
                    log.warning(
                        "active_round_registration_skipped round_review_id=%s reason=%s validation_failure=%s",
                        round_identity, exc.code, exc.message,
                    )

    @property
    def publications(self) -> dict[str, Publication]:
        return self._publications

    def reload_publications(
        self,
        generation_id: str | None = None,
        publication_ids: set[str] | None = None,
        dist_dir: Path | None = None,
    ) -> None:
        """Load a complete generation before atomically exposing its publication map.

        In generation mode, committed publication bundles are not automatically
        visible. Only IDs named by the active generation metadata are exposed;
        crash-orphaned bundles remain inert until publisher recovery activates
        them in a later generation.
        """
        publications, invalid, warnings = load_publications(
            self.publications_dir, self.approved_roots, self.contract_size_limit
        )
        if publication_ids is not None:
            missing = publication_ids - set(publications)
            if missing:
                raise ServiceError(
                    500,
                    "generation_incomplete",
                    f"active generation references unavailable publications: {sorted(missing)}",
                )
            publications = {key: value for key, value in publications.items() if key in publication_ids}
            # Invalid/warning rows from bundles outside this immutable generation
            # do not belong to the visible generation's health contract.
            invalid = 0
            warnings = []
        candidate_dist = Path(dist_dir).resolve() if dist_dir is not None else self._dist_dir
        if candidate_dist is not None:
            if not (candidate_dist / "index.html").is_file():
                raise ServiceError(500, "generation_incomplete", f"generation static index missing: {candidate_dist / 'index.html'}")
            if publication_ids is not None:
                missing_pages = [
                    publication_id for publication_id in sorted(publication_ids)
                    if not (candidate_dist / "review" / publication_id / "index.html").is_file()
                ]
                if missing_pages:
                    raise ServiceError(500, "generation_incomplete", f"generation review pages missing: {missing_pages}")
        # One lock is the visibility boundary for API publications, static root,
        # and generation identity. Active-round registrations stay on this same
        # long-lived service and are intentionally not replaced.
        with self._lock:
            self._publications, self._invalid_manifests, self._manifest_warnings = publications, invalid, warnings
            self._dist_dir = candidate_dist
            self.generation_id = generation_id
            self.last_reload_status, self.last_reload_error = "ok", None

    def current_static_root(self) -> Path | None:
        with self._lock:
            return self._dist_dir

    def _require_threads_dir_contained(self) -> Path:
        """The threads dir must live inside an approved root (BLOCKER 1)."""
        resolved = self.threads_dir.resolve()
        if not any(resolved.is_relative_to(root) for root in self.approved_roots):
            raise ServiceError(422, "unsafe_path", "threads directory is outside every approved root")
        return resolved

    def _open_threads_fd(self, create: bool = True) -> int:
        """Threads directory fd via a no-symlink component walk from the
        tightest approved root containing it (round 3 (c)). Caller closes."""
        resolved = self.threads_dir.resolve()
        matching = [root for root in self.approved_roots if resolved.is_relative_to(root)]
        if not matching:
            raise ServiceError(422, "unsafe_path", "threads directory is outside every approved root")
        root = max(matching, key=lambda r: len(str(r)))
        components = [p for p in resolved.relative_to(root).parts if p not in ("", ".")]
        root_fd = os.open(root, os.O_RDONLY)
        try:
            return open_dir_components(root_fd, components, create=create)
        except BaseException:
            os.close(root_fd)
            raise

    def get_publication(self, publication_id: str) -> Publication:
        publication = self._publications.get(publication_id)
        if publication is None:
            raise ServiceError(404, "publication_not_found", f"no such publication: {publication_id}")
        return publication

    def origin_allowed(self, origin: str | None, host: str | None = None) -> bool:
        # Absent Origin (curl, tests, same-origin tools) is allowed. A present
        # Origin must be a configured origin or the exact HTTP host:port that
        # received this request; malformed origins fail closed.
        if not origin:
            return True
        parsed_origin = parse_web_origin(origin)
        if parsed_origin is None:
            return False
        if parsed_origin in {parsed for value in self.allowed_origins if (parsed := parse_web_origin(value))}:
            return True
        parsed_host = parse_host_header(host)
        return parsed_origin[0] == "http" and parsed_host == parsed_origin[1:]

    # -- Active UAT rounds --------------------------------------------------

    def _refresh_active_round_sources(self) -> None:
        """Atomically adopt a fully validated active-round config generation."""
        config_path = self._active_round_config_path
        if config_path is None:
            return
        try:
            config_bytes = config_path.read_bytes()
            digest = sha256_bytes(config_bytes)
            if digest == self._active_round_config_digest:
                return
            config = load_config(config_path)
            if sha256_bytes(config_path.read_bytes()) != digest:
                raise ConfigError(f"config changed during read: {config_path}")
            candidate: dict[str, ActiveRoundSource] = {}
            for document in config.get("active_round_sources", []):
                round_review_id = document.get("round_review_id") if isinstance(document, dict) else "<unknown>"
                try:
                    source = parse_active_round_source(document, size_limit=self.contract_size_limit)
                except ServiceError as exc:
                    raise ServiceError(exc.status, exc.code, f"{round_review_id}: {exc.message}") from exc
                if source.round_review_id in candidate:
                    raise ServiceError(422, "source_invalid", f"{source.round_review_id}: round_review_id is already registered")
                candidate[source.round_review_id] = source
        except (ConfigError, ServiceError, OSError) as exc:
            warning = f"active_round_config_refresh_failed {config_path}: {exc}"
            with self._lock:
                self._active_round_warnings = [warning]
            log.warning("%s", warning)
            return
        with self._lock:
            self._active_round_sources = candidate
            self._active_round_warnings = []
            self._active_round_config_digest = digest

    def register_active_round(self, document: dict) -> None:
        source = parse_active_round_source(document, size_limit=self.contract_size_limit)
        if source.round_review_id in self._active_round_sources:
            raise ServiceError(422, "source_invalid", "round_review_id is already registered")
        self._active_round_sources[source.round_review_id] = source
        log.info("active_round_registration_accepted round_review_id=%s", source.round_review_id)

    @staticmethod
    def _package_identity(package: dict) -> dict:
        return {field: package.get(field) for field in IDENTITY_FIELDS}

    def _round_view_from_snapshots(self, source: ActiveRoundSource, current: RoundFileSnapshot, feedback: RoundFileSnapshot) -> dict:
        try:
            current_projection = uat_round_materialize.parse_canonical_round_projection(current.bytes)
            feedback_projection = uat_feedback.parse_feedback_projection(feedback.bytes)
        except (ValueError, uat_round_materialize.ValidationError, uat_feedback.PackageValidationError) as exc:
            raise ServiceError(422, "source_invalid", "registered UAT source failed canonical parsing") from exc
        current_package = current_projection["normalized_package"]
        feedback_package = feedback_projection["normalized_package"]
        if not isinstance(current_package, dict) or not isinstance(feedback_package, dict):
            raise ServiceError(422, "source_invalid", "canonical package projection is invalid")
        if (self._package_identity(current_package) != source.document["package_identity"]
                or self._package_identity(feedback_package) != source.document["package_identity"]
                or current_package != feedback_package):
            raise ServiceError(409, "uat_round_projection_stale", "registered package identity no longer matches canonical artifacts")
        if current_package.get("ticket") != source.document["ticket"]:
            raise ServiceError(409, "round_feedback_identity_mismatch", "registered ticket does not match canonical package")
        journeys = current_projection["journeys"]
        if journeys != feedback_package.get("journeys"):
            raise ServiceError(409, "round_feedback_identity_mismatch", "round and feedback journeys differ")
        entries = feedback_projection["entries"]
        if set(entries) != {item.get("journey_id") for item in journeys if isinstance(item, dict)}:
            raise ServiceError(409, "round_feedback_identity_mismatch", "feedback entries do not match canonical journeys")
        is_v2 = current_package.get("schema_version") == "uat-canonical-package@2"
        writable = is_v2 and feedback_projection["processing_state"] == "UNPROCESSED"
        step_entries = feedback_projection.get("step_entries") if is_v2 else None
        if is_v2:
            if not isinstance(step_entries, dict):
                raise ServiceError(409, "round_feedback_identity_mismatch", "v2 feedback step entries are missing")
            total = len(step_entries)
            step_counts = {
                "total": total,
                "gray": sum(not entry.get("complete") and not any(entry.get(field) for field in ("assessment", "finding_severity", "finding_disposition", "observed", "comment")) for entry in step_entries.values() if isinstance(entry, dict)),
                "yellow": sum(not entry.get("complete") and any(entry.get(field) for field in ("assessment", "finding_severity", "finding_disposition", "observed", "comment")) for entry in step_entries.values() if isinstance(entry, dict)),
                "green": sum(bool(entry.get("complete")) for entry in step_entries.values() if isinstance(entry, dict)),
            }
        current_view = {
            "path": source.document["current_round_path"], "sha256": current.digest,
            "size": len(current.bytes), "package_state": current_projection["package_state"],
            "source": current.bytes.decode("utf-8"), "normalized_package": current_package,
            "journeys": journeys, "writable": False,
        }
        feedback_view = {
            "path": source.document["feedback_path"], "sha256": feedback.digest,
            "size": len(feedback.bytes), "processing_state": feedback_projection["processing_state"],
            "round_status": feedback_projection["round_status"],
            "feedback_generated_at": feedback_projection["generated_at"], "entries": entries,
            "markerized": feedback_projection["markerized"], "writable": writable,
        }
        if is_v2:
            feedback_view["step_entries"] = step_entries
            feedback_view["step_counts"] = step_counts
            feedback_view["walk_stop"] = feedback_projection.get("walk_stop")
        projection = {
            "schema": ACTIVE_ROUND_VIEW_SCHEMA_V2 if is_v2 else ACTIVE_ROUND_VIEW_SCHEMA, "schema_version": "2.0" if is_v2 else "1.0",
            "round_review_id": source.round_review_id, "ticket": source.document["ticket"],
            "round_status": feedback_projection["round_status"],
            "current_round": current_view, "feedback": feedback_view,
            "capabilities": {"feedback_update": writable, "practice_fixture": source.is_practice_fixture},
        }
        if source.is_practice_fixture:
            projection["fixture"] = {"purpose": "safe-practice-round", "resettable": True}
        if is_v2:
            projection["feedback_result_contract"] = uat_feedback.feedback_result_contract()
        if is_v2:
            # A/C 5: event committed, consumer delivered, and projection current
            # stay separately observable — never one collapsed success flag.
            state_root = source.document.get("allowed_repo_root")
            if state_root:
                watch = form_events_mod.read_form_watch(state_root, source.round_review_id)
                projection["form_action_notification"] = {
                    "armed": watch is not None,
                    "states": form_events_mod.form_event_states(
                        state_root, source.round_review_id,
                        projection_sha256=feedback.digest,
                    ),
                }
        projection["projection_sha256"] = sha256_bytes(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode())
        return projection

    def _round_view(self, round_review_id: str, *, refresh: bool = True) -> dict:
        if refresh:
            self._refresh_active_round_sources()
        source = self._active_round_sources.get(round_review_id)
        if source is None:
            raise ServiceError(404, "round_not_found", "no such registered active UAT round")
        if not source.is_practice_fixture:
            self._arm_form_watch(source)
        with source.snapshot_current(self.contract_size_limit) as current, source.snapshot_feedback(self.contract_size_limit) as feedback:
            return self._round_view_from_snapshots(source, current, feedback)

    def _arm_form_watch(self, source: ActiveRoundSource) -> dict | None:
        """Ensure durable source registration; idempotent and never fatal to page readability.

        Registration does not arm a runtime watcher or guarantee exactly-once
        delivery.
        """
        state_root = source.document.get("allowed_repo_root")
        if not state_root or source.is_practice_fixture:
            return None
        try:
            return form_events_mod.arm_form_watch(
                state_root,
                round_review_id=source.round_review_id,
                ticket=source.document["ticket"],
            )
        except (OSError, form_events_mod.FormEventError) as exc:
            log.warning("form_watch_arm_failed round_review_id=%s error=%s", source.round_review_id, exc)
            return None

    def active_round_summaries(self) -> dict:
        self._refresh_active_round_sources()
        rounds = []
        warnings = list(self._active_round_warnings)
        for round_review_id in sorted(self._active_round_sources):
            try:
                view = self._round_view(round_review_id, refresh=False)
            except ServiceError as exc:
                warnings.append(f"active round {round_review_id} skipped: {exc.code}: {exc.message}")
                log.warning(
                    "active_round_registration_skipped round_review_id=%s reason=%s validation_failure=%s",
                    round_review_id, exc.code, exc.message,
                )
                continue
            rounds.append({
                "round_review_id": round_review_id, "ticket": view["ticket"],
                "package_state": view["current_round"]["package_state"],
                "current_round_sha256": view["current_round"]["sha256"],
                "feedback_sha256": view["feedback"]["sha256"],
                "feedback_generated_at": view["feedback"]["feedback_generated_at"],
                "processing_state": view["feedback"]["processing_state"],
                "round_status": view["round_status"],
                "writable": view["capabilities"]["feedback_update"],
                "practice_fixture": view["capabilities"]["practice_fixture"],
                "legacy": view["schema"] == ACTIVE_ROUND_VIEW_SCHEMA,
            })
        # Issuance time is a canonical feedback field. Sort it explicitly so
        # every valid registration stays visible and the most recently issued
        # round appears first. Lexical id ordering breaks equal-time ties.
        rounds.sort(key=lambda row: row["round_review_id"])
        rounds.sort(key=lambda row: row["feedback_generated_at"], reverse=True)
        return {"rounds": rounds, "warnings": warnings}

    @staticmethod
    def _validate_feedback_command(command: dict) -> str:
        common = {"schema", "schema_version", "expected_feedback_sha256", "expected_current_round_sha256"}
        if not isinstance(command, dict):
            raise ServiceError(422, "invalid_feedback_update", "feedback update must be an object")
        if command.get("schema") == FEEDBACK_UPDATE_SCHEMA_V2 and command.get("schema_version") == "2.0":
            entry_key = "step_entries"
            entry_fields = {"complete", "assessment", "finding_severity", "finding_disposition", "observed", "comment"}
        elif command.get("schema") == FEEDBACK_UPDATE_SCHEMA and command.get("schema_version") == "1.0":
            entry_key = "entries"
            entry_fields = {"scenario_outcome", "finding_severity", "finding_disposition", "reason", "comment"}
        else:
            raise ServiceError(422, "invalid_feedback_update", "unsupported feedback update schema/version")
        allowed = common | {entry_key} | ({"walk_stop"} if entry_key == "step_entries" else set())
        if not set(command) <= allowed or not set(command) >= common | {entry_key} or not all(isinstance(command[key], str) and SHA256_RE.fullmatch(command[key]) for key in common - {"schema", "schema_version"}):
            raise ServiceError(422, "invalid_feedback_update", "feedback update has unsupported fields")
        stop = command.get("walk_stop")
        if stop is not None:
            if (not isinstance(stop, dict) or set(stop) != WALK_STOP_FIELDS
                    or stop.get("state") != "STOPPED" or stop.get("trigger") != "OBSERVED_FAILURE"
                    or stop.get("recorded_by") not in {"OWNER", "ORCHESTRATOR_TRANSCRIPTION"}):
                raise ServiceError(422, "invalid_feedback_update", "walk stop marker is invalid")
            unreached = stop.get("unreached_step_ids")
            if (not isinstance(unreached, list) or not unreached
                    or not all(isinstance(item, str) and item for item in unreached)
                    or len(set(unreached)) != len(unreached)):
                raise ServiceError(422, "invalid_feedback_update", "walk stop unreached ids are invalid")
            for field in ("halting_step_id", "note"):
                value = stop.get(field)
                if (not isinstance(value, str) or not value.strip() or len(value) > 20000
                        or any(ord(char) < 32 and char not in "\n\r\t" for char in value)):
                    raise ServiceError(422, "invalid_feedback_update", "walk stop text is invalid")
        entries = command[entry_key]
        if not isinstance(entries, dict):
            raise ServiceError(422, "invalid_feedback_update", "entries must be an object")
        for entry in entries.values():
            if not isinstance(entry, dict) or set(entry) != entry_fields:
                raise ServiceError(422, "invalid_feedback_update", "entry fields are invalid")
            if entry_key == "step_entries" and not isinstance(entry["complete"], bool):
                raise ServiceError(422, "invalid_feedback_update", "step completion is invalid")
            for field, value in entry.items():
                if field != "complete" and (not isinstance(value, str) or len(value) > 20000 or any(ord(char) < 32 and char not in "\n\r\t" for char in value)):
                    raise ServiceError(422, "invalid_feedback_update", "entry text is invalid")
        return entry_key

    @staticmethod
    def _validate_round_status_command(command: dict) -> str:
        required = {"schema", "schema_version", "expected_feedback_sha256", "expected_current_round_sha256", "round_status"}
        if (not isinstance(command, dict) or set(command) != required
                or command.get("schema") != ROUND_STATUS_UPDATE_SCHEMA
                or command.get("schema_version") != "1.0"
                or command.get("round_status") not in ROUND_STATUSES
                or not all(isinstance(command[key], str) and SHA256_RE.fullmatch(command[key])
                           for key in ("expected_feedback_sha256", "expected_current_round_sha256"))):
            raise ServiceError(422, "invalid_round_status_update", "round status update has unsupported fields")
        return command["round_status"]

    def update_active_round_status(self, round_review_id: str, command: dict) -> tuple[int, dict]:
        """Atomically persist a shared owner status in the canonical feedback artifact."""
        round_status = self._validate_round_status_command(command)
        self._refresh_active_round_sources()
        source = self._active_round_sources.get(round_review_id)
        if source is None:
            raise ServiceError(404, "round_not_found", "no such registered active UAT round")
        with self._lock, source.snapshot_current(self.contract_size_limit) as current, source.snapshot_feedback(self.contract_size_limit) as feedback:
            view = self._round_view_from_snapshots(source, current, feedback)
            if view["feedback"]["processing_state"] != "UNPROCESSED":
                raise ServiceError(409, "feedback_processed", "feedback has already been processed")
            if view["round_status"] == round_status:
                try:
                    os.fsync(feedback.parent_fd)
                except OSError as exc:
                    raise ServiceError(500, "feedback_commit_uncertain", "round status may already be canonical; retry the exact original command") from exc
                return 200, {"status": "idempotent", "view": view}
            if feedback.digest != command["expected_feedback_sha256"]:
                raise ServiceError(409, "stale_feedback", "feedback changed; reload the latest projection")
            if current.digest != command["expected_current_round_sha256"]:
                raise ServiceError(409, "stale_round", "current round changed; reload the latest projection")
            try:
                candidate = uat_feedback.render_feedback_round_status(feedback.bytes, round_status)
            except uat_feedback.PackageValidationError as exc:
                raise ServiceError(422, "invalid_round_status_update", "round status cannot be safely rendered") from exc
            temp_name = f".uat-feedback-{uuid.uuid4().hex}.tmp"
            replaced = False
            try:
                fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=feedback.parent_fd)
                try:
                    write_all(fd, candidate)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                with source.snapshot_current(self.contract_size_limit) as latest_current, source.snapshot_feedback(self.contract_size_limit) as latest_feedback:
                    latest_view = self._round_view_from_snapshots(source, latest_current, latest_feedback)
                    if latest_view["feedback"]["processing_state"] != "UNPROCESSED":
                        raise ServiceError(409, "feedback_processed", "feedback was processed during save")
                    if latest_current.digest != current.digest:
                        raise ServiceError(409, "stale_round", "current round changed during save")
                    if latest_feedback.digest != feedback.digest:
                        raise ServiceError(409, "stale_feedback", "feedback changed during save")
                    current_parent_matches = (os.fstat(latest_current.parent_fd).st_dev, os.fstat(latest_current.parent_fd).st_ino) == (os.fstat(current.parent_fd).st_dev, os.fstat(current.parent_fd).st_ino)
                    feedback_parent_matches = (os.fstat(latest_feedback.parent_fd).st_dev, os.fstat(latest_feedback.parent_fd).st_ino) == (os.fstat(feedback.parent_fd).st_dev, os.fstat(feedback.parent_fd).st_ino)
                if not current_parent_matches:
                    raise ServiceError(409, "stale_round", "current round parent changed during save")
                if not feedback_parent_matches:
                    raise ServiceError(409, "stale_feedback", "feedback parent changed during save")
                os.replace(temp_name, feedback.components[-1], src_dir_fd=feedback.parent_fd, dst_dir_fd=feedback.parent_fd)
                replaced = True
                os.fsync(feedback.parent_fd)
            except ServiceError:
                if not replaced:
                    try:
                        os.unlink(temp_name, dir_fd=feedback.parent_fd)
                    except OSError:
                        pass
                raise
            except OSError as exc:
                if replaced:
                    raise ServiceError(500, "feedback_commit_uncertain", "round status may already be canonical; retry the exact original command") from exc
                try:
                    os.unlink(temp_name, dir_fd=feedback.parent_fd)
                except OSError:
                    pass
                raise ServiceError(500, "feedback_write_failed", "round status was not persisted") from exc
        return 200, {"status": "saved", "view": self._round_view(round_review_id)}

    def update_active_round_feedback(self, round_review_id: str, command: dict) -> tuple[int, dict]:
        self._refresh_active_round_sources()
        entry_key = self._validate_feedback_command(command)
        submitted_entries = command[entry_key]
        submitted_stop = command.get("walk_stop")
        source = self._active_round_sources.get(round_review_id)
        if source is None:
            raise ServiceError(404, "round_not_found", "no such registered active UAT round")
        with self._lock, source.snapshot_current(self.contract_size_limit) as current, source.snapshot_feedback(self.contract_size_limit) as feedback:
            view = self._round_view_from_snapshots(source, current, feedback)
            try:
                # This is deliberately before digest/idempotency decisions: the
                # shared Phase-2 validator owns triples, N/A rules, and journey shape.
                candidate = uat_feedback.render_feedback_update(feedback.bytes, submitted_entries, walk_stop=submitted_stop)
                candidate_projection = uat_feedback.parse_feedback_projection(candidate)
                # v2 completed blank classifications intentionally normalize to
                # their legal ledger projection. Compare the rendered candidate
                # with that same deterministic projection, not the raw UI form.
                expected_entries = uat_feedback._validate_feedback_entries(
                    submitted_entries, candidate_projection["normalized_package"],
                )
            except (ValueError, uat_feedback.PackageValidationError) as exc:
                code = "ambiguous_editable_region" if "ambiguous" in str(exc) else "invalid_feedback_update"
                raise ServiceError(422, code, "feedback update cannot be safely rendered") from exc
            if candidate_projection.get(entry_key) != expected_entries:
                raise ServiceError(422, "invalid_feedback_update", "candidate feedback did not preserve submitted entries")
            if candidate_projection.get("walk_stop") != submitted_stop:
                raise ServiceError(422, "invalid_feedback_update", "candidate feedback did not preserve the walk stop")
            if len(candidate) > self.contract_size_limit:
                raise ServiceError(413, "source_too_large", "rendered feedback exceeds service source limit")
            if view["feedback"]["processing_state"] != "UNPROCESSED":
                raise ServiceError(409, "feedback_processed", "feedback has already been processed")
            content_changed = (view["feedback"].get(entry_key) != submitted_entries
                               or view["feedback"].get("walk_stop") != submitted_stop)
            if not content_changed:
                try:
                    os.fsync(feedback.parent_fd)
                except OSError as exc:
                    raise ServiceError(500, "feedback_commit_uncertain", "feedback may already be canonical; retry the exact original command") from exc
                return 200, {"status": "idempotent", "view": view}
            if content_changed and view["round_status"] == "NOT_STARTED":
                # The owner controls every status after the first persisted content
                # change; returning to NOT_STARTED deliberately arms this promotion again.
                candidate = uat_feedback.render_feedback_round_status(candidate, "IN_PROGRESS")
            if feedback.digest != command["expected_feedback_sha256"]:
                raise ServiceError(409, "stale_feedback", "feedback changed; reload the latest projection")
            if current.digest != command["expected_current_round_sha256"]:
                raise ServiceError(409, "stale_round", "current round changed; reload the latest projection")
            temp_name = f".uat-feedback-{uuid.uuid4().hex}.tmp"
            replaced = False
            try:
                fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=feedback.parent_fd)
                try:
                    write_all(fd, candidate)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                # The post-stage check closes the race between rendering and publication.
                with source.snapshot_current(self.contract_size_limit) as latest_current, source.snapshot_feedback(self.contract_size_limit) as latest_feedback:
                    latest_view = self._round_view_from_snapshots(source, latest_current, latest_feedback)
                    if latest_view["feedback"]["processing_state"] != "UNPROCESSED":
                        raise ServiceError(409, "feedback_processed", "feedback was processed during save")
                    if latest_current.digest != current.digest:
                        raise ServiceError(409, "stale_round", "current round changed during save")
                    if latest_feedback.digest != feedback.digest:
                        raise ServiceError(409, "stale_feedback", "feedback changed during save")
                    current_parent_matches = (os.fstat(latest_current.parent_fd).st_dev, os.fstat(latest_current.parent_fd).st_ino) == (os.fstat(current.parent_fd).st_dev, os.fstat(current.parent_fd).st_ino)
                    feedback_parent_matches = (os.fstat(latest_feedback.parent_fd).st_dev, os.fstat(latest_feedback.parent_fd).st_ino) == (os.fstat(feedback.parent_fd).st_dev, os.fstat(feedback.parent_fd).st_ino)
                if not current_parent_matches:
                    raise ServiceError(409, "stale_round", "current round parent changed during save")
                if not feedback_parent_matches:
                    raise ServiceError(409, "stale_feedback", "feedback parent changed during save")
                os.replace(temp_name, feedback.components[-1], src_dir_fd=feedback.parent_fd, dst_dir_fd=feedback.parent_fd)
                replaced = True
                os.fsync(feedback.parent_fd)
            except ServiceError:
                if not replaced:
                    try:
                        os.unlink(temp_name, dir_fd=feedback.parent_fd)
                    except OSError:
                        pass
                raise
            except OSError as exc:
                if replaced:
                    raise ServiceError(500, "feedback_commit_uncertain", "feedback may already be canonical; retry the exact original command") from exc
                try:
                    os.unlink(temp_name, dir_fd=feedback.parent_fd)
                except OSError:
                    pass
                raise ServiceError(500, "feedback_write_failed", "feedback was not persisted") from exc
        fresh = self._round_view(round_review_id)
        log.info("active_round_feedback_autosaved round_review_id=%s feedback_sha256=%s", round_review_id, fresh["feedback"]["sha256"][:12])
        return 200, {"status": "saved", "view": fresh}

    def submit_active_round_feedback(self, round_review_id: str, command: dict) -> tuple[int, dict]:
        """Persist the exact command, then notify once for its canonical digest.

        The feedback route is deliberately persist-only. This separate explicit
        submit action is the sole notification boundary, retaining the closed
        v2 command contract and making absent intent safe by default.
        """
        status, payload = self.update_active_round_feedback(round_review_id, command)
        source = self._active_round_sources[round_review_id]
        object_sha256 = payload["view"]["feedback"]["sha256"]
        state_root = source.document["allowed_repo_root"]
        if source.is_practice_fixture:
            prior = form_events_mod.read_practice_receipts(state_root, round_review_id)
            receipt = form_events_mod.record_practice_receipt(
                state_root, round_review_id=round_review_id, ticket=source.document["ticket"],
                object_sha256=object_sha256, prior_object_sha256=command["expected_feedback_sha256"],
            )
            payload["fixture_receipt"] = receipt
            payload["notification"] = "practice_already_submitted" if any(
                row.get("object_sha256") == object_sha256 for row in prior
            ) else "practice_submitted"
            return status, payload
        existing = form_events_mod.read_form_events(state_root, round_review_id)
        if any(event.get("action") == "feedback.send" and event.get("object_sha256") == object_sha256 for event in existing):
            payload["notification"] = "already_submitted"
            return status, payload
        event = self._emit_form_action_event(
            source, action="feedback.send", actor="owner", object_sha256=object_sha256,
            prior_object_sha256=command["expected_feedback_sha256"],
        )
        payload["notification"] = "submitted" if event is not None else "pending"
        return status, payload

    @staticmethod
    def _replace_snapshot_bytes(snapshot: RoundFileSnapshot, payload: bytes) -> None:
        temp_name = f".uat-reset-{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=snapshot.parent_fd)
            try:
                write_all(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp_name, snapshot.components[-1], src_dir_fd=snapshot.parent_fd, dst_dir_fd=snapshot.parent_fd)
            os.fsync(snapshot.parent_fd)
        except OSError as exc:
            try:
                os.unlink(temp_name, dir_fd=snapshot.parent_fd)
            except OSError:
                pass
            raise ServiceError(500, "practice_reset_failed", "practice artifacts could not be rematerialized") from exc

    def reset_practice_round(self, round_review_id: str) -> tuple[int, dict]:
        self._refresh_active_round_sources()
        source = self._active_round_sources.get(round_review_id)
        if source is None:
            raise ServiceError(404, "round_not_found", "no such registered active UAT round")
        if not source.is_practice_fixture:
            raise ServiceError(409, "not_practice_round", "only a safe practice round can be reset")
        with self._lock, source.snapshot_current(self.contract_size_limit) as current, source.snapshot_feedback(self.contract_size_limit) as feedback:
            package = uat_round_materialize.parse_canonical_round_projection(current.bytes)["normalized_package"]
            current_bytes = (uat_round_materialize.render_canonical_package_block_bytes(package, package_state="ISSUED")
                             + b"\n" + uat_round_materialize.render_normalized_package_annex_bytes(package))
            feedback_bytes = uat_feedback.render_feedback_document(
                package, generated_at=source.fixture["reset_generated_at"]
            )
            self._replace_snapshot_bytes(current, current_bytes)
            self._replace_snapshot_bytes(feedback, feedback_bytes)
        return 200, {"status": "practice_reset", "view": self._round_view(round_review_id)}

    def _emit_form_action_event(self, source: ActiveRoundSource, *, action: str, actor: str,
                                object_sha256: str, prior_object_sha256: str | None) -> dict | None:
        """Commit one immutable privacy-safe event for a successful form action.

        Only identities and digests are recorded — never the owner's comment,
        observed behavior, or assessment values.
        """
        state_root = source.document.get("allowed_repo_root")
        if not state_root:
            return None
        try:
            if form_events_mod.read_form_watch(state_root, source.round_review_id) is None:
                self._arm_form_watch(source)
            event = form_events_mod.emit_form_action_event(
                state_root, round_review_id=source.round_review_id,
                ticket=source.document["ticket"], action=action, actor=actor,
                object_sha256=object_sha256, prior_object_sha256=prior_object_sha256,
            )
        except (OSError, form_events_mod.FormEventError) as exc:
            log.warning("form_action_event_failed round_review_id=%s error=%s", source.round_review_id, exc)
            return None
        log.info(
            "form_action_event_committed round_review_id=%s action=%s event_id=%s next=%s",
            source.round_review_id, action, event["event_id"], event["next_lifecycle_step"],
        )
        return event

    # -- GET summaries ------------------------------------------------------

    def publication_summaries(self) -> dict:
        items = []
        for publication_id in sorted(self._publications):
            publication = self._publications[publication_id]
            item = {
                "publication_id": publication_id,
                "contract_path": publication.manifest["contract_path"],
                "contract_version": publication.manifest["contract_version"],
                "contract_schema": publication.manifest["contract_schema"],
                "gate_kind": publication.manifest["gate_kind"],
                "publication_kind": publication.manifest.get("publication_kind", "gate"),
                "published_at_utc": publication.manifest["published_at_utc"],
                "manifest_sha256": sha256_bytes(publication.manifest_path.read_bytes()),
            }
            try:
                item["digest_matches"] = publication.recomputed_digest() == publication.manifest["contract_sha256"]
            except ServiceError:
                item["digest_matches"] = False
            items.append(item)
        return {
            "publications": items,
            "invalid_manifests": self._invalid_manifests,
            "warnings": self._manifest_warnings,
        }

    def publication_detail(self, publication_id: str) -> dict:
        publication = self.get_publication(publication_id)
        with publication.snapshot() as snap:
            contract = snap.document()
            recomputed = snap.digest
        try:
            view = view_model.build_view_model(publication.contract_path, publication.manifest_path)
        except (ValueError, json.JSONDecodeError, FileNotFoundError) as exc:
            raise ServiceError(409, "contract_unreadable", f"cannot render contract: {exc}")
        receipt_list, receipt_warnings = receipts_mod.list_receipts(publication.receipt_dir)
        threads = self.threads_for_publication(publication_id)
        return {
            "publication_id": publication_id,
            "manifest": publication.manifest,
            "view_model": view,
            "current_sha256": recomputed,
            "digest_matches": recomputed == publication.manifest["contract_sha256"],
            "required_approval_vocabulary": substitute_vocab_placeholder(
                contract.get("required_approval_vocabulary", ""), publication.manifest["contract_sha256"]
            ),
            "receipts": [
                {
                    **{k: r.get(k) for k in ("receipt_id", "action", "actor", "created_at_utc", "contract_sha256")},
                    **{k: r[k] for k in ("authorization_source", "fix_settings_path", "fix_settings_sha256") if k in r},
                }
                for r in receipt_list
            ],
            "receipt_warnings": receipt_warnings,
            "threads": [
                {
                    "thread_id": t["thread_id"],
                    "status": t["status"],
                    "anchor": t.get("anchor", {}),
                    "digest_matches": self._thread_digest_matches(t),
                }
                for t in threads
            ],
        }

    def _thread_digest_matches(self, thread: dict) -> bool:
        """True when the thread's recorded contract digest still matches the
        live contract digest (anchored threads only)."""
        anchor = thread.get("anchor", {}) or {}
        recorded = anchor.get("contract_sha256")
        if not recorded:
            return False
        publication_id = anchor.get("publication_id")
        publication = self._publications.get(publication_id)
        if publication is None:
            return False
        try:
            return publication.recomputed_digest() == recorded
        except ServiceError:
            return False

    def threads_for_publication(self, publication_id: str) -> list[dict]:
        out = []
        if not self.threads_dir.is_dir():
            return out
        for path in sorted(self.threads_dir.glob("*.json")):
            try:
                thread = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            anchor = thread.get("anchor", {})
            if anchor.get("publication_id") == publication_id:
                out.append(thread)
        return out

    # -- POST actions -------------------------------------------------------

    @staticmethod
    def _is_decision_gate(publication: Publication, contract: dict, publication_kind: str) -> bool:
        """A publication may take approve/deny decisions ONLY when it is a
        gate publication AND (for the fix-contract digest gate) the contract is
        the FIRST repair-authorizing one. Revisions (first_repair_authorizing
        false) are informational per SC-14 — never a third gate."""
        if publication_kind != "gate":
            return False
        if publication.manifest["gate_kind"] == "fix-contract-digest":
            return bool(contract.get("first_repair_authorizing"))
        return True

    def apply_action(self, publication_id: str, body: dict) -> tuple[int, dict]:
        publication = self.get_publication(publication_id)
        action = body.get("action")
        if action not in ("approve", "deny", "comment"):
            raise ServiceError(400, "bad_action", f"unknown action: {action!r}")
        actor = body.get("actor") or "owner"
        if not isinstance(actor, str) or not actor.strip():
            raise ServiceError(400, "bad_actor", "actor must be a non-empty string")
        idem_key = body.get("idempotency_key")
        if idem_key is not None and (not isinstance(idem_key, str) or not idem_key.strip()):
            raise ServiceError(400, "bad_idempotency_key", "idempotency_key must be a non-empty string")
        # Round-2 SMALL (b): authorization provenance is SERVER-minted only;
        # a client supplying these fields is refused outright (fail closed).
        reserved = {"authorization_source", "fix_settings_path", "fix_settings_sha256"}
        if reserved.intersection(body):
            raise ServiceError(
                422, "reserved_field",
                f"action body keys are reserved for the server: {sorted(reserved.intersection(body))}",
            )

        with self._lock, publication.snapshot() as snap:
            # (a0) Phase 4 + round-2: settings digest/ticket binding re-verified
            # at action time under the lock — adversarially-injected publications
            # cannot act either.
            gate_settings = publication.verify_settings()

            # (a) digest currency from the SINGLE snapshot: stale -> 409, NO receipt.
            # (Checked before coherence so digest drift keeps its Phase-3
            # stale_review semantics instead of surfacing as a chain failure.)
            expected = publication.manifest["contract_sha256"]
            if snap.digest != expected:
                raise ServiceError(
                    409,
                    "stale_review",
                    "contract changed since publication; refresh and review the new digest",
                    manifest_sha256=expected,
                    current_sha256=snap.digest,
                    contract_path=publication.manifest["contract_path"],
                )
            publication.validate_coherence()

            # (a1) Phase 4 two-gate model (PROBE G): only the FIRST
            # repair-authorizing contract published as a GATE takes decisions.
            # Informational publications accept comments + Q&A, never approve/deny.
            contract = snap.document()
            publication_kind = publication.manifest.get("publication_kind", "gate")
            if action in ("approve", "deny") and not self._is_decision_gate(publication, contract, publication_kind):
                raise ServiceError(
                    409,
                    "not_gate_publication",
                    "this publication is informational (only the first repair-authorizing "
                    "contract as a gate publication takes decisions); comments and Q&A remain open",
                    publication_kind=publication_kind,
                    first_repair_authorizing=contract.get("first_repair_authorizing"),
                )

            # (b) HIGH 5: validate the COMPLETE incoming action BEFORE any
            # idempotency/decision-once lookup. A retry with wrong vocabulary
            # is 422 even after the first decision succeeded.
            if action == "approve":
                required = substitute_vocab_placeholder(
                    contract.get("required_approval_vocabulary", ""), expected
                )
                if body.get("required_approval_vocabulary") != required:
                    raise ServiceError(
                        422,
                        "vocabulary_mismatch",
                        "required_approval_vocabulary does not match the contract's approval phrase",
                    )
            if action == "comment" and not (isinstance(body.get("comment"), str) and body["comment"].strip()):
                raise ServiceError(422, "comment_required", "a comment action requires non-empty comment text")

            # (c) idempotency: same key + publication + action -> existing receipt.
            if idem_key:
                prior = self._lookup_receipt_by_key(publication, idem_key)
                if prior is not None:
                    if prior.get("action") != action:
                        raise ServiceError(
                            409,
                            "idempotency_key_conflict",
                            "this idempotency key was already used for a different action",
                            prior_action=prior.get("action"),
                        )
                    return 200, {"status": "idempotent", "receipt": self._receipt_public(prior)}

            # (d) A gate is decided once: an existing approve/deny receipt makes
            # the same action idempotent and the opposite action a conflict.
            # This also collapses concurrent duplicate decisions into one receipt.
            if action in ("approve", "deny"):
                receipt_list, _warnings = receipts_mod.list_receipts(publication.receipt_dir)
                existing = [r for r in receipt_list if r.get("action") in ("approve", "deny")]
                if existing:
                    prior = existing[0]
                    if prior.get("action") != action:
                        raise ServiceError(
                            409,
                            "decision_conflict",
                            f"this publication already has a {prior.get('action')} decision",
                            prior_receipt_id=prior.get("receipt_id"),
                        )
                    return 200, {"status": "idempotent", "receipt": self._receipt_public(prior)}

            # (e) authorize the receipt from manifest-bound contract data —
            # contract identity fields are NEVER taken from the client.
            receipt = {
                "schema": receipts_mod.RECEIPT_SCHEMA_CONST,
                "schema_version": "1.1",
                "receipt_id": receipts_mod.new_receipt_id(),
                "publication_id": publication_id,
                "action": action,
                "actor": actor,
                "created_at_utc": now_utc(),
                "contract_path": publication.manifest["contract_path"],
                "contract_sha256": expected,
                "contract_schema": publication.manifest["contract_schema"],
                "contract_version": publication.manifest["contract_version"],
                "gate_kind": publication.manifest["gate_kind"],
            }
            if action == "approve":
                receipt["required_approval_vocabulary"] = substitute_vocab_placeholder(
                    contract.get("required_approval_vocabulary", ""), expected
                )
            if isinstance(body.get("comment"), str) and body["comment"].strip():
                receipt["comment"] = body["comment"]
            if isinstance(idem_key, str) and idem_key.strip():
                # The receipt schema is additionalProperties:false; the
                # idempotency key rides in the sanctioned x_extension namespace.
                receipt["x_extension"] = {"idempotency_key": idem_key}

            # (f) BLOCKER 2 + R2: the contract must be UNCHANGED since the
            # snapshot read. Two checks right before publishing:
            #   1. re-stat the retained descriptor (same-inode mutation), and
            #   2. re-resolve the contract's CURRENT identity through a
            #      contained parent fd (O_NOFOLLOW) and re-hash the bytes —
            #      catches an atomic rename-swap of the pathname, which an
            #      fd-stat alone cannot see.
            if not snap.unchanged_since_read():
                raise ServiceError(
                    409,
                    "stale_review",
                    "contract changed during review; refresh and review the new digest",
                    manifest_sha256=expected,
                    contract_path=publication.manifest["contract_path"],
                )
            try:
                parent_fd, contract_name = publication.open_contract_parent_fd()
                try:
                    path_current = snap.still_current_at_path(parent_fd, contract_name)
                finally:
                    os.close(parent_fd)
            except ServiceError:
                path_current = False  # component vanished or became a symlink
            if not path_current:
                raise ServiceError(
                    409,
                    "stale_review",
                    "contract changed during review; refresh and review the new digest",
                    manifest_sha256=expected,
                    contract_path=publication.manifest["contract_path"],
                )

            # (g) atomic immutable write THROUGH a no-symlink descriptor walk
            # (R1: never touch the receipt path by name after validation);
            # failure -> 500, never claim success.
            try:
                receipts_fd = publication.open_receipts_fd(create=True)
                try:
                    receipts_mod.write_receipt_fd(receipt, receipts_fd)
                finally:
                    os.close(receipts_fd)
            except receipts_mod.ReceiptError as exc:
                raise ServiceError(500, "receipt_write_failed", f"receipt was NOT persisted: {exc}")
            except ServiceError as exc:
                raise ServiceError(exc.status if exc.status == 422 else 500, "path_containment", exc.message)
            return 201, {"status": "written", "receipt": self._receipt_public(receipt)}

    def auto_publish(self, publication_id: str, body: dict | None = None) -> tuple[int, dict]:
        """Mint a settings-authorized receipt when bound gate settings are auto.

        Authorization is never accepted from the client — the resolved
        settings file itself (path + digest re-verified against the manifest
        here) is the only source. Auto-configured gate publications mint an
        ``approve`` receipt; informational publications retain their ``comment``
        receipt. An owner-authorized gate remains a hard 403. The receipt's
        auto provenance (authorization_source + fix_settings_*) makes it
        structurally distinguishable from a human decision.
        """
        publication = self.get_publication(publication_id)
        reserved = {"authorization_source", "fix_settings_path", "fix_settings_sha256"}
        if isinstance(body, dict) and reserved.intersection(body):
            raise ServiceError(
                422, "reserved_field",
                f"auto-publish body keys are reserved for the server: {sorted(reserved.intersection(body))}",
            )
        with self._lock, publication.snapshot() as snap:
            gate_settings = publication.verify_settings()
            expected = publication.manifest["contract_sha256"]
            if snap.digest != expected:
                raise ServiceError(
                    409, "stale_review",
                    "contract changed since publication; refresh and review the new digest",
                    manifest_sha256=expected,
                    current_sha256=snap.digest,
                    contract_path=publication.manifest["contract_path"],
                )
            publication.validate_coherence()
            publication_kind = publication.manifest.get("publication_kind", "gate")
            settings_key = publication.gate_settings_key()
            document = snap.document()
            is_within_scope_revision = (
                publication_kind == "informational"
                and publication.manifest.get("contract_schema")
                == "jswarm.fix-decisions.fix-contract/v1"
                and document.get("first_repair_authorizing") is False
            )
            if is_within_scope_revision:
                mode = gate_settings.within_scope_revision_mode(settings_key)
                authority_key = settings_mod.REVISION_KEY
            else:
                mode = gate_settings.gate_mode(settings_key)
                authority_key = settings_key
            if mode != "auto":
                raise ServiceError(
                    403, "gate_requires_owner",
                    f"publication authority {authority_key} is {mode!r} in the bound fix settings; "
                    "this publication requires a human owner decision",
                    gate_settings_key=authority_key,
                    mode=mode,
                )
            # Decision-once: an existing settings-authorized receipt is
            # idempotent; a HUMAN decision is never overridden by auto.
            receipt_list, _warnings = receipts_mod.list_receipts(publication.receipt_dir)
            existing_auto = [r for r in receipt_list if r.get("authorization_source") == "auto"]
            if existing_auto:
                return 200, {"status": "idempotent", "receipt": self._receipt_public(existing_auto[0])}
            if any(r.get("action") in ("approve", "deny") for r in receipt_list):
                raise ServiceError(
                    409, "decision_conflict",
                    "this publication already carries a human decision; "
                    "auto-publication never overrides it",
                )

            receipt = {
                "schema": receipts_mod.RECEIPT_SCHEMA_CONST,
                "schema_version": "1.1",
                "receipt_id": receipts_mod.new_receipt_id(),
                "publication_id": publication_id,
                "action": "comment" if publication_kind == "informational" else "approve",
                "actor": "settings",
                "created_at_utc": now_utc(),
                "contract_path": publication.manifest["contract_path"],
                "contract_sha256": expected,
                "contract_schema": publication.manifest["contract_schema"],
                "contract_version": publication.manifest["contract_version"],
                "gate_kind": publication.manifest["gate_kind"],
                "comment": "settings-authorized informational publication",
                # The auto provenance block — structurally distinct from a
                # human approval (which carries required_approval_vocabulary).
                "authorization_source": "auto",
                "fix_settings_path": publication.manifest["fix_settings_path"],
                "fix_settings_sha256": publication.manifest["fix_settings_sha256"],
            }
            if receipt["action"] == "approve":
                receipt["required_approval_vocabulary"] = substitute_vocab_placeholder(
                    document.get("required_approval_vocabulary", ""), expected
                )

            # Same pre-publish currency checks as human actions.
            if not snap.unchanged_since_read():
                raise ServiceError(
                    409, "stale_review",
                    "contract changed during review; refresh and review the new digest",
                    manifest_sha256=expected,
                    contract_path=publication.manifest["contract_path"],
                )
            try:
                parent_fd, contract_name = publication.open_contract_parent_fd()
                try:
                    path_current = snap.still_current_at_path(parent_fd, contract_name)
                finally:
                    os.close(parent_fd)
            except ServiceError:
                path_current = False
            if not path_current:
                raise ServiceError(
                    409, "stale_review",
                    "contract changed during review; refresh and review the new digest",
                    manifest_sha256=expected,
                    contract_path=publication.manifest["contract_path"],
                )

            try:
                receipts_fd = publication.open_receipts_fd(create=True)
                try:
                    receipts_mod.write_receipt_fd(receipt, receipts_fd)
                finally:
                    os.close(receipts_fd)
            except receipts_mod.ReceiptError as exc:
                raise ServiceError(500, "receipt_write_failed", f"receipt was NOT persisted: {exc}")
            except ServiceError as exc:
                raise ServiceError(exc.status if exc.status == 422 else 500, "path_containment", exc.message)
            log.info(
                "auto_published publication_id=%s gate_settings_key=%s settings=%s",
                publication_id, settings_key, publication.manifest["fix_settings_path"],
            )
            return 201, {"status": "written", "receipt": self._receipt_public(receipt)}

    def _lookup_receipt_by_key(self, publication: Publication, idem_key: str) -> dict | None:
        # In-memory first (fast path), then durable scan (restart-safe).
        cached = self._idempotency.get(f"{publication.publication_id}\x00{idem_key}")
        if cached is not None:
            return cached
        receipt_list, _warnings = receipts_mod.list_receipts(publication.receipt_dir)
        for receipt in receipt_list:
            if (receipt.get("x_extension") or {}).get("idempotency_key") == idem_key:
                self._idempotency[f"{publication.publication_id}\x00{idem_key}"] = receipt
                return receipt
        return None

    @staticmethod
    def _receipt_public(receipt: dict) -> dict:
        out = {
            "receipt_id": receipt["receipt_id"],
            "action": receipt["action"],
            "publication_id": receipt["publication_id"],
            "contract_sha256": receipt["contract_sha256"],
            "created_at_utc": receipt["created_at_utc"],
        }
        # Round-2 SMALL (a): settings-authorized receipts surface their auto
        # provenance everywhere a receipt is listed, so UI/orchestrator can
        # distinguish them from human decisions (which never carry it).
        for key in ("authorization_source", "fix_settings_path", "fix_settings_sha256"):
            if key in receipt:
                out[key] = receipt[key]
        return out

    # -- POST threads -------------------------------------------------------

    def create_thread(self, publication_id: str, body: dict) -> tuple[int, dict]:
        publication = self.get_publication(publication_id)
        message_body = body.get("body")
        if not isinstance(message_body, str) or not message_body.strip():
            raise ServiceError(400, "body_required", "first message body is required")
        anchor = body.get("anchor")
        if anchor is not None and (not isinstance(anchor, dict) or not anchor):
            raise ServiceError(400, "bad_anchor", "anchor must be a non-empty object or omitted")

        threads_dir = self._require_threads_dir_contained()
        with publication.snapshot() as snap:
            # Digest-bound thread creation (HIGH 3): refuse to anchor a new
            # thread to a contract whose live digest no longer matches.
            expected = publication.manifest["contract_sha256"]
            if snap.digest != expected:
                raise ServiceError(
                    409, "stale_review",
                    "contract changed since publication; cannot anchor a new thread",
                    manifest_sha256=expected,
                    current_sha256=snap.digest,
                )
            ticket = snap.document().get("ticket", "unknown")

        thread_id = new_thread_id()
        asked_by = body.get("asked_by") or "owner"
        # R3: server identity is NEVER client-supplied. Reserved anchor keys
        # are rejected outright (fail closed) rather than silently overridden;
        # only non-reserved contextual fields merge through.
        reserved = {"publication_id", "contract_path", "contract_sha256"}
        if isinstance(anchor, dict) and reserved.intersection(anchor):
            raise ServiceError(
                422, "reserved_anchor_key",
                f"anchor keys are reserved for the server: {sorted(reserved.intersection(anchor))}",
            )
        # Merge order per the stated contract (round 4 (2)): client
        # NON-reserved contextual fields first, server identity LAST and
        # unconditionally. (Reserved keys were already rejected above, so the
        # identity can never be overwritten — this makes it literally true.)
        thread_anchor = dict(anchor) if isinstance(anchor, dict) else {}
        thread_anchor.update({
            "publication_id": publication_id,
            "contract_path": publication.manifest["contract_path"],
            "contract_sha256": expected,
        })
        thread = {
            "schema": "jswarm.fix-decisions.qa-thread/v1",
            "schema_version": "1.0",
            "thread_id": thread_id,
            "ticket": ticket,
            "created_at_utc": now_utc(),
            "status": "open",
            "asked_by": asked_by,
            "anchor": thread_anchor,
            "messages": [
                {
                    "message_id": f"msg-{uuid.uuid4().hex[:12]}",
                    "author_role": "owner",
                    "created_at_utc": now_utc(),
                    "body": message_body,
                }
            ],
        }
        threads_fd = self._open_threads_fd()
        try:
            write_thread_fd(threads_fd, thread)
        finally:
            os.close(threads_fd)
        log.info("thread_created publication_id=%s thread_id=%s", publication_id, thread_id)
        return 201, {"status": "created", "thread_id": thread_id}

    def _require_anchored_thread_current(self, thread: dict) -> None:
        """Anchored thread writes verify the recorded digest still matches the
        live recomputed digest; mismatch -> 409 stale, no write (HIGH 3)."""
        anchor = thread.get("anchor", {}) or {}
        recorded = anchor.get("contract_sha256")
        if not recorded:
            return  # free-floating (ticket-level) thread: exempt
        publication_id = anchor.get("publication_id")
        publication = self._publications.get(publication_id)
        if publication is None:
            raise ServiceError(409, "stale_review", "thread's publication is no longer served")
        current = publication.recomputed_digest()
        if current != recorded:
            raise ServiceError(
                409, "stale_review",
                "contract changed since this thread was anchored; thread stays on its original digest",
                manifest_sha256=recorded,
                current_sha256=current,
            )

    def append_message(self, thread_id: str, body: dict) -> tuple[int, dict]:
        if not THREAD_ID_RE.match(thread_id or ""):
            raise ServiceError(400, "bad_thread_id", "invalid thread id")
        message_body = body.get("body")
        if not isinstance(message_body, str) or not message_body.strip():
            raise ServiceError(400, "body_required", "message body is required")
        threads_fd = self._open_threads_fd()
        try:
            with self._lock:
                thread = read_thread_fd(threads_fd, thread_id)
                if thread.get("status") == "closed":
                    raise ServiceError(409, "thread_closed", "cannot append to a closed thread")
                self._require_anchored_thread_current(thread)
                in_reply_to = body.get("in_reply_to")
                if in_reply_to is not None:
                    known = {m["message_id"] for m in thread["messages"]}
                    if in_reply_to not in known:
                        raise ServiceError(422, "bad_in_reply_to", f"in_reply_to {in_reply_to!r} does not match any message")
                message = {
                    "message_id": f"msg-{uuid.uuid4().hex[:12]}",
                    "author_role": body.get("author_role") or "orchestrator",
                    "created_at_utc": now_utc(),
                    "body": message_body,
                }
                if in_reply_to:
                    message["in_reply_to"] = in_reply_to
                thread["messages"].append(message)
                if message["author_role"] != "owner" and thread.get("status") == "open":
                    thread["status"] = "answered"
                    thread["answered_by"] = message["author_role"]
                write_thread_fd(threads_fd, thread)
            log.info("thread_message_appended thread_id=%s message_id=%s", thread_id, message["message_id"])
            return 201, {"status": "appended", "message_id": message["message_id"], "messages": len(thread["messages"])}
        finally:
            os.close(threads_fd)

    def transition_thread(self, thread_id: str, body: dict) -> tuple[int, dict]:
        if not THREAD_ID_RE.match(thread_id or ""):
            raise ServiceError(400, "bad_thread_id", "invalid thread id")
        new_status = body.get("status")
        if new_status not in STATUS_ORDER:
            raise ServiceError(400, "bad_status", f"unknown status: {new_status!r}")
        threads_fd = self._open_threads_fd()
        try:
            with self._lock:
                thread = read_thread_fd(threads_fd, thread_id)
                self._require_anchored_thread_current(thread)
                current = thread.get("status")
                if STATUS_ORDER[new_status] != STATUS_ORDER[current] + 1:
                    raise ServiceError(
                        409,
                        "invalid_transition",
                        f"transition {current}->{new_status} is not allowed (only open->answered->closed)",
                    )
                old = current
                thread["status"] = new_status
                write_thread_fd(threads_fd, thread)
            log.info("thread_status thread_id=%s from=%s to=%s", thread_id, old, new_status)
            return 200, {"status": new_status}
        finally:
            os.close(threads_fd)


# ---------------------------------------------------------------------------
# HTTP layer


def make_handler(service: DecisionService, dist_dir: Path | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fix-decisions/1.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # silence default stderr logging
            pass

        # -- helpers --------------------------------------------------------

        def _send_json(self, status: int, payload: dict):
            body = json.dumps(payload, indent=2).encode("utf-8")
            # A client that disconnects mid-request (e.g. one of several racing
            # concurrent decisions retrying) must not crash the handler thread
            # with a broken-pipe/Bad-file-descriptor error that then also
            # poisons the socket close path in process_request_thread — the
            # outcome was already logged; the write failure is the client's.
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, OSError):
                log.info("response_write_failed client disconnected before response")

        def _read_body(self) -> dict:
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                raise ServiceError(400, "bad_transfer_encoding", "chunked request bodies are not supported")
            length_header = self.headers.get("Content-Length")
            if length_header is None:
                raise ServiceError(411, "length_required", "Content-Length is required")
            try:
                length = int(length_header)
            except ValueError:
                raise ServiceError(400, "bad_content_length", "invalid Content-Length")
            if length < 0:
                raise ServiceError(400, "bad_content_length", "negative Content-Length")
            if length > service.body_limit:
                raise ServiceError(413, "body_too_large", f"request body exceeds {service.body_limit} bytes")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                raise ServiceError(400, "empty_body", "a JSON body is required")
            try:
                document = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ServiceError(400, "bad_json", f"invalid JSON: {exc}")
            if not isinstance(document, dict):
                raise ServiceError(400, "bad_json", "JSON body must be an object")
            return document

        def _check_origin(self):
            if not service.origin_allowed(self.headers.get("Origin"), self.headers.get("Host")):
                raise ServiceError(403, "origin_forbidden", "Origin is not on the allowlist or same-origin host")

        def _handle(self, method: str):
            # access log carries ids/actions/outcomes, NEVER comment or question text
            publication_id = None
            receipt_id = None
            action = None
            try:
                if method == "POST":
                    self._check_origin()
                path = unquote(self.path.split("?", 1)[0]).rstrip("/") or "/"
                segments = [s for s in path.split("/") if s]
                if len(segments) >= 3 and segments[:2] == ["api", "publications"]:
                    publication_id = segments[2]
                body = self._read_body() if method == "POST" else None
                if body is not None:
                    action = body.get("action") if isinstance(body.get("action"), str) else None
                routed = self._route(method, segments, body)
                if routed is None:  # static response was written directly
                    return
                status, payload = routed
                receipt_id = (payload.get("receipt") or {}).get("receipt_id") if isinstance(payload, dict) else None
                outcome = payload.get("status", "ok") if isinstance(payload, dict) else "ok"
                self._send_json(status, payload)
                log.info(
                    "access method=%s path=%s publication_id=%s receipt_id=%s action=%s outcome=%s error_type=",
                    method, path, publication_id, receipt_id, action, outcome,
                )
            except ServiceError as exc:
                status, payload = exc.status, {"error": {"code": exc.code, "message": exc.message, **exc.detail}}
                receipt_id = None
                self._send_json(status, payload)
                log.error(
                    "access method=%s path=%s publication_id=%s receipt_id=%s action=%s outcome=error error_type=%s",
                    method, self.path.split("?", 1)[0], publication_id, receipt_id, action, exc.code,
                )
            except Exception as exc:  # pragma: no cover - defensive
                log.error("access method=%s path=%s outcome=error error_type=internal detail=%s", method, self.path, exc)
                try:
                    self._send_json(500, {"error": {"code": "internal_error", "message": "internal server error"}})
                except Exception:
                    pass

        _CONTENT_TYPES = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
        }

        def _serve_static(self, path: str):
            active_dist = service.current_static_root()
            if active_dist is None:
                raise ServiceError(404, "not_found", "no route and no dist directory configured")
            rel = path.lstrip("/") or "index.html"
            target = (active_dist / rel).resolve()
            # Astro emits directory routes as <route>/index.html. Browser-facing
            # links use the natural trailing-slash form (/review/<id>/, /uat/),
            # so resolve directories to their index rather than requiring callers
            # to know the generated filename.
            if target.is_dir():
                target = (target / "index.html").resolve()
            if not str(target).startswith(str(active_dist.resolve()) + os.sep) or not target.is_file():
                raise ServiceError(404, "not_found", f"no such file: {rel}")
            payload = target.read_bytes()
            ctype = self._CONTENT_TYPES.get(target.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            log.info("access method=GET path=%s outcome=static", path)
            return None

        def _route(self, method: str, segments: list[str], body: dict | None) -> tuple[int, dict]:
            if method == "GET":
                if segments == ["api", "publications"]:
                    return 200, service.publication_summaries()
                if len(segments) == 3 and segments[:2] == ["api", "publications"]:
                    return 200, service.publication_detail(segments[2])
                if segments == ["api", "uat-rounds"]:
                    return 200, service.active_round_summaries()
                if len(segments) >= 3 and segments[:2] == ["api", "uat-rounds"]:
                    return 200, service._round_view("/".join(segments[2:]))
                if segments == ["api", "health"]:
                    return 200, {"status": "ok", "publications": len(service.publications), "generation_id": service.generation_id, "last_reload_status": service.last_reload_status, "last_reload_error": service.last_reload_error}
                return self._serve_static(self.path.split("?", 1)[0])
            if method == "POST" and body is not None:
                if len(segments) >= 4 and segments[:2] == ["api", "uat-rounds"] and segments[-1] == "reset":
                    return service.reset_practice_round("/".join(segments[2:-1]))
                if len(segments) >= 5 and segments[:2] == ["api", "uat-rounds"] and segments[-2:] == ["feedback", "submit"]:
                    return service.submit_active_round_feedback("/".join(segments[2:-2]), body)
                if len(segments) >= 4 and segments[:2] == ["api", "uat-rounds"] and segments[-1] == "status":
                    return service.update_active_round_status("/".join(segments[2:-1]), body)
                if len(segments) >= 4 and segments[:2] == ["api", "uat-rounds"] and segments[-1] == "feedback":
                    return service.update_active_round_feedback("/".join(segments[2:-1]), body)
                if len(segments) == 4 and segments[:2] == ["api", "publications"] and segments[3] == "actions":
                    return service.apply_action(segments[2], body)
                if len(segments) == 4 and segments[:2] == ["api", "publications"] and segments[3] == "auto-publish":
                    return service.auto_publish(segments[2], body)
                if len(segments) == 4 and segments[:2] == ["api", "publications"] and segments[3] == "threads":
                    return service.create_thread(segments[2], body)
                if len(segments) == 4 and segments[0] == "api" and segments[1] == "threads" and segments[3] == "messages":
                    return service.append_message(segments[2], body)
                if len(segments) == 4 and segments[0] == "api" and segments[1] == "threads" and segments[3] == "status":
                    return service.transition_thread(segments[2], body)
            raise ServiceError(404, "not_found", f"no route: {method} /{'/'.join(segments)}")

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def handle_one_request(self):
            # The same synchronized-connect/close race that makes a client
            # see RemoteDisconnected can surface here as a connection reset
            # while socketserver drains the socket during shutdown_request —
            # stdlib then logs an unhandled-thread exception (EBADF/ECONNRESET)
            # even though the request was served and its receipt (if any) is
            # durable. Swallow transport-level teardown errors only; protocol
            # errors still propagate.
            try:
                super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                log.info("connection teardown race ignored detail=%s", exc)

    return Handler


def serve(
    publications_dir: Path,
    threads_dir: Path,
    approved_roots: list[Path],
    dist_dir: Path | None = None,
    port: int = 0,
    bind_host: str = DEFAULT_BIND_HOST,
    body_limit: int = DEFAULT_BODY_LIMIT,
    contract_size_limit: int = DEFAULT_CONTRACT_LIMIT,
    allowed_origins: list[str] | None = None,
    publications: dict[str, Publication] | None = None,
    invalid_manifests: int = 0,
    manifest_warnings: list[str] | None = None,
    active_round_sources: list[dict] | None = None,
    config_path: Path | None = None,
) -> ThreadingHTTPServer:
    """Build a ThreadingHTTPServer on bind_host:port (port 0 = ephemeral)."""
    service = DecisionService(
        publications_dir=publications_dir,
        threads_dir=threads_dir,
        approved_roots=approved_roots,
        body_limit=body_limit,
        contract_size_limit=contract_size_limit,
        allowed_origins=allowed_origins,
        publications=publications,
        invalid_manifests=invalid_manifests,
        manifest_warnings=manifest_warnings,
        active_round_sources=active_round_sources,
        config_path=config_path,
        dist_dir=dist_dir() if callable(dist_dir) else dist_dir,
    )
    handler = make_handler(service, dist_dir)

    class _Server(ThreadingHTTPServer):
        # The stdlib default accept backlog (5) drops synchronized connect
        # storms (e.g. concurrent decision retries): the client sees
        # RemoteDisconnected with no response. 128 is the conventional
        # listen backlog for a small local service.
        request_queue_size = 128

        def server_bind(self):
            # HTTPServer performs reverse DNS just to label server_name. That
            # lookup can stall local startup on hosts with unavailable DNS.
            # Nothing here needs a canonical hostname; retain the bound address.
            TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

        def close_request(self, request):
            # Double-close guard (round-2 residue): under the loopback
            # connect/close race the connection fd can already be closed by
            # the teardown path when socketserver's process_request_thread
            # calls close_request — stdlib then raises EBADF inside the
            # worker thread, which surfaces as an unhandled thread exception.
            # Closing an already-closed connection is a no-op, not an error.
            try:
                super().close_request(request)
            except OSError:
                pass

    httpd = _Server((bind_host, port), handler)
    httpd.service = service
    httpd.daemon_threads = True
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="jswarm.portal.server",
        description="Fix-decision receipt service (stdlib only). Binds 0.0.0.0 (all interfaces).",
    )
    parser.add_argument("--config", default=None,
                        help="JSON config file, rendered per-machine by jswarm.portal.render_portal_config "
                             "(default location: ~/.jswarm/decision-review/config.json); CLI flags override it")
    parser.add_argument("--dist", default=None, help="review UI dist directory to serve statically (optional)")
    parser.add_argument("--publications", default=None, help="directory of publication manifests (must be inside an approved root)")
    parser.add_argument("--threads", default=None, help="Q&A threads directory (default: <publications>/.threads)")
    parser.add_argument("--approved-roots", action="append", default=None,
                        help="approved absolute repository root (repeatable); every manifest-controlled path must resolve inside one")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    config: dict = {}
    if args.config:
        config = load_config(Path(args.config))

    def config_or_flag(flag_value, config_key):
        """CLI flag wins when given; else the config file; else None."""
        return flag_value if flag_value is not None else config.get(config_key)

    publications_value = config_or_flag(args.publications, "publications_dir")
    if not publications_value:
        parser.error("--publications (or config publications_dir) is required")
    approved_roots_value = config_or_flag(args.approved_roots, "approved_roots")
    if not approved_roots_value:
        parser.error("--approved-roots (or config approved_roots) is required — BLOCKER 1")
    port = config_or_flag(args.port, "port")
    if port is None:
        port = DEFAULT_PORT

    publications_dir = Path(publications_value).resolve()
    threads_value = config_or_flag(args.threads, "threads_dir")
    threads_dir = (Path(threads_value).resolve() if threads_value else publications_dir / ".threads")
    approved_roots = [Path(r).resolve() for r in approved_roots_value]
    dist_value = config_or_flag(args.dist, "dist_dir")
    dist_dir = Path(dist_value).resolve() if dist_value else None
    store = Path(
        config.get(
            "service_store_root",
            str(jswarm_home() / ".jswarm" / "decision-review"),
        )
    ).resolve()
    store.mkdir(parents=True, exist_ok=True)

    def current_dist() -> Path | None:
        current = store / "current.json"
        if not current.is_file():
            return dist_dir
        try:
            return Path(json.loads(current.read_text(encoding="utf-8"))["generation_path"]) / "dist"
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None

    def current_generation_id() -> str | None:
        try:
            return json.loads((store / "current.json").read_text(encoding="utf-8")).get("generation_id")
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def current_generation_publication_ids() -> set[str] | None:
        current = store / "current.json"
        if not current.is_file():
            return None  # legacy scan mode
        try:
            pointer = json.loads(current.read_text(encoding="utf-8"))
            metadata = json.loads((Path(pointer["generation_path"]) / "generation.json").read_text(encoding="utf-8"))
            return {str(value) for value in metadata.get("publications", [])}
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ServiceError(500, "generation_metadata_invalid", f"cannot read active generation metadata: {exc}") from exc

    allowed_origins = config.get("origin_allowlist")
    httpd = serve(
        publications_dir=publications_dir,
        threads_dir=threads_dir,
        approved_roots=approved_roots,
        dist_dir=current_dist,
        port=port,
        bind_host=config.get("bind_host", DEFAULT_BIND_HOST) if isinstance(config, dict) else DEFAULT_BIND_HOST,
        allowed_origins=allowed_origins,
        active_round_sources=config.get("active_round_sources"),
        config_path=Path(args.config) if args.config else None,
    )
    def reload_worker() -> None:
        try:
            httpd.service.reload_publications(current_generation_id(), current_generation_publication_ids(), current_dist())
        except Exception as exc:  # retain the last known generation on bad candidates
            httpd.service.last_reload_status, httpd.service.last_reload_error = "error", str(exc)
            log.exception("generation reload failed")

    def request_reload(_signum, _frame) -> None:
        threading.Thread(target=reload_worker, daemon=True).start()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGHUP, request_reload)
    httpd.service.reload_publications(current_generation_id(), current_generation_publication_ids(), current_dist())
    (store / "service.pid").write_text(f"{os.getpid()}\n")
    bound_host, bound_port = httpd.server_address[:2]
    log.info("listening host=%s port=%s", bound_host, bound_port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
