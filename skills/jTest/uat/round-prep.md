
# UAT round preparation

This F-26 procedure governs `/jTest uat prepare`. For the complete lifecycle and ownership table, see [the lifecycle hub](../test-uat.lifecycle.md).

## Issuance fast path for v2

For a `uat-canonical-package@2` round, prepare is optional for issuance. In the
practical issuance path, **prepare builds only the QA handoff; issuance needs
only `materialize-validate` (the materializer validate-only check) and
owner-authorized cutover**. If the exact v2
practical-cutover request and accepted evidence already exist, do not rerun
prepare merely to issue the round. Validate that request without writing:

```bash
.venv/bin/python -m jswarm.uat_round_materialize preflight-manifest \
  --request .jswarm/plans/<TICKET>/<TICKET>.uat-practical-cutover-request.json
```

Require `VALID:`. Then follow [round registration](round-registration.md) for
the canonical request digest check, stop/cutover/bootstrap sequence, health
poll, round-list check, and owner-page verification. The owner link is sent only
after those checks pass.

`/jTest uat prepare` executes through `jswarm/uat_prepare.py`: use `--ticket`, `--request-json <PATH|->`, optional `--report`, and `--json-out`; fixture code calls `run_prepare()` directly. The request is transient input, while PREP owns sealing and report consumption only.

Authoring the request itself (journey/scenario/step shapes, nested GWT lineage,
the digest rule, and the `preflight-manifest` check to run before prepare) is
[`round-authoring.md`](round-authoring.md). Check the manifest there first; a
shape error found at prepare or cutover is the same error found expensively.

The deployed [feedback result contract](feedback-result-contract.json) owns the
step-option vocabulary and ordering. Inspect the contract and its SHA with:

```bash
.venv/bin/python -m jswarm.uat_feedback result-contract --json
```

A current-shape prewalk may exercise only the options authored on each step, and
the owner-readable rendering must show those same options. For a newly issued
or resealed round, a mismatch among the deployed master, the runtime-published
contract, and the CLI-reported SHA means `OWNER-MAY-WALK: no`; do not invite the
owner. A legacy sealed package may still be read and prewalked without adding
or inserting defaults into its sealed object.

## Prepare the exact walk

Before any round is prepared or materialized, select exactly one typed certification source. Cross-source, relabeled, health-only, raw-preview, or operator-authored receipt JSON does not certify.

### `jinfra-docker-recreate-v1`

**Not available in this distribution: `jInfra` is enterprise-only tooling not shipped in this repository. Use `local-process-v1` below instead.** The rest of this subsection documents the contract for an installation that does provide it.

Use the absolute-path JINFRA array form mandated by the jInfra skill; never invoke a bare `jInfra` command:

```bash
JINFRA=("${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python" "${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/jinfra_cli.py")
"${JINFRA[@]}" --currency --json-out ".jswarm/plans/<TICKET>/jinfra.json"
```

Continue only when the receipt reports a clean `PASS` with exit `0` and contains no `warn` result. A warning is blocking, even when jInfra exits `0`; any `warn`, especially stale-derived-cache, stops preparation until remediated. For stale-derived-cache, apply the engine recommendation `docker compose up -d --force-recreate --renew-anon-volumes <service>`, rerun `--currency`, and do not proceed until clean. Record the jInfra JSON receipt path with the round. Container health and deployment identity do not prove that the served surface is current, and test suites structurally cannot see it; this gate is the only connection between jInfra's derived-cache knowledge and UAT.

**This currency gate is IN ADDITION to (not a substitute for) the selected source-typed certified build receipt the executable prep gate requires** (`uat_prepare.py` demands `certification.verdict == "certified"`). jInfra mints certification only through a confirmed unforced `--docker-recreate` with clean identical HEAD and an unscoped currency PASS: a `--currency`-only run, however clean, cannot certify, because generation currency is still blind to process staleness on bind-mounted services (a running process older than its re-read-on-restart inputs passes file-level currency): the recreate is what makes "running = current" true, not just plausible. So a Docker round prep on a current stack still performs one confirmed recreate to mint the certified receipt; under the advisory worker-safety default this carries no waiver ceremony. If the process-staleness check lands in the engine, currency-clean-at-identical-HEAD may become sufficient to certify and this paragraph will be revised. Until then the recreate requirement is the honest cost, stated here so no lane rediscovers the prose/executable split.

