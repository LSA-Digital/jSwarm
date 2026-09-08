"""Validate the changelog and require notes alongside product changes in CI."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

CATEGORIES = ('Added', 'Improved', 'Fixed', 'Breaking changes', 'Upgrade steps',
              'Known limitations', 'Verification')
PRODUCT_DIRS = ('jswarm/', 'skills/', 'hooks/', 'portal/', 'schemas/', 'templates/', 'scripts/')
PRODUCT_FILES = {'install.sh', 'requirements.txt'}


def validate(markdown: str):
    entries = re.split(r'^## ', markdown, flags=re.MULTILINE)
    if len(entries) < 2 or not re.match(r'v\d+\.\d+\.\d+\b', entries[1]):
        raise ValueError('CHANGELOG.md needs a versioned first entry.')
    sections = dict(re.findall(r'^### ([^\n]+)\n(.*?)(?=^### |\Z)',
                               entries[1], flags=re.MULTILINE | re.DOTALL))
    for category in CATEGORIES:
        if not sections.get(category, '').strip():
            raise ValueError(f'CHANGELOG.md needs a nonempty {category} section. Use None if applicable.')


def validate_changed_paths(paths):
    if 'CHANGELOG.md' not in paths and any(
        path.startswith(PRODUCT_DIRS) or path in PRODUCT_FILES for path in paths
    ):
        raise ValueError('Product changes require an entry in CHANGELOG.md in the same change set.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default=os.environ.get('NOTES_BASE', ''),
                        help='Base commit for the change set; omit to validate format only')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    try:
        validate((root / 'CHANGELOG.md').read_text(encoding='utf-8'))
        if args.base and args.base != '0' * 40:
            if not re.fullmatch(r'[a-f0-9]{40}', args.base):
                raise ValueError('Release-note base must be a full commit hash.')
            result = subprocess.run(['git', '-C', str(root), 'diff', '--name-only',
                                     args.base, 'HEAD'], check=True, capture_output=True,
                                    text=True, timeout=30)
            validate_changed_paths(result.stdout.splitlines())
        print('Release-note checks passed. Human review must still confirm accuracy and completeness.')
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'Release-note check failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
