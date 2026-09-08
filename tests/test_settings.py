import json
import os
import subprocess
from pathlib import Path

import pytest

from jswarm import settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('JSWARM_HOME', str(Path(__file__).resolve().parents[1]))
    return tmp_path


def test_default_status_and_preview_are_read_only(home):
    assert settings.main([]) == 0
    assert settings.main(['hud', 'enable']) == 0
    assert list(home.iterdir()) == []


def test_apply_requires_exact_unchanged_preview(home):
    assert settings.main(['hud', 'enable', '--apply']) == 1
    digest = settings.hud_digest('enable', False)
    config = home / '.claude/settings.json'
    config.parent.mkdir()
    config.write_text('{"other":true}')
    assert settings.main(['hud', 'enable', '--apply', '--approved-sha', digest]) == 1
    digest = settings.hud_digest('enable', False)
    assert settings.main(['hud', 'enable', '--apply', '--approved-sha', digest]) == 0
    assert json.loads(config.read_text())['other'] is True
    digest = settings.hud_digest('disable', False)
    assert settings.main(['hud', 'disable', '--apply', '--approved-sha', digest]) == 0
    assert json.loads(config.read_text()) == {'other': True}


def test_replacement_needs_its_own_preview(home):
    config = home / '.claude/settings.json'
    config.parent.mkdir()
    config.write_text('{"statusLine":{"command":"original"}}')
    assert settings.main(['hud', 'enable']) == 1
    digest = settings.hud_digest('enable', False)
    assert settings.main(['hud', 'enable', '--replace-existing', '--apply', '--approved-sha', digest]) == 1
    digest = settings.hud_digest('enable', True)
    assert settings.main(['hud', 'enable', '--replace-existing', '--apply', '--approved-sha', digest]) == 0
    digest = settings.hud_digest('disable', False)
    assert settings.main(['hud', 'disable', '--apply', '--approved-sha', digest]) == 0
    assert json.loads(config.read_text())['statusLine']['command'] == 'original'


def test_real_settings_cli_from_application_folder_keeps_decoy_config_untouched(home):
    source = Path(__file__).resolve().parents[1]
    application = home / 'application'; application.mkdir()
    decoy = home / 'decoy'; decoy.mkdir()
    marker = decoy / 'settings.json'; marker.write_text('{"keep":true}')
    env = {**os.environ, 'HOME': str(home), 'PYTHONPATH': str(source),
           'JSWARM_HOME': str(source), 'CLAUDE_CONFIG_DIR': str(decoy), 'PYTHONDONTWRITEBYTECODE': '1'}
    cmd = [str(source / '.venv/bin/python'), '-m', 'jswarm.settings', 'hud', 'enable']
    preview = subprocess.run(cmd, cwd=application, env=env, capture_output=True, text=True, timeout=10)
    assert preview.returncode == 0
    digest = preview.stdout.split('Preview SHA-256: ')[1].splitlines()[0]
    applied = subprocess.run(cmd + ['--apply', '--approved-sha', digest], cwd=application, env=env,
                             capture_output=True, text=True, timeout=10)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert 'rendered a test payload' in applied.stdout
    assert marker.read_text() == '{"keep":true}'
    assert list(application.iterdir()) == []
