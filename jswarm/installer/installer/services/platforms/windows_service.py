"""Pure Windows service PowerShell template renderer for COM-162."""

from __future__ import annotations

from . import RenderedPlatform
from .common import argv, env_lines, ps_quote, shell_join


def render(descriptor, ctx) -> RenderedPlatform:
    replacements = ctx.replacements
    rendered_argv = argv(descriptor, replacements)
    command = shell_join(rendered_argv)
    env_notes = "\n".join(f"#   {item}" for item in env_lines(descriptor.env, replacements))
    text = f'''# JarviSWARM template only: review this generated PowerShell before manual deployment.
# JarviSWARM template only; it did not run New-Service, sc.exe, NSSM, or PowerShell service commands.
$ServiceName = {ps_quote(descriptor.label)}
$DisplayName = {ps_quote(descriptor.display_name)}
$BinaryPathName = {ps_quote(command)}

# Required environment placeholders for the developer to supply before deployment:
{env_notes}

# Manual SCM option (copy-paste only):
New-Service -Name $ServiceName -DisplayName $DisplayName -BinaryPathName $BinaryPathName -StartupType Manual

# Manual NSSM option if your environment standardizes on NSSM (copy-paste only):
# nssm install $ServiceName $BinaryPathName
'''
    return RenderedPlatform("service-template.windows-service.ps1", text)
