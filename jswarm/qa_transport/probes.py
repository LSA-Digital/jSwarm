"""Slim pre-dispatch liveness probes for browser-QA transports (COM-246 AC-1).

Every probe runs under a hard timeout and returns a structured result instead
of raising: agents cannot self-timebox a hung tool call, so the probe layer is
where hangs are converted into bounded, observable failures.

What each probe covers:

- ``probe_harness_cli`` — runs the project e2e harness preflight command (the
  ``agent-e2e.sh preflight`` class, manifest-authoritative) as a real
  subprocess. Healthy == exit 0 within the timeout.
- ``probe_command`` — generic bounded runner for any declared liveness command
  (e.g. a CDP endpoint check) so browser backends that fail independently of
  any proxy are caught before dispatch.

In-session MCP exposure is deliberately NOT probed here: only the orchestrator
can observe its live tool list. It supplies that fact to the ladder directly
(config presence != session exposure — QA21 2026-07-10).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ProbeResult:
    name: str
    healthy: bool
    detail: str
    duration_seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "healthy": self.healthy,
            "detail": self.detail,
            "duration_seconds": round(self.duration_seconds, 3),
        }


def probe_command(
    name: str,
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Run ``command`` bounded by ``timeout_seconds``; never raises, never hangs."""
    start = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - operator-declared liveness command.
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(
            name=name,
            healthy=False,
            detail=f"timeout after {timeout_seconds}s (treated as unhealthy; the ladder falls through)",
            duration_seconds=time.monotonic() - start,
        )
    except (OSError, ValueError) as exc:
        return ProbeResult(
            name=name,
            healthy=False,
            detail=f"failed to launch: {exc}",
            duration_seconds=time.monotonic() - start,
        )
    tail = (completed.stdout or completed.stderr or "").strip().splitlines()
    detail = f"exit {completed.returncode}"
    if tail:
        detail += f"; last line: {tail[-1][:200]}"
    return ProbeResult(
        name=name,
        healthy=completed.returncode == 0,
        detail=detail,
        duration_seconds=time.monotonic() - start,
    )


def _preflight_command_from_manifest(manifest_path: Path) -> list[str] | None:
    """Read the e2e manifest's declared preflight command, if any.

    Supported shapes, first match wins — ``uatState.preflightCommand`` is the
    REAL field shipped in project manifests (verified against
    hai-sim-engine/.jswarm/e2e-manifest.json, QA21 2026-07-10); the generic
    ``commands.preflight`` / top-level ``preflight`` shapes are fallbacks.
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = None
    uat_state = manifest.get("uatState")
    if isinstance(uat_state, dict):
        raw = uat_state.get("preflightCommand")
    if raw is None:
        commands = manifest.get("commands")
        if isinstance(commands, dict):
            raw = commands.get("preflight")
    if raw is None:
        raw = manifest.get("preflight")
    if isinstance(raw, str) and raw.strip():
        return shlex.split(raw)
    if isinstance(raw, list) and raw and all(isinstance(item, str) for item in raw):
        return list(raw)
    return None


def probe_harness_cli(
    project_root: Path,
    *,
    manifest_path: Path | None = None,
    preflight_command: list[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Preflight the project e2e harness CLI (branch 2 of the ladder).

    Resolution order: explicit ``preflight_command`` > manifest-declared
    command (`.jswarm/e2e-manifest.json`) > conventional
    ``jswarm/agent-e2e.sh preflight`` if that script exists.
    """
    command = preflight_command
    if command is None:
        manifest = manifest_path or (project_root / ".jswarm" / "e2e-manifest.json")
        if manifest.exists():
            command = _preflight_command_from_manifest(manifest)
    if command is None:
        conventional = project_root / "scripts" / "agent-e2e.sh"
        if conventional.exists():
            command = [str(conventional), "preflight"]
    if command is None:
        return ProbeResult(
            name="harness-cli",
            healthy=False,
            detail=(
                "no harness CLI found: no explicit command, no manifest preflight in "
                ".jswarm/e2e-manifest.json, and no jswarm/agent-e2e.sh"
            ),
            duration_seconds=0.0,
        )
    return probe_command("harness-cli", command, cwd=project_root, timeout_seconds=timeout_seconds)
