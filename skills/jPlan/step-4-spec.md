
# Step 4: Create technical design spec (Standard + Deep only)

## Step 4: Create technical design spec (Standard + Deep only)

Skip for Quick (depth 1) plans and Lite mode. Required only for Standard (depth 2) and Deep (depth 3).

### 4A: Read architecture and design inputs

Search at each level:

**1. Project-level architecture (living docs):**
```bash
ls docs/architecture/ 2>/dev/null && cat docs/architecture/*.md
```
These are the project's permanent architectural decisions, patterns, and constraints.

**2. Feature-level specs (sibling specs for related tickets):**
```bash
find .jswarm/plans -name "*.specs.*.md" -maxdepth 2 2>/dev/null | head -10
```
Read specs for related or parent tickets to ensure consistency.

**3. Existing plan files for related work:**
```bash
find .jswarm/plans -maxdepth 1 -name "TICKET-*.plan.*.md" 2>/dev/null | grep -i "<related_keywords>" | head -5
```

**4. CLAUDE.md / AGENTS.md** for project-specific conventions.

**5. Research artifacts from prior Oracle/research agent work:**
```bash
find .jswarm/plans -maxdepth 2 -name "*.research.*.md" 2>/dev/null | head -10
```

Record everything read in the spec's **Input Documents** section. If no architecture docs exist, note that; it signals the project may need them.

### 4B: Read the technical design spec template

```
Read docs/templates/TECH_DESIGN_SPEC_TEMPLATE.md
```

### 4C: Populate the technical design spec

Write to: `.jswarm/plans/TICKET-XXX/TICKET-XXX.specs.<descriptive>.md`

Populate from:
- User's description, scope, and acceptance criteria (Step 1)
- Codebase context and test search results (Step 3)
- Jira ticket details (Step 2A)

Key sections to populate thoroughly:
- **Technical Context**: current architecture, systems touched, existing tech debt
- **Technical Approach**: proposed solution, key design decisions, component design, data model, API design
- **Domain Rules and Edge Cases**: business rules shaping implementation, edge case handling
- **Non-Functional Technical Requirements**: security approach, performance budget, observability plan, reliability
- **Constraints and Assumptions**: technical constraints and beliefs that may fail
- **Technical Risks**: what could go wrong and specific mitigations
- **Open Technical Questions**: flag unresolved items; mark blocking vs non-blocking

Leave the **Oracle / Advisor Review** section empty; Step 4D populates it.

### 4D: Oracle review

**Deep (depth 3), MANDATORY.** Consult Oracle before writing the plan.
**Standard (depth 2), RECOMMENDED.** Offer Oracle consultation to the user.

Ask user:
```
The technical design spec is ready. Would you like Oracle to review it before planning?

1. jOracle (Recommended): strong reasoning at lower cost. Good for typically complex plans.
2. jOracle at xhigh effort: deepest reasoning available. Use for extremely complex architecture, multi-service designs, or security-critical work.

[1 / 2 / skip (Deep cannot skip)]
```

**Oracle consultation prompt:**

```
Review this technical design spec for TICKET-XXX before planning begins.

[paste full technical design spec content]

Your review MUST cover:
1. Assumptions that may be wrong or unverified
2. Missing scenarios or edge cases
3. Architectural concerns or design risks
4. Contradictions or ambiguity in requirements
5. NFR gaps (security, reliability, observability)
6. Scope issues (too broad, too narrow, missing dependencies)

For each finding: what you found, why it matters, required action (must fix before planning) vs optional improvement, your confidence level.

End with: Proceed / Revise Spec / Split Scope / Defer
```

**After Oracle returns:** Update the technical design spec's **Oracle / Advisor Review** section with Oracle's findings. If Oracle identified required changes:
1. Fix the technical design spec sections Oracle flagged
2. Mark the Oracle Review Status as "Complete"
3. Record Oracle's recommendation

**Persist Oracle output** using the `research-output` skill. Write to `.jswarm/plans/TICKET-XXX/TICKET-XXX.research.oracle-spec-review.md`.
