---
name: jFix
description: Diagnose, contract, repair, and prove a fix for a described problem.
user-invocable: true
triggers:
  - fix
  - debug and fix
  - diagnose and fix
  - root cause fix
argument-hint: "<description of what's broken | error message | test name>"
---

# /jFix: Diagnose, Contract, Repair, Prove

## Safety contract

- **Default is read-only / no-write.** Invoked with no args, `--help`, or `status`, this skill only inspects and reports; it performs no write.
- **Confirm before any production change.** Repair (Step 3 below) requires the user to have seen and accepted the contract from Step 2 first.

## Usage

```
/jFix <description of what's broken | error message | test name>
/jFix --diagnose-only <description>   # stop after Step 1; no contract, no repair
```

Public `/jFix` diagnoses on its own; it does not hand off to another agent for triage before starting.

## Extension steps

Run `${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.ext jFix`. For each path printed, in order, read the file and carry out its steps here before continuing. If nothing is printed, continue.

## Step 1: Diagnose

Reproduce the failure, or gather the exact evidence that stands in for reproduction (error message, failing test, log excerpt). Identify the causal mechanism, not just the symptom: the code path that actually produces the wrong behavior, and why. Cite the evidence for that mechanism: do not accept a plausible-sounding cause without something that would fail differently if the cause were wrong.

If `--diagnose-only` was passed, stop here and report the diagnosis; no contract or repair.

## Step 2: Contract

State, in plain language the user can approve or reject before any production code changes:

- the mechanism identified in Step 1;
- the smallest change that fixes it;
- what is explicitly out of scope for this pass (a related-looking issue is not automatically included);
- how the fix will be proven (which of Step 4's proof forms apply, and why).

Wait for the user to accept the contract, or to redirect it, before Step 3.

## Step 3: Repair

1. Write a test that fails for the identified mechanism, using an independently sourced expected value, not one derived by re-running the buggy code path.
2. Confirm that test is RED before touching production code.
3. Make the smallest change described in the contract.
4. Confirm the test is GREEN.

## Step 4: Prove

Run, in order, whichever of these the contract called for:

1. **Connected proof**: the fix proven through the real integration seam it touches (a real call, a real record written and read back), not only a unit-level mock, for anything beyond pure local logic.
2. **Counterexample proof**: deliberately revert or break the fix and confirm the test fails again. A test that would pass either way is not proof.
3. **Canary**: one cheap, targeted check of the surrounding surface, selected by what the fix risks touching, before any expensive UAT round.
4. **Full effective proof set, rerun**: after the last production change, rerun everything from Steps 3-4 once more; a fix proven against an earlier version of itself is not proven.

If the fix requires the user to see it work in the running application (not just in tests), route that through `/jTest`'s UAT preparation rather than improvising a browser walk here; `/jFix` does not close acceptance on its own.

## Step 5: Report

```
Diagnosis: <mechanism, with evidence>
Contract: <what was fixed, what was excluded>
Proof: RED -> GREEN / connected / counterexample / canary / final rerun (each PASS/FAIL/N/A with reason)
Files changed: <list>
```

**Next:** if the fix needs a real user-journey check, run `/jTest` in this project's agent session to prepare a UAT round. Otherwise, once every affected round is clean, run `/jClose`.
