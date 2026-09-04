#!/usr/bin/env python3
"""ColGREP MCP server: wraps the `colgrep` CLI for semantic code search.

`colgrep` is a public crate (crates.io, `cargo install colgrep`), upstream at
`lightonai/next-plaid`: "Semantic code search powered by ColBERT." This server
shells out to that CLI and exposes exactly two tools -- the public product
contract (see `docs/superpowers/specs/2026-09-03-jswarm-public-repo-split-design.md`,
decision D7):

    colgrep_search            -- backed by `colgrep search`
    colgrep_list_dev_indices  -- backed by `colgrep status`

Both are code search only. Neither talks to a network service, a Docker
container, or any document corpus -- there is no content-search backend here.
This is a fresh, small, public implementation; it does not import or share
code with the private `next-plaid/colgrep-mcp-server.py`, which also backs a
private document corpus on port 3281 and several company-specific ingest
paths that are not part of this product.

The `colgrep` binary is resolved from the `COLGREP_BIN` environment variable
if set, else from `PATH` -- never a hardcoded path, since that would encode
one developer's machine into a public server.

Run directly (`python -m jswarm.colgrep_mcp_server`) to serve over stdio, the
same way the installer registers it with the agent host.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DEFAULT_TOP_K = 10
SEARCH_TIMEOUT_S = float(os.environ.get("COLGREP_MCP_SEARCH_TIMEOUT_S", "600"))
STATUS_TIMEOUT_S = float(os.environ.get("COLGREP_MCP_STATUS_TIMEOUT_S", "15"))

mcp = FastMCP(
    "ColGREP",
    instructions=(
        "Semantic code search over a git checkout, powered by the `colgrep` CLI "
        "(ColBERT embeddings). Call colgrep_search(query=..., cwd=<absolute checkout "
        "path>) for codebase questions before falling back to grep/find; it auto-builds "
        "the index on first use. Call colgrep_list_dev_indices(cwd=...) to check whether "
        "that checkout already has a built index."
    ),
)


def _colgrep_binary() -> str | None:
    """Resolve the colgrep CLI: `COLGREP_BIN` env override first, else `PATH`.

    Never a hardcoded absolute path -- every developer's machine differs, and a
    public server must not encode one of them.
    """
    override = os.environ.get("COLGREP_BIN", "").strip()
    if override:
        return override if Path(override).is_file() and os.access(override, os.X_OK) else None
    return shutil.which("colgrep")


def _error(status: str, message: str) -> str:
    return json.dumps({"ok": False, "status": status, "message": message, "results": []})


def _missing_binary_error() -> str:
    return _error(
        "colgrep-not-found",
        "the `colgrep` binary was not found ($COLGREP_BIN and PATH both checked). "
        "Install it with `cargo install colgrep`, or run "
        "`./install.sh install --with-colgrep` from the jSwarm clone.",
    )


def _resolve_scope(cwd: str, path: str | None) -> tuple[Path | None, str]:
    """Resolve and validate the search scope. Returns (scope, "") or (None, error-status)."""
    if not cwd or not str(cwd).strip():
        return None, "cwd-required"
    checkout = Path(cwd)
    if not checkout.is_dir():
        return None, "cwd-missing"
    checkout = checkout.resolve()
    if not path:
        return checkout, ""
    candidate = Path(path)
    scope = candidate if candidate.is_absolute() else checkout / candidate
    try:
        scope = scope.resolve()
    except OSError:
        return None, "path-unresolvable"
    if scope != checkout and checkout not in scope.parents:
        return None, "path-outside-cwd"
    if not scope.exists():
        return None, "path-missing"
    return scope, ""


@mcp.tool()
def colgrep_search(query: str, cwd: str, path: str | None = None, top_k: int = DEFAULT_TOP_K) -> str:
    """Semantic code search over `cwd` (a git checkout), backed by `colgrep search`.

    Auto-indexes the checkout on first use (this may take a while for a large,
    never-indexed checkout); later calls reuse the existing index. `path`, if
    given, must resolve inside `cwd` and scopes the search to that subtree.

    Returns a JSON object: {"ok", "status", "message", "results": [...]}.
    Each result has "name", "qualified_name", "file", "line", "end_line",
    "language", "unit_type", "signature", "docstring", "code", "score".
    `status` is one of: "ok", "empty" (no matches, not an error),
    "colgrep-not-found", "cwd-required", "cwd-missing", "path-outside-cwd",
    "path-missing", "path-unresolvable", "indexing" (still building, retry
    shortly), "cli-error".
    """
    if not query or not query.strip():
        return _error("query-required", "query must not be empty")

    binary = _colgrep_binary()
    if not binary:
        return _missing_binary_error()

    scope, err = _resolve_scope(cwd, path)
    if scope is None:
        return _error(err, f"invalid search scope (cwd={cwd!r}, path={path!r})")

    try:
        k = max(1, int(top_k or DEFAULT_TOP_K))
    except (TypeError, ValueError):
        k = DEFAULT_TOP_K

    argv = [binary, "search", "--json", "--color", "never", "-y", "-k", str(k), query, str(scope)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=SEARCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _error(
            "indexing",
            f"colgrep did not finish within {SEARCH_TIMEOUT_S:.0f}s -- it is likely still "
            "building the index for this checkout. Try again shortly.",
        )
    except OSError as exc:
        return _error("colgrep-not-found", f"could not run colgrep at {binary!r}: {exc}")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:1000] or f"colgrep exited {proc.returncode}"
        return _error("cli-error", detail)

    try:
        hits = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return _error("cli-error", f"colgrep produced non-JSON output: {proc.stdout[:500]!r}")

    if not isinstance(hits, list):
        return _error("cli-error", f"unexpected `colgrep --json` shape: {type(hits).__name__}")

    if not hits:
        return json.dumps({
            "ok": True,
            "status": "empty",
            "message": f"no results for {query!r} in {scope}",
            "results": [],
        })

    results = []
    for hit in hits:
        unit = hit.get("unit") if isinstance(hit, dict) else None
        unit = unit if isinstance(unit, dict) else {}
        results.append({
            "name": unit.get("name"),
            "qualified_name": unit.get("qualified_name"),
            "file": unit.get("file"),
            "line": unit.get("line"),
            "end_line": unit.get("end_line"),
            "language": unit.get("language"),
            "unit_type": unit.get("unit_type"),
            "signature": unit.get("signature"),
            "docstring": unit.get("docstring"),
            "code": unit.get("code"),
            "score": hit.get("score") if isinstance(hit, dict) else None,
        })
    return json.dumps({
        "ok": True,
        "status": "ok",
        "message": f"{len(results)} result(s)",
        "results": results,
    })


@mcp.tool()
def colgrep_list_dev_indices(cwd: str) -> str:
    """Report the ColGREP index status for `cwd`, backed by `colgrep status`.

    Returns a JSON object: {"ok", "status", "message", "project", "model",
    "index_path"}. `status` is "indexed" when a built index exists,
    "no-index" when `colgrep status` reports none yet (the next
    `colgrep_search` call will build one), or an error status matching
    `colgrep_search`'s.
    """
    binary = _colgrep_binary()
    if not binary:
        return _missing_binary_error()

    checkout = Path(cwd) if cwd else None
    if checkout is None or not checkout.is_dir():
        return _error("cwd-missing", f"cwd does not exist or is not a directory: {cwd!r}")
    checkout = checkout.resolve()

    argv = [binary, "status", str(checkout), "--color", "never"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=STATUS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _error("status-timeout", f"`colgrep status` did not finish within {STATUS_TIMEOUT_S:.0f}s")
    except OSError as exc:
        return _error("colgrep-not-found", f"could not run colgrep at {binary!r}: {exc}")

    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        detail = (proc.stderr or out).strip()[:1000] or f"colgrep exited {proc.returncode}"
        return _error("cli-error", detail)

    if out.lower().startswith("no index found"):
        return json.dumps({
            "ok": True,
            "status": "no-index",
            "message": out,
            "project": str(checkout),
            "model": None,
            "index_path": None,
        })

    project = model = index_path = None
    for line in out.splitlines():
        if line.startswith("Project:"):
            project = line.split(":", 1)[1].strip()
        elif line.startswith("Model:"):
            model = line.split(":", 1)[1].strip()
        elif line.startswith("Index:"):
            index_path = line.split(":", 1)[1].strip()

    return json.dumps({
        "ok": True,
        "status": "indexed" if index_path else "unknown",
        "message": out,
        "project": project,
        "model": model,
        "index_path": index_path,
    })


if __name__ == "__main__":
    mcp.run()
