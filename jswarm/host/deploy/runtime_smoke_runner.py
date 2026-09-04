#!/usr/bin/env python3
"""COM-146 AC-13 runtime-honor smoke runner.

Filesystem topology is not enough: a surface is live-ready only when the Claude
runtime demonstrably honors the deployed form. This runner deploys unique-nonce
PROBE artifacts (never product artifacts), asks a fresh ``claude --print``
subprocess to exercise them, and decides per-surface honor verdicts.

Safety gates (all fail-closed):

* The probe PROMPT never contains the private sentinel — the sentinel lives only
  in the probe master file, so a model echo of the sentinel proves the runtime
  actually read the deployed artifact (no prompt-parroting false positive).
* Invoking a real ``claude`` subprocess requires the explicit operator opt-in
  ``COM146_SMOKE_ALLOW_LIVE_CLAUDE=1``. Without it ``invoke_claude_print``
  refuses — pytest runs stay hermetic (tests monkeypatch the adapter).
* Probe writes against the real ``~/.claude`` require the explicit
  ``--live-home`` acknowledgement (parity with deploy.py); a probe target must
  be ABSENT before deploy (unique-nonce names; collision = refuse), and the
  probe manifest is written BEFORE the first live write so cleanup is always
  possible. Settings is the only in-place mutation: exact prior bytes are
  backed up first and restored byte-identically afterward.
* A failed smoke rolls back the surface from the supplied backup manifest
  (``deploy.py rollback``) and blocks completion for that surface only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    # Insert the repository root (parents[3]: jswarm/host/deploy/ -> jswarm/host/ ->
    # jswarm/ -> repo root), not jswarm/ itself (parents[2]). jswarm/ on sys.path would
    # shadow the stdlib for anything under jswarm/ sharing a name with it (e.g.
    # jswarm/platform/ vs the stdlib platform module), and the jswarm.host.deploy.*
    # imports below need the repo root on the path anyway.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jswarm.host.deploy import deploy as _deploy_engine

SUPPORTED_SURFACES = ("command", "hook", "skill", "rule", "command-injection", "settings")

ALLOW_LIVE_CLAUDE_ENV = "COM146_SMOKE_ALLOW_LIVE_CLAUDE"
MAX_CLAUDE_TIMEOUT_SECONDS = 120
PROBE_MANIFEST_KIND = "controlled-config-smoke-probe-manifest"
PROBE_MANIFEST_SCHEMA_VERSION = 1
# Diagnostics from the most recent REAL claude invocation (test fakes never touch this);
# folded into the evidence NDJSON so a crashed binary is distinguishable from "not honored".
LAST_CLAUDE_DIAGNOSTICS: dict[str, object] = {}

# Per-surface probe target layout (unique-nonce names under the live .claude root).
_SURFACE_TARGETS = {
    "command": ("commands", "com146-probe-{nonce}.md"),
    "hook": ("hooks", "com146-probe-hook-{nonce}.py"),
    "skill": ("skills", "com146-symlink-probe-{nonce}"),
    "rule": ("rules", "com146-probe-{nonce}.mdc"),
    "command-injection": ("commands", "com146-probe-inject-{nonce}.md"),
    "settings": ("", "settings.json"),
}


@dataclass(frozen=True)
class ProbePlan:
    """Everything one surface smoke needs; the sentinel is NEVER in the prompt.

    ``live_home_ack`` carries the operator's explicit live acknowledgement from the CLI (or a
    deliberate caller) down to the WRITE chokepoint — it defaults False so every library path
    is fail-closed (critic P11 R1 BLOCKER-2)."""

    surface: str
    nonce: str
    prompt: str
    private_sentinel: str
    live_probe_target: Path
    master_probe_source: Path
    home_root: Path = Path(".")
    manifest_path: Path | None = None
    log_root: Path | None = None
    timeout_seconds: int = MAX_CLAUDE_TIMEOUT_SECONDS
    live_home_ack: bool = False
    injection_project_root: Path | None = None


@dataclass(frozen=True)
class SmokeResult:
    surface: str
    passed: bool
    blocks_completion: bool
    detail: str
    stdout_excerpt: str = ""


@dataclass(frozen=True)
class SkillPolicyDecision:
    """AC-13 skill policy: symlink only if the symlink probe passes; else managed-copy
    if that passes; else the skill surface is blocked."""

    install_type: str | None
    blocks_surface: bool
    attempts: tuple[str, ...] = field(default=())


def _new_sentinel(surface: str) -> str:
    token = secrets.token_hex(16)
    if surface == "skill":
        # Spec-pinned shape for the load-bearing skill probe.
        return f"COM146_SKILL_SYMLINK_OK_{token}"
    return f"COM146_{surface.upper().replace('-', '_')}_OK_{token}"


def _surface_prompt(surface: str, nonce: str) -> str:
    """Fresh-session probe prompt. MUST mention the nonce (targets THIS probe) and MUST
    NOT contain the private sentinel (a sentinel echo must prove artifact loading)."""
    if surface == "command":
        return f"/com146-probe-{nonce}"
    if surface == "command-injection":
        return f"/com146-probe-inject-{nonce}"
    if surface == "skill":
        return (
            f"A skill named com146-symlink-probe-{nonce} may be available. If it is "
            "available, use it to answer the COM-146 skill symlink probe. If it is not "
            "available, answer exactly NO_SKILL."
        )
    if surface == "rule":
        return (
            f"COM146 rule probe {nonce}: if a COM-146 rule sentinel token is in effect "
            "for this session, output that token verbatim. Otherwise output exactly NO_RULE."
        )
    if surface == "hook":
        return (
            f"COM146 hook probe {nonce}: if a COM-146 sentinel token was injected into "
            "your context by a hook, output that token verbatim. Otherwise output exactly NO_HOOK."
        )
    if surface == "settings":
        return (
            f"COM146 settings probe {nonce}: if a COM-146 sentinel token was injected into "
            "your context by a hook, output that token verbatim. Otherwise output exactly NO_SETTINGS."
        )
    raise ValueError(f"unsupported surface {surface!r}; expected one of {SUPPORTED_SURFACES}")


def _surface_paths(surface: str, nonce: str, *, master_root: Path, home_root: Path) -> tuple[Path, Path]:
    family, name_template = _SURFACE_TARGETS[surface]
    name = name_template.format(nonce=nonce)
    relpath = f"{family}/{name}" if family else name
    return master_root / relpath, home_root / relpath


def build_probe_plan(
    *,
    surface: str,
    master_root: str | Path,
    common_root: str | Path,  # kept for interface parity with the deploy engine roots
    home_root: str | Path,
    nonce: str,
    manifest_path: str | Path | None = None,
    log_root: str | Path | None = None,
    live_home_ack: bool = False,
) -> ProbePlan:
    if surface not in SUPPORTED_SURFACES:
        raise ValueError(f"unsupported surface {surface!r}; expected one of {SUPPORTED_SURFACES}")
    del common_root  # probes deploy master -> live home only; common has no probe leg
    # All plan paths are ABSOLUTIZED here (T11.2 live finding): a relative master root would
    # produce a probe symlink whose target dangles when resolved from the live family dir —
    # the runtime then reports "Unknown command" and the probe false-negatives.
    master_root_path = Path(os.path.abspath(master_root))
    home_root_path = Path(os.path.abspath(home_root))
    master_probe_source, live_probe_target = _surface_paths(
        surface, nonce, master_root=master_root_path, home_root=home_root_path
    )
    sentinel = _new_sentinel(surface)
    prompt = _surface_prompt(surface, nonce)
    if sentinel in prompt:  # real raise, not assert: survives python -O (critic MINOR-3b)
        raise RuntimeError("private sentinel must never appear in the probe prompt")
    injection_root = (
        master_root_path.parent / f"com146-inject-root-{nonce}" if surface == "command-injection" else None
    )
    # MAJOR-R2-1: support roots may never live inside the real ~/.claude — refused at plan
    # build, before any path can be used, regardless of any acknowledgement.
    for support_path, label in (
        (master_root_path, "probe master root"),
        (Path(log_root) if log_root is not None else None, "evidence log root"),
        (injection_root, "injection staging root"),
    ):
        if support_path is None:
            continue
        containment_error = _support_root_containment_error(support_path, label)
        if containment_error is not None:
            raise RuntimeError(f"probe plan refused: {containment_error} (live tree)")
    return ProbePlan(
        surface=surface,
        nonce=nonce,
        prompt=prompt,
        private_sentinel=sentinel,
        live_probe_target=live_probe_target,
        master_probe_source=master_probe_source,
        home_root=home_root_path,
        manifest_path=Path(os.path.abspath(manifest_path)) if manifest_path is not None else None,
        log_root=Path(os.path.abspath(log_root)) if log_root is not None else None,
        live_home_ack=live_home_ack,
        injection_project_root=injection_root,
    )


def _support_root_containment_error(path: Path, label: str) -> str | None:
    """Return an error when a probe SUPPORT root (probe master root, evidence/log root, the
    derived injection staging root) lies at or under the real live ``~/.claude``. Support
    artifacts are never legitimate live deployments, so containment is refused outright —
    no acknowledgement can allow it (critic P11 R2 MAJOR-R2-1). Checked lexically AND on
    resolved paths so a symlink alias cannot hide the containment."""
    live = Path.home() / ".claude"
    for candidate, anchor in (
        (Path(os.path.normpath(path)), Path(os.path.normpath(live))),
        (Path(path).resolve(strict=False), live.resolve(strict=False)),
    ):
        try:
            candidate.relative_to(anchor)
        except ValueError:
            continue
        return (
            f"{label} {path} lies inside the real live ~/.claude tree; probe support artifacts "
            f"must never be written into the live config directory"
        )
    return None


def _classify_live_root(root: str | Path) -> bool:
    """True when ``root`` IS the real live ``~/.claude`` — by lexical normpath, resolved
    identity, or (when both exist) inode/device identity. Engine parity: deploy.py's rollback
    uses the lexical+resolved dual (P9 MAJOR-R2-1); the samefile arm additionally covers
    case-aliasing filesystems like APFS (critic P11 R1 probe F3)."""
    live = Path.home() / ".claude"
    candidate = Path(root)
    if os.path.normpath(candidate) == os.path.normpath(live):
        return True
    try:
        if candidate.resolve(strict=False) == live.resolve(strict=False):
            return True
    except OSError:
        pass
    try:
        if candidate.exists() and live.exists() and os.path.samefile(candidate, live):
            return True
    except OSError:
        pass
    return False


def invoke_claude_print(prompt: str, *, timeout_seconds: int) -> str:
    """Real adapter: fresh ``claude --print`` subprocess (fresh session per spec).

    Fail-closed: requires the explicit operator opt-in env so test suites and
    unattended runs can never silently launch a live model session.
    """
    if timeout_seconds > MAX_CLAUDE_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be <= {MAX_CLAUDE_TIMEOUT_SECONDS}, got {timeout_seconds}")
    if os.environ.get(ALLOW_LIVE_CLAUDE_ENV) != "1":
        raise RuntimeError(
            f"live claude --print probe refused: set {ALLOW_LIVE_CLAUDE_ENV}=1 for an "
            "operator-approved Phase 11 live probe run"
        )
    proc = subprocess.run(
        ["claude", "--print", prompt],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    # MINOR-3a: keep subprocess diagnostics for the evidence record — a crashed claude is
    # fail-closed for the verdict either way, but T11.2 diagnosis needs rc/stderr.
    LAST_CLAUDE_DIAGNOSTICS["returncode"] = proc.returncode
    LAST_CLAUDE_DIAGNOSTICS["stderr_excerpt"] = (proc.stderr or "")[:400]
    return proc.stdout


# Manifest artifacts must stay within ONE surface family for a surface-named rollback
# (critic P11 R1 MAJOR-2: the engine restores EVERY record in a manifest, so a spanning
# manifest would roll back unrelated healthy surfaces).
_SURFACE_FAMILY_PREFIX = {
    "command": "commands/",
    "command-injection": "commands/",
    "hook": "hooks/",
    "skill": "skills/",
    "rule": "rules/",
    "settings": "settings.json",
}


def _manifest_surface_scope_error(manifest_path: Path, surface: str) -> str | None:
    """Return an error when the manifest's artifacts are not all within the surface family."""
    prefix = _SURFACE_FAMILY_PREFIX[surface]
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = payload.get("artifacts", [])
    except (OSError, json.JSONDecodeError) as exc:
        return f"manifest unreadable for surface scoping at {manifest_path}: {exc}"
    if not isinstance(records, list):
        return f"manifest artifacts is not a list at {manifest_path}"
    for record in records:
        relpath = str(record.get("target_relpath", "")) if isinstance(record, dict) else ""
        in_family = relpath == prefix or relpath.startswith(prefix)
        if not in_family:
            return (
                f"manifest {manifest_path} spans beyond the {surface!r} surface family "
                f"({prefix!r}): found artifact {relpath!r}; per-surface manifests are required "
                f"so a surface rollback never restores unrelated surfaces"
            )
    return None


