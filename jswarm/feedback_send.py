"""Review an exact feedback draft, then send only with explicit payload approval."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
import uuid

from jswarm.feedback import FIELDS, MAX_BYTES, FORM_URL

ENDPOINT = 'https://www.jarviswarm.com/api/feedback-cli'
TOKEN_PATTERN = r'[A-Za-z0-9_-]{43,128}'


def private_path(name):
    home = Path.home().resolve()
    root = Path.home() / '.jswarm'
    path = root / name
    if root.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(home):
        raise ValueError('Feedback state must stay inside your home without symlinks.')
    return path


def exclusive_json(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())


def credential():
    path = private_path('feedback-auth.json')
    if not path.exists():
        raise ValueError('Direct sending is not connected. Use /jSettings to connect an LSA-issued credential, or keep the browser fallback.')
    if path.stat().st_mode & 0o077 or path.stat().st_size > 2048:
        raise ValueError('Feedback credential permissions or size are unsafe. Inspect the owner-only credential file.')
    data = json.loads(path.read_text(encoding='utf-8'))
    value = data.get('token') if isinstance(data, dict) else None
    if not isinstance(value, str) or not re.fullmatch(TOKEN_PATTERN, value):
        raise ValueError('Feedback credential is invalid. Reconnect using an LSA-issued credential.')
    return value


def connect():
    if not sys.stdin.isatty():
        raise ValueError('Connect once in an interactive Terminal. Never paste the credential into chat or command arguments.')
    path = private_path('feedback-auth.json')
    if path.exists():
        raise ValueError('A credential already exists. Disconnect it explicitly before replacing it.')
    value = getpass.getpass('LSA feedback credential (hidden): ').strip()
    if not re.fullmatch(TOKEN_PATTERN, value):
        raise ValueError('Use the scoped credential issued by LSA, not an email-provider or AI-provider key.')
    exclusive_json(path, {'token': value})
    print('Credential saved privately. It will be checked by LSA on submission; nothing sent.')


def review(path):
    if path.stat().st_size > 30000:
        raise ValueError('Choose a jFeedback draft under 30 KB.')
    data = json.loads(path.read_text(encoding='utf-8'))
    env_keys = ('commit', 'os', 'architecture', 'python')
    if not isinstance(data, dict) or set(data) != {'schema', 'id', 'environment', 'report'} or data['schema'] != 'jswarm.feedback.v1':
        raise ValueError('Choose the draft created by /jFeedback, not a transcript or source file.')
    if not isinstance(data['id'], str) or str(uuid.UUID(data['id'])) != data['id']:
        raise ValueError('Invalid report ID.')
    if not isinstance(data['report'], dict) or set(data['report']) != set(FIELDS) or not isinstance(data['environment'], dict) or set(data['environment']) != set(env_keys):
        raise ValueError('Unexpected report fields. Prepare a new draft.')
    if any(not isinstance(v, str) for v in [*data['report'].values(), *data['environment'].values()]):
        raise ValueError('Report fields must be text.')
    if any(not data['report'][k].strip() for k in ('summary', 'expected', 'actual')):
        raise ValueError('Summary, expected result, and actual result are required.')
    report = '\n\n'.join([f'{k.upper()}\n{data["report"][k]}' for k in FIELDS if data['report'][k]] +
                         ['ENVIRONMENT\n' + '\n'.join(f'{k}: {data["environment"][k]}' for k in env_keys)])
    if len(report.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Shorten the report to under 24 KB before sending.')
    payload = {'id': data['id'], 'report': report}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    return payload, digest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(path, approved_sha):
    payload, digest = review(path)
    if not approved_sha or approved_sha != digest:
        raise ValueError('The report is not approved or changed since review. Show it again and ask before sending.')
    secret = credential()
    attempt = private_path(f'feedback-attempt-{payload["id"]}.json')
    receipt = private_path(f'feedback-receipt-{payload["id"]}.json')
    if attempt.exists() or receipt.exists():
        raise ValueError('This report already has a send attempt. Keep its ID and check with LSA; do not send it again or create a new ID to retry.')
    request = urllib.request.Request(ENDPOINT, method='POST',
        data=json.dumps({**payload, 'approved': True, 'sha256': digest}, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': f'Bearer {secret}', 'Content-Type': 'application/json'})
    # Persist before dispatch. A crash/timeout must never turn into an automatic retry.
    exclusive_json(attempt, {'id': payload['id'], 'sha256': digest, 'status': 'attempted'})
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=20) as response:
            raw = response.read(8193)
            if len(raw) > 8192 or response.status != 202:
                raise ValueError('Invalid receipt')
            result = json.loads(raw)
        if not isinstance(result, dict) or result.get('status') != 'accepted' or result.get('receipt') != payload['id']:
            raise ValueError('Invalid receipt')
        exclusive_json(receipt, {'id': payload['id'], 'sha256': digest, 'status': 'accepted'})
        print(f'Email provider accepted report {payload["id"]} for LSA. This is not confirmation of inbox delivery or human review.')
        return 0
    except (OSError, ValueError, urllib.error.URLError):
        print(f'Acceptance could not be confirmed for {payload["id"]}. Keep the draft and this ID; check with LSA before retrying. Nothing will retry automatically.', file=sys.stderr)
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['review', 'send'], nargs='?')
    parser.add_argument('--draft', type=Path)
    parser.add_argument('--approved-sha')
    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 0
    try:
        if not args.draft:
            raise ValueError('Choose the local draft with --draft.')
        if args.action == 'review':
            payload, digest = review(args.draft)
            print(json.dumps({'destination': ENDPOINT, 'payload': payload, 'sha256': digest,
                              'status': 'Not sent. Approve this exact report before sending.'}, indent=2))
            return 0
        return send(args.draft, args.approved_sha)
    except (OSError, ValueError) as exc:
        # Do not echo JSON parser excerpts, reports, or secrets.
        message = str(exc) if type(exc) is ValueError else 'Check the draft and private state files; nothing else was attempted.'
        print(f'Feedback stopped: {message}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
