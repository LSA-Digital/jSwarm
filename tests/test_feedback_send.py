import io
import json
from pathlib import Path

import pytest

from jswarm.feedback import draft
from jswarm import feedback_send as transport


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    path = tmp_path / 'draft.json'
    path.write_text(json.dumps(draft({'summary': 'Synthetic test', 'expected': 'A result', 'actual': 'No result', 'excerpt': 'café 🧪'})))
    return path


def connect():
    transport.exclusive_json(transport.private_path('feedback-auth.json'), {'token': 's' * 48})


def test_review_is_read_only_and_hash_changes_with_content(prepared, capsys, monkeypatch):
    monkeypatch.setattr(transport.urllib.request, 'build_opener', lambda *a: pytest.fail('No network before approval'))
    before = prepared.read_bytes()
    assert transport.main(['review', '--draft', str(prepared)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['destination'] == transport.ENDPOINT
    assert 'café' in result['payload']['report']
    assert prepared.read_bytes() == before
    assert not (prepared.parent / '.jswarm').exists()
    data = json.loads(before); data['report']['actual'] = 'Changed'
    prepared.write_text(json.dumps(data))
    assert transport.review(prepared)[1] != result['sha256']


def test_missing_approval_or_credential_never_sends(prepared, monkeypatch):
    monkeypatch.setattr(transport.urllib.request, 'build_opener', lambda *a: pytest.fail('Do not dispatch'))
    assert transport.main(['send', '--draft', str(prepared)]) == 1
    assert transport.main(['send', '--draft', str(prepared), '--approved-sha', transport.review(prepared)[1]]) == 1


def test_exact_approval_sends_once_and_records_receipt(prepared, monkeypatch, capsys):
    connect()
    payload, digest = transport.review(prepared)
    calls = []
    class Response(io.BytesIO):
        status = 202
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert request.full_url == 'https://www.jarviswarm.com/api/feedback-cli'
            assert request.get_header('Authorization') == 'Bearer ' + 's' * 48
            assert transport.private_path(f'feedback-attempt-{payload["id"]}.json').exists()
            assert json.loads(request.data) == {**payload, 'approved': True, 'sha256': digest}
            return Response(json.dumps({'status': 'accepted', 'receipt': payload['id']}).encode())
    monkeypatch.setattr(transport.urllib.request, 'build_opener', lambda handler: Opener())
    args = ['send', '--draft', str(prepared), '--approved-sha', digest]
    assert transport.main(args) == 0
    assert transport.main(args) == 1
    assert len(calls) == 1
    assert 's' * 48 not in capsys.readouterr().out
    for p in (prepared.parent / '.jswarm').iterdir():
        assert p.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('result', [None, [], {'status': 'accepted', 'receipt': 'wrong'}])
def test_timeout_or_bad_receipt_is_unknown_and_cannot_retry(prepared, monkeypatch, result):
    connect()
    class Response(io.BytesIO):
        status = 202
    class Opener:
        def open(self, request, timeout):
            if result is None:
                raise TimeoutError('do not expose network details')
            return Response(json.dumps(result).encode())
    monkeypatch.setattr(transport.urllib.request, 'build_opener', lambda handler: Opener())
    args = ['send', '--draft', str(prepared), '--approved-sha', transport.review(prepared)[1]]
    assert transport.main(args) == 1
    monkeypatch.setattr(transport.urllib.request, 'build_opener', lambda handler: pytest.fail('No retry'))
    assert transport.main(args) == 1


def test_malformed_extra_fields_and_symlink_credentials_are_rejected(prepared):
    data = json.loads(prepared.read_text()); data['credentials'] = 'never include'
    prepared.write_text(json.dumps(data))
    assert transport.main(['review', '--draft', str(prepared)]) == 1
    root = prepared.parent / '.jswarm'; root.mkdir()
    (root / 'feedback-auth.json').symlink_to(prepared)
    with pytest.raises(ValueError): transport.credential()


def test_credentials_are_not_accepted_when_world_readable(prepared):
    connect()
    path = transport.private_path('feedback-auth.json'); path.chmod(0o644)
    with pytest.raises(ValueError): transport.credential()


def test_redirects_are_never_followed_and_noargs_is_help(capsys):
    assert transport.NoRedirect().redirect_request(None, None, 302, None, None, 'https://another.example') is None
    assert transport.main([]) == 0
    assert 'review' in capsys.readouterr().out


def test_one_time_hidden_connection_and_explicit_disconnect(prepared, monkeypatch, capsys):
    from jswarm import settings
    monkeypatch.setattr(transport.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(transport.getpass, 'getpass', lambda prompt: 's' * 48)
    assert settings.main(['feedback', 'connect']) == 0
    assert not transport.private_path('feedback-auth.json').exists()
    assert settings.main(['feedback', 'connect', '--apply']) == 0
    assert settings.main(['status']) == 0
    assert 's' * 48 not in capsys.readouterr().out
    assert settings.main(['feedback', 'connect', '--apply']) == 1
    assert settings.main(['feedback', 'disconnect']) == 0
    assert transport.private_path('feedback-auth.json').exists()
    assert settings.main(['feedback', 'disconnect', '--apply']) == 0
    assert not transport.private_path('feedback-auth.json').exists()
    assert prepared.exists()
