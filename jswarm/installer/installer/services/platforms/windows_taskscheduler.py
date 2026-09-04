"""Pure Windows Task Scheduler XML renderer for COM-162."""

from __future__ import annotations

from . import RenderedPlatform
from .common import argv, env_lines, xml


def render(descriptor, ctx) -> RenderedPlatform:
    replacements = ctx.replacements
    rendered_argv = argv(descriptor, replacements)
    command = rendered_argv[0]
    arguments = " ".join(rendered_argv[1:])
    env_comment = "; ".join(env_lines(descriptor.env, replacements))
    text = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <URI>\\{xml(descriptor.label)}</URI>
    <Description>{xml(descriptor.display_name)} - JarviSWARM service-template only</Description>
  </RegistrationInfo>
  <Triggers />
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <Enabled>false</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{xml(command)}</Command>
      <Arguments>{xml(arguments)}</Arguments>
    </Exec>
  </Actions>
  <!-- Required environment placeholders: {xml(env_comment)} -->
</Task>
'''
    return RenderedPlatform("service-template.task.xml", text)
