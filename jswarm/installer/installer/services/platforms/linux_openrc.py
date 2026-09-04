"""Pure OpenRC script renderer for COM-162 service templates."""

from __future__ import annotations

from . import RenderedPlatform
from .common import argv, env_lines, render_placeholders, shell_join


def render(descriptor, ctx) -> RenderedPlatform:
    replacements = ctx.replacements
    rendered_argv = argv(descriptor, replacements)
    command = rendered_argv[0]
    command_args = shell_join(rendered_argv[1:])
    working_directory = render_placeholders(descriptor.working_directory, replacements)
    stdout = render_placeholders(descriptor.logs.stdout, replacements)
    stderr = render_placeholders(descriptor.logs.stderr, replacements)
    env_exports = "\n".join(f"export {item}" for item in env_lines(descriptor.env, replacements))
    text = f'''#!/sbin/openrc-run
# JarviSWARM service-template only; review and install manually.

name="{descriptor.display_name}"
description="{descriptor.display_name}"
command="{command}"
command_args="{command_args}"
directory="{working_directory}"
output_log="{stdout}"
error_log="{stderr}"
command_background="yes"
pidfile="{{{{JARVISWARM_LOG_DIR}}}}/{descriptor.service_id}.pid"

{env_exports}

depend() {{
    need net
}}
'''
    return RenderedPlatform("service-template.openrc", text)
