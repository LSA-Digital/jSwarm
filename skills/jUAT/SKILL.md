---
name: jUAT
description: Issue a UAT round to the local review portal for a human to walk, and author the scenarios a round needs first.
---

# /jUAT: Issue a UAT Round

## Safety contract

- **Default is read-only / no-write.** Invoked with no args, `--help`, or `status`, this skill only inspects and reports; it performs no write.
- **Confirm before any mutation.** Issuing a round (Step 4) replaces the live registration the portal serves. Run it once `/jTest` has passed and the round content is ready to show a person.

## Usage

```
/jUAT                  # issue a round for the current work item
/jUAT <work-item>       # explicit tracker key (PS-14) or slug (add-csv-export)
/jUAT author            # author or update a scenario; do not issue a round
```

## Step 1: Resolve the work item

Same resolution `/jClose` uses: an explicit argument parsed with `jswarm.workitem.identity.parse`; otherwise the current branch against `feat/<id>`, then the single most recently modified `.jswarm/work/*/state.json`. If neither resolves, ask which work item this is.

## Step 2: Make sure scenarios exist

Look for the canonical UAT scenarios this work item's plan names. If none exist yet, or `/jUAT author` was passed, invoke the **uat-author-scenario** skill to author (or update) one; it previews before writing and generates the scenario's test stubs. Once at least one scenario is authored, continue; if the authoring skill ended the turn waiting on approval, type `/jUAT` again once it is applied.

## Extension steps

Run `PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.ext jUAT`. For each path printed, in order, read the file and carry out its steps here before continuing. If nothing is printed, continue.

## Step 3: Build and validate the round request

A round is `uat-canonical-package@2`: journeys hold **scenarios** (the GWT the Step 2 scenarios describe) and **steps** (what the person does in the app, each linking back by `gwt_ref`/`sha256` to the scenario clauses it exercises). Write `.jswarm/work/<ID>/uat-round/<ID>.uat-round-request.json`, one journey per user-facing thread this work item changed:

```json
{
  "schema": "jswarm.test-uat.practical-cutover-request/v1",
  "schema_version": "1.0",
  "ticket": "<ID>",
  "canonical_manifest": {
    "schema_version": "uat-canonical-package@2",
    "ticket": "<ID>",
    "round_id": "round-1",
    "certified_build_hash": "<git rev-parse HEAD>",
    "folder_path": "N/A",
    "app_url": "<where the app runs, e.g. http://localhost:3000>",
    "login": "<how to sign in, or \"no login required\">",
    "observer": {"available": false, "capture": "N/A", "fallback": "<who narrates what they saw>"},
    "recovery_policy": "SETUP_REFRESH_ONLY; POST_ACTION_REFRESH_RELOAD_RETRY_CANNOT_PASS_ORIGINAL_ATOM",
    "known_sources_checked": ["<what you checked before writing this>"],
    "journeys": [{
      "journey_id": "<id>", "title": "Journey 1: <name>", "source": "manual", "uat_test_anchor": "N/A",
      "app_link": {"href": "/", "label": "App"}, "requirement_ref": "N/A", "known_items": [],
      "atom_ids": ["<atom-id>"],
      "outcomes": [{"atom_id": "<atom-id>", "expected": "<one sentence>", "fail_if": ["<what would mean it failed>"]}],
      "scenarios": [{
        "scenario_id": "<id>", "title": "<name>",
        "gwt": [{"gwt_ref": "<sha256, below>", "sha256": "<same>", "given": ["clause"], "when": ["clause"], "then": ["clause"]}]
      }],
      "steps": [{
        "step_id": "<id>", "ordinal": 1, "name": "<short description, not the ordinal>",
        "instruction": "<what the person does>",
        "expected_outcome": "<one sentence>\n- <observable, if any>",
        "scenario_links": [{"scenario_id": "<id>", "gwt_refs": ["<sha256>"]}],
        "assessment_options": ["PASS", "FAIL", "BLOCKED", "NOT_OBSERVED"],
        "app_link": {"href": "/", "label": "App"}
      }]
    }]
  }
}
```

