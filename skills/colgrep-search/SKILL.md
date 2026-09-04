---
name: colgrep-search
description: Guide agents to use the ColGREP MCP server for semantic code search before exact-match fallbacks.
user-invocable: false
---

# ColGREP Semantic Search

Semantic code search over the current checkout, powered by ColBERT embeddings via
the `colgrep` CLI (`cargo install colgrep`, crates.io, upstream `lightonai/next-plaid`).
Installed and registered by `./install.sh install --with-colgrep`.

**ColGREP is a first-class MCP server.** Its tools are directly callable; no
proxy or wrapper skill call is needed.

## Mandatory Subagent Search Protocol

> **Orchestrators:** Copy this block verbatim into every `task()` delegation prompt for codebase work.

```
SEARCH PROTOCOL (MANDATORY, follow before touching any file):
1. Use ColGREP FIRST for any codebase question:
    colgrep_search(query="<describe what you need>", cwd="<absolute checkout path>", top_k=10)
2. Read at most 5 files total: use ColGREP results to decide which ones
3. Do NOT use grep/find/glob for exploratory questions; only for exact pattern matching AFTER ColGREP
4. Do NOT enumerate directories or read files speculatively
5. Development discovery (informational only): colgrep_list_dev_indices(cwd="<absolute checkout path>")
```

**Why this matters:** The runtime cancels agents that call `read` >80% of the time in a sliding window. Without ColGREP, agents grep → read 15+ files → get cancelled (false positive). With ColGREP, they search semantically → read 2-3 targeted files → succeed.

## MCP Tools

This server exposes exactly two tools -- code search only, over the current
checkout. There is no content-search backend here.

### `colgrep_search`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | yes | Natural language search query |
| `cwd` | yes | Absolute checkout/worktree path to search |
| `path` | no | Subtree within `cwd` to scope the search to |
| `top_k` | no | Number of results (default: 10) |

Auto-indexes `cwd` on first use (this may take a while for a large, never-indexed
checkout); later calls reuse the existing index. Returns a JSON object:

```json
{"ok": true, "status": "ok", "message": "3 result(s)", "results": [
  {"name": "...", "qualified_name": "...", "file": "...", "line": 12, "end_line": 20,
   "language": "python", "unit_type": "function", "signature": "...", "docstring": "...",
   "code": "...", "score": 2.7}
]}
```

`status` is one of: `ok`, `empty` (no matches -- not an error), `colgrep-not-found`,
`cwd-required`, `cwd-missing`, `path-outside-cwd`, `path-missing`,
`path-unresolvable`, `indexing` (still building on first use -- retry shortly),
`cli-error`.

### `colgrep_list_dev_indices`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `cwd` | yes | Absolute checkout path to report on |

Informational: reports whether `cwd` already has a built index (`status:
"indexed"`, with `model` and `index_path`) or not yet (`status: "no-index"` --
the next `colgrep_search` call will build one). Does not search or index.

## Usage Examples

```
colgrep_search(query="role-based access control middleware", cwd="/absolute/path/to/checkout", top_k=10)

colgrep_search(query="Monte Carlo simulation engine", cwd="/absolute/path/to/checkout", top_k=10)

colgrep_search(query="rate limiting", cwd="/absolute/path/to/checkout", path="src/api", top_k=10)

colgrep_list_dev_indices(cwd="/absolute/path/to/checkout")
```

## When to Use What

| Task | Tool |
|------|------|
| Find code by intent/description | `colgrep_search` |
| Explore/understand a codebase | `colgrep_search` with `top_k=25` |
| Check whether a checkout is already indexed | `colgrep_list_dev_indices` |
| Exact string/regex match only | Built-in `Grep` tool |
| Find files by name | Built-in `Glob` tool |

## Why ColGREP Over Grep?

| Method | Query | Results |
|--------|-------|---------|
| `grep("role assignment")` | Exact string match | **0 matches**, string doesn't exist |
| `colgrep_search("role-based access control")` | Semantic intent | **5+ functions** found by PURPOSE |

## Key Rules

1. **Use `colgrep_list_dev_indices`** only for informational index-status checks; search itself always uses `cwd` directly
2. **Increase `top_k`** when exploring (20-30 results)
3. **NEVER run `colgrep` via Bash for search**: it re-runs indexing logic outside the MCP tool's error handling. ALWAYS use `colgrep_search`.
4. For exact text matching, use the built-in `Grep` tool instead

## Anti-Patterns

```
# WRONG: shelling out to colgrep CLI
Bash: colgrep "role assignment" -k 10

# WRONG: grep for exploratory questions
grep(pattern="role.*assignment")

# CORRECT: first-class MCP tool (direct call)
colgrep_search(query="role assignment and access control", cwd="/absolute/path/to/checkout", top_k=10)
```
