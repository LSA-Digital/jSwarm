<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/test/uat/round-registration.md
     Deploys as a symlink via COM-176 install; edit this master only.
     Manage via /devops-maint dotclaude (mode 37). -->

# Put a prepared UAT round in front of the owner

For a `uat-canonical-package@2` round, `/jTest uat prepare` is optional for
issuance. In this practical path, **prepare builds only the QA handoff;
issuance needs only `materialize-validate` (the materializer validate-only check) and owner-authorized cutover**. If a
valid practical-cutover request and accepted evidence already exist, do not run
prepare again just to make the round writable. Run the materializer validate-only
check, then continue below.

This is the owner-authorized front door for practical issuance. Do not edit the
deployed config or invent an `active_round_sources` registration.

## Good-enough rounds — first-class methodology (owner-ruled 2026-08-31, HAS-652)

Owner UAT exists to produce NEW information from owner minutes — experience quality and unpredicted inputs — not to re-prove defects automation already booked. Two binding practices follow; both apply to every ticket's rounds, and Option 4 executes them.

**1. Peel the owner layer off the automation layer.** The owner-facing round carries ONLY user-voice journeys: what a user types, clicks, and sees, organized as a catalog of the kinds of inputs users are predicted to give and the outputs they should get. No mechanism vocabulary (typed refusal codes, ledger/event names, testids) and no failure-mode/adversarial/reload/duplicate-guard probes in owner journeys — those are regression-owned. Every owner journey keeps a traceability mapping to its technical contracts (scenario IDs/GWT in the canonical inventory); the mapping travels with the catalog and is not shown in the round. Test-case engineering continues underneath at its own pace; neither layer waits for the other.

**2. Cut on the green-enough bar.** Issue the round when its headline journeys — the ones the round exists to show — pass end-to-end in user terms. Edge-case reds never hold a cut: they enter the round's disclosed known-items list in plain English with fix status, and the round's qa_status records what was red at issuance (UAT-R5/R7 already require this representation — disclosure is the mechanism that makes early issuance honest). The only cut-blockers are walk-killers: broken auth/build, or a defect the owner would hit on nearly every journey. A red edge case blocks MERGE, not owner UAT; the regression layer owns it. Never hide a red, and never hold an owner behind one.

**3. Deliver with parallel jQATester lanes (owner-ruled 2026-08-31).** Round delivery speed is the priority, so split QA into concurrent jQATester lanes: a **round lane** that walks ONLY the owner round's user-voice journeys — it alone gates the cut, and a green-enough verdict from it cuts the round immediately — and one or more **coverage lanes** that run everything else (technical scenarios, regression-adjacent walks) concurrently; their findings flow into disclosed known-items or the fix loop without delaying issuance. Lanes run **headless by default for speed** (headless satisfies verification per the 2026-07-31 ruling; headed remains available when the developer wants to watch). Multiple concurrent jQATester agents are the norm, with one harness constraint intact: the shared Playwright MCP browser stays a SINGLETON — at most one lane drives it; every additional concurrent lane uses its own browser instance via the project e2e harness CLI or a node-driven Playwright browser. Never two lanes on one browser.

## Validate and cut over

Before cutover, validate the exact request that the acceptance evidence covers.
This is the materializer's read-only v2 check; it writes nothing:

```bash
.venv/bin/python -m jswarm.uat_round_materialize preflight-manifest \
  --request .jswarm/plans/<TICKET>/<TICKET>.uat-practical-cutover-request.json
```

A `VALID:` result is required. `INVALID:` means fix or regenerate the request;
do not hand-edit the sealed round or bypass the check.

### Enforce the result-contract gate

The deployed [feedback result contract](feedback-result-contract.json) is the
single authority for step options, aliases, entry cells, defaults, and aggregate
rules. The exact request must pass current-shape materializer validation against
that contract before issue or explicit reseal. Check its managed-Python
publication before issuing or explicitly resealing a round:

```bash
.venv/bin/python -m jswarm.uat_feedback result-contract --json
```

The CLI-reported SHA must equal both the live API's published contract SHA and
the deployed schema file's SHA. Any mismatch is schema skew: block new issue,
reseal, and owner invitation. Existing sealed rounds without authored options
remain valid and need no reseal; an explicit reseal upgrades the request to the
current shape and therefore changes the sealed identities normally.