`gwt_ref` and `sha256` are the digest of the exact clause arrays, and must be recomputed after any wording change:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python - <<'PY'
import hashlib, json
given, when, then = ["clause"], ["clause"], ["clause"]  # the exact arrays above
payload = json.dumps({"given": given, "when": when, "then": then}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())
PY
```

Validate before writing anything further; this writes nothing and reports every problem at once:

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python ${JSWARM_HOME:-$HOME/dev/jswarm}/jswarm/uat_round_materialize.py preflight-manifest \
  --request .jswarm/work/<ID>/uat-round/<ID>.uat-round-request.json
```

Fix and re-run until it prints `VALID:`.

## Step 4: Issue the round

Record that this content is ready to show a person, at `.jswarm/work/<ID>/uat-round/<ID>.uat-acceptance-evidence.json`. This is the agent's own sign-off after `/jTest`'s smoke walk, not the human owner's verdict; the human's verdict is what walking the round in the portal produces.

```json
{
  "schema": "jswarm.test-uat.practical-acceptance-evidence/v1",
  "schema_version": "1.0",
  "round_review_id": "<ID>/round-1",
  "verdict": "ACCEPTED",
  "accepted_by": "ticket-boss",
  "accepted_at": "<UTC ISO-8601>",
  "journey_step_counts": {"<journey-id>": "<step count>"},
  "step_counts": {"total": "<sum of the above>"},
  "request_sha256": "<sha256 of the request file's parsed JSON, canonical form, below>"
}
```

```bash
${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python - .jswarm/work/<ID>/uat-round/<ID>.uat-round-request.json <<'PY'
import hashlib, json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
payload = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())
PY
```

Then cut over. This renders and writes the round, feedback shell, handoff, receipt, and traceability ledger, and registers the round with the portal, atomically:

```bash
TICKET="<ID>"; ROUND_REVIEW_ID="$TICKET/round-1"
WORK=".jswarm/work/$TICKET/uat-round"
CONFIG="${HOME}/.jswarm/decision-review/config.json"
PYTHONPATH="${JSWARM_HOME:-$HOME/dev/jswarm}" "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" -m jswarm.uat_practical_cutover practical-cutover \
  --authorized-by ticket-boss \
  --note "issued via /jUAT" \
  --request "$WORK/$TICKET.uat-round-request.json" \
  --acceptance-evidence "$WORK/$TICKET.uat-acceptance-evidence.json" \
  --config "$CONFIG" \
  --archive-dir "$WORK/history/$(date -u +%Y%m%dT%H%M%SZ)" \
  --current-round "$WORK/$TICKET.UAT-CURRENT-ROUND.md" \
  --feedback "$WORK/$TICKET.uat-feedback.md" \
  --handoff "$WORK/$TICKET.uat-handoff.json" \
  --receipt "$WORK/$TICKET.uat-cutover-receipt.json" \
  --ledger "$WORK/$TICKET.uat-traceability.md" \
  --service-pid "${HOME}/.jswarm/decision-review/service.pid" \
  --round-review-id "$ROUND_REVIEW_ID"
```

**Reissuing after `/jFix`:** cutover refuses to replace a round the portal is currently serving. Stop it first (`./install.sh portal --stop` from the jSwarm clone), rerun the block above with the same `$ROUND_REVIEW_ID`, then restart (`./install.sh portal --background`).

Confirm the portal is up and the round is listed before telling anyone to look:

```bash
curl --fail --silent --show-error http://127.0.0.1:8766/api/health >/dev/null
curl --fail --silent --show-error http://127.0.0.1:8766/api/uat-rounds | grep -q "$ROUND_REVIEW_ID" || echo "round missing from /api/uat-rounds"
```

If the portal is not running yet, start it from the jSwarm clone: `./install.sh portal --background`, then repeat the two checks above.

## Step 5: Summary

```
✅ <ID> UAT round issued

Round: <ID>/round-1
Portal: http://localhost:8766/uat/?round=<ID>%2Fround-1
Request/evidence: .jswarm/work/<ID>/uat-round/
```

**Next:** open the URL above and walk the round yourself, or send it to whoever owns acceptance. If the round finds something, run `/jFix <problem>` in this project's agent session. If it comes back clean, run `/jClose`.
