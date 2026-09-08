import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from jswarm.hud import quotas, render
from jswarm.installer.hud import configure, command, verify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JSWARM_HOME", str(ROOT))
    return tmp_path


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_claude_quotas_zero_missing_and_expired(home):
    assert quotas({"rate_limits": {"five_hour": {"used_percentage": 0, "resets_at": 4600}}}, {}, 1000) == "Claude 5h 0% used (reset 1h0m)"
    assert "unavailable" in quotas({}, {}, 1000)
    assert "awaiting refresh" in quotas({"rate_limits": {"seven_day": {"used_percentage": 90, "resets_at": 999}}}, {}, 1000)
    assert "unavailable" in quotas({"rate_limits": {"five_hour": {"used_percentage": None}}}, {}, 1000)
    assert "125%" in quotas({"rate_limits": {"spend_limit": {"used_percentage": 125}}}, {}, 1000)


@pytest.mark.parametrize("provider", ["gpt", "glm", "anthropic"])
def test_session_bound_provider_and_staleness(home, provider):
    root = home / "quotas"
    env = {"JSWARM_PROVIDER_QUOTA_ROOT": str(root), "ANTHROPIC_BASE_URL": "http://localhost:1234"}
    binding = {"schema": "jswarm.provider-quota-binding.v1", "session_id": "one", "provider": provider, "account_ref": "a" * 64}
    snapshot = {"schema": "jswarm.provider-quota.v1", "provider": provider, "account_ref": "a" * 64,
                "observed_at": 990, "windows": {"7d": {"used_percent": 42, "resets_at": "1970-01-02T00:00:00Z"}}}
    write(root / "bindings/one.json", binding)
    write(root / "accounts" / provider / ("a" * 64 + ".json"), snapshot)
    payload = {"session_id": "one", "rate_limits": {"five_hour": {"used_percentage": 99}}}
    assert "42%" in quotas(payload, env, 1000)
    assert "99%" not in quotas(payload, env, 1000)
    assert "[stale]" in quotas(payload, env, 1200)
    assert "unavailable" in quotas({**payload, "session_id": "two"}, env, 1000)
    snapshot["account_ref"] = "b" * 64
    write(root / "accounts" / provider / ("a" * 64 + ".json"), snapshot)
    assert "unavailable" in quotas(payload, env, 1000)


def test_no_network_no_transcript_and_no_terminal_control_injection(home, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("HUD must not open sockets"))
    poison = home / "transcript"
    poison.write_text("secret marker")
    result = render({"model": {"display_name": "Claude\x1b]0;evil\x07"}, "transcript_path": str(poison)}, {}, 1000)
    assert "\x1b" not in result and "\x07" not in result and "secret marker" not in result
    assert "Context unavailable" in result


def test_only_this_sessions_slug_is_shown(home):
    repo = home / "repo"
    (repo / ".git").mkdir(parents=True)
    write(repo / ".jswarm/state/sessions/one/active-ticket.json", {"session_id": "one", "ticket": "add-search"})
    plans = repo / ".jswarm/plans"
    plans.mkdir(parents=True)
    (plans / "add-search.plan.feature.md").write_text("---\nstatus: ACTIVE\nphase: BUILD\nac_complete: 2/5\n---\n")
    payload = {"cwd": str(repo), "session_id": "one"}
    result = render(payload, {}, 1000)
    assert "add-search" in result and "2/5" in result and "BUILD" in result
    assert "add-search" not in render({**payload, "session_id": "two"}, {}, 1000)


def test_enable_preview_restore_and_drift(home):
    path = home / ".claude/settings.json"
    original = {"statusLine": {"type": "command", "command": "my-old-hud"}, "permissions": {"allow": ["Read"]}}
    write(path, original)
    with pytest.raises(ValueError): configure("enable")
    configure("enable", dry_run=True, replace_existing=True)
    assert json.loads(path.read_text()) == original
    assert not (home / ".jswarm").exists()
    configure("enable", replace_existing=True)
    assert json.loads(path.read_text())["permissions"] == original["permissions"]
    configure("enable")
    altered = json.loads(path.read_text()); altered["another_setting"] = True
    write(path, altered)
    configure("disable", dry_run=True)
    assert json.loads(path.read_text()) == altered
    configure("disable")
    assert json.loads(path.read_text()) == {**original, "another_setting": True}
    configure("enable", replace_existing=True)
    altered["statusLine"] = {"command": "changed"}; write(path, altered)
    with pytest.raises(ValueError): configure("disable")
    assert json.loads(path.read_text()) == altered


def test_real_registered_command_runs_from_unrelated_directory(home):
    configure("enable")
    assert verify()[0]
    actual = json.loads((home / ".claude/settings.json").read_text())["statusLine"]["command"]
    result = subprocess.run(actual, shell=True, cwd=home, env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        input=json.dumps({"model": {"display_name": "Claude"}, "rate_limits": {"five_hour": {"used_percentage": 21}}}),
        text=True, capture_output=True, timeout=5)
    assert result.returncode == 0 and "21% used" in result.stdout and "jSwarm" in result.stdout
    malformed = subprocess.run(actual, shell=True, cwd=home, input="bad json", text=True, capture_output=True, timeout=5)
    assert malformed.returncode == 0 and malformed.stdout == ""


def test_core_uninstall_restores_hud_and_preserves_other_settings(home):
    path = home / ".claude/settings.json"
    write(path, {"other": "keep"})
    configure("enable")
    result = subprocess.run(["bash", "install.sh", "uninstall", "--keep-backups"], cwd=ROOT,
        env={**os.environ, "HOME": str(home), "JSWARM_HOME": str(ROOT)}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(path.read_text()) == {"other": "keep"}
    assert not (home / ".jswarm/hud-install.json").exists()


def test_symlink_settings_cannot_escape_home(home):
    external = home.parent / "other-config"
    external.mkdir(exist_ok=True)
    (home / ".claude").symlink_to(external)
    with pytest.raises(ValueError): configure("enable")
    assert not (external / "settings.json").exists()


def test_interrupted_install_resumes_without_losing_previous_statusline(home):
    path = home / ".claude/settings.json"
    original = {"statusLine": {"command": "original"}, "keep": True}
    write(path, original)
    configure("enable", replace_existing=True)
    write(path, original)  # receipt persisted but config write never reached disk
    configure("enable")
    assert verify()[0]
    configure("disable")
    assert json.loads(path.read_text()) == original
