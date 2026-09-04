#!/usr/bin/env python3
"""ctx v2 subagent-self-identity resolver (Phase 1).

Rewritten, cross-provider, subagent-self-resolving successor to an
earlier adopting project's v1 ``ctx-usage.py`` skill. Fixes v1's ``_find_current_transcript()``
newest-mtime-anywhere fallback, which could leak an orchestrator's (or an
unrelated session's) transcript into a subagent's reading. This module does
not port any v1 logic.

Chosen Layer-B mechanism (jArchitect Phase-0 decision, recorded at
``.jswarm/plans/TICKET-XXX/evidence/phase0/ARCH-layerb-decision.md``, countersigned
by jOracle at ``.jswarm/plans/TICKET-XXX/evidence/phase0/ORACLE-signoff.md``): S2
content-self-location with a mandatory caller-passed literal
``--identity-nonce``. A subagent proves which ``agent-<agentId>.jsonl`` file
under its own session's ``subagents/`` directory is its own by finding the
single file whose recent content contains its own invocation nonce.

Layer A (containment floor, always applies, independent of Layer B's outcome):
``CLAUDE_CODE_CHILD_SESSION == "1"`` => subagent role, bounded to reading only
``<session>/subagents/agent-*.jsonl``; otherwise => main/orchestrator role,
bounded to reading only ``<session>.jsonl``. All ambiguity resolves to
``unavailable``, never a guess.

Layer B (subagent, within the Layer-A-bounded set): the single
``agent-<agentId>.jsonl`` whose recent content contains the exact literal
nonce (as a full token, no substring/boundary collisions) wins, IF AND ONLY
IF: filename ``agentId`` == JSONL line ``agentId``, JSONL ``sessionId`` == the
env session id, JSONL ``isSidechain == true``, and the file's real path
resolves inside the session's ``subagents/`` directory (no symlink escape,
re-validated after open to close TOCTOU). Zero matches, more than one match,
or any of those invariants failing => ``unavailable``.

Usage lookup (``find_usage_for_resolution``): parses raw jAgentProxy
``[OUTCOME]`` key=value log lines using the same grammar as
``jswarm/jagentproxy_cost/aggregate.py:parse_outcome_line`` /
``_normalize_usage`` (reimplemented locally here: this module is
stdlib-only/no-dependency and must not import ``aggregate.py``, which pulls in
a non-stdlib YAML dependency, and must not reuse its *aggregated* terminal
rows, which drop ``agentId`` in favor of an ``agent`` slug). Lines are
filtered by the resolved ``session_id`` + ``agent_id`` + ``role``, then by
``usage_status == "exact"``. No usable line => ``unavailable``. The resolved
transcript path is a documented fallback/cross-check: Phase 1 does not
implement transcript-based usage extraction (no frozen regression test
requires that positive path), but it does check whether the fallback is even
reachable; if the transcript itself is missing, that is reported as the more
specific, more actionable failure.

Window-registry join (context-window sizing) and full provider %/statusLine
1M regression handling are explicitly Phase 2, not built here. ``window`` on
``UsageResult`` is left ``None``.

Phase 2 adds, on top of the Phase 1 identity resolver above:
cross-provider ``[OUTCOME]`` usage parsing that passes the raw ``class``
field through as ``provider`` (rather than bucketing it), a no-dependency
``model-windows.yaml`` line parser feeding ``resolve_model_window`` (env
override > registry lookup > ``[1m]`` heuristic > default), a
``transcript-jsonl`` fallback usage source for when the jAgentProxy OUTCOME
log is unavailable, the ``build_report``/``main`` ``--json`` CLI output
contract, and the ``JSWARM_CTX_WINDOW`` / ``JSWARM_CTX_TAIL_LINES`` env-key
split described on ``_resolve_tail_window`` below.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import stat
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class Resolution:
    """Result of resolving the calling agent's own identity (Phase 1 target shape).

    Attributes:
        status: ``"ok"`` or ``"unavailable"``.
        role: ``"subagent"`` or ``"main"``.
        session_id: The resolved Claude Code session id, or ``None`` if unresolved.
        agent_id: The resolved subagent's own ``agentId``, or ``None`` for main/unresolved.
        transcript_path: The single bounded transcript file the caller may read, or ``None``.
        reason: Human-readable explanation of the resolution step that failed, when
            ``status == "unavailable"`` (ties NFR-OBS: every unavailable names the
            failed step). ``None`` when ``status == "ok"``.
    """

    status: str
    role: str
    session_id: str | None
    agent_id: str | None
    transcript_path: Path | None
    reason: str | None


@dataclass(frozen=True)
class UsageResult:
    """Result of resolving cross-provider usage for an already-resolved identity.

    Attributes:
        status: ``"ok"`` or ``"unavailable"``.
        provider: Provider label. Phase 2: the raw jAgentProxy OUTCOME ``class``
            field value passed through verbatim (e.g. ``"openai-codex"``,
            ``"anthropic-direct"``) when present, else ``None``.
        model: The model id the usage record was reported against, or ``None``.
        context_tokens: Total context tokens (input + cache) for the latest terminal
            usage record, or ``None`` when unavailable.
        window: The model's context window size in tokens, or ``None`` when unavailable.
        reason: Human-readable explanation of the failed step, when
            ``status == "unavailable"``. ``None`` when ``status == "ok"``.
        context_pct: ``round(context_tokens / window * 100, 1)``, or ``None``
            when either input is unavailable. Phase 2.
        usage_source: Which source produced this result: one of
            ``"jagentproxy-outcome-log"``, ``"transcript-jsonl"``, or
            ``"unavailable"``. Phase 2; defaults to ``"unavailable"`` so
            Phase-1-shaped construction sites keep working unchanged.
        window_source: How ``window`` was resolved: one of ``"env"``,
            ``"registry"``, ``"heuristic-1m"``, ``"default"``, or ``None``
            when ``window`` itself is ``None``. Phase 2.
    """

    status: str
    provider: str | None
    model: str | None
    context_tokens: int | None
    window: int | None
    reason: str | None
    context_pct: float | None = None
    usage_source: str = "unavailable"
    window_source: str | None = None


# ---------------------------------------------------------------------------
# Layer A / Layer B: identity resolution
# ---------------------------------------------------------------------------

_DEFAULT_RECENT_WINDOW = 200
_NONCE_RE = re.compile(r"^ctx-nonce:[A-Za-z0-9_-]{22,24}$")
_AGENT_FILENAME_RE = re.compile(r"^agent-(.+)\.jsonl$")
_TOKEN_BOUNDARY_CHARS = "A-Za-z0-9_-"


def _env_get(env: Any, key: str) -> str | None:
    """Read exactly one allow-listed key from ``env`` via ``.get`` only.

    Never iterates ``env`` and never reads any key outside the fixed
    allow-list this module is authorized to touch (``CLAUDE_CODE_SESSION_ID``,
    ``CLAUDE_CODE_CHILD_SESSION``, ``JSWARM_CTX_WINDOW``,
    ``JSWARM_CTX_TAIL_LINES``, ``JSWARM_CTX_OUTCOME_LOG``,
    ``JSWARM_CTX_AGENT_ID``, ``JSWARM_CTX_MODEL_WINDOWS``,
    ``JSWARM_CTX_CREDENTIALS``, ``JSWARM_CTX_QUOTA_TIMEOUT``,
    ``JSWARM_CTX_CODEX_CREDENTIALS``, ``JARVISWARM_ROOT``, ``HOME``).
    ``JARVISWARM_ROOT`` and ``HOME`` (Phase 7) are read only by
    ``_default_outcome_log`` to resolve the default jAgentProxy OUTCOME log
    path when ``JSWARM_CTX_OUTCOME_LOG`` is unset. ``JSWARM_CTX_CREDENTIALS``
    and ``JSWARM_CTX_QUOTA_TIMEOUT`` (Phase 8) override the
    Anthropic OAuth credentials file path and the quota-fetch timeout used
    by ``_fetch_anthropic_usage``. ``JSWARM_CTX_CODEX_CREDENTIALS``
    (Phase 9) overrides the Codex/ChatGPT OAuth credentials file
    path used by ``_fetch_codex_usage`` in the same way. ``env`` may be a
    plain ``dict`` or any ``Mapping``-like object; this helper only ever
    calls ``.get``, never ``.keys()``/``.items()``/``.values()``/
    ``for k in env``.
    """

    return env.get(key)


def _unavailable_resolution(*, role: str, session_id: str | None, reason: str) -> Resolution:
    return Resolution(
        status="unavailable",
        role=role,
        session_id=session_id,
        agent_id=None,
        transcript_path=None,
        reason=reason,
    )


def _valid_nonce_grammar(nonce: str | None) -> bool:
    return isinstance(nonce, str) and bool(_NONCE_RE.fullmatch(nonce))


def _resolve_tail_window(env: Any) -> int:
    """Resolve the identity-lookup tail-line bound (Layer B recent-window size).

    Phase 2 renames this bound's primary env key to
    ``JSWARM_CTX_TAIL_LINES``. ``JSWARM_CTX_WINDOW`` is exclusively the
    model-context-window-tokens override read by ``resolve_model_window``
    below and must never influence this tail-line bound. The two keys are
    deliberately not aliased: reading ``JSWARM_CTX_WINDOW`` here would let a
    huge model-context-window override (e.g. ``1000000``) silently expand
    the identity-lookup search past the caller's intended recent-window,
    which is the dual-key collision this phase closes.

    Existence is probed with ``key in env`` (safe on both a plain ``dict``
    and a stricter allow-listing ``Mapping`` whose ``__contains__`` merely
    reports membership) before any ``.get()`` call, so this never triggers a
    "non-identity key accessed" guard for a key a caller's env genuinely does
    not carry.
    """

    raw: str | None = None
    if "JSWARM_CTX_TAIL_LINES" in env:
        raw = _env_get(env, "JSWARM_CTX_TAIL_LINES")
    if raw is None:
        return _DEFAULT_RECENT_WINDOW
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return _DEFAULT_RECENT_WINDOW
    return value if value > 0 else _DEFAULT_RECENT_WINDOW


def _is_escaped(entry: Path, allowed_root: Path) -> bool:
    """True if ``entry``'s real path is not contained within ``allowed_root``.

    Fails closed: any resolution error (broken symlink, permission issue) is
    treated as an escape. Resolving the real path is a metadata operation
    (readlink-equivalent); it does not read the target's content, so this
    check rejects a symlink escape without ever following/reading it.
    """

    try:
        resolved = entry.resolve(strict=True)
    except OSError:
        return True
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        return True
    return False


def _read_tail_lines_bounded(fh: Any, window: int) -> list[str]:
    """Stream ``fh`` line-by-line into a bounded ring buffer of ``window`` lines.

    Mechanical bound (jCritic P1 MAJOR): a whole-file ``readlines()`` would
    fully materialize an arbitrarily large transcript in memory just to keep
    its last few lines. Instead this reads one line at a time via explicit
    ``.readline()`` calls into a ``collections.deque(maxlen=window)`` so at
    most ``window`` lines are ever retained, regardless of file size.
    ``window <= 0`` (defensive; ``_resolve_window`` never actually returns a
    non-positive value) preserves the prior "keep everything" fallback.
    """

    maxlen = window if window > 0 else None
    tail: collections.deque[str] = collections.deque(maxlen=maxlen)
    while True:
        line = fh.readline()
        if not line:
            break
        tail.append(line)
    return list(tail)


def _match_nonce_record(lines: list[str], nonce: str) -> dict[str, Any] | None:
    """Return the parsed JSON of the last line in ``lines`` containing ``nonce``.

    Uses token-boundary lookaround so a quoted/echoed nonce (surrounded by
    non-token characters like ``"``) still counts as a match, while a nonce
    embedded as a substring of a longer alnum/-/_ run does not.
    """

    pattern = re.compile(
        rf"(?<![{_TOKEN_BOUNDARY_CHARS}]){re.escape(nonce)}(?![{_TOKEN_BOUNDARY_CHARS}])"
    )
    record: dict[str, Any] | None = None
    for raw_line in lines:
        if not pattern.search(raw_line):
            continue
        try:
            parsed = json.loads(raw_line)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            record = parsed
    return record


def _find_nonce_record_in_tail(entry: Path, nonce: str, window: int) -> dict[str, Any] | None:
    """Search the last ``window`` lines of ``entry`` for ``nonce`` as a full token.

    Returns the parsed JSON of the last matching line in the tail, or
    ``None`` if no line in the bounded tail contains the nonce as a
    token-bounded match, or the file cannot be opened/read.
    """

    try:
        with entry.open("r", encoding="utf-8", errors="replace") as fh:
            tail = _read_tail_lines_bounded(fh, window)
    except OSError:
        return None
    return _match_nonce_record(tail, nonce)


def _find_nonce_record_via_fd(
    dir_fd: int, basename: str, nonce: str, window: int
) -> dict[str, Any] | None:
    """Independently re-read ``basename`` via a ``dir_fd``-anchored, ``O_NOFOLLOW``
    open and search its bounded tail for ``nonce`` (jCritic P1 HIGH: fd-anchored
    TOCTOU close).

    ``dir_fd`` is a file descriptor opened once on the real ``subagents/``
    directory. Anchoring the open to that fd (instead of re-resolving a
    pathname) means the OS resolves ``basename`` relative to the directory
    inode already known to be inside the allowed root, and ``O_NOFOLLOW``
    refuses to open ``basename`` at all if it is a symlink. This closes the
    gap where an earlier pathname-based read (used only to discover which
    candidate contains the nonce) could have its file descriptor's identity
    swapped between the pre-check and the read: the bytes actually used for
    identity validation come from THIS fd, not from that earlier read.

    Returns ``None`` (never raises) if the anchored open fails for any reason
    (symlink rejected by ``O_NOFOLLOW``, gone, permission denied, dir_fd
    unavailable) or if the anchored read does not corroborate the nonce.
    """

    try:
        fd = os.open(basename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as fh:
            tail = _read_tail_lines_bounded(fh, window)
    except OSError:
        return None
    return _match_nonce_record(tail, nonce)


def resolve_current_invocation(
    *,
    home: Path,
    cwd: str,
    env: Any,
    argv: list[str],
    identity_nonce: str | None,
) -> Resolution:
    """Resolve the calling agent's own identity (Layer A containment + Layer B nonce match).

    See the module docstring and ``.jswarm/plans/TICKET-XXX/evidence/phase0/``
    for the full contract. Env access is allow-list-only via ``.get()``; this
    function never iterates ``env`` and never reads a secret-shaped key.
    """

    session_id = _env_get(env, "CLAUDE_CODE_SESSION_ID")
    if not isinstance(session_id, str) or not session_id.strip():
        return _unavailable_resolution(
            role="main",
            session_id=None,
            reason="layer_a: CLAUDE_CODE_SESSION_ID missing or malformed (session_missing)",
        )

    child_flag = _env_get(env, "CLAUDE_CODE_CHILD_SESSION")
    role = "subagent" if child_flag == "1" else "main"

    project_key = str(cwd).replace("/", "-")
    session_dir = Path(home) / ".claude" / "projects" / project_key / session_id

    if role == "main":
        # Layer A containment: main/orchestrator role reads ONLY its own
        # <session>.jsonl; never inspects subagents/ at all.
        # The real transcript lives at the project-root level, as a SIBLING
        # of the <session>/ subagents directory (<project>/<session>.jsonl),
        # not nested inside <project>/<session>/<session>.jsonl.
        parent_path = session_dir.parent / f"{session_id}.jsonl"
        if not parent_path.is_file():
            return _unavailable_resolution(
                role="main",
                session_id=session_id,
                reason=f"layer_a: main transcript not_found (missing) at {parent_path}",
            )
        return Resolution(
            status="ok",
            role="main",
            session_id=session_id,
            agent_id=None,
            transcript_path=parent_path,
            reason=None,
        )

    # role == "subagent": Layer A bounds ALL reads to <session>/subagents/;
    # <session>.jsonl (the parent transcript) is never opened below.
    if not _valid_nonce_grammar(identity_nonce):
        return _unavailable_resolution(
            role="subagent",
            session_id=session_id,
            reason="layer_b: identity_nonce missing or invalid grammar (nonce_grammar)",
        )
    nonce: str = identity_nonce  # type: ignore[assignment]  # narrowed by _valid_nonce_grammar

    subagents_dir = session_dir / "subagents"
    window = _resolve_tail_window(env)

    nonce_hits: list[tuple[Path, str, dict[str, Any]]] = []
    escape_detected = False
    fd_identity_mismatch_detected = False

    if subagents_dir.is_dir():
        try:
            allowed_root = subagents_dir.resolve(strict=True)
        except OSError:
            allowed_root = subagents_dir

        try:
            subagents_dir_fd: int | None = os.open(
                str(subagents_dir), os.O_RDONLY | os.O_DIRECTORY
            )
        except OSError:
            subagents_dir_fd = None

        try:
            for entry in sorted(subagents_dir.glob("agent-*.jsonl")):
                match = _AGENT_FILENAME_RE.match(entry.name)
                if not match:
                    continue
                filename_agent_id = match.group(1)

                # Reject a symlink escape BEFORE opening: realpath resolution is
                # metadata-only and does not follow/read the escaped target.
                if _is_escaped(entry, allowed_root):
                    escape_detected = True
                    continue

                record = _find_nonce_record_in_tail(entry, nonce, window)
                if record is None:
                    continue

                # TOCTOU (O-5): re-validate containment immediately after the
                # open that located the nonce; the entry may have been swapped
                # for a symlink escape during that very read.
                if _is_escaped(entry, allowed_root):
                    escape_detected = True
                    continue

                # fd-anchored TOCTOU close (jCritic P1 HIGH): the pathname-based
                # scan above only proves a nonce appears somewhere under a path
                # that resolves safely both BEFORE and AFTER the read; it never
                # proves the BYTES actually read came from that safe file. A
                # descriptor-level content swap during the read (the underlying
                # fd silently pointing elsewhere for that one read) defeats a
                # purely realpath-based check. Re-open the same basename via a
                # dir_fd anchored on the already-validated real subagents
                # directory, with O_NOFOLLOW so a symlinked basename is refused
                # outright, and require THIS independent read to corroborate the
                # nonce match. Only the fd-anchored record is trusted for the
                # identity fields checked below.
                if subagents_dir_fd is None:
                    fd_identity_mismatch_detected = True
                    continue
                fd_record = _find_nonce_record_via_fd(
                    subagents_dir_fd, entry.name, nonce, window
                )
                if fd_record is None:
                    fd_identity_mismatch_detected = True
                    continue

                nonce_hits.append((entry, filename_agent_id, fd_record))
        finally:
            if subagents_dir_fd is not None:
                os.close(subagents_dir_fd)

    if not nonce_hits:
        if escape_detected:
            return _unavailable_resolution(
                role="subagent",
                session_id=session_id,
                reason=(
                    "layer_b: matched candidate real path escaped the subagents "
                    "root (symlink escape / toctou realpath check failed; "
                    "rejected, not followed)"
                ),
            )
        if fd_identity_mismatch_detected:
            return _unavailable_resolution(
                role="subagent",
                session_id=session_id,
                reason=(
                    "layer_b: fd-anchored identity re-verification failed (the "
                    "dir_fd-anchored, O_NOFOLLOW-protected re-read of the "
                    "matched candidate did not corroborate the nonce found by "
                    "the initial scan; treated as a toctou fd/content-identity "
                    "escape, rejected not followed)"
                ),
            )
        return _unavailable_resolution(
            role="subagent",
            session_id=session_id,
            reason="layer_b: identity_nonce not_found in recent_window (no match)",
        )

    if len(nonce_hits) > 1:
        return _unavailable_resolution(
            role="subagent",
            session_id=session_id,
            reason="layer_b: multiple/duplicate agent-*.jsonl files matched identity_nonce",
        )

    entry, filename_agent_id, record = nonce_hits[0]

    content_agent_id = record.get("agentId")
    if content_agent_id != filename_agent_id:
        return _unavailable_resolution(
            role="subagent",
            session_id=session_id,
            reason=(
                f"layer_b: filename agentId {filename_agent_id!r} != content "
                f"agentId {content_agent_id!r} (filename_agentid_conflict)"
            ),
        )

    if record.get("sessionId") != session_id:
        return _unavailable_resolution(
            role="subagent",
            session_id=session_id,
            reason=(
                "layer_b: content sessionId does not match env "
                "CLAUDE_CODE_SESSION_ID (session_mismatch)"
            ),
        )

    if record.get("isSidechain") is not True:
        return _unavailable_resolution(
            role="subagent",
            session_id=session_id,
            reason="layer_b: matched record isSidechain is not true (sidechain_false)",
        )

    # Final defense-in-depth TOCTOU re-check before declaring "ok".
    try:
        allowed_root = subagents_dir.resolve(strict=True)
    except OSError:
        allowed_root = subagents_dir
    if _is_escaped(entry, allowed_root):
        return _unavailable_resolution(
            role="subagent",
            session_id=session_id,
            reason=(
                "layer_b: toctou realpath check failed after match, symlink "
                "escape detected (toctou_symlink_realpath)"
            ),
        )

    return Resolution(
        status="ok",
        role="subagent",
        session_id=session_id,
        agent_id=filename_agent_id,
        transcript_path=entry,
        reason=None,
    )


# ---------------------------------------------------------------------------
# Usage lookup: raw jAgentProxy [OUTCOME] log-line parse (Phase 1)
# ---------------------------------------------------------------------------

_OUTCOME_TIMESTAMP_RE = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s*(?P<body>.*)$")


def _parse_key_value_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in text.split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if not key:
            continue
        fields[key] = value
    return fields


def _has_minimum_outcome_grammar(fields: dict[str, str]) -> bool:
    return bool(fields.get("status") and (fields.get("model") or fields.get("class")))


def _parse_outcome_line(line: str) -> dict[str, str] | None:
    """Parse one raw ``[OUTCOME]`` line into its key=value fields.

    Same grammar as ``jswarm/jagentproxy_cost/aggregate.py:parse_outcome_line``
    / ``_normalize_usage``, reimplemented locally (stdlib-only: this module
    must not import ``aggregate.py``, which pulls in a non-stdlib YAML
    dependency, nor reuse its *aggregated* terminal rows, which drop
    ``agentId`` in favor of an ``agent`` slug).
    """

    if not line or "[OUTCOME]" not in line:
        return None

    body = line.rstrip("\n").strip()
    match = _OUTCOME_TIMESTAMP_RE.match(body)
    if match:
        body = match.group("body").strip()

    if not body.startswith("[OUTCOME]"):
        return None

    fields = _parse_key_value_fields(body[len("[OUTCOME]") :].strip())
    if not fields or not _has_minimum_outcome_grammar(fields):
        return None
    return fields


def _coerce_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _derive_provider(fields: dict[str, str]) -> str | None:
    """Return the OUTCOME line's provider label.

    Phase 2 (T-C1..T-C3): pass the raw ``class`` field through
    verbatim (e.g. ``"openai-codex"``, ``"anthropic-direct"``) rather than
    bucketing it into a generic ``"openai"``/``"anthropic"`` label; callers
    now rely on the literal jAgentProxy provider-class string. Falls back to
    a coarse model-prefix guess only when ``class`` itself is absent.
    """

    raw_class = (fields.get("class") or "").strip()
    if raw_class:
        return raw_class
    raw_model = (fields.get("model") or "").strip().lower()
    if not raw_model:
        return None
    return "openai" if raw_model.startswith("gpt-") else "anthropic"


class _NegativeUsageError(ValueError):
    """Raised when an exact OUTCOME line carries a negative usage field.

    A negative token count is not provable usage (Phase 2);
    the caller must fail closed rather than compute a negative
    ``context_pct``.
    """

    def __init__(self, field_name: str) -> None:
        super().__init__(field_name)
        self.field_name = field_name


def _coerce_usage_int(value: str | None, *, field_name: str) -> int | None:
    parsed = _coerce_int(value)
    if parsed is not None and parsed < 0:
        raise _NegativeUsageError(field_name)
    return parsed


def _compute_context_tokens(fields: dict[str, str]) -> int | None:
    """Derive ``context_tokens`` from an exact OUTCOME line's usage fields.

    Precedence (Phase 2): ``usage_total`` wins whenever it is
    present and valid: this is the OpenAI Responses shape jAgentProxy emits
    (``input_tokens`` -> ``usage_in``, ``total_tokens`` -> ``usage_total``),
    and ``total_tokens`` is the authoritative full-context figure even when
    ``usage_in`` is also present. Falls back to the Anthropic
    ``usage_in + usage_cache_create + usage_cache_read`` shape, then to a
    bare ``usage_prompt``.

    Raises ``_NegativeUsageError`` (propagated to the caller) instead of
    returning a negative total for any field that feeds ``context_tokens``.
    """

    usage_total = _coerce_usage_int(fields.get("usage_total"), field_name="usage_total")
    if usage_total is not None:
        return usage_total

    usage_in = _coerce_usage_int(fields.get("usage_in"), field_name="usage_in")
    if usage_in is not None:
        cache_create = _coerce_usage_int(fields.get("usage_cache_create"), field_name="usage_cache_create") or 0
        cache_read = _coerce_usage_int(fields.get("usage_cache_read"), field_name="usage_cache_read") or 0
        return usage_in + cache_create + cache_read

    usage_prompt = _coerce_usage_int(fields.get("usage_prompt"), field_name="usage_prompt")
    if usage_prompt is not None:
        return usage_prompt

    return None


# ---------------------------------------------------------------------------
# Model context-window resolution (Phase 2): no-dependency YAML line parser
# ---------------------------------------------------------------------------

_MODEL_KEY_RE = re.compile(r"^  ([^\s:][^:]*):\s*(?:#.*)?$")
_WINDOW_TOKENS_RE = re.compile(r"^\s{4,}window_tokens:\s*([0-9]+)\s*(?:#.*)?$")
_HEURISTIC_1M_WINDOW = 1_000_000
_DEFAULT_MODEL_WINDOW = 200_000


def _default_registry_path() -> Path:
    """The repo's ``jswarm/config/model-windows.yaml`` (absent by default).

    ``Path(__file__).resolve()`` follows this module's own symlink chain back
    to its master source under ``skills/ctx/ctx-usage.py``, so ``parents[2]``
    is always the repo root regardless of where this module was
    deployed/loaded from. The registry is optional: ``_parse_model_windows_registry``
    below fails safe to ``{}`` on any read error, so a missing file falls
    through to the heuristic/default window tiers rather than raising.
    """

    return Path(__file__).resolve().parents[2] / "jswarm" / "config" / "model-windows.yaml"


def _parse_model_windows_registry(path: Path) -> dict[str, int]:
    """Minimal, targeted, stdlib-only line parser for ``model-windows.yaml``.

    Understands exactly the shape this registry is written in: a top-level
    ``models:`` key, 2-space-indented ``<model-id>:`` entries under it, and a
    4+-space-indented ``window_tokens: <int>`` line within each entry. Does
    not import ``yaml`` or ``registry.py`` (no-dependency requirement).
    Fails safe to ``{}`` on any read/parse error so callers fall through to
    the heuristic/default window tiers rather than raising.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    registry: dict[str, int] = {}
    try:
        in_models = False
        current_model: str | None = None
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not in_models:
                if raw_line.rstrip() == "models:":
                    in_models = True
                continue
            if raw_line.startswith("  ") and not raw_line.startswith("   "):
                match = _MODEL_KEY_RE.match(raw_line)
                current_model = match.group(1).strip() if match else None
                continue
            if current_model is not None:
                match = _WINDOW_TOKENS_RE.match(raw_line)
                if match:
                    try:
                        registry[current_model] = int(match.group(1))
                    except ValueError:
                        pass
    except Exception:
        return {}
    return registry


def resolve_model_window(
    *, model: str, env: Any, registry_path: Path | None = None
) -> tuple[int, str]:
    """Resolve a model's context-window size in tokens.

    Precedence (highest first):
      1. ``JSWARM_CTX_WINDOW`` env override (int tokens) -> ``"env"``.
      2. Exact ``model`` key in the ``model-windows.yaml`` registry at
         ``registry_path`` (default: the deployed/repo ``model-windows.yaml``;
         overridable via ``JSWARM_CTX_MODEL_WINDOWS``) -> ``"registry"``.
      3. ``model`` id containing the literal ``"[1m]"`` suffix ->
         1,000,000 tokens -> ``"heuristic-1m"``.
      4. 200,000 tokens -> ``"default"``.

    Fails safe to the ``"default"`` tier on any registry read/parse error.
    """

    raw_window = _env_get(env, "JSWARM_CTX_WINDOW")
    if raw_window is not None:
        try:
            window_value = int(str(raw_window))
        except (TypeError, ValueError):
            window_value = None
        if window_value is not None and window_value > 0:
            return window_value, "env"

    effective_registry_path = registry_path
    if effective_registry_path is None:
        raw_override = _env_get(env, "JSWARM_CTX_MODEL_WINDOWS")
        effective_registry_path = (
            Path(raw_override) if raw_override else _default_registry_path()
        )

    # Strip a trailing effort-suffix like "(medium)" before any lookup, so
    # an OUTCOME-log model id such as "gpt-5.4-mini(medium)" resolves the
    # same as its bare form "gpt-5.4-mini" for both the registry and the
    # "[1m]" heuristic below.
    base_model = re.sub(r"\(.*\)\s*$", "", model or "").strip()

    registry = _parse_model_windows_registry(Path(effective_registry_path))
    registry_window = registry.get(base_model)
    if isinstance(registry_window, int) and registry_window > 0:
        return registry_window, "registry"

    if base_model and "[1m]" in base_model:
        return _HEURISTIC_1M_WINDOW, "heuristic-1m"

    return _DEFAULT_MODEL_WINDOW, "default"


def _usage_unavailable(reason: str) -> UsageResult:
    return UsageResult(
        status="unavailable",
        provider=None,
        model=None,
        context_tokens=None,
        window=None,
        reason=reason,
    )


_DEFAULT_MAX_TAIL_BYTES = 2_000_000


def _read_tail_text(path: Path, max_bytes: int) -> str:
    """Read ``path``, bounded to its last ``max_bytes`` (NFR-PERF).

    ``max_bytes <= 0`` (jCritic P1 MEDIUM) is clamped to
    ``_DEFAULT_MAX_TAIL_BYTES`` rather than being treated as "unbounded"; a
    caller passing ``0``/negative must never disable the bound and force a
    whole-file read.
    """

    if max_bytes <= 0:
        max_bytes = _DEFAULT_MAX_TAIL_BYTES

    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            data = fh.read()
            newline_index = data.find(b"\n")
            if newline_index != -1:
                # Drop the (possibly truncated) partial first line.
                data = data[newline_index + 1 :]
        else:
            data = fh.read()
    return data.decode("utf-8", errors="replace")


def _compute_context_tokens_from_anthropic_usage(usage: dict[str, Any]) -> int | None:
    """Anthropic-shaped ``usage`` object (``message.usage`` in a transcript line).

    Mirrors ``_compute_context_tokens``'s "input + cache" semantics but keyed
    to the Anthropic API's own field names rather than the jAgentProxy
    OUTCOME grammar's ``usage_*`` keys.

    Raises ``_NegativeUsageError`` (Phase 2) instead of
    returning a smaller-but-positive total when any of the three fields is a
    negative int; the same fail-closed semantics as the OUTCOME-line path's
    ``_coerce_usage_int``.
    """

    input_tokens = usage.get("input_tokens")
    if not isinstance(input_tokens, int):
        return None
    if input_tokens < 0:
        raise _NegativeUsageError("input_tokens")

    cache_create_raw = usage.get("cache_creation_input_tokens")
    if isinstance(cache_create_raw, int) and cache_create_raw < 0:
        raise _NegativeUsageError("cache_creation_input_tokens")
    cache_create = cache_create_raw if isinstance(cache_create_raw, int) else 0

    cache_read_raw = usage.get("cache_read_input_tokens")
    if isinstance(cache_read_raw, int) and cache_read_raw < 0:
        raise _NegativeUsageError("cache_read_input_tokens")
    cache_read = cache_read_raw if isinstance(cache_read_raw, int) else 0

    return input_tokens + cache_create + cache_read


def _extract_usage_from_transcript_tail(
    path: Path, *, max_tail_bytes: int
) -> tuple[str, int] | None:
    """Last usable Anthropic-shaped ``usage`` record in ``path``'s bounded tail.

    Reads only the last ``max_tail_bytes`` of ``path`` (NFR-PERF, same bound
    as the OUTCOME log read) and returns ``(model, context_tokens)`` for the
    last JSONL line whose ``message.model`` and ``message.usage`` yield a
    computable token count, or ``None`` if no such line exists in the tail.

    Raises ``_NegativeUsageError`` (propagated to the caller), Phase 2,
    if a ``message.usage`` record carries a negative
    ``input_tokens``, ``cache_creation_input_tokens``, or
    ``cache_read_input_tokens`` field; the transcript fallback must fail
    closed rather than silently skip the bad record.
    """

    try:
        text = _read_tail_text(path, max_tail_bytes)
    except OSError:
        return None

    if not text.strip():
        return None

    found: tuple[str, int] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(parsed, dict):
            continue
        message = parsed.get("message")
        if not isinstance(message, dict):
            continue
        model = message.get("model")
        usage = message.get("usage")
        if not isinstance(model, str) or not model or not isinstance(usage, dict):
            continue
        context_tokens = _compute_context_tokens_from_anthropic_usage(usage)
        if context_tokens is None:
            continue
        found = (model, context_tokens)
    return found


def _finalize_no_usage(
    reason: str,
    *,
    resolution: Resolution,
    env: Any,
    registry_path: Path | None,
    max_tail_bytes: int,
) -> UsageResult:
    """Apply the transcript-path fallback/cross-check before failing closed.

    The resolved transcript path is a fallback/cross-check when the OUTCOME
    log yields nothing usable. Phase 2: actually attempt to extract
    a usable Anthropic-shaped ``usage`` record from the caller's own bounded
    transcript tail (``usage_source="transcript-jsonl"``) before failing
    closed; Phase 1's reachability check (transcript missing/deleted is a
    more specific, more actionable failure than the underlying OUTCOME-log
    reason) is preserved unchanged.
    """

    transcript_path = resolution.transcript_path
    if transcript_path is None or not Path(transcript_path).exists():
        return _usage_unavailable(
            "usage lookup unavailable: no usable OUTCOME line, and the "
            "fallback/cross-check transcript_path is not_found (missing / deleted)"
        )

    try:
        extracted = _extract_usage_from_transcript_tail(
            Path(transcript_path), max_tail_bytes=max_tail_bytes
        )
    except _NegativeUsageError as exc:
        return _usage_unavailable(
            "usage lookup unavailable: transcript-jsonl usage record has "
            f"invalid field {exc.field_name!r} (negative usage is not provable)"
        )
    except OSError:
        extracted = None

    if extracted is not None:
        model, context_tokens = extracted
        window, window_source = resolve_model_window(
            model=model, env=env, registry_path=registry_path
        )
        context_pct = round(context_tokens / window * 100, 1) if window else None
        return UsageResult(
            status="ok",
            provider=None,
            model=model,
            context_tokens=context_tokens,
            window=window,
            context_pct=context_pct,
            usage_source="transcript-jsonl",
            window_source=window_source,
            reason=None,
        )

    return _usage_unavailable(reason)


def find_usage_for_resolution(
    *,
    resolution: Resolution,
    outcome_log_path: Path | None,
    env: Any = None,
    registry_path: Path | None = None,
    max_tail_bytes: int = 2_000_000,
) -> UsageResult:
    """Resolve cross-provider usage for an already-resolved caller identity.

    Primary source: the jAgentProxy ``[OUTCOME]`` log
    (``usage_source="jagentproxy-outcome-log"``), window-joined via
    ``resolve_model_window``. Fallback (Phase 2): the caller's own bounded
    transcript tail (``usage_source="transcript-jsonl"``) when the OUTCOME
    log is unavailable or yields nothing usable. Fails closed
    (``usage_source="unavailable"``) when neither source yields a usable
    record.
    """

    effective_env: Any = env if env is not None else {}

    if resolution.status != "ok":
        underlying = resolution.reason or "no_reason"
        return _usage_unavailable(
            f"usage lookup unavailable: identity resolution is not ok ({underlying})"
        )

    if outcome_log_path is None:
        return _finalize_no_usage(
            "outcome_log_path not configured (none): no OUTCOME log path was provided",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    log_path = Path(outcome_log_path)
    if not log_path.is_file():
        return _finalize_no_usage(
            f"outcome_log not_found (missing) at {log_path}",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    try:
        text = _read_tail_text(log_path, max_tail_bytes)
    except OSError as exc:
        return _finalize_no_usage(
            f"failed to open/read outcome_log (unreadable): {exc}",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    if not text.strip():
        return _finalize_no_usage(
            "outcome_log is empty: no usable OUTCOME usage line found (not_found / no match)",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    records: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        fields = _parse_outcome_line(raw_line)
        if fields is not None:
            records.append(fields)

    if not records:
        return _finalize_no_usage(
            "outcome_log lines did not parse as OUTCOME key=value grammar (grammar / parse failure)",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    identity_matches = [
        r
        for r in records
        if r.get("session") == resolution.session_id
        and r.get("role") == resolution.role
        and r.get("agentId") == resolution.agent_id
    ]
    if not identity_matches:
        return _finalize_no_usage(
            (
                f"no OUTCOME line matched session={resolution.session_id!r} "
                f"role={resolution.role!r} agentId={resolution.agent_id!r} "
                "(usage not_found / no match)"
            ),
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    exact_matches = [r for r in identity_matches if r.get("usage_status") == "exact"]
    if not exact_matches:
        observed = identity_matches[-1].get("usage_status")
        return _finalize_no_usage(
            f"no matching OUTCOME line has usage_status=exact (observed usage_status={observed!r})",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    chosen = exact_matches[-1]

    model = chosen.get("model")
    if not model:
        return _finalize_no_usage(
            "matching OUTCOME line has usage_status=exact but is missing the "
            "required model field (required / missing)",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    try:
        context_tokens = _compute_context_tokens(chosen)
    except _NegativeUsageError as exc:
        return _finalize_no_usage(
            f"matching OUTCOME line has usage_status=exact but field "
            f"{exc.field_name!r} is invalid (negative usage is not provable)",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )
    if context_tokens is None:
        return _finalize_no_usage(
            "matching OUTCOME line has usage_status=exact but no usable usage "
            "tokens (missing/unparseable usage_in, usage_total, usage_prompt)",
            resolution=resolution,
            env=effective_env,
            registry_path=registry_path,
            max_tail_bytes=max_tail_bytes,
        )

    window, window_source = resolve_model_window(
        model=model, env=effective_env, registry_path=registry_path
    )
    context_pct = round(context_tokens / window * 100, 1) if window else None

    return UsageResult(
        status="ok",
        provider=_derive_provider(chosen),
        model=model,
        context_tokens=context_tokens,
        window=window,
        context_pct=context_pct,
        usage_source="jagentproxy-outcome-log",
        window_source=window_source,
        reason=None,
    )


# ---------------------------------------------------------------------------
# Anthropic OAuth usage endpoint quota (Phase 8)
# ---------------------------------------------------------------------------
#
# Replaces the Phase 7 ccstatusline quota cache entirely: quota now comes
# directly from Anthropic's OAuth usage endpoint
# (``https://api.anthropic.com/api/oauth/usage``), parsed generically so a
# brand-new scoped limit (a new model family, a new ``kind``) appears
# automatically with no code change (turbulent-safe).

_ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_ANTHROPIC_BETA_HEADER = "oauth-2025-04-20"

# (top-level response key, quota label) pairs used only by the fallback path
# below, when the endpoint response carries no generic ``limits`` list.
_TOP_LEVEL_QUOTA_FIELDS: tuple[tuple[str, str], ...] = (
    ("five_hour", "5h"),
    ("seven_day", "7d"),
    ("seven_day_opus", "opus"),
    ("seven_day_sonnet", "sonnet"),
)


def _quota_label_for_limit_entry(entry: dict[str, Any]) -> str:
    """Turbulent-safe label for one ``response["limits"]`` entry.

    ``kind == "session"`` -> ``"5h"``; ``kind == "weekly_all"`` -> ``"7d"``;
    ``kind == "weekly_scoped"`` -> the scoped model's ``display_name``
    lower-cased (e.g. ``"Fable"`` -> ``"fable"``, a brand-new family like
    ``"Nimbus"`` -> ``"nimbus"`` with no code change). Any other/unknown
    ``kind`` still never drops the limit: it prefers a scoped model name when
    present, else falls back to the endpoint's own raw ``kind`` string.
    """

    kind = entry.get("kind")
    scope = entry.get("scope")
    scoped_model_label: str | None = None
    if isinstance(scope, dict):
        model = scope.get("model")
        if isinstance(model, dict):
            display_name = model.get("display_name")
            if isinstance(display_name, str) and display_name:
                scoped_model_label = display_name.lower()

    if kind == "session":
        return "5h"
    if kind == "weekly_all":
        return "7d"
    if kind == "weekly_scoped":
        return scoped_model_label if scoped_model_label is not None else "weekly_scoped"

    # Unknown/other kind: pass through generically rather than dropping it.
    if scoped_model_label is not None:
        return scoped_model_label
    if isinstance(kind, str) and kind:
        return kind
    return "unknown"


def parse_quota(response: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Parse an Anthropic OAuth usage endpoint response into a generic quota list.

    Prefers ``response["limits"]`` (a list) when present and non-empty,
    mapping each entry via ``_quota_label_for_limit_entry`` while preserving
    endpoint order; this is the turbulent-safe path: a brand-new scoped
    limit appears automatically with no code change. Falls back to the
    top-level ``five_hour``/``seven_day``/``seven_day_opus``/``seven_day_sonnet``
    windows (``percent`` = each block's ``utilization``) only when ``limits``
    is absent/empty; a top-level block that is ``None``/missing/has no
    ``utilization`` is excluded rather than emitted as a placeholder.

    Returns ``None`` when neither shape yields anything usable, so callers
    can fail closed without special-casing an empty list.
    """

    if not isinstance(response, dict):
        return None

    limits = response.get("limits")
    if isinstance(limits, list) and limits:
        quota: list[dict[str, Any]] = []
        for entry in limits:
            if not isinstance(entry, dict):
                continue
            quota.append(
                {
                    "label": _quota_label_for_limit_entry(entry),
                    "percent": entry.get("percent"),
                    "resets_at": entry.get("resets_at"),
                }
            )
        return quota if quota else None

    fallback: list[dict[str, Any]] = []
    for key, label in _TOP_LEVEL_QUOTA_FIELDS:
        block = response.get(key)
        if not isinstance(block, dict):
            continue
        utilization = block.get("utilization")
        if utilization is None:
            continue
        fallback.append({"label": label, "percent": utilization, "resets_at": block.get("resets_at")})
    return fallback if fallback else None


def _default_usage_opener(url: str, *, headers: dict[str, str], timeout: float) -> Any:
    """Production default ``opener`` for ``_fetch_anthropic_usage`` (real HTTP call).

    Built on plain ``urllib.request`` (stdlib-only). Tests inject a fake
    opener instead; this function itself never prints/logs anything.
    """

    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def _read_credentials_object(
    credentials_path: Path, *, max_bytes: int = 65536
) -> dict[str, Any] | None:
    """No-follow, regular-file, size-bounded read of the OAuth credentials JSON.

    Opens ``credentials_path`` fd-anchored with ``O_NOFOLLOW`` (mirroring the
    ``_find_nonce_record_via_fd`` pattern above) so a symlinked credentials
    path is refused before any bytes are read, then ``fstat``s the opened fd
    to reject anything that is not a regular file (FIFO, device, directory)
    and anything larger than ``max_bytes``. Returns the parsed JSON object
    only if it decodes to a ``dict``; returns ``None`` on ANY failure
    (missing file, symlink, non-regular file, oversized file, malformed
    JSON, non-dict JSON) and never raises or leaks exception text.
    """

    fd: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(credentials_path, flags)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_size > max_bytes:
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = None  # fdopen now owns the fd; avoid double-close below
            raw = fh.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _fetch_anthropic_usage(
    *,
    env: Any,
    credentials_path: Path,
    timeout: float,
    opener: Callable[..., Any],
) -> dict[str, Any] | None:
    """Fetch the raw Anthropic OAuth usage endpoint response, failing closed.

    Reads the OAuth access token from ``credentials_path`` (the JSON shape
    Claude Code itself writes: ``{"claudeAiOauth": {"accessToken": "..."}}``)
    and calls ``opener(url, headers=..., timeout=timeout)`` where ``url`` is
    always the literal endpoint URL; the token is carried ONLY in the
    ``Authorization`` header, never in the URL.

    CRITICAL SECURITY (NFR-SEC): this function fails closed on EVERY error:
    missing/unreadable credentials file, missing token, non-200 status,
    ``socket.timeout``, malformed JSON, or any other exception, by catching
    broadly and returning ``None``. It never re-raises and never inspects or
    persists ``str(exc)`` anywhere, because an exception message raised by a
    transport layer could itself contain the ``Authorization`` header (and
    thus the token). ``env`` is accepted for interface symmetry with the
    rest of this module's env-aware helpers but is not read here; the caller
    (``main``) already resolves ``credentials_path``/``timeout`` from env.
    """

    del env  # reserved for interface symmetry; not read here (see docstring)

    try:
        credentials = _read_credentials_object(credentials_path)
        token = None
        if isinstance(credentials, dict):
            oauth = credentials.get("claudeAiOauth")
            if isinstance(oauth, dict):
                token = oauth.get("accessToken")
        if not isinstance(token, str) or not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": _ANTHROPIC_BETA_HEADER,
        }
        with opener(_ANTHROPIC_USAGE_URL, headers=headers, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status != 200:
                return None
            body = response.read()

        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except Exception:
        # Fail closed on every error class (missing file, bad JSON, HTTP
        # error, socket.timeout, transport exception, ...). Never propagate:
        # an exception's message may itself carry the Authorization header.
        return None


# ---------------------------------------------------------------------------
# Provider/model-scoped quota dispatch + Codex/ChatGPT adapter (Phase 9)
# ---------------------------------------------------------------------------
#
# Phase 8 hardcoded the Anthropic OAuth usage endpoint as the only quota
# source. Phase 9 scopes quota to the CALLER's own provider AND own model:
# ``resolve_quota_provider`` classifies the caller (from its ``UsageResult``),
# ``fetch_quota_for_caller`` dispatches to the matching adapter, and
# ``_fetch_codex_usage``/``parse_codex_quota`` are a new OpenAI/Codex adapter
# that reads the caller's own ChatGPT/Codex ``wham/usage`` quota. The
# Anthropic path (``_fetch_anthropic_usage``/``parse_quota`` above) is
# unchanged; ``fetch_quota_for_caller`` is the only new caller of it.

_CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"

# Window durations (seconds) that map to the Anthropic-style short labels
# used elsewhere in this module's quota output, so a Codex "5h"/"7d" window
# renders identically to an Anthropic "5h"/"7d" window.
_CODEX_WINDOW_LABELS: dict[int, str] = {18000: "5h", 604800: "7d"}


def _quota_label_for_window_seconds(seconds: Any) -> str:
    """Turbulent-safe human label for a Codex rate-limit window's seconds.

    ``18000`` -> ``"5h"``, ``604800`` -> ``"7d"`` (the two canonical Codex
    primary/secondary window sizes, kept consistent with the Anthropic
    ``"5h"``/``"7d"`` labels). Any other exact multiple of an hour or a day
    humanizes generically (``3600`` -> ``"1h"``, ``86400`` -> ``"1d"``) so a
    brand-new window size still reads sensibly with no code change. Anything
    else falls back to the raw seconds count (``123`` -> ``"123s"``).
    """

    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return f"{seconds}s"
    label = _CODEX_WINDOW_LABELS.get(seconds)
    if label is not None:
        return label
    if seconds > 0 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds > 0 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds}s"


def _codex_epoch_to_iso(value: Any) -> str | None:
    """``datetime.fromtimestamp(value, tz=timezone.utc).isoformat()``, or ``None``."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _codex_window_quota_entry(window: Any) -> dict[str, Any] | None:
    """One ``{"label", "percent", "resets_at"}`` entry for a Codex rate-limit window.

    ``window`` is the raw ``primary_window``/``secondary_window`` object from
    the ``wham/usage`` response. Returns ``None`` (never raises) when
    ``window`` isn't a dict or is missing the fields needed to build a usable
    entry, so a malformed window is skipped rather than emitted as a
    placeholder.
    """

    if not isinstance(window, dict):
        return None
    limit_window_seconds = window.get("limit_window_seconds")
    resets_at = _codex_epoch_to_iso(window.get("reset_at"))
    if not isinstance(limit_window_seconds, int) or isinstance(limit_window_seconds, bool):
        return None
    if resets_at is None:
        return None
    used_percent = window.get("used_percent")
    if not isinstance(used_percent, (int, float)) or isinstance(used_percent, bool):
        return None
    return {
        "label": _quota_label_for_window_seconds(limit_window_seconds),
        "percent": used_percent,
        "resets_at": resets_at,
    }


def parse_codex_quota(
    response: dict[str, Any], caller_model: str
) -> list[dict[str, Any]] | None:
    """Parse a Codex/ChatGPT ``wham/usage`` response, scoped to ``caller_model``.

    Always includes the account-wide ``primary_window``/``secondary_window``
    entries from ``response["rate_limit"]`` (when present and well-formed),
    in that order. Then scans ``response["additional_rate_limits"]`` and
    includes ONLY the entry (or entries, in list order) whose ``limit_name``
    (lower-cased) equals the caller's own base model name (the same
    effort-suffix-stripped, lower-cased normalization used by
    ``resolve_model_window`` above); this is what keeps a Spark-only limit
    scoped to Spark callers and invisible to e.g. a ``gpt-5.4-mini`` caller.

    Malformed entries (wrong types, missing fields) are skipped defensively
    and never raise. Returns ``None`` when neither the account-wide windows
    nor any matching additional entry yielded anything usable.
    """

    if not isinstance(response, dict):
        return None

    quota: list[dict[str, Any]] = []

    rate_limit = response.get("rate_limit")
    if isinstance(rate_limit, dict):
        primary_entry = _codex_window_quota_entry(rate_limit.get("primary_window"))
        if primary_entry is not None:
            quota.append(primary_entry)
        secondary_entry = _codex_window_quota_entry(rate_limit.get("secondary_window"))
        if secondary_entry is not None:
            quota.append(secondary_entry)

    base_caller_model = re.sub(r"\(.*\)\s*$", "", caller_model or "").strip().lower()

    additional = response.get("additional_rate_limits")
    if isinstance(additional, list):
        for item in additional:
            if not isinstance(item, dict):
                continue
            limit_name = item.get("limit_name")
            if not isinstance(limit_name, str) or not limit_name:
                continue
            if limit_name.lower() != base_caller_model:
                continue
            item_rate_limit = item.get("rate_limit")
            if not isinstance(item_rate_limit, dict):
                continue
            entry = _codex_window_quota_entry(item_rate_limit.get("primary_window"))
            if entry is None:
                continue
            quota.append({**entry, "label": limit_name.lower()})

    return quota if quota else None


def resolve_quota_provider(usage: UsageResult) -> str | None:
    """Classify which quota adapter to use for this caller's own provider/model.

    Reads ``usage.model``/``usage.provider`` (case-insensitively): a model
    containing ``gpt``/``codex``/``openai``, or a provider containing
    ``openai``/``codex``, resolves to ``"openai"``. A model containing
    ``glm``, or a provider containing ``zai``/``glm``, resolves to
    ``"glm"`` (a documented stub; Phase 9 does not implement a GLM/Z.ai
    quota adapter). A ``claude``-named model or an ``anthropic`` provider
    resolves to ``"anthropic"`` on explicit evidence. An unknown/unset
    model and provider returns ``None`` instead of defaulting to
    ``"anthropic"``, so an unidentified caller never triggers an Anthropic
    credential read or network call.
    """

    model = (usage.model or "").lower()
    provider = (usage.provider or "").lower()

    if "gpt" in model or "codex" in model or "openai" in model:
        return "openai"
    if "openai" in provider or "codex" in provider:
        return "openai"
    if "glm" in model:
        return "glm"
    if "zai" in provider or "glm" in provider:
        return "glm"
    if "claude" in model or "anthropic" in model:
        return "anthropic"
    if "claude" in provider or "anthropic" in provider:
        return "anthropic"
    return None


def _default_codex_credentials_dir() -> Path:
    return Path.home() / ".cli-proxy-api"


def _resolve_codex_credentials_path(env: Any) -> Path:
    """Resolve the Codex/ChatGPT OAuth credentials file path, never raising.

    ``JSWARM_CTX_CODEX_CREDENTIALS`` overrides entirely when set (mirrors
    ``_resolve_credentials_path``'s ``JSWARM_CTX_CREDENTIALS`` for the
    Anthropic path). Otherwise globs CLIProxyAPI's on-disk credential
    naming, ``~/.cli-proxy-api/codex-*.json``, in sorted (deterministic)
    order and returns the first candidate that reads as a valid,
    non-``disabled`` credentials object via ``_read_credentials_object``
    (reusing its fd-anchored no-follow read). When no candidate qualifies,
    falls back to the first glob match (if any) or a sensible default path
    under the same directory, so the subsequent fetch fails closed on a
    missing/unreadable file rather than this resolver ever raising.
    """

    raw = _env_get(env, "JSWARM_CTX_CODEX_CREDENTIALS")
    if raw:
        return Path(raw)

    try:
        candidates = sorted(_default_codex_credentials_dir().glob("codex-*.json"))
    except Exception:
        candidates = []

    for candidate in candidates:
        creds = _read_credentials_object(candidate)
        if creds is None:
            continue
        if creds.get("disabled") is True:
            continue
        return candidate

    if candidates:
        return candidates[0]
    return _default_codex_credentials_dir() / "codex-default.json"


def _fetch_codex_usage(
    *,
    env: Any,
    credentials_path: Path,
    timeout: float,
    opener: Callable[..., Any],
) -> dict[str, Any] | None:
    """Fetch the raw Codex/ChatGPT ``wham/usage`` endpoint response, failing closed.

    Mirrors ``_fetch_anthropic_usage`` exactly, adapted to the Codex/CLIProxyAPI
    credentials shape (``{"access_token": ..., "account_id": ..., "disabled": ...}``,
    reused via ``_read_credentials_object``) and headers (``Authorization: Bearer
    <access_token>`` plus ``ChatGPT-Account-Id: <account_id>``); the token and
    account id are carried ONLY in headers, never in the URL.

    CRITICAL SECURITY (NFR-SEC): fails closed on every error: missing/unreadable
    or ``disabled`` credentials, a missing token or account id, non-200 status,
    ``socket.timeout``, malformed JSON, a non-dict JSON body, or any other
    exception, by catching broadly and returning ``None``. It never re-raises
    and never inspects or persists ``str(exc)`` anywhere, because an exception
    raised by a transport layer could itself contain the ``Authorization``
    header. ``env`` is accepted for interface symmetry with
    ``_fetch_anthropic_usage`` but is not read here.
    """

    del env  # reserved for interface symmetry; not read here (see docstring)

    try:
        credentials = _read_credentials_object(credentials_path)
        if not isinstance(credentials, dict):
            return None
        if credentials.get("disabled") is True:
            return None

        token = credentials.get("access_token")
        account = credentials.get("account_id")
        if not isinstance(token, str) or not token:
            return None
        if not isinstance(account, str) or not account:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "ChatGPT-Account-Id": account,
        }
        with opener(_CODEX_USAGE_URL, headers=headers, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status != 200:
                return None
            body = response.read()

        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except Exception:
        # Fail closed on every error class (missing/disabled credentials, bad
        # JSON, HTTP error, socket.timeout, transport exception, ...). Never
        # propagate: an exception's message may itself carry the
        # Authorization header or the ChatGPT-Account-Id.
        return None


def fetch_quota_for_caller(
    *,
    usage: UsageResult,
    env: Any,
    anthropic_credentials_path: Path | None = None,
    codex_credentials_path: Path | None = None,
    timeout: float | None = None,
    anthropic_opener: Callable[..., Any] = _default_usage_opener,
    codex_opener: Callable[..., Any] = _default_usage_opener,
) -> list[dict[str, Any]] | None:
    """Dispatch quota fetching to the caller's own provider adapter.

    Resolves the provider via ``resolve_quota_provider(usage)``, then:

    - ``"anthropic"``: fetches/parses via ``_fetch_anthropic_usage``/
      ``parse_quota`` (unchanged Phase 8 path). The ``codex_opener``/
      ``codex_credentials_path`` are never touched.
    - ``"openai"``: fetches/parses via ``_fetch_codex_usage``/
      ``parse_codex_quota`` (this caller's own ``usage.model``). The
      ``anthropic_opener``/``anthropic_credentials_path`` are never touched.
    - anything else (e.g. ``"glm"``): returns ``None`` without calling any
      opener (a documented stub; Phase 9 does not implement a GLM/Z.ai
      quota adapter).

    Any credentials-path/timeout argument left ``None`` is resolved from
    ``env`` the same way ``main()`` resolves it today (``_resolve_credentials_path``,
    ``_resolve_codex_credentials_path``, ``_resolve_quota_timeout``).
    """

    provider = resolve_quota_provider(usage)

    if provider == "anthropic":
        resolved_path = (
            anthropic_credentials_path
            if anthropic_credentials_path is not None
            else _resolve_credentials_path(env)
        )
        resolved_timeout = timeout if timeout is not None else _resolve_quota_timeout(env)
        fetched = _fetch_anthropic_usage(
            env=env,
            credentials_path=resolved_path,
            timeout=resolved_timeout,
            opener=anthropic_opener,
        )
        return parse_quota(fetched) if fetched else None

    if provider == "openai":
        resolved_path = (
            codex_credentials_path
            if codex_credentials_path is not None
            else _resolve_codex_credentials_path(env)
        )
        resolved_timeout = timeout if timeout is not None else _resolve_quota_timeout(env)
        fetched = _fetch_codex_usage(
            env=env,
            credentials_path=resolved_path,
            timeout=resolved_timeout,
            opener=codex_opener,
        )
        return parse_codex_quota(fetched, usage.model or "") if fetched else None

    # "glm" (or any other/future provider label): documented stub, no network.
    return None


def _format_reset_in(raw: Any, now: datetime, *, weekly: bool) -> str | None:
    """Human-readable countdown to an absolute ISO reset timestamp, or ``None``.

    Ported from ``jswarm-quota-remaining-widget.py``'s ``_fmt_session``/
    ``_fmt_weekly`` formatting: session -> ``{h}h{m}m``/``{m}m``; weekly ->
    ``{d}d{h}h`` once >=1 day, else the session-style format. Fails safe to
    ``None`` on any missing/malformed/past timestamp.
    """

    if not isinstance(raw, str) or not raw:
        return None
    try:
        reset_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    remaining = int((reset_at - now).total_seconds())
    if remaining <= 0:
        return None
    if weekly:
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        minutes = (remaining % 3600) // 60
        if days > 0:
            return f"{days}d{hours}h"
        if hours > 0:
            return f"{hours}h{minutes}m"
        return f"{minutes}m"
    hours = remaining // 3600
    minutes = (remaining % 3600) // 60
    if hours > 0:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# --json output contract + CLI
# ---------------------------------------------------------------------------


def _quota_entry(quota: list[dict[str, Any]] | None, label: str) -> dict[str, Any] | None:
    """The first entry in ``quota`` whose ``label`` matches, or ``None``."""

    if not quota:
        return None
    for entry in quota:
        if isinstance(entry, dict) and entry.get("label") == label:
            return entry
    return None


def build_report(
    *,
    resolution: Resolution,
    usage: UsageResult,
    env: Any,
    quota: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the ``--json`` output contract.

    v1 field names (``context_tokens, context_pct, window, window_source,
    session_usage_pct, weekly_usage_pct, session_reset_in, weekly_reset_in,
    model``) plus Phase 2 fields (``agent_id, usage_source, provider,
    reason``). On ``usage.status == "unavailable"`` the report still carries
    ``usage_source="unavailable"`` and ``reason``.

    Phase 8: quota is generic and endpoint-driven; ``quota`` is
    the ordered list ``parse_quota`` produced (or ``None`` when the endpoint
    fetch failed), carried verbatim as ``report["quota"]``. The v1/Phase 7
    backcompat percentage keys (``session_usage_pct``, ``weekly_usage_pct``,
    ``weekly_fable_pct``, ``weekly_opus_pct``, ``weekly_sonnet_pct``) are
    derived by looking up the ``"5h"``/``"7d"``/``"fable"``/``"opus"``/
    ``"sonnet"`` labels in ``quota``; ``None`` when that label is absent, so
    a turbulent-safe endpoint response (a new/renamed model family) never
    breaks these fixed keys, it just leaves them ``None``.
    ``session_reset_in``/``weekly_reset_in`` are derived the same way, via
    ``_format_reset_in`` on the ``"5h"``/``"7d"`` entries' ``resets_at``.
    """

    del env  # reserved for future report fields; not needed today

    now = datetime.now(timezone.utc)
    five_hour = _quota_entry(quota, "5h")
    seven_day = _quota_entry(quota, "7d")
    fable = _quota_entry(quota, "fable")
    opus = _quota_entry(quota, "opus")
    sonnet = _quota_entry(quota, "sonnet")

    return {
        "agent_id": resolution.agent_id,
        "provider": usage.provider,
        "model": usage.model,
        "context_tokens": usage.context_tokens,
        "context_pct": usage.context_pct,
        "window": usage.window,
        "window_source": usage.window_source,
        "usage_source": usage.usage_source,
        "reason": usage.reason,
        "quota": quota,
        "session_usage_pct": five_hour.get("percent") if five_hour else None,
        "weekly_usage_pct": seven_day.get("percent") if seven_day else None,
        "weekly_fable_pct": fable.get("percent") if fable else None,
        "weekly_opus_pct": opus.get("percent") if opus else None,
        "weekly_sonnet_pct": sonnet.get("percent") if sonnet else None,
        "session_reset_in": _format_reset_in(
            five_hour.get("resets_at") if five_hour else None, now, weekly=False
        ),
        "weekly_reset_in": _format_reset_in(
            seven_day.get("resets_at") if seven_day else None, now, weekly=True
        ),
    }


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctx-usage.py",
        description="Report the calling agent's own context-window usage (fail-closed).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit the machine-readable JSON report contract"
    )
    parser.add_argument(
        "--identity-nonce",
        default=None,
        help="The ctx-nonce:<literal> Claude Code recorded for this invocation",
    )
    return parser


def _humanize_tokens(value: int | float | None) -> str:
    """Compact human token count: ``552771->"552.8k"``, ``1000000->"1M"``.

    Precedence: ``>=1_000_000`` renders as ``<n/1e6, 1 decimal, trailing
    zero/dot trimmed>M`` (e.g. ``1000000 -> "1M"``, ``1500000 -> "1.5M"``);
    ``>=1_000`` renders the same way with a ``k`` suffix (e.g.
    ``552771 -> "552.8k"``, ``200000 -> "200k"``); below that, the bare int.
    ``None`` renders as ``"?"``; ``format_compact`` never calls this for a
    ``None`` token/window on the "ok" branch (that branch requires both to be
    present), so ``"?"`` is a defensive fallback only.
    """

    if value is None:
        return "?"
    if value >= 1_000_000:
        text = f"{value / 1_000_000:.1f}"
        if text.endswith("0"):
            text = text[:-1]
        if text.endswith("."):
            text = text[:-1]
        return f"{text}M"
    if value >= 1_000:
        text = f"{value / 1_000:.1f}"
        if text.endswith("0"):
            text = text[:-1]
        if text.endswith("."):
            text = text[:-1]
        return f"{text}k"
    return str(int(value))


def _quota_parts(report: dict[str, Any]) -> str:
    """Render the generic ``quota.<label> <percent>%`` segment, in endpoint order.

    Phase 8: no labels are hardcoded here; this renders whatever
    ``report["quota"]`` (the ordered list ``parse_quota`` produced) actually
    contains, one ``quota.<label> <percent>%`` term per entry, joined by
    ``", "``. This is what makes a brand-new scoped limit (a new model
    family, a new ``kind``) show up automatically with no code change here.
    When ``quota`` is ``None``/empty, renders the single turbulent-safe
    fallback ``"quota unavailable"`` rather than guessing at a fixed label
    set to fill with ``n/a``.
    """

    quota = report.get("quota")
    if not quota:
        return "quota unavailable"

    now = datetime.now(timezone.utc)
    parts: list[str] = []
    for entry in quota:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        percent = entry.get("percent")
        percent_text = f"{percent}%" if percent is not None else "n/a"
        segment = f"quota.{label} {percent_text}"
        reset_in = _format_reset_in(entry.get("resets_at"), now, weekly=True)
        if reset_in is not None:
            segment = f"{segment} reset:{reset_in}"
        parts.append(segment)

    return ", ".join(parts) if parts else "quota unavailable"


def format_compact(report: dict[str, Any]) -> str:
    """Render the ``--json``-equivalent ``report`` as ONE compact all-info line.

    Identical shape for the orchestrator (``report["agent_id"] is None``) and
    a subagent; nothing about role changes the template. Two branches:

    - Usable usage (``report["reason"] is None``):
      ``ctx <tok>/<window> <pct>% | <model>[ <provider>] | src:<usage_source> |
      <quota parts>``.
    - Unavailable usage (``report["reason"] is not None``): no model block;
      ``ctx unavailable (<reason>) | src:<usage_source> | <quota parts>``.

    The quota segment (``_quota_parts``) is identical in both branches and
    renders whatever generic quota labels the endpoint actually returned
    (Phase 8), or ``"quota unavailable"`` when the endpoint fetch
    failed/returned nothing usable.
    """

    quota = _quota_parts(report)
    usage_source = report.get("usage_source") or "unavailable"
    reason = report.get("reason")
    if reason is not None:
        return f"ctx unavailable ({reason}) | src:{usage_source} | {quota}"

    tokens = _humanize_tokens(report.get("context_tokens"))
    window = _humanize_tokens(report.get("window"))
    pct = report.get("context_pct")
    pct_text = f"{round(pct)}%" if pct is not None else "?%"
    model = report.get("model") or "?"
    provider = report.get("provider")
    model_block = f"{model} {provider}" if provider else str(model)
    return f"ctx {tokens}/{window} {pct_text} | {model_block} | src:{usage_source} | {quota}"


def _default_outcome_log(env: Any) -> Path | None:
    """Resolve the default jAgentProxy OUTCOME log when unconfigured.

    Phase 7: when ``JSWARM_CTX_OUTCOME_LOG`` is unset, the original
    ``/ctx`` skill intent is to auto-resolve a sensible default rather than
    fail closed on a missing explicit override. Precedence (first existing
    file wins):

    1. ``$JARVISWARM_ROOT/log/jagentproxy.log``.
    2. ``${JSWARM_HOME:-$HOME/dev/jswarm}/log/jagentproxy.log``.
    3. ``None`` (caller falls back to its own ``outcome_log_path=None``
       handling, which is unchanged and still fails closed).

    Reads ``JARVISWARM_ROOT``/``HOME`` via ``.get()`` only (no wholesale
    ``env`` iteration, no secret-shaped keys) and never raises; a missing or
    unreadable candidate simply falls through to the next tier.
    """

    jarviswarm_root = _env_get(env, "JARVISWARM_ROOT")
    if jarviswarm_root:
        candidate = Path(jarviswarm_root) / "log" / "jagentproxy.log"
        if candidate.is_file():
            return candidate

    home = _env_get(env, "HOME")
    if home:
        candidate = Path(home) / "dev" / "common" / "log" / "jagentproxy.log"
        if candidate.is_file():
            return candidate

    return None


_DEFAULT_QUOTA_TIMEOUT_SECONDS = 3.0


def _default_credentials_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


def _resolve_credentials_path(env: Any) -> Path:
    raw = _env_get(env, "JSWARM_CTX_CREDENTIALS")
    return Path(raw) if raw else _default_credentials_path()


def _resolve_quota_timeout(env: Any) -> float:
    raw = _env_get(env, "JSWARM_CTX_QUOTA_TIMEOUT")
    if raw is None:
        return _DEFAULT_QUOTA_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_QUOTA_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_QUOTA_TIMEOUT_SECONDS


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: resolve identity -> resolve usage -> build/emit report.

    Exits non-zero when the resolved usage is ``unavailable`` (fail-closed).
    Calls ``resolve_current_invocation``/``find_usage_for_resolution`` by
    bare module-global name so tests can monkeypatch either in isolation.
    The default (non-``--json``) human output is ``format_compact``'s single
    all-info line (Phase 7); the prior multi-line render is
    retired.

    Phase 8: quota is fetched directly from the Anthropic OAuth
    usage endpoint via ``_fetch_anthropic_usage``/``parse_quota``; the
    ccstatusline cache is no longer consulted at all. A failed/unavailable
    fetch yields ``quota=None``, which ``build_report``/``format_compact``
    already render fail-closed (``"quota unavailable"``).

    Phase 9: quota is scoped to the caller's OWN provider AND own
    model via ``fetch_quota_for_caller``; an Anthropic caller still gets the
    Phase 8 Anthropic OAuth path unchanged, while an OpenAI/Codex caller now
    gets its own ChatGPT/Codex quota instead.
    """

    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = _build_argparser()
    args = parser.parse_args(effective_argv)

    env = os.environ

    resolution = resolve_current_invocation(
        home=Path.home(),
        cwd=os.getcwd(),
        env=env,
        argv=effective_argv,
        identity_nonce=args.identity_nonce,
    )

    raw_outcome_log = _env_get(env, "JSWARM_CTX_OUTCOME_LOG")
    outcome_log_path = (
        Path(raw_outcome_log) if raw_outcome_log else _default_outcome_log(env)
    )

    usage = find_usage_for_resolution(
        resolution=resolution,
        outcome_log_path=outcome_log_path,
        env=env,
    )

    quota = fetch_quota_for_caller(usage=usage, env=env)

    report = build_report(
        resolution=resolution,
        usage=usage,
        env=env,
        quota=quota,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_compact(report))

    return 1 if usage.status == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
