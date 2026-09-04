// COM-391 Phase 12 RED — browser-side canonical-v2 field contracts for A/C 19, 21, 22.
//
// These tests are deliberately failing until Phase 15 lands.  They drive the real
// production parser (`src/lib/uat-rounds.ts`), not a copy of it.
//
// The new resolvers are reached through a namespace import on purpose: a named
// `import { resolveEffectiveAppLink }` of a symbol that does not exist yet throws at
// module load and would take every other test in this file down with it, hiding the
// parser evidence behind one unrelated failure.

import assert from 'node:assert/strict';
import test from 'node:test';
import * as uatRounds from '../src/lib/uat-rounds.ts';
import { parseActiveRoundView } from '../src/lib/uat-rounds.ts';

const SEALED_APP_URL = 'http://macstudio-lsa:8765/uat/';

const feedbackResultContract = {
  schema_version: 'jswarm.test-uat.result-contract/v1',
  assessment_outcomes: {
    ALIGNED: 'PASS', OBSERVED_FAILURE: 'FAIL', BLOCKED: 'BLOCKED',
    NOT_OBSERVED: 'NOT_RUN', NOT_APPLICABLE: 'N/A',
  },
  cells: [
    ['PASS', 'NONE', 'SATISFIED'], ['PASS', 'NONE', 'ANSWERED-NO-CHANGE'],
    ['PASS', 'NONE', 'REJECTED-BY-OWNER'], ['PASS', 'MINOR', 'ACCEPTED-BY-OWNER'],
    ['PASS', 'MINOR', 'OPEN'], ['FAIL', 'MINOR', 'OPEN'], ['FAIL', 'MAJOR', 'OPEN'],
    ['BLOCKED', 'NONE', 'BLOCKED'], ['NOT_RUN', 'NONE', 'OPEN'], ['N/A', 'NONE', 'N/A'],
  ],
};

const gwtOne = 'd'.repeat(64);
const gwtTwo = 'e'.repeat(64);

function journeyOne() {
  return {
    journey_id: 'uat-391-1',
    title: 'Read-only active context',
    known_items: [],
    scenarios: [{
      scenario_id: 'UAT-391-1', title: 'Inspect the active round',
      gwt: [{
        gwt_ref: gwtOne, sha256: gwtOne,
        given: ['An active round'], when: ['It is opened'], then: ['Its exact source is visible'],
      }],
    }],
    steps: [
      {
        step_id: 'uat-391-1-step-01', ordinal: 1, name: 'Open exact source',
        instruction: 'Open the exact source dialog.', expected_outcome: 'Literal source text is visible.',
        scenario_links: [{ scenario_id: 'UAT-391-1', gwt_refs: [gwtOne] }],
      },
      {
        step_id: 'uat-391-1-step-02', ordinal: 2, name: 'Close exact source',
        instruction: 'Close the exact source dialog.', expected_outcome: 'The dialog closes.',
        scenario_links: [{ scenario_id: 'UAT-391-1', gwt_refs: [gwtOne] }],
      },
    ],
  };
}

function journeyTwo() {
  return {
    journey_id: 'uat-391-2',
    title: 'Save aligned feedback',
    known_items: [],
    scenarios: [{
      scenario_id: 'UAT-391-2', title: 'Save feedback',
      gwt: [{
        gwt_ref: gwtTwo, sha256: gwtTwo,
        given: ['A writable round'], when: ['Feedback is saved'], then: ['The digest changes'],
      }],
    }],
    steps: [{
      step_id: 'uat-391-2-step-01', ordinal: 1, name: 'Save feedback',
      instruction: 'Save the completed card.', expected_outcome: 'A fresh digest appears.',
      scenario_links: [{ scenario_id: 'UAT-391-2', gwt_refs: [gwtTwo] }],
    }],
  };
}

function activeView({ appUrl = SEALED_APP_URL, journeys } = {}) {
  const built = journeys ? journeys() : [journeyOne(), journeyTwo()];
  return {
    schema: 'jswarm.test-uat.active-round-view/v2', schema_version: '2.0',
    round_review_id: 'COM-391/round-com-391-001', ticket: 'COM-391', round_status: 'NOT_STARTED',
    projection_sha256: 'c'.repeat(64), feedback_result_contract: feedbackResultContract,
    current_round: {
      path: '.jswarm/plans/COM-391/COM-391.UAT-CURRENT-ROUND.md', sha256: 'a'.repeat(64), size: 100,
      package_state: 'ISSUED', source: '# Canonical round', writable: false,
      // The producer already seals app_url here; the browser has simply never read it.
      normalized_package: { schema_version: 'uat-canonical-package@2', app_url: appUrl },
      journeys: built,
    },
    feedback: {
      path: '.jswarm/plans/COM-391/COM-391.uat-feedback.md', sha256: 'b'.repeat(64), size: 80,
      processing_state: 'UNPROCESSED', round_status: 'NOT_STARTED', step_entries: {}, walk_stop: null,
      step_counts: { total: 3, gray: 3, yellow: 0, green: 0 }, markerized: true, writable: true,
    },
    capabilities: { feedback_update: true },
  };
}

