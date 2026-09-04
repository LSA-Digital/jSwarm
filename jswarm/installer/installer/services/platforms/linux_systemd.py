"""Pure systemd unit renderer for COM-162 service templates."""

from __future__ import annotations

from . import RenderedPlatform
from .common import argv, env_lines, render_placeholders, shell_join


def _systemd_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render(descriptor, ctx) -> RenderedPlatform:
    replacements = ctx.replacements
    command = shell_join(argv(descriptor, replacements))
    working_directory = render_placeholders(descriptor.working_directory, replacements)
    stdout = render_placeholders(descriptor.logs.stdout, replacements)
    stderr = render_placeholders(descriptor.logs.stderr, replacements)
    env_text = "\n".join(f"Environment={_systemd_escape(item)}" for item in env_lines(descriptor.env, replacements))
    text = f'''[Unit]
Description={descriptor.display_name}
Documentation=https://jarviswarm.local/service-template

[Service]
Type=simple
WorkingDirectory={working_directory}
ExecStart={command}
{env_text}
StandardOutput=append:{stdout}
StandardError=append:{stderr}
Restart=no

[Install]
WantedBy=default.target
'''
    return RenderedPlatform("service-template.service", text)