def rollback_surface(surface: str, manifest_path: str | Path, *, live_home_ack: bool = False) -> int:
    """Roll the surface back from its backup manifest via the AC-12 engine rollback.

    The operator's live acknowledgement is THREADED, never self-granted (critic MAJOR-2): the
    engine's own live-manifest gate stays meaningful. A manifest whose artifacts span more
    than the named surface family is refused before any restore."""
    scope_error = _manifest_surface_scope_error(Path(manifest_path), surface)
    if scope_error is not None:
        print(f"smoke rollback refused: {scope_error}")
        return 1
    print(f"smoke: rolling back surface {surface!r} from manifest {manifest_path}")
    argv = ["rollback", "--manifest", str(manifest_path)]
    if live_home_ack:
        argv.append("--live-home")
    return _deploy_engine.main(argv)


def _failed_result(surface: str, plan: ProbePlan, *, detail: str, excerpt: str = "") -> SmokeResult:
    if plan.manifest_path is not None:
        rollback_rc = rollback_surface(surface, plan.manifest_path, live_home_ack=plan.live_home_ack)
        if rollback_rc == 0:
            detail += "; surface rolled back and blocked"
        else:
            # MINOR-R2-2: never record "rolled back" for a rollback that was refused/failed.
            detail += (
                f"; rollback REFUSED/FAILED (rc={rollback_rc}) — surface blocked, "
                f"manual intervention required"
            )
    else:
        detail += "; surface blocked"
    return SmokeResult(
        surface=surface,
        passed=False,
        blocks_completion=True,
        detail=detail,
        stdout_excerpt=excerpt,
    )


