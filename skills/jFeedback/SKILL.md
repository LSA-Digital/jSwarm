---
name: jFeedback
description: Prepare a private report about jSwarm for LSA. Never uploads conversations automatically.
disable-model-invocation: true
---

# /jFeedback

Help the user report a jSwarm problem, confusing instruction, or improvement idea.
This is product feedback, not /jUAT application feedback. Jira and adoption are not required.

1. Ask once: what went wrong, what they expected, and the steps or command involved.
   Reuse details the user has already provided. Do not diagnose by changing their project.
2. Draft a concise report. Include only user-selected information. Do not read chat history,
   credentials, environment dumps, source files, Jira issues, or logs automatically. Offer a short
   conversation excerpt only if helpful, then ask permission for that exact excerpt.
3. Show the report before writing. Explain that the helper adds only the jSwarm commit, OS,
   CPU architecture, and Python version. It redacts recognizable paths, emails, and tokens;
   redaction cannot detect every secret or proprietary detail. Never claim it can.
4. After approval, write a temporary JSON input containing text fields `summary`, `expected`,
   `actual`, `steps`, `command`, and optional `excerpt`. The first three are required. Use a
   newly created private temporary directory outside the application repository. Do not put
   the user's report in Git, pass report text in shell arguments, or overwrite an existing file.
5. Run this helper from any folder, substituting the approved input and a new output path:

```bash
PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.feedback --input <input.json> --output <draft.json>
```

6. Show the sanitized draft. Give the user its file path and https://jarviswarm.com/feedback.
   The user chooses the file, reviews/edits it, and clicks **Send privately to LSA**. Selecting
   the file does not upload it. No public GitHub issue is created. The form uses LSA's email
   delivery provider and Google reCAPTCHA; an optional reply email is entered separately.
7. Stop at the human submission step. Never bypass the form's approval or anti-abuse checks.
   A saved draft or open browser is **not sent**. Only the form's receipt confirms email-provider
   acceptance, not inbox delivery or that a human read it. Do not automatically retry uncertain
   submissions. Users may keep the draft offline or delete it; do not delete their source logs.
