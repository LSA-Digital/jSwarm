"""R6a follow-up: `precompact_reconcile.matrices`/`.rows` do
`from markdown_tables import normalize_path` -- markdown_tables.py lives
under `scripts/test-catalog/` upstream, which is enterprise-only and not
part of this repo. Both modules must still import cleanly and their
`_normalize_path` fallback must behave identically to the real function
(a trivial, generic cell-value cleanup with no test-catalog-specific
behavior), not degrade to a stub.
"""

from __future__ import annotations

from jswarm.precompact_reconcile import matrices, rows


def test_matrices_normalize_path_strips_backticks_and_leading_dot_segment():
    assert matrices._normalize_path("./foo/bar.py") == "foo/bar.py"
    assert matrices._normalize_path("`baz.py`") == "baz.py"
    assert matrices._normalize_path("  ./a/./b  ") == "a/./b"


def test_rows_normalize_path_strips_backticks_and_leading_dot_segment():
    assert rows._normalize_path("./foo/bar.py") == "foo/bar.py"
    assert rows._normalize_path("`baz.py`") == "baz.py"


def test_matrices_and_rows_agree_with_each_other():
    for value in ("./x/y.md", "`z.md`", "plain.md", "./././nested.md"):
        assert matrices._normalize_path(value) == rows._normalize_path(value)