### Check the acceptance-evidence digest

The evidence field `request_sha256` is **not** the SHA-256 of the request file's
raw bytes. It is the SHA-256 of the parsed request object serialized as compact,
UTF-8 canonical JSON. Compute it this way:

```bash
.venv/bin/python - .jswarm/plans/<TICKET>/<TICKET>.uat-practical-cutover-request.json <<'PY'
import hashlib
import json
import sys
from pathlib import Path

request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
payload = json.dumps(
    request,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())
PY
```

Compare the output with `request_sha256` in the acceptance-evidence JSON. If
cutover refuses with `acceptance evidence digest does not bind this request`, it
prints the expected digest. Use that value to diagnose request-file drift: restore
the exact accepted request, or repeat acceptance and produce matching evidence.
Do not change the evidence value merely to silence the refusal.

### New distinct-path round: cut over online and verify

A new round with distinct, unclaimed artifact paths is **request-fresh**: leave
the running service up. Cutover writes the registration atomically, and the
service adopts it on the next request without a restart or signal. Replace the
ticket and round values; the cutover creates the handoff, receipt, and ledger.

```bash
cd "${JSWARM_HOME:-$HOME/dev/jswarm}"
TICKET="<TICKET>"
ROUND_ID="<round-id>"
ROUND_REVIEW_ID="$TICKET/$ROUND_ID"
PLAN="$PWD/.jswarm/plans/$TICKET"
CURRENT_ROUND="$PLAN/$TICKET.UAT-CURRENT-ROUND.md"
FEEDBACK="$PLAN/$TICKET.uat-feedback.md"
HANDOFF="$PLAN/$TICKET.uat-practical-handoff.json"
RECEIPT="$PLAN/$TICKET.uat-practical-cutover.receipt.json"
LEDGER="$PLAN/$TICKET.test-traceability.md"
CONFIG="${HOME}/.jswarm/decision-review/config.json"
SERVICE_PID="$PWD/.jswarm/decision-review/service.pid"
ARCHIVE_DIR="$PLAN/uat-round-history/${ROUND_ID}-$(date -u +%Y%m%dT%H%M%SZ)"
REQUEST="$PLAN/$TICKET.uat-practical-cutover-request.json"
EVIDENCE="$PLAN/$TICKET.uat-practical-acceptance-evidence.json"
ERROR_LOG="${JSWARM_HOME:-$HOME/dev/jswarm}/log/decision-review.launchd.error.log"

# 1. Owner-authorized practical cutover. Registration is created atomically.
.venv/bin/python -m jswarm.uat_practical_cutover practical-cutover \
  --authorized-by owner \
  --note "<owner-approved cutover note>" \
  --request "$REQUEST" \
  --acceptance-evidence "$EVIDENCE" \
  --config "$CONFIG" \
  --archive-dir "$ARCHIVE_DIR" \
  --current-round "$CURRENT_ROUND" \
  --feedback "$FEEDBACK" \
  --handoff "$HANDOFF" \
  --receipt "$RECEIPT" \
  --ledger "$LEDGER" \
  --service-pid "$SERVICE_PID" \
  --round-review-id "$ROUND_REVIEW_ID"

# 2. Poll health with a bounded loop. Inspect the error log after the first
#    failed probe; KeepAlive can make "not up yet" and "crash-looping" look
#    identical for minutes.
HEALTH_OK=0
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:8766/api/health; then
    HEALTH_OK=1
    printf '\n'
    break
  fi
  if [ "$attempt" -eq 1 ]; then
    printf 'First health probe failed; current launchd error log follows:\n' >&2
    tail -50 "$ERROR_LOG" >&2 2>/dev/null || true
  fi
  sleep 1
done
if [ "$HEALTH_OK" -ne 1 ]; then
  printf 'decision-review health did not become ready after 30 seconds\n' >&2
  exit 1
fi

# 3. Confirm the API lists this round before sharing anything.
ROUNDS_JSON="$(curl --fail --silent --show-error http://127.0.0.1:8766/api/uat-rounds)"
printf '%s\n' "$ROUNDS_JSON"
case "$ROUNDS_JSON" in
  *"$ROUND_REVIEW_ID"*) ;;
  *) printf 'round %s is absent from GET /api/uat-rounds\n' "$ROUND_REVIEW_ID" >&2; exit 1 ;;
esac

# 4. GET the owner-facing page and require a successful response.
OWNER_URL="http://macstudio-lsa:8765/uat/?round=${TICKET}%2F${ROUND_ID}"
curl --fail --silent --show-error "$OWNER_URL" >/dev/null
printf 'Owner page verified: %s\n' "$OWNER_URL"

# 5. Send OWNER_URL to the owner only after all checks above pass.
```

