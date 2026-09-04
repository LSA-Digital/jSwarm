---
name: ctx
aliases: ["ctx-usage", "context-usage", "quota"]
description: Report the calling Claude Code session or subagent context-window usage with fail-closed self-identification.
usage: |
  .venv/bin/python ~/.claude/skills/ctx/ctx-usage.py [--json]
  .venv/bin/python ~/.claude/skills/ctx/ctx-usage.py [--json] --identity-nonce ctx-nonce:<random-base64url-128bit>
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/ctx/SKILL.md
     Master source of truth. Deploys to ~/.claude as a symlink ONLY via /devops-maint dotclaude (mode 37);
     that one-time symlink deploy is out of scope for COM-239 (this master is not yet deployed).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy. -->

# ctx

**Last Updated:** 2026-07-05 (Phase 9, COM-239)

Use this skill to check the current context-window usage for the caller itself. It is designed to work for both the top-level orchestrator session and subagents without letting a subagent accidentally report the parent session or a sibling agent.

## Invocation contract

### Main/orchestrator session

The top-level Claude Code session does not need an identity nonce:

```bash
.venv/bin/python ~/.claude/skills/ctx/ctx-usage.py
.venv/bin/python ~/.claude/skills/ctx/ctx-usage.py --json
```

For the main session, `ctx-usage.py` reads only the current session transcript named `<session>.jsonl` under the active Claude Code project/session directory.

### Subagents

A subagent **must** pass a fresh literal nonce in the command text:

```bash
.venv/bin/python ~/.claude/skills/ctx/ctx-usage.py --json --identity-nonce ctx-nonce:<random-base64url-128bit>
```

Rules:

- The value must match `ctx-nonce:<random-base64url-128bit>` grammar, where the random part is base64url text of 22-24 characters.
- The nonce must be a literal in the command recorded in the transcript.
- Do not use shell expansion such as `$(openssl rand ...)` in place of the nonce. The resolver searches transcript text; it needs the actual recorded command text to contain the final nonce.
- Use a fresh unique nonce for each invocation.
- If the nonce is missing, malformed, absent from the bounded transcript tail, or appears in more than one candidate subagent transcript, the result is `unavailable` by design.

Why this is required: subagent self-context is resolved by locating the single `subagents/agent-<id>.jsonl` file that contains the caller's nonce. The resolver then verifies that the filename `agentId`, transcript `agentId`, `sessionId`, and `isSidechain` metadata agree. Ambiguity or mismatch fails closed as `unavailable`; it never guesses and never reads the parent transcript for a subagent.

### Runtime note — orchestrator Bash subprocess is classified as a subagent

In the current Claude Code runtime, the orchestrator's own `Bash` tool runs its subprocess with `CLAUDE_CODE_CHILD_SESSION=1` set, so a plain `ctx` call made from the orchestrator's Bash tool is classified as a subagent invocation and returns `unavailable` (no identity nonce was supplied for it). Subagents are the primary supported path here — invoke with `--identity-nonce`. To read the orchestrator's own session context directly, invoke `ctx-usage.py` in true main-mode with `CLAUDE_CODE_CHILD_SESSION` cleared from the environment. Separately, the F-49 `jswarm-ctx-widget.py` HUD already owns reading the orchestrator's own statusline context, so most callers never need this workaround.

## What it reports

### Context usage

`ctx-usage.py` reports the last known terminal context usage for the caller:

- Primary source: raw jAgentProxy `[OUTCOME]` telemetry (`usage_source="jagentproxy-outcome-log"`). This path is cross-provider because all agent traffic is proxied, so it can report Anthropic and non-Anthropic model usage.
- Fallback source: the caller's own bounded transcript tail (`usage_source="transcript-jsonl"`) when the proxy outcome log is unavailable or has no usable exact match. This fallback expects an Anthropic-shaped transcript `message.usage` object.
- Failure source: `usage_source="unavailable"` with a `reason` and non-zero exit when neither path can produce a usable usage record.

