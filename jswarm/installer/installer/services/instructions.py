"""Inert copy-paste instructions for COM-162 service-template renders."""

from __future__ import annotations

from pathlib import PurePosixPath

SAFETY_FOOTER = (
    "JarviSWARM generated this template and these instructions only.\n"
    "JarviSWARM did not install, load, start, enable, stop, disable, or remove any host service.\n"
    "Review the template and run the commands manually if you choose to deploy it.\n"
)


def _output_path(ctx, file_name: str) -> str:
    return str(PurePosixPath("{{JARVISWARM_ROOT}}") / "services" / ctx.service_id / ctx.platform_key / file_name)


def _env_placeholders(descriptor) -> str:
    rows = [f"- `{name}`: `{value}`" for name, value in sorted(descriptor.env.items())]
    if descriptor.ports:
        rows.extend(f"- `{port.env}`: `{port.default}`" for port in descriptor.ports)
    return "\n".join(rows) if rows else "- None"


def _body(descriptor, platform_key: str, ctx, section: str, commands: str) -> str:
    return (
        f"# {section} instructions\n\n"
        f"Service ID: `{descriptor.service_id}`\n\n"
        f"Platform key: `{platform_key}`\n\n"
        f"Output path: `{_output_path(ctx, ctx.artifact_name)}`\n\n"
        "Required env placeholders:\n"
        f"{_env_placeholders(descriptor)}\n\n"
        f"{section} commands (copy-paste text only):\n\n"
        "```sh\n"
        f"{commands.rstrip()}\n"
        "```\n\n"
        "Leak-gate result: pass before write; fail closes with no durable service output.\n\n"
        f"{SAFETY_FOOTER}"
    )


def _commands(descriptor, ctx) -> tuple[str, str, str]:
    label = descriptor.label
    artifact = _output_path(ctx, ctx.artifact_name)
    if ctx.platform_key == "darwin-launchd":
        dest = f"~/Library/LaunchAgents/{label}.plist"
        return (
            f"mkdir -p ~/Library/LaunchAgents\ncp {artifact} {dest}\nlaunchctl bootstrap \"gui/$(id -u)\" {dest}",
            f"launchctl print \"gui/$(id -u)/{label}\"\n# Inspect logs under {{{{JARVISWARM_LOG_DIR}}}}",
            f"launchctl bootout \"gui/$(id -u)\" {dest}\nrm -f {dest}",
        )
    if ctx.platform_key == "linux-systemd":
        dest = f"~/.config/systemd/user/{label}.service"
        return (
            f"mkdir -p ~/.config/systemd/user\ncp {artifact} {dest}\nsystemctl --user daemon-reload && systemctl --user enable --now {label}",
            f"systemctl --user status {label}\njournalctl --user -u {label}",
            f"systemctl --user disable --now {label}\nrm -f {dest}\nsystemctl --user daemon-reload",
        )
    if ctx.platform_key == "linux-sysvinit":
        dest = f"/etc/init.d/{label}"
        return (
            f"sudo cp {artifact} {dest}\nsudo chmod +x {dest}\n# Register with update-rc.d or chkconfig according to your distribution policy.",
            f"service {label} status\n# Inspect logs under {{{{JARVISWARM_LOG_DIR}}}}",
            f"service {label} stop\n# Unregister with update-rc.d/chkconfig according to your distribution policy.\nsudo rm -f {dest}",
        )
    if ctx.platform_key == "linux-openrc":
        dest = f"/etc/init.d/{label}"
        return (
            f"sudo cp {artifact} {dest}\nsudo chmod +x {dest}\nrc-update add {label} default",
            f"rc-service {label} status\n# Inspect logs under {{{{JARVISWARM_LOG_DIR}}}}",
            f"rc-service {label} stop\nrc-update del {label} default\nsudo rm -f {dest}",
        )
    if ctx.platform_key == "windows-service":
        return (
            f"# Review {artifact}, then run its New-Service or NSSM commands manually in an elevated PowerShell session.",
            f"Get-Service {label}\n# Inspect Event Viewer and logs under {{{{JARVISWARM_LOG_DIR}}}}",
            f"Stop-Service {label}\nsc.exe delete {label}",
        )
    if ctx.platform_key == "windows-taskscheduler":
        return (
            f"schtasks /Create /XML {artifact} /TN {label}",
            f"schtasks /Query /TN {label}\n# Inspect logs under {{{{JARVISWARM_LOG_DIR}}}}",
            f"schtasks /Delete /TN {label} /F",
        )
    raise ValueError(f"unsupported service-template platform: {ctx.platform_key}")


def render_instructions(descriptor, platform_key: str, ctx) -> dict[str, str]:
    """Return inert deploy/verify/teardown instruction text for a platform."""

    deploy, verify, teardown = _commands(descriptor, ctx)
    return {
        "deploy": _body(descriptor, platform_key, ctx, "Deploy", deploy),
        "verify": _body(descriptor, platform_key, ctx, "Verify", verify),
        "teardown": _body(descriptor, platform_key, ctx, "Teardown", teardown),
    }