// ------------------------------------------------------------------ A/C 21

test('A/C 21 — the parser exposes the sealed application base URL as a typed value', () => {
  const parsed = parseActiveRoundView(activeView());
  assert.equal(
    parsed.current_round.app_url,
    SEALED_APP_URL,
    'current_round.app_url is untyped today; normalized_package is kept as an opaque object so the workbench can never render the sealed base',
  );
});

test('A/C 21 — the parser never infers or rewrites the application host', () => {
  const foreign = 'http://some-other-host:9000/app/';
  const parsed = parseActiveRoundView(activeView({ appUrl: foreign }));
  assert.equal(
    parsed.current_round.app_url,
    foreign,
    'the sealed host must be surfaced verbatim, never substituted with the portal origin',
  );
});

// Bound: 8 malformed values, each replacing the single sealed app_url and nothing else.
for (const [name, appUrl] of [
  ['credentials', 'http://owner:secret@macstudio-lsa:8765/uat/'],
  ['credentials user only', 'http://owner@macstudio-lsa:8765/uat/'],
  ['javascript scheme', 'javascript:alert(1)'],
  ['data scheme', 'data:text/html,<b>x</b>'],
  ['protocol relative', '//macstudio-lsa:8765/uat/'],
  ['control character', 'http://macstudio-lsa:8765/uat/\n'],
  ['empty string', ''],
  ['non string', 7],
]) {
  test(`A/C 21 — the parser rejects an unsafe sealed app_url: ${name}`, () => {
    assert.throws(
      () => parseActiveRoundView(activeView({ appUrl })),
      TypeError,
      `an unsafe app_url (${name}) reached the browser unchallenged`,
    );
  });
}

test('A/C 21 — a sealed round with a valid HTTP tailnet base still parses', () => {
  const parsed = parseActiveRoundView(activeView({ appUrl: 'http://macstudio-lsa:8765/uat' }));
  assert.equal(parsed.current_round.app_url, 'http://macstudio-lsa:8765/uat');
});

// ------------------------------------------------------------------ A/C 22

test('A/C 22 — the parser types app_link at journey and step scope', () => {
  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.app_link = { href: '/journey-one', label: 'Journey one workspace' };
      one.steps[0].app_link = { href: '/journey-one/step-one', label: 'Step one route' };
      return [one, journeyTwo()];
    },
  }));

  const journey = parsed.current_round.journeys[0];
  assert.deepEqual(journey.app_link, { href: '/journey-one', label: 'Journey one workspace' });
  assert.deepEqual(journey.steps[0].app_link, { href: '/journey-one/step-one', label: 'Step one route' });
  assert.equal(journey.steps[1].app_link, null, 'absence must normalize to null only in browser view state');
});

// Bound: 12 invalid link values x 2 scopes (journey, step) = 24 cases, each mutating
// exactly one app_link and leaving the rest of the sealed round untouched.
for (const scope of ['journey', 'step']) {
  for (const [name, link] of [
    ['cross origin', { href: 'http://evil.test/uat/' }],
    ['cross origin other port', { href: 'http://macstudio-lsa:9999/uat/' }],
    ['cross scheme same host', { href: 'https://macstudio-lsa:8765/uat/' }],
    ['protocol relative', { href: '//macstudio-lsa:8765/uat/' }],
    ['credentials', { href: 'http://owner:secret@macstudio-lsa:8765/uat/' }],
    ['javascript scheme', { href: 'javascript:alert(1)' }],
    ['control character', { href: '/sessions/\nsession-123' }],
    ['empty href', { href: '' }],
    ['missing href', { label: 'Resume' }],
    ['blank label', { href: '/sessions/session-123', label: '   ' }],
    ['unknown member', { href: '/sessions/session-123', title: 'Resume' }],
    ['array instead of object', [{ href: '/sessions/session-123' }]],
  ]) {
    test(`A/C 22 — the parser rejects an invalid ${scope} app_link: ${name}`, () => {
      assert.throws(
        () => parseActiveRoundView(activeView({
          journeys: () => {
            const one = journeyOne();
            if (scope === 'journey') one.app_link = link; else one.steps[0].app_link = link;
            return [one, journeyTwo()];
          },
        })),
        TypeError,
        `an invalid ${scope} app_link (${name}) was accepted by the browser parser`,
      );
    });
  }
}

