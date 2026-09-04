<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/test/uat/notification-operation.md
     Deploys as a symlink via COM-176 install; edit this master only.
     Manage via /devops-maint dotclaude (mode 37). -->

# Website feedback notification operation

The **parent ticket orchestrator** owns this Monitor. The PREP-only `jTestEngineer` does not own or arm it: `/test uat prepare` is PREP-only and may exit before the owner acts.

After prepare returns all three owner-ready predicates — `ISSUED`, `OWNER-MAY-WALK: yes`, and `invitation=True` — arm the Monitor immediately and **before sending the Option 4 owner invitation**. Option 4 owns composing and sending that invitation; do not treat a prepared round alone as an armed runtime task. If you skip arming, an owner submission cannot wake this idle session; only boundary discovery can find it later, so the owner must send a message or otherwise re-enter the workflow to tell you that feedback exists.

This procedure does not define result vocabulary, entry cells, aliases, or
aggregate verdicts. Use the deployed [feedback result contract](feedback-result-contract.json)
as the authority, and inspect its managed-Python publication and SHA with:

```bash
.venv/bin/python -m jswarm.uat_feedback result-contract --json
```

Use the exact absolute watcher command carried by the active round registration; do not reconstruct paths or flags. The Monitor call is:

```text
Monitor({
  description: f"{ticket} UAT feedback events",
  command: registration["notify_command"],
  persistent: true,
  timeout_ms: 300000,
})
```

The registration's `notify_command` is the non-consuming watcher. Its command has this shape, with the absolute state root and round identifier supplied by the registration:

```sh
exec <repo-root>/.venv/bin/python -u -m jswarm.portal.form_events watch \
  --state-root <absolute-state-root> \
  --round-review-id <round-review-id> \
  --action feedback.send \
  --poll-seconds 2 \
  2>&1
```

#### Canonical wake surface

The canonical wake surface is `.jswarm/plans/<TICKET>/form-events/<key>/events.ndjson`. In this path, `<key>` is the first 16 characters of the SHA-256 digest of the registration's `round_review_id` (normally `<TICKET>/<round-id>`); `form_events.py` derives it rather than accepting a caller-supplied path. For the ticket-owned plan registration, `allowed_repo_root` is the plan root, and its `events_path` points to this log.

An explicit owner submit appends one immutable `feedback.send` row to `events.ndjson` using append-and-fsync. That append is the signal. The supplied `registration["notify_command"]` already runs `jswarm.portal.form_events watch`, which polls this event log for pending `feedback.send` events and emits one non-consuming pending signal per event for `Monitor` to stream. Do not build a second file watcher or substitute the feedback document as the source.

> **WARNING — feedback projection fill-in-place trap:** The v2 feedback `.md` is pre-materialized with its entry blocks. Owner submissions fill or replace those existing blocks in the projection; they do not append the submit event to that document. A watcher keyed on the feedback file growing, or on its mtime as a proxy for “new feedback”, will never fire for the canonical submission signal. Watch the `events.ndjson` log via the supplied `notify_command` instead.

> **WARNING — persistent watcher versus one-shot discovery:** The persistent Monitor command must **not** carry `--once`. Use `registration["notify_command"]` verbatim for the persistent watcher; `--once` belongs only to `registration["notify_once_command"]` for one-shot boundary discovery. If a Monitor exits immediately with no output, the wrong one-shot form was armed — it does **not** mean that the queue is empty, and that Monitor cannot wake later.

`persistent: true` keeps the Monitor alive while this owning session is idle; `timeout_ms: 300000` is the standard Monitor value and is ignored for a persistent task. Keep the returned task id in **ephemeral session execution context only**. Never persist it as proof of a live watcher, and never trust or treat a persisted task id as live in a later session.

#### Wake sequence

When the Monitor emits a signal, follow these steps in order. A signal is only a wake-up; it is not feedback, a continuation result, or a durable state change.

