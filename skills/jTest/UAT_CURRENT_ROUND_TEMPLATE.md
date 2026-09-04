# UAT: CURRENT ROUND (single always-current file; wholesale-replaced at issuance, PATCHED mid-round)

**Round:** <YYYY-MM-DD> · <one-line round purpose>
**Last refreshed:** <host-clock YYYY-MM-DDTHH:MM±HH:MM (IANA zone + abbrev) · HH:MM UTC; update on every patch>
**Stack:** <UNVERIFIED — PREP must supply deploy id, preflight evidence, clickable UI URL, and admin identity>
**Pre-walk:** <UNVERIFIED — PREP must supply validated pre-walk receipt>
**Deploy status:** <UNVERIFIED — PREP must supply pending-change truth>

## Canonical package (generated, owner-readable, and self-contained)
**package_state:** `<DRAFT_SEALED|QA_VERIFIED|ISSUED>`
**schema_version:** `uat-canonical-package@1`
**ticket:** `<TICKET>` · **round_id:** `<ROUND_ID>` · **package_id:** `<PACKAGE_ID>` · **package_hash:** `<PACKAGE_HASH>`
**sealed_payload_sha256:** `<SEALED_PAYLOAD_SHA256>` · **script_id:** `<SCRIPT_ID>` · **script_hash:** `<SCRIPT_HASH>` · **certified_build_hash:** `<BUILD_HASH>`
**folder_path:** `<TICKET_FOLDER>` · **app_url:** `<HTTPS_URL>` · **login:** `<OWNER_LOGIN>`
**observer:** `available=<true|false>` · **capture:** `<BROWSER_CAPTURE_METHOD_OR_N/A>` · **fallback:** `<IMMEDIATE_SERVER_CAPTURE_OR_N/A>`
**recovery_policy:** `SETUP_REFRESH_ONLY; POST_ACTION_REFRESH_RELOAD_RETRY_CANNOT_PASS_ORIGINAL_ATOM`
**known_sources_checked:** `<ordered inspected source_ref list>`

Setup refresh is allowed only before the observed action. Post-action refresh, reload, or retry cannot PASS the original atom.

Each generated journey records `journey_id`, copied `requirement_ref`, `source`, `scenario_id`, `uat_test_anchor`, ordered `actions`, ordered `outcomes` (`atom_id`, `expected`, `fail_if`), ordered `atom_ids`, and `known_items` with `source_ref`. Outcome and finding vocabularies are recorded once in structured feedback, not repeated per journey.

### Journey <JOURNEY_ID>
Source: <SOURCE>
Scenario: <SCENARIO_ID>
Official GWT SHA-256: <GWT_SHA256>
UAT-test anchor: <ANCHOR>
Actions:
1. <ACTION>
Outcomes:
- Atom: <ATOM_ID>
  Expected: <EXPECTED>
  Fail if: <FAIL_CLAUSE>
Atom IDs: <ATOM_ID>
Known items:
- <KNOWN_ITEM> (<SOURCE_REF>)
Requirement ref: <AC_REF>

BEGIN NORMALIZED PACKAGE
```json
{"schema_version":"uat-canonical-package@1","ticket":"<TICKET>","round_id":"<ROUND_ID>","package_id":"<PACKAGE_ID>","package_hash":"<PACKAGE_HASH>","sealed_payload_sha256":"<SEALED_PAYLOAD_SHA256>","script_id":"<SCRIPT_ID>","script_hash":"<SCRIPT_HASH>","certified_build_hash":"<BUILD_HASH>","folder_path":"<TICKET_FOLDER>","app_url":"<HTTPS_URL>","login":"<OWNER_LOGIN>","observer":{"available":false,"capture":"N/A","fallback":"<IMMEDIATE_SERVER_CAPTURE>"},"recovery_policy":"SETUP_REFRESH_ONLY; POST_ACTION_REFRESH_RELOAD_RETRY_CANNOT_PASS_ORIGINAL_ATOM","known_sources_checked":["<SOURCE_REF>"],"journeys":[{"journey_id":"<JOURNEY_ID>","requirement_ref":"<AC_REF>","source":"<SOURCE>","scenario_id":"<SCENARIO_ID>","gwt_sha256":"<GWT_SHA256>","uat_test_anchor":"<ANCHOR>","actions":["<ACTION>"],"outcomes":[{"atom_id":"<ATOM_ID>","expected":"<EXPECTED>","fail_if":["<FAIL_CLAUSE>"]}],"atom_ids":["<ATOM_ID>"],"known_items":[{"text":"<KNOWN_ITEM>","source_ref":"<SOURCE_REF>"}]}]}
```
END NORMALIZED PACKAGE

