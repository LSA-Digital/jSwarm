# UAT: CURRENT ROUND (single always-current file; wholesale-replaced at issuance, PATCHED mid-round)

**Round:** 2026-01-15 · re-observation of the three owner-reported defect fixes (#201/#202/#203) + carry-forward
**Last refreshed:** 2026-01-15T15:50-04:00 (America/New_York EDT) · 19:50 UTC
**Stack:** recreate #7 · preflight PASS (fix sentinels in-container ×2, health 200, replay probe 4/4) · http://devbox:8300 · admin account
**Deploy status:** ⚠️ PENDING: #205 fix `a1b2c3d4` (import-queue retry defaulting) committed, awaiting your go for recreate #8; holds Journey 1's retry leg and the `f00dcafe` re-test. Everything else testable now.

## Definitions

| Term | Meaning |
|---|---|
| Recreate #N | The numbered backend deploy (`docker compose up -d --force-recreate app`). Hot reload is OFF, so a fix is live ONLY after the recreate that carries it |
| Preflight | Proof the RUNNING stack serves the fix code: in-container fix-sentinel grep + health 200 + worker polling + per-session replay probe |
| Purge-protected session | A session id registered as bug/pass evidence (read-only forever) |
| Terminal / settled state | Judge builds only at their final state (complete/failure card), never a transient progress frame |

## Keys & Sets

**Keys**
| Key | Definition (1-2 sentences) |
|---|---|
| `#202-import-fallback-kind` | Import on an unrecognized file type failed with `unknown source kind`. Fixed at the parser boundary (`b2c3d4e5-fallback-identity-stamp`). |
| `#203-missing-summary` | Review cards never carried the "Confidence summary" section. Fixed (`c3d4e5f6-summary-producer`); forward-only. |

**Sets**
| Set | Members | Meaning |
|---|---|---|
| `SET.2026-01-12T09:12-EDT` | [`#201-card-composition`→`aaaa1111`, `#202-import-fallback-kind`→`bbbb2222`, `#203-missing-summary`→`cccc3333`] | LIVE: the deploy under test this round |

## Journeys (hot): test NOW, or fix/trace in progress

**UAT ready?** 🟢 test it NOW · 🟡 fix in progress, do NOT test yet · 🔴 not started

**Data sources for this table (all rows summarized from, never invented):** [TICKET-XXX.uat-scenarios.md](../../.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-scenarios.md) · [TICKET-XXX.uat-test.md (step script)](../../.jswarm/plans/TICKET-XXX/TICKET-XXX.uat-test.md) · [uat-scenarios-e2e.md](../../.jswarm/plans/TICKET-XXX/uat-scenarios-e2e.md) · project canon [architecture.uat-scenarios.md](../architecture.uat-scenarios.md)

| # | Journey | Sources (scenario ids) | Walk (click-path, summarized) | PASS looks like | FAIL looks like | Closes |
|---|---|---|---|---|---|---|
| 1 | **HEADLINE**: card composition + review summary (fresh upload→process→review) | UAT.INPUTS.UPLOAD-ADD.BLEND ·<br>UAT.INPUTS.REVIEW-ADD.BLEND ·<br>UAT.REVISION.SUMMARY-LATEST-CARD ·<br>UAT.REVIEW.PROVENANCE-DETAIL | New session → add file → process →<br>Enrichment ON + profile → wait terminal →<br>⚠️ review leg AFTER recreate #8:<br>Confidence Summary ON → wait terminal →<br>result cards collapsed, then expanded | New revision per enrichment, base preserved;<br>chip `+X FIELDS` matching model truth;<br>collapsed summary on the LATEST card only (even under newer cards);<br>expanded = detail without summary duplication;<br>review report carries the expected sections, change-specific, not generic | Summary on a wrong card · summary+detail duplicated ·<br>sections missing/generic on a FRESH session ·<br>sections leaking onto unrelated revisions ·<br>base overwritten by an enrichment | #201 · #203<br>(defects A/B/C) |
| 2 | **Your #202 retest**: import-only build, same sample set as report 4 | UAT.IMPORT.FALLBACK-SOURCE-LANE ·<br>UAT.STARTSCREEN.IMPORT (cascade walk) | New session → Browse Sample Sources →<br>cascade to the sample-set-b model →<br>wait buffer → BUILD → wait terminal | Build completes; revision/provenance presents as **Import lane**;<br>fallback wording only as provenance detail | Failure card · `unknown source kind: 'legacy_format'` ·<br>revision presenting as the wrong lane | #202 |
| 3 | MY-INTENT → Import quick-select render (spot-confirm; agent-QA already PASSED session `deadbeef`) | UAT.STARTSCREEN.MY-INTENT ·<br>UAT.STARTSCREEN.IMPORT ·<br>UAT.STARTSCREEN.IMPORT.SETTLE-STATE ·<br>UAT.BUILD.SECOND-STAGE-REPAINTS | New session → 3-4 My Intent fields → save →<br>quick-select a cross-catalog reference →<br>buffer ready → BUILD → wait for SETTLED state<br>(judge only the final settled revision, never a transient frame) | Coherent settled model: no missing rows, no dropped links,<br>terminal item in its predecessor's group (not stranded);<br>canvas repaints within the settle window | Missing/overwritten rows · dangling links ·<br>terminal item pinned to first group ·<br>canvas stuck on pre-add base past the settle ceiling | #203 |
| 4 | Carry-forward: cache-hit replay + Apply arming | UAT.STARTSCREEN.IMPORT.RELOAD-HYDRATION ·<br>UAT.INPUTS.MY-INTENT.FORM-STATE ·<br>UAT.INPUTS.MY-INTENT.APPLY-PROVENANCE ·<br>UAT.INTENT-GOVERNANCE.4 | (a) Upload a SEEN-BEFORE file → instant cached build →<br>open the SAME session in a NEW tab → model must paint;<br>(b) intent suggestions → Apply My Intent → after build,<br>edit one field → save → check Apply state | (a) fresh tab hydrates canvas + import/upload content chips;<br>(b) Apply arms only on real input change; typing/editing never auto-builds | (a) blank canvas with populated info panel;<br>(b) Apply armed with nothing changed · edit auto-triggering a build | #190 P1.1 ·<br>#180/#181 |

## Do-NOT-test / Do-NOT-panic

**Data sources for this table:** [bug master ledger](../../.jswarm/plans/TICKET-XXX.plan.ui-harvest-and-retrieval-depth.md) · [session registry](uat-bug-report-session-registry.json)

| Path | Why (defect id + one line) | Workaround if hit |
|---|---|---|
| Journey 1 review leg + rebuilding session `f00dcafe` (until recreate #8) | #205 (your report 4): post-gate normalization drops import attrs past the defaulting seam; FIXED `a1b2c3d4`, deploy awaits your go; recurrence is deterministic pre-deploy | Judge Journey 1's upload + process sub-legs now; review leg after I confirm recreate #8 |
| Enrichment toggle erroring on a fresh session (buffer error, no summary card) | #204 (pre-existing, NOT the deploy): enrichment-shallow response truncated JSON, byte-identical on retry (cache-replay suspicion); disposition = your call | Retry once in a NEW session; report the session id if it repeats, not a #201 regression |
| The 3 old heavy sessions `abc12300` / `def45600` / `ghi78900` | Replay-fragile heavy-history class (TMPRL-EXAMPLE-1); durable fix = TICKET-YYY | Fresh sessions only |
| 6+ session tabs simultaneously | #199 connection-cap starvation, ticketed (TICKET-ZZZ streaming) | Close spare tabs; hard-refresh the starved one |
| Big multi-source `/build` returning a 500 mid-flight | #198 (traced): sync build-ack exceeds ~30s proxy budget under load; the 500 lies, the build usually succeeded; fold→TICKET-YYY = your pending decision | Wait ~1 min + refresh; report only if the build itself failed |

## Notes (≤3 bullets)
- "Legacy import" expander is now HIDDEN on all views (your ruling; hard-refresh); expected, not a bug; removal = TICKET-WWW tech debt.
- The #203 summary fix is forward-only: old sessions keep two summary headers by design; judge the new summary only on fresh sessions.
- Your pending decisions live in `.jswarm/plans/TICKET-XXX/TICKET-XXX.state.md` §MORNING OWNER DECISIONS.
