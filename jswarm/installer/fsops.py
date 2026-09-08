"""The one place `--dry-run` is decided.

Every mutating helper in `jswarm.installer` takes a `WriteContext` and calls
its methods instead of touching the filesystem directly. `WriteContext.dry_run`
is set once, at the top of each subcommand, and every write anywhere
downstream of that respects it: dry-run truly writes nothing, because there
is only one place that could forget to check the flag, not the twenty-first
call site buried in a step function.

Every method also prints a one-line description of the action it is taking
(or would take), so `--dry-run` output doubles as a plan of what a real run
would do.

`home` is the second thing decided once, here: the directory this install
(or uninstall, or adopt, or unadopt) is scoped to. `run()` builds every
subprocess's environment explicitly from it rather than letting the child
inherit whatever the parent process happens to have -- see `_scoped_env`.
"""
from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
from pathlib import Path

import yaml

# Environment variables that a host CLI treats as an override for where it
# reads/writes its *own* global config, independent of `HOME`. If one of
# these is inherited from the parent process, a subprocess would escape the
# `home` a WriteContext is scoped to even though `HOME` itself is set
# correctly -- exactly the failure mode this module exists to close.
# `CLAUDE_CONFIG_DIR` is the confirmed case: the `claude` CLI honors it as
# "the configuration home" ahead of the location `HOME` would otherwise
# derive, so a real `claude mcp add`/`claude mcp remove` run with a scoped
# `HOME` can still land in the real `~/.claude` (or wherever
# `CLAUDE_CONFIG_DIR` happened to point in the parent shell) unless it is
# stripped here. Removed unconditionally -- containment must not depend on
# the parent environment happening not to have one set.
_HOST_OVERRIDE_ENV_VARS = ("CLAUDE_CONFIG_DIR",)


def _scoped_env(home: Path, base_env: dict | None = None) -> dict[str, str]:
    """The environment a subprocess launched by this WriteContext gets:
    the parent's environment (or `base_env`, if a caller supplied one) with
    `HOME` forced to `home` and every known host config-dir override
    stripped, so nothing the child does can read or write outside `home`
    on the strength of inherited state alone.
    """
    env = dict(base_env) if base_env is not None else dict(os.environ)
    env["HOME"] = str(home)
    for var in _HOST_OVERRIDE_ENV_VARS:
        env.pop(var, None)
    return env


class WriteContext:
    def __init__(self, dry_run: bool, home: Path) -> None:
        self.dry_run = dry_run
        self.home = Path(home)
        self.actions: list[str] = []

    def env(self) -> dict[str, str]:
        """The explicit, home-scoped environment `run()` uses for every
        subprocess. Exposed so call sites that must shell out directly
        (a read-only query that isn't itself a write action, so doesn't go
        through `run()`) can still get the same containment.
        """
        return _scoped_env(self.home)

    def _note(self, verb: str, target: Path) -> None:
        prefix = "would " if self.dry_run else ""
        line = f"  {prefix}{verb} {target}"
        self.actions.append(line)
        print(line)

    def write_text(self, path: Path, content: str) -> None:
        path = Path(path)
        self._note("write", path)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, path: Path, data) -> None:
        self.write_text(Path(path), json.dumps(data, indent=2) + "\n")

    def write_yaml(self, path: Path, data) -> None:
        self.write_text(Path(path), yaml.safe_dump(data, sort_keys=False))

    def mkdir(self, path: Path) -> None:
        path = Path(path)
        self._note("create directory", path)
        if self.dry_run:
            return
        path.mkdir(parents=True, exist_ok=True)

    def touch(self, path: Path) -> None:
        path = Path(path)
        self._note("touch", path)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def copy_file(self, src: Path, dst: Path) -> None:
        src, dst = Path(src), Path(dst)
        self._note(f"copy {src} to", dst)
        if self.dry_run:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def copy_tree(self, src: Path, dst: Path) -> None:
        src, dst = Path(src), Path(dst)
        self._note(f"copy {src} to", dst)
        if self.dry_run:
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def remove(self, path: Path) -> None:
        path = Path(path)
        if not path.exists() and not path.is_symlink():
            return
        self._note("remove", path)
        if self.dry_run:
            return
        path.unlink(missing_ok=True)

    def remove_tree(self, path: Path) -> None:
        path = Path(path)
        if not path.exists():
            return
        self._note("remove", path)
        if self.dry_run:
            return
        shutil.rmtree(path, ignore_errors=True)

    def run(self, argv: list[str], **kwargs) -> "subprocess.CompletedProcess[str] | None":
        """Run an external command as a write action (installing a binary,
        registering an MCP server, ...). Prints what it does (or would do)
        the same way every other method here does; in dry-run, nothing is
        executed and this returns None.

        Always passes an explicit `env` built by `_scoped_env`, `HOME`
        forced to `self.home` -- a caller-supplied `env=` kwarg is used as
        the base instead of `os.environ`, but `HOME` and the host override
        vars are still forced/stripped on top of it. The child never
        depends on inherited state to stay inside `self.home`.
        """
        prefix = "would run" if self.dry_run else "run"
        line = f"  {prefix}: {shlex.join(argv)}"
        self.actions.append(line)
        print(line)
        if self.dry_run:
            return None
        kwargs["env"] = _scoped_env(self.home, kwargs.get("env"))
        try:
            return subprocess.run(argv, capture_output=True, text=True, **kwargs)
        except OSError as exc:
            # The executable itself is missing or not runnable -- report it the same
            # way a non-zero exit is reported (an actionable message), not a traceback.
            return subprocess.CompletedProcess(argv, 127, "", str(exc))
