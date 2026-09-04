import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import * as uatRounds from '../src/lib/uat-rounds.ts';
import { createFieldSaveState, transitionFieldSave } from '../src/lib/uat-field-save-indicator.ts';
import {
  UatApiError,
  buildFeedbackCommand,
  cloneEntries,
  createFeedbackController,
  deriveFeedbackUi,
  describeFailure,
  fetchRoundDetail,
  fetchRoundList,
  isDraftDirty,
  parseActiveRoundView,
  parseRoundList,
  saveFeedback,
  submitFeedback,
  buildRoundStatusCommand,
  updateRoundStatus,
  transitionFeedbackController,
  validateDraft,
} from '../src/lib/uat-rounds.ts';

const execFileAsync = promisify(execFile);
const projectRoot = fileURLToPath(new URL('..', import.meta.url));

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

const baseline = {
  'journey-1-step-01': {
    complete: false,
    assessment: '',
    finding_severity: '',
    finding_disposition: '',
    observed: '',
    comment: '',
  },
  'journey-2-step-01': {
    complete: true,
    assessment: 'ALIGNED',
    finding_severity: 'NONE',
    finding_disposition: 'SATISFIED',
    observed: 'The expected outcome was visible.',
    comment: 'Reviewed.',
  },
};

test('round list parser keeps valid summaries and registration warnings', () => {
  const parsed = parseRoundList({
    rounds: [{
      round_review_id: 'DEMO-391/round-001', ticket: 'DEMO-391', package_state: 'ISSUED',
      current_round_sha256: 'a'.repeat(64), feedback_sha256: 'b'.repeat(64),
      processing_state: 'UNPROCESSED', round_status: 'NOT_STARTED', writable: true,
    }],
    warnings: ['active round bad-registration skipped: source_invalid'],
  });
  assert.equal(parsed.rounds[0].round_review_id, 'DEMO-391/round-001');
  assert.deepEqual(parsed.warnings, ['active round bad-registration skipped: source_invalid']);
});

test('round summaries require canonical generated-at UTC and sort newest-first with stable ID ties', () => {
  const parsed = parseRoundList({
    rounds: [
      { round_review_id: 'DEMO-391/z', ticket: 'DEMO-391', package_state: 'ISSUED', current_round_sha256: 'a'.repeat(64), feedback_sha256: 'b'.repeat(64), feedback_generated_at: '2026-08-28T13:00:00Z', processing_state: 'UNPROCESSED', round_status: 'NOT_STARTED', writable: true },
      { round_review_id: 'DEMO-391/a', ticket: 'DEMO-391', package_state: 'ISSUED', current_round_sha256: 'c'.repeat(64), feedback_sha256: 'd'.repeat(64), feedback_generated_at: '2026-08-28T13:00:00Z', processing_state: 'UNPROCESSED', round_status: 'NOT_STARTED', writable: true },
      { round_review_id: 'DEMO-391/older', ticket: 'DEMO-391', package_state: 'ISSUED', current_round_sha256: 'e'.repeat(64), feedback_sha256: 'f'.repeat(64), feedback_generated_at: '2026-08-28T12:00:00Z', processing_state: 'UNPROCESSED', round_status: 'NOT_STARTED', writable: true },
    ],
    warnings: [],
  });
  assert.equal(parsed.rounds[0].feedback_generated_at, '2026-08-28T13:00:00Z');
  assert.deepEqual(
    uatRounds.sortUatSummariesNewestFirst(parsed.rounds).map((item) => item.round_review_id),
    ['DEMO-391/a', 'DEMO-391/z', 'DEMO-391/older'],
  );
  assert.throws(() => parseRoundList({ rounds: [{ ...parsed.rounds[0], feedback_generated_at: 'not-a-date' }], warnings: [] }), /feedback_generated_at/);
});

test('round summaries group into canonical date bands after newest-first sorting', () => {
  const rounds = [
    { round_review_id: 'DEMO-391/older', ticket: 'DEMO-391', package_state: 'ISSUED', current_round_sha256: 'a'.repeat(64), feedback_sha256: 'b'.repeat(64), feedback_generated_at: '2026-08-27T23:59:00Z', processing_state: 'UNPROCESSED', round_status: 'NOT_STARTED', writable: true },
    { round_review_id: 'DEMO-391/newer-b', ticket: 'DEMO-391', package_state: 'ISSUED', current_round_sha256: 'c'.repeat(64), feedback_sha256: 'd'.repeat(64), feedback_generated_at: '2026-08-28T09:00:00Z', processing_state: 'UNPROCESSED', round_status: 'NOT_STARTED', writable: true },
    { round_review_id: 'DEMO-391/newer-a', ticket: 'DEMO-391', package_state: 'ISSUED', current_round_sha256: 'e'.repeat(64), feedback_sha256: 'f'.repeat(64), feedback_generated_at: '2026-08-28T10:00:00Z', processing_state: 'PROCESSED', round_status: 'COMPLETE', writable: false },
  ];
  assert.equal(typeof uatRounds.groupUatSummariesByGeneratedDate, 'function');
  const bands = uatRounds.groupUatSummariesByGeneratedDate(rounds);
  assert.deepEqual(bands.map((band) => band.date), ['2026-08-28', '2026-08-27']);
  assert.deepEqual(bands[0].rounds.map((round) => round.round_review_id), ['DEMO-391/newer-a', 'DEMO-391/newer-b']);
});


test('feedback update v2 sends the full step snapshot while allowing dirty partial cards', () => {
  const stepEntries = cloneEntries(baseline);
  stepEntries['journey-1-step-01'].observed = 'A partial observation is worth saving.';
  const command = buildFeedbackCommand(stepEntries, 'b'.repeat(64), 'a'.repeat(64));
  assert.deepEqual(Object.keys(command).sort(), [
    'expected_current_round_sha256', 'expected_feedback_sha256', 'schema', 'schema_version', 'step_entries',
  ]);
  assert.equal(command.schema, 'jswarm.test-uat.feedback-update/v2');
  assert.equal(command.schema_version, '2.0');
  assert.deepEqual(command.step_entries, stepEntries);
  assert.notEqual(command.step_entries, stepEntries);
  assert.equal(command.step_entries['journey-1-step-01'].complete, false);
  assert.equal('source' in command, false);
  assert.equal('processing_state' in command, false);
  assert.equal('package_identity' in command, false);
});

test('feedback update carries a stopped walk only after explicit owner action', () => {
  const entries = cloneEntries(baseline);
  Object.assign(entries['journey-2-step-01'], {
    complete: true, assessment: 'OBSERVED_FAILURE', finding_severity: 'MAJOR',
    finding_disposition: 'OPEN', observed: 'The save action failed with a visible error.', comment: '',
  });
  Object.assign(entries['journey-1-step-01'], {
    complete: false, assessment: 'NOT_OBSERVED', finding_severity: 'NONE',
    finding_disposition: 'OPEN', observed: 'Walk stopped at journey-2-step-01; this step was not reached.', comment: '',
  });
  const walkStop = {
    state: 'STOPPED', halting_step_id: 'journey-2-step-01',
    unreached_step_ids: ['journey-1-step-01'], trigger: 'OBSERVED_FAILURE',
    recorded_by: 'OWNER', note: 'Stopped because saving failed.',
  };

  const ordinary = buildFeedbackCommand(entries, 'b'.repeat(64), 'a'.repeat(64));
  assert.equal('walk_stop' in ordinary, false, 'a failure-shaped draft never infers a stop');
  const stopped = buildFeedbackCommand(entries, 'b'.repeat(64), 'a'.repeat(64), walkStop);
  assert.deepEqual(stopped.walk_stop, walkStop);
  assert.notEqual(stopped.walk_stop, walkStop);
  assert.notEqual(stopped.walk_stop.unreached_step_ids, walkStop.unreached_step_ids);
});

