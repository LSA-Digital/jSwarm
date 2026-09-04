"""Read-only ColGREP health/status report generator.

COM-241 AC-5 freezes this public API in RED tests first (see
``jswarm/tests/test_colgrep_status_report.py`` and
``.jswarm/plans/COM-241/COM-241.specs.status-report.md``). This module implements the
GREEN phase: ``build_report`` composes the ``colgrep.status-report.v1`` payload purely
over the injectable ``Probes`` seam (no direct I/O, fail-open per probe), and
``render_markdown`` renders that payload as Markdown (a pure function of the payload).

The retro rule this encodes (HAS-520 status-before-recovery): the FIRST answer is
always "is a rebuild currently running?" plus the active-jobs list, rendered before
any table, advisory, or recommendation. Per-index health distinguishes "listed" from
"queryable" (a base can be listed with docs but fail a live query — the malformed-base
incident case) and recommendations only ever render as advisories AFTER the status
tables, in a goal-oriented ordered action sequence (NFR-241-7).
"""

from __future__ import annotations

import re
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypedDict

SCHEMA_VERSION = "colgrep.status-report.v1"
CODE_BACKEND = "http://localhost:3280"
CONTENT_BACKEND = "http://localhost:3281"

_GENERATION_RE = re.compile(r"^(?P<prefix>.+)-full-g\d+-[0-9a-f]+$")

_LAUNCHD_COMPONENTS = ("overlay-fleet-supervisor", "watcher", "health-check")

# COM-241 AC-6 jCritic delta finding (residual of finding #6, reopened at the
# report layer): `colgrep_launchd_control.py` correctly treats
# overlay-fleet-supervisor/watcher as LONG-RUNNING — `loaded` alone is not
# proof of "up" for them, a live pid is also required, or the daemon may be
# crash-looping while launchd still reports the label loaded. health-check is
# PERIODIC and is expected to be loaded-without-a-live-pid between runs, so
# `loaded` alone is sufficient for it. This set is asserted (in the test
# module only) to equal `colgrep_launchd_control.LONG_RUNNING_COMPONENTS` so
# the two modules never drift — this module must NOT import
# colgrep_launchd_control at module load time (that module imports
# `_parse_launchd_loaded` from here, so a module-level import back would be
# circular).
_LONG_RUNNING_LAUNCHD = frozenset({"overlay-fleet-supervisor", "watcher"})


class ActiveRebuildScanPayload(TypedDict, total=False):
    jobs: list[dict[str, Any]]
    stale_active_ops: list[dict[str, Any]]


ActiveRebuildScan = list[dict[str, Any]] | ActiveRebuildScanPayload


def _is_live_pid(pid: Any) -> bool:
    """A live pid is a real positive integer (bool excluded — a stray
    True/False must never be mistaken for a live pid)."""
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0