def run_surface_smoke(surface: str, plan: ProbePlan) -> SmokeResult:
    """Run one surface's runtime-honor smoke against an already-deployed probe.

    Pass condition: the fresh-session stdout (or, for command-injection, the RENDERER output —
    spec §3:372) contains the private sentinel that exists only in the probe master artifact.
    ANY failure — sentinel absent, subprocess timeout, missing/crashed binary — takes the same
    fail-closed path (critic MAJOR-1): roll back from the plan's backup manifest when one was
    supplied, and block completion for that surface.
    """
    if surface != plan.surface:
        raise ValueError(f"surface mismatch: asked {surface!r} but plan is for {plan.surface!r}")
    try:
        if surface == "command-injection":
            observed = render_injection_probe(plan)
            proof = "renderer output"
        else:
            observed = invoke_claude_print(plan.prompt, timeout_seconds=plan.timeout_seconds)
            proof = "fresh-session stdout"
    except subprocess.TimeoutExpired:
        return _failed_result(
            surface, plan, detail=f"smoke probe timed out after {plan.timeout_seconds}s (TimeoutExpired)"
        )
    except OSError as exc:
        return _failed_result(surface, plan, detail=f"smoke probe subprocess failed: {type(exc).__name__}: {exc}")
    passed = plan.private_sentinel in observed
    excerpt = observed[:400]
    if passed:
        return SmokeResult(
            surface=surface,
            passed=True,
            blocks_completion=False,
            detail=f"runtime honored the deployed probe artifact (sentinel observed in {proof})",
            stdout_excerpt=excerpt,
        )
    return _failed_result(
        surface,
        plan,
        detail=f"runtime did NOT honor the deployed probe artifact (sentinel absent from {proof})",
        excerpt=excerpt,
    )