### `local-process-v1`

The only local alternative is `jswarm/uat_prepare_local_process.py`. Its receipt schema is `jswarm.uat.local-process-certification/v1`; it must bind the exact DEMO-391 Python service and Astro production preview to closed content/build/fixture/config manifests, fresh loopback listener PID/PPID/argv/cwd/start facts, byte-equal `/` and `/jUAT/` assets, targeted read-only API/UI smoke, and current-script replay N/A. Run its `verify` operation immediately before the expensive walk. It performs no POST and rejects redirects, stale bytes, missing assets, non-loopback services, or malformed evidence.

1. Start from the project-localized executable UAT-scenarios E2E source, then verify the official scenario/GWT and UAT-script chain with the chain verifier and require the selected source-typed certified build receipt. Use a prior round manifest only as fallback context; source selection is governed by [`round-authoring.md`](round-authoring.md#choose-the-round-source).
2. Materialize and seal the non-issued `<TICKET>.UAT-CURRENT-ROUND.md` with `jswarm/uat_round_materialize.py`. Its package state is `DRAFT_SEALED` before any jQATester dispatch.
3. Ensure every actionable HOT `Closes` cell contains exactly one backticked `AC:<requirement-ref>` marker. The trimmed payload is copied byte-for-byte into the package journey requirement reference; multiple journeys may share it.
4. Dispatch jQATester against that exact sealed file, package identity, script identity, and certified build. PREP does not perform or judge the browser walk.

The dispatch handoff carries a PREP-generated exact-walk annex. If governed inputs are absent, the generated annex is unavailable: hand-compose the thirteen fields against the sealed round and record the deviation.

## Consume the pre-walk

A later `/jTest uat prepare` invocation consumes the jQATester report. Only a complete PASS for the same package, script, and build may attach gate receipts, transition `QA_VERIFIED` to `ISSUED`, materialize the aligned feedback file, and invite the owner. The sealed journey payload remains unchanged.

FAIL, PARTIAL, or BLOCKED routes to RED → fix → GREEN. Do not re-judge the walk, substitute a refreshed state, or consume owner feedback from PREP. Feedback ingestion belongs to [feedback.md](feedback.md).

## Preparation outcomes

| Outcome | Procedure response |
| --- | --- |
| `BLOCKED: jInfra currency not clean` | Do not prepare, materialize, seal, or dispatch; remediate every warning, rerun `--currency`, and record the clean receipt with the round. |
| `BLOCKED: local-process certification failed` | Do not prepare, materialize, seal, or dispatch; recreate the exact local fixture/build/process stack and rerun `jswarm/uat_prepare_local_process.py verify`. |
| `BLOCKED: no owner-script pre-walk` | Do not issue; complete a current exact-file pre-walk. |
| `BLOCKED: SEALED PACKAGE DRIFT` | Do not consume the report or issue; regenerate, reseal, and redispatch. |
| `PARTIAL: SHEET NOT WALKED VERBATIM` | Do not produce owner-walk authorization; finish the canonical walk in order. |
| `FAIL: RECOVERY-DEPENDENT LIVE PATH` | Preserve diagnostic evidence and route the defect; recovery cannot pass the original atom. |
| `OWNER OBSERVER UNAVAILABLE: FALLBACK REQUIRED` | Do not invite until the package states the limitation and immediate fallback capture. |

The materializer, chain verifier, and typed certification receipt are deterministic helpers. This document defines their preparation contract; Phase 4 owns runtime trigger and execution implementation.
