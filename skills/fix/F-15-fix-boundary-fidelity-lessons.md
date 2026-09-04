# F-15 boundary-fidelity lessons

## Ground conditions

HAS-617 recorded green helper batches before owner-visible composed failures. HAS-652 first found five of six product defects at the walk and required six serially unmasking cycles. Both cases show that a local green test is not proof that the failed production seam was crossed.

## Measured contract

| Measure | Baseline | Target | Recipe | Falsifier |
|---|---|---|---|---|
| Detection point | HAS-652: 5/6 first found at walk; HAS-617 after green batches | >=80% before review/UAT | Read receipt detection stages for comparable declared seams | <80% or a silently omitted seam |
| Cycle time | HAS-652: ~90 minutes, including 25-30 minute treadmill | <=45 minutes median | Compare diagnosis-to-final-verify timestamps | <20% improvement after six cycles/two tickets |
| Repeated cycles | HAS-652: six serial cycles; HAS-617: day-scale repeats | >=50% fewer | Group receipts by path and boundary | Below 50% reduction |
| False green | HAS-617: 2/2 cited green batches escaped | <=10% | Join final proof digest to later same-seam failure | >25% triggers architecture review |
| Alignment | Stale/narrow bars existed in both retros | 100% linked or typed N/A | Require scenario/requirement/NFR authority in receipt | Any inferred acceptance |
| First-time success | Missing seams caused later rework | >=80% without boundary rework | Count no-rework comparable cycles | Below 80% |

The recipe is causal RED, independently sourced expected values, a connected real-seam proof, counterexample, selected canary, and final proof rerun. The denominator excludes explicitly not-applicable cycles; it never treats omitted data as success.