test('A/C 22 — the effective link resolver is exported', () => {
  assert.equal(
    typeof uatRounds.resolveEffectiveAppLink,
    'function',
    'no shared resolver exists, so every card would have to reimplement inheritance',
  );
});

test('A/C 22 — an explicit step link wins and reports step provenance', () => {
  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.app_link = { href: '/journey-one', label: 'Journey one workspace' };
      one.steps[0].app_link = { href: '/journey-one/step-one', label: 'Step one route' };
      return [one, journeyTwo()];
    },
  }));
  const journey = parsed.current_round.journeys[0];
  const effective = uatRounds.resolveEffectiveAppLink(journey.steps[0], journey, parsed.current_round.app_url);

  assert.equal(effective.href, '/journey-one/step-one');
  assert.equal(effective.label, 'Step one route');
  assert.equal(effective.source, 'step');
});

test('A/C 22 — an unaffected sibling step inherits the journey link with visible journey provenance', () => {
  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.app_link = { href: '/journey-one', label: 'Journey one workspace' };
      one.steps[0].app_link = { href: '/journey-one/step-one', label: 'Step one route' };
      return [one, journeyTwo()];
    },
  }));
  const journey = parsed.current_round.journeys[0];
  const effective = uatRounds.resolveEffectiveAppLink(journey.steps[1], journey, parsed.current_round.app_url);

  assert.equal(effective.href, '/journey-one', 'the sibling must not pick up step one route');
  assert.equal(effective.label, 'Journey one workspace', 'an inherited authored label is preserved exactly');
  assert.equal(effective.source, 'journey');
});

test('A/C 22 — a journey with no link falls back to the application base with a deterministic label', () => {
  const parsed = parseActiveRoundView(activeView());
  const journey = parsed.current_round.journeys[1];
  const effective = uatRounds.resolveEffectiveAppLink(journey.steps[0], journey, parsed.current_round.app_url);

  assert.equal(effective.href, SEALED_APP_URL);
  assert.equal(effective.source, 'base');
  assert.equal(typeof effective.label, 'string');
  assert.ok(effective.label.trim().length > 0, 'the base fallback must carry a deterministic label');
});

test('A/C 22 — a link on one journey never leaks into another journey', () => {
  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.app_link = { href: '/journey-one', label: 'Journey one workspace' };
      return [one, journeyTwo()];
    },
  }));
  const other = parsed.current_round.journeys[1];
  const effective = uatRounds.resolveEffectiveAppLink(other.steps[0], other, parsed.current_round.app_url);

  assert.equal(effective.href, SEALED_APP_URL, 'journey two inherited journey one\'s link');
  assert.equal(effective.source, 'base');
});

test('A/C 22 — resolution is presence-based, with no health oracle in the signature', () => {
  assert.equal(
    uatRounds.resolveEffectiveAppLink.length,
    3,
    'the resolver takes exactly (step, journey, appUrl); a fourth argument would be a place to thread link health and silently substitute a fallback',
  );

  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.app_link = { href: '/journey-one', label: 'Journey one workspace' };
      // A route that no longer exists in the application.  It stays explicit.
      one.steps[0].app_link = { href: '/journey-one/removed-route', label: 'Removed route' };
      return [one, journeyTwo()];
    },
  }));
  const journey = parsed.current_round.journeys[0];
  const effective = uatRounds.resolveEffectiveAppLink(journey.steps[0], journey, parsed.current_round.app_url);

  assert.equal(effective.href, '/journey-one/removed-route', 'a dead explicit link was silently replaced');
  assert.equal(effective.source, 'step');
});

test('A/C 22 — parsing does not mutate the caller\'s sealed round payload', () => {
  const view = activeView();
  const pristine = JSON.parse(JSON.stringify(view));

  parseActiveRoundView(view);

  // Bound: the whole active-round-view object, compared structurally, including every
  // journey, every step and the normalized package annex.
  assert.deepEqual(view, pristine, 'the parser inserted view-state defaults back into the sealed payload');
});

// ------------------------------------------------------------------ A/C 19

test('A/C 19 — the parser types step_refs on a known item and marks its scope', () => {
  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.known_items = [{
        text: 'Observer capture is unavailable on this step.',
        source_ref: 'COM-391.uat-test.md#known',
        step_refs: ['uat-391-1-step-02'],
      }];
      return [one, journeyTwo()];
    },
  }));
  const item = parsed.current_round.journeys[0].known_items[0];

  assert.deepEqual(item.step_refs, ['uat-391-1-step-02']);
  assert.equal(item.scope, 'step', 'a scoped disclosure must be distinguishable from a legacy one');
});