The context tokens are terminal / last-known usage from the last usable record. They are not provider-effective billing or quota accounting.

### Window size

The context window comes from the COM-49 `model-windows.yaml` registry unless a higher-precedence source applies. Precedence is:

1. `JSWARM_CTX_WINDOW` explicit positive integer token override (`window_source="env"`).
2. Exact model id in `model-windows.yaml` (`window_source="registry"`).
3. Model id containing `[1m]` (`window_source="heuristic-1m"`, window `1000000`).
4. Default window `200000` (`window_source="default"`).

Set `JSWARM_CTX_MODEL_WINDOWS` to point at an alternate registry file when testing or running from a nonstandard deployment.

### Quota data — scoped to the caller's OWN provider and model (Phase 9, COM-239)

Quota reflects the **caller's own provider**, resolved from the usage record's model/provider (`resolve_quota_provider`), then dispatched to a per-provider adapter (`fetch_quota_for_caller`). A caller never shows another provider's quota — a gpt subagent shows its OpenAI/Codex quota, a Claude caller shows its Anthropic quota. An **unknown/unidentified** provider fetches **no** quota and makes **no** network call (fail-closed dispatch isolation).

- **Anthropic caller** (`claude*`) → `GET https://api.anthropic.com/api/oauth/usage`. OAuth token read header-only from `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`, or `JSWARM_CTX_CREDENTIALS`). Response `limits[]` parsed generically/turbulent-safely: `kind:"session"`→`5h`, `kind:"weekly_all"`→`7d`, `kind:"weekly_scoped"`→scope model `display_name` lowercased (`fable`/`opus`/`sonnet`/…); unknown kinds pass through; when `limits[]` is absent, top-level `five_hour`/`seven_day` utilization is used.
- **OpenAI / gpt / Codex caller** (`gpt`/`codex`/`openai`) → `GET https://chatgpt.com/backend-api/wham/usage`. OAuth `access_token` + `account_id` read header-only from `~/.cli-proxy-api/codex-*.json` (a non-`disabled` entry; or `JSWARM_CTX_CODEX_CREDENTIALS`), sent only in the `Authorization: Bearer` and `ChatGPT-Account-Id` headers. Windows are labeled by duration: `primary_window` (18000s)→`5h`, `secondary_window` (604800s)→`7d` (other durations humanized); `reset_at` (unix epoch) is converted to a countdown. **Model-scoped:** the account-wide `additional_rate_limits[]` (per-model scoped limits such as `gpt-5.3-codex-spark`, the micro-agent model) is filtered to **only the limit whose name matches the caller's own model** — so a spark micro-agent shows its `gpt-5.3-codex-spark` limit, while a `gpt-5.4-mini`/`gpt-5.5` caller shows only `5h`/`7d`. A window with a missing/non-numeric percent is treated as unusable (skipped).
- **GLM / Z.ai caller** (`glm`/`zai`) → documented stub behind the same dispatch seam (no quota endpoint wired yet) → `quota unavailable`.

Every adapter reads its credential with the same fd-anchored, `O_NOFOLLOW`, regular-file, size-bounded read; uses the token/account header-only; never logs/echoes/persists it; runs on a bounded timeout; and **fails closed independently** — any missing credential, `disabled` account, non-200, timeout, network error, or malformed body yields no quota for that caller rather than an error, and never breaks the context line or another provider's quota. This supersedes the earlier Phase-8 Anthropic-only quota path and the still-earlier ccstatusline-cache source.

## Output

A single compact all-info line is the default human-readable output (Phase 7, COM-239) — nothing is hidden, and the shape is **identical** whether the caller is the orchestrator (`agent_id` is `null`) or a subagent:

