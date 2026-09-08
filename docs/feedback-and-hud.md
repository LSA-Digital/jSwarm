# v1.1 candidate: private feedback and optional HUD

These additions are being prepared for v1.1.0. They do not change the delivery loop or require Jira.
They are available from `main`; existing users should follow [Update jSwarm](updating.md)
before trying the new command or enabling the HUD. A published version tag remains separate.

Contract addition: `/jFeedback` is an auxiliary public command, not a lifecycle step.
`/jSettings` is the everyday settings command. It inspects settings without changes
and guides HUD enable/disable through preview, approval, and verification. See the
[everyday controls contract](everyday-controls-contract.md).
`/jUpgrade` is also auxiliary: it checks and upgrades the public main channel with
explicit source-update and installer-preview approvals. It does not upgrade projects
or Enterprise; pinned tags and custom branches stop for manual channel selection.
Personal provider-quota display is included in the public HUD; routing, account collectors,
and enterprise governance remain separate capabilities. The private submission surface is
`/feedback` with a POST-only `/api/feedback` endpoint on jarviswarm.com. There is no public fallback.

## Private feedback

Run `/jFeedback` in Claude Code. It asks what happened, drafts a report, and lets you review it.
Only the jSwarm commit, OS, CPU architecture, Python version, and text you select are included.
No automatic transcript, source-code, credential, or log collection occurs. Recognizable sensitive
strings are redacted locally, but you must still review for confidential information.

The draft stays on your machine until you explicitly approve sending the exact
sanitized payload. When connected, Claude submits it directly and returns a receipt.
It never attaches a transcript or discovers more content automatically. Changing
the report requires a new review and approval. Reports go privately to LSA through
Brevo, not to a public issue tracker or marketing list.

Direct sending requires an LSA-issued feedback credential, saved once through
`/jSettings`. LSA must activate the authenticated intake and durable rate-limit
store first. The one-time connection uses a hidden Terminal prompt; never paste
credentials into chat or use an email-provider/AI-provider key. A saved credential
does not prove server authorization. Missing configuration blocks sending.

The browser fallback is https://jarviswarm.com/feedback: choose the draft, review
it, and click **Send privately to LSA**. Selecting the file does not upload it.
That separate form retains Google reCAPTCHA and an optional reply-email field.
Direct sending does not yet include a separate reply address.

A receipt means the email provider accepted the message; it does not prove inbox
delivery or human review. On uncertainty, keep the draft and report ID and contact
LSA. Do not resend with a new ID or switch to the browser to retry. Local attempt
records block another direct send. Server records keep hashes/status, not report
text, for 30 days; rate limits allow five attempts per sender per one-hour window.

## Optional HUD

Run `/jSettings` in Claude Code and ask to enable the HUD. It shows the preview,
asks for approval, applies the change, and verifies the renderer. If another
status line exists, replacement needs separate approval and its prior value is
saved. Disable through the same command to restore it. Restart Claude afterward.

The initial controls are status, enable, and disable, not layout/color/widget
customization. Settings does not install provider collectors or model routing.

Terminal recovery remains available. From the jSwarm clone, preview before enabling:

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

- Activate direct sending with expiring LSA sender digests, durable rate-limit and
  deduplication storage, and the mail provider. Current implementation fails closed
  until those are configured; mocked email tests do not prove production delivery.
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
