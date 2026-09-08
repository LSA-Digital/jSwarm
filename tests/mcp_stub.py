"""Stateful CLI fixture; real MCP protocol is still exercised against our server."""
from pathlib import Path
import sys


def write_claude_stub(bindir: Path, log: Path | None = None) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    script = bindir / "claude"
    script.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
log = {str(log) if log else None!r}
if log:
    with open(log, "a") as output:
        output.write(" ".join(args) + "\\n")
path = Path(os.environ["HOME"]) / ".claude.json"
data = json.loads(path.read_text()) if path.exists() else {{}}
servers = data.setdefault("mcpServers", {{}})
operation = args[1]
rest = args[2:]
env = {{}}
positional = []
i = 0
while i < len(rest):
    if rest[i] == "--scope":
        i += 2
    elif rest[i] == "--env":
        key, value = rest[i + 1].split("=", 1)
        env[key] = value
        i += 2
    elif rest[i] == "--":
        positional.extend(rest[i + 1:])
        break
    else:
        positional.append(rest[i])
        i += 1
name = positional[0]
if operation == "add":
    if name in servers:
        print("already exists", file=sys.stderr)
        sys.exit(1)
    servers[name] = {{"type": "stdio", "command": positional[1], "args": positional[2:], "env": env}}
elif operation == "remove":
    servers.pop(name, None)
else:
    sys.exit(2)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data))
''')
    script.chmod(0o755)
