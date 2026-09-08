"""Owned ColGREP registration, repair, and real stdio readiness checks.

Only launch a recognized jSwarm entry. Never execute or replace an arbitrary
program merely because someone named its MCP registration 'colgrep'.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from jswarm.host import current as current_host

MODULE = "jswarm.colgrep_mcp_server"
TOOLS = {"colgrep_search", "colgrep_list_dev_indices"}
MANAGED_ENV = {"PYTHONPATH", "COLGREP_BIN", "PYTHONDONTWRITEBYTECODE"}


def owned_registration(config: dict, source: Path) -> bool:
    source = source.resolve()
    if config.get("type", "stdio") != "stdio":
        return False
    if config.get("command") != str(source / ".venv/bin/python"):
        return False
    env = config.get("env", {})
    if not isinstance(env, dict) or set(env) - MANAGED_ENV:
        return False
    args = config.get("args", [])
    if args == [str(source / "jswarm/colgrep_mcp_server.py")]:
        return True  # exact legacy entry emitted by this clone's installer
    return args == ["-m", MODULE] and env.get("PYTHONPATH") == str(source)


def desired_registration(source: Path, binary: str) -> dict:
    return {
        "type": "stdio", "command": str(source.resolve() / ".venv/bin/python"),
        "args": ["-m", MODULE],
        "env": {"PYTHONPATH": str(source.resolve()), "COLGREP_BIN": str(Path(binary).absolute())},
    }


def same_registration(actual: dict | None, expected: dict) -> bool:
    return actual is not None and all(actual.get(key, "stdio" if key == "type" else None) == value
                                      for key, value in expected.items())


async def _handshake(config: dict, source: Path, env: dict[str, str], cwd: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if not owned_registration(config, source):
        raise ValueError("unrecognized registration")
    scoped = dict(env)
    scoped.pop("PYTHONPATH", None)
    scoped.update(config.get("env", {}))
    # Verification must not create bytecode in the user's checkout or index it.
    scoped["PYTHONDONTWRITEBYTECODE"] = "1"
    params = StdioServerParameters(command=config["command"], args=config["args"], env=scoped, cwd=cwd)
    with open(os.devnull, "w") as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                if {tool.name for tool in tools.tools} != TOOLS:
                    raise ValueError("unexpected MCP tool set")


def verify_registration(source: Path, env: dict[str, str], *, timeout: float = 15) -> tuple[bool, str]:
    try:
        config = current_host().mcp_registration("colgrep")
        if not config:
            return False, "MCP registration missing"
        if not owned_registration(config, source):
            return False, "MCP registration is not a recognized entry for this clone; inspect it manually"
        if config.get("args") != ["-m", MODULE]:
            return False, "legacy file-path launcher needs repair"
        import shutil
        binary = config.get("env", {}).get("COLGREP_BIN") or env.get("COLGREP_BIN")
        binary = binary or shutil.which("colgrep", path=env.get("PATH", ""))
        if not binary or not Path(binary).is_file() or not os.access(binary, os.X_OK):
            return False, "colgrep CLI missing or not executable"
        asyncio.run(asyncio.wait_for(_handshake(config, source, env, Path(env["HOME"])), timeout))
        return True, "MCP handshake and both tools verified; indexed search not tested"
    except Exception as exc:
        # Do not expose registration environment values, tokens, or raw stderr.
        return False, f"MCP readiness failed ({type(exc).__name__}); inspect the registration and server dependencies"


def ensure_registration(ctx, source: Path, binary: str) -> bool:
    host = current_host()
    expected = desired_registration(source, binary)
    try:
        existing = host.mcp_registration("colgrep")
    except ValueError as exc:
        print(f"  colgrep: {exc}")
        return False
    if not same_registration(existing, expected):
        if existing is not None:
            if not owned_registration(existing, source):
                print("  colgrep: refusing to replace an unrelated or customized MCP registration; inspect it manually")
                return False
            print("  colgrep: repairing this clone's MCP registration")
            removed = ctx.run(host.mcp_remove_argv("colgrep"))
            if removed is not None and removed.returncode:
                print("  colgrep: MCP removal failed; registration and install state need inspection")
                return False
        added = ctx.run(host.mcp_add_argv("colgrep", expected["command"], expected["args"], env=expected["env"]))
        if added is not None and added.returncode:
            print("  colgrep: MCP registration failed; re-run install --with-colgrep after resolving the error")
            return False
        if not ctx.dry_run:
            try:
                saved = host.mcp_registration("colgrep")
            except ValueError:
                saved = None
            if not same_registration(saved, expected):
                print("  colgrep: MCP registration was not saved as requested")
                return False
    else:
        print("  colgrep: MCP registration already correct")
    if ctx.dry_run:
        print("  colgrep: would verify MCP handshake and both tools (no index build)")
        return True
    ready, detail = verify_registration(source, ctx.env())
    print(f"  colgrep: {detail}")
    return ready
