---
name: jsetup
description: Guided day-0 front door for a fresh JSWARM (JarviSWARM) clone.
---
<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/jsetup/SKILL.md
     Deploys as a symlink to this master: editing here edits the live source of truth (no deploy step).
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy. -->

# /jsetup

## Safety contract (COM-219 — destructive skill, agent-invocable)

- **Default is read-only / no-write.** Invoked with no args (or `--help`/`status`), this skill only inspects and reports; it performs NO write, apply, push, delete, remote, or trim.
- **Confirm before any mutation.** Before any state-changing mode, the invoker (human or agent) must obtain explicit confirmation — or run an approved preview/dry-run first and act only on that approved plan.
- **Agents are NOT locked out** (no `disable-model-invocation`); this contract — not frontmatter — is what gates writes, so the read-only path is freely usable and a mutation requires approval.


Guided day-0 front door for a fresh JSWARM (JarviSWARM) clone.

```bash
.venv/bin/python -m jswarm.installer.jsetup "$@"
```