test('stopped-walk validation mirrors the exact server grammar', () => {
  const draft = cloneEntries(baseline);
  Object.assign(draft['journey-2-step-01'], {
    complete: true, assessment: 'OBSERVED_FAILURE', finding_severity: 'MAJOR',
    finding_disposition: 'OPEN', observed: 'The save action failed with a visible error.', comment: '',
  });
  Object.assign(draft['journey-1-step-01'], {
    complete: false, assessment: 'NOT_OBSERVED', finding_severity: 'NONE',
    finding_disposition: 'OPEN', observed: 'Walk stopped at journey-2-step-01; this step was not reached.', comment: '',
  });
  const stop = {
    state: 'STOPPED', halting_step_id: 'journey-2-step-01', unreached_step_ids: ['journey-1-step-01'],
    trigger: 'OBSERVED_FAILURE', recorded_by: 'OWNER', note: 'Stopped because saving failed.',
  };
  assert.equal(validateDraft(draft, Object.keys(baseline), feedbackResultContract, stop).valid, true);

  const badHalt = structuredClone(draft);
  badHalt['journey-2-step-01'].observed = '   ';
  assert.match(validateDraft(badHalt, Object.keys(baseline), feedbackResultContract, stop).errors['journey-2-step-01-observed'], /real observation/i);

  const dressedUp = structuredClone(draft);
  Object.assign(dressedUp['journey-1-step-01'], { complete: true, assessment: 'ALIGNED', finding_severity: 'NONE', finding_disposition: 'SATISFIED' });
  assert.match(validateDraft(dressedUp, Object.keys(baseline), feedbackResultContract, stop).errors['journey-1-step-01-stop'], /not reached/i);

  const notedPartial = structuredClone(draft);
  notedPartial['journey-1-step-01'] = { complete: false, assessment: '', finding_severity: '', finding_disposition: '', observed: '', comment: 'I observed this before stopping.' };
  notedPartial['journey-3-step-01'] = { complete: false, assessment: 'NOT_OBSERVED', finding_severity: 'NONE', finding_disposition: 'OPEN', observed: 'Walk stopped at journey-2-step-01; this step was not reached.', comment: '' };
  const partialStop = { ...stop, unreached_step_ids: ['journey-3-step-01'] };
  assert.equal(validateDraft(notedPartial, [...Object.keys(baseline), 'journey-3-step-01'], feedbackResultContract, partialStop).valid, true, 'owner-entered partial content remains editable and is not unreached');

  const mismatch = { ...stop, unreached_step_ids: [] };
  assert.match(validateDraft(draft, Object.keys(baseline), feedbackResultContract, mismatch).errors.form, /at least one unreached/i);
});

test('stopped-walk validation derives unreached ids from genuinely empty cards, not incomplete partial cards', () => {
  const draft = cloneEntries(baseline);
  Object.assign(draft['journey-2-step-01'], { complete: true, assessment: 'OBSERVED_FAILURE', finding_severity: 'MAJOR', finding_disposition: 'OPEN', observed: 'The save action failed with a visible error.' });
  draft['journey-1-step-01'] = { complete: false, assessment: '', finding_severity: '', finding_disposition: '', observed: '', comment: 'Owner note survives.' };
  const stop = { state: 'STOPPED', halting_step_id: 'journey-2-step-01', unreached_step_ids: [], trigger: 'OBSERVED_FAILURE', recorded_by: 'OWNER', note: 'Stopped because saving failed.' };
  assert.match(validateDraft(draft, Object.keys(baseline), feedbackResultContract, stop).errors.form, /at least one unreached/i);
});

test('draft validation permits completion with no owner-entered fields', () => {
  const textOnly = cloneEntries(baseline);
  Object.assign(textOnly['journey-1-step-01'], {
    complete: true,
    observed: 'The owner recorded only what they saw.',
  });
  const textOnlyValidation = validateDraft(textOnly, Object.keys(baseline), feedbackResultContract);
  assert.deepEqual(textOnlyValidation.errors, {}, 'observation-only completion has no synthetic classification errors');
  assert.equal(textOnlyValidation.valid, true);

  const empty = cloneEntries(baseline);
  empty['journey-1-step-01'].complete = true;
  const emptyValidation = validateDraft(empty, Object.keys(baseline), feedbackResultContract);
  assert.deepEqual(emptyValidation.errors, {}, 'empty completion is genuinely valid and submittable');
  assert.equal(emptyValidation.valid, true);
});

test('assessment severity options are derived from contract cells so no offered pair has an empty disposition', () => {
  assert.equal(typeof uatRounds.severitiesFromResultContract, 'function');
  for (const assessment of ['ALIGNED', 'OBSERVED_FAILURE', 'BLOCKED', 'NOT_OBSERVED', 'NOT_APPLICABLE']) {
    const severities = uatRounds.severitiesFromResultContract(feedbackResultContract, assessment);
    assert.ok(severities.length > 0, `${assessment} must offer at least one contract-backed severity`);
    for (const severity of severities) {
      assert.ok(
        uatRounds.allowedDispositions(assessment, severity, feedbackResultContract).length > 0,
        `${assessment}/${severity} was offered even though it has no disposition`,
      );
    }
  }
  assert.deepEqual(uatRounds.severitiesFromResultContract(feedbackResultContract, 'ALIGNED'), ['NONE', 'MINOR']);
  assert.deepEqual(uatRounds.severitiesFromResultContract(feedbackResultContract, 'OBSERVED_FAILURE'), ['MINOR', 'MAJOR']);
  assert.deepEqual(uatRounds.severitiesFromResultContract(feedbackResultContract, 'BLOCKED'), ['NONE']);
  assert.deepEqual(uatRounds.severitiesFromResultContract(feedbackResultContract, 'NOT_OBSERVED'), ['NONE']);
  assert.deepEqual(uatRounds.severitiesFromResultContract(feedbackResultContract, 'NOT_APPLICABLE'), ['NONE']);
});

test('step card state is independently gray empty, yellow partial, or green strict-complete', () => {
  const controller = createFeedbackController(activeView);
  let ui = deriveFeedbackUi(controller, Object.keys(baseline));
  assert.deepEqual(ui.cardStates, {
    'journey-1-step-01': 'gray',
    'journey-2-step-01': 'green',
  });

  const partial = cloneEntries(controller.draft);
  partial['journey-1-step-01'].observed = 'Partial observation';
  ui = deriveFeedbackUi(
    transitionFeedbackController(controller, { type: 'draft-changed', draft: partial }),
    Object.keys(baseline),
  );
  assert.equal(ui.cardStates['journey-1-step-01'], 'yellow');
  assert.equal(ui.cardStates['journey-2-step-01'], 'green');
  assert.equal(ui.canSave, true, 'one dirty partial card enables Save without completing other cards');
});

