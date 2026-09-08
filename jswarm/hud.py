"""Read-only, credential-free Claude statusline. No transcript reads or network calls."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LIMIT = 65_536
PROVIDERS = {"anthropic": "Anthropic", "gpt": "GPT", "glm": "GLM"}


def safe(value, limit=70):
    # Strip terminal controls (including OSC/ANSI) from untrusted model/branch/plan text.
    return re.sub(r"[^\w .:/%?()\-]", "", str(value))[:limit]


def token(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None


def read_json(path: Path, root: Path):
    try:
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            return {}
        if not path.is_file() or path.stat().st_size > LIMIT:
            return {}
        result = json.loads(path.read_text())
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def timestamp(value):
    if number(value):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError):
            pass
    return None


def window(label, row, now, percent_key="used_percentage", stale=False):
    if not isinstance(row, dict) or not number(row.get(percent_key)):
        return None
    pct = row[percent_key]
    if pct > 100 and label != "spend":
        return None
    reset = timestamp(row.get("resets_at"))
    if reset is not None and reset <= now:
        return f"{label} awaiting refresh"
    result = f"{label} {pct:.0f}% used"
    if reset is not None:
        minutes = max(1, math.ceil((reset - now) / 60))
        days, hours, mins = minutes // 1440, (minutes % 1440) // 60, minutes % 60
        result += f" (reset {str(days) + 'd' if days else ''}{str(hours) + 'h' if hours else ''}{str(mins) + 'm' if not days else ''})"
    return result + (" [stale]" if stale else "")


def quotas(payload, env, now):
    root = Path(env.get("JSWARM_PROVIDER_QUOTA_ROOT", str(Path.home() / ".cache/jswarm/provider-quota/v1")))
    sid = payload.get("session_id")
    binding = read_json(root / "bindings" / f"{sid}.json", root) if token(sid) else {}
    if binding:
        provider, account = binding.get("provider"), binding.get("account_ref")
        label = PROVIDERS.get(provider, "Provider") if isinstance(provider, str) else "Provider"
        if (binding.get("schema") != "jswarm.provider-quota-binding.v1" or
                binding.get("session_id") != sid or provider not in PROVIDERS or
                not isinstance(account, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", account)):
            return "Provider quota unavailable"
        snapshot = read_json(root / "accounts" / provider / f"{account}.json", root)
        if (snapshot.get("schema") != "jswarm.provider-quota.v1" or
                snapshot.get("provider") != provider or snapshot.get("account_ref") != account):
            return f"{label} quota unavailable"
        observed = timestamp(snapshot.get("observed_at"))
        if observed is None or observed > now + 5:
            return f"{label} quota unavailable"
        windows = snapshot.get("windows", {})
        if not isinstance(windows, dict):
            return f"{label} quota unavailable"
        parts = [window(key, windows.get(key), now, "used_percent", now - observed > 150) for key in ("5h", "7d")]
        return label + " " + (" | ".join(x for x in parts if x) or "quota unavailable")
    # A gateway/proxy must never silently borrow an Anthropic account's quota.
    if env.get("ANTHROPIC_BASE_URL") or any(env.get(key) for key in (
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")):
        limits = payload.get("rate_limits", {})
        spend = window("spend", limits.get("spend_limit"), now) if isinstance(limits, dict) else None
        return "Gateway " + spend if spend else "Provider quota unavailable (no session binding)"
    limits = payload.get("rate_limits", {})
    if not isinstance(limits, dict):
        limits = {}
    parts = [window(label, limits.get(key), now) for key, label in
             (("five_hour", "5h"), ("seven_day", "7d"), ("spend_limit", "spend"))]
    return "Claude " + (" | ".join(x for x in parts if x) or "quota unavailable")


def project_line(payload):
    from jswarm.plan_status.config import find_repo_root
    import yaml
    workspace = payload.get("workspace", {})
    cwd = workspace.get("current_dir") if isinstance(workspace, dict) else None
    root = find_repo_root(Path(cwd or payload.get("cwd") or Path.cwd()))
    if root is None:
        return "No project"
    branch = ""
    try:
        # No external diff/filter programs, no lock writes, bounded runtime.
        branch = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "branch", "--show-current"],
                                capture_output=True, text=True, timeout=.3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    parts = [safe(branch or "detached HEAD")]
    sid = payload.get("session_id")
    binding = read_json(root / ".jswarm/state/sessions" / str(sid) / "active-ticket.json", root) if token(sid) else {}
    ticket = binding.get("ticket") if binding.get("session_id") == sid else None
    if not token(ticket):
        return " | ".join(parts + ["No active work item"])
    parts.append(safe(ticket))
    plans = root / ".jswarm/plans"
    # Use this ticket only; ambiguous matches stay neutral rather than showing a different plan.
    files = list(plans.glob(f"{ticket}.plan.*.md")) + list((plans / ticket).glob(f"{ticket}.plan.*.md"))
    if len(files) == 1 and files[0].resolve().is_relative_to(root.resolve()) and files[0].stat().st_size <= LIMIT:
        content = files[0].read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---(?:\n|$)", content, re.S)
        fields = yaml.safe_load(match.group(1)) if match else {}
        if isinstance(fields, dict):
            for key, label in (("status", "Status"), ("phase", "Phase"), ("ac_complete", "A/C"),
                               ("uat_complete", "UAT"), ("nfr_complete", "NFR")):
                if isinstance(fields.get(key), (str, int)):
                    parts.append(f"{label}: {safe(fields[key], 30)}")
    return " | ".join(parts)


def render(payload, env=None, now=None):
    if not isinstance(payload, dict):
        return ""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    model = payload.get("model", {})
    context = payload.get("context_window", {})
    effort = payload.get("effort", {})
    parts = ["jSwarm", safe(model.get("display_name", "Model unavailable")) if isinstance(model, dict) else "Model unavailable"]
    if isinstance(effort, dict) and effort.get("level"):
        parts.append(safe(effort["level"]))
    pct = context.get("used_percentage") if isinstance(context, dict) else None
    if number(pct) and pct <= 100:
        filled = max(0, min(10, round(pct / 10)))
        parts.append(f"Context {pct:.0f}% [{'#' * filled}{'-' * (10 - filled)}]")
    else:
        parts.append("Context unavailable")
    try:
        quota = quotas(payload, env, now)
    except (OSError, ValueError, TypeError):
        quota = "Provider quota unavailable"
    try:
        project = project_line(payload)
    except Exception:
        project = "Work item unavailable"
    if env.get("JSWARM_HUD_COLOR") == "1" and "NO_COLOR" not in env:
        parts[0] = "\033[36;1mjSwarm\033[0m"
        quota = ("\033[33m" if any(word in quota for word in ("stale", "unavailable", "awaiting")) else "\033[36m") + quota + "\033[0m"
    return " | ".join(parts) + "\n" + quota + "\n" + project


def main():
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) <= LIMIT:
            output = render(json.loads(raw))
            if output:
                print(output)
    except Exception:
        pass  # The HUD must never break an agent session.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
