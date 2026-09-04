"""Pure launchd plist renderer for COM-162 service templates."""

from __future__ import annotations

from . import RenderedPlatform
from .common import argv, env_lines, plist_string, render_placeholders


def render(descriptor, ctx) -> RenderedPlatform:
    replacements = ctx.replacements
    rendered_argv = argv(descriptor, replacements)
    env_items = env_lines(descriptor.env, replacements)
    program_arguments = "\n".join(f"\t\t<string>{plist_string(part)}</string>" for part in rendered_argv)
    env_dict = "\n".join(
        f"\t\t<key>{plist_string(item.split('=', 1)[0])}</key>\n\t\t<string>{plist_string(item.split('=', 1)[1])}</string>"
        for item in env_items
    )
    stdout = render_placeholders(descriptor.logs.stdout, replacements)
    stderr = render_placeholders(descriptor.logs.stderr, replacements)
    working_directory = render_placeholders(descriptor.working_directory, replacements)
    text = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>{plist_string(descriptor.label)}</string>
\t<key>ProgramArguments</key>
\t<array>
{program_arguments}
\t</array>
\t<key>WorkingDirectory</key>
\t<string>{plist_string(working_directory)}</string>
\t<key>EnvironmentVariables</key>
\t<dict>
{env_dict}
\t</dict>
\t<key>StandardOutPath</key>
\t<string>{plist_string(stdout)}</string>
\t<key>StandardErrorPath</key>
\t<string>{plist_string(stderr)}</string>
\t<key>RunAtLoad</key>
\t<false/>
\t<key>KeepAlive</key>
\t<false/>
</dict>
</plist>
'''
    return RenderedPlatform("service-template.plist", text)