Cutover registration is automatic: there is no separate config edit,
registration command, or pre-approved root. For a new row,
`allowed_repo_root` is the resolved parent of the current-round artifact; when
replacing an existing matching row, cutover retains that row's resolved root.
In either case, relative artifact paths are validated and contained inside the
registration root. A new distinct-path config write is request-fresh.

## In-place reseal: stop-gated replacement

Replacing an existing package is not a new-round path: stop the service under
explicit operational authority, rerun practical cutover with the same `round_review_id`
and a fresh archive destination, then restart and verify the current identities
and owner URL. The target registration is excluded from its
own path-claim check, but peer path claims remain protected: never reuse a
peer's artifact paths. Before inviting the owner, arm a new Monitor from the
current registration's `notify_command`; do not treat the durable registration
as a live Monitor task.

## First round and cutover refusals

For the first round, do **not** hand-author prior feedback, receipt, or ledger
seed files. If those prior artifacts are absent, cutover archives
`NO PRIOR ARTIFACT\n`, seeds a valid initial traceability ledger, and writes the
new round artifacts. The request, evidence, and destination parent must still
exist.

Cutover preflights the rendered current round, feedback document, traceability
ledger, and active-round registration **before** creating the archive or writing
any live file. These refusal messages identify the boundary that failed:

- `preflight feedback document parse failed: ...` means the generated feedback
  cannot be consumed by the feedback parser; fix the request/package rather than
  editing the deployed feedback file.
- `preflight traceability ledger parse failed: ...` means the generated stamped
  ledger cannot be consumed by the traceability parser; restore a valid prior
  ledger or correct the accepted package inputs.
- `preflight active round registration failed: <code>: <message>` means the
  service would reject the generated current-round/feedback pair, paths, or
  package identity; no registration or artifact write occurred.

A refusal before the archive exists is safe to investigate and retry after the
inputs are corrected. Never bypass it with a manual config edit.

## Silent symptoms

- **Round absent from `GET /api/uat-rounds`:** the service has no live
  registration. The portal renders an empty queue, not an error. Check that
  cutover completed, bootstrap completed, and query the endpoint again.
- **Health probe fails after bootstrap:** inspect
  `${JSWARM_HOME:-$HOME/dev/jswarm}/log/decision-review.launchd.error.log` immediately after the
  first failed probe. Under `KeepAlive`, repeated launches can make a missing
  dependency and a crash loop look the same until the log is checked.
- **Round listed but owner cannot save:** inspect its detail response. A
  `uat-canonical-package@1` round registers and renders, but it is legacy
  read-only; `capabilities.feedback_update` is `false`. Only a v2 round is
  writable. Both this case and an unregistered round can look like a broken
  page, and neither reports itself in the UI.

## Verify the live watcher, not just the registration

A watcher that is dead, or armed on the wrong path, is indistinguishable from an
empty queue. Three incidents in one week traced to this distinction. In one,
an agent had the right round paths and key derivation but no watcher process for
the live round; it was watching the two previous rounds. Another alarm used a
method that could not detect the watcher that did exist. Both checks also
resolved state roots from the canonical plans path, although a worktree-based
ticket roots its events inside the worktree.

The HAS-617 lane contributed this procedure from field use (observed in field
use, 2026-09-01).

### Enumerate mechanisms by path

1. Enumerate every watching mechanism, matching on the **path**, not only the
   process name. A quick candidate search such as `pgrep -fl "form-events"`
   can catch both the canonical `form_events watch` poller and direct
   `tail -F .../form-events/<sha16>/events.ndjson` watchers. It is not a proof
   of absence: also inspect the full command line with `ps auxww` and grep the
   path. A process-name-only search silently misses mechanisms you did not
   think of, including a `tail -F` watcher or a bare zsh polling loop.
