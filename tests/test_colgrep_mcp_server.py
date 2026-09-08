"""The ColGREP MCP server's two tools, exercised against the real `colgrep`
CLI on a small scratch git checkout this module builds -- not mocks. Per the
public product contract (docs/superpowers/specs/2026-09-03-jswarm-public-repo-
split-design.md, D7), this server wraps `colgrep search` and `colgrep status`
and nothing else; there is no content-search backend to exercise here.

`colgrep` (`cargo install colgrep`, crates.io) is not something CI installs
(that would mean a real Rust build and a real model download on every run),
so every test here is skipped -- loudly, with a reason, never silently
mocked into a pass -- when the binary is not resolvable via `COLGREP_BIN` or
`PATH`. Locally, where the binary is present, they run for real: a real
subprocess builds a real ColBERT index over real files and returns real
results, which is what is asserted on.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jswarm import colgrep_mcp_server as srv  # noqa: E402

_BINARY = srv._colgrep_binary()
pytestmark = pytest.mark.skipif(
    _BINARY is None,
    reason="colgrep CLI not found ($COLGREP_BIN / PATH) -- cannot exercise the real binary; "
    "install it with `cargo install colgrep` to run these tests",
)


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory):
    """A tiny, real git checkout with semantically distinct functions, indexed
    once (indexing is real work against a real model) and reused read-only by
    every test in this module.
    """
    root = tmp_path_factory.mktemp("colgrep-mcp-checkout")
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "colgrep-mcp-test", cwd=root)  # not email-shaped: keeps leakgate quiet
    _git("config", "user.name", "Test", cwd=root)

    (root / "auth.py").write_text(
        "def authenticate_user(username, password):\n"
        '    """Check credentials against the user database and return a session token."""\n'
        "    if not username or not password:\n"
        '        raise ValueError("missing credentials")\n'
        "    return generate_token(username)\n"
        "\n"
        "def generate_token(username):\n"
        '    """Create a signed session token for the given username."""\n'
        '    return f"token-for-{username}"\n',
        encoding="utf-8",
    )
    (root / "math_utils.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    empty_dir = root / "empty"
    empty_dir.mkdir()
    (empty_dir / ".gitkeep").write_text("", encoding="utf-8")

    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)

    # Build the index once, up front, so every test below reuses it instead of
    # each triggering (and waiting on) its own first-search build.
    first = json.loads(srv.colgrep_search("authenticate a user with a password", str(root)))
    assert first["ok"] is True, first
    return root


# --------------------------------------------------------------- colgrep_search
def test_search_finds_the_semantically_relevant_function(checkout):
    raw = srv.colgrep_search("authenticate a user with a password", str(checkout), top_k=5)
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["results"], payload
    names = [r["name"] for r in payload["results"]]
    assert "authenticate_user" in names, f"expected the auth function among top results, got: {names}"
    top = payload["results"][0]
    for key in ("name", "qualified_name", "file", "line", "end_line", "language", "unit_type", "signature", "code", "score"):
        assert key in top, f"missing {key!r} in a result: {top}"
    assert top["file"].endswith("auth.py") or "auth.py" in top["file"]


def test_installer_module_launch_serves_a_real_indexed_search(checkout):
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from jswarm.installer.colgrep import desired_registration

    async def search():
        config = desired_registration(REPO_ROOT, _BINARY)
        params = StdioServerParameters(command=config["command"], args=config["args"],
                                       env=config["env"], cwd=checkout)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("colgrep_search", {
                    "query": "authenticate a user with a password", "cwd": str(checkout), "top_k": 5,
                })
                assert not result.isError
                payload = json.loads(next(block.text for block in result.content if block.type == "text"))
                assert payload["ok"], payload
                assert "authenticate_user" in [hit["name"] for hit in payload["results"]]

    asyncio.run(asyncio.wait_for(search(), 90))


def test_search_path_scopes_to_a_subtree(checkout):
    raw = srv.colgrep_search("anything at all", str(checkout), path="empty", top_k=5)
    payload = json.loads(raw)
    assert payload == {
        "ok": True,
        "status": "empty",
        "message": f"no results for 'anything at all' in {checkout / 'empty'}",
        "results": [],
    }


def test_search_path_outside_cwd_is_refused(checkout, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    raw = srv.colgrep_search("anything", str(checkout), path=str(outside), top_k=5)
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["status"] == "path-outside-cwd"


def test_search_missing_cwd_is_an_error_not_a_traceback():
    raw = srv.colgrep_search("anything", "/definitely/does/not/exist/xyz")
    payload = json.loads(raw)
    assert payload == {
        "ok": False,
        "status": "cwd-missing",
        "message": "invalid search scope (cwd='/definitely/does/not/exist/xyz', path=None)",
        "results": [],
    }


def test_search_empty_query_is_rejected_cleanly(checkout):
    payload = json.loads(srv.colgrep_search("", str(checkout)))
    assert payload["ok"] is False
    assert payload["status"] == "query-required"


def test_search_missing_binary_is_reported_not_raised(checkout, monkeypatch):
    monkeypatch.setenv("COLGREP_BIN", "/nonexistent/colgrep")
    payload = json.loads(srv.colgrep_search("authenticate", str(checkout)))
    assert payload["ok"] is False
    assert payload["status"] == "colgrep-not-found"
    assert "cargo install colgrep" in payload["message"]


# ---------------------------------------------------------- colgrep_list_dev_indices
def test_list_dev_indices_reports_indexed_after_a_search(checkout):
    raw = srv.colgrep_list_dev_indices(str(checkout))
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["status"] == "indexed"
    assert payload["project"] == str(checkout)
    assert payload["model"]
    assert payload["index_path"]


def test_list_dev_indices_reports_no_index_for_a_fresh_checkout(tmp_path_factory):
    fresh = tmp_path_factory.mktemp("colgrep-mcp-fresh")
    _git("init", "-q", cwd=fresh)
    (fresh / "f.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = json.loads(srv.colgrep_list_dev_indices(str(fresh)))
    assert payload["ok"] is True
    assert payload["status"] == "no-index"
    assert payload["index_path"] is None


def test_list_dev_indices_missing_cwd_is_an_error_not_a_traceback():
    payload = json.loads(srv.colgrep_list_dev_indices("/definitely/does/not/exist/xyz"))
    assert payload["ok"] is False
    assert payload["status"] == "cwd-missing"


def test_list_dev_indices_missing_binary_is_reported_not_raised(checkout, monkeypatch):
    monkeypatch.setenv("COLGREP_BIN", "/nonexistent/colgrep")
    payload = json.loads(srv.colgrep_list_dev_indices(str(checkout)))
    assert payload["ok"] is False
    assert payload["status"] == "colgrep-not-found"


# ------------------------------------------------------------------- binary resolution
def test_binary_resolution_prefers_colgrep_bin_override(tmp_path, monkeypatch):
    fake = tmp_path / "colgrep"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("COLGREP_BIN", str(fake))
    assert srv._colgrep_binary() == str(fake)


def test_binary_resolution_falls_back_to_path(monkeypatch):
    monkeypatch.delenv("COLGREP_BIN", raising=False)
    assert srv._colgrep_binary() == shutil.which("colgrep")
