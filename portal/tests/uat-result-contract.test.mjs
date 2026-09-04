// COM-391 Phase 8 RED — the browser half of the one authoritative result contract.
//
// Contract: `.jswarm/plans/COM-391/COM-391.design.consolidated-recommendations.md` §2 —
// the backend owns a versioned result contract and "the browser derives its controls
// from it. Backend stays the final validator."
//
// Today `uat-rounds.ts` keeps its own `dispositionByAssessmentAndSeverity` literal,
// which is the second copy of a grammar that must have one owner. These tests fail
// for that structural reason, not because of a hand-copied fixture: they drive the
// real exported functions.
//
// Nothing in this file may skip. The decision-review-ui browser suite silently skips
// when Playwright is absent, and a missing runtime then reads as a pass — the
// mechanism by which portal defects reached production past a green suite.

import assert from 'node:assert/strict';
import test from 'node:test';
import * as uatRounds from '../src/lib/uat-rounds.ts';
import { allowedDispositions } from '../src/lib/uat-rounds.ts';

// Owner-facing alphabets from the COM-391 design contract, not copied from either
// implementation, so neither side serves as its own golden.
const SPEC_ASSESSMENTS = ['ALIGNED', 'BLOCKED', 'NOT_APPLICABLE', 'NOT_OBSERVED', 'OBSERVED_FAILURE'];
const SPEC_SEVERITIES = ['NONE', 'MINOR', 'MAJOR'];

// A backend-published contract, in the shape the round view is to carry. It is a
// stand-in for whatever the service serves: the point is that the browser's offered
// controls must follow it rather than a module-local literal.
const PUBLISHED_CONTRACT = {
  schema_version: 'jswarm.test-uat.result-contract/v1',
  assessment_outcomes: {
    ALIGNED: 'PASS',
    OBSERVED_FAILURE: 'FAIL',
    BLOCKED: 'BLOCKED',
    NOT_OBSERVED: 'NOT_RUN',
    NOT_APPLICABLE: 'N/A',
  },
  cells: [
    ['PASS', 'NONE', 'SATISFIED'],
    ['PASS', 'NONE', 'ANSWERED-NO-CHANGE'],
    ['PASS', 'NONE', 'REJECTED-BY-OWNER'],
    ['PASS', 'MINOR', 'ACCEPTED-BY-OWNER'],
    ['PASS', 'MINOR', 'OPEN'],
    ['FAIL', 'MINOR', 'OPEN'],
    ['FAIL', 'MAJOR', 'OPEN'],
    ['BLOCKED', 'NONE', 'BLOCKED'],
    ['NOT_RUN', 'NONE', 'OPEN'],
    ['N/A', 'NONE', 'N/A'],
  ],
};

function dispositionsFromContract(contract, assessment, severity) {
  const outcome = contract.assessment_outcomes[assessment];
  return contract.cells
    .filter(([cellOutcome, cellSeverity]) => cellOutcome === outcome && cellSeverity === severity)
    .map(([, , disposition]) => disposition);
}

test('an owner who observed a minor failure is offered a disposition', () => {
  // The workbench offers assessment OBSERVED_FAILURE and severity MINOR
  // (ActiveUatWorkbench.astro), then empties the disposition select, so the only
  // ways forward are overstating the severity as MAJOR or relabelling the step
  // ALIGNED — which records the failure as a pass.
  const offered = allowedDispositions('OBSERVED_FAILURE', 'MINOR', PUBLISHED_CONTRACT);

  assert.ok(
    offered.length > 0,
    'OBSERVED_FAILURE + MINOR leaves the disposition select empty: an observed minor failure is unrecordable.',
  );
  assert.ok(offered.includes('OPEN'), `expected OPEN among ${JSON.stringify(offered)}`);
});

test('the browser derives its controls from the backend-published contract', () => {
  // Phase 8: the browser must stop owning a second matrix. Given the published
  // contract, every assessment/severity pair must offer exactly the contract's cells.
  const derive =
    uatRounds.dispositionsFromResultContract ??
    uatRounds.allowedDispositionsFromContract ??
    (allowedDispositions.length >= 3
      ? (contract, assessment, severity) => allowedDispositions(assessment, severity, contract)
      : null);

  assert.ok(
    typeof derive === 'function',
    'uat-rounds.ts exposes no way to derive dispositions from the backend-published result contract; '
      + 'it still answers from its own dispositionByAssessmentAndSeverity literal.',
  );

  const mismatches = [];
  for (const assessment of SPEC_ASSESSMENTS) {
    for (const severity of SPEC_SEVERITIES) {
      const expected = dispositionsFromContract(PUBLISHED_CONTRACT, assessment, severity);
      const actual = [...derive(PUBLISHED_CONTRACT, assessment, severity)];
      if (JSON.stringify([...actual].sort()) !== JSON.stringify([...expected].sort())) {
        mismatches.push(`${assessment}/${severity}: offered ${JSON.stringify(actual)}, contract says ${JSON.stringify(expected)}`);
      }
    }
  }

  assert.deepEqual(mismatches, [], `browser controls disagree with the published contract:\n${mismatches.join('\n')}`);
});

test('a draft carrying a minor observed failure validates', () => {
  // The client-side draft validator is the last gate before the owner may submit.
  // It must accept the same cell the backend records.
  const entries = {
    'journey-1-step-01': {
      complete: true,
      assessment: 'OBSERVED_FAILURE',
      finding_severity: 'MINOR',
      finding_disposition: 'OPEN',
      observed: 'The banner rendered one pixel low; everything else behaved as expected.',
      comment: 'Cosmetic only; the journey continued.',
    },
  };

  const result = uatRounds.validateDraft(entries, ['journey-1-step-01'], PUBLISHED_CONTRACT, null);

  assert.deepEqual(
    result.errors,
    {},
    'the browser refuses a draft recording an observed minor failure, so the owner cannot submit one',
  );
  assert.equal(result.valid, true);
});
