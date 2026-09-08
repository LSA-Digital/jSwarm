# Everyday controls: v1.1 candidate

This public amendment mirrors the shared split spec's Public interface contract,
v1.1 everyday-controls amendment dated 2026-09-08. It does not change the nine
lifecycle commands or publish v1.1.0.

- `/jUpgrade`: approved main-channel update with exact-commit notes and installer preview.
- `/jSettings`: read-only settings overview, HUD preview, approved enable/disable,
  separate consent to replace a pre-existing status line, and runtime verification.
  Helper: `python -m jswarm.settings`. Default actions never write.
- `/jFeedback`: selected-content draft, exact sanitized payload review, then explicit
  send approval bound to SHA-256. Helper: `python -m jswarm.feedback_send`.
  Direct destination: `https://www.jarviswarm.com/api/feedback-cli`, LSA only.

Direct sending requires an LSA-issued, revocable sender credential. The service
stores only its SHA-256 digest. The client stores a provided credential owner-only,
never prints it, and never places it in command arguments or a report. Approval
does not authorize collecting transcripts, source code, logs, or tracker content.

The authenticated route requires durable rate limits (five attempts per hour per
credential) and report-ID deduplication. Missing configuration or storage failure
blocks sending. Browser CAPTCHA remains unchanged on the separate public form.
Clients never follow redirects with credentials. The client records attempts before
network dispatch and stops after uncertain results instead of retrying automatically.
Provider acceptance, inbox delivery, and human review remain distinct states.

Terminal installer commands remain compatibility and recovery interfaces. No
project adoption, lifecycle action, or optional HUD is triggered by an upgrade.
