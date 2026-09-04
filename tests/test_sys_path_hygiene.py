"""Regression guard for task-A5A2A3 (Defect 2): a module must never put
jswarm/'s own directory at the front of sys.path.

`jswarm/plan_status/cli.py` (and several siblings) used to do
`sys.path.insert(0, str(_HERE.parent))` where `_HERE` was the module's own
directory one level under `jswarm/` -- so `_HERE.parent` resolved to
`jswarm/` itself. Putting `jswarm/` at position 0 shadows the stdlib for
anything under `jswarm/` that happens to share a name with a stdlib module
-- concretely, `jswarm/platform/` versus the stdlib `platform` module,
reproduced as `AttributeError: module 'platform' has no attribute
'python_implementation'` deep inside `attrs`.

These modules must keep working both as `import jswarm.pkg.mod` and when
invoked directly by path (the lifecycle skills do the latter), which is
exactly why they insert something onto sys.path at all. The fix is to
insert the *repository root* (jswarm/'s parent), never jswarm/ itself.

This test statically evaluates every `sys.path.insert(...)` call under
jswarm/ (and the simple local assignments that feed into it, e.g.
`_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1])`) using real
`pathlib.Path` semantics, and fails if any of them would resolve to the
jswarm/ package directory itself.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
JSWARM_DIR = (REPO_ROOT / "jswarm").resolve()

_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")
_INSERT_CALL_RE = re.compile(r"sys\.path\.insert\(")


def _extract_insert_arg(line: str) -> str | None:
    """Pull the second argument out of a single-line `sys.path.insert(pos, arg)`
    call, respecting paren nesting (the arg itself is often another call, e.g.
    `str(Path(__file__).resolve().parents[1])`, so a non-greedy regex up to the
    first `)` would truncate it).
    """
    m = _INSERT_CALL_RE.search(line)
    if not m:
        return None
    start = m.end()  # just after the opening "("
    depth = 1
    i = start
    while i < len(line) and depth > 0:
        if line[i] == "(":
            depth += 1
        elif line[i] == ")":
            depth -= 1
        i += 1
    if depth != 0:
        return None  # call spans multiple lines -- not this test's job to chase
    call_args = line[start : i - 1]  # inside the outer parens, comma-separated
    # Split off the first (position) argument at its top-level comma.
    depth = 0
    for j, ch in enumerate(call_args):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            return call_args[j + 1 :].strip()
    return None


def _iter_py_files():
    return sorted(p for p in (REPO_ROOT / "jswarm").rglob("*.py") if "__pycache__" not in p.parts)


_SAFE_BUILTINS = {"str": str, "Path": Path}


def _safe_eval(expr: str, env: dict):
    """Evaluate a simple pathlib expression with only `str`/`Path` available
    (no other builtins), so this can only ever construct/inspect Path
    objects -- never do anything with real side effects.
    """
    return eval(expr, {"__builtins__": _SAFE_BUILTINS}, env)  # noqa: S307 - restricted env, test-only


def test_no_module_inserts_jswarms_own_directory_onto_sys_path():
    violations = []
    for f in _iter_py_files():
        text = f.read_text(encoding="utf-8")
        if "sys.path.insert" not in text:
            continue

        file_resolved = f.resolve()
        env = {"__file__": str(file_resolved), "Path": Path}

        for lineno, line in enumerate(text.splitlines(), start=1):
            m = _ASSIGN_RE.match(line)
            if m:
                name, expr = m.groups()
                try:
                    env[name] = _safe_eval(expr, env)
                except Exception:
                    pass  # not a path expression (or references something we don't track) -- skip

            expr = _extract_insert_arg(line)
            if expr is None:
                continue
            try:
                value = _safe_eval(expr, env)
            except Exception:
                continue  # can't statically resolve this one -- not this test's job to flag it
            target = value if isinstance(value, Path) else Path(value)
            try:
                target = target.resolve()
            except Exception:
                continue
            if target == JSWARM_DIR:
                rel = f.relative_to(REPO_ROOT)
                violations.append(
                    f"{rel}:{lineno}: sys.path.insert(..., {expr!r}) resolves to jswarm/ itself "
                    f"-- this shadows the stdlib for anything under jswarm/ with a colliding name "
                    f"(e.g. jswarm/platform/ vs the stdlib platform module). Insert the repository "
                    f"root instead."
                )

    assert violations == [], "\n" + "\n".join(violations)