```text
ctx <context_tokens>/<window> <context_pct>% | <model>[ <provider>] | src:<usage_source> | quota.5h <n>%, quota.7d <n>%, quota.fable <n>%[, quota.opus <n>%, quota.sonnet <n>%]
```

- `<context_tokens>`/`<window>` are humanized (`552771` -> `552.8k`, `1000000` -> `1M`, `200000` -> `200k`).
- `<context_pct>` is rounded to the nearest whole percent.
- The quota segment renders **one `quota.<label> <percent>%` part per limit the caller's provider adapter returned, in order** — nothing is hidden. For an Anthropic caller the labels are typically `5h`/`7d`/`fable`(/`opus`/`sonnet`); for an OpenAI/Codex caller `5h`/`7d`(+ the caller's own model-scoped limit, e.g. `gpt-5.3-codex-spark`). The set is whatever the caller's provider currently tracks (turbulent-safe + model-scoped).
- When the endpoint is unavailable (missing credential, timeout, non-200, network/parse error), the quota segment renders `quota unavailable` and the context line is still emitted.

When usage is unavailable, the model block is dropped and the line reads:

```text
ctx unavailable (<reason>) | src:<usage_source> | quota.5h <n>%, quota.7d <n>%, quota.fable <n>%[, …]
```

Use `--json` for machine-readable output:

```bash
.venv/bin/python ~/.claude/skills/ctx/ctx-usage.py --json
```

The JSON report contains these fields:

| Field | Meaning |
| --- | --- |
| `agent_id` | Resolved subagent id, or `null` for the main session. |
| `provider` | Provider label from jAgentProxy `class`, a coarse fallback from model prefix, or `null`. |
| `model` | Model id from the usage record. |
| `context_tokens` | Last-known context token count. |
| `context_pct` | `context_tokens / window * 100`, rounded to one decimal. |
| `window` | Resolved context-window token count. |
| `window_source` | `env`, `registry`, `heuristic-1m`, `default`, or `null`. |
| `usage_source` | `jagentproxy-outcome-log`, `transcript-jsonl`, or `unavailable`. |
| `reason` | Failure reason when unavailable, otherwise `null`. |
| `quota` | Ordered list of every limit the caller's own provider adapter returned (Anthropic `/api/oauth/usage` or OpenAI/Codex `wham/usage`, model-scoped), each `{label, percent, resets_at}`; `null` when the adapter is unavailable, the caller's provider is unknown, or it's the GLM stub (fail-closed). Turbulent-safe — new limits appear automatically. |
| `session_usage_pct` | Convenience: percent of the `5h` (session) quota entry, or `null`. Derived from `quota`. |
| `weekly_usage_pct` | Convenience: percent of the `7d` (weekly-all) quota entry, or `null`. Derived from `quota`. |
| `weekly_fable_pct` | Convenience: percent of the `fable` quota entry, or `null` when the caller's provider quota has no Fable limit (e.g. a non-Anthropic caller). Derived from `quota`. |
| `weekly_opus_pct` | Convenience: percent of the `opus` quota entry, or `null`. Derived from `quota`. |
| `weekly_sonnet_pct` | Convenience: percent of the `sonnet` quota entry, or `null`. Derived from `quota`. |
| `session_reset_in` | Best-effort session reset countdown (from the `5h` entry's `resets_at`). |
| `weekly_reset_in` | Best-effort weekly reset countdown (from the `7d` entry's `resets_at`). |

On failure, the command still emits the report. It sets `usage_source` to `unavailable`, includes a `reason`, and exits non-zero.

## Environment variables

The implementation reads only an allow-list of named variables via direct lookup. It never enumerates the environment and never reads secret-shaped variables such as `*_KEY`, `*_SECRET`, or `*_TOKEN`. Provider OAuth credentials are **not** read from the environment — they are read from their credentials files (Anthropic: `~/.claude/.credentials.json` / `JSWARM_CTX_CREDENTIALS`; OpenAI/Codex: `~/.cli-proxy-api/codex-*.json` / `JSWARM_CTX_CODEX_CREDENTIALS`) solely for the caller's read-only provider quota GET, used header-only, and never logged/echoed/persisted.

Supported user-facing overrides:

| Variable | Purpose |
| --- | --- |
| `JSWARM_CTX_WINDOW` | Positive integer model window token override. |
| `JSWARM_CTX_TAIL_LINES` | Number of recent transcript lines searched for a subagent identity nonce. Defaults to `200`. |
| `JSWARM_CTX_OUTCOME_LOG` | Path to the raw jAgentProxy `[OUTCOME]` log. When unset, the default is auto-resolved (Phase 7, COM-239): `$JARVISWARM_ROOT/log/jagentproxy.log` if that file exists, else `${JSWARM_HOME:-$HOME/dev/jswarm}/log/jagentproxy.log` if that file exists, else no outcome log (falls through to the transcript-tail source). |
| `JSWARM_CTX_MODEL_WINDOWS` | Path to an alternate `model-windows.yaml` registry. |
| `JSWARM_CTX_CREDENTIALS` | Path to the Anthropic OAuth credentials JSON used for the Anthropic-caller quota GET. Defaults to `~/.claude/.credentials.json`. Read header-only; token never logged/persisted. |
| `JSWARM_CTX_CODEX_CREDENTIALS` | Path to the OpenAI/Codex OAuth credentials JSON used for the gpt-caller quota GET. Defaults to a non-`disabled` `~/.cli-proxy-api/codex-*.json`. Read header-only (`access_token` + `account_id`); never logged/persisted. |
| `JSWARM_CTX_QUOTA_TIMEOUT` | Bounded timeout (seconds) for the provider quota GET (Anthropic or Codex). Defaults to `3.0`. Failure fails closed (no quota, no error). |

The script also reads Claude Code/session variables needed for operation: `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION`, `JSWARM_CTX_AGENT_ID`, `JARVISWARM_ROOT`, and `HOME` (the latter two are read only for the `JSWARM_CTX_OUTCOME_LOG` default-resolution fallback described above).

## `/ctx` command

A thin `/ctx` slash command wraps this skill for interactive use — see `docs/_CONTROLLED_CONFIG/dotclaude/user/commands/ctx.md`. It documents the orchestrator path (clear `CLAUDE_CODE_CHILD_SESSION` so the call resolves as main-session, not subagent) and the agent path (pass a fresh `--identity-nonce ctx-nonce:<...>`).

## Limitations

- **One-turn lag:** context usage reflects the last usable terminal usage record, not tokens that will be added later in the current in-progress turn.
- **New-session shows 0/unavailable until usage exists:** a fresh session with no usable assistant usage record may show no context usage yet.
- **Fail-closed self-identification:** missing session metadata, invalid nonce grammar, no nonce match, multiple nonce matches, transcript mismatch, missing proxy log, unusable usage fields, and negative usage values resolve to `unavailable` rather than a guessed value.
- **Quota is best-effort, caller-provider-scoped, and fail-closed:** quota comes from a live read-only GET to the caller's OWN provider quota endpoint (Anthropic `/api/oauth/usage` or OpenAI/Codex `wham/usage`, model-scoped); a non-Anthropic provider without a wired adapter (e.g. GLM) or an unidentified provider yields no quota and makes no call; a missing credential, timeout, non-200, network error, or malformed body yields no quota (`quota` is `null`, the compact line shows `quota unavailable`) rather than failing the whole report.

## Related mechanisms

The F-49 `jswarm-ctx-widget.py` HUD is a separate statusline widget. It is a decoupled, single-writer HUD mechanism and is not the same as this on-demand `ctx` skill.

## Deploy note

This file is a controlled-config master. It deploys to `~/.claude` only through `/devops-maint dotclaude` mode 37. Do not hand-install or hand-edit a deployed copy.