def probe_skill_install(
    install_type: str,
    *,
    nonce: str,
    master_root: Path,
    home_root: Path,
    live_home_ack: bool = False,
    log_root: Path | None = None,
) -> bool:
    """Deploy the probe skill via ``install_type`` (symlink | managed-copy), smoke it in a
    fresh session, clean up, and return the honor verdict.

    The live acknowledgement is enforced at the WRITE chokepoint (``_deploy_probe``) and
    defaults False — this library path is fail-closed without an explicit operator ack
    (critic P11 R1 BLOCKER-2)."""
    if install_type not in {"symlink", "managed-copy"}:
        raise ValueError(f"unsupported skill install_type {install_type!r}")
    plan = build_probe_plan(
        surface="skill",
        master_root=master_root,
        common_root=master_root,
        home_root=home_root,
        nonce=f"{nonce}-{install_type}",
        live_home_ack=live_home_ack,
        log_root=log_root,
    )
    _write_skill_probe_master(plan)
    deployed = _deploy_probe(plan, install_type=install_type)
    try:
        result = run_surface_smoke("skill", plan)
        # F-EG-5: ladder rungs leave the same durable NDJSON trail as CLI smokes.
        _append_evidence(plan, result, run_id=f"skill-ladder-{nonce}-{install_type}")
        return result.passed
    finally:
        if deployed:
            _cleanup_probe(plan)


