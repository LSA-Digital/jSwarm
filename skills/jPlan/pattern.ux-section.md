## Trigger

A story touches UI: components, pages, navigation, or user-facing feedback.

## Header lines

None.

## Plan sections

## User Experience

> **Delete this section for backend-only work.** Required for any story that touches the UI. Agents implementing this story MUST read this section before writing code and build the UI to match these mockups.

### Entry

How does the user arrive at this feature? [e.g., clicks sidebar nav, redirected after login, lands on URL]

### Journey

| Step | User Action | What User Sees | What Is Interactive | Feedback After Action |
|------|-------------|---------------|--------------------|-----------------------|
| 1 | [action] | [screen state] | [clickable/typeable elements] | [visual feedback] |

### Error States

| Error Scenario | What User Sees | How User Recovers |
|---------------|---------------|-------------------|
| [scenario] | [error display] | [recovery action] |

### Exit

How does the user know they're done? [e.g., success toast, redirected to dashboard, download starts]

### ASCII Mockups

> Include one mockup per distinct screen state. These are the contract between planner and implementer — if the implementation doesn't match the mockup, it's a bug.

```
┌─────────────────────────────────────┐
│  [Screen title / header]            │
├─────────────────────────────────────┤
│                                     │
│  [Main content area]                │
│  [Interactive elements marked: →]   │
│                                     │
│  → [Button label]  → [Input field]  │
│                                     │
├─────────────────────────────────────┤
│  [Footer / status bar]              │
└─────────────────────────────────────┘
```

### UX Acceptance Criteria

> Every UI-touching story must have at least one UX A/C in addition to technical A/C. Format: "User can [verb] [object] and sees [feedback]."

- [ ] **UX-1:** User can see [what] and do [action]
- [ ] **UX-2:** User receives [feedback] after [action]
- [ ] **UX-3:** Screen shows [expected state] after [user action]

## Rules

Every UI-touching story contains entry, journey, error states, exit, ASCII mockups for distinct states, and at least one outcome-based UX A/C. Backend-only work removes this section.

Provenance: `PLAN_TEMPLATE.md` § User Experience; `step-5-assemble-plan.md` § UI-touching stories.