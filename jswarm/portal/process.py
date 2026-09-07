"""Manage the local portal as a detached user process, without a service daemon.

No reboot registration or administrator access. Identity-checked shutdown also
accepts old PID files, but only when the command is our server using our config.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

import psutil


class PortalProcessError(RuntimeError):
    pass


def state_dir(home: Path) -> Path:
    return home / ".jswarm" / "decision-review"


@contextmanager
def _locked(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "process.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _server_process(pid: int, config: Path, created: float | None = None):
    """Never terminate a process merely because its PID was saved on disk."""
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        argv = process.cmdline()
        is_server = any(argv[i:i + 2] == ["-m", "jswarm.portal.server"] for i in range(len(argv) - 1))
        is_config = any(
            argv[i] == "--config" and Path(argv[i + 1]).resolve() == config.resolve()
            for i in range(len(argv) - 1)
        )
        if not is_server or not is_config or (created is not None and process.create_time() != created):
            raise PortalProcessError(f"refusing to stop unrelated/reused pid {pid}; inspect the portal PID files")
        return process
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied as exc:
        raise PortalProcessError(f"cannot verify portal pid {pid}; stop it manually") from exc


def _stop(directory: Path, *, dry_run: bool) -> None:
    config = directory / "config.json"
    record_path = directory / "process.json"
    candidates = {}
    if record_path.exists():
        try:
            record = json.loads(record_path.read_text())
            candidates[int(record["pid"])] = float(record["created"])
            config = Path(record["config"])
        except (ValueError, KeyError, TypeError) as exc:
            raise PortalProcessError(f"invalid process record: {record_path}; inspect it before stopping") from exc
    pid_files = [directory / "server.pid", directory / "service.pid"]
    for path in pid_files:
        if path.exists():
            try:
                candidates.setdefault(int(path.read_text().strip()), None)
            except ValueError:
                pass  # malformed stale marker, never a process to terminate
    # Verify every candidate before stopping any of them.
    processes = [_server_process(pid, config, created) for pid, created in candidates.items()]
    for process in processes:
        if process is None:
            continue
        if dry_run:
            print(f"would stop portal pid {process.pid}")
            continue
        try:
            process.terminate()  # psutil rechecks PID reuse before sending a signal
            deadline = time.monotonic() + 5
            while True:
                try:
                    process.wait(timeout=0.1)
                    break
                except psutil.TimeoutExpired:
                    # A detached process can be dead but not yet reaped by
                    # init (notably in containers). It is no longer a server.
                    if process.status() == psutil.STATUS_ZOMBIE:
                        break
                    if time.monotonic() >= deadline:
                        raise
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired as exc:
            raise PortalProcessError(f"portal pid {process.pid} did not stop; inspect it manually") from exc
        print(f"stopped portal pid {process.pid}")
    for path in [record_path, *pid_files]:
        if path.exists():
            if dry_run:
                print(f"would remove {path}")
            else:
                path.unlink()
    if not candidates:
        print("no portal process recorded; nothing to stop")


def stop(home: Path, *, dry_run: bool = False) -> None:
    directory = state_dir(home)
    if dry_run or not directory.exists():
        _stop(directory, dry_run=dry_run)
    else:
        with _locked(directory):
            _stop(directory, dry_run=False)


def start(config: Path, *, background: bool, dry_run: bool = False, timeout: float = 20) -> None:
    config = config.resolve()
    settings = json.loads(config.read_text()) if config.exists() else {}
    port = int(settings.get("port", 8766))
    host = settings.get("bind_host", "127.0.0.1")
    probe_host = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
    url_host = f"[{probe_host}]" if ":" in probe_host else probe_host
    url = f"http://{url_host}:{port}/uat/"
    argv = [sys.executable, "-m", "jswarm.portal.server", "--config", str(config)]
    root = Path(__file__).resolve().parents[2]
    if dry_run:
        print(f"would start portal in {'background' if background else 'foreground'}: {url}")
        return
    if not config.is_file():
        raise PortalProcessError(f"missing config: {config}; run ./install.sh install first")
    with _locked(config.parent):
        record_path = config.parent / "process.json"
        if record_path.exists():
            record = json.loads(record_path.read_text())
            if _server_process(int(record["pid"]), Path(record["config"]), float(record["created"])):
                raise PortalProcessError("portal is already running; use ./install.sh portal --stop first")
        # The server itself is the final authority on bind races. This check
        # gives a useful error and never signals whatever is using the port.
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError as exc:
                raise PortalProcessError(f"port {port} unavailable: {exc}; edit port in {config}") from exc
        if not background:
            print(f"portal: {url} (Ctrl-C to stop)", flush=True)
            # Close our process lock before exec: it is non-inheritable.
            os.chdir(root)
            os.execv(sys.executable, argv)
        log_path = config.parent / "server.log"
        with log_path.open("ab") as log:
            child = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, close_fds=True)
        try:
            identity = psutil.Process(child.pid).create_time()
            record_path.write_text(json.dumps({"pid": child.pid, "created": identity, "config": str(config)}))
            (config.parent / "server.pid").write_text(f"{child.pid}\n")
            opener = build_opener(ProxyHandler({}))  # local health checks never go through a proxy
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise PortalProcessError(f"portal exited ({child.returncode}); inspect {log_path}")
                try:
                    with opener.open(f"http://{url_host}:{port}/api/publications", timeout=0.5) as response:
                        ready = response.status == 200
                    if ready and child.poll() is None:
                        print(f"portal ready: {url} (pid {child.pid}); log: {log_path}")
                        return
                except OSError:
                    pass
                time.sleep(0.1)
            raise PortalProcessError(f"portal never became ready; inspect {log_path}")
        except BaseException:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            for name in ("process.json", "server.pid", "service.pid"):
                (config.parent / name).unlink(missing_ok=True)
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.stop:
            stop(Path.home(), dry_run=args.dry_run)
        else:
            start(state_dir(Path.home()) / "config.json", background=args.background, dry_run=args.dry_run)
    except (PortalProcessError, OSError, ValueError, KeyError) as exc:
        print(f"portal: {exc}", file=sys.stderr)
        print("Next: correct the error, then ./install.sh portal   (here, in the jSwarm clone)")
        return 1
    print("Next: ./install.sh portal --stop   (here, in the jSwarm clone) when done, or portal to start again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