## Composer gate: complete before issue
**Scenario + step sources:** <linked owner GWT scenarios and derived step-script sections>
**Bound GWT + boundary:** <verbatim relevant clauses and production/user-visible boundary>
**Stack currency:** <UAT-G0 proof: runtime sentinel, start time, smoke, replay or N/A>
**Round truth:** <host-clock timestamp/time zone/UTC, deploy status, issuance or patch mode>
**HOT/COLD + Who:** <all OPEN bugs in HOT; known behavior in COLD; OWNER/QA assignment>
**Journey + numbered Walk:** <specific scenario citations and self-contained numbered steps>
**Closes / NFR:** <defect pills, AC, and relevant NFR IDs including reliability/data integrity>
**QA handoff:** <all 13 fields complete or N/A — reason; transport decision JSON>

## Definitions

| Term | Meaning |
|---|---|
| <term> | <one-line definition; keep ≤8 current entries> |

## Keys & Sets

**Keys**
| Key | Definition (1-2 sentences) |
|---|---|
| `#NNN-slug-slug` | <user-visible defect symptom and state> |
| `shortsha-slug-slug` | <commit change, one breath> |

**Sets**
| Set | Members | Meaning |
|---|---|---|
| `SET.<YYYY-MM-DDTHH:MM-TZ>` | [`key`→`key`, `key`] | <timestamp-named review batch purpose> |

## Journeys (hot): readiness assigned during PREP; placeholder rows are not test targets

**Data sources for this table (all rows summarized from, never invented):** [<TICKET>.uat-scenarios.md](<path>) · [<TICKET>.uat-scenario-steps.md](<path>) · [uat-scenarios-e2e.md](<path>) · [architecture.uat-scenarios.md](<path>) · [bug ledger](<path>) · [session registry](<path>) · [NFR slice](<path>)

The source links govern both tables; per-row citations live inside Journey and there is no separate Sources column. Journey names the specific scenarios/step sections; Walk is a numbered, self-contained do-this script. Rows move between HOT and COLD in the same patch as readiness changes. Every OPEN ledger bug is HOT with Who. Sets use creation timestamps only. Owner-openable sessions are clickable absolute links.

**UAT ready?** Placeholder rows start 🔴 and are NOT test targets. PREP must replace each placeholder with truthful readiness and Who before issue. 🟡 fix in progress — do NOT test yet · 🔴 not started · ⚫ known behavior — NOT a test target. **Who** = OWNER (visual/product judgment) · QA (jQATester machine walk) · — for ⚫ rows. Every actionable HOT row's Closes cell must contain exactly one backticked `AC:<requirement-ref>` marker. Its payload is trimmed, non-empty, at most 128 characters, and contains no backtick, pipe, CR, or LF; other pills and NFR IDs may remain.

| # | UAT ready? | Who | Journey (plain-English summary + SPECIFIC scenario ids + step §s) | Walk — numbered do-this steps | PASS / expected looks like | FAIL / report if | Closes (pills / exact `AC:<requirement-ref>` / relevant NFR ids) | Official GWT SHA-256 |
|---|---|---|---|---|---|---|---|---|
| 1 | 🔴 | <OWNER / QA / OWNER + QA> | <what this proves — UAT-n steps §x + e2e §Jx> | <1. Do X. 2. Do Y. 3. Do Z.> | <observable outcome from cited GWT> | <explicit FAIL condition> | <`#NNN-slug` / `AC:<requirement-ref>` / relevant NFR IDs> | `<lowercase 64-hex copied from the cited official scenario gwt_sha256>` |

## Journeys (cold, no action needed): context, confirmed/closed rows, known behavior

| # | UAT ready? | Who | Journey (plain-English summary + SPECIFIC scenario ids + step §s) | Walk — numbered do-this steps (or n/a for ⚫) | PASS / expected looks like | FAIL / report if | Closes |
|---|---|---|---|---|---|---|---|
| 2 | ⚫ | — | KNOWN — <class — bug ledger/registry source> | <path or trigger> | <expected known behavior + do-not-panic note> | <when it is reportable + what to capture> | <`#NNN-slug` → deferral home> |

## Notes (≤3 bullets)
- <bullet>
