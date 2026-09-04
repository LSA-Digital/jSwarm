"""PROVENANCE.yaml must stay honest: every recorded target must actually exist,
and the file must keep the shape scripts/upstream-diff.sh (the sync tool)
expects. A sync tool that silently skips stale entries is worse than none,
because it looks like it worked.

Target existence is checked against the git index (``git ls-files``), not
``Path.exists()``: on a case-insensitive filesystem (the macOS default),
``Path("skills/jsetup/SKILL.md").exists()`` returns True even though the
real, tracked file is ``skills/jSetup/SKILL.md`` — a mismatch that would
break on any case-sensitive checkout (Linux CI included) while passing
silently here.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVENANCE_PATH = REPO_ROOT / "PROVENANCE.yaml"
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _load_provenance() -> dict:
    with PROVENANCE_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _tracked_files() -> set[str]:
    """The git index's file set — case-sensitive regardless of filesystem."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return set(out.splitlines())


def test_parses_and_has_the_shape_upstream_diff_sh_expects():
    """Mirrors the exact checks scripts/upstream-diff.sh performs before it
    will trust this file: a 40-hex-char source_commit, and a non-empty list
    of file entries each carrying source/target."""
    data = _load_provenance()
    assert isinstance(data, dict)

    source_commit = data.get("source_commit")
    assert SHA_RE.match(str(source_commit) or ""), (
        f"source_commit must be a 40-char hex commit sha, got {source_commit!r}"
    )

    files = data.get("files")
    assert isinstance(files, list) and files, "files must be a non-empty list"

    for entry in files:
        assert isinstance(entry, dict)
        assert "source" in entry and str(entry["source"]).strip()
        assert "target" in entry and str(entry["target"]).strip()
        # sha256 is carried metadata (upstream-diff.sh does not read it), but
        # every entry in this file has always recorded one — catch a hand-add
        # that forgot it.
        assert "sha256" in entry and str(entry["sha256"]).strip()


def test_every_target_exists_in_this_repo():
    data = _load_provenance()
    tracked = _tracked_files()
    missing = [e["target"] for e in data["files"] if str(e["target"]) not in tracked]
    assert not missing, (
        f"{len(missing)} PROVENANCE.yaml target(s) do not exist in this repo "
        f"(stale — the file moved, was renamed, or was deleted and the entry "
        f"was not updated/removed):\n" + "\n".join(f"  {t}" for t in missing)
    )


def test_no_duplicate_targets():
    data = _load_provenance()
    targets = [str(e["target"]) for e in data["files"]]
    seen: set[str] = set()
    dupes = sorted({t for t in targets if t in seen or seen.add(t)})
    assert not dupes, f"duplicate PROVENANCE.yaml target(s): {dupes}"


def _upstream_common_path() -> Path | None:
    """Best-effort discovery of an upstream ``common`` checkout to diff
    sources against. Not a hard requirement — this repo is meant to be
    usable (and testable) without one, so tests that need it skip cleanly."""
    import os

    override = os.environ.get("JSWARM_UPSTREAM_COMMON")
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None
    sibling = REPO_ROOT.parent / "common"
    return sibling if sibling.is_dir() else None


def test_every_source_exists_upstream():
    """Confirm every recorded source still exists in the upstream common
    checkout. A source that no longer exists there means the file was
    deleted upstream since the copy — exactly what upstream-diff.sh (and
    this test) exist to surface. Skips cleanly when no upstream checkout is
    available (e.g. CI, or a machine without a `common` sibling checkout),
    since that depends on something outside this repo."""
    common = _upstream_common_path()
    if common is None:
        pytest.skip(
            "no upstream `common` checkout found (set JSWARM_UPSTREAM_COMMON "
            "or place one at ../common relative to this repo) — skipping the "
            "source-exists check, which depends on it"
        )

    data = _load_provenance()
    missing = [e["source"] for e in data["files"] if not (common / str(e["source"])).exists()]
    assert not missing, (
        f"{len(missing)} PROVENANCE.yaml source(s) no longer exist in {common} "
        f"(deleted upstream since the copy):\n" + "\n".join(f"  {s}" for s in missing)
    )