test('dirty comparison covers every step field and exact retained comments', () => {
  const draft = cloneEntries(baseline);
  assert.equal(isDraftDirty(draft, baseline), false);
  draft['journey-2-step-01'].comment = 'Owner draft must survive failures.';
  assert.equal(isDraftDirty(draft, baseline), true);
});

test('typed failures map to explicit owner recovery states', () => {
  assert.equal(describeFailure('stale_feedback').state, 'stale-feedback');
  assert.equal(describeFailure('stale_round').state, 'stale-round');
  assert.equal(describeFailure('feedback_processed').state, 'processed');
  assert.equal(describeFailure('invalid_feedback_update').state, 'validation');
  assert.equal(describeFailure('feedback_write_failed').state, 'write-error');
  assert.equal(describeFailure('feedback_commit_uncertain').state, 'commit-uncertain');
  assert.equal(describeFailure('offline').state, 'offline');
});

const activeView = {
  schema: 'jswarm.test-uat.active-round-view/v2', schema_version: '2.0',
  round_review_id: 'DEMO-391/round-001', ticket: 'DEMO-391', round_status: 'NOT_STARTED', projection_sha256: 'c'.repeat(64), feedback_result_contract: feedbackResultContract,
  current_round: {
    path: '.jswarm/plans/DEMO-391/DEMO-391.UAT-CURRENT-ROUND.md', sha256: 'a'.repeat(64), size: 100,
    package_state: 'ISSUED', source: '# Canonical round', normalized_package: {}, writable: false,
    journeys: [
      {
        journey_id: 'journey-1', title: 'Read-only active context',
        known_items: [{ text: 'Known journey limitation.', source_ref: 'DEMO-391.uat-test.md#known' }],
        scenarios: [
          { scenario_id: 'UAT-391-1', title: 'Inspect the active round', gwt: [
            { gwt_ref: 'd'.repeat(64), sha256: 'd'.repeat(64), given: ['An active round', 'A selected journey'], when: ['It is opened'], then: ['Its exact source is visible', 'Clause order is preserved'] },
            { gwt_ref: 'f'.repeat(64), sha256: 'f'.repeat(64), given: ['An unrelated state'], when: ['Another action occurs'], then: ['This block must not render for the step'] },
          ] },
          { scenario_id: 'UAT-391-1-ALT', title: 'Alternative linked scenario', gwt: [
            { gwt_ref: '1'.repeat(64), sha256: '1'.repeat(64), given: ['An alternate state'], when: ['The linked step runs', 'The dialog opens'], then: ['The alternate result appears'] },
          ] },
        ],
        steps: [{ step_id: 'journey-1-step-01', ordinal: 1, name: 'Open exact source', instruction: 'Open the exact source dialog.', expected_outcome: 'Literal source text is visible.', scenario_links: [
          { scenario_id: 'UAT-391-1', gwt_refs: ['d'.repeat(64)] },
          { scenario_id: 'UAT-391-1-ALT', gwt_refs: ['1'.repeat(64)] },
        ] }],
      },
      {
        journey_id: 'journey-2', title: 'Save aligned feedback',
        known_items: [],
        scenarios: [{ scenario_id: 'UAT-391-2', title: 'Save feedback', gwt: [{ gwt_ref: 'e'.repeat(64), sha256: 'e'.repeat(64), given: ['A writable round'], when: ['Feedback is saved'], then: ['The digest changes'] }] }],
        steps: [{ step_id: 'journey-2-step-01', ordinal: 1, name: 'Save feedback', instruction: 'Save the completed card.', expected_outcome: 'A fresh digest appears.', scenario_links: [{ scenario_id: 'UAT-391-2', gwt_refs: ['e'.repeat(64)] }] }],
      },
    ],
  },
  feedback: {
    path: '.jswarm/plans/DEMO-391/DEMO-391.uat-feedback.md', sha256: 'b'.repeat(64), size: 80,
    processing_state: 'UNPROCESSED', round_status: 'NOT_STARTED', step_entries: baseline, walk_stop: null,
    step_counts: { total: 2, gray: 1, yellow: 0, green: 1 }, markerized: true, writable: true,
  },
  capabilities: { feedback_update: true },
};

test('active round parser accepts v2 step entries and rejects unsupported versions', () => {
  const parsed = parseActiveRoundView(activeView);
  assert.equal(parsed.schema_version, '2.0');
  assert.equal(parsed.round_status, 'NOT_STARTED');
  assert.equal(parsed.feedback.round_status, 'NOT_STARTED');
  assert.deepEqual(parsed.feedback.step_counts, { total: 2, gray: 1, yellow: 0, green: 1 });
  assert.equal(parsed.feedback.walk_stop, null);
  assert.equal(parsed.current_round.journeys[0].title, 'Read-only active context');
  assert.equal(parsed.current_round.journeys[0].steps[0].step_id, 'journey-1-step-01');
  assert.deepEqual(parsed.current_round.journeys[0].known_items, [{ text: 'Known journey limitation.', source_ref: 'DEMO-391.uat-test.md#known' }]);
  assert.deepEqual(parsed.current_round.journeys[1].known_items, []);
  assert.throws(() => parseActiveRoundView({ ...activeView, schema_version: '3.0' }), /unsupported active UAT round schema\/version/);
  const missingContract = structuredClone(activeView);
  delete missingContract.feedback_result_contract;
  assert.throws(
    () => parseActiveRoundView(missingContract),
    /feedback_result_contract is required for active UAT round view/,
  );
});

test('safeAppUrl accepts the documented "N/A" no-application escape hatch and still rejects everything else unsafe', () => {
  // Mirrors uat_round_materialize.py::_validate_app_url's documented
  // escape hatch for a ticket with no running application: "N/A" must be
  // accepted literally, not fed to `new URL()` (which throws for it).
  assert.equal(uatRounds.safeAppUrl('N/A', 'app_url'), 'N/A');
  assert.equal(uatRounds.safeAppUrl('http://localhost:3000', 'app_url'), 'http://localhost:3000');
  assert.throws(() => uatRounds.safeAppUrl('', 'app_url'), /app_url must be a safe absolute HTTP\(S\) URL or the literal "N\/A"/);
  assert.throws(() => uatRounds.safeAppUrl('not a url', 'app_url'), /app_url must be a safe absolute HTTP\(S\) URL or the literal "N\/A"/);
  assert.throws(() => uatRounds.safeAppUrl('ftp://example.com', 'app_url'), /app_url must be a safe absolute HTTP\(S\) URL or the literal "N\/A"/);
  assert.throws(() => uatRounds.safeAppUrl('javascript:alert(1)', 'app_url'), /app_url must be a safe absolute HTTP\(S\) URL or the literal "N\/A"/);
});