def _pid_is_live(pid: Any) -> bool:
    if not _is_live_pid(pid):
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_active_ops_jobs(
    active_ops_dir: Path,
    *,
    now: float,
    pid_live_fn: Callable[[Any], bool] = _pid_is_live,
) -> dict[str, list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    if not active_ops_dir.exists():
        return {"active": active, "stale": stale}
    for op_file in sorted(active_ops_dir.glob("op-*.json")):
        try:
            payload_raw = json.loads(op_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload_raw, dict) or payload_raw.get("kind") != "overlay-build":
            continue
        payload: dict[str, Any] = payload_raw
        details_raw = payload.get("details")
        details: dict[str, Any] = details_raw if isinstance(details_raw, dict) else {}
        repo = str(details.get("worktree") or details.get("index") or "unknown")
        elapsed_s: Optional[float] = None
        started_at = payload.get("started_at")
        if isinstance(started_at, str):
            try:
                dt = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                elapsed_s = max(0.0, now - dt.timestamp())
            except ValueError:
                elapsed_s = None
        job = {"pid": payload.get("pid"), "repo": repo, "elapsed_s": elapsed_s, "log": str(op_file)}
        if pid_live_fn(payload.get("pid")):
            active.append(job)
        else:
            stale.append({**job, "stale_reason": "pid-not-live"})
    return {"active": active, "stale": stale}

# COM-241 AC-6 Bug 2 fix (HAS-520 gap): the original rebuild-scan regex only matched
# native `colgrep init` / mem-guard / refresh-indices processes and was BLIND to a
# live `colgrep_worktree.py build-overlay` (or `refresh-if-stale`) encode — during a
# live overlay build the status report falsely said "Rebuild running: NO". Match
# both the `jswarm/colgrep-worktree` wrapper invocation and the raw
# `colgrep_worktree.py` module invocation.
_OVERLAY_BUILD_RE = re.compile(r"colgrep[-_]worktree(?:\.py)?\s+(?:build-overlay|refresh-if-stale)")
_REBUILD_SCAN_RE = re.compile(
    r"colgrep init|colgrep_mem_guard|refresh-indices|colgrep_onboard_or_refresh|"
    + _OVERLAY_BUILD_RE.pattern
)


@dataclass
class Probes:
    """Injectable read-only I/O seam — ALL external effects behind callables so tests fake them with zero live deps."""

    backend_up: Callable[[str], dict[str, Any]]
    list_indices: Callable[[str], list[str]]
    index_stats: Callable[[str], dict[str, Any]]
    probe_queryable: Callable[[str], dict[str, Any]]
    scan_active_rebuilds: Callable[[], ActiveRebuildScan]
    index_dir_size: Callable[[str], int | None]
    index_last_updated: Callable[[str], float | None]
    launchd_state: Callable[[], dict[str, Any]]
    memguard_available: Callable[[], bool]
    eta_estimate: Callable[[str], dict[str, Any] | None]
    # Optional (Finding #10): best-effort container RSS-vs-cap probe. Defaults to
    # None so existing callers/tests that don't supply it get an honest "no probe"
    # container state rather than a fabricated healthy default.
    container_stats: Callable[[], dict[str, Any] | None] | None = None
    # Optional (COM-241 Phase B): read-only fleet-plan diagnostic snapshot
    # (`colgrep_overlay_fleet_supervisor.py plan`). Defaults to None so existing
    # callers/tests that don't supply it get the back-compat behavior of no
    # worktree-level action_sequence entries.
    fleet_plan: Callable[[], dict[str, Any] | None] | None = None
    # Optional (COM-289 BR-15): registry-integrity lint findings, reusing
    # `colgrep_worktree._registry_integrity_findings()` (the same BR-05 detection
    # already wired into `health`) via an injected probe rather than reimplementing
    # detection here. Defaults to None so absent callers get an honest "no probe"
    # row instead of a fabricated-clean one — report/health parity is the point.
    registry_integrity_findings: Callable[[], list[dict[str, Any]]] | None = None
    # Optional (COM-289 BR-08): cross-plane disagreement findings, reusing
    # `colgrep_worktree._status_plane_disagreements()` (the same detection already
    # wired into `health`) via an injected probe rather than reimplementing it here.
    # Defaults to None so absent callers get an honest "no probe" row instead of a
    # fabricated-clean one — report/health parity is the point.
    status_plane_disagreements: Callable[[], list[dict[str, Any]]] | None = None


# -----------------------------------------------------------------------------
# Small pure helpers (no I/O) shared by build_report/render_markdown.
# -----------------------------------------------------------------------------


def _resolve_raw_dir(index_name: str, candidate_names: list) -> list:
    """Return the subset of candidate dir names that are VALID raw dirs for index_name
    (exact `index_name`, or `index_name-<hexhash>`), EXCLUDING sibling families
    (`index_name-wt-…`, `index_name-full-…`, `…-overlay`). Caller picks newest by mtime."""
    hash_re = re.compile(rf"^{re.escape(index_name)}-([0-9a-f]+)$")
    resolved: list = []
    for name in candidate_names:
        if not isinstance(name, str):
            continue
        if name == index_name:
            resolved.append(name)
            continue
        if "-wt-" in name or "-full-" in name or name.endswith("-overlay"):
            continue
        if hash_re.match(name):
            resolved.append(name)
    return resolved


def _parse_rebuild_target(cmd: str) -> dict:
    """Parse the `colgrep init` target repo structurally (the FIRST filesystem-path-shaped
    positional after `init`, and after `--` for a mem-guard wrapper). A path-shaped token
    starts with `/`, `~`, or `./` — this distinguishes the repo from a later flag's own
    value (e.g. `--index common`), which is never path-shaped in practice.
    Return {"repo": str|None, "confidence": "high"|"low"}."""
    if not cmd or not isinstance(cmd, str):
        return {"repo": None, "confidence": "low"}
    tokens = cmd.split()
    if "--" in tokens:
        last_dashdash = len(tokens) - 1 - tokens[::-1].index("--")
        tokens = tokens[last_dashdash + 1 :]
    if "init" not in tokens:
        return {"repo": None, "confidence": "low"}
    init_idx = tokens.index("init")
    rest = tokens[init_idx + 1 :]
    non_flag_tokens = [tok for tok in rest if not tok.startswith("-")]
    path_shaped = [tok for tok in non_flag_tokens if tok.startswith(("/", "~", "./"))]
    if path_shaped:
        return {"repo": path_shaped[0], "confidence": "high"}
    if not non_flag_tokens:
        return {"repo": None, "confidence": "low"}
    return {"repo": None, "confidence": "low"}


def _parse_etime(etime_s: str) -> Optional[float]:
    """Parse `ps -o etime` format ([[dd-]hh:]mm:ss) into elapsed seconds."""
    try:
        normalized = etime_s.replace("-", ":")
        parts = [int(p) for p in normalized.split(":")]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    if len(parts) == 3:
        days = 0
        hours, minutes, secs = parts
    else:
        days, hours, minutes, secs = parts[-4:]
    return days * 86400 + hours * 3600 + minutes * 60 + secs


def _parse_overlay_target(cmd: str) -> dict:
    """Parse a `colgrep-worktree build-overlay` / `refresh-if-stale` command line for
    its worktree/repo target. An explicit `--worktree <path>` value is high-confidence;
    otherwise fall back to the ticket positional argument right after the subcommand
    (low-confidence — a ticket key, not necessarily a filesystem path)."""
    if not cmd or not isinstance(cmd, str):
        return {"repo": None, "confidence": "low"}
    tokens = cmd.split()
    if "--worktree" in tokens:
        idx = tokens.index("--worktree")
        if idx + 1 < len(tokens):
            return {"repo": tokens[idx + 1], "confidence": "high"}
    for subcmd in ("build-overlay", "refresh-if-stale"):
        if subcmd in tokens:
            idx = tokens.index(subcmd)
            rest = tokens[idx + 1 :]
            non_flag_tokens = [tok for tok in rest if not tok.startswith("-")]
            if non_flag_tokens:
                return {"repo": non_flag_tokens[0], "confidence": "low"}
    return {"repo": None, "confidence": "low"}


def _scan_ps_lines_for_rebuilds(ps_stdout: str) -> list:
    """Pure: parse `ps -axo pid,etime,command` stdout into raw rebuild-job dicts.

    Extracted from the live `scan_active_rebuilds` probe (COM-241 AC-6 Bug 2) so the
    overlay-build/refresh-if-stale regex extension is directly testable without
    mocking `subprocess` — feed captured `ps` stdout text straight in. Matches the
    original native-rebuild patterns PLUS the overlay-build/refresh-if-stale worktree
    encode commands, and picks the right target parser (`_parse_overlay_target` vs
    `_parse_rebuild_target`) per matched line.
    """
    jobs: list[dict[str, Any]] = []
    lines = ps_stdout.splitlines() if ps_stdout else []
    for line in lines[1:]:
        line = line.strip()
        if not line or not _REBUILD_SCAN_RE.search(line):
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, etime_s, cmd = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        elapsed_s = _parse_etime(etime_s)
        if _OVERLAY_BUILD_RE.search(cmd):
            target = _parse_overlay_target(cmd)
        else:
            target = _parse_rebuild_target(cmd)
        repo = target.get("repo") or "unknown"
        jobs.append({"pid": pid, "repo": repo, "elapsed_s": elapsed_s, "log": ""})
    return jobs


def _parse_launchd_loaded(output: str, component: str) -> dict:
    """Parse this host's launchd job-list output (`jswarm.platform.macos.list_launchd_jobs`);
    EXACT-match the final label column == f'com.colgrep.{component}'.
    Return {"loaded": bool|None, "pid": int|None}.

    A VALID row has an int-or-`-` PID in its first column. If at least one valid row
    is parsed, `loaded` is the exact-label membership among those rows (True/False —
    proven, not guessed). If ZERO valid rows are parsed (malformed output, a header
    line, or anything the parser cannot make sense of), that is parser uncertainty,
    not a healthy/absent default -> `loaded: None`, `pid: None`.
    """
    if output is None or not isinstance(output, str) or not output.strip():
        return {"loaded": None, "pid": None}
    label = f"com.colgrep.{component}"
    valid_rows_found = False
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = line.split()
        if not cols:
            continue
        pid_token = cols[0]
        if pid_token != "-" and not pid_token.lstrip("-").isdigit():
            # Not a valid PID column (e.g. a header like "PID") -> not a valid row.
            continue
        valid_rows_found = True
        if cols[-1] == label:
            pid = None
            if pid_token not in ("-", "") and pid_token.lstrip("-").isdigit():
                try:
                    pid = int(pid_token)
                except ValueError:
                    pid = None
            return {"loaded": True, "pid": pid}
    if not valid_rows_found:
        return {"loaded": None, "pid": None}
    return {"loaded": False, "pid": None}


def _safe_call(fn: Callable[..., Any], *args: Any, default: Any = None) -> Any:
    """Fail-open probe invocation: any exception degrades to `default`, never raises."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - probe seam; a broken probe must never crash the report.
        return default


def _human_size(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "n/a"
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return "n/a"
    if value < 0:
        return "n/a"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units[:-1]:
        if value < 1024.0:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} {units[-1]}"


def _format_elapsed(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    try:
        total = int(max(0, seconds))
    except (TypeError, ValueError):
        return "n/a"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    return f"{secs}s"


def _format_last_updated(mtime: Optional[float], *, now: float) -> str:
    if mtime is None:
        return "n/a"
    try:
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return "n/a"
    age_human = _format_elapsed(now - mtime)
    return f"{dt.strftime('%Y-%m-%d %H:%MZ')} ({age_human} ago)"


def _repo_basename(repo: str) -> str:
    return repo.rstrip("/").rsplit("/", 1)[-1] if repo else ""


def _classify_types(index_names: list[str]) -> dict[str, str]:
    """base | overlay | generation | orphan-generation, per Section 2 of the spec."""
    types: dict[str, str] = {}
    generation_groups: dict[str, list[str]] = {}
    for name in index_names:
        if name.endswith("-overlay"):
            types[name] = "overlay"
            continue
        match = _GENERATION_RE.match(name)
        if match:
            types[name] = "generation"
            generation_groups.setdefault(match.group("prefix"), []).append(name)
            continue
        types[name] = "base"
    for members in generation_groups.values():
        if len(members) > 1:
            for name in members:
                types[name] = "orphan-generation"
    return types


# -----------------------------------------------------------------------------
# build_report — pure over probes, fail-open.
# -----------------------------------------------------------------------------


def build_report(probes: "Probes", *, now: float) -> dict:
    """Return the colgrep.status-report.v1 payload.

    Pure over the probes (no direct I/O). Fail-open: a probe that raises -> that
    cell is 'n/a', report still built.
    """

    # --- Section 0: active rebuild(s) — answered FIRST, per the retro rule. ---
    # Fail-CLOSED tri-state (Finding #5, most important gate): jobs -> running;
    # [] -> none (proven clear); the probe RAISING -> unknown. Unknown gates like
    # running: no mutating action may ever be recommended on a guess.
    try:
        raw_scan = probes.scan_active_rebuilds()
        stale_active_ops: list[dict[str, Any]] = []
        if isinstance(raw_scan, dict):
            raw_jobs = raw_scan.get("jobs") or []
            stale_raw = raw_scan.get("stale_active_ops") or []
            if isinstance(stale_raw, list):
                stale_active_ops = [item for item in stale_raw if isinstance(item, dict)]
        else:
            raw_jobs = raw_scan
        if not isinstance(raw_jobs, list):
            raw_jobs = []
        rebuild_status = "running" if raw_jobs else "none"
    except Exception:  # noqa: BLE001 - safety gate: distinguish "raised" from "returned empty".
        raw_jobs = []
        stale_active_ops = []
        rebuild_status = "unknown"

    active_jobs: list[dict[str, Any]] = []
    rebuild_targets: dict[str, dict[str, Any]] = {}
    for job in raw_jobs:
        if not isinstance(job, dict):
            continue
        pid = job.get("pid")
        repo = job.get("repo") or ""
        elapsed_s = job.get("elapsed_s")
        log = job.get("log") or ""
        elapsed_human = _format_elapsed(elapsed_s)
        active_jobs.append({"pid": pid, "repo": repo, "elapsed_human": elapsed_human, "log": log})
        target_name = _repo_basename(repo)
        if target_name and target_name not in rebuild_targets:
            rebuild_targets[target_name] = {"pid": pid, "elapsed_s": elapsed_s, "elapsed_human": elapsed_human}
    running = rebuild_status == "running"
    active_rebuild = {
        "running": running,
        "status": rebuild_status,
        "jobs": active_jobs,
        "stale_active_ops": stale_active_ops,
    }

    # --- infrastructure probes (backend up/down, launchd, memory guard). ---
    code_up = _safe_call(probes.backend_up, CODE_BACKEND, default={"up": False, "detail": "probe failed"}) or {}
    content_up = _safe_call(probes.backend_up, CONTENT_BACKEND, default={"up": False, "detail": "probe failed"}) or {}
    launchd = _safe_call(probes.launchd_state, default={}) or {}
    memguard_ok = bool(_safe_call(probes.memguard_available, default=False))

    infrastructure: list[dict[str, Any]] = []
    infrastructure.append(
        {
            "component": "code backend `:3280`",
            "state": "🟢 up" if code_up.get("up") else "❌ down",
            "detail": str(code_up.get("detail", "")),
        }
    )
    infrastructure.append(
        {
            "component": "content backend `:3281`",
            "state": "🟢 up" if content_up.get("up") else "❌ down",
            "detail": str(content_up.get("detail", "")),
        }
    )
    # Finding #10: never render 🟢 running for the container from HTTP reachability
    # alone. Best-effort RSS-vs-cap probe (if the caller supplies one); otherwise an
    # honest "no probe" rather than a fabricated healthy default.
    container_stats_fn = getattr(probes, "container_stats", None)
    container_info = _safe_call(container_stats_fn, default=None) if callable(container_stats_fn) else None
    container_rss_bytes: Optional[float] = None
    container_cap_bytes: Optional[float] = None
    if isinstance(container_info, dict) and isinstance(container_info.get("rss_bytes"), (int, float)):
        container_rss_bytes = container_info["rss_bytes"]
        cap = container_info.get("cap_bytes")
        container_cap_bytes = cap if isinstance(cap, (int, float)) else None
        cap_text = f" / cap {_human_size(container_cap_bytes)}" if container_cap_bytes is not None else ""
        infrastructure.append(
            {
                "component": "next-plaid container(s)",
                "state": f"🟢 running (RSS {_human_size(container_rss_bytes)}{cap_text})",
                "detail": "docker stats --no-stream RSS probe",
            }
        )
    else:
        infrastructure.append(
            {
                "component": "next-plaid container(s)",
                "state": "❔ n/a — no container/RSS probe",
                "detail": "no container/RSS probe available (HTTP reachability alone is not container health)",
            }
        )
    infrastructure.append(
        {
            "component": "memory-guard (`jswarm/colgrep_mem_guard.sh`)",
            "state": "🟢 available" if memguard_ok else "❌ unavailable",
            "detail": "present + executable" if memguard_ok else "not found or not executable",
        }
    )
    # Finding #6 residual (jCritic delta): "up" for gating/action purposes is
    # NOT the same as raw "loaded" for a long-running component — see
    # _LONG_RUNNING_LAUNCHD above. Table A and the action-sequence gate below
    # both key off launchd_up_state, to avoid the false steady-state a
    # crash-looping-but-loaded supervisor would otherwise render.
    launchd_up_state: dict[str, bool] = {}
    for key in _LAUNCHD_COMPONENTS:
        info = launchd.get(key) if isinstance(launchd, dict) else None
        info = info if isinstance(info, dict) else {}
        loaded = info.get("loaded")
        loaded = loaded if isinstance(loaded, bool) else None
        pid = info.get("pid")
        crash_loop = loaded is True and key in _LONG_RUNNING_LAUNCHD and not _is_live_pid(pid)
        launchd_up_state[key] = loaded is True and not crash_loop
        if crash_loop:
            state = "⚠️ loaded/no-live-pid (crash-loop?)"
            detail = "loaded but no live pid — daemon may be crash-looping"
        elif loaded is True:
            state = f"🟢 loaded (pid {pid})" if pid else "🟢 loaded"
            detail = f"pid {pid}" if pid else "loaded"
        elif loaded is False:
            state = "⏸ not loaded"
            detail = ""
        else:
            state = "❓ unknown"
            detail = ""
        infrastructure.append(
            {
                "component": f"launchd `com.colgrep.{key}`",
                "state": state,
                "detail": detail,
            }
        )
    infrastructure.append(
        {
            "component": "active-rebuild jobs",
            "state": (
                f"⚙️ {len(active_jobs)} running"
                if running
                else ("❓ unknown" if rebuild_status == "unknown" else "🟢 none")
            ),
            "detail": ", ".join(f"PID {job['pid']}" for job in active_jobs) if active_jobs else "no active rebuild",
        }
    )
    # COM-289 BR-15: registry-integrity lint parity with `health` (BR-05's linter,
    # reused via probe — see the `registry_integrity_findings` field docstring).
    # Fail-CLOSED tri-state, same shape as Section 0's active-rebuild scan: probe
    # absent -> honest "no probe" (never fabricated-clean); probe raises -> unknown
    # (never silently clean); probe returns [] -> clean; probe returns findings ->
    # loud, specific violation list. Silence must mean "checked and agreed", never
    # "not checked".
    registry_findings_fn = getattr(probes, "registry_integrity_findings", None)
    if not callable(registry_findings_fn):
        infrastructure.append(
            {
                "component": "registry integrity lint",
                "state": "❔ n/a — no probe",
                "detail": "no registry-integrity probe available (BR-05 findings not checked)",
            }
        )
    else:
        try:
            registry_findings_raw = registry_findings_fn()
            registry_findings = [f for f in registry_findings_raw if isinstance(f, dict)] if isinstance(registry_findings_raw, list) else []
            registry_lint_status = "ok"
        except Exception as exc:  # noqa: BLE001 - safety gate: distinguish raised (unknown) from empty (clean).
            registry_findings = []
            registry_lint_status = "unknown"
            registry_lint_error = str(exc)
        if registry_lint_status == "unknown":
            infrastructure.append(
                {
                    "component": "registry integrity lint",
                    "state": "❓ unknown",
                    "detail": f"registry-integrity probe failed: {registry_lint_error}",
                }
            )
        elif registry_findings:
            # COM-289 BR-17: "api-index-equals-base-informational" (no dedicated
            # index -- the honest base-authoritative encoding, see
            # colgrep_worktree._registry_integrity_findings' docstring) must not
            # render as a ⚠️ WARN row, but must never be silently dropped either.
            hard_findings = [f for f in registry_findings if f.get("class") != "api-index-equals-base-informational"]
            informational_findings = [f for f in registry_findings if f.get("class") == "api-index-equals-base-informational"]
            if hard_findings:
                classes = ", ".join(sorted({str(f.get("class") or "unknown") for f in hard_findings}))
                tickets = ", ".join(str(f.get("ticket") or "?") for f in hard_findings)
                detail = f"classes: {classes}; tickets: {tickets}"
                if informational_findings:
                    info_tickets = ", ".join(str(f.get("ticket") or "?") for f in informational_findings)
                    detail += f"; plus {len(informational_findings)} informational (no dedicated index, likely honest base-authoritative): {info_tickets}"
                infrastructure.append(
                    {
                        "component": "registry integrity lint",
                        "state": f"⚠️ {len(hard_findings)} finding(s) — {classes}",
                        "detail": detail,
                    }
                )
            else:
                tickets = ", ".join(str(f.get("ticket") or "?") for f in informational_findings)
                infrastructure.append(
                    {
                        "component": "registry integrity lint",
                        "state": f"ℹ️ {len(informational_findings)} informational — api-index-equals-base (no dedicated index)",
                        "detail": f"tickets: {tickets}; likely honest base-authoritative encoding, not corruption (COM-289 BR-17)",
                    }
                )
        else:
            infrastructure.append(
                {
                    "component": "registry integrity lint",
                    "state": "🟢 clean",
                    "detail": "no registry-integrity violations",
                }
            )

    # COM-289 BR-08: status-plane disagreement parity with `health` (reused via
    # probe — see the `status_plane_disagreements` field docstring). Same fail-
    # CLOSED tri-state as the registry-integrity row above: probe absent -> honest
    # "no probe" (never fabricated-clean); probe raises -> unknown (never silently
    # clean); probe returns [] -> clean; probe returns findings -> loud DISAGREEMENT
    # line naming which planes disagree and what each claims. Silence must mean
    # "checked and agreed", never "not checked" (retro §WRONG-8).
    status_plane_fn = getattr(probes, "status_plane_disagreements", None)
    if not callable(status_plane_fn):
        infrastructure.append(
            {
                "component": "status-plane disagreement",
                "state": "❔ n/a — no probe",
                "detail": "no status-plane probe available (BR-08 cross-check not performed)",
            }
        )
    else:
        try:
            status_plane_raw = status_plane_fn()
            status_plane_findings = [f for f in status_plane_raw if isinstance(f, dict)] if isinstance(status_plane_raw, list) else []
            status_plane_check_status = "ok"
        except Exception as exc:  # noqa: BLE001 - safety gate: distinguish raised (unknown) from empty (clean).
            status_plane_findings = []
            status_plane_check_status = "unknown"
            status_plane_error = str(exc)
        if status_plane_check_status == "unknown":
            infrastructure.append(
                {
                    "component": "status-plane disagreement",
                    "state": "❓ unknown",
                    "detail": f"status-plane probe failed: {status_plane_error}",
                }
            )
        elif status_plane_findings:
            tickets = ", ".join(str(f.get("ticket") or "?") for f in status_plane_findings)
            claims = "; ".join(
                f"{f.get('ticket') or '?'}: queue={f.get('queue_state')!r} vs watcher={f.get('watcher_state')!r}"
                for f in status_plane_findings
            )
            infrastructure.append(
                {
                    "component": "status-plane disagreement",
                    "state": f"🚨 DISAGREEMENT — {len(status_plane_findings)} worktree(s) — {tickets}",
                    "detail": claims,
                }
            )
        else:
            infrastructure.append(
                {
                    "component": "status-plane disagreement",
                    "state": "🟢 clean",
                    "detail": "queue plane and watcher-liveness plane agree for every registered worktree",
                }
            )

    # --- indices table (Section 2). ---
    # Finding #4 (listing gate, fail-CLOSED): distinguish "listing raised" (unknown)
    # from "listing succeeded with an empty result" (ok, 0 indices).
    try:
        index_names_raw = probes.list_indices(CODE_BACKEND)
        if not isinstance(index_names_raw, list):
            index_names_raw = []
        listing_status = "ok"
        listing_error: Optional[str] = None
    except Exception as exc:  # noqa: BLE001 - safety gate: distinguish raised vs empty.
        index_names_raw = []
        listing_status = "unknown"
        listing_error = str(exc)
    index_names = [str(name) for name in index_names_raw]
    types = _classify_types(index_names)

    indices: list[dict[str, Any]] = []
    known_size_bytes: list[int] = []
    for name in index_names:
        type_ = types.get(name, "base")
        stats = _safe_call(probes.index_stats, name, default={}) or {}
        docs = stats.get("num_documents") if isinstance(stats, dict) else None
        docs = docs if isinstance(docs, int) and not isinstance(docs, bool) else 0

        size_bytes = _safe_call(probes.index_dir_size, name, default=None)
        if isinstance(size_bytes, int):
            known_size_bytes.append(size_bytes)
        size_human = _human_size(size_bytes)

        mtime = _safe_call(probes.index_last_updated, name, default=None)
        last_updated_human = _format_last_updated(mtime, now=now)

        target = rebuild_targets.get(name)
        if target is not None:
            health = "⏳ building"
            rebuild_elapsed = target["elapsed_human"]
            eta_info = _safe_call(probes.eta_estimate, name, default=None)
            if isinstance(eta_info, dict):
                eta = str(eta_info.get("eta") or "unknown")
                est_final_size = str(eta_info.get("est_final_size") or "unknown")
            else:
                eta = "unknown"
                est_final_size = "unknown"
        else:
            rebuild_elapsed = "—"
            eta = "—"
            est_final_size = "—"
            # R3 (COM-241 AC-5): classify generation/orphan-generation rows by TYPE
            # FIRST, regardless of docs count. A listed generation with docs<=0 (or
            # unknown/absent stats) is "listed-unprobed" / "orphan-listed" — never the
            # base-style "❌ missing", which implies an actionable missing base.
            # "❌ missing" is reserved for base/overlay rows lacking stats/exists.
            if type_ == "orphan-generation":
                # Finding #9: unprobed rows are never labeled queryable.
                health = "🔸 orphan-listed"
            elif type_ == "generation":
                # generation: deliberately not post-probed (perf note); "listed", not "queryable".
                health = "◽ listed-unprobed"
            elif docs <= 0:
                health = "❌ missing"
            else:
                # base/overlay with docs > 0.
                # Finding #3 (fail-CLOSED): hit True -> proven queryable; hit False ->
                # proven not-queryable; the PROBE RAISING -> unknown (never default True).
                try:
                    queryable = probes.probe_queryable(name)
                    hit = (
                        bool(queryable.get("queryability_hit", True)) if isinstance(queryable, dict) else True
                    )
                    health = "✅ queryable" if hit else "⚠️ listed-not-queryable"
                except Exception:  # noqa: BLE001 - safety gate: unknown, never a healthy default.
                    health = "⚠️ queryability-unknown"

        indices.append(
            {
                "index": name,
                "type": type_,
                "health": health,
                "docs": docs,
                "size_human": size_human,
                "last_updated_human": last_updated_human,
                "rebuild_elapsed": rebuild_elapsed,
                "eta": eta,
                "est_final_size": est_final_size,
            }
        )

    # --- Section 3: summary + advisories. ---
    if listing_status == "unknown":
        # Finding #4: an index-listing failure is never rendered as "0 healthy
        # indices" — every downstream count is an explicit n/a, not a false green.
        summary = {
            "bases": "n/a",
            "overlays": "n/a",
            "orphans": "n/a",
            "total_size_human": "n/a",
            "advisories": [f"index listing failed: {listing_error} — counts unavailable until listing succeeds"],
        }
    else:
        bases = sum(1 for row in indices if row["type"] == "base")
        overlays = sum(1 for row in indices if row["type"] == "overlay")
        orphans = sum(1 for row in indices if row["type"] == "orphan-generation")
        total_size_human = _human_size(sum(known_size_bytes)) if known_size_bytes else "n/a"

        advisories: list[str] = []
        for row in indices:
            if row["type"] == "base" and row["health"] == "⚠️ listed-not-queryable":
                advisories.append(
                    f"{row['index']}: listed but not queryable — quiesced reload + fleet-supervisor restart likely required"
                )
            if row["type"] == "base" and row["health"] == "⚠️ queryability-unknown":
                advisories.append(
                    f"{row['index']}: queryability probe failed (unknown) — verify manually before deciding next step"
                )
            if row["type"] == "base" and row["health"] == "❌ missing":
                advisories.append(f"{row['index']}: missing/corrupt on disk — memory-guarded rebuild required")
        orphan_prefixes: dict[str, int] = {}
        for row in indices:
            if row["type"] == "orphan-generation":
                match = _GENERATION_RE.match(row["index"])
                prefix = match.group("prefix") if match else row["index"]
                orphan_prefixes[prefix] = orphan_prefixes.get(prefix, 0) + 1
        # Finding #10: an orphan pile atop high/unknown container RSS is elevated —
        # evicting stale generations must happen BEFORE any rebuild is attempted.
        rss_known_low = (
            container_rss_bytes is not None
            and container_cap_bytes is not None
            and container_rss_bytes < 0.8 * container_cap_bytes
        )
        for prefix, count in orphan_prefixes.items():
            if orphans >= 3 and not rss_known_low:
                advisories.append(
                    f"{count} orphan generations for `{prefix}` — evict orphans BEFORE any rebuild "
                    "(memory-pressured engine cannot commit)"
                )
            else:
                advisories.append(f"{count} orphan generations for `{prefix}` — operator-gated cleanup recommended")

        summary = {
            "bases": bases,
            "overlays": overlays,
            "orphans": orphans,
            "total_size_human": total_size_human,
            "advisories": advisories,
        }

    # --- Section 4: ordered, goal-oriented action sequence (NFR-241-7). ---
    action_sequence: list[dict[str, Any]] = []
    step_counter = 0

    def _append_step(
        *,
        component: str,
        action: str,
        command: str,
        operator_gate: bool,
        verify: str,
        blocked_by_missing_parameter: Optional[list] = None,
    ) -> None:
        nonlocal step_counter
        step_counter += 1
        step: dict[str, Any] = {
            "step": step_counter,
            "component": component,
            "action": action,
            "command": command,
            "operator_gate": operator_gate,
            "verify": verify,
        }
        if blocked_by_missing_parameter:
            # Fix 3 (COM-241 AC-6): a command that still needs an operator-supplied
            # value (`<cap>`, `<repo>`) is kept as a placeholder for readability, but
            # is explicitly flagged as not-yet-runnable so an agent/operator resolves
            # the parameter rather than executing an un-runnable command as-is.
            step["blocked_by_missing_parameter"] = list(blocked_by_missing_parameter)
        action_sequence.append(step)

    if rebuild_status in ("running", "unknown"):
        # Findings #1/#5: running OR unknown gates identically — NO mutating action
        # (no reload/rebuild/restart/cleanup) may ever be recommended on a guess.
        if rebuild_status == "running":
            _append_step(
                component="active-rebuild",
                action="wait",
                command="take no action; re-run status until no active colgrep init/guard process remains",
                operator_gate=True,
                verify="re-run status: no active colgrep init/colgrep_mem_guard process for this repo",
            )
        else:
            _append_step(
                component="active-rebuild",
                action="resolve-rebuild-uncertainty",
                command="resolve rebuild-scan uncertainty (re-run status / check `ps` manually)",
                operator_gate=False,
                verify="a subsequent status run (or manual `ps` check) proves no colgrep init/guard process is running",
            )
    elif listing_status == "unknown":
        # Finding #4: listing gate — read-only re-probe only, never a mutation.
        _append_step(
            component="index-listing",
            action="reprobe-listing",
            command=f"re-probe list_indices (check :3280/:3281 reachability) — last error: {listing_error}",
            operator_gate=False,
            verify="index listing succeeds (no exception) and returns index names",
        )
    else:
        base_rows = [row for row in indices if row["type"] == "base"]
        missing_base = next((row["index"] for row in base_rows if row["health"] == "❌ missing"), None)
        warning_base = next((row["index"] for row in base_rows if row["health"] == "⚠️ listed-not-queryable"), None)
        unknown_query_base = next(
            (row["index"] for row in base_rows if row["health"] == "⚠️ queryability-unknown"), None
        )

        # Fix 3 (COM-241 AC-6): orphan generations get a REAL guarded, agent-runnable
        # action, not just advisory text — and never `generation_reaper reap` (that
        # tool refuses without a worktree-generation manifest and preserves served
        # names) and never `colgrep_index_lifecycle.py cleanup` (a bare LRU/global
        # sweep, not scoped to the observed orphan family, and pairs a raw launchd
        # job unload with NO restart, leaving the fleet unsupervised). The
        # guarded primitive for this exact case is `colgrep_orphan_cleanup.py`
        # (`plan`/`apply --family <family>`), which does its own finally-safe
        # driver stop+restart — this report must never emit a bare launchd
        # job-unload command itself. Placed first, before any rebuild step, per
        # "evict orphans BEFORE any rebuild".
        orphan_prefixes: dict[str, int] = {}
        for row in indices:
            if row["type"] == "orphan-generation":
                match = _GENERATION_RE.match(row["index"])
                prefix = match.group("prefix") if match else row["index"]
                orphan_prefixes[prefix] = orphan_prefixes.get(prefix, 0) + 1
        if orphan_prefixes:
            orphan_prefix_list = ", ".join(f"`{prefix}`" for prefix in sorted(orphan_prefixes))
            if len(orphan_prefixes) == 1:
                # Single dominant family observed in Table B — the `--family` value
                # can be derived with confidence, so emit a concretely runnable command.
                orphan_family = next(iter(orphan_prefixes))
                orphan_family_blocked: Optional[list] = None
            else:
                # Multiple distinct orphan families observed — `--family` cannot be
                # uniquely derived, so the command is presented with a placeholder and
                # explicitly flagged as not-yet-runnable rather than guessing one.
                orphan_family = "<family>"
                orphan_family_blocked = ["family"]
            _append_step(
                component="orphan-generations",
                action="orphan-cleanup-preview",
                command=f".venv/bin/python jswarm/colgrep_orphan_cleanup.py plan --family {orphan_family} --json",
                operator_gate=True,
                verify=(
                    f"plan JSON lists the orphan generations for {orphan_prefix_list} as "
                    "ORPHAN-ELIGIBLE (not PROTECTED)"
                ),
                blocked_by_missing_parameter=orphan_family_blocked,
            )
            _append_step(
                component="orphan-generations",
                action="orphan-cleanup-apply",
                command=(
                    f".venv/bin/python jswarm/colgrep_orphan_cleanup.py apply --family {orphan_family} "
                    "--stop-driver --restart-driver --json"
                ),
                operator_gate=True,
                verify=f"re-run status: orphan generations for {orphan_prefix_list} no longer listed",
                blocked_by_missing_parameter=orphan_family_blocked,
            )

        if missing_base is not None:
            _append_step(
                component=missing_base,
                action="memory-guarded-rebuild",
                command=f"colgrep_mem_guard.sh --cap-gb <cap> -- colgrep init -y --force-cpu <repo-for-{missing_base}>",
                operator_gate=True,
                verify="'Indexed … (added: N)' printed + raw dir created",
                blocked_by_missing_parameter=["cap", "repo"],
            )
            _append_step(
                component="next-plaid",
                action="quiesced-reload",
                command="next-plaid/bin/nextplaid-quiesce-restart",
                operator_gate=True,
                verify=f"base {missing_base} becomes queryable after reload",
            )
            _append_step(
                component=missing_base,
                action="verify-queryable",
                command=f"POST /indices/{missing_base}/search_with_encoding",
                operator_gate=False,
                verify=f"response returns a hit for {missing_base} (✅ queryable, not ❌)",
            )
        elif warning_base is not None:
            _append_step(
                component="next-plaid",
                action="quiesced-reload",
                command="next-plaid/bin/nextplaid-quiesce-restart",
                operator_gate=True,
                verify=f"base {warning_base} becomes queryable after reload",
            )
            _append_step(
                component=warning_base,
                action="verify-queryable",
                command=f"POST /indices/{warning_base}/search_with_encoding",
                operator_gate=False,
                verify=f"response returns a hit for {warning_base} (✅ queryable, not ⚠️)",
            )
        elif unknown_query_base is not None:
            # Finding #3: unknown is never a healthy default — a read-only
            # diagnose step, never the mutating quiesced-reload.
            _append_step(
                component=unknown_query_base,
                action="diagnose-queryability",
                command=f"re-probe queryability for {unknown_query_base}; check next-plaid logs for the prior failure",
                operator_gate=False,
                verify=f"queryability probe for {unknown_query_base} returns a definite hit/no-hit (no longer unknown)",
            )

        # Findings #1/#2/#11: supervision deficits are independent of base health —
        # health-check, then watcher, then fleet-supervisor (a launchd state of
        # None/unknown is treated the same as "not loaded" — never a healthy default).
        # Fix 2/6 (COM-241 AC-6 jCritic pass): every mutating control step must be a
        # REAL agent-runnable command, never raw launchd-load prose — route
        # through the guarded `colgrep_launchd_control.py` wrapper (exact-label
        # match, before/after verification) instead. `restart` is used uniformly
        # rather than branching on a fabricated "never loaded" vs "loaded but died"
        # distinction the launchd-state probe cannot actually prove (both read as
        # `loaded is not True`) — `restart`'s own kickstart-then-unload/load
        # fallback already self-heals a not-yet-loaded component, so it is safe for
        # both cases without guessing.
        #
        # Finding #6 residual (jCritic delta, reopened at the report layer): gate
        # on launchd_up_state (raw "loaded" is not enough) — a long-running
        # component (fleet-supervisor/watcher) that is `loaded` but has no live pid
        # is a supervision deficit (crash-loop) just like "not loaded", and must
        # still emit the restart action rather than a false steady-state.
        for key, action_name, label in (
            ("health-check", "restart-health-check", "health-check"),
            ("watcher", "restart-watcher", "watcher"),
            ("overlay-fleet-supervisor", "restart-fleet-supervisor", "overlay-fleet-supervisor"),
        ):
            if launchd_up_state.get(key) is not True:
                _append_step(
                    component=label,
                    action=action_name,
                    command=f".venv/bin/python jswarm/platform/colgrep_launchd_control.py restart --component {key} --json",
                    operator_gate=True,
                    verify=f"colgrep_launchd_control.py status --component {key} --json shows loaded: true",
                )

        # COM-241 Phase B: consult the fleet-plan probe (read-only, fail-open) to
        # surface per-worktree build-blocked recommendations that the fleet
        # supervisor already computed, AFTER the infra/base/orphan/launchd
        # steps above but BEFORE the terminal verify-no-reflap advisory below.
        # A `fleet_plan is None` caller (back-compat) or a raising/malformed
        # probe both degrade to "no worktree actions" — a broken fleet-plan
        # probe must never crash the status report.
        fleet_plan_fn = getattr(probes, "fleet_plan", None)
        if callable(fleet_plan_fn):
            try:
                fleet_snapshot = fleet_plan_fn()
            except Exception:  # noqa: BLE001 - fail-open: never let a fleet-plan probe crash the report.
                fleet_snapshot = None
            if isinstance(fleet_snapshot, dict):
                worktree_records = fleet_snapshot.get("worktrees")
                if isinstance(worktree_records, list):
                    for record in worktree_records:
                        if not isinstance(record, dict):
                            continue
                        # Real fleet-plan worktree records (colgrep_overlay_fleet_supervisor.py
                        # `run_once`/`plan`) key on `path`, not a `worktree_key` field — derive
                        # the component key as the path's basename. Skip a record with a
                        # missing/empty path rather than guessing an identity for it.
                        record_path = record.get("path")
                        worktree_key = _repo_basename(str(record_path)) if record_path else ""
                        if not worktree_key:
                            continue
                        dependency_assessment = record.get("dependency_assessment")
                        dependency_assessment = dependency_assessment if isinstance(dependency_assessment, dict) else {}
                        actual_blocker = dependency_assessment.get("actual_blocker")
                        worktree_next_action = record.get("next_action")
                        component = f"worktree:{worktree_key}"
                        if actual_blocker == "overlay-fleet-supervisor-not-running":
                            _append_step(
                                component=component,
                                action="restart-fleet-supervisor",
                                command=(
                                    ".venv/bin/python jswarm/platform/colgrep_launchd_control.py restart "
                                    "--component overlay-fleet-supervisor --json"
                                ),
                                operator_gate=True,
                                verify=(
                                    "colgrep_launchd_control.py status --component overlay-fleet-supervisor "
                                    "--json shows daemon_up: true and the "
                                    f"{worktree_key} worktree action advancing from fleet-supervisor-not-running"
                                ),
                            )
                        elif actual_blocker in (
                            "lane-disabled",
                            "lane-config-unresolved",
                            "seed-capability-unavailable",
                            "large-build-lock-unavailable",
                        ):
                            # Operator-gated: a genuinely gated lane must never be
                            # reported as "just start it" — no supervisor restart
                            # command is emitted, only the fleet-plan's own
                            # recommended next_action as advisory text.
                            _append_step(
                                component=component,
                                action="operator-review-required",
                                command=(
                                    f"no automated command — operator must resolve `{actual_blocker}` for "
                                    f"{worktree_key} (recommended next_action: {worktree_next_action or 'see fleet-plan'})"
                                ),
                                operator_gate=True,
                                verify=(
                                    f"fleet-plan for {worktree_key} no longer reports "
                                    f"actual_blocker={actual_blocker}"
                                ),
                            )

        if action_sequence:
            _append_step(
                component="status",
                action="verify-no-reflap",
                command="re-run colgrep_status_report.py --format md; check fleet-plan base_health",
                operator_gate=False,
                verify="bases stay ✅ queryable, all launchd components stay loaded, no new base-restore churn or orphan generations",
            )
        # else: rebuild none + listing ok + every base ✅ queryable + all launchd
        # loaded -> steady state, no action steps (render_markdown emits the line).

    generated_at = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "active_rebuild": active_rebuild,
        "infrastructure": infrastructure,
        "listing_status": listing_status,
        "listing_error": listing_error,
        "indices": indices,
        "summary": summary,
        "action_sequence": action_sequence,
    }


# -----------------------------------------------------------------------------
# render_markdown — pure function of the payload.
# -----------------------------------------------------------------------------


def render_markdown(report: dict) -> str:
    """Render the status payload as Markdown.

    Pure function of the payload -> Markdown: lead banner + Table A (infra) +
    Table B (all indices) + Summary + Advisories + Section 4 ordered action sequence.
    """

    lines: list[str] = []
    generated_at = report.get("generated_at", "")
    lines.append(f"# ColGREP Health Check & Status — {generated_at}")
    lines.append("")

    active_rebuild = report.get("active_rebuild", {}) or {}
    rebuild_status = active_rebuild.get("status") or ("running" if active_rebuild.get("running") else "none")
    rebuild_label = {"running": "YES", "none": "NO", "unknown": "UNKNOWN"}.get(rebuild_status, "NO")
    lines.append(f"Rebuild running: {rebuild_label}")
    jobs = active_rebuild.get("jobs") or []
    if jobs:
        lines.append("Active jobs:")
        for job in jobs:
            lines.append(
                f"- PID {job.get('pid')} — {job.get('repo')} — elapsed {job.get('elapsed_human')} — log {job.get('log')}"
            )
        lines.append("⚠️ Do NOT recommend rebuild / reload / overlay / cleanup while a guarded build is running.")
    else:
        lines.append("Active jobs: none")
    stale_active_ops = active_rebuild.get("stale_active_ops") or []
    if stale_active_ops:
        lines.append("Stale active-op tokens (non-gating):")
        for token in stale_active_ops:
            lines.append(
                f"- PID {token.get('pid')} — {token.get('repo')} — elapsed "
                f"{_format_elapsed(token.get('elapsed_s'))} — token {token.get('log')} — "
                f"{token.get('stale_reason') or 'stale'}"
            )
    lines.append("")

    lines.append("## Table A — Infrastructure Health")
    lines.append("")
    lines.append("| Component | State | Detail |")
    lines.append("|---|---|---|")
    for row in report.get("infrastructure", []) or []:
        lines.append(f"| {row.get('component', '')} | {row.get('state', '')} | {row.get('detail', '')} |")
    lines.append("")

    lines.append("## Table B — All Indices Status")
    lines.append("")
    if report.get("listing_status") == "unknown":
        lines.append(f"n/a — index listing failed: {report.get('listing_error') or 'unknown reason'}")
    else:
        lines.append("| Index | Type | Health | Docs | Size | Last updated | Rebuild elapsed | ETA | Est. final size |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for row in report.get("indices", []) or []:
            lines.append(
                "| "
                + " | ".join(
                    str(row.get(key, ""))
                    for key in (
                        "index",
                        "type",
                        "health",
                        "docs",
                        "size_human",
                        "last_updated_human",
                        "rebuild_elapsed",
                        "eta",
                        "est_final_size",
                    )
                )
                + " |"
            )
    lines.append("")

    summary = report.get("summary", {}) or {}
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Bases: {summary.get('bases', 0)}")
    lines.append(f"- Overlays: {summary.get('overlays', 0)}")
    lines.append(f"- Orphan generations: {summary.get('orphans', 0)}")
    lines.append(f"- Total on-disk size: {summary.get('total_size_human', 'n/a')}")
    lines.append("")

    lines.append("## Advisories")
    lines.append("")
    advisories = summary.get("advisories") or []
    if advisories:
        for advisory in advisories:
            lines.append(f"- {advisory}")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Recommended action sequence")
    lines.append("")
    action_sequence = report.get("action_sequence") or []
    if action_sequence:
        for step in action_sequence:
            gate = "operator-gated" if step.get("operator_gate") else "auto-observed"
            lines.append(
                f"{step.get('step')}. **{step.get('action')}** ({step.get('component')}, {gate}) — "
                f"`{step.get('command')}` · verify: {step.get('verify')}"
            )
    else:
        lines.append("- No action needed — steady state (healthy + served + supervised).")
    lines.append("")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# main — CLI entrypoint; wires the REAL probes. Heavy imports stay lazy so
# importing this module (e.g. for tests) never pays their cost or can fail.
# -----------------------------------------------------------------------------


def _build_live_probes() -> "Probes":
    import json as _json
    import os as _os
    import re as _re
    import subprocess as _subprocess
    import time as _time
    import urllib.error
    import urllib.parse
    import urllib.request
    from pathlib import Path as _Path
    from jswarm.platform.macos import list_launchd_jobs as _list_launchd_jobs

    http_timeout = 3.0
    raw_root_code = _Path.home() / "dev" / "colgrep-idx" / "code"
    raw_root_content = _Path.home() / "dev" / "colgrep-idx" / "content"

    def _http_json(base_url: str, method: str, path: str, body: dict | None = None, *, timeout: float = http_timeout):
        url = base_url.rstrip("/") + path
        data = _json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local-only probe target.
            raw = resp.read()
            status = resp.getcode()
        try:
            parsed = _json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return status, parsed

    def backend_up(base_url: str) -> dict:
        try:
            status, body = _http_json(base_url, "GET", "/")
        except Exception as exc:  # noqa: BLE001 - probe seam, fail-open.
            return {"up": False, "detail": f"unreachable: {exc}"}
        if status != 200:
            return {"up": False, "detail": f"HTTP {status}"}
        detail = "up (status 200)"
        if isinstance(body, dict):
            count = body.get("num_indices") or body.get("count")
            if isinstance(count, int):
                detail = f"{count} indices served"
        return {"up": True, "detail": detail}

    def list_indices(base_url: str) -> list:
        # Safety gate (Finding #4): deliberately do NOT swallow failures here — a
        # raised exception is how build_report distinguishes "listing failed"
        # (unknown) from "listing succeeded with 0 indices" (ok, empty).
        status, body = _http_json(base_url, "GET", "/indices")
        if status != 200:
            raise RuntimeError(f"index listing failed: HTTP {status}")
        if isinstance(body, list):
            return [str(item) for item in body]
        if isinstance(body, dict):
            names = body.get("indices")
            if isinstance(names, list):
                return [str(item) for item in names]
        raise RuntimeError("index listing failed: unexpected response shape")

    def index_stats(index_name: str) -> dict:
        encoded = urllib.parse.quote(index_name, safe="")
        try:
            status, body = _http_json(CODE_BACKEND, "GET", f"/indices/{encoded}")
        except Exception:  # noqa: BLE001 - fail-open.
            return {}
        if status != 200 or not isinstance(body, dict):
            return {"exists": status != 404}
        return body

    def probe_queryable(index_name: str) -> dict:
        # Safety gate (Finding #3): a network/timeout exception here is left to
        # propagate so build_report can render "queryability-unknown" instead of
        # a fabricated "proven not queryable" (only an actual HTTP response — even
        # a non-200 one — is a definite, proven result).
        encoded = urllib.parse.quote(index_name, safe="")
        status, body = _http_json(
            CODE_BACKEND,
            "POST",
            f"/indices/{encoded}/search_with_encoding",
            {"queries": ["colgrep status probe"], "params": {"top_k": 1}},
            timeout=6.0,
        )
        hit = status == 200 and isinstance(body, dict) and bool(body.get("results") or body.get("hits"))
        return {"queryability_hit": bool(hit), "reason": "ok" if hit else f"HTTP {status}"}

    def scan_active_rebuilds() -> ActiveRebuildScanPayload:
        # Safety gate (Finding #5, most important): deliberately do NOT swallow a
        # `ps` failure here — build_report treats a raised exception as "unknown"
        # and gates ALL mutating recommendations, never a silent "none" (proven clear).
        completed = _subprocess.run(
            ["ps", "-axo", "pid,etime,command"], capture_output=True, text=True, timeout=5, check=True
        )
        jobs = _scan_ps_lines_for_rebuilds(completed.stdout)
        # COM-241 AC-6 Bug 2: merge in the active-ops registry token as a
        # supplementary, best-effort source (never the fail-closed gate itself) —
        # it is written the instant an overlay build begins, which can be cheaper
        # and more reliable than reconstructing the worktree from a `ps` command line.
        try:
            active_ops_scan = _read_active_ops_jobs(
                _Path.home() / "dev" / "colgrep-idx" / "active-ops", now=_time.time()
            )
        except Exception:  # noqa: BLE001 - supplement only, never escalates to the ps-scan gate.
            active_ops_scan = {"active": [], "stale": []}
        known_pids = {job.get("pid") for job in jobs}
        for job in active_ops_scan.get("active") or []:
            if job.get("pid") not in known_pids:
                jobs.append(job)
                known_pids.add(job.get("pid"))
        return {"jobs": jobs, "stale_active_ops": active_ops_scan.get("stale") or []}

    def _candidate_dir(root: "_Path", index_name: str) -> Optional["_Path"]:
        # Finding #6: resolve via the pure, tested _resolve_raw_dir helper so a
        # sibling family (`index_name-wt-…`, `index_name-full-…`, `…-overlay`)
        # never gets picked as the raw dir whose size/mtime we report.
        if not root.exists():
            return None
        try:
            candidate_names = [p.name for p in root.iterdir() if p.is_dir()]
        except OSError:
            return None
        valid_names = _resolve_raw_dir(index_name, candidate_names)
        if not valid_names:
            return None
        candidates = [root / name for name in valid_names]
        try:
            candidates = sorted(
                candidates,
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                reverse=True,
            )
        except OSError:
            pass
        return candidates[0] if candidates else None

    def index_dir_size(index_name: str) -> Optional[int]:
        path = _candidate_dir(raw_root_code, index_name) or _candidate_dir(raw_root_content, index_name)
        if path is None:
            return None
        total = 0
        for root, _dirs, files in _os.walk(path):
            for name in files:
                try:
                    total += (_Path(root) / name).stat().st_size
                except OSError:
                    pass
        return total

    def index_last_updated(index_name: str) -> Optional[float]:
        path = _candidate_dir(raw_root_code, index_name) or _candidate_dir(raw_root_content, index_name)
        if path is None:
            return None
        state_file = path / "state.json"
        try:
            return state_file.stat().st_mtime
        except OSError:
            try:
                return path.stat().st_mtime
            except OSError:
                return None

    def launchd_state() -> dict:
        try:
            out = _list_launchd_jobs()
        except Exception:  # noqa: BLE001 - fail-open to unknown (None), never a healthy default.
            return {key: {"loaded": None, "pid": None} for key in _LAUNCHD_COMPONENTS}
        # Finding #8: use the pure, tested _parse_launchd_loaded helper so an
        # EXACT label match is required (e.g. `com.colgrep.watcher.backup` must
        # never mark `watcher` loaded).
        return {key: _parse_launchd_loaded(out, key) for key in _LAUNCHD_COMPONENTS}

    def container_stats() -> Optional[dict]:
        # Finding #10: best-effort RSS-vs-cap via `docker stats --no-stream`; fail-open
        # to None (no probe) rather than fabricating a healthy container state.
        try:
            completed = _subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.Container}}\t{{.MemUsage}}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:  # noqa: BLE001 - fail-open: no container/RSS probe available.
            return None
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        for line in completed.stdout.splitlines():
            if "next-plaid" not in line.lower():
                continue
            match = _re.search(r"([\d.]+)\s*(GiB|MiB|KiB|B)\s*/\s*([\d.]+)\s*(GiB|MiB|KiB|B)", line)
            if not match:
                continue
            units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
            rss = float(match.group(1)) * units.get(match.group(2), 1)
            cap = float(match.group(3)) * units.get(match.group(4), 1)
            return {"rss_bytes": rss, "cap_bytes": cap}
        return None

    def memguard_available() -> bool:
        script = _Path(__file__).resolve().parent / "colgrep_mem_guard.sh"
        return script.is_file() and _os.access(script, _os.X_OK)

    def eta_estimate(index_name: str) -> Optional[dict]:
        # Best-effort native-rebuild ETA is out of scope for a safe live default
        # (needs a per-repo target-doc count this seam does not have); fail-open.
        return None

    def fleet_plan() -> Optional[dict]:
        # COM-241 Phase B: strictly read-only — the dry-run `plan` subcommand
        # (never `run`/`once`, which spawn/supervise live watcher children).
        # Fail-open on ANY error (missing script, non-zero exit, unparsable
        # stdout) so a broken fleet-plan diagnostic degrades the status report
        # to "no worktree actions" instead of crashing it.
        script = _Path(__file__).resolve().parent / "colgrep_overlay_fleet_supervisor.py"
        venv_python = _Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"
        try:
            completed = _subprocess.run(
                [str(venv_python), str(script), "plan"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:  # noqa: BLE001 - fail-open: no fleet-plan probe available.
            return None
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        try:
            parsed = _json.loads(completed.stdout)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def registry_integrity_findings() -> list[dict]:
        # COM-289 BR-15/B5: reuse the BR-05 detection function verbatim (no
        # reimplementation) so `report` and `health` can never drift apart on what
        # counts as a registry-integrity violation. `_registry_integrity_findings`
        # returns [] only for a MISSING registry (a legitimate empty state); an
        # unreadable/malformed registry now raises (COM-289 B5 -- it no longer
        # swallows that into a false-clean []), so let it propagate here too, so
        # build_report's probe wrapper (below) marks this row "unknown", never
        # "clean".
        try:
            from jswarm.colgrep_worktree import DEFAULT_REGISTRY_PATH, _registry_integrity_findings
        except ImportError:  # running as a top-level script with jswarm/ on sys.path
            from colgrep_worktree import DEFAULT_REGISTRY_PATH, _registry_integrity_findings  # type: ignore[no-redef]
        return _registry_integrity_findings(DEFAULT_REGISTRY_PATH)

    def status_plane_disagreements() -> list[dict]:
        # COM-289 BR-08: reuse the detection function verbatim (no reimplementation)
        # so `report` and `health` can never drift apart on what counts as a
        # cross-plane disagreement. `_status_plane_disagreements` already fails open
        # internally (unreadable/missing registry -> []), so a raise here means the
        # import itself is broken -> let it propagate so build_report's probe
        # wrapper marks this row "unknown", never "clean".
        try:
            from jswarm.colgrep_worktree import DEFAULT_REGISTRY_PATH, _status_plane_disagreements
        except ImportError:  # running as a top-level script with jswarm/ on sys.path
            from colgrep_worktree import DEFAULT_REGISTRY_PATH, _status_plane_disagreements  # type: ignore[no-redef]
        return _status_plane_disagreements(DEFAULT_REGISTRY_PATH)

    return Probes(
        backend_up=backend_up,
        list_indices=list_indices,
        index_stats=index_stats,
        probe_queryable=probe_queryable,
        scan_active_rebuilds=scan_active_rebuilds,
        index_dir_size=index_dir_size,
        index_last_updated=index_last_updated,
        launchd_state=launchd_state,
        memguard_available=memguard_available,
        eta_estimate=eta_estimate,
        container_stats=container_stats,
        fleet_plan=fleet_plan,
        registry_integrity_findings=registry_integrity_findings,
        status_plane_disagreements=status_plane_disagreements,
    )


def main(argv=None) -> int:
    """CLI entrypoint.

    Supports --json | --format md (default md); wires the REAL probes (HTTP
    :3280/:3281, ps scan, du, this host's launchd jobs, colgrep_index_lag_eta).
    """

    import argparse
    import json as _json
    import time as _time

    parser = argparse.ArgumentParser(description="Read-only ColGREP health/status report (status-before-recovery).")
    parser.add_argument("--json", action="store_true", help="emit the colgrep.status-report.v1 JSON payload")
    parser.add_argument("--format", choices=("md", "json"), default="md", help="output format (default: md)")
    args = parser.parse_args(argv)

    probes = _build_live_probes()
    report = build_report(probes, now=_time.time())

    if args.json or args.format == "json":
        print(_json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
