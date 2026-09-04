<!-- CONFIG-CONTROLLED (COM-176 controlled-config) — master: skills/test/UAT_CURRENT_ROUND_EXAMPLE.md
     WORKED EXAMPLE of UAT_CURRENT_ROUND_TEMPLATE.md — snapshot of the hai-sim-engine HAS-508 round (2026-07-08).
     Read alongside the template when instantiating a round; project paths/ids here are illustrative.
     Manage via /devops-maint dotclaude (mode 37); do not hand-edit a deployed copy. -->
# UAT — CURRENT ROUND (single always-current file; wholesale-replaced at issuance, PATCHED mid-round)

**Round:** 2026-07-08 · re-observation of the four owner-reported defect fixes (#102/#103/#104/#106) + carry-forward
**Last refreshed:** 2026-07-08T15:50-04:00 (America/New_York EDT) · 19:50 UTC
**Stack:** recreate #14 · preflight PASS (fix sentinels in-container ×2, health 200, replay probe 4/4) · http://macstudio-lsa:8300 · admin `mike@lsa.dev`
**Deploy status:** ⚠️ PENDING: #108 fix `52a59967` (HUMAN-AI token-attrs defaulting) committed, awaiting your go for recreate #15 — holds J1's HUMAN-AI leg and the `cddef070` retry. Everything else testable now.

## Definitions

| Term | Meaning |
|---|---|
| Recreate #N | The numbered backend deploy (`docker compose up -d --force-recreate hai-simulator`). Hot reload is OFF, so a fix is live ONLY after the recreate that carries it |
| Preflight | Proof the RUNNING stack serves the fix code: in-container fix-sentinel grep + health 200 + worker polling + per-session replay probe |
| Purge-protected session | A session id registered as bug/pass evidence — read-only forever |
| Terminal / settled state | Judge builds only at their final state (complete/failure card), never a transient progress frame |

## Keys & Sets

**Keys**
| Key | Definition (1-2 sentences) |
|---|---|
| `#104-ref-fallback-kind` | REF build on an unavailable-content model failed with `unknown build source kind`. Fixed at the buffer seams (`e4979b49-fallback-identity-stamp`). |
| `#106-missing-trio` | HUMAN-AI cards never carried "Token-efficiency economics". Fixed (`ef3dbbd3-trio-producer`); forward-only. |

**Sets**
| Set | Members | Meaning |
|---|---|---|
| `SET.2026-07-05T09:12-EDT` | [`#102-card-composition`→`e4eec4fa`, `#103-ref-miswired-paint`→`573540b0`, `#104-ref-fallback-kind`→`e4979b49`, `#106-missing-trio`→`ef3dbbd3`] | LIVE — the deploy under test this round |

## Journeys (hot) — test NOW, or fix/trace in progress

**UAT ready?** 🟢 test it NOW · 🟡 fix in progress — do NOT test yet · 🔴 not started

**Data sources for this table (all rows summarized from, never invented):** [HAS-508.uat-scenarios.md](../../.jswarm/plans/HAS-508/HAS-508.uat-scenarios.md) · [HAS-508.uat-test.md (step script)](../../.jswarm/plans/HAS-508/HAS-508.uat-test.md) · [uat-scenarios-e2e.md](../../.jswarm/plans/HAS-508/uat-scenarios-e2e.md) · project canon [architecture.uat-scenarios.md](../architecture.uat-scenarios.md)

| # | Journey | Sources (scenario ids) | Walk (click-path, summarized) | PASS looks like | FAIL looks like | Closes |
|---|---|---|---|---|---|---|
| 1 | **HEADLINE** — card composition + rich trio (fresh DOC→COMPL→HUMAN-AI) | UAT.INPUTS.COMPL-ADD.BLEND ·<br>UAT.INPUTS.HUMAN-AI-ADD.BLEND ·<br>UAT.REVISION.COMPL-SUMMARY-LATEST-CARD ·<br>UAT.HUMANAI.PROVENANCE-DETAIL | New session → add doc → build →<br>Compliance ON + framework → wait terminal →<br>⚠️ HUMAN-AI leg AFTER recreate #15:<br>Human-AI Balance ON → wait terminal →<br>judge cards collapsed, then expanded | New revision per enrichment, base preserved;<br>COMPL chip `+X CONTROLS` matching model truth;<br>collapsed compliance summary on the LATEST COMPL card only (even under newer cards);<br>expanded COMPL = detail without summary duplication;<br>HUMAN-AI report carries the trio — "Logical Process Analysis", "AI Value-Add", "Token-efficiency economics" — change-specific, not generic | Summary on a non-COMPL card · summary+detail duplicated ·<br>trio missing/generic on a FRESH session ·<br>trio leaking onto DOC/COMPL/REF revisions ·<br>base overwritten by an enrichment | #102 · #106<br>(defects A/B/C) |
| 2 | **Your #104 retest** — REF-only build, same Life Sciences model as report 11 | UAT.REF.FALLBACK-REFERENCE-LANE ·<br>UAT.STARTSCREEN.REF (cascade walk) | New session → Browse Reference Models →<br>cascade to the report-11 aris-life-sciences model →<br>wait buffer → BUILD → wait terminal | Build completes; revision/provenance presents as **Reference lane**;<br>research-fallback wording only as provenance detail | Failure card · `unknown build source kind: 'research_findings'` ·<br>revision presenting as AI/research lane | #104 |
| 3 | MY-INTENT → REF quick-select render (spot-confirm; agent-QA already PASSED session f63d4a1a) | UAT.STARTSCREEN.MY-INTENT ·<br>UAT.STARTSCREEN.REF ·<br>UAT.STARTSCREEN.REF.SETTLE-STATE ·<br>UAT.BUILD.SECOND-STAGE-REPAINTS | New session → 3-4 My Intent fields → save →<br>quick-select an APQC cross-industry reference →<br>buffer ready → BUILD → wait for SETTLED state<br>(judge only the final settled revision, never a transient frame) | Coherent settled model: no missing tasks, no dropped flows,<br>EndEvent in its predecessor's lane (not stranded in lane 1);<br>canvas repaints within the settle window | Missing/overwritten tasks · dangling flows ·<br>EndEvent pinned to first lane ·<br>canvas stuck on pre-add base past the settle ceiling | #103 |
| 4 | Carry-forward: cache-hit replay + Apply arming | UAT.STARTSCREEN.REF.RELOAD-HYDRATION ·<br>UAT.INPUTS.MY-INTENT.FORM-STATE ·<br>UAT.INPUTS.MY-INTENT.APPLY-PROVENANCE ·<br>UAT.INTENT-GOVERNANCE.4 | (a) Upload a SEEN-BEFORE document → instant cached build →<br>open the SAME session in a NEW tab → model must paint;<br>(b) intent suggestions → Apply My Intent → after build,<br>edit one field → save → check Apply state | (a) fresh tab hydrates canvas + REF/DOC content chips;<br>(b) Apply arms only on real input change; typing/editing never auto-builds | (a) blank canvas with populated info panel;<br>(b) Apply armed with nothing changed · edit auto-triggering a build | #97 P1.1 ·<br>#85/#86 |

## Do-NOT-test / Do-NOT-panic

**Data sources for this table:** [bug master ledger](../../.jswarm/plans/HAS-508.plan.ui-harvest-and-retrieval-depth.md) · [session registry](uat-bug-report-session-registry.json)

| Path | Why (defect id + one line) | Workaround if hit |
|---|---|---|
| J1 HUMAN-AI leg + rebuilding session `cddef070` — until recreate #15 | #108 (your report 13): post-gate lane normalization drops token attrs past the defaulting seam; FIXED `52a59967`, deploy awaits your go; recurrence is deterministic pre-deploy | Judge J1's DOC + COMPL sub-legs now; HUMAN-AI leg after I confirm recreate #15 |
| Compliance toggle erroring on a fresh session (buffer error, no COMPL card) | #107 (pre-existing, NOT the deploy): compliance-shallow LLM truncated JSON, byte-identical on retry (cache-replay suspicion); disposition = your call | Retry once in a NEW session; report the session id if it repeats — not a #102 regression |
| The 3 old heavy sessions `f47abdad` / `a323547d` / `a5d2cf28` | Replay-fragile heavy-history class (TMPRL1101) — durable fix = HAS-524 | Fresh sessions only |
| 6+ session tabs simultaneously | #95 connection-cap starvation — ticketed (HAS-522 streaming) | Close spare tabs; hard-refresh the starved one |
| Big multi-source `/build` returning a 500 mid-flight | #105 (traced): sync build-ack exceeds ~30s proxy budget under load — the 500 lies, the build usually succeeded; fold→HAS-524 = your pending decision | Wait ~1 min + refresh; report only if the build itself failed |

## Notes (≤3 bullets)
- "Harvestable Research" expander is now HIDDEN on all views (your ruling; hard-refresh) — expected, not a bug; removal = HAS-523 tech debt.
- The #106 trio fix is forward-only: old sessions keep two rationale headers by design — judge the trio only on fresh sessions.
- Your five pending decisions live in `.jswarm/plans/HAS-508/HAS-508.state.md` §MORNING OWNER DECISIONS.
