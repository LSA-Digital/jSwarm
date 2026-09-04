"""Pure sysvinit script renderer for COM-162 service templates."""

from __future__ import annotations

from . import RenderedPlatform
from .common import argv, env_lines, render_placeholders, shell_join


def render(descriptor, ctx) -> RenderedPlatform:
    replacements = ctx.replacements
    command = shell_join(argv(descriptor, replacements))
    working_directory = render_placeholders(descriptor.working_directory, replacements)
    stdout = render_placeholders(descriptor.logs.stdout, replacements)
    stderr = render_placeholders(descriptor.logs.stderr, replacements)
    env_exports = "\n".join(f"export {item}" for item in env_lines(descriptor.env, replacements))
    text = f'''#!/bin/sh
### BEGIN INIT INFO
# Provides:          {descriptor.label}
# Required-Start:    $remote_fs $syslog
# Required-Stop:     $remote_fs $syslog
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: {descriptor.display_name}
### END INIT INFO

# JarviSWARM service-template only; review and install manually.
NAME="{descriptor.label}"
DISPLAY_NAME="{descriptor.display_name}"
WORKING_DIRECTORY="{working_directory}"
COMMAND="{command}"
STDOUT_LOG="{stdout}"
STDERR_LOG="{stderr}"
{env_exports}

case "$1" in
  start)
    echo "Template command for $DISPLAY_NAME: cd $WORKING_DIRECTORY && $COMMAND"
    ;;
  stop)
    echo "Template stop hook for $DISPLAY_NAME; implement distro-specific stop behavior manually."
    ;;
  status)
    echo "Template status hook for $DISPLAY_NAME; inspect logs at $STDOUT_LOG and $STDERR_LOG."
    ;;
  *)
    echo "Usage: $0 {{start|stop|status}}"
    exit 2
    ;;
esac
exit 0
'''
    return RenderedPlatform("service-template.init", text)