2. Tools truncate, and the truncation is invisible in the result. Default `ps`
   output is width-limited; any `cut`, `head`, or `-maxdepth` in the pipeline
   silently discards evidence; `pgrep -fl` has its own display limits and will
   not show a long argv. Use `ps auxww` and grep the full line. Never place a
   width or depth bound before the match.
3. A reported negative must state the bound it was searched within. “No match
   in `ps auxww`” is a checkable claim; “no match” is not. The same rule applies
   to `find` without `-maxdepth` and to naming which paths a probe actually
   reads. Two independently-bounded checks agreeing on a negative is not
   corroboration.

These failures have all occurred in field use:

- `find ... -maxdepth 6` reported a watcher directory absent; it was at depth 8
  inside a git worktree.
- A Playwright check read the wrong `node_modules` and the Python package rather
  than the path the tests actually probe, producing a false “browser tests
  silently skip” claim that survived for hours.
- `ps -eo pid,command | grep | cut -c1-160` reported no watcher for a live
  round; the key was past column 160 in an approximately 800-character argv.
  The process was visible but was glanced past, producing an urgent false alarm
  against a correctly armed round with an owner mid-walk.
- Process-name enumeration was blind to both a `tail -F` watcher and a bare zsh
  polling loop: three process shapes for the same job.

### Resolve and compare the derived path

Read the live round's registration from `config.json` under
`active_round_sources`. Use that row's `allowed_repo_root` as the state root;
never substitute the ticket's canonical plans path. For a worktree-based round,
the root can therefore be:

```text
.claude/worktrees/<wt>/.jswarm/plans/<TICKET>/form-events/...
```

Derive the expected directory and compare every candidate watcher against it:

```text
expected = <state_root>/form-events/<sha256(round_review_id)[:16]>/
```

For the canonical poller, read `--state-root` and `--round-review-id` from the
full process arguments. If the root is relative, resolve it from the process'
working directory, for example:

```bash
lsof -p <pid> | awk '$4=="cwd"'
```

For a tail watcher, compare its argument path directly with `expected`. Any
mismatch is a wrong-path watcher; it will never fire for the live round. These
watchers **poll** and hold no file handle, so `lsof` on `events.ndjson` proves
nothing. That is why the obvious open-file check fails.

### Prove the fire path safely

Never emit into a live round. A synthetic `feedback.send` is indistinguishable
from a real owner action permanently in that round's durable history. Use a
throwaway root instead:

1. Call `arm_form_watch` for the throwaway root and round.
2. Start the one-shot watcher for that root and round, then immediately call
   `emit_form_action_event`. The event must be present before the watcher's
   single poll; wait for the process and require exit `0` **and** a pending
   signal. If the one-shot completed before the emit, run the one-shot again
   after the emit and require the pending signal.
3. Use the throwaway root with no pre-created `events.ndjson` to cover the
   freshly cut round case. The emit must fail closed without a durable source
   registration; a successful emit before `arm_form_watch` is a failed probe.

The command shape for the watcher is the registered one-shot form, with
`--once` added only for this bounded discovery:

```bash
.venv/bin/python -u -m jswarm.portal.form_events watch \
  --state-root "$THROWAWAY_ROOT" \
  --round-review-id "$ROUND_REVIEW_ID" \
  --action feedback.send --poll-seconds 2 --once
```

### Absence-claim discipline

“No watcher found by mechanism X” is never “no watcher exists,” and “a watcher
exists for rounds N-1 and N-2” is never “the LIVE round is watched.” Check the
sha16 derived from the live round's `round_review_id` specifically, and state
the mechanisms, argv width, and path/depth bounds used by every negative claim.

## Before merging a worktree round

Run this exact non-mutating gate before `/jMerge`:

```bash
"${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" \
  "${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/jmerge_form_events_gate.py" \
  --repo-root "$(git rev-parse --show-toplevel)" --ticket "<TICKET>"
```

Exit `0` is clear, including when the directory is absent. Exit `1` names every
untracked or ignored path at risk and prints the exact `git add` or `git add -f`
command to run. Exit `2` means the worktree could not be inspected and blocks
the merge. The gate never stages files itself; rerun it after staging.
