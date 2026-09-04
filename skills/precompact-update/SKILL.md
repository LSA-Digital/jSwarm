---
name: precompact-update
description: Symlink to the global /precompact-update command that authors global, project-local, or ticket-local precompact include rules from natural-language instructions.
---

# /precompact-update — Scope-Aware Precompact Include Authoring

Author or update precompact standards in the correct include layer from natural-language developer instructions. This is a global command: it can run from any project repo so developers can maintain global, project-local, or ticket-local precompact rules without weakening the canonical `/jPrecompact` safety contract.

**Source of truth:**
- Global command to extend safely: `~/.claude/skills/jPrecompact/SKILL.md`
- Project include target: `.claude/precompact.local.md`
- Ticket include target: `.jswarm/plans/<TICKET-KEY>/.precompact.md`
- Safety boundary from `/jPrecompact` §`### 1c: Local precompact include resolution`: local includes must not weaken safety, canonical retro, or plan-maintenance requirements.
- When a local include incorporates project/auto-memory items, distill each to a ONE-LINE directive; never paste memory bodies verbatim.

## Instructions

When invoked, ALWAYS show this menu first. Do NOT skip the menu. Wait for the developer to pick a scope and provide natural-language instructions.

```
Precompact Update Console
─────────────────────────
  1. global   — Propose updates to ~/.claude/skills/jPrecompact/SKILL.md under staged-cutover
  2. project  — Write/update .claude/precompact.local.md in the current project root
  3. ticket   — Write/update .jswarm/plans/<TICKET-KEY>/.precompact.md in the current project
  4. inspect  — Show current global/project/ticket precompact layers without editing

Pick (1-4):
```

After the scope is selected, ask for the developer's natural-language instructions. Translate those instructions into the selected include layer. Keep edits narrow, operational, and compatible with `/jPrecompact`'s local include resolution semantics.

---

## Option 1: global — Staged Cutover for `~/.claude/skills/jPrecompact/SKILL.md`

Use this option only when the developer wants to change the canonical global `/jPrecompact` command itself.

**Step 1: Read current state**

Read `~/.claude/skills/jPrecompact/SKILL.md` from disk. Never rely on memory or cached command text.

**Step 2: Create backup first**

Before drafting the irreversible global edit, create a timestamped backup:

```bash
mkdir -p ~/.claude/skills/jPrecompact/.backups/<ts>
cp ~/.claude/skills/jPrecompact/SKILL.md ~/.claude/skills/jPrecompact/.backups/<ts>/SKILL.md.bak
```

Record the backup path in the proposed change summary.

**Step 3: Draft the proposed update**

Apply the developer's natural-language instructions to a proposed replacement for `~/.claude/skills/jPrecompact/SKILL.md`. Preserve existing lifecycle contracts unless the user explicitly authorized a stronger rule. Do not introduce `scope: common-only`.

**Step 4: Run jCritic review gate (xhigh effort)**

Before any live global edit, run or request a `jCritic` review (xhigh effort) of the proposed diff. The review must check:
- The proposed global edit preserves the non-weakenable safety boundary.
- It does not break canonical retro handling, plan maintenance, evidence, commit, or no-source-loss requirements.
- It remains compatible with local include layering: global → project → ticket.

**Step 5: Present reviewed diff for explicit user apply**

Show the reviewed diff between the current file and the proposed global command. Ask for explicit user apply before editing the live `~/.claude/skills/jPrecompact/SKILL.md` file. Do not auto-apply the global edit. If the user does not explicitly approve applying the reviewed diff, stop after presenting the proposal and backup path.

**Step 6: Apply only after approval**

Only after explicit user apply, write the approved diff to `~/.claude/skills/jPrecompact/SKILL.md`. Then show the final diff and any verification requested by the user.

---

## Option 2: project — `.claude/precompact.local.md`

Use this option for project-wide standards that should apply to every `/jPrecompact` run in the current project.

**Step 1: Confirm project root**

Identify the current project root. The target path is `.claude/precompact.local.md` relative to that root.

**Step 2: Read target from disk**

Always read `.claude/precompact.local.md` if it exists. If it does not exist, treat the current content as empty but still show that the file is new.

**Step 3: Translate instructions into project-local standards**

