# Monitor operation

## Website action notification operation

The `/jFix` workflow owner owns the website-action Monitor. Arm it only on approval-matrix branches that stop for explicit developer approval: `developer-direct` and delegated `require_contract_approval`. A delegated run with `none` records orchestrator approval and continues; **delegated `none` does not arm** an owner-action watcher.

### Publication-to-wait boundary

After durable publication returns `handoff.json`, arm the Monitor immediately and **before presenting or waiting for approval**. Do not arm before the handoff exists, and do not arm on the delegated-`none` path.

Use the exact absolute watcher command carried by the handoff; do not reconstruct paths or flags. The Monitor call is:

```text
Monitor({
  description: f"{ticket} FIX approval events",
  command: handoff["consumer_notify_command"],
  persistent: true,
  timeout_ms: 300000,
})
```

The handoff's `consumer_notify_command` is the non-consuming watcher, and its one-shot counterpart is `consumer_notify_once_command`. Use the one-shot field for boundary discovery; do not substitute the consuming command. The watcher's command has this shape, with the absolute paths and approved contract value supplied by the handoff:

```sh
exec <repo-root>/.venv/bin/python -u -m jswarm.portal.consumer \
  --receipts <absolute-receipt-directory> \
  --threads <absolute-threads-directory> \
  --state <absolute-consumer-state-directory> \
  --notify-only \
  --kind decision.approve \
  --expected-digest <approved-publication-contract-sha256> \
  --poll-seconds 2 \
  2>&1
```

> **WARNING — persistent watcher versus one-shot discovery:** The persistent Monitor command must **not** carry `--once`. Use `handoff["consumer_notify_command"]` verbatim for the persistent watcher; `--once` belongs only to `handoff["consumer_notify_once_command"]` for one-shot boundary discovery. If a Monitor exits immediately with no output, the wrong one-shot form was armed — it does **not** mean that the queue is empty, and that Monitor cannot wake later.

The publisher emits both notification fields in `handoff.json`; use `consumer_notify_command` and `consumer_notify_once_command` verbatim. Do not run the unmodified consuming command as a Monitor source: the notification fields exist specifically to prevent a Monitor from leasing or consuming the event stream.

`--notify-only` is essential: the watcher emits a stdout signal but does not lease, consume, run a lifecycle callback, or change the ledger. `persistent: true` keeps the Monitor alive while this owning session is idle; `timeout_ms: 300000` is the standard Monitor value and is ignored for a persistent task. Keep the returned task id in **ephemeral session execution context only**. Never persist it as proof of a live watcher, and never trust or treat a persisted task id as live in a later session.

### Wake sequence

When the Monitor emits a signal, follow these steps in order. A signal is only a wake-up; it is not a decision or a durable state change.

1. **Discover without mutation.** Execute `handoff["consumer_notify_once_command"]` (the same absolute `--notify-only` command with `--once`) to rediscover the pending receipt. This one-shot path must not acquire the consuming lease or alter the source.
2. **Validate the current receipt and digest.** Read the matching receipt and compare its contract `digest` with the currently issued contract. A stale receipt authorizes no gate change.
3. **Consume under lease and continue.** Invoke the existing leased `handoff["consumer_once_command"]` path for the matching receipt, then run the mapped `fix-decision-apply` continuation. The continuation must be idempotent and must make its durable effect before this event is treated as handled.
4. **Durably acknowledge only after success.** Allow the source consumed-ledger write only after `fix-decision-apply` has returned its durable result. If the continuation fails, leave the receipt pending and do not mark it consumed.
5. **Tear down the wake task.** Call `TaskStop` with the ephemeral Monitor task id after the durable result and acknowledgement are complete.

### Teardown, failure, and handoff

Stop the Monitor after durable acknowledgement, denial, cancellation, publication supersedence, contract revision, any other gate closure, or session exit. Session exit ends the session-local task automatically; no watcher daemon or orphan process is expected to survive.

If the watcher emits `source-error` or completes unexpectedly, wake the session and run exactly one immediate `consumer_notify_once_command` discovery. Classify the cause, correct or record it, and **re-arm only after classification**; never spin unchanged retries. If the source is explicitly closed, treat that as terminal rather than retrying.

On resume or ownership transfer, first run one non-consuming one-shot discovery. If the publication remains active and a receipt is pending, arm a fresh Monitor from the current handoff. Never trust a persisted old task id. Duplicate Monitors can duplicate wake signals, but the leased consumer and idempotent continuation protect durable processing.

If no Monitor is armed, the durable receipt is still discovered at `SessionStart`, `UserPromptSubmit`, or `/jFix` workflow re-entry by running `consumer_notify_once_command`. This boundary fallback is non-consuming: it never acknowledges the receipt. After discovery, apply the same validation, leased continuation, post-success acknowledgement, and teardown sequence.

