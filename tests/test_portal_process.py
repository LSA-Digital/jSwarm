"""Real process tests, run identically on Mac and Linux (including WSL)."""
import json
from pathlib import Path
import socket
import subprocess
import sys
from urllib.request import urlopen

import psutil
import pytest

from jswarm.portal.process import PortalProcessError, start, state_dir, stop


def test_server_start_does_not_require_reverse_dns(tmp_path, monkeypatch):
    from jswarm.portal.server import serve

    def unavailable_dns(*args, **kwargs):
        raise AssertionError("local portal startup must not perform reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", unavailable_dns)
    server = serve(tmp_path / "publications", tmp_path / "threads", [tmp_path],
                   bind_host="127.0.0.1", port=0)
    try:
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.server_address[1]
    finally:
        server.server_close()


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JSWARM_HOME", str(Path.cwd()))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    directory = state_dir(tmp_path)
    directory.mkdir(parents=True)
    config = directory / "config.json"
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<html>Process-test fixture</html>")
    config.write_text(json.dumps({
        "approved_roots": [str(tmp_path)], "publications_dir": str(tmp_path / "publications"),
        "threads_dir": str(tmp_path / "threads"), "dist_dir": str(tmp_path / "dist"),
        "port": port, "bind_host": "127.0.0.1", "service_store_root": str(directory),
    }))
    try:
        yield tmp_path, config, port
    finally:
        # Pytest displays this only for failures, including CI-only startup
        # problems whose temporary server log would otherwise be lost.
        log = directory / "server.log"
        if log.exists():
            print(log.read_text()[-4000:])
        stop(tmp_path)


def test_real_start_ready_duplicate_stop_and_restart(portal, capsys):
    home, config, port = portal
    for _ in range(2):
        start(config, background=True)
        assert f":{port}/uat/" in capsys.readouterr().out
        record = json.loads((config.parent / "process.json").read_text())
        assert psutil.pid_exists(record["pid"])
        with urlopen(f"http://127.0.0.1:{port}/api/publications") as response:
            assert response.status == 200
        with pytest.raises(PortalProcessError, match="already running"):
            start(config, background=True)
        stop(home, dry_run=True)
        assert psutil.pid_exists(record["pid"])
        assert (config.parent / "process.json").exists()
        stop(home)
        assert not (config.parent / "process.json").exists()
        assert not (config.parent / "server.pid").exists()
        assert not (config.parent / "service.pid").exists()


def test_busy_configured_port_is_not_killed(portal):
    home, config, port = portal
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", port))
        holder.listen()
        with pytest.raises(PortalProcessError, match=f"port {port} unavailable"):
            start(config, background=True)
        assert holder.getsockname()[1] == port
        assert not (config.parent / "process.json").exists()


def test_stale_pid_cannot_kill_an_unrelated_process(tmp_path):
    directory = state_dir(tmp_path)
    directory.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (directory / "server.pid").write_text(str(child.pid))
        with pytest.raises(PortalProcessError, match="unrelated/reused"):
            stop(tmp_path)
        assert child.poll() is None
        assert (directory / "server.pid").exists()
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_dry_runs_write_nothing_even_with_no_home(tmp_path):
    home = tmp_path / "not-created"
    start(state_dir(home) / "config.json", background=True, dry_run=True)
    stop(home, dry_run=True)
    assert not home.exists()


def test_failed_server_does_not_report_success_or_leave_pid_files(portal):
    home, config, _ = portal
    config.write_text(json.dumps({"port": json.loads(config.read_text())["port"], "not_a_valid_setting": True}))
    with pytest.raises(PortalProcessError, match="exited"):
        start(config, background=True)
    for name in ("process.json", "server.pid", "service.pid"):
        assert not (config.parent / name).exists()


def test_uninstall_uses_same_identity_checked_stop(portal):
    from jswarm.installer.cli import _stop_portal_daemon
    from jswarm.installer.fsops import WriteContext

    home, config, _ = portal
    start(config, background=True)
    _stop_portal_daemon(WriteContext(dry_run=False, home=home), home)
    assert not (config.parent / "process.json").exists()


@pytest.mark.portal_build
def test_shell_entry_builds_starts_serves_uat_and_stops(portal):
    home, config, port = portal
    root = Path(__file__).resolve().parents[1]
    data = json.loads(config.read_text())
    data["dist_dir"] = str(root / "jswarm/portal/dist")
    config.write_text(json.dumps(data))
    result = subprocess.run(["bash", "install.sh", "portal", "--background"], cwd=root,
                            text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f":{port}/uat/" in result.stdout
    with urlopen(f"http://127.0.0.1:{port}/uat/") as response:
        assert response.status == 200
        assert len(response.read()) > 100
    result = subprocess.run(["bash", "install.sh", "portal", "--stop", "--dry-run"], cwd=root,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (config.parent / "process.json").exists()
    result = subprocess.run(["bash", "install.sh", "portal", "--stop"], cwd=root,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not (config.parent / "process.json").exists()
