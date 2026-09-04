---
name: ctx
description: Report the calling session's or subagent's context-window usage and quota via the ctx skill.
usage: |
  /ctx
  /ctx --json
---
<!-- Canonical source: templates/commands/ctx.md in this repo. Copy or symlink
     into your project's .claude/commands/ (or globally into ~/.claude/commands/)
     to install the /ctx command; edit the canonical copy, not a deployed one. -->

# /ctx

Run the `ctx` skill (`skills/ctx/ctx-usage.py`, deployed at
`~/.claude/skills/ctx/ctx-usage.py`) and print its compact all-info context-usage line. See that
skill's `SKILL.md` for the full field/format contract; this command only documents how to invoke it
correctly from each caller role.

## Orchestrator / user path

The orchestrator's own `Bash` tool subprocess runs with `CLAUDE_CODE_CHILD_SESSION=1` set, which the
skill's Layer-A containment floor treats as a subagent invocation. To read the orchestrator's own
session context (not a subagent's), clear that variable for the call so the skill resolves in
main-session mode, and omit `--identity-nonce`:

```bash
CLAUDE_CODE_CHILD_SESSION= .venv/bin/python ~/.claude/skills/ctx/ctx-usage.py
CLAUDE_CODE_CHILD_SESSION= .venv/bin/python ~/.claude/skills/ctx/ctx-usage.py --json
```

`JSWARM_CTX_OUTCOME_LOG` does not need to be set explicitly — when unset, the skill auto-resolves a
default jAgentProxy `[OUTCOME]` log path (`$JARVISWARM_ROOT/log/jagentproxy.log`, else
`${JSWARM_HOME:-$HOME/dev/jswarm}/log/jagentproxy.log`, else falls back to the transcript-tail source).

## Agent path

A subagent invokes the same skill with its own fresh identity nonce, generated and passed as a
literal in the command text (never via shell expansion):

```bash
.venv/bin/python ~/.claude/skills/ctx/ctx-usage.py --identity-nonce ctx-nonce:<random-base64url-128bit>
.venv/bin/python ~/.claude/skills/ctx/ctx-usage.py --json --identity-nonce ctx-nonce:<random-base64url-128bit>
```

Use a new nonce per invocation; do not reuse one across calls. See `SKILL.md`'s "Invocation contract"
section for the exact nonce grammar and fail-closed rules.

## Output

Default output is one compact line — identical shape for orchestrator and agent callers:

```text
ctx <context_tokens>/<window> <context_pct>% | <model>[ <provider>] | src:<usage_source> | quota.5h <n>%, quota.7d <n>%, quota.fable <n>%[, quota.opus <n>%, quota.sonnet <n>%]
```

Pass `--json` for the full machine-readable report (see `SKILL.md`'s JSON field table).

## Related

- Skill: `skills/ctx/SKILL.md`
- The F-49 `jswarm-ctx-widget.py` HUD is a separate, decoupled statusline mechanism — not this on-demand command.
