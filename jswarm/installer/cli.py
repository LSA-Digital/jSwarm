"""The Python side of `install.sh`.

`install.sh` (bash) owns bootstrapping the venv, since nothing here can run
before that exists, and the three operating-context "next step" framing
that closes every command. Everything else — the lock file, backups, the
skills/portal-config deploy, adopt, unadopt, upgrade, uninstall — lives here
so it goes through one `WriteContext` and one dry-run decision (see
`jswarm.installer.fsops`) instead of being re-implemented in bash.

Invoked as `python -m jswarm.installer.cli <subcommand> ...`, always with
the working directory set to the jSwarm clone (`$JSWARM_HOME`), so that
`jswarm.paths.jswarm_home()` and a plain `import jswarm` both resolve.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REQUIRED_INSTALL_STEPS = ("venv", "skills", "portal_config")


def _public_version(source: Path, *, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=5, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "v1.0.0-dev"
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "v1.0.0-dev"


# --------------------------------------------------------------------- check
def _cmd_check(args: argparse.Namespace) -> int:
    from jswarm.platform import current as current_platform

    plat = current_platform()
    print("check: prerequisites")
    checks = plat.check_prerequisites()
    width = max((len(c.name) for c in checks), default=0)
    missing = False
    for c in checks:
        status = "ok  " if c.ok else ("opt " if c.optional else "miss")
        print(f"  [{status}] {c.name.ljust(width)}" + ("" if c.ok else f"   fix: {c.remedy}"))
        missing = missing or (not c.ok and not c.optional)

    print()
    if missing:
        print("check: required items missing (see fix lines above)")
        print("Next: run the fix commands above, then ./install.sh check   (here, in the jSwarm clone)")
        return 1
    print("check: all prerequisites present")
    print("Next: ./install.sh install --dry-run   (here, in the jSwarm clone)")
    return 0


# ------------------------------------------------------------------- install
# colgrep-search and code-overview both lean on the colgrep_search /
# colgrep_list_dev_indices MCP tools that only exist once ColGREP is actually
# installed and registered (see _install_colgrep). Installing them without that
# would leave a silently non-functional command sitting in the user's command
# surface -- unacceptable, per the same standard --with-colgrep is held to.
# Excluded rather than installed with a "not available" banner: colgrep-search is
# `user-invocable: false`, designed to be copied verbatim into other skills'
# subagent-dispatch prompts, so a banner at its own top would not stop that
# reliance.
_COLGREP_DEPENDENT_SKILLS = {"colgrep-search", "code-overview"}


def _expected_skill_names(source: Path, *, with_colgrep: bool) -> list[str]:
    """The top-level skill names `_install_skills` deploys (or would deploy)
    for a given `with_colgrep` setting -- real skills plus non-colliding
    `_shims/` aliases -- without touching the destination filesystem.

    Shared by `_install_skills` (to decide what to copy) and `_cmd_verify`
    (to decide what must actually be present on disk): computing this list
    in two places let them drift apart, which is exactly how `verify`
    reported "all artefacts present" while the colgrep-gated skills were
    silently missing -- it never knew colgrep-search/code-overview were
    supposed to exist, so it never looked for them.
    """
    skills_source = source / "skills"
    if not skills_source.is_dir():
        return []

    excluded = set() if with_colgrep else _COLGREP_DEPENDENT_SKILLS
    real_skill_dirs = sorted(
        p for p in skills_source.iterdir()
        if p.is_dir() and p.name != "_shims" and p.name not in excluded
    )
    real_names_lower = {p.name.lower() for p in real_skill_dirs}
    names = [p.name for p in real_skill_dirs]

    # _shims/ is a source-tree grouping directory only. Each child is a
    # deprecation-alias skill that must land at its own discoverable
    # top-level name (dest_root/<alias>), exactly like a real skill -- not
    # nested under a literal "_shims" directory, which nothing that
    # discovers skills by top-level name would ever find. A shim whose name
    # only differs from a real skill's by case is never actually deployed
    # under its own name (see the case-collision note in `_install_skills`),
    # so it must not be listed as expected either -- that path IS the real
    # skill's directory.
    shims_source = skills_source / "_shims"
    if shims_source.is_dir():
        for shim_dir in sorted(p for p in shims_source.iterdir() if p.is_dir()):
            if shim_dir.name.lower() not in real_names_lower:
                names.append(shim_dir.name)

    return names


def _install_skills(ctx, home: Path, source: Path, timestamp: str, *, with_colgrep: bool = False) -> None:
    from jswarm.host import current as current_host
    from jswarm.installer import backup as backup_mod

    host = current_host()
    dest_root = host.skills_dir()
    skills_source = source / "skills"
    print(f"  skills: {skills_source} -> {dest_root}")
    if not skills_source.is_dir():
        print(f"  skills: {skills_source} not found, skipping")
        return

    excluded = set() if with_colgrep else _COLGREP_DEPENDENT_SKILLS
    for name in sorted(excluded):
        if (skills_source / name).is_dir():
            print(f"  skills: {name} skipped (requires --with-colgrep)")

    expected = _expected_skill_names(source, with_colgrep=with_colgrep)
    real_names = {
        p.name for p in skills_source.iterdir()
        if p.is_dir() and p.name != "_shims" and p.name not in excluded
    }

    # Real skills first, shims second: a rename that only changed case (e.g.
    # jsetup -> jSetup) leaves the deprecated shim's name identical to the
    # real skill's name on the case-insensitive filesystem every supported
    # host uses, so `dest_root/jsetup` and `dest_root/jSetup` are the *same*
    # destination directory. Installing both, in either order, would let one
    # silently overwrite the other -- landing the deprecation-stub content
    # under the real skill's name, or vice versa. Real skills install first
    # so identity is never in doubt; the shim loop below then skips any shim
    # whose name collides case-insensitively with a real skill instead of
    # clobbering it.
    for name in expected:
        if name not in real_names:
            continue  # a shim; handled below
        skill_dir = skills_source / name
        dest = dest_root / name
        if dest.exists():
            backup_mod.backup_path(ctx, home, dest, timestamp)
        ctx.copy_tree(skill_dir, dest)

    shims_source = skills_source / "_shims"
    if not shims_source.is_dir():
        return
    for name in expected:
        if name in real_names:
            continue
        shim_dir = shims_source / name
        shim_dest = dest_root / name
        if shim_dest.exists():
            backup_mod.backup_path(ctx, home, shim_dest, timestamp)
        ctx.copy_tree(shim_dir, shim_dest)
    skipped_shims = {p.name for p in shims_source.iterdir() if p.is_dir()} - set(expected)
    for shim_name in sorted(skipped_shims):
        dest_root_shim = dest_root / shim_name
        print(
            f"  skills: shim '{shim_name}' skipped -- its name is a case-only "
            f"variant of a real skill already installed at {dest_root_shim}, "
            "the same path on a case-insensitive filesystem"
        )


def _install_portal_config(ctx, home: Path, source: Path, timestamp: str) -> None:
    from jswarm.installer import backup as backup_mod

    template = source / "templates" / "decision-review" / "config.template.json"
    dest = home / ".jswarm" / "decision-review" / "config.json"
    print(f"  portal_config: {template} -> {dest}")
    if dest.exists():
        backup_mod.backup_path(ctx, home, dest, timestamp)
        print(f"  portal_config: {dest} already present, left as-is (re-rendering resets active UAT rounds)")
        return
    if not template.is_file():
        print(f"  portal_config: {template} not found, skipping")
        return
    text = template.read_text(encoding="utf-8")
    text = (
        text.replace("__JSWARM_COMMON__", str(source))
        .replace("__HOME__", str(home))
        .replace("__PORT_UI__", "8765")
        .replace("__PORT_BACKEND__", "8766")
    )
    ctx.write_text(dest, text)


def _install_colgrep(ctx, source: Path) -> bool:
    """Install (or verify) the `colgrep` CLI and register the bundled MCP
    server with the agent host. Returns True only when ColGREP is actually
    ready to use -- that, not the `--with-colgrep` flag alone, is what gates
    installing the two skills that depend on it (see `_install_skills`).
    """
    import shutil

    from jswarm.colgrep_mcp_server import _colgrep_binary

    binary = _colgrep_binary()
    if binary:
        print(f"  colgrep: found existing binary at {binary}")
    else:
        if shutil.which("cargo") is None:
            print(
                "  colgrep: Rust toolchain not found (no `cargo` on PATH). Install it: "
                "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"
            )
            print("  colgrep: skipped -- install the Rust toolchain and re-run ./install.sh install --with-colgrep")
            return False

        print("  colgrep: installing (cargo install colgrep)")
        result = ctx.run(["cargo", "install", "colgrep"])
        if result is not None and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:400]
            print(f"  colgrep: `cargo install colgrep` failed (exit {result.returncode}): {detail}")
            return False
        if ctx.dry_run:
            binary = "colgrep"  # nothing was actually installed to verify
        else:
            binary = _colgrep_binary()
            if not binary:
                print(
                    "  colgrep: `cargo install colgrep` reported success but the binary is "
                    "still not on PATH; add ~/.cargo/bin to PATH and re-run"
                )
                return False

    from jswarm import paths as jswarm_paths
    from jswarm.host import current as current_host

    host = current_host()
    mcp_server = source / "jswarm" / "colgrep_mcp_server.py"
    argv = host.mcp_add_argv("colgrep", str(jswarm_paths.python()), [str(mcp_server)])
    print(f"  colgrep: registering the MCP server with {host.name}")
    result = ctx.run(argv)
    if result is not None and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:400]
        # `claude mcp add` exits non-zero both for a real registration failure
        # and for "this name is already registered" -- the latter is not a
        # failure at all, it is the idempotency signal that ColGREP is
        # already correctly set up (a second `install --with-colgrep`, a
        # prior partial install that got this far, or a leftover
        # registration from outside this installer entirely). Treating it as
        # a failure was the actual bug: it set `colgrep_ready = False`, which
        # is exactly the flag `_cmd_install`'s `skills_need_run` checks
        # before (re)deploying colgrep-search/code-overview, so the
        # documented `install --with-colgrep` resume path silently skipped
        # both skills with no error, and also never recorded "colgrep" as a
        # completed step -- which is also why `uninstall` had nothing to undo
        # (see `_cmd_uninstall`'s `colgrep_registered` check).
        if "already exists" in detail.lower():
            print(f"  colgrep: MCP server already registered ({detail})")
        else:
            print(f"  colgrep: MCP registration failed (exit {result.returncode}): {detail}")
            return False

    print("  colgrep: ready -- colgrep_search and colgrep_list_dev_indices are registered")
    return True


def _cmd_install(args: argparse.Namespace) -> int:
    from jswarm import paths as jswarm_paths
    from jswarm.installer import backup as backup_mod
    from jswarm.installer import lockfile
    from jswarm.installer.fsops import WriteContext

    home = Path.home()
    source = jswarm_paths.jswarm_home()
    ctx = WriteContext(dry_run=args.dry_run, home=home)

    existing = lockfile.read(home)
    steps = list(existing.steps_completed) if existing else []
    if "venv" not in steps:
        steps.append("venv")  # true by construction: this process is running under a real venv python
    installed_at = existing.installed_at if existing else lockfile.now_iso()
    version = _public_version(source, env=ctx.env())

    print(f"install: {lockfile.lock_path(home)}")
    if args.dry_run:
        print("DRY-RUN: nothing will be written.")

    def _save(state: str) -> None:
        lockfile.write(ctx, home, lockfile.Lock(public_version=version, installed_at=installed_at, state=state, steps_completed=steps))

    _save("partial")

    timestamp = backup_mod.utc_timestamp()

    # colgrep is optional and never gates completeness, but it runs before "skills"
    # so _install_skills knows whether ColGREP actually ended up ready --
    # installing colgrep-search/code-overview without a working ColGREP would leave
    # a silently non-functional command sitting in the user's command surface.
    if "colgrep" in steps:
        print("install: colgrep (already done)")
        colgrep_ready = True
    elif not args.with_colgrep:
        print("install: colgrep")
        print("  colgrep: skipped (pass --with-colgrep to enable)")
        colgrep_ready = False
    else:
        print("install: colgrep")
        colgrep_ready = _install_colgrep(ctx, source)
        if colgrep_ready:
            steps.append("colgrep")
            _save("partial")

    # "skills" alone is not a fine-enough marker: the first `install` (no
    # --with-colgrep) legitimately completes "skills" having deployed everything
    # except the two colgrep-gated skills (colgrep-search, code-overview). A later
    # `install --with-colgrep` -- the documented resume path ("re-run it with the
    # flag added") -- must still redeploy skills once, now that colgrep is ready,
    # even though "skills" is already marked done. "skills_colgrep" tracks that
    # narrower fact: it is only set once colgrep-dependent skills have actually
    # been included in a skills deploy.
    skills_need_run = "skills" not in steps or (colgrep_ready and "skills_colgrep" not in steps)
    if not skills_need_run:
        print("install: skills (already done)")
    else:
        print("install: skills")
        _install_skills(ctx, home, source, timestamp, with_colgrep=colgrep_ready)
        if "skills" not in steps:
            steps.append("skills")
        if colgrep_ready and "skills_colgrep" not in steps:
            steps.append("skills_colgrep")
        _save("partial")

    if "portal_config" in steps:
        print("install: portal_config (already done)")
    else:
        print("install: portal_config")
        _install_portal_config(ctx, home, source, timestamp)
        steps.append("portal_config")
        _save("partial")

    required = set(REQUIRED_INSTALL_STEPS)
    state = "complete" if required.issubset(steps) else "partial"
    _save(state)

    print()
    if args.dry_run:
        print("DRY-RUN complete. Re-run without --dry-run to apply.")
        print("Next: ./install.sh install   (here, in the jSwarm clone) to apply it.")
        return 0
    if state == "complete":
        print("jSwarm installed.")
        print("Next: ./install.sh verify   (here, in the jSwarm clone)")
        return 0
    print("install: partial (see the steps above). Re-run: ./install.sh install")
    print("Next: ./install.sh install   (here, in the jSwarm clone) to finish it.")
    return 1


# -------------------------------------------------------------------- verify
def _cmd_verify(args: argparse.Namespace) -> int:
    from jswarm import paths as jswarm_paths
    from jswarm.installer import lockfile

    home = Path.home()
    lock = lockfile.read(home)
    if lock is None:
        print("verify: no install found.")
        print("Next: ./install.sh install   (here, in the jSwarm clone)")
        return 1
    if lock.state != "complete":
        done = ", ".join(lock.steps_completed) or "none"
        print(f"verify: install is partial. Steps completed so far: {done}.")
        print("Next: ./install.sh install   (here, in the jSwarm clone) to finish it.")
        return 1

    from jswarm.host import current as current_host

    host = current_host()
    source = jswarm_paths.jswarm_home()
    dest_root = host.skills_dir()

    # A lock file recording "skills" (and, once ColGREP is enabled,
    # "skills_colgrep") as completed steps is a record of what the installer
    # *attempted*, not proof of what actually survived on disk -- the old
    # "skills dir is a directory" check below is satisfied as long as *any*
    # skill is still there, so it kept reporting "all artefacts present"
    # even when `_install_colgrep` mis-reported an already-registered MCP
    # server as a failure and silently left colgrep-search and code-overview
    # undeployed (see `_install_colgrep`). This recomputes the exact set of
    # skill names that lock state says should be deployed -- via the same
    # `_expected_skill_names` helper `_install_skills` uses to decide what to
    # copy -- and checks each one is actually present, so the two can never
    # silently drift apart again.
    with_colgrep = "skills_colgrep" in lock.steps_completed
    expected_skills = _expected_skill_names(source, with_colgrep=with_colgrep)
    missing_skills = [name for name in expected_skills if not (dest_root / name / "SKILL.md").is_file()]

    rows = [
        ("skills dir", dest_root.is_dir(), str(dest_root)),
        ("portal config", (home / ".jswarm" / "decision-review" / "config.json").is_file(), str(home / ".jswarm" / "decision-review" / "config.json")),
        ("venv python", jswarm_paths.python().is_file(), str(jswarm_paths.python())),
    ]
    width = max(len(r[0]) for r in rows + [("skills deployed", None, None)])
    ok = True
    for name, present, detail in rows:
        print(f"{name.ljust(width)}  {'ok     ' if present else 'MISSING'}  {detail}")
        ok = ok and present

    skills_ok = not missing_skills
    skills_detail = (
        f"{len(expected_skills)}/{len(expected_skills)} present"
        if skills_ok
        else f"{len(expected_skills) - len(missing_skills)}/{len(expected_skills)} present; missing: {', '.join(missing_skills)}"
    )
    print(f"{'skills deployed'.ljust(width)}  {'ok     ' if skills_ok else 'MISSING'}  {skills_detail}")
    ok = ok and skills_ok

    print()
    if not ok:
        print("verify: some deployed artefacts are missing.")
        print("Next: ./install.sh install   (here, in the jSwarm clone) to repair them.")
        return 1
    print("verify: install complete, all artefacts present.")
    print("Next: ./install.sh adopt <your repo>   (here, in the jSwarm clone)")
    return 0


# --------------------------------------------------------------------- adopt
def _cmd_adopt(args: argparse.Namespace) -> int:
    from jswarm.installer.adopt import AdoptError, adopt

    try:
        result = adopt(Path(args.repo), jira_key=args.jira_key, hooks=not args.no_hooks, dry_run=args.dry_run)
    except AdoptError as exc:
        print(f"adopt: {exc}", file=sys.stderr)
        return 1

    for line in result.report_lines:
        print(line)
    print()
    if args.dry_run:
        print("DRY-RUN complete. Re-run without --dry-run to adopt.")
        print(f"Next: ./install.sh adopt {result.repo}   (here, in the jSwarm clone) to apply it.")
        return 0
    print(f"adopt: {result.repo} is adopted.")
    print(f"Next: /jSetup   (agent session, opened in {result.repo})")
    return 0


def _cmd_unadopt(args: argparse.Namespace) -> int:
    from jswarm.installer.unadopt import unadopt

    result = unadopt(Path(args.repo), dry_run=args.dry_run)
    for line in result.report_lines:
        print(line)
    if args.dry_run:
        print("DRY-RUN complete. Re-run without --dry-run to unadopt.")
        print(f"Next: ./install.sh unadopt {args.repo}   (here, in the jSwarm clone) to apply it.")
        return 0
    print("Next: nothing further required -- adopt it again with ./install.sh adopt <repo-path>   (here, in the jSwarm clone) if needed.")
    return 0


# ------------------------------------------------------------------- upgrade
def _cmd_upgrade(args: argparse.Namespace) -> int:
    from jswarm import paths as jswarm_paths
    from jswarm.installer import backup as backup_mod
    from jswarm.installer import lockfile
    from jswarm.installer.fsops import WriteContext

    home = Path.home()
    lock = lockfile.read(home)
    if lock is None:
        print("upgrade: no existing install found.")
        print("Next: ./install.sh install   (here, in the jSwarm clone)")
        return 1

    source = jswarm_paths.jswarm_home()
    from_version = lock.public_version
    ctx = WriteContext(dry_run=args.dry_run, home=home)
    to_version = _public_version(source, env=ctx.env())
    print(f"upgrade: {from_version} -> {to_version}")

    if args.dry_run:
        pieces = ", ".join(["skills", "portal_config"] + (["colgrep"] if "colgrep" in lock.steps_completed else []))
        print(f"DRY-RUN: would refresh Python dependencies, redeploy {pieces} and rewrite {lockfile.lock_path(home)}")
        print("Next: ./install.sh upgrade   (here, in the jSwarm clone) to apply it.")
        return 0

    result = ctx.run([str(jswarm_paths.python()), "-m", "pip", "install", "-q", "-r", str(source / "requirements.txt")])
    if result is not None and result.returncode != 0:
        print("upgrade: Python dependency refresh failed; deployed skills and lock left unchanged.")
        print(result.stderr or result.stdout or "")
        print("Next: ./install.sh install   (here, in the jSwarm clone) to repair dependencies")
        return 1
    timestamp = backup_mod.utc_timestamp()
    colgrep_ready = "colgrep" in lock.steps_completed
    _install_skills(ctx, home, source, timestamp, with_colgrep=colgrep_ready)
    steps = list(lock.steps_completed)
    for step in ("venv", "skills"):
        if step not in steps:
            steps.append(step)
    if colgrep_ready and "skills_colgrep" not in steps:
        steps.append("skills_colgrep")
    lockfile.write(ctx, home, lockfile.Lock(public_version=to_version, installed_at=lock.installed_at, state=lock.state, steps_completed=steps))
    print(f"upgrade: done ({from_version} -> {to_version})")
    print("Next: ./install.sh verify   (here, in the jSwarm clone)")
    return 0


# ----------------------------------------------------------------- uninstall
def _stop_portal_daemon(ctx, home: Path) -> None:
    from jswarm.portal.process import stop

    stop(home, dry_run=ctx.dry_run)


def _cmd_uninstall(args: argparse.Namespace) -> int:
    from jswarm.host import current as current_host
    from jswarm.installer import lockfile, registry
    from jswarm.installer.fsops import WriteContext

    home = Path.home()
    ctx = WriteContext(dry_run=args.dry_run, home=home)
    host = current_host()

    # Read before anything below removes the lock file: "colgrep" recorded
    # as a completed step is the only record that `install` ever ran
    # `claude mcp add` for it, so it is also the only signal `uninstall` has
    # for whether there is a registration to undo.
    lock = lockfile.read(home)
    colgrep_registered = bool(lock and "colgrep" in lock.steps_completed)

    source_skill_names: list[str] = []
    try:
        from jswarm import paths as jswarm_paths

        skills_source = jswarm_paths.jswarm_home() / "skills"
        if skills_source.is_dir():
            # Real skills first, same as _install_skills: everything directly
            # under skills/ except the _shims/ grouping directory itself.
            real_names = [p.name for p in skills_source.iterdir() if p.is_dir() and p.name != "_shims"]
            source_skill_names = list(real_names)
            # Shim skills (skills/_shims/<name>/) are deployed at their own
            # top-level dest_root/<name>, exactly like a real skill -- not
            # nested under "_shims" -- so they must be named here too, or
            # uninstall never lists (and never removes) them. Mirror
            # _install_skills' case-insensitive collision skip: a shim whose
            # name only differs from a real skill's by case is never actually
            # deployed under its own name, so it must not be listed for
            # removal either (that path IS the real skill's directory).
            shims_source = skills_source / "_shims"
            if shims_source.is_dir():
                real_names_lower = {name.lower() for name in real_names}
                for shim_dir in shims_source.iterdir():
                    if shim_dir.is_dir() and shim_dir.name.lower() not in real_names_lower:
                        source_skill_names.append(shim_dir.name)
    except Exception:  # noqa: BLE001 - listing deployed skills must never block uninstall
        source_skill_names = []

    deployed = [host.skills_dir() / name for name in source_skill_names if (host.skills_dir() / name).exists()]
    jswarm_dir = home / ".jswarm"
    backups_dir = jswarm_dir / "backups"

    verb = "would remove" if args.dry_run else "removing"
    print(f"uninstall: {verb}:")
    for p in deployed:
        print(f"  - {p}")
    print(f"  - {jswarm_dir} (installer state, portal config)")
    backups_note = "kept" if args.keep_backups else "kept unless you confirm deleting them interactively"
    print(f"  - {backups_dir} ({backups_note})")
    if colgrep_registered:
        print(f"  - MCP server 'colgrep' registration ({host.name})")

    repos = registry.read(home)
    if repos:
        print("uninstall: adopted repositories on record (left untouched; undo each with):")
        for r in repos:
            print(f"  - ./install.sh unadopt {r}")
    else:
        print("uninstall: no adopted repositories on record.")

    if args.dry_run:
        print("Next: ./install.sh uninstall   (here, in the jSwarm clone) to apply it.")
        return 0

    _stop_portal_daemon(ctx, home)

    if colgrep_registered:
        print("uninstall: colgrep MCP registration")
        result = ctx.run(host.mcp_remove_argv("colgrep"))
        if result is not None and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:400]
            print(
                f"  colgrep: MCP removal failed (exit {result.returncode}): {detail}; "
                f"remove it by hand: {host.name} mcp remove colgrep --scope user"
            )
        else:
            print("  colgrep: MCP registration removed")

    for p in deployed:
        ctx.remove_tree(p)

    delete_backups = False
    if not args.keep_backups and sys.stdin.isatty():
        answer = input("Delete backups too? [y/N] ").strip().lower()
        delete_backups = answer.startswith("y")

    if jswarm_dir.exists():
        for child in sorted(jswarm_dir.iterdir()):
            if child.name == "backups":
                continue
            if child.is_dir():
                ctx.remove_tree(child)
            else:
                ctx.remove(child)
        if delete_backups and backups_dir.exists():
            ctx.remove_tree(backups_dir)
        elif backups_dir.exists():
            print(f"  kept {backups_dir}")
        if not any(jswarm_dir.iterdir()):
            ctx.remove_tree(jswarm_dir)

    print("uninstall: done.")
    print("Next: nothing further required -- jSwarm is uninstalled. Re-run ./install.sh install   (here, in the jSwarm clone) to reinstall.")
    return 0


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jswarm.installer.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check")

    p_install = sub.add_parser("install")
    p_install.add_argument("--dry-run", action="store_true")
    p_install.add_argument("--with-colgrep", action="store_true")

    sub.add_parser("verify")

    p_adopt = sub.add_parser("adopt")
    p_adopt.add_argument("repo")
    p_adopt.add_argument("--jira-key")
    p_adopt.add_argument("--no-hooks", action="store_true")
    p_adopt.add_argument("--dry-run", action="store_true")

    p_unadopt = sub.add_parser("unadopt")
    p_unadopt.add_argument("repo")
    p_unadopt.add_argument("--dry-run", action="store_true")

    p_upgrade = sub.add_parser("upgrade")
    p_upgrade.add_argument("--dry-run", action="store_true")

    p_uninstall = sub.add_parser("uninstall")
    p_uninstall.add_argument("--keep-backups", action="store_true")
    p_uninstall.add_argument("--dry-run", action="store_true")

    return parser


_HANDLERS = {
    "check": _cmd_check,
    "install": _cmd_install,
    "verify": _cmd_verify,
    "adopt": _cmd_adopt,
    "unadopt": _cmd_unadopt,
    "upgrade": _cmd_upgrade,
    "uninstall": _cmd_uninstall,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return _HANDLERS[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
