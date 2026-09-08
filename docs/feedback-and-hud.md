# v1.1 candidate: private feedback and optional HUD

These additions are being prepared for v1.1.0. They do not change the delivery loop or require Jira.

Contract addition: `/jFeedback` is an auxiliary public command, not a lifecycle step.
Personal provider-quota display is included in the public HUD; routing, account collectors,
and enterprise governance remain separate capabilities. The private submission surface is
`/feedback` with a POST-only `/api/feedback` endpoint on jarviswarm.com. There is no public fallback.

## Private feedback

Run `/jFeedback` in Claude Code. It asks what happened, drafts a report, and lets you review it.
Only the jSwarm commit, OS, CPU architecture, Python version, and text you select are included.
No automatic transcript, source-code, credential, or log collection occurs. Recognizable sensitive
strings are redacted locally, but you must still review for confidential information.

The draft stays on your machine until you choose it at https://jarviswarm.com/feedback and click
**Send privately to LSA**. Choosing the file only opens it in the browser; submission is separate.
Reports go to LSA by its email delivery provider (Brevo), not a public issue tracker. Google
reCAPTCHA protects submission. A reply email is optional. The receipt means the email provider
accepted the message; it does not prove inbox delivery or human review. No automatic retries.

## Optional HUD

From the jSwarm clone, preview before enabling:

```bash
./install.sh hud enable --dry-run
./install.sh hud enable
```

Restart Claude Code. The HUD shows model, reasoning effort, context use, Git branch, active work
item, plan status, and available progress counts. It reads the current session's binding; it
never chooses another session's newest ticket. Local slugs work without Jira.

Quota windows show percent **used**, not remaining, and a reset countdown. Direct Claude sessions
use the host's native `rate_limits` payload. Claude documents these fields for Pro/Max and
supported gateway sessions after the first response, with Claude Code 2.1.251 or later.
Missing windows are unavailable, not zero. Expired values are not presented as current.

For other providers, the HUD can read session-bound snapshots from
`~/.cache/jswarm/provider-quota/v1` (or `JSWARM_PROVIDER_QUOTA_ROOT`), using the existing
`jswarm.provider-quota-binding.v1` / `jswarm.provider-quota.v1` formats. The producer must supply
an exact session, provider, and account binding. Snapshots older than 150 seconds are labeled
stale. This reader does not install a provider collector, enable routing, or authenticate another
provider. Without a valid binding it must not substitute Claude's quota for a proxy provider.

The bundled renderer uses the existing Python environment, not an npm download on every refresh.
It makes no network requests, reads no tokens/keychain, and does not inspect chat transcripts.
Its design is informed by common's HUD, but the renderer is a new implementation for the public
core's current data sources; no private proxy code is bundled.

An existing status line is left alone unless you explicitly use `--replace-existing`. The previous
`statusLine` value is recorded locally and restored on disable. Other Claude settings are preserved.
Local edits to the installed status line block automatic replacement/removal for inspection.

```bash
./install.sh hud status
./install.sh hud disable --dry-run
./install.sh hud disable
```

Uninstall disables a recorded HUD before deleting installer state. macOS, Linux, and Windows
through WSL2 use the same renderer. Native Windows remains outside jSwarm's supported shell path.

Sources: [Claude statusline contract](https://code.claude.com/docs/en/statusline),
common's F-49 widget source and session/provider-quota contracts.

## Before release

- Deploy the website's private form and endpoint before publishing a core release that links to it.
  The server uses the existing Brevo sender/key and Google reCAPTCHA configuration. Its default
  destination is the existing LSA inbox; an operator can set `JSWARM_FEEDBACK_RECIPIENT` to another
  address inside LSA's domain. The sender cannot choose a destination.
- Send one synthetic report through the real browser form. Confirm provider acceptance, then have
  LSA confirm inbox receipt by report ID. Unit tests mock email delivery; they do not prove this.
- Enable the HUD on the test machine, restart Claude, and compare displayed native quotas with
  the host's usage screen after a response. Test a second session to ensure work items do not bleed.
- If using another provider, confirm its live collector writes a matching session/account binding
  and fresh snapshot. Fixture-driven reader tests do not establish collector availability.
- Preview disable, restore the previous status line, then repeat enable/uninstall. Recheck WSL2
  on an actual Windows machine before treating that environment as manually accepted.
- Version/tag publication remains separate from this candidate implementation.
