"""Everyday jSwarm settings. Read-only by default; changes require preview approval."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from jswarm.installer import hud
from jswarm import feedback_send
from jswarm.paths import jswarm_home


def hud_digest(action, replace_existing):
    files = hud.locations(Path.home())
    snapshot = {'action': action, 'replace_existing': replace_existing,
                'command': hud.command(jswarm_home()),
                'files': [hud.load(path) for path in files]}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='section')
    sub.add_parser('status')
    p = sub.add_parser('hud')
    p.add_argument('action', choices=['status', 'enable', 'disable'], nargs='?', default='status')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--approved-sha')
    p.add_argument('--replace-existing', action='store_true')
    p = sub.add_parser('feedback')
    p.add_argument('action', choices=['status', 'connect', 'disconnect'], nargs='?', default='status')
    p.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.section in (None, 'status'):
            hud.configure('status')
            try:
                feedback_send.credential()
                print('Feedback: credential saved locally; server authorization is checked on submission.')
            except ValueError:
                print('Feedback: direct sending not connected or credential needs attention. Local drafts and browser fallback remain available.')
            print('Updates: /jUpgrade. HUD: enable or disable here after preview. No settings changed.')
            return 0
        if args.section == 'feedback':
            if args.action == 'status':
                return main(['status'])
            if not args.apply:
                print('Preview: save an LSA-issued credential privately (hidden Terminal prompt).' if args.action == 'connect' else
                      'Preview: remove this machine\'s saved feedback credential. This does not revoke it at LSA or delete reports.')
                print('Approve before running with --apply. Nothing changed.')
                return 0
            if args.action == 'connect':
                feedback_send.connect()
            else:
                feedback_send.private_path('feedback-auth.json').unlink(missing_ok=True)
                print('Local feedback credential removed. Drafts and send records retained.')
            return 0
        if args.action == 'status':
            return hud.configure('status')
        digest = hud_digest(args.action, args.replace_existing)
        if args.apply:
            if args.approved_sha != digest:
                raise ValueError('Settings changed since preview or approval is missing. Preview again and ask before applying.')
            hud.configure(args.action, replace_existing=args.replace_existing)
            if args.action == 'enable':
                ready, detail = hud.verify()
                print(detail)
                if not ready:
                    print('HUD configuration changed but verification failed. Preserve the output; do not claim it is working.')
                    return 1
            else:
                hud.configure('status')
            print('Restart Claude Code in your application folder. No project was re-adopted.')
        else:
            hud.configure(args.action, dry_run=True, replace_existing=args.replace_existing)
            print(f'Preview SHA-256: {digest}')
            print('Ask before applying this exact preview. No settings changed.')
        return 0
    except (OSError, ValueError) as exc:
        detail = str(exc) if type(exc) is ValueError else 'Inspect the saved configuration or credential files; no automatic recovery was attempted.'
        print(f'Settings stopped: {detail}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