def decide_skill_install_policy(
    *,
    nonce: str,
    master_root: Path,
    home_root: Path,
    live_home_ack: bool = False,
    log_root: Path | None = None,
) -> SkillPolicyDecision:
    """AC-13 skill policy ladder: symlink first; managed-copy fallback; else block.
    Forwards the operator acknowledgement fail-closed (never invents one)."""
    attempts: list[str] = []
    for install_type in ("symlink", "managed-copy"):
        attempts.append(install_type)
        if probe_skill_install(
            install_type,
            nonce=nonce,
            master_root=master_root,
            home_root=home_root,
            live_home_ack=live_home_ack,
            log_root=log_root,
        ):
            return SkillPolicyDecision(
                install_type=install_type,
                blocks_surface=False,
                attempts=tuple(attempts),
            )
    return SkillPolicyDecision(install_type=None, blocks_surface=True, attempts=tuple(attempts))


# --- probe deployment / cleanup (runner-owned, manifest-backed) ----------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _probe_manifest_path(plan: ProbePlan) -> Path:
    base = plan.log_root if plan.log_root is not None else plan.master_probe_source.parent
    return base / f"probe-{plan.surface}-{plan.nonce}.manifest.json"


def _write_probe_manifest(
    plan: ProbePlan, *, before_state: dict[str, Any], created_dirs: list[str] | None = None
) -> Path:
    """Record the probe's before-state BEFORE any live write so cleanup is always possible.
    ``created_dirs`` records family dirs the deploy will create, so cleanup can remove them
    (critic MINOR-1a). The manifest itself is retained after cleanup as run evidence."""
    manifest = {
        "kind": PROBE_MANIFEST_KIND,
        "schema_version": PROBE_MANIFEST_SCHEMA_VERSION,
        "surface": plan.surface,
        "nonce": plan.nonce,
        "written_utc": _utc_now(),
        "live_probe_target": str(plan.live_probe_target),
        "master_probe_source": str(plan.master_probe_source),
        "before": before_state,
        "created_dirs": created_dirs or [],
    }
    path = _probe_manifest_path(plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _require_absent(target: Path) -> None:
    """Probe targets use unique-nonce names; ANY pre-existing entry is a refuse (no-follow)."""
    try:
        os.lstat(target)
    except FileNotFoundError:
        return
    raise RuntimeError(f"probe target {target} already exists; refusing to touch a non-probe path")


def _write_skill_probe_master(plan: ProbePlan) -> None:
    skill_dir = plan.master_probe_source
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# COM-146 symlink probe skill\n\n"
        "If the user asks for the COM-146 skill symlink probe, answer exactly:\n"
        f"{plan.private_sentinel}\n",
        encoding="utf-8",
    )


def _write_probe_master(plan: ProbePlan) -> None:
    """Write the per-surface probe master containing the private sentinel."""
    if plan.surface == "skill":
        _write_skill_probe_master(plan)
        return
    plan.master_probe_source.parent.mkdir(parents=True, exist_ok=True)
    if plan.surface == "command-injection":
        # The sentinel deliberately lives in the SNIPPET, not the command body — only a
        # successful renderer splice through the anchor can surface it (spec §3:372).
        body = (
            f"# COM-146 command-injection probe {plan.nonce}\n\n"
            f"<!-- inject:com146-probe-{plan.nonce} -->\n"
        )
    elif plan.surface == "command":
        body = (
            f"# COM-146 {plan.surface} probe {plan.nonce}\n\n"
            f"When this command runs, output exactly this token and nothing else:\n"
            f"{plan.private_sentinel}\n"
        )
    elif plan.surface == "rule":
        body = (
            "---\nalwaysApply: true\n---\n\n"
            f"# COM-146 rule probe {plan.nonce}\n\n"
            "When asked for the COM-146 rule probe token, output exactly:\n"
            f"{plan.private_sentinel}\n"
        )
    elif plan.surface == "hook":
        body = (
            "#!/usr/bin/env python3\n"
            f'"""COM-146 hook probe {plan.nonce} (UserPromptSubmit)."""\n'
            "import json, sys\n"
            f"SENTINEL = {plan.private_sentinel!r}\n"
            "payload = {\"hookSpecificOutput\": {\"hookEventName\": \"UserPromptSubmit\","
            " \"additionalContext\": \"COM-146 hook sentinel: \" + SENTINEL}}\n"
            "print(json.dumps(payload))\n"
            "sys.exit(0)\n"
        )
    else:
        raise ValueError(f"no probe master template for surface {plan.surface!r}")
    plan.master_probe_source.write_text(body, encoding="utf-8")
    if plan.surface == "hook":
        plan.master_probe_source.chmod(0o755)


