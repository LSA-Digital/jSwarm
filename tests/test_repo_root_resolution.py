"""Regression guard for task-A8A7: a variable named for the repository root
must actually resolve to it.

`jswarm/host/deploy/topology_policy.py`, `jswarm/host/deploy/deploy.py`
(inside `run_runtime_smoke`), and `jswarm/host/deploy/runtime_smoke_runner.py`
all live three directories below the repository root
(`jswarm/host/deploy/<file>.py`), but each computed its `REPO_ROOT` /
`repo_root` as `Path(__file__).resolve().parents[2]` -- which lands on
`jswarm/`, the package directory, not the repository root above it. Those
values were then used to build `.jswarm` and `.claude` paths, so files meant
for the repository root (or an adopted project) silently landed one
directory too deep.

This test statically evaluates every simple assignment to a variable named
(with optional leading/trailing underscores) `REPO_ROOT`, case-insensitively,
across the whole repository, using real `pathlib.Path` semantics, and fails
if any of them would resolve to anywhere other than the actual repository
root. It deliberately does not special-case the three sites above -- it
would catch the same bug reintroduced anywhere.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Sanity-check that REPO_ROOT above is really the repository root and this
# test isn't quietly validating against the wrong baseline.
assert (REPO_ROOT / "jswarm").is_dir(), REPO_ROOT
assert (REPO_ROOT / "install.sh").is_file(), REPO_ROOT
assert (REPO_ROOT / ".git").exists(), REPO_ROOT

_EXCLUDE_DIR_NAMES = {".venv", ".git", "__pycache__", "node_modules", ".pytest_cache"}

_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")
_REPO_ROOT_NAME_RE = re.compile(r"^_*repo_root_*$", re.IGNORECASE)

_SAFE_BUILTINS = {"str": str, "Path": Path}


def _safe_eval(expr: str, env: dict):
    """Evaluate a simple pathlib expression with only `str`/`Path` available
    (no other builtins), so this can only ever construct/inspect Path
    objects -- never do anything with real side effects.
    """
    return eval(expr, {"__builtins__": _SAFE_BUILTINS}, env)  # noqa: S307 - restricted env, test-only


def _iter_py_files():
    for p in sorted(REPO_ROOT.rglob("*.py")):
        if any(part in _EXCLUDE_DIR_NAMES for part in p.parts):
            continue
        yield p


def test_every_repo_root_named_variable_resolves_to_the_real_repo_root():
    violations = []
    for f in _iter_py_files():
        text = f.read_text(encoding="utf-8")
        if "REPO_ROOT" not in text.upper():
            continue

        file_resolved = f.resolve()
        env = {"__file__": str(file_resolved), "Path": Path}

        for lineno, line in enumerate(text.splitlines(), start=1):
            m = _ASSIGN_RE.match(line)
            if not m:
                continue
            name, expr = m.groups()
            try:
                value = _safe_eval(expr, env)
            except Exception:
                continue  # not a (fully) static path expression -- skip
            env[name] = value  # keep chained assignments (e.g. _HERE -> REPO_ROOT) resolvable

            if not _REPO_ROOT_NAME_RE.match(name):
                continue

            target = value if isinstance(value, Path) else Path(str(value))
            try:
                target = target.resolve()
            except Exception:
                continue
            if target != REPO_ROOT:
                rel = f.relative_to(REPO_ROOT)
                violations.append(
                    f"{rel}:{lineno}: `{name} = {expr}` resolves to {target}, not the "
                    f"repository root ({REPO_ROOT}). A variable named for the repo root "
                    f"that actually points somewhere else (e.g. the jswarm/ package "
                    f"directory) silently misplaces every path built from it."
                )

    assert violations == [], "\n" + "\n".join(violations)
