from pathlib import Path

import pytest

from jswarm import release_notes


def notes():
    return '## v1.1.0 candidate (not released)\n\n' + '\n'.join(
        f'### {name}\n\n- None.\n' for name in release_notes.CATEGORIES)


def test_current_changelog_has_required_categories():
    release_notes.validate(Path('CHANGELOG.md').read_text())


def test_missing_or_empty_category_is_rejected():
    with pytest.raises(ValueError, match='Fixed'):
        release_notes.validate(notes().replace('### Fixed\n\n- None.', '### Fixed\n'))


def test_only_current_entry_must_follow_new_format():
    release_notes.validate(notes() + '\n## v1.0.0\nLegacy notes.\n')


@pytest.mark.parametrize('path', ['jswarm/upgrade.py', 'skills/jGo/SKILL.md', 'install.sh', 'portal/src/page.astro', 'requirements.txt', 'hooks/test.sh', 'scripts/upstream-diff.sh'])
def test_product_changes_require_notes(path):
    with pytest.raises(ValueError, match='CHANGELOG.md'):
        release_notes.validate_changed_paths([path])
    release_notes.validate_changed_paths([path, 'CHANGELOG.md'])


def test_test_only_changes_do_not_require_release_bullet():
    release_notes.validate_changed_paths(['tests/test_upgrade_workflow.py', 'docs/releasing.md'])