Convert the developer's natural-language instructions into concise Markdown appropriate for a project-local precompact include. The include may add project-specific required reading, evidence requirements, artifact conventions, local templates, or stricter checkpoint surfaces. If incorporating project/auto-memory items, distill each memory to a one-line directive; never paste memory bodies verbatim.

**Step 4: Confirm diff before write**

Show the diff from the current on-disk content to the proposed `.claude/precompact.local.md`. Ask for confirmation before writing. Do not write if the developer declines.

**Step 5: Write after confirmation**

After confirmation, create or update `.claude/precompact.local.md`. Report the final path and summarize the standards added or changed.

---

## Option 3: ticket — `.jswarm/plans/<TICKET-KEY>/.precompact.md`

Use this option for standards that apply only to one ticket's checkpoint flow.

**Step 1: Resolve ticket key**

Ask for a ticket key if not provided. Use the fixed target path `.jswarm/plans/<TICKET-KEY>/.precompact.md` in the current project. The dotfile name is canonical and must not be key-prefixed or moved elsewhere.

**Step 2: Read target from disk**

Always read `.jswarm/plans/<TICKET-KEY>/.precompact.md` if it exists. If it does not exist, treat the current content as empty but still show that the file is new.

**Step 3: Translate instructions into ticket-local standards**

Convert the developer's natural-language instructions into concise Markdown appropriate for a ticket-local precompact include. The include may add ticket-specific evidence, required reading, artifact update expectations, local status-row requirements, or stricter checkpoint rules. If incorporating project/auto-memory items, distill each memory to a one-line directive; never paste memory bodies verbatim.

**Step 4: Confirm diff before write**

Show the diff from the current on-disk content to the proposed `.jswarm/plans/<TICKET-KEY>/.precompact.md`. Ask for confirmation before writing. Do not write if the developer declines.

**Step 5: Write after confirmation**

After confirmation, create or update `.jswarm/plans/<TICKET-KEY>/.precompact.md`. Report the final path and summarize the standards added or changed.

---

## Option 4: inspect — Show Current Layers

Use this option when the developer wants to understand the effective include stack before making changes.

**Step 1:** Read `~/.claude/skills/jPrecompact/SKILL.md` and summarize the global include-resolution contract.

**Step 2:** Read `.claude/precompact.local.md` if present; otherwise report it as absent.

**Step 3:** If a ticket key is provided or detected, read `.jswarm/plans/<TICKET-KEY>/.precompact.md` if present; otherwise report it as absent.

**Step 4:** Summarize effective precedence as global → project → ticket. Do not edit files in inspect mode.

---

## Key Rules

- ALWAYS show the menu first — never auto-select a scope.
- ALWAYS take the developer's natural-language instructions before drafting an update.
- ALWAYS read the target file from disk before proposing changes.
- ALWAYS confirm a diff before writing project or ticket include files.
- For global scope, ALWAYS use staged-cutover: backup first, `jCritic` review gate (xhigh effort), then reviewed diff for explicit user apply.
- NEVER auto-apply a live global edit to `~/.claude/skills/jPrecompact/SKILL.md`.
- NEVER add `scope: common-only`; `/precompact-update` is a global command.
- Local includes must not weaken safety, canonical retro, or plan-maintenance requirements. They also must not weaken evidence, commit, no-source-loss, absolute worktree-safe symlink, or final `/jClose` obligations.
- Preserve `/jPrecompact` layering semantics: global → project → ticket; additive standards append; later layers may override ordinary operational defaults only when they do not weaken the safety boundary.
- When incorporating project/auto-memory items into any include layer, distill each to a one-line directive; never paste memory bodies verbatim.
- Use `.jswarm/plans/<TICKET-KEY>/.precompact.md` for ticket scope. Do not use `.jswarm/plans/<TICKET-KEY>/<TICKET-KEY>.precompact.md` or any other ticket-local filename.

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-06-06 | | Added one-line distillation rule for project/auto-memory items incorporated into local precompact includes; memory bodies must not be pasted verbatim. |
| 2026-06-05 | Claude | Initial global `/precompact-update` command for scope-aware precompact layer authoring, staged-cutover global edits, project/ticket include writes, and non-weakenable safety boundary restatement. |
