"""End to end coverage for the pattern catalog `jswarm.patterns.recommend` /
`jswarm.patterns.ceremony_selector` read at runtime from
`docs/_JarviSWARM/patterns/PAT-*.pattern.yaml`.

Before this test existed, nothing in the suite ever loaded that catalog.
It is data read from disk at runtime rather than a Python import, so the
directory being entirely absent from the public repo (the closure gap that
split the pattern catalog out of `common` without carrying it across) was
invisible to every other test: `pytest` was green while
`jswarm/patterns/ceremony_selector.py render ...` -- the exact command
`skills/jPlan.ceremony-selector/SKILL.md` tells a user to run -- crashed with
an unhandled `KeyError`. See docs/superpowers or the clean-mac-verify report
for the original repro.

This file has two jobs:

1. Prove the ceremony selector genuinely works end to end against the real,
   shipped catalog (not a mock, not a stub) -- render, explain, and apply,
   run as the actual subprocess a user or a skill would invoke.
2. Prove a missing or partial catalog fails with a clear, named message
   (`PatternCatalogError`) instead of an unhandled `KeyError`/traceback, at
   both the library level and the CLI level, so this class of failure can
   never again go unnoticed the way the original gap did.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
CEREMONY_SELECTOR = REPO_ROOT / "jswarm" / "patterns" / "ceremony_selector.py"
PATTERN_DIR = REPO_ROOT / "docs" / "_JarviSWARM" / "patterns"

ALL_LOW_SIGNALS = (
    "scope_blast_radius=low,user_visible_behavior=low,shared_contract_surface=low,"
    "security_compliance_external_write=low,reversibility_migration_risk=low,"
    "novelty_architecture_uncertainty=low,concurrency_shared_files=low"
)
ALL_LOW_SIGNALS_JSON = json.dumps(
    {
        "scope_blast_radius": "low",
        "user_visible_behavior": "low",
        "shared_contract_surface": "low",
        "security_compliance_external_write": "low",
        "reversibility_migration_risk": "low",
        "novelty_architecture_uncertainty": "low",
        "concurrency_shared_files": "low",
    }
)

sys.path.insert(0, str(REPO_ROOT))

import jswarm.patterns.recommend as recommend  # noqa: E402


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), str(CEREMONY_SELECTOR), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------- #
# 1. The catalog actually ships and is complete.
# --------------------------------------------------------------------------- #

def test_pattern_catalog_directory_ships_in_this_repo():
    """The blocking gap: docs/_JarviSWARM/patterns/ must exist in this repo,
    not just in the private upstream monorepo."""
    assert PATTERN_DIR.is_dir(), f"{PATTERN_DIR} is missing; the ceremony selector cannot load presets"
    assert sorted(PATTERN_DIR.glob("PAT-*.pattern.yaml")), "no PAT-*.pattern.yaml records shipped"


def test_shipped_catalog_supplies_all_three_l1_presets():
    catalog = recommend._load_catalog()
    for preset_key in recommend.PRESET_KEYS.values():
        assert preset_key in catalog, f"shipped catalog is missing required preset {preset_key!r}"


def test_shipped_catalog_closes_over_every_included_l2_ingredient():
    """Every L2 ingredient key referenced by an L1 preset's composition.includes
    must itself resolve in the catalog. This is exactly the closure the original
    split missed for the whole directory; pin it at the ingredient level too so a
    future partial copy (some PAT-0NN files but not others) fails a test instead
    of silently rendering a blank ingredient name."""
    catalog = recommend._load_catalog()
    missing: list[str] = []
    for preset_key in recommend.PRESET_KEYS.values():
        preset = catalog[preset_key]
        includes = preset.get("composition", {}).get("includes", [])
        for include in includes:
            key = include.get("key")
            if key not in catalog:
                missing.append(f"{preset_key} -> {key}")
    assert not missing, f"catalog closure gap(s): {missing}"


# --------------------------------------------------------------------------- #
# 2. The ceremony selector genuinely works end to end (the documented repro).
# --------------------------------------------------------------------------- #

def test_ceremony_selector_render_works_against_shipped_catalog():
    """This is exactly the command skills/jPlan.ceremony-selector/SKILL.md
    tells a user to run. It must not crash and must recommend Low for an
    all-low signal read."""
    result = _run(
        "render",
        "--signals", ALL_LOW_SIGNALS,
        "--no-tty",
    )
    assert result.returncode == 0, result.stderr
    assert "PATTERN CARD OPTIONS" in result.stdout
    assert "Story Low Essential Preset" in result.stdout
    assert "Story Medium Standard Preset" in result.stdout
    assert "Story High Assurance Preset" in result.stdout
    assert "[L] LOW ✓ recommended" in result.stdout


def test_ceremony_selector_explain_resolves_real_ingredient_names():
    """explain must render real L2 ingredient names pulled from the catalog,
    not bare keys -- proof the composition.includes closure actually resolves."""
    result = _run(
        "--signals", ALL_LOW_SIGNALS_JSON,
        "--no-tty",
        "--beat", "explain",
        "--tier", "medium",
    )
    assert result.returncode == 0, result.stderr
    assert "l2.tdd-loop: TDD Red-Green-Refactor Loop" in result.stdout
    assert "l2.acceptance-core: Acceptance Core and Scope Boundaries" in result.stdout


def test_ceremony_selector_apply_works_against_shipped_catalog():
    result = _run(
        "apply",
        "--signals", ALL_LOW_SIGNALS,
        "--tier", "medium",
        "--jarvi-tier", "medium",
        "--situational-rationale", "Standard bounded story work for this test.",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["selected_ceremony_tier"] == "medium"
    assert payload["engine_recommended_tier"] == "low"


def test_ceremony_selector_high_tier_end_to_end():
    """A hard-High signal read must resolve High through the real catalog too."""
    high_signals = ALL_LOW_SIGNALS.replace(
        "security_compliance_external_write=low", "security_compliance_external_write=high"
    )
    result = _run("render", "--signals", high_signals, "--no-tty")
    assert result.returncode == 0, result.stderr
    assert "[H] HIGH ✓ recommended" in result.stdout


# --------------------------------------------------------------------------- #
# 3. A missing or partial catalog fails with a clear message, not a KeyError.
# --------------------------------------------------------------------------- #

def test_missing_catalog_directory_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(recommend, "PATTERN_DIR", tmp_path / "nonexistent")
    with pytest.raises(recommend.PatternCatalogError) as exc_info:
        recommend.recommend({"scope_blast_radius": "low"})
    message = str(exc_info.value)
    assert "pattern catalog directory not found" in message
    assert "l1.story.low-essential" in message


def test_partial_catalog_names_the_missing_preset(monkeypatch, tmp_path):
    """A catalog directory that exists but is short one required preset must
    name exactly which preset is missing, not raise KeyError."""
    fake_dir = tmp_path / "patterns"
    fake_dir.mkdir()
    for path in sorted(PATTERN_DIR.glob("PAT-*.pattern.yaml")):
        if "story-high-assurance" in path.name:
            continue
        (fake_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(recommend, "PATTERN_DIR", fake_dir)

    with pytest.raises(recommend.PatternCatalogError) as exc_info:
        recommend.recommend({"scope_blast_radius": "low"})
    message = str(exc_info.value)
    assert "l1.story.high-assurance" in message
    assert "missing required preset" in message


def test_missing_catalog_fails_cleanly_at_the_cli_not_as_a_traceback():
    """Reproduces the original finding directly: run the real CLI with the
    pattern directory temporarily absent and confirm the failure is a clear,
    single-line stderr message with a non-zero exit, never a Python traceback.

    Renames the real directory aside and back within the same parent (same
    filesystem, so the rename is atomic) rather than through pytest's tmp_path,
    which may live on a different filesystem and make the restore itself
    unreliable.
    """
    real_dir = PATTERN_DIR
    assert real_dir.is_dir(), f"{real_dir} must exist before this test can safely move it aside"
    moved = real_dir.parent / "patterns.test-moved-aside"
    assert not moved.exists(), f"{moved} already exists; refusing to clobber it"
    real_dir.rename(moved)
    try:
        result = _run("render", "--signals", ALL_LOW_SIGNALS, "--no-tty")
    finally:
        moved.rename(real_dir)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "KeyError" not in result.stderr
    assert "error:" in result.stderr
    assert "pattern catalog directory not found" in result.stderr