1. **Discover without mutation.** Execute `registration["notify_once_command"]` (the same absolute watcher command with `--once`) to rediscover pending form events. This one-shot path is non-consuming: it does not acquire the delivery lease, invoke a continuation, or write consumer state.
2. **Drain under the consumer lease.** Enter the lease-owned `drain_form_events` path for the round. Production callers must pass the `current_object_sha256` resolver:

   ```python
   drain_form_events(
       state_root,
       round_review_id,
       on_event,
       current_object_sha256=<callable that resolves the current canonical feedback SHA-256>,
   )
   ```

   Construct the resolver from the validated active-round source. `parse_active_round_source` accepts the registration dictionary, approved roots as `Path` objects, and an optional contract size limit. `canonical_feedback_digest_resolver` requires that validated source and a **keyword-only, required** `size_limit` — it has no default:

   ```python
   from pathlib import Path
   from jswarm.portal import server, form_events

   source = server.parse_active_round_source(
       registration_document,
       [Path(p) for p in approved_roots],
       server.DEFAULT_CONTRACT_LIMIT,
   )
   resolver = form_events.canonical_feedback_digest_resolver(
       source,
       size_limit=server.DEFAULT_CONTRACT_LIMIT,
   )
   ```

   Pass `source` to the resolver, not the raw registration dictionary. A raw dictionary fails with `FormEventError: resolver requires a validated active round source`. Use the resulting `resolver` as `current_object_sha256` in `drain_form_events` above. For direct feedback API updates and the completed-step result triples that must survive rendering, see [`docs/tools/fix-decisions/uat-rounds.md`](../../../../../tools/fix-decisions/uat-rounds.md#direct-feedback-api).

   The resolver is mandatory in production. Omitting it silently disables currency checking and can process superseded events as if they were current. This matters when the owner saves twice: two events can have different digests, and only the second event may carry the final answers.
3. **Classify delivery-time currency.** Classify each event as `current|superseded|missing` using the resolver result. Only `current` is eligible for continuation.
4. **Continue the current event.** For `current`, run `test-uat-feedback-process` keyed by the wake signal's `notification_id`. The drained raw event has a distinct durable `event_id`; do not substitute it for `notification_id`. The continuation must produce its durable round/feedback result before this event is treated as handled.
5. **Durably acknowledge only after success.** After the durable continuation result, write the event `acknowledge`/consumed-ledger record. If the continuation fails, leave the event pending and do not acknowledge it.
6. **Tear down the wake task.** Call `TaskStop` with the ephemeral Monitor task id after the durable result and acknowledgement are complete.

For `superseded` or `missing`, record the terminal `disposition` and perform **no continuation**; do not invoke `test-uat-feedback-process`. A superseded event remains useful history, but it must not overwrite the current owner feedback.

Stop the Monitor after durable acknowledgement, round cancellation, supersession, cutover, archive, any other terminal round state, or session exit. Session exit ends the session-local task automatically; no watcher daemon or orphan process is expected to survive.

If the watcher emits `source-error` or completes unexpectedly, wake the session and run exactly one immediate `registration["notify_once_command"]` discovery. Classify the cause, correct or record it, and **re-arm only after classification**; never spin unchanged retries. If the registration is explicitly closed, treat that as terminal rather than retrying.

On resume or ownership transfer, first run one non-consuming one-shot discovery. If the round remains active and pending, arm a fresh Monitor from the current registration. Never trust a persisted old task id. Duplicate Monitors can duplicate wake signals, but the lease and idempotent continuation protect durable processing.

If no Monitor is armed, `SessionStart`, `UserPromptSubmit`, or `/test uat` workflow re-entry runs `registration["notify_once_command"]` for one-shot boundary discovery. This fallback is non-consuming and never acknowledges the event. After discovery, apply the same lease, currency classification, continuation, post-success acknowledgement, and teardown sequence.

Reference: `docs/testing/uat-fix-playbook.md`, `docs/templates/UAT_REPORT_TEMPLATE.md`, and `UAT_CURRENT_ROUND_EXAMPLE.md`.
