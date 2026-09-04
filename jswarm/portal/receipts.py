"""Phase 3 — atomic, immutable decision-receipt store.

A receipt file is named ``<created_at_utc compact>-<receipt_id>.json`` and is
published once via the :mod:`jswarm.jinfra.lifecycle` atomic-write discipline:
temp file created ``O_EXCL`` relative to an open directory descriptor, bytes
flushed + fsynced BEFORE the name becomes visible, published with ``os.link``
(fails ``EEXIST`` on an existing destination — never replaces), directory
fsynced after, temp unlinked. A crash at any point leaves either nothing or an
ignored temp file; a committed receipt is never truncated or overwritten.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from jswarm.portal import validate as validate_mod

RECEIPT_SCHEMA_CONST = "jswarm.fix-decisions.decision-receipt/v1"
# created_at_utc is RFC3339 UTC; the filename uses the compact no-punctuation form.
_TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$")
_FILENAME_RE = re.compile(r"^(\d{14})-(.+)\.json$")


class ReceiptError(Exception):
    """Base error for the receipt store."""


class ReceiptInvalidError(ReceiptError):
    """The receipt does not validate against the decision-receipt schema."""


class ReceiptExistsError(ReceiptError):
    """A receipt with this filename is already committed; refusing to replace."""


def new_receipt_id() -> str:
    """A fresh receipt id: ``receipt_<32 hex>``."""
    return f"receipt_{uuid.uuid4().hex}"


def compact_timestamp(created_at_utc: str) -> str:
    """``2026-08-27T15:04:05Z`` -> ``20260827150405`` (fractional seconds dropped)."""
    match = _TS_RE.match(created_at_utc or "")
    if not match:
        raise ReceiptInvalidError(f"created_at_utc is not RFC3339 UTC: {created_at_utc!r}")
    return "".join(match.groups())


def receipt_filename(receipt: dict) -> str:
    receipt_id = receipt.get("receipt_id", "")
    if not receipt_id or not receipt_id.startswith("receipt_"):
        raise ReceiptInvalidError(f"bad receipt_id: {receipt_id!r}")
    return f"{compact_timestamp(receipt.get('created_at_utc', ''))}-{receipt_id}.json"


def validate_receipt(receipt: dict) -> None:
    """Schema-validate an in-memory receipt dict; raise ReceiptInvalidError otherwise."""
    valid, messages = validate_mod.validate_document(receipt, "receipt")
    if not valid:
        raise ReceiptInvalidError("; ".join(messages))


def receipt_path(receipt_dir: Path, receipt_id: str) -> Path | None:
    """Locate the committed file for a receipt id, or None."""
    for path in sorted(receipt_dir.glob(f"*-{receipt_id}.json")):
        return path
    return None


class ReceiptContainmentError(ReceiptError):
    """A directory component is (or became) a symlink; refusing to follow it."""


def write_receipt(receipt: dict, receipt_dir: Path) -> Path:
    """Atomically publish an immutable receipt; return its committed path.

    Validates against the decision-receipt schema BEFORE any write. The
    publication is no-replace: a committed filename raises ReceiptExistsError
    and the existing bytes are untouched.
    """
    validate_receipt(receipt)
    receipt_dir = Path(receipt_dir)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    filename = receipt_filename(receipt)
    dir_fd = os.open(receipt_dir, os.O_RDONLY)
    try:
        write_receipt_fd(receipt, dir_fd, filename)
    finally:
        os.close(dir_fd)
    return receipt_dir / filename


def write_receipt_fd(receipt: dict, dir_fd: int, filename: str | None = None) -> None:
    """Publish an immutable receipt relative to an ALREADY-VALIDATED directory
    descriptor (round 3 R1: the caller establishes containment with an
    O_NOFOLLOW component walk and this function never touches a path by name).

    Durability ordering: bytes fsynced before the name becomes visible,
    directory fsynced after the link, and the directory fsynced once more
    after the temp unlink so cleanup is durable too.
    """
    if filename is None:
        filename = receipt_filename(receipt)
    temp_name = f".tmp-receipt-{uuid.uuid4().hex}.json"
    fd = os.open(temp_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644, dir_fd=dir_fd)
    temp_linked = False
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_name, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            temp_linked = True
        except FileExistsError as error:
            raise ReceiptExistsError(
                f"refusing to write {filename}: a committed receipt with that "
                "timestamp+id already exists; receipts are immutable."
            ) from error
        os.fsync(dir_fd)
    except BaseException:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise
    # The receipt is committed and durable; tidy the temp hardlink (M8) and
    # fsync the directory once more so the cleanup itself is durable.
    if temp_linked:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass  # already gone (crash-recovery race); harmless
        os.fsync(dir_fd)


def read_receipt(path: Path) -> dict:
    """Load and schema-validate one receipt file."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_receipt(document)
    return document


def list_receipts(receipt_dir: Path) -> tuple[list[dict], list[str]]:
    """Committed receipts sorted by filename, plus typed warnings for
    receipt-shaped files that fail to load or validate.

    A corrupt file never breaks the listing (jCritic advice): it is skipped
    and reported as a warning string so callers (the API) can surface it.
    """
    receipt_dir = Path(receipt_dir)
    if not receipt_dir.is_dir():
        return [], []
    out, warnings = [], []
    for path in sorted(receipt_dir.iterdir()):
        if not _FILENAME_RE.match(path.name):
            continue  # temp files and foreign files are never receipts
        try:
            out.append(read_receipt(path))
        except (json.JSONDecodeError, OSError, ReceiptError) as exc:
            warnings.append(f"{path.name}: unreadable/invalid receipt skipped: {exc}")
    return out, warnings
