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
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import yaml


class WriteContext:
    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.actions: list[str] = []

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
        """
        prefix = "would run" if self.dry_run else "run"
        line = f"  {prefix}: {' '.join(argv)}"
        self.actions.append(line)
        print(line)
        if self.dry_run:
            return None
        try:
            return subprocess.run(argv, capture_output=True, text=True, **kwargs)
        except OSError as exc:
            # The executable itself is missing or not runnable -- report it the same
            # way a non-zero exit is reported (an actionable message), not a traceback.
            return subprocess.CompletedProcess(argv, 127, "", str(exc))
