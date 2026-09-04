---
name: jPlan.ceremony-selector
description: Run the JSWARM ceremony selector to render scope, recommend a planning tier, and persist the selected ceremony state.
---

# Ceremony Selector

**Last Updated:** 2026-08-22

Use this skill for Story, Task, and Bug planning when the developer wants full mode with the JSWARM (JarviSWARM) ceremony selector. The live flow is intentionally light: one deterministic scope render command, one deterministic recommendation render command, then tier pick.

Keep output deterministic and persist the validated decision state returned by the `apply` boundary to the plan: `selected_ceremony_tier`, `engine_recommended_tier` (the deterministic Medium-default baseline), Jarvi's authored situational recommendation tier, `situational_rationale`, `selector_signals`, `hard_high_triggers`, `owner_approved_high` when High is selected, and, when a downgrade below a fired High bar is accepted, `downgrade_rationale`. Medium is the default pick. High requires explicit owner approval.

### Skill and controlled-config edits

Classify these by **contract surface, not file type**. Default prose, guidance, example, formatting, and additive opt-in edits that do not change a parsed contract to **Low ceremony (S0)**: one-pass edit, light smoke, then `jregister`/`jdeploy`; no RED/GREEN, separate xhigh review, or tabletop. Ask: **Does tooling parse or enforce this exact content, or does another command, agent, or schema depend on this exact byte?** If no, keep `shared_contract_surface=low`. If yes, treat it as **S1**: raise `shared_contract_surface` to match the actual blast radius and use `novelty_architecture_uncertainty` only when genuine uncertainty exists; use RED→GREEN plus one independent `jCritic`, reserving xhigh/architect/tabletop for a high-blast-radius live-global shared contract.

## Live flow

### Step 1 — scope render command

Run EXACTLY this one command. Fill the drafted scope fields (Goal, In scope, Out of scope, and Acceptance) from the Q1-Q4 read, then print its output verbatim and ask the developer to confirm or refine:

```bash
.venv/bin/python jswarm/patterns/ceremony_selector.py render-scope --goal '<one-sentence goal>' --in-scope '<comma-or-semicolon-separated bullets>' --out-of-scope '<comma-or-semicolon-separated bullets>' --acceptance '<observable done conditions>' --no-tty
```

If the developer refines the scope, update your signal read from the confirmed scope before Step 2.

### Step 2 — one render command

After scope confirmation, run EXACTLY this one command. Fill the seven signal values from the Q1-Q4 read; add `--low`, `--medium`, or `--high` only if the developer passed a tier flag. Then print its output verbatim:

```bash
.venv/bin/python jswarm/patterns/ceremony_selector.py render --signals 'scope_blast_radius=<low|medium|high>,user_visible_behavior=<low|medium|high>,shared_contract_surface=<low|medium|high>,security_compliance_external_write=<low|medium|high>,reversibility_migration_risk=<low|medium|high>,novelty_architecture_uncertainty=<low|medium|high>,concurrency_shared_files=<low|medium|high>' --no-tty
```

The rendered block contains the three pattern cards, the deterministic Engine Baseline (signals + baseline tier), and the tier menu in one deterministic response. Jarvi's situational recommendation is authored in Step 3, not in this deterministic render.

### Step 3 — pick, apply, and persist

After scope confirmation, Jarvi authors `jarvi_recommended_tier` and a non-empty `situational_rationale` anchored to the rendered `engine_recommended_tier` and selector card data. **Default `jarvi_recommended_tier` to Medium** unless the owner already locked `--high`. Ask the developer to pick Low, Medium, or High, presenting Medium as the default. Do not apply High unless the owner explicitly chose High. If the rendered block shows `downgrade rationale required`, ask for the rationale.

Then invoke the executable boundary to validate and persist the complete decision state. Replace every `<...>` with the real value first — `apply` rejects template/placeholder text such as `<scope-anchored rationale>`:

```bash
.venv/bin/python jswarm/patterns/ceremony_selector.py apply --signals 'scope_blast_radius=<low|medium|high>,user_visible_behavior=<low|medium|high>,shared_contract_surface=<low|medium|high>,security_compliance_external_write=<low|medium|high>,reversibility_migration_risk=<low|medium|high>,novelty_architecture_uncertainty=<low|medium|high>,concurrency_shared_files=<low|medium|high>' --tier '<low|medium|high>' --jarvi-tier '<low|medium|high>' --situational-rationale '<the actual scope-anchored rationale you authored>' --format json
```

Default `--tier` to `medium` unless the owner picked another tier. Only when the owner explicitly selected High, append `--owner-approved-high`. Only when the selected tier is below a fired High bar, append `--rationale '<the actual downgrade reason>'` with concrete text (never the literal placeholder). `apply` exits non-zero if High is selected without `--owner-approved-high`, or if a High bar fired and no real downgrade rationale is supplied — Jarvi's recommendation can never waive either gate.

Prose-only decision recording is forbidden: persist the JSON returned by `ceremony_selector.py apply`, which enforces downgrade rationale floors.

Do not probe, inspect, run `--help`, run `--describe`, read source, or author files — the two commands above are all you need for rendering.