def _parent_chain_clean_or_raise(plan: ProbePlan, *, action: str) -> None:
    """Refuse when any component between the supplied root and the probe target's parent is a
    symlink, or the root itself is one — otherwise a lexically-in-root write/delete physically
    lands OUTSIDE the supplied root (critic BLOCKER-3). Reuses the engine's parent-chain
    helper; full dirfd-anchored I/O is intentionally NOT used here: probes defend against
    static config shapes (symlinked family dirs), not racing attackers — accepted limitation
    of the probe-only write path."""
    if not _deploy_engine._live_context_user_parent_clean(plan.home_root, plan.live_probe_target):
        raise RuntimeError(
            f"probe {action} refused: a symlinked or escaping parent component lies between "
            f"{plan.home_root} and {plan.live_probe_target.parent}"
        )


def _missing_ancestors(home_root: Path, parent: Path) -> list[Path]:
    """Family dirs that do not exist yet between home_root (exclusive) and parent (inclusive),
    outermost first — recorded in the probe manifest before they are created."""
    missing: list[Path] = []
    cur = Path(parent)
    home_norm = Path(os.path.normpath(home_root))
    while os.path.normpath(cur) != os.path.normpath(home_norm):
        if not cur.exists():
            missing.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    return list(reversed(missing))


def _deploy_probe(plan: ProbePlan, *, install_type: str = "symlink") -> bool:
    """Deploy the probe master to its absent unique-nonce live target. Returns True when a
    live entry was created (and therefore must be cleaned up).

    This is the single WRITE chokepoint for every probe path (CLI and library): the live
    acknowledgement, target-absence, and parent-chain checks all happen here, fail-closed
    (critic P11 R1 BLOCKER-1/2/3)."""
    if plan.surface == "settings":
        # MINOR-4: settings is an in-place merge of the live settings.json; it must NEVER go
        # through the absent-target probe path. The operator-gated coupled sequence owns it.
        raise RuntimeError(
            "probe deploy refused: the settings surface mutates settings.json in place and is "
            "never deployed through the absent-target probe path"
        )
    if _classify_live_root(plan.home_root) and not plan.live_home_ack:
        raise RuntimeError(
            f"probe write refused: home root {plan.home_root} classifies as the real live "
            f"~/.claude and no --live-home operator acknowledgement was given"
        )
    if plan.home_root.is_symlink() or not plan.home_root.is_dir():
        raise RuntimeError(
            f"probe write refused: home root {plan.home_root} is not an existing real directory"
        )
    target = plan.live_probe_target
    _require_absent(target)
    _parent_chain_clean_or_raise(plan, action="write")
    created_dirs = _missing_ancestors(plan.home_root, target.parent)
    _write_probe_manifest(
        plan, before_state={"state": "absent"}, created_dirs=[str(d) for d in created_dirs]
    )
    for directory in created_dirs:
        directory.mkdir()
    if install_type == "symlink":
        target.symlink_to(plan.master_probe_source)
    elif install_type == "managed-copy":
        if plan.master_probe_source.is_dir():
            shutil.copytree(plan.master_probe_source, target, symlinks=False)
        else:
            shutil.copy2(plan.master_probe_source, target)
    else:
        raise ValueError(f"unsupported probe install_type {install_type!r}")
    return True


def _cleanup_probe(plan: ProbePlan) -> None:
    """Remove the deployed probe entry, verifying it is still OUR probe first (no-follow).
    Applies the same parent-chain refusal as deploy (a swapped-in symlinked family dir must
    not redirect the delete), and removes only family dirs this run created (and only when
    empty). The probe manifest and master are retained as run evidence."""
    target = plan.live_probe_target
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return
    _parent_chain_clean_or_raise(plan, action="cleanup")
    if stat.S_ISLNK(st.st_mode):
        if Path(os.readlink(target)) != plan.master_probe_source:
            raise RuntimeError(f"probe cleanup refused: {target} no longer points at the probe master")
        target.unlink()
    elif stat.S_ISDIR(st.st_mode):
        skill_md = target / "SKILL.md"
        try:
            deployed_body = skill_md.read_text(encoding="utf-8") if skill_md.is_file() else ""
        except OSError:
            deployed_body = ""
        # MINOR-2: presence of a SKILL.md is not proof of OUR probe — require OUR sentinel so a
        # swapped-in real user skill directory is never rmtree'd.
        if plan.private_sentinel not in deployed_body:
            raise RuntimeError(
                f"probe cleanup refused: {target} is not this run's probe skill directory "
                f"(SKILL.md missing or lacks the run sentinel)"
            )
        shutil.rmtree(target)
    elif stat.S_ISREG(st.st_mode):
        expected = plan.master_probe_source.read_bytes()
        if _sha256_bytes(target.read_bytes()) != _sha256_bytes(expected):
            raise RuntimeError(f"probe cleanup refused: {target} bytes no longer match the probe master")
        target.unlink()
    else:
        raise RuntimeError(f"probe cleanup refused: {target} has unexpected file type")
    _require_absent(target)
    # Remove family dirs this run created (innermost first), only when empty.
    try:
        manifest = json.loads(_probe_manifest_path(plan).read_text(encoding="utf-8"))
        created = [Path(p) for p in manifest.get("created_dirs", [])]
    except (OSError, json.JSONDecodeError):
        created = []
    for directory in reversed(created):
        try:
            directory.rmdir()
        except OSError:
            pass  # non-empty or already gone — leave it; never force-remove