test('active round parser honors the "N/A" app_url escape hatch end to end and still renders real round content', () => {
  const noAppView = structuredClone(activeView);
  noAppView.current_round.normalized_package = { app_url: 'N/A' };
  // Before the fix, this threw a plain TypeError from `new URL("N/A")` and
  // the whole round -- including its real journeys/steps -- never parsed.
  const parsed = parseActiveRoundView(noAppView);
  assert.equal(parsed.current_round.app_url, 'N/A');
  assert.equal(parsed.current_round.journeys[0].title, 'Read-only active context');
  assert.equal(parsed.current_round.journeys[0].steps[0].step_id, 'journey-1-step-01');
  assert.equal(parsed.current_round.journeys[1].steps[0].step_id, 'journey-2-step-01');

  // The fallback "open application base" link resolves to the literal "N/A"
  // marker -- the component layer is responsible for not rendering that as
  // a broken anchor, but the library must not throw producing it.
  const effective = uatRounds.resolveEffectiveAppLink(
    parsed.current_round.journeys[0].steps[0],
    parsed.current_round.journeys[0],
    parsed.current_round.app_url,
  );
  assert.deepEqual(effective, { href: 'N/A', label: 'Open application base', source: 'base' });

  // A same-origin-relative app_link (the documented convention, e.g. "/")
  // is still valid on top of an "N/A" app_url.
  const withRelativeLink = structuredClone(noAppView);
  withRelativeLink.current_round.journeys[0].app_link = { href: '/', label: 'App' };
  const parsedWithLink = parseActiveRoundView(withRelativeLink);
  assert.deepEqual(parsedWithLink.current_round.journeys[0].app_link, { href: '/', label: 'App' });

  // An absolute app_link cannot be resolved against a nonexistent "N/A"
  // origin, so it is correctly rejected rather than silently accepted.
  const withAbsoluteLink = structuredClone(noAppView);
  withAbsoluteLink.current_round.journeys[0].app_link = { href: 'https://example.com/', label: 'App' };
  assert.throws(() => parseActiveRoundView(withAbsoluteLink), /app_link\.href must be a safe same-origin link/);
});

