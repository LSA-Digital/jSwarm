---
name: ctx-optional-hook-config
description: Optional default-off hook configuration for main-session ctx threshold warnings.
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/ctx/OPTIONAL_HOOK_CONFIG.md
     Master source of truth. Deploys to ~/.claude as a symlink ONLY via /devops-maint dotclaude (mode 37);
     that one-time symlink deploy is out of scope for COM-239 (this master is not yet deployed).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy. -->

# Optional ctx Threshold Hook Configuration

**Last Updated:** 2026-07-05

This optional hook is default-OFF. Enable it only if you want Claude Code to inject a warning when the main/orchestrator session crosses a context-window threshold.

Important caveat: this hook is intended for the **main/orchestrator session**. It does **not** provide subagent self-context. A subagent must invoke the skill explicitly with its own fresh literal `--identity-nonce ctx-nonce:<random-base64url-128bit>`, because a hook-driven call cannot supply the per-invocation transcript nonce that the subagent resolver requires.

## Hook script

After the ctx skill has been deployed to `~/.claude`, create this local hook script at `~/.claude/hooks/pretool-ctx-threshold-report.py`:

```python
#!/usr/bin/env python3
"""Default-off ctx threshold reporter for the main/orchestrator session."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

THRESHOLD = 80.0
PYTHON = Path.cwd() / ".venv" / "bin" / "python"
SCRIPT = Path.home() / ".claude" / "skills" / "ctx" / "ctx-usage.py"


def main() -> int:
    if not PYTHON.is_file() or not SCRIPT.is_file():
        return 0

    try:
        result = subprocess.run(
            [str(PYTHON), str(SCRIPT), "--json"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return 0

    if result.returncode != 0 or not result.stdout.strip():
        return 0

    try:
        data = json.loads(result.stdout)
    except ValueError:
        return 0

    ctx_pct = data.get("context_pct")
    if not isinstance(ctx_pct, (int, float)) or ctx_pct < THRESHOLD:
        return 0

    context_tokens = data.get("context_tokens")
    window = data.get("window")
    usage_source = data.get("usage_source")
    session_pct = data.get("session_usage_pct")
    weekly_pct = data.get("weekly_usage_pct")
    session_reset = data.get("session_reset_in")
    weekly_reset = data.get("weekly_reset_in")

    parts = [
        f"Context window is at {ctx_pct}%",
        f"tokens={context_tokens}/{window}",
        f"usage_source={usage_source}",
    ]
    if session_pct is not None:
        suffix = f" reset={session_reset}" if session_reset else ""
        parts.append(f"session={session_pct}%{suffix}")
    if weekly_pct is not None:
        suffix = f" reset={weekly_reset}" if weekly_reset else ""
        parts.append(f"weekly={weekly_pct}%{suffix}")

    print(
        "<system-reminder>ctx threshold warning: " + "; ".join(parts) + "</system-reminder>"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Adjust `THRESHOLD` to the warning level you want.

## Settings hook entry

Add one of the hook entries below to `~/.claude/settings.json` or project `.claude/settings.json` only after you decide to opt in.

### PreToolUse

Use this when you want the warning checked before tool calls:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": ".venv/bin/python $HOME/.claude/hooks/pretool-ctx-threshold-report.py"
          }
        ]
      }
    ]
  }
}
```

### UserPromptSubmit

Use this when you prefer one check before each submitted user prompt:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": ".venv/bin/python $HOME/.claude/hooks/pretool-ctx-threshold-report.py"
          }
        ]
      }
    ]
  }
}
```

## Behavior and limitations

- Default-OFF / opt-in only.
- Intended for the main/orchestrator session, which does not require `--identity-nonce`.
- Not a substitute for subagent self-context. Subagents must call `ctx-usage.py` themselves with a fresh literal nonce.
- Uses `~/.claude/skills/ctx/ctx-usage.py` after deployment and the current working tree's `.venv/bin/python` interpreter.
- Calls the skill with `--json` and suppresses warnings when ctx usage is unavailable or below threshold.
- Adds hook latency on every matched event; keep the timeout small.
- Reports last-known terminal usage, so it has the same one-turn lag as the skill itself.

## Deploy note

This file is a controlled-config master. It deploys to `~/.claude` only through `/devops-maint dotclaude` mode 37. Do not hand-install or hand-edit a deployed copy.