# --- command-injection renderer proof (spec §3:372 — no claude subprocess needed) -----------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Allowlisted managed-command name the probe renders under (see _stage_injection_probe_root).
INJECTION_PROBE_COMMAND_NAME = "test.md"


def _stage_injection_probe_root(plan: ProbePlan) -> Path:
    """Stage a temp project root for the renderer proof: a probe SNIPPET (symlinked, carrying
    the private sentinel) + a managed-commands manifest binding it to the probe command's
    anchor. The probe command master itself contains the anchor marker and NO sentinel — only
    a successful renderer splice can surface the sentinel."""
    root = plan.injection_project_root
    if root is None:
        raise RuntimeError("injection probe root is only defined for the command-injection surface")
    # EMPIRICAL CONSTRAINT (captured during P11): the renderer's no-escape guard refuses any
    # snippet whose RESOLVED path leaves the project root — so a controlled-config snippet
    # symlinked to an out-of-root master can never render. The probe therefore proves the
    # honored form: a snippet SYMLINK resolving WITHIN the project root. Migration consequence
    # (P15): command-injection snippets must be managed-copy (or in-root symlinks), never
    # symlinks to the out-of-root controlled master.
    snippet_master = root / "snippet-masters" / f"com146-probe-snippet-{plan.nonce}.md"
    snippet_master.parent.mkdir(parents=True, exist_ok=True)
    snippet_master.write_text(
        f"COM-146 injected snippet {plan.nonce}\n\n{plan.private_sentinel}\n", encoding="utf-8"
    )
    injections_dir = root / ".claude" / "command-injections"
    injections_dir.mkdir(parents=True, exist_ok=True)
    snippet_link = injections_dir / f"com146-probe-{plan.nonce}.md"
    if not snippet_link.is_symlink():
        snippet_link.symlink_to(snippet_master)
    # The renderer enforces a governance ALLOWLIST of managed command names; probe filenames can
    # never (and must never) be allowlisted. The probe therefore renders under an allowlisted
    # NAME via --command-name while the source file stays the deployed nonce-named probe — the
    # pipeline under test (symlinked source + symlinked snippet + manifest) is unchanged.
    manifest = {
        "version": 1,
        "managed_commands": {
            INJECTION_PROBE_COMMAND_NAME: {
                "state": "managed",
                "anchors": {
                    f"com146-probe-{plan.nonce}": {
                        "required": True,
                        "snippet_path": f".claude/command-injections/com146-probe-{plan.nonce}.md",
                    }
                },
            }
        },
    }
    (root / ".claude" / "project-command-injections.yaml").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return root


def render_injection_probe(plan: ProbePlan) -> str:
    """Run the real command-injection renderer against the deployed probe command and return
    the rendered output. Hermetic: no claude subprocess; pass condition is the SNIPPET
    sentinel spliced through the anchor (proving symlinked command + symlinked snippet are
    readable by the injection pipeline)."""
    if plan.injection_project_root is None:
        raise RuntimeError("render_injection_probe requires a command-injection plan")
    proc = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "jswarm" / "devops_command_injection.py"),
            "render-file",
            "--source-file",
            str(plan.live_probe_target),
            "--project-root",
            str(plan.injection_project_root),
            "--command-name",
            INJECTION_PROBE_COMMAND_NAME,
        ],
        capture_output=True,
        text=True,
        timeout=plan.timeout_seconds,
        check=False,
    )
    return proc.stdout