test('A/C 19 — a legacy known item stays readable and is explicitly marked legacy', () => {
  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.known_items = [{ text: 'A legacy journey-wide disclosure.', source_ref: 'COM-391.uat-test.md#known' }];
      return [one, journeyTwo()];
    },
  }));
  const item = parsed.current_round.journeys[0].known_items[0];

  assert.equal(item.text, 'A legacy journey-wide disclosure.');
  assert.equal(
    item.scope,
    'legacy-journey',
    'a legacy disclosure must be labelled legacy, never silently reinterpreted as step-specific',
  );
  assert.deepEqual(item.step_refs, [], 'a legacy disclosure must not be given invented step targets');
});

// Bound: 6 invalid step_refs values on a single known item of journey one.
for (const [name, stepRefs] of [
  ['empty array', []],
  ['blank string', ['   ']],
  ['duplicate', ['uat-391-1-step-01', 'uat-391-1-step-01']],
  ['unknown step', ['uat-391-1-step-99']],
  ['cross journey', ['uat-391-2-step-01']],
  ['ordinal not identity', ['1']],
]) {
  test(`A/C 19 — the parser rejects invalid step_refs: ${name}`, () => {
    assert.throws(
      () => parseActiveRoundView(activeView({
        journeys: () => {
          const one = journeyOne();
          one.known_items = [{
            text: 'A disclosure that must not render.',
            source_ref: 'COM-391.uat-test.md#known',
            step_refs: stepRefs,
          }];
          return [one, journeyTwo()];
        },
      })),
      TypeError,
      `invalid step_refs (${name}) reached the workbench`,
    );
  });
}

test('A/C 19 — disclosures resolve only to their targeted steps', () => {
  assert.equal(
    typeof uatRounds.resolveStepKnownItems,
    'function',
    'no step-scoped disclosure resolver exists, so the workbench can only render known items journey-wide',
  );

  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.known_items = [
        { text: 'Only step two.', source_ref: 'COM-391.uat-test.md#known', step_refs: ['uat-391-1-step-02'] },
        { text: 'Both steps.', source_ref: 'COM-391.uat-test.md#known', step_refs: ['uat-391-1-step-01', 'uat-391-1-step-02'] },
      ];
      return [one, journeyTwo()];
    },
  }));
  const journey = parsed.current_round.journeys[0];

  const first = uatRounds.resolveStepKnownItems(journey, journey.steps[0]).map((item) => item.text);
  const second = uatRounds.resolveStepKnownItems(journey, journey.steps[1]).map((item) => item.text);

  assert.deepEqual(first, ['Both steps.'], 'an unaffected step received a disclosure it was not targeted by');
  assert.deepEqual(second, ['Only step two.', 'Both steps.']);
});

test('A/C 19 — targeting survives a step reorder because ordinals are not identity', () => {
  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.known_items = [{
        text: 'Scope must survive a reorder.',
        source_ref: 'COM-391.uat-test.md#known',
        step_refs: ['uat-391-1-step-02'],
      }];
      // Swap positions and renumber ordinals; ids are unchanged.
      const [first, secondStep] = one.steps;
      one.steps = [{ ...secondStep, ordinal: 1 }, { ...first, ordinal: 2 }];
      return [one, journeyTwo()];
    },
  }));
  const journey = parsed.current_round.journeys[0];
  const nowFirst = journey.steps[0];

  assert.equal(nowFirst.step_id, 'uat-391-1-step-02');
  assert.deepEqual(
    uatRounds.resolveStepKnownItems(journey, nowFirst).map((item) => item.text),
    ['Scope must survive a reorder.'],
  );
  assert.deepEqual(uatRounds.resolveStepKnownItems(journey, journey.steps[1]), []);
});

test('A/C 19 — a legacy disclosure is not attributed to any step card', () => {
  const parsed = parseActiveRoundView(activeView({
    journeys: () => {
      const one = journeyOne();
      one.known_items = [{ text: 'A legacy journey-wide disclosure.', source_ref: 'COM-391.uat-test.md#known' }];
      return [one, journeyTwo()];
    },
  }));
  const journey = parsed.current_round.journeys[0];

  // Bound: every step of the journey carrying the legacy item.
  for (const step of journey.steps) {
    assert.deepEqual(
      uatRounds.resolveStepKnownItems(journey, step),
      [],
      `legacy disclosure was silently attributed to ${step.step_id}`,
    );
  }
});
