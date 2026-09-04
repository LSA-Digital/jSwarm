---
name: colgrep-search
description: Guide agents to use ColGREP MCP for semantic code and ARIS content search before exact-match fallbacks.
user-invocable: false
---

# ColGREP Semantic Search

Semantic search powered by ColBERT embeddings. Code search uses local per-checkout OOB indexes; ARIS/content search uses the independent :3281 content plane.

**ColGREP is a first-class global MCP server.** Tools are directly callable — no lazy-mcp proxy needed.

## Mandatory Subagent Search Protocol

> **Orchestrators:** Copy this block verbatim into every `task()` delegation prompt for codebase work.

```
SEARCH PROTOCOL (MANDATORY — follow before touching any file):
1. Use ColGREP FIRST for any codebase question:
    colgrep_search(query="<describe what you need>", cwd="<absolute checkout/worktree path>", top_k=10)
2. Read at most 5 files total — use ColGREP results to decide which ones
3. Do NOT use grep/find/glob for exploratory questions — only for exact pattern matching AFTER ColGREP
4. Do NOT enumerate directories or read files speculatively
5. Development discovery (informational only): colgrep_list_dev_indices()
```

**Why this matters:** The runtime cancels agents that call `read` >80% of the time in a sliding window. Without ColGREP, agents grep → read 15+ files → get cancelled (false positive). With ColGREP, they search semantically → read 2-3 targeted files → succeed.

## MCP Tools: Code Search (local per-checkout OOB)

### `colgrep_search`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | yes | Natural language search query |
| `cwd` | yes | Absolute checkout/worktree path |
| `top_k` | no | Number of results (default: 10) |

### `colgrep_list_dev_indices`

Discover active local OOB development indexes/checkouts. This is informational; use `cwd` for code search and do not restore the legacy `index=` calling pattern.

## OOB per-checkout mode: preferred code-search shape

When the OOB backend is active (`COLGREP_MCP_BACKEND=oob` on the ColGREP MCP
server), `colgrep_search` queries the per-checkout index directly and `cwd`
replaces `index`:

```
colgrep_search(query="<what you need>", cwd="<absolute checkout/worktree path>",
               path="<optional contained directory>", top_k=10)
```

- Always pass `cwd` (the active checkout or worktree). Use `path` to scope to a
  directory subtree; to narrow to a file, scope to its containing directory and
  use built-in `Grep` for exact matching. Results never leak outside the scope.
- Queries never trigger indexing (`--no-update` always).
- The response status line ends with index health: `index fresh`, or when
  behind, `N unindexed, eta ~Ts` (count + ETA only — never a file list). A
  `WARNING ... watcher not running` token means background refresh is down;
  results still return but may be outdated.

## MCP Tools: Content Search (port 3281)

### `colgrep_search_content`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | yes | Natural language description of what you're looking for |
| `index` | yes | Content index name (use `colgrep_list_content_indices` to discover) |
| `top_k` | no | Number of results (default: 10) |

### `colgrep_search_content_filtered`

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | yes | Natural language search query |
| `index` | yes | Content index name |
| `filter_condition` | yes | SQL WHERE clause with `?` placeholders (e.g. `model_type = ?`) |
| `filter_parameters` | yes | Values for `?` placeholders |
| `top_k` | no | Number of results (default: 10) |

Available filter fields: `model_type`, `model_name`, `package`, `guid`

### `colgrep_list_content_indices`

Discover available content indices with document counts. Call with no arguments.

### `colgrep_get_content_catalog`

Returns the master catalog describing what each content index contains. **Call this first** before searching content — it tells you which index answers which questions.

## Usage Examples

### Code Search

```
colgrep_search(query="role-based access control middleware", cwd="/absolute/path/to/checkout", top_k=10)

colgrep_search(query="Monte Carlo simulation engine", cwd="/absolute/path/to/checkout", top_k=10)

colgrep_search(query="SDOH assessment for permit report", cwd="/absolute/path/to/checkout", top_k=25)
```

### Content Search (ARIS Business Processes)

```
colgrep_get_content_catalog()

colgrep_search_content(query="employee training and development programs", index="aris-pcf-cross", top_k=10)

colgrep_search_content_filtered(
  query="approval workflow",
  index="aris-pcf-cross",
  filter_condition="model_type = ?",
  filter_parameters=["Enterprise BPMN collaboration diagram"],
  top_k=10
)

colgrep_list_content_indices()
```

## When to Use What

| Task | Tool |
|------|------|
| Find code by intent/description | `colgrep_search` |
| Find ARIS process models/activities | `colgrep_search_content` |
| Filter content by model type | `colgrep_search_content_filtered` |
| Discover what content indices exist | `colgrep_get_content_catalog` |
| Explore/understand a codebase | `colgrep_search` with `top_k=25` |
| Discover active local OOB development indexes/checkouts | `colgrep_list_dev_indices` |
| Discover available content indices | `colgrep_list_content_indices` |
| Exact string/regex match only | Built-in `Grep` tool |
| Find files by name | Built-in `Glob` tool |

## Why ColGREP Over Grep?

| Method | Query | Results |
|--------|-------|---------|
| `grep("role assignment")` | Exact string match | **0 matches** — string doesn't exist |
| `colgrep_search("role-based access control")` | Semantic intent | **5+ functions** found by PURPOSE |

## Key Rules

1. **Use `colgrep_list_dev_indices`** only for informational development discovery; search uses `cwd`
2. **Increase `top_k`** when exploring (20-30 results)
3. **NEVER run `colgrep` via Bash** — triggers expensive index rebuilds. ALWAYS use the MCP tool.
4. For exact text matching, use the built-in `Grep` tool instead

## CLI Fallback (Edge Cases Only)

The MCP tool does NOT support file-type filtering or hybrid text+semantic search. For these rare cases only:

```bash
colgrep "query" ~/dev/project --include="*.py" -k 10 2>/dev/null

colgrep -e "exactPattern" "semantic description" ~/dev/project -k 10 2>/dev/null
```

## Anti-Patterns

```
# WRONG: shelling out to colgrep CLI
Bash: colgrep "role assignment" -k 10

# WRONG: grep for exploratory questions
grep(pattern="role.*assignment")

# CORRECT: first-class MCP tool (direct call)
colgrep_search(query="role assignment and access control", cwd="/absolute/path/to/checkout", top_k=10)
```