def _append_evidence(plan: ProbePlan, result: SmokeResult, *, run_id: str) -> Path | None:
    if plan.log_root is None:
        return None
    plan.log_root.mkdir(parents=True, exist_ok=True)
    evidence = plan.log_root / f"{run_id}.ndjson"
    record = {
        "ts": _utc_now(),
        "run_id": run_id,
        "surface": result.surface,
        "nonce": plan.nonce,
        "passed": result.passed,
        "blocks_completion": result.blocks_completion,
        "detail": result.detail,
        "stdout_excerpt": result.stdout_excerpt,
        "live_probe_target": str(plan.live_probe_target),
        "claude_diagnostics": dict(LAST_CLAUDE_DIAGNOSTICS),
        # F-EG-4: command-injection is proven by the RENDERER, not a print session.
        "mode": "renderer" if plan.surface == "command-injection" else "print",
    }
    with evidence.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return evidence


# --- CLI ------------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", required=True, choices=SUPPORTED_SURFACES, help="AC-13 surface to smoke")
    parser.add_argument("--master-root", required=True, type=Path, help="probe master root (controlled master or tmp)")
    parser.add_argument("--common-root", required=True, type=Path, help="common checkout root (interface parity)")
    parser.add_argument("--home-root", required=True, type=Path, help=".claude root the probe deploys into")
    parser.add_argument("--manifest", type=Path, default=None, help="AC-12 backup manifest to roll back on smoke failure")
    parser.add_argument("--log-root", type=Path, default=None, help="evidence NDJSON root (.jswarm/logs/controlled-config-smoke)")
    parser.add_argument("--nonce", default=None, help="probe nonce (generated when omitted)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build + validate the probe plan only; never deploys a probe or invokes claude",
    )
    parser.add_argument(
        "--live-home",
        action="store_true",
        help="explicit acknowledgement required before probes may deploy into the real ~/.claude",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    nonce = args.nonce or f"{args.surface}-{secrets.token_hex(4)}"
    plan = build_probe_plan(
        surface=args.surface,
        master_root=args.master_root,
        common_root=args.common_root,
        home_root=args.home_root,
        nonce=nonce,
        manifest_path=args.manifest,
        log_root=args.log_root,
        live_home_ack=bool(args.live_home),
    )

    if args.dry_run:
        print(
            json.dumps(
                {
                    "surface": plan.surface,
                    "nonce": plan.nonce,
                    "prompt": plan.prompt,
                    "live_probe_target": str(plan.live_probe_target),
                    "master_probe_source": str(plan.master_probe_source),
                    "sentinel_in_prompt": plan.private_sentinel in plan.prompt,
                    "timeout_seconds": plan.timeout_seconds,
                    "dry_run": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    # Surface-categorical refusals come FIRST (they hold for ANY root):
    if args.surface == "settings":
        print(
            "smoke refused: the settings surface mutates the live settings.json in place and is "
            "driven only through the operator-gated hook+settings probe sequence, not the bare CLI"
        )
        return 1
    if args.surface == "hook":
        # MAJOR-3: an unregistered hook can never fire under claude --print — a bare-CLI hook
        # smoke would be a guaranteed false negative wired to rollback. Hooks require a
        # settings.json registration, so they are driven only through the coupled
        # hook+settings operator sequence.
        print(
            "smoke refused: a hook only fires with a settings.json registration; the bare CLI "
            "would always report a false negative — use the coupled hook+settings operator sequence"
        )
        return 1

    # Live gate (parity with deploy.py AC-11 + the P9 MAJOR-R2-1 dual classification): probes
    # may only touch the real ~/.claude under an explicit --live-home acknowledgement. The
    # classification is lexical OR resolved OR samefile, so a home-root reaching the live root
    # through a symlink alias (or APFS case-aliasing) cannot bypass the acknowledgement —
    # and the same classification rejects --live-home against a non-live root.
    targets_live = _classify_live_root(args.home_root)
    if targets_live and not args.live_home:
        print("smoke refused: probing the real ~/.claude requires the explicit --live-home acknowledgement")
        return 1
    if args.live_home and not targets_live:
        print("smoke refused: --live-home given but --home-root is not the real ~/.claude")
        return 1

    run_id = f"smoke-{_utc_now()}-{nonce}"
    _write_probe_master(plan)
    if args.surface == "command-injection":
        _stage_injection_probe_root(plan)
    deployed = _deploy_probe(plan, install_type="symlink")
    try:
        result = run_surface_smoke(args.surface, plan)
    finally:
        if deployed:
            _cleanup_probe(plan)
    evidence = _append_evidence(plan, result, run_id=run_id)
    print(
        json.dumps(
            {
                "surface": result.surface,
                "nonce": plan.nonce,
                "passed": result.passed,
                "blocks_completion": result.blocks_completion,
                "detail": result.detail,
                "evidence": str(evidence) if evidence else None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.passed else 1


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
