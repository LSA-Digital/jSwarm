# `/jFix` localization reference

The portable resolver invocation is `$HOME/.claude/skills/fix/scripts/fix_localization.py`; Phase 5 materializes that declared target. It is read-only except for explicit, create-only `freeze --output` writes. It resolves immutable `global_floor → global_profiles → project → ticket → cycle` authority and emits `jswarm.fix-resolution/v1` receipts with precedence, source hashes, statuses, and an effective digest.

Project overlays use `jswarm.fix-overlay/v1`. They can select declared proof profiles and add typed, contained runner references; they cannot supply shell commands, model routing, global-floor changes, or ownership gates. A malformed or absent optional overlay is visibly `absent`, `ignored`, or `rejected`; weakening, unknown selections, traversal, invalid state transitions, and overwrite attempts are blocked.

Runners are structured references only (`pytest`, `script`, `playwright_manifest`, `http_canary`). The resolver never executes them. Frozen cycles are evidence contracts: a non-`local_logic` risk requires a jTestEngineer boundary map, causal RED, independent expected-value authority, connected proof, a counterexample, selected canary, and final effective-proof rerun. UAT-required acceptance returns to `/jTest uat prepare → execute → feedback`.