test('round status parser and command use the exact shared four-state contract', async () => {
  assert.deepEqual(uatRounds.ROUND_STATUS_OPTIONS, [
    ['NOT_STARTED', 'NOT STARTED'],
    ['IN_PROGRESS', 'IN PROGRESS'],
    ['COMPLETE', 'COMPLETE'],
    ['FAILED', 'FAILED'],
  ]);
  const parsed = parseRoundList({
    rounds: [{
      round_review_id: 'DEMO-391/round-status', ticket: 'DEMO-391', package_state: 'ISSUED',
      current_round_sha256: 'a'.repeat(64), feedback_sha256: 'b'.repeat(64),
      processing_state: 'UNPROCESSED', round_status: 'IN_PROGRESS', writable: true,
    }],
    warnings: [],
  });
  assert.equal(parsed.rounds[0].round_status, 'IN_PROGRESS');
  assert.throws(() => parseRoundList({ rounds: [{ ...parsed.rounds[0], round_status: 'UNPROCESSED' }], warnings: [] }), /round_status/);

  const command = buildRoundStatusCommand('FAILED', 'b'.repeat(64), 'a'.repeat(64));
  assert.deepEqual(command, {
    schema: 'jswarm.test-uat.round-status-update/v1',
    schema_version: '1.0',
    expected_feedback_sha256: 'b'.repeat(64),
    expected_current_round_sha256: 'a'.repeat(64),
    round_status: 'FAILED',
  });

  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push([String(url), init]);
    return new Response(JSON.stringify({ status: 'saved', view: { ...activeView, round_status: 'FAILED', feedback: { ...activeView.feedback, round_status: 'FAILED' } } }), { status: 200 });
  };
  try {
    const saved = await updateRoundStatus('DEMO-391/round-status', command);
    assert.equal(saved.status, 'saved');
    assert.equal(saved.view.round_status, 'FAILED');
    assert.equal(calls[0][0], '/api/uat-rounds/DEMO-391%2Fround-status/status');
    assert.equal(calls[0][1].method, 'POST');
    assert.deepEqual(JSON.parse(calls[0][1].body), command);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('linked scenario blocks preserve link and clause order and exclude unlinked GWT blocks', () => {
  assert.equal(typeof uatRounds.resolveLinkedScenarioBlocks, 'function');
  const journey = activeView.current_round.journeys[0];
  const step = journey.steps[0];

  const linked = uatRounds.resolveLinkedScenarioBlocks(journey, step);

  assert.deepEqual(linked.map((item) => item.scenario_id), ['UAT-391-1', 'UAT-391-1-ALT']);
  assert.deepEqual(linked.map((item) => item.gwt.map((block) => block.gwt_ref)), [
    ['d'.repeat(64)], ['1'.repeat(64)],
  ]);
  assert.deepEqual(linked[0].gwt[0].given, ['An active round', 'A selected journey']);
  assert.deepEqual(linked[0].gwt[0].when, ['It is opened']);
  assert.deepEqual(linked[0].gwt[0].then, ['Its exact source is visible', 'Clause order is preserved']);
  assert.equal(JSON.stringify(linked).includes('f'.repeat(64)), false, 'unlinked GWT block stays hidden');
});

test('active round parser rejects cross-scenario and unresolved step lineage', () => {
  const crossed = structuredClone(activeView);
  crossed.current_round.journeys[0].steps[0].scenario_links[0].gwt_refs = ['1'.repeat(64)];
  assert.throws(() => parseActiveRoundView(crossed), /unresolved step lineage.*UAT-391-1/);

  const missingScenario = structuredClone(activeView);
  missingScenario.current_round.journeys[0].steps[0].scenario_links[0].scenario_id = 'UAT-391-MISSING';
  assert.throws(() => parseActiveRoundView(missingScenario), /unresolved step lineage.*UAT-391-MISSING/);
});

test('checked-complete steps no longer require an owner-entered observation', () => {
  const completedWithoutObservation = cloneEntries(baseline);
  Object.assign(completedWithoutObservation['journey-1-step-01'], {
    complete: true,
    assessment: 'OBSERVED_FAILURE',
    finding_severity: 'MAJOR',
    finding_disposition: 'OPEN',
    observed: '',
  });

  let controller = createFeedbackController(activeView);
  controller = transitionFeedbackController(controller, { type: 'draft-changed', draft: completedWithoutObservation });
  const ui = deriveFeedbackUi(controller, Object.keys(baseline));
  assert.equal(ui.validation.valid, true);
  assert.equal(ui.validation.errors['journey-1-step-01-observed'], undefined);
  assert.equal(ui.cardStates['journey-1-step-01'], 'green');
  assert.equal(ui.canSave, true);
});

test('shared controller executes dirty/valid gating, submit lock, warnings, and focus intents', () => {
  let controller = createFeedbackController(activeView);
  let ui = deriveFeedbackUi(controller, Object.keys(baseline));
  assert.deepEqual({ dirty: ui.dirty, canSave: ui.canSave, duplicateSubmitLocked: ui.duplicateSubmitLocked }, { dirty: false, canSave: false, duplicateSubmitLocked: false });

  const edited = cloneEntries(controller.draft);
  edited['journey-1-step-01'].comment = 'Retained owner draft';
  controller = transitionFeedbackController(controller, { type: 'draft-changed', draft: edited });
  ui = deriveFeedbackUi(controller, Object.keys(baseline));
  assert.equal(ui.canSave, true);
  assert.equal(ui.unsavedNavigationGuard, true);
  assert.equal(ui.reloadWarningIntent, 'warn-before-replacing-draft');
  assert.equal(ui.copyDraftIntent, 'copy-complete-draft');

  const command = buildFeedbackCommand(controller.draft, activeView.feedback.sha256, activeView.current_round.sha256);
  controller = transitionFeedbackController(controller, { type: 'save-started', command });
  ui = deriveFeedbackUi(controller, Object.keys(baseline));
  assert.equal(ui.canSave, false);
  assert.equal(ui.duplicateSubmitLocked, true);
  assert.equal(ui.controlsDisabled, true);

  controller = transitionFeedbackController(controller, { type: 'save-failed', code: 'invalid_feedback_update', command });
  ui = deriveFeedbackUi(controller, Object.keys(baseline));
  assert.equal(controller.draft['journey-1-step-01'].comment, 'Retained owner draft');
  assert.equal(ui.focusTarget, 'validation-summary');
});

test('shared controller resets baseline on saved/idempotent and disables processed views', () => {
  for (const status of ['saved', 'idempotent']) {
    let controller = createFeedbackController(activeView);
    const edited = cloneEntries(controller.draft);
    edited['journey-2-step-01'].comment = `Confirmed ${status}`;
    controller = transitionFeedbackController(controller, { type: 'draft-changed', draft: edited });
    const freshView = structuredClone(activeView);
    freshView.feedback.step_entries = cloneEntries(edited);
    freshView.feedback.sha256 = 'd'.repeat(64);
    controller = transitionFeedbackController(controller, { type: 'save-succeeded', status, view: freshView });
    const ui = deriveFeedbackUi(controller, Object.keys(baseline));
    assert.equal(ui.dirty, false);
    assert.equal(ui.canSave, false);
    assert.equal(ui.saveOutcome, status);
    assert.deepEqual(controller.baseline, edited);
  }

  const processedView = structuredClone(activeView);
  processedView.feedback.processing_state = 'PROCESSED';
  processedView.feedback.writable = false;
  processedView.capabilities.feedback_update = false;
  const processedController = createFeedbackController(processedView);
  assert.equal(deriveFeedbackUi(processedController, Object.keys(baseline)).controlsDisabled, true);
});

test('shared controller retains drafts for conflict/write/offline failures and selects alert focus', () => {
  for (const code of ['stale_feedback', 'stale_round', 'feedback_processed', 'feedback_write_failed', 'offline']) {
    let controller = createFeedbackController(activeView);
    const edited = cloneEntries(controller.draft);
    edited['journey-1-step-01'].comment = `Draft for ${code}`;
    controller = transitionFeedbackController(controller, { type: 'draft-changed', draft: edited });
    const command = buildFeedbackCommand(edited, activeView.feedback.sha256, activeView.current_round.sha256);
    controller = transitionFeedbackController(controller, { type: 'save-started', command });
    controller = transitionFeedbackController(controller, { type: 'save-failed', code, command });
    const ui = deriveFeedbackUi(controller, Object.keys(baseline));
    assert.equal(controller.draft['journey-1-step-01'].comment, `Draft for ${code}`);
    assert.equal(ui.focusTarget, 'request-alert');
    assert.equal(ui.copyDraftIntent, 'copy-complete-draft');
  }
});

test('commit uncertainty retains the exact posted command for object-equivalent retry', async () => {
  const originalFetch = globalThis.fetch;
  const postedBodies = [];
  globalThis.fetch = async (_url, init = {}) => {
    postedBodies.push(init.body);
    if (postedBodies.length === 1) return new Response(JSON.stringify({ error: { code: 'feedback_commit_uncertain', message: 'directory fsync failed' } }), { status: 500 });
    return new Response(JSON.stringify({ status: 'idempotent', view: activeView }), { status: 200 });
  };
  try {
    let controller = createFeedbackController(activeView);
    const postedDraft = cloneEntries(controller.draft);
    postedDraft['journey-1-step-01'].comment = 'Exact posted bytes';
    controller = transitionFeedbackController(controller, { type: 'draft-changed', draft: postedDraft });
    const firstPost = buildFeedbackCommand(postedDraft, 'b'.repeat(64), 'a'.repeat(64));
    controller = transitionFeedbackController(controller, { type: 'save-started', command: firstPost });
    await assert.rejects(saveFeedback(activeView.round_review_id, firstPost), (error) => error.code === 'feedback_commit_uncertain');
    controller = transitionFeedbackController(controller, { type: 'save-failed', code: 'feedback_commit_uncertain', command: firstPost });

    const surroundingDraft = cloneEntries(postedDraft);
    surroundingDraft['journey-1-step-01'].comment = 'MUTATED AFTER POST';
    const surroundingView = structuredClone(activeView);
    surroundingView.feedback.sha256 = 'e'.repeat(64);
    surroundingView.current_round.sha256 = 'f'.repeat(64);
    controller = transitionFeedbackController(controller, { type: 'draft-changed', draft: surroundingDraft });
    controller = { ...controller, view: surroundingView };

    const ui = deriveFeedbackUi(controller, Object.keys(baseline));
    assert.deepEqual(ui.retryCommand, firstPost);
    assert.equal(ui.retryCommand.expected_feedback_sha256, 'b'.repeat(64));
    assert.equal(ui.retryCommand.expected_current_round_sha256, 'a'.repeat(64));
    assert.equal(ui.retryCommand.step_entries['journey-1-step-01'].comment, 'Exact posted bytes');
    assert.equal(ui.controlsDisabled, false, 'commit uncertainty preserves editable owner content');
    assert.equal(controller.draft['journey-1-step-01'].comment, 'MUTATED AFTER POST');
    assert.equal(ui.canSave, true, 'exact persist-only retry remains available while newer edits stay local');

    await saveFeedback(activeView.round_review_id, ui.retryCommand);
    assert.equal(postedBodies[0], postedBodies[1], 'retry serializes the exact same command payload as the uncertain POST');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('runtime API client uses same-origin routes and accepts saved/idempotent fresh views', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push([url, init]);
    if (String(url) === '/api/uat-rounds') return new Response(JSON.stringify({ rounds: [], warnings: [] }), { status: 200 });
    if (String(url).endsWith('/feedback')) return new Response(JSON.stringify({ status: 'idempotent', view: activeView }), { status: 200 });
    return new Response(JSON.stringify(activeView), { status: 200 });
  };
  try {
    assert.deepEqual(await fetchRoundList(), { rounds: [], warnings: [] });
    assert.equal((await fetchRoundDetail('DEMO-391/round-001')).current_round.writable, false);
    const saved = await saveFeedback('DEMO-391/round-001', buildFeedbackCommand(baseline, 'b'.repeat(64), 'a'.repeat(64)));
    assert.equal(saved.status, 'idempotent');
    assert.equal(calls[1][0], '/api/uat-rounds/DEMO-391%2Fround-001');
    assert.equal(calls[2][1].method, 'POST');
    const posted = JSON.parse(calls[2][1].body);
    assert.equal('current_round' in posted, false);
    assert.equal('normalized_package' in posted, false);
    assert.equal('processing_state' in posted, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('runtime API client preserves typed failures without exposing submitted comments', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: 'stale_feedback', message: 'feedback changed' } }), { status: 409 });
  try {
    await assert.rejects(fetchRoundDetail('DEMO-391/round-001'), (error) => {
      assert.ok(error instanceof UatApiError);
      assert.equal(error.code, 'stale_feedback');
      assert.equal(error.status, 409);
      return true;
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('runtime shell has stable selectors, immutable region, and no comment logging', async () => {
  const component = await readFile(new URL('../src/components/uat/ActiveUatWorkbench.astro', import.meta.url), 'utf8');
  const page = await readFile(new URL('../src/pages/uat/index.astro', import.meta.url), 'utf8');
  const index = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');

  for (const selector of [
    'data-uat-round-list', 'data-uat-round-card', 'data-uat-current-round',
    'data-uat-current-round-source', 'data-uat-feedback-form', 'data-uat-journey',
    'data-uat-journey-heading', 'data-uat-scenario-square', 'data-uat-step-card',
    'data-uat-step-heading', 'data-uat-step-caret', 'data-uat-step-state',
    'data-uat-complete', 'data-uat-assessment', 'data-uat-severity',
    'data-uat-disposition', 'data-uat-observed', 'data-uat-comment',
    'data-uat-save', 'data-uat-save-state', 'data-uat-validation-summary',
    'data-uat-stale', 'data-uat-reload-latest', 'data-uat-copy-draft',
    'data-uat-stop-action', 'data-uat-stop-confirmation', 'data-uat-stop-note',
    'data-uat-stop-unreached-list', 'data-uat-stop-confirm', 'data-uat-stop-cancel', 'data-uat-stop-undo',
  ]) assert.match(component, new RegExp(selector));

  assert.match(component, /Read-only canonical current round/);
  assert.match(component, /data-uat-date-bands/);
  assert.match(component, /data-uat-date-band/);
  assert.match(component, /data-uat-round-grid/);
  assert.match(component, /data-uat-round-card-action/);
  assert.ok(component.indexOf('data-uat-round-list') < component.indexOf('data-uat-workbench'), 'full-width queue precedes selected-round detail');
  assert.doesNotMatch(component, /lg:grid-cols-\[minmax\(15rem,0\.34fr\)_minmax\(0,1fr\)\]/, 'old permanent sidebar split is removed');
  assert.match(component, /data-uat-round-summary/);
  assert.match(component, /data-uat-identity-details/);
  assert.match(component, /data-uat-journey-plan-details/);
  assert.match(component, /data-uat-source-details/);
  assert.match(component, /Canonical identity/);
  assert.match(component, /Journey plan/);
  assert.match(component, /Exact source/);
  assert.match(component, /data-uat-journey-details/);
  assert.match(component, /data-uat-journey-feedback-summary/);
  assert.match(component, /updateJourneyFeedbackSummary/);
  assert.match(component, /grid min-w-0 max-w-full gap-4 md:grid-cols-2/);
  assert.match(component, /journey\.title/);
  assert.match(component, /step\.name/);
  assert.match(component, /step\.ordinal/);
  assert.match(component, /scenario\.scenario_id/);
  assert.match(component, /resolveLinkedScenarioBlocks/);
  assert.match(component, /for \(const linkedScenario of resolveLinkedScenarioBlocks/);
  assert.match(component, /step\.scenario_links/);
  assert.match(component, /data-uat-gwt-clause-list/);
  assert.match(component, /block\.(?:given|when|then)/);
  assert.doesNotMatch(component, /heading\.textContent\s*=\s*journey\.journey_id/);
  assert.match(component, /sourceText\.textContent\s*=\s*nextView\.current_round\.source/);
  assert.doesNotMatch(component, /sourceText\.innerHTML/);
  assert.match(component, /sourceDialog\.showModal\(\)/);
  assert.match(component, /min-w-0 max-w-full/);
  assert.match(component, /Cards with content stay exactly as you entered them/);
  assert.doesNotMatch(component, /replace any draft text|locked by the stopped-walk|unreached\.has/,
    'stopped walks do not replace or lock owner-entered cards');
  assert.match(component, /control\.disabled = disabled/,
    'only saving or read-only state disables card controls');
  assert.match(component, /max-w-full break-words/);
  assert.match(component, /overflow-x-auto/);
  assert.match(component, /min-w-0 \[overflow-wrap:anywhere\].*String\(action\)/);
  assert.match(component, /beforeunload/);
  assert.doesNotMatch(component, /console\.(?:log|info|warn|error|debug)/);
  assert.match(page, /ActiveUatWorkbench/);
  assert.match(index, /href="\/uat\/"/);
});


test('generated front page requires the final lifecycle graphic and peer area card contract', async () => {
  await execFileAsync('npm', ['run', 'build'], { cwd: projectRoot });
  const index = await readFile(new URL('../dist/index.html', import.meta.url), 'utf8');

  assert.match(index, /data-fix-uat-lifecycle/);
  assert.match(index, /aria-label="FIX and UAT TEST lifecycle"/);
  assert.match(index, /data-lifecycle-loop="continuous"/);
  // One continuous figure-eight: exactly one crossing band and exactly one lobe
  // per side. Two of either would mean overlaid/concentric loops again.
  assert.match(index, /data-lifecycle-shape="infinity"/);
  assert.equal((index.match(/id="lifecycle-band-path"/g) ?? []).length, 1, 'exactly one crossing band');
  assert.equal((index.match(/data-lifecycle-half="fix"/g) ?? []).length, 1, 'one FIX lobe, never overlaid groups');
  assert.equal((index.match(/data-lifecycle-half="uat"/g) ?? []).length, 1, 'one UAT lobe, never overlaid groups');
  for (const stage of ['diagnose', 'contract', 'repair', 'prove', 'prepare', 'execute', 'observe', 'feedback']) {
    assert.match(index, new RegExp(`data-lifecycle-stage="${stage}"`), `stage ${stage} is labelled`);
  }
  assert.match(index, /data-lifecycle-half="fix"[^>]*data-color="blue"/);
  assert.match(index, /data-lifecycle-half="uat"[^>]*data-color="green"/);
  assert.match(index, /FIX cycle/);
  assert.match(index, /UAT TEST cycle/);
  assert.match(index, /FIX verification/);
  assert.match(index, /findings\s*\/\s*feedback/);
  assert.match(index, /data-lifecycle-direction="fix-to-uat"/);
  assert.match(index, /data-lifecycle-direction="uat-to-fix"/);
  assert.doesNotMatch(index, /cycle-path cycle-fix|cycle-path cycle-uat/);
  const lifecycleSection = index.match(/<section[^>]*data-fix-uat-lifecycle[\s\S]*?<\/section>/)?.[0] ?? '';
  const lifecycleAnchors = [...lifecycleSection.matchAll(/<a\b[^>]*>/g)].map(([tag]) => tag);
  assert.equal(lifecycleAnchors.length, 4, 'only the desktop and mobile FIX/UAT caption links');
  assert.deepEqual(
    lifecycleAnchors.map((tag) => tag.match(/\bhref="([^"]+)"/)?.[1]),
    ['/fix/', '/uat/', '/fix/', '/uat/'],
    'caption links target the FIX and UAT pages in both responsive variants',
  );
  assert.deepEqual(
    lifecycleAnchors.map((tag) => tag.match(/\bdata-lifecycle-caption-link="([^"]+)"/)?.[1]),
    ['fix', 'uat', 'fix', 'uat'],
    'every lifecycle anchor is an intended caption link',
  );
  assert.match(index, /data-fix-decision-review-area/);
  assert.match(index, /data-test-uat-feedback-area/);
  assert.match(index, /FIX DECISION REVIEW/);
  assert.match(index, /TEST UAT FEEDBACK/);
  assert.match(index, /data-order="newest-first"/);
  assert.match(index, /data-fix-summary-card/);
  assert.match(index, /href="\/review\//);
  assert.match(index, /data-test-uat-feedback-area/);
  assert.match(index, /data-uat-summary-status/);
  assert.match(index, /data-uat-summary-warnings/);
  assert.match(index, /data-uat-summary-list/);
  assert.doesNotMatch(index, /DEMO-391\/round-newer|DEMO-391\/round-older/);
  assert.match(index, /prefers-reduced-motion/);
  assert.match(index, /320px/);
});

// --- A/C 6: durable long-session form state ---------------------------------

function memoryStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(key, String(value)); },
    removeItem: (key) => { map.delete(key); },
    get size() { return map.size; },
    keys: () => [...map.keys()],
  };
}

test('unsent step edits persist to durable local storage bound to round and baseline', () => {
  const storage = memoryStorage();
  let controller = createFeedbackController(activeView);
  const draft = cloneEntries(controller.draft);
  draft['journey-1-step-01'].comment = 'Half-written note the owner has not sent yet.';
  controller = transitionFeedbackController(controller, { type: 'draft-changed', draft });

  uatRounds.persistLocalDraft(storage, controller, '2026-08-29T08:00:00Z');
  const raw = JSON.parse(storage.getItem(uatRounds.draftStorageKey(activeView.round_review_id)));
  assert.equal(raw.schema, uatRounds.LOCAL_DRAFT_SCHEMA);
  assert.equal(raw.round_review_id, activeView.round_review_id);
  assert.equal(raw.baseline_feedback_sha256, activeView.feedback.sha256);
  assert.equal(raw.baseline_current_round_sha256, activeView.current_round.sha256);
  assert.equal(raw.step_entries['journey-1-step-01'].comment, 'Half-written note the owner has not sent yet.');
});

test('a matching draft restores after refresh as an explicit local draft, never as canonical sent feedback', () => {
  const storage = memoryStorage();
  let controller = createFeedbackController(activeView);
  const draft = cloneEntries(controller.draft);
  draft['journey-1-step-01'].observed = 'Survives a refresh.';
  controller = transitionFeedbackController(controller, { type: 'draft-changed', draft });
  uatRounds.persistLocalDraft(storage, controller, '2026-08-29T08:00:00Z');

  // A brand-new page load only knows the canonical view.
  const reloaded = uatRounds.restoreLocalDraft(storage, createFeedbackController(activeView));
  assert.equal(reloaded.status, 'restored');
  assert.equal(reloaded.state.draft['journey-1-step-01'].observed, 'Survives a refresh.');
  // Canonical baseline is untouched: the restored value is a draft, not a send.
  assert.equal(reloaded.state.baseline['journey-1-step-01'].observed, '');
  assert.equal(reloaded.state.saveOutcome, null);
  const ui = deriveFeedbackUi(reloaded.state, Object.keys(baseline));
  assert.equal(ui.dirty, true);
  assert.equal(ui.draftStatus, 'local-draft-unsent');
});

test('a draft bound to a stale baseline is discarded and never overwrites canonical state', () => {
  const storage = memoryStorage();
  let controller = createFeedbackController(activeView);
  const draft = cloneEntries(controller.draft);
  draft['journey-1-step-01'].observed = 'Written against an older baseline.';
  controller = transitionFeedbackController(controller, { type: 'draft-changed', draft });
  uatRounds.persistLocalDraft(storage, controller, '2026-08-29T08:00:00Z');

  const moved = { ...activeView, feedback: { ...activeView.feedback, sha256: '9'.repeat(64) } };
  const restored = uatRounds.restoreLocalDraft(storage, createFeedbackController(moved));
  assert.equal(restored.status, 'stale-baseline');
  assert.equal(restored.state.draft['journey-1-step-01'].observed, '');
  assert.equal(storage.getItem(uatRounds.draftStorageKey(activeView.round_review_id)), null);
});

test('a draft can never cross rounds', () => {
  const otherRound = { ...activeView, round_review_id: 'DEMO-391/round-demo-391-002' };
  const storage = memoryStorage({
    [uatRounds.draftStorageKey(otherRound.round_review_id)]: JSON.stringify({
      schema: uatRounds.LOCAL_DRAFT_SCHEMA,
      round_review_id: activeView.round_review_id,
      baseline_feedback_sha256: activeView.feedback.sha256,
      baseline_current_round_sha256: activeView.current_round.sha256,
      saved_at: '2026-08-29T08:00:00Z',
      step_entries: cloneEntries(baseline),
    }),
  });
  const restored = uatRounds.restoreLocalDraft(storage, createFeedbackController(otherRound));
  assert.equal(restored.status, 'other-round');
  assert.equal(storage.getItem(uatRounds.draftStorageKey(otherRound.round_review_id)), null);
});

test('an unchanged or sent state clears the durable draft instead of hoarding it', () => {
  const storage = memoryStorage();
  let controller = createFeedbackController(activeView);
  const draft = cloneEntries(controller.draft);
  draft['journey-1-step-01'].comment = 'Pending.';
  controller = transitionFeedbackController(controller, { type: 'draft-changed', draft });
  uatRounds.persistLocalDraft(storage, controller, '2026-08-29T08:00:00Z');
  assert.equal(storage.size, 1);

  // A successful send makes the draft canonical, so nothing unsent remains.
  const sentView = {
    ...activeView,
    feedback: { ...activeView.feedback, sha256: 'c'.repeat(64), step_entries: cloneEntries(draft) },
  };
  const sent = transitionFeedbackController(controller, { type: 'save-succeeded', status: 'saved', view: sentView });
  uatRounds.persistLocalDraft(storage, sent, '2026-08-29T08:05:00Z');
  assert.equal(storage.size, 0);
  assert.equal(deriveFeedbackUi(sent, Object.keys(baseline)).draftStatus, 'sent');
});

test('corrupt draft bytes are discarded without breaking the round', () => {
  const storage = memoryStorage({ [uatRounds.draftStorageKey(activeView.round_review_id)]: '{not json' });
  const restored = uatRounds.restoreLocalDraft(storage, createFeedbackController(activeView));
  assert.equal(restored.status, 'invalid');
  assert.equal(restored.state.draft['journey-1-step-01'].observed, '');
  assert.equal(storage.getItem(uatRounds.draftStorageKey(activeView.round_review_id)), null);
});

test('explicit submit uses its notification route while normal saves remain persist-only', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push([String(url), init]);
    return new Response(JSON.stringify({ status: 'saved', notification: 'submitted', view: activeView }), { status: 200 });
  };
  try {
    const command = buildFeedbackCommand(baseline, 'b'.repeat(64), 'a'.repeat(64));
    await saveFeedback('DEMO-391/round-001', command);
    const submitted = await submitFeedback('DEMO-391/round-001', command);
    assert.equal(calls[0][0], '/api/uat-rounds/DEMO-391%2Fround-001/feedback');
    assert.equal(calls[1][0], '/api/uat-rounds/DEMO-391%2Fround-001/feedback/submit');
    assert.equal(submitted.notification, 'submitted');
    assert.deepEqual(JSON.parse(calls[0][1].body), JSON.parse(calls[1][1].body), 'submit reuses the closed canonical command');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('partial feedback never accepts an invalid assessment/severity/disposition triple', () => {
  const draft = cloneEntries(baseline);
  Object.assign(draft['journey-1-step-01'], {
    assessment: 'ALIGNED',
    finding_severity: 'MINOR',
    finding_disposition: 'SATISFIED',
  });
  const validation = validateDraft(draft, Object.keys(baseline), feedbackResultContract);
  assert.equal(validation.valid, false);
  assert.match(validation.errors['journey-1-step-01-finding_disposition'], /before saving/i);

  draft['journey-1-step-01'].finding_disposition = 'ACCEPTED-BY-OWNER';
  assert.equal(validateDraft(draft, Object.keys(baseline), feedbackResultContract).valid, true);
});


test('field save state starts quiet and unchanged commits never fabricate confirmation', () => {
  let state = createFieldSaveState('Canonical value');
  assert.equal(state.phase, 'idle');
  assert.equal(state.visualText, '');
  assert.equal(state.confirmedRevision, 0);

  state = transitionFieldSave(state, { type: 'input', value: 'Canonical value' });
  assert.equal(state.phase, 'idle');
  state = transitionFieldSave(state, { type: 'commit' });
  assert.equal(state.phase, 'idle');
  assert.equal(state.committedRevision, 0, 'unchanged blur does not create a save revision');
});

test('field save timing is explicit, transient, reduced-motion aware, and cancelled by editing', () => {
  let state = createFieldSaveState('Before');
  state = transitionFieldSave(state, { type: 'input', value: 'After' });
  state = transitionFieldSave(state, { type: 'commit' });
  const revision = state.committedRevision;
  assert.equal(state.phase, 'queued');
  assert.equal(state.visualText, '');

  state = transitionFieldSave(state, { type: 'request-started', revision });
  assert.equal(state.visualText, '', 'the first 299ms stay visually quiet');
  state = transitionFieldSave(state, { type: 'saving-delay-expired', revision });
  assert.equal(state.phase, 'saving');
  assert.equal(state.visualText, 'Saving…');
  state = transitionFieldSave(state, { type: 'request-succeeded', revision, value: 'After' });
  assert.equal(state.phase, 'saved');
  assert.equal(state.visualText, '✓ Saved');
  state = transitionFieldSave(state, { type: 'success-hold-expired', revision, reducedMotion: false });
  assert.equal(state.phase, 'fading');
  state = transitionFieldSave(state, { type: 'fade-expired', revision });
  assert.equal(state.phase, 'idle');
  assert.equal(state.visualText, '');

  state = transitionFieldSave(state, { type: 'input', value: 'Again' });
  state = transitionFieldSave(state, { type: 'commit' });
  const nextRevision = state.committedRevision;
  state = transitionFieldSave(state, { type: 'request-succeeded', revision: nextRevision, value: 'Again' });
  state = transitionFieldSave(state, { type: 'success-hold-expired', revision: nextRevision, reducedMotion: true });
  assert.equal(state.phase, 'idle', 'reduced motion keeps the hold but skips fading');
  state = transitionFieldSave(state, { type: 'input', value: 'Edited immediately' });
  assert.equal(state.phase, 'editing');
  assert.equal(state.visualText, '');
});

test('superseded responses cannot confirm newer edits and failures persist until retry or reversion', () => {
  let state = createFieldSaveState('Zero');
  state = transitionFieldSave(state, { type: 'input', value: 'One' });
  state = transitionFieldSave(state, { type: 'commit' });
  const revisionOne = state.committedRevision;
  state = transitionFieldSave(state, { type: 'request-started', revision: revisionOne });
  state = transitionFieldSave(state, { type: 'input', value: 'Two' });
  state = transitionFieldSave(state, { type: 'commit' });
  const revisionTwo = state.committedRevision;
  state = transitionFieldSave(state, { type: 'request-succeeded', revision: revisionOne, value: 'One' });
  assert.equal(state.phase, 'queued');
  assert.equal(state.confirmedRevision, revisionOne);
  assert.equal(state.committedRevision, revisionTwo);
  assert.equal(state.visualText, '');

  state = transitionFieldSave(state, { type: 'request-failed', revision: revisionTwo });
  assert.equal(state.phase, 'failed');
  assert.equal(state.visualText, '! Not saved');
  state = transitionFieldSave(state, { type: 'success-hold-expired', revision: revisionTwo, reducedMotion: false });
  assert.equal(state.phase, 'failed', 'failure never expires on a timer');
  state = transitionFieldSave(state, { type: 'retry' });
  assert.equal(state.phase, 'queued');
  state = transitionFieldSave(state, { type: 'request-succeeded', revision: revisionTwo, value: 'Two' });
  assert.equal(state.phase, 'saved');

  state = transitionFieldSave(state, { type: 'input', value: 'Zero' });
  assert.equal(state.phase, 'editing');
  state = transitionFieldSave(state, { type: 'input', value: 'Two' });
  state = transitionFieldSave(state, { type: 'revert' });
  assert.equal(state.phase, 'idle');
  assert.equal(state.visualText, '');
});

test('workbench renders reserved state slots and recovery routes never cross into submit', async () => {
  const component = await readFile(new URL('../src/components/uat/ActiveUatWorkbench.astro', import.meta.url), 'utf8');
  assert.match(component, /data-uat-field-save-visual/);
  assert.match(component, /:global\(\[data-uat-field-save-visual\]\)/, 'dynamic indicator slots must receive unscoped rendered CSS');
  assert.match(component, /data-uat-autosave-live/);
  assert.match(component, /data-uat-autosave-alert/);
  assert.doesNotMatch(component, /data-uat-field-save-indicator/);
  assert.match(component, /Retry autosave/);
  assert.match(component, /Retry exact autosave/);
  const failure = component.slice(component.indexOf('function showFailure'), component.indexOf('function setSelectOptions'));
  assert.doesNotMatch(failure, /submitDraft/);
  assert.match(failure, /retryAutosave/);
  assert.match(failure, /retryExactAutosave/);
  const submit = component.slice(component.indexOf('async function submitDraft'), component.indexOf('function dateBandLabel'));
  assert.match(submit, /await submitFeedback/);
});

test('practice projection exposes the safe fixture banner and reset capability', async () => {
  const fixture = structuredClone(activeView);
  fixture.capabilities.practice_fixture = true;
  fixture.fixture = { purpose: 'safe-practice-round', resettable: true };
  const parsed = parseActiveRoundView(fixture);
  assert.deepEqual(parsed.fixture, { purpose: 'safe-practice-round', resettable: true });
  assert.equal(parsed.capabilities.practice_fixture, true);
  const shell = await readFile(new URL('../src/components/uat/ActiveUatWorkbench.astro', import.meta.url), 'utf8');
  assert.match(shell, /data-uat-practice-banner/);
  assert.match(shell, /data-uat-practice-reset/);
  assert.match(shell, /no ticket agent was notified/);
});
