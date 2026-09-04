export const FEEDBACK_UPDATE_SCHEMA = "jswarm.test-uat.feedback-update/v2" as const;
export const ROUND_STATUS_UPDATE_SCHEMA = "jswarm.test-uat.round-status-update/v1" as const;
export const ROUND_STATUS_OPTIONS = [
  ["NOT_STARTED", "NOT STARTED"],
  ["IN_PROGRESS", "IN PROGRESS"],
  ["COMPLETE", "COMPLETE"],
  ["FAILED", "FAILED"],
] as const;
export type RoundStatus = typeof ROUND_STATUS_OPTIONS[number][0];

function roundStatus(value: unknown, label: string): RoundStatus {
  const status = string(value, label);
  if (!ROUND_STATUS_OPTIONS.some(([candidate]) => candidate === status)) throw new TypeError(`${label} is unsupported`);
  return status as RoundStatus;
}

export type FeedbackEntry = {
  complete: boolean;
  assessment: string;
  finding_severity: string;
  finding_disposition: string;
  observed: string;
  comment: string;
};

export type FeedbackEntries = Record<string, FeedbackEntry>;

export type WalkStop = {
  state: "STOPPED";
  halting_step_id: string;
  unreached_step_ids: string[];
  trigger: "OBSERVED_FAILURE";
  recorded_by: "OWNER" | "ORCHESTRATOR_TRANSCRIPTION";
  note: string;
};

export function cloneWalkStop(stop: WalkStop | null): WalkStop | null {
  return stop ? { ...stop, unreached_step_ids: [...stop.unreached_step_ids] } : null;
}

export type FeedbackResultContract = {
  schema_version: string;
  canonical_assessments: readonly string[];
  assessment_aliases?: Record<string, string>;
  assessment_outcomes: Record<string, string>;
  cells: readonly (readonly [string, string, string])[];
};

export function dispositionsFromResultContract(
  contract: FeedbackResultContract,
  assessment: string,
  severity: string,
): readonly string[] {
  const canonicalAssessment = contract.assessment_aliases?.[assessment] ?? assessment;
  const outcome = contract.assessment_outcomes[canonicalAssessment] ?? contract.assessment_outcomes[assessment];
  return outcome === undefined
    ? []
    : contract.cells
      .filter(([cellOutcome, cellSeverity]) => cellOutcome === outcome && cellSeverity === severity)
      .map(([, , disposition]) => disposition);
}

export function allowedDispositions(
  assessment: string,
  severity: string,
  contract: FeedbackResultContract,
): readonly string[] {
  return dispositionsFromResultContract(contract, assessment, severity);
}


export function severitiesFromResultContract(
  contract: FeedbackResultContract,
  assessment: string,
): readonly string[] {
  const canonicalAssessment = contract.assessment_aliases?.[assessment] ?? assessment;
  const outcome = contract.assessment_outcomes[canonicalAssessment] ?? contract.assessment_outcomes[assessment];
  if (outcome === undefined) return [];
  return [...new Set(
    contract.cells
      .filter(([cellOutcome]) => cellOutcome === outcome)
      .map(([, severity]) => severity),
  )];
}

export type RoundSummary = {
  round_review_id: string;
  ticket: string;
  package_state: string;
  current_round_sha256: string;
  feedback_sha256: string;
  feedback_generated_at?: string;
  processing_state: string;
  round_status: RoundStatus;
  writable: boolean;
  practice_fixture: boolean;
};

export type RoundList = { rounds: RoundSummary[]; warnings: string[] };

export type GwtClause = {
  gwt_ref: string;
  sha256: string;
  given: string[];
  when: string[];
  then: string[];
};

export type JourneyScenario = {
  scenario_id: string;
  title: string;
  gwt: GwtClause[];
};

export type StepScenarioLink = {
  scenario_id: string;
  gwt_refs: string[];
};

export type AppLink = {
  href: string;
  label?: string;
};

export type JourneyStep = {
  step_id: string;
  ordinal: number;
  name: string;
  instruction: string;
  expected_outcome: string;
  scenario_links: StepScenarioLink[];
  app_link: AppLink | null;
};

export type KnownItem = {
  text: string;
  source_ref: string;
  scope: "step" | "legacy-journey";
  step_refs: string[];
};

export type Journey = {
  journey_id: string;
  title: string;
  scenarios: JourneyScenario[];
  steps: JourneyStep[];
  known_items: KnownItem[];
  app_link: AppLink | null;
  [key: string]: unknown;
};

export type LinkedScenarioBlocks = {
  scenario_id: string;
  title: string;
  gwt: GwtClause[];
};

export type EffectiveAppLink = AppLink & { source: "step" | "journey" | "base" };

export function resolveEffectiveAppLink(step: JourneyStep, journey: Journey, appUrl: string): EffectiveAppLink {
  if (step.app_link) return { ...step.app_link, label: step.app_link.label ?? "Open step in application", source: "step" };
  if (journey.app_link) return { ...journey.app_link, label: journey.app_link.label ?? "Open journey in application", source: "journey" };
  return { href: appUrl, label: "Open application base", source: "base" };
}

export function resolveStepKnownItems(journey: Journey, step: JourneyStep): KnownItem[] {
  return journey.known_items.filter((item) => item.scope === "step" && item.step_refs.includes(step.step_id));
}

export function resolveLinkedScenarioBlocks(journey: Journey, step: JourneyStep): LinkedScenarioBlocks[] {
  return step.scenario_links.map((link) => {
    const scenarios = journey.scenarios.filter((scenario) => scenario.scenario_id === link.scenario_id);
    if (scenarios.length !== 1) {
      throw new TypeError(`unresolved step lineage: scenario ${link.scenario_id} is missing or ambiguous for ${step.step_id}`);
    }
    const scenario = scenarios[0];
    const gwt = link.gwt_refs.map((gwtRef) => {
      const blocks = scenario.gwt.filter((block) => block.gwt_ref === gwtRef);
      if (blocks.length !== 1) {
        throw new TypeError(`unresolved step lineage: GWT ${gwtRef} is missing or ambiguous in scenario ${link.scenario_id}`);
      }
      return blocks[0];
    });
    return { scenario_id: scenario.scenario_id, title: scenario.title, gwt };
  });
}

export type ActiveRoundView = {
  schema: string;
  schema_version: string;
  round_review_id: string;
  ticket: string;
  round_status: RoundStatus;
  projection_sha256: string;
  feedback_result_contract: FeedbackResultContract;
  current_round: {
    path: string;
    sha256: string;
    size: number;
    package_state: string;
    source: string;
    normalized_package: Record<string, unknown>;
    app_url: string;
    journeys: Journey[];
    writable: false;
  };
  feedback: {
    path: string;
    sha256: string;
    size: number;
    processing_state: string;
    round_status: RoundStatus;
    step_entries: FeedbackEntries;
    walk_stop: WalkStop | null;
    step_counts: { total: number; gray: number; yellow: number; green: number };
    markerized: boolean;
    writable: boolean;
  };
  capabilities: { feedback_update: boolean; practice_fixture: boolean };
  fixture?: { purpose: "safe-practice-round"; resettable: true };
};

export type FeedbackUpdateCommand = {
  schema: typeof FEEDBACK_UPDATE_SCHEMA;
  schema_version: "2.0";
  expected_feedback_sha256: string;
  expected_current_round_sha256: string;
  step_entries: FeedbackEntries;
  walk_stop?: WalkStop | null;
};

export type RoundStatusUpdateCommand = {
  schema: typeof ROUND_STATUS_UPDATE_SCHEMA;
  schema_version: "1.0";
  expected_feedback_sha256: string;
  expected_current_round_sha256: string;
  round_status: RoundStatus;
};

export type SaveResponse = { status: "saved" | "idempotent"; view: ActiveRoundView; notification?: "submitted" | "already_submitted" | "pending" | "practice_submitted" | "practice_already_submitted" };

export type FailureState =
  | "stale-feedback" | "stale-round" | "processed" | "validation"
  | "write-error" | "commit-uncertain" | "offline" | "source-error" | "unavailable";

export class UatApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "UatApiError";
    this.status = status;
    this.code = code;
  }
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${label} must be an object`);
  return value as Record<string, unknown>;
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string") throw new TypeError(`${label} must be a string`);
  return value;
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new TypeError(`${label} must be a boolean`);
  return value;
}

function utcTimestamp(value: unknown, label: string): string {
  const timestamp = string(value, label);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(timestamp) || Number.isNaN(Date.parse(timestamp))) {
    throw new TypeError(`${label} must be a canonical UTC timestamp`);
  }
  return timestamp;
}

export function sortUatSummariesNewestFirst(rounds: RoundSummary[]): RoundSummary[] {
  return [...rounds].sort((left, right) => {
    const leftGeneratedAt = left.feedback_generated_at ?? "";
    const rightGeneratedAt = right.feedback_generated_at ?? "";
    const byGeneratedAt = rightGeneratedAt.localeCompare(leftGeneratedAt);
    return byGeneratedAt || left.round_review_id.localeCompare(right.round_review_id);
  });
}

export type RoundDateBand = { date: string; rounds: RoundSummary[] };

export function groupUatSummariesByGeneratedDate(rounds: RoundSummary[]): RoundDateBand[] {
  const bands = new Map<string, RoundSummary[]>();
  for (const round of sortUatSummariesNewestFirst(rounds)) {
    const date = round.feedback_generated_at?.slice(0, 10) || "undated";
    const band = bands.get(date) ?? [];
    band.push(round);
    bands.set(date, band);
  }
  return [...bands].map(([date, groupedRounds]) => ({ date, rounds: groupedRounds }));
}

export function cloneEntries(entries: FeedbackEntries): FeedbackEntries {
  return Object.fromEntries(Object.entries(entries).map(([id, entry]) => [id, { ...entry }]));
}

export function parseRoundList(value: unknown): RoundList {
  const root = object(value, "active UAT round list");
  if (!Array.isArray(root.rounds) || !Array.isArray(root.warnings)) throw new TypeError("active UAT round list is invalid");
  return {
    rounds: root.rounds.map((item, index) => {
      const row = object(item, `round ${index + 1}`);
      return {
        round_review_id: string(row.round_review_id, "round_review_id"),
        ticket: string(row.ticket, "ticket"),
        package_state: string(row.package_state, "package_state"),
        current_round_sha256: string(row.current_round_sha256, "current_round_sha256"),
        feedback_sha256: string(row.feedback_sha256, "feedback_sha256"),
        ...(row.feedback_generated_at === undefined
          ? {}
          : { feedback_generated_at: utcTimestamp(row.feedback_generated_at, "feedback_generated_at") }),
        processing_state: string(row.processing_state, "processing_state"),
        round_status: roundStatus(row.round_status, "round_status"),
        writable: boolean(row.writable, "writable"),
        practice_fixture: row.practice_fixture === undefined ? false : boolean(row.practice_fixture, "practice_fixture"),
      };
    }),
    warnings: root.warnings.map((warning, index) => string(warning, `warning ${index + 1}`)),
  };
}

function parseEntries(value: unknown): FeedbackEntries {
  const entries = object(value, "feedback step entries");
  return Object.fromEntries(Object.entries(entries).map(([stepId, raw]) => {
    const entry = object(raw, `feedback step entry ${stepId}`);
    return [stepId, {
      complete: boolean(entry.complete, "complete"),
      assessment: entry.assessment === null ? "" : string(entry.assessment, "assessment"),
      finding_severity: entry.finding_severity === null ? "" : string(entry.finding_severity, "finding_severity"),
      finding_disposition: entry.finding_disposition === null ? "" : string(entry.finding_disposition, "finding_disposition"),
      observed: string(entry.observed, "observed"),
      comment: string(entry.comment, "comment"),
    }];
  }));
}

function parseFeedbackResultContract(value: unknown): FeedbackResultContract {
  const contract = object(value, "feedback_result_contract");
  const outcomes = object(contract.assessment_outcomes, "feedback_result_contract.assessment_outcomes");
  const aliases = contract.assessment_aliases === undefined ? {} : object(contract.assessment_aliases, "feedback_result_contract.assessment_aliases");
  if (!Array.isArray(contract.cells)) throw new TypeError("feedback_result_contract.cells must be an array");
  const canonicalAssessments = contract.canonical_assessments === undefined
    ? Object.keys(outcomes)
    : stringArray(contract.canonical_assessments, "feedback_result_contract.canonical_assessments");
  return {
    schema_version: string(contract.schema_version, "feedback_result_contract.schema_version"),
    canonical_assessments: canonicalAssessments,
    assessment_aliases: Object.fromEntries(Object.entries(aliases).map(([alias, assessment]) => [alias, string(assessment, "feedback_result_contract assessment alias")])),
    assessment_outcomes: Object.fromEntries(Object.entries(outcomes).map(([assessment, outcome]) => [assessment, string(outcome, "feedback_result_contract assessment outcome")])),
    cells: contract.cells.map((cell) => {
      if (!Array.isArray(cell) || cell.length !== 3) throw new TypeError("feedback_result_contract.cells entries must be triples");
      return [string(cell[0], "feedback_result_contract outcome"), string(cell[1], "feedback_result_contract severity"), string(cell[2], "feedback_result_contract disposition")] as const;
    }),
  };
}

function parseWalkStop(value: unknown): WalkStop | null {
  if (value === null || value === undefined) return null;
  const stop = object(value, "feedback.walk_stop");
  const fields = ["state", "halting_step_id", "unreached_step_ids", "trigger", "recorded_by", "note"];
  if (Object.keys(stop).length !== fields.length || fields.some((field) => !(field in stop))) {
    throw new TypeError("feedback.walk_stop must have the exact stopped-walk fields");
  }
  const state = string(stop.state, "feedback.walk_stop.state");
  const trigger = string(stop.trigger, "feedback.walk_stop.trigger");
  const recordedBy = string(stop.recorded_by, "feedback.walk_stop.recorded_by");
  if (state !== "STOPPED" || trigger !== "OBSERVED_FAILURE" || !["OWNER", "ORCHESTRATOR_TRANSCRIPTION"].includes(recordedBy)) {
    throw new TypeError("feedback.walk_stop has unsupported values");
  }
  return {
    state, halting_step_id: string(stop.halting_step_id, "feedback.walk_stop.halting_step_id"),
    unreached_step_ids: stringArray(stop.unreached_step_ids, "feedback.walk_stop.unreached_step_ids"),
    trigger, recorded_by: recordedBy as WalkStop["recorded_by"], note: string(stop.note, "feedback.walk_stop.note"),
  };
}

function stringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value)) throw new TypeError(`${label} must be an array`);
  return value.map((item, index) => string(item, `${label}[${index}]`));
}

function safeAppUrl(value: unknown, label: string): string {
  const raw = string(value, label);
  if (!raw || /[\u0000-\u001f\u007f]/.test(raw)) throw new TypeError(`${label} must be a safe absolute HTTP(S) URL`);
  let parsed: URL;
  try { parsed = new URL(raw); } catch { throw new TypeError(`${label} must be a safe absolute HTTP(S) URL`); }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new TypeError(`${label} must be a safe absolute HTTP(S) URL`);
  }
  return raw;
}

function parseAppLink(value: unknown, label: string, appUrl: string): AppLink | null {
  if (value === undefined) return null;
  const link = object(value, label);
  const keys = Object.keys(link);
  if (keys.some((key) => key !== "href" && key !== "label") || !("href" in link)) {
    throw new TypeError(`${label} must contain only href and optional label`);
  }
  const href = string(link.href, `${label}.href`);
  if (!href.trim() || /[\u0000-\u001f\u007f{}]/.test(href) || href.startsWith("//")
      || (!href.startsWith("/") && !/^https?:\/\//.test(href))) {
    throw new TypeError(`${label}.href must be a safe same-origin link`);
  }
  const base = new URL(appUrl);
  let resolved: URL;
  try { resolved = new URL(href, base); } catch { throw new TypeError(`${label}.href must be a safe same-origin link`); }
  if (!["http:", "https:"].includes(resolved.protocol) || resolved.origin !== base.origin || resolved.username || resolved.password) {
    throw new TypeError(`${label}.href must be a safe same-origin link`);
  }
  const parsed: AppLink = { href };
  if (link.label !== undefined) {
    const linkLabel = string(link.label, `${label}.label`);
    if (!linkLabel.trim() || /[\u0000-\u001f\u007f]/.test(linkLabel)) throw new TypeError(`${label}.label must be non-blank`);
    parsed.label = linkLabel;
  }
  return parsed;
}

function knownItem(text: string, sourceRef: string, scope: KnownItem["scope"], stepRefs: string[]): KnownItem {
  const item = { text, source_ref: sourceRef } as KnownItem;
  Object.defineProperties(item, {
    scope: { value: scope, enumerable: false },
    step_refs: { value: stepRefs, enumerable: false },
  });
  return item;
}

function parseJourney(raw: unknown, index: number, appUrl: string): Journey {
  const journey = object(raw, `journey ${index + 1}`);
  if (!Array.isArray(journey.scenarios) || !Array.isArray(journey.steps)) throw new TypeError(`journey ${index + 1} scenarios and steps must be arrays`);
  const steps: JourneyStep[] = journey.steps.map((stepRaw, stepIndex) => {
    const step = object(stepRaw, `step ${stepIndex + 1}`);
    if (!Number.isInteger(step.ordinal) || Number(step.ordinal) < 1) throw new TypeError("step.ordinal must be a positive integer");
    if (!Array.isArray(step.scenario_links)) throw new TypeError("step.scenario_links must be an array");
    return {
      step_id: string(step.step_id, "step_id"), ordinal: Number(step.ordinal), name: string(step.name, "step.name"),
      instruction: string(step.instruction, "step.instruction"), expected_outcome: string(step.expected_outcome, "step.expected_outcome"),
      scenario_links: step.scenario_links.map((linkRaw, linkIndex) => {
        const link = object(linkRaw, `step.scenario_links[${linkIndex}]`);
        return { scenario_id: string(link.scenario_id, "step.scenario_link.scenario_id"), gwt_refs: stringArray(link.gwt_refs, "step.scenario_link.gwt_refs") };
      }),
      app_link: parseAppLink(step.app_link, `step ${stepIndex + 1}.app_link`, appUrl),
    };
  });
  const stepIds = new Set(steps.map((step) => step.step_id));
  const parsed = {
    ...journey, journey_id: string(journey.journey_id, "journey_id"), title: string(journey.title, "journey.title"),
    app_link: parseAppLink(journey.app_link, `journey ${index + 1}.app_link`, appUrl),
    scenarios: journey.scenarios.map((scenarioRaw, scenarioIndex) => {
      const scenario = object(scenarioRaw, `scenario ${scenarioIndex + 1}`);
      if (!Array.isArray(scenario.gwt)) throw new TypeError("scenario.gwt must be an array");
      return { scenario_id: string(scenario.scenario_id, "scenario_id"), title: string(scenario.title, "scenario.title"),
        gwt: scenario.gwt.map((gwtRaw, gwtIndex) => {
          const gwt = object(gwtRaw, `gwt ${gwtIndex + 1}`);
          return { gwt_ref: string(gwt.gwt_ref, "gwt_ref"), sha256: string(gwt.sha256, "gwt.sha256"),
            given: stringArray(gwt.given, "gwt.given"), when: stringArray(gwt.when, "gwt.when"), then: stringArray(gwt.then, "gwt.then") };
        }) };
    }),
    known_items: (() => {
      if (!Array.isArray(journey.known_items)) throw new TypeError("journey.known_items must be an array");
      return journey.known_items.map((knownRaw, knownIndex) => {
        const known = object(knownRaw, `journey.known_items[${knownIndex}]`);
        if (known.step_refs === undefined) return knownItem(string(known.text, "known_item.text"), string(known.source_ref, "known_item.source_ref"), "legacy-journey", []);
        const stepRefs = stringArray(known.step_refs, "known_item.step_refs");
        if (!stepRefs.length || stepRefs.some((ref) => !ref.trim() || !stepIds.has(ref)) || new Set(stepRefs).size !== stepRefs.length) throw new TypeError("known_item.step_refs must name distinct steps in its journey");
        return knownItem(string(known.text, "known_item.text"), string(known.source_ref, "known_item.source_ref"), "step", stepRefs);
      });
    })(),
    steps,
  } as Journey;
  for (const step of parsed.steps) resolveLinkedScenarioBlocks(parsed, step);
  return parsed;
}

export function parseActiveRoundView(value: unknown): ActiveRoundView {
  const root = object(value, "active UAT round view");
  if (root.schema !== "jswarm.test-uat.active-round-view/v2" || root.schema_version !== "2.0") {
    throw new TypeError("unsupported active UAT round schema/version");
  }
  const current = object(root.current_round, "current_round");
  const feedback = object(root.feedback, "feedback");
  const capabilities = object(root.capabilities, "capabilities");
  if (!Array.isArray(current.journeys)) throw new TypeError("current_round.journeys must be an array");
  const normalizedPackage = object(current.normalized_package, "current_round.normalized_package");
  const appUrl = normalizedPackage.app_url === undefined
    ? ""
    : safeAppUrl(normalizedPackage.app_url, "current_round.normalized_package.app_url");
  const journeys = current.journeys.map((journey, index) => parseJourney(journey, index, appUrl));
  const stepCounts = object(feedback.step_counts, "feedback.step_counts");
  if (!("feedback_result_contract" in root)) {
    throw new TypeError("feedback_result_contract is required for active UAT round view");
  }
  return {
    schema: string(root.schema, "schema"), schema_version: string(root.schema_version, "schema_version"),
    round_review_id: string(root.round_review_id, "round_review_id"), ticket: string(root.ticket, "ticket"),
    round_status: roundStatus(root.round_status, "round_status"),
    projection_sha256: string(root.projection_sha256, "projection_sha256"),
    feedback_result_contract: parseFeedbackResultContract(root.feedback_result_contract),
    current_round: {
      path: string(current.path, "current_round.path"), sha256: string(current.sha256, "current_round.sha256"),
      size: Number(current.size), package_state: string(current.package_state, "current_round.package_state"),
      source: string(current.source, "current_round.source"),
      normalized_package: normalizedPackage,
      app_url: appUrl,
      journeys, writable: false,
    },
    feedback: {
      path: string(feedback.path, "feedback.path"), sha256: string(feedback.sha256, "feedback.sha256"),
      size: Number(feedback.size), processing_state: string(feedback.processing_state, "feedback.processing_state"),
      round_status: roundStatus(feedback.round_status, "feedback.round_status"),
      step_entries: parseEntries(feedback.step_entries),
      walk_stop: parseWalkStop(feedback.walk_stop),
      step_counts: {
        total: Number(stepCounts.total), gray: Number(stepCounts.gray), yellow: Number(stepCounts.yellow), green: Number(stepCounts.green),
      },
      markerized: boolean(feedback.markerized, "feedback.markerized"),
      writable: boolean(feedback.writable, "feedback.writable"),
    },
    capabilities: {
      feedback_update: boolean(capabilities.feedback_update, "capabilities.feedback_update"),
      practice_fixture: capabilities.practice_fixture === undefined ? false : boolean(capabilities.practice_fixture, "capabilities.practice_fixture"),
    },
    ...(root.fixture === undefined ? {} : (() => {
      const fixture = object(root.fixture, "fixture");
      if (fixture.purpose !== "safe-practice-round" || fixture.resettable !== true) throw new TypeError("fixture is unsupported");
      return { fixture: { purpose: "safe-practice-round" as const, resettable: true as const } };
    })()),
  };
}

export function buildFeedbackCommand(
  entries: FeedbackEntries,
  feedbackSha: string,
  currentRoundSha: string,
  walkStop?: WalkStop | null,
): FeedbackUpdateCommand {
  return {
    schema: FEEDBACK_UPDATE_SCHEMA,
    schema_version: "2.0",
    expected_feedback_sha256: feedbackSha,
    expected_current_round_sha256: currentRoundSha,
    step_entries: cloneEntries(entries),
    ...(walkStop === undefined ? {} : { walk_stop: cloneWalkStop(walkStop) }),
  };
}

export function isDraftDirty(draft: FeedbackEntries, baseline: FeedbackEntries): boolean {
  return JSON.stringify(draft) !== JSON.stringify(baseline);
}

export function validateDraft(
  draft: FeedbackEntries,
  stepIds: string[],
  resultContract: FeedbackResultContract,
  walkStop: WalkStop | null = null,
): { valid: boolean; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  if (Object.keys(draft).length !== stepIds.length || stepIds.some((id) => !(id in draft))) {
    errors.form = "The draft must include feedback for every canonical step.";
  }
  for (const id of stepIds) {
    const entry = draft[id];
    if (!entry) continue;
    if (entry.assessment && entry.finding_severity && entry.finding_disposition
        && !allowedDispositions(entry.assessment, entry.finding_severity, resultContract).includes(entry.finding_disposition)) {
      errors[`${id}-finding_disposition`] = "Choose a disposition valid for the selected assessment and severity before saving this step.";
    }
    if (entry.observed.length > 20_000) errors[`${id}-observed`] = "Keep the observation at 20,000 characters or fewer.";
    if (entry.comment.length > 20_000) errors[`${id}-comment`] = "Keep the comment at 20,000 characters or fewer.";
    if ([entry.observed, entry.comment].some((text) => [...text].some((char) => char.charCodeAt(0) < 32 && !"\n\r\t".includes(char)))) {
      errors[`${id}-comment`] = "Remove unsupported control characters.";
    }
  }
  if (walkStop) {
    const canonical = new Set(stepIds);
    const unreached = walkStop.unreached_step_ids;
    const unreachedSet = new Set(unreached);
    if (walkStop.state !== "STOPPED" || walkStop.trigger !== "OBSERVED_FAILURE"
        || !["OWNER", "ORCHESTRATOR_TRANSCRIPTION"].includes(walkStop.recorded_by)) {
      errors.form = "A stopped walk must be caused by an observed failure and have a supported recorder.";
    } else if (!walkStop.note.trim()) errors.form = "Explain why the walk stopped before saving.";
    else if (walkStop.note.length > 20_000) errors.form = "Keep the stop note at 20,000 characters or fewer.";
    else if ([...walkStop.note].some((char) => char.charCodeAt(0) < 32 && !"\n\r\t".includes(char))) {
      errors.form = "Remove unsupported control characters from the stop note.";
    }
    else if (!canonical.has(walkStop.halting_step_id)) errors.form = "The halting step is not part of this canonical round.";
    else if (!unreached.length) errors.form = "A stopped walk must name at least one unreached step.";
    else if (unreachedSet.size !== unreached.length || unreached.some((id) => !canonical.has(id)) || unreachedSet.has(walkStop.halting_step_id)) {
      errors.form = "The stopped walk must name distinct canonical unreached steps and never the halting step.";
    }
    const expectedObserved = `Walk stopped at ${walkStop.halting_step_id}; this step was not reached.`;
    const transcribed = walkStop.recorded_by === "ORCHESTRATOR_TRANSCRIPTION";
    const emptyUnreached = stepIds.filter((id) => {
      const entry = draft[id];
      if (!entry || entry.complete || entry.assessment !== "NOT_OBSERVED" || entry.finding_severity !== "NONE"
          || entry.finding_disposition !== "OPEN" || entry.observed !== expectedObserved) return false;
      return transcribed
        ? entry.comment.startsWith("TRANSCRIPTION: ") && Boolean(entry.comment.slice("TRANSCRIPTION: ".length).trim())
        : entry.comment === "";
    });
    if (unreached.length && (emptyUnreached.length !== unreachedSet.size || emptyUnreached.some((id) => !unreachedSet.has(id)))) {
      errors.form = "The unreached list must match all and only empty not-observed cards.";
    }
    const halting = draft[walkStop.halting_step_id];
    if (!halting || !halting.complete || !["FAIL", "OBSERVED_FAILURE"].includes(halting.assessment)
        || halting.finding_severity !== "MAJOR" || halting.finding_disposition !== "OPEN") {
      errors[`${walkStop.halting_step_id}-stop`] = "The halting step must be a complete observed failure with MAJOR severity and OPEN disposition.";
    }
    if (!halting?.observed.trim()) {
      errors[`${walkStop.halting_step_id}-observed`] = "Record a real observation of the failure before stopping the walk.";
    }
    if (transcribed && halting?.comment && !halting.comment.startsWith("TRANSCRIPTION: ")) {
      errors[`${walkStop.halting_step_id}-comment`] = "A transcribed halting-step comment must begin with TRANSCRIPTION: .";
    }
    for (const id of unreached) {
      const entry = draft[id];
      if (!entry || entry.complete || entry.assessment !== "NOT_OBSERVED" || entry.finding_severity !== "NONE"
          || entry.finding_disposition !== "OPEN" || entry.observed !== expectedObserved) {
        errors[`${id}-stop`] = "A not reached step must stay incomplete and record exactly NOT_OBSERVED / NONE / OPEN with the stopped-walk text.";
      }
      if (transcribed) {
        if (!entry?.comment.startsWith("TRANSCRIPTION: ") || !entry.comment.slice("TRANSCRIPTION: ".length).trim()) {
          errors[`${id}-comment`] = "A transcribed unreached-step comment must begin with TRANSCRIPTION: and include provenance.";
        }
      } else if (entry?.comment) {
        errors[`${id}-comment`] = "An unreached owner step comment must be empty; put the stop account in the stop note.";
      }
    }
  }
  return { valid: Object.keys(errors).length === 0, errors };
}

export type FeedbackControllerState = {
  view: ActiveRoundView;
  baseline: FeedbackEntries;
  draft: FeedbackEntries;
  baselineWalkStop: WalkStop | null;
  walkStop: WalkStop | null;
  walkStopExplicit: boolean;
  saving: boolean;
  activeCommand: FeedbackUpdateCommand | null;
  uncertainCommand: FeedbackUpdateCommand | null;
  lastFailure: string | null;
  saveOutcome: "saved" | "idempotent" | null;
};

export type FeedbackControllerEvent =
  | { type: "draft-changed"; draft: FeedbackEntries }
  | { type: "walk-stop-changed"; walkStop: WalkStop | null; draft: FeedbackEntries }
  | { type: "save-started"; command: FeedbackUpdateCommand }
  | { type: "save-succeeded"; status: "saved" | "idempotent"; view: ActiveRoundView }
  | { type: "save-failed"; code: string; command: FeedbackUpdateCommand };

export function createFeedbackController(view: ActiveRoundView): FeedbackControllerState {
  const entries = cloneEntries(view.feedback.step_entries);
  return {
    view,
    baseline: cloneEntries(entries),
    draft: entries,
    baselineWalkStop: cloneWalkStop(view.feedback.walk_stop),
    walkStop: cloneWalkStop(view.feedback.walk_stop),
    walkStopExplicit: false,
    saving: false,
    activeCommand: null,
    uncertainCommand: null,
    lastFailure: null,
    saveOutcome: null,
  };
}

export function transitionFeedbackController(
  state: FeedbackControllerState,
  event: FeedbackControllerEvent,
): FeedbackControllerState {
  if (event.type === "draft-changed") {
    const processed = state.view.feedback.processing_state !== "UNPROCESSED" || !state.view.capabilities.feedback_update;
    if (processed) return state;
    return { ...state, draft: cloneEntries(event.draft), saveOutcome: null };
  }
  if (event.type === "walk-stop-changed") {
    const processed = state.view.feedback.processing_state !== "UNPROCESSED" || !state.view.capabilities.feedback_update;
    if (processed) return state;
    return {
      ...state,
      draft: cloneEntries(event.draft),
      walkStop: cloneWalkStop(event.walkStop),
      walkStopExplicit: true,
      saveOutcome: null,
    };
  }
  if (event.type === "save-started") {
    if (state.saving) return state;
    return { ...state, saving: true, activeCommand: event.command, lastFailure: null, saveOutcome: null };
  }
  if (event.type === "save-succeeded") {
    return { ...createFeedbackController(event.view), saveOutcome: event.status };
  }
  return {
    ...state,
    saving: false,
    activeCommand: null,
    uncertainCommand: event.code === "feedback_commit_uncertain" ? event.command : null,
    lastFailure: event.code,
    saveOutcome: null,
  };
}

// --- Durable long-session form state (A/C 6) ---------------------------------
//
// Unsent edits are the owner's work in progress, so they survive a refresh or a
// browser restart. They stay a *local draft*: they never claim to be canonical
// sent feedback, and they are bound to the exact round and baseline they were
// written against so a draft can never cross rounds or overwrite state that has
// moved on underneath it.

export const LOCAL_DRAFT_SCHEMA = "jswarm.test-uat.local-draft/v1" as const;

export type LocalDraft = {
  schema: typeof LOCAL_DRAFT_SCHEMA;
  round_review_id: string;
  baseline_feedback_sha256: string;
  baseline_current_round_sha256: string;
  saved_at: string;
  step_entries: FeedbackEntries;
  walk_stop?: WalkStop | null;
};

export type DraftStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

export type RestoreDraftStatus = "restored" | "none" | "stale-baseline" | "other-round" | "invalid";

export function draftStorageKey(roundReviewId: string): string {
  return `jw.uat.draft.v1:${roundReviewId}`;
}

export function persistLocalDraft(
  storage: DraftStorage,
  state: FeedbackControllerState,
  savedAt: string,
): LocalDraft | null {
  const key = draftStorageKey(state.view.round_review_id);
  // Nothing unsent means nothing to keep: a sent or untouched round clears its draft.
  if (!isDraftDirty(state.draft, state.baseline) && JSON.stringify(state.walkStop) === JSON.stringify(state.baselineWalkStop)) {
    try { storage.removeItem(key); } catch { /* storage is a convenience, never a dependency */ }
    return null;
  }
  const draft: LocalDraft = {
    schema: LOCAL_DRAFT_SCHEMA,
    round_review_id: state.view.round_review_id,
    baseline_feedback_sha256: state.view.feedback.sha256,
    baseline_current_round_sha256: state.view.current_round.sha256,
    saved_at: savedAt,
    step_entries: cloneEntries(state.draft),
    ...(state.walkStop === null && state.baselineWalkStop === null && !state.walkStopExplicit ? {} : { walk_stop: cloneWalkStop(state.walkStop) }),
  };
  try { storage.setItem(key, JSON.stringify(draft)); } catch { return null; }
  return draft;
}

export function clearLocalDraft(storage: DraftStorage, roundReviewId: string): void {
  try { storage.removeItem(draftStorageKey(roundReviewId)); } catch { /* ignore */ }
}

export function restoreLocalDraft(
  storage: DraftStorage,
  state: FeedbackControllerState,
): { status: RestoreDraftStatus; state: FeedbackControllerState } {
  const roundReviewId = state.view.round_review_id;
  let raw: string | null = null;
  try { raw = storage.getItem(draftStorageKey(roundReviewId)); } catch { raw = null; }
  if (raw === null) return { status: "none", state };

  const discard = (status: RestoreDraftStatus) => {
    clearLocalDraft(storage, roundReviewId);
    return { status, state };
  };

  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { return discard("invalid"); }
  if (typeof parsed !== "object" || parsed === null) return discard("invalid");
  const candidate = parsed as Partial<LocalDraft>;
  if (candidate.schema !== LOCAL_DRAFT_SCHEMA) return discard("invalid");
  if (candidate.round_review_id !== roundReviewId) return discard("other-round");
  if (candidate.baseline_feedback_sha256 !== state.view.feedback.sha256
      || candidate.baseline_current_round_sha256 !== state.view.current_round.sha256) {
    return discard("stale-baseline");
  }
  const entries = candidate.step_entries;
  if (typeof entries !== "object" || entries === null) return discard("invalid");
  const expected = Object.keys(state.baseline).sort();
  if (JSON.stringify(Object.keys(entries).sort()) !== JSON.stringify(expected)) return discard("invalid");

  const rawStop = candidate.walk_stop;
  let restored: FeedbackControllerState;
  if (rawStop === undefined) {
    restored = transitionFeedbackController(state, { type: "draft-changed", draft: entries as FeedbackEntries });
  } else {
    let parsedStop: WalkStop | null;
    try { parsedStop = parseWalkStop(rawStop); } catch { return discard("invalid"); }
    restored = transitionFeedbackController(state, { type: "walk-stop-changed", walkStop: parsedStop, draft: entries as FeedbackEntries });
  }
  if (!isDraftDirty(restored.draft, restored.baseline)
      && JSON.stringify(restored.walkStop) === JSON.stringify(restored.baselineWalkStop)) return discard("none");
  return { status: "restored", state: restored };
}

export function walkStopCommandValue(state: FeedbackControllerState): WalkStop | null | undefined {
  if (state.walkStop !== null) return cloneWalkStop(state.walkStop);
  return state.baselineWalkStop !== null || state.walkStopExplicit ? null : undefined;
}

export function deriveFeedbackUi(
  state: FeedbackControllerState,
  stepIds: string[],
) {
  const validation = validateDraft(state.draft, stepIds, state.view.feedback_result_contract, state.walkStop);
  const dirty = isDraftDirty(state.draft, state.baseline) || JSON.stringify(state.walkStop) !== JSON.stringify(state.baselineWalkStop);
  const cardStates = Object.fromEntries(stepIds.map((stepId) => {
    const entry = state.draft[stepId];
    const untouched = entry && !entry.complete && !entry.assessment && !entry.finding_severity
      && !entry.finding_disposition && !entry.observed && !entry.comment;
    const stepHasError = Object.keys(validation.errors).some((key) => key.startsWith(`${stepId}-`));
    return [stepId, untouched ? "gray" : entry?.complete && !stepHasError ? "green" : "yellow"];
  })) as Record<string, "gray" | "yellow" | "green">;
  const processed = state.view.feedback.processing_state !== "UNPROCESSED"
    || !state.view.feedback.writable
    || !state.view.capabilities.feedback_update
    || state.lastFailure === "feedback_processed";
  const conflictLocked = state.lastFailure === "stale_feedback" || state.lastFailure === "stale_round" || processed;
  const exactRetry = Boolean(state.uncertainCommand);
  return {
    dirty,
    validation,
    cardStates,
    // Draft and sent are never presented as the same thing.
    draftStatus: dirty ? "local-draft-unsent" as const : "sent" as const,
    duplicateSubmitLocked: state.saving,
    controlsDisabled: state.saving || processed,
    canSave: exactRetry
      ? !state.saving
      : state.view.feedback.writable && state.view.capabilities.feedback_update
        && dirty && validation.valid && !state.saving && !conflictLocked,
    retryCommand: state.uncertainCommand,
    saveOutcome: state.saveOutcome,
    unsavedNavigationGuard: dirty,
    reloadWarningIntent: dirty ? "warn-before-replacing-draft" as const : null,
    copyDraftIntent: dirty ? "copy-complete-draft" as const : null,
    focusTarget: state.lastFailure === "invalid_feedback_update"
      ? "validation-summary" as const
      : state.lastFailure ? "request-alert" as const : null,
  };
}

const failureCopy: Record<string, { state: FailureState; title: string; message: string }> = {
  stale_feedback: { state: "stale-feedback", title: "Changes not saved — newer feedback exists", message: "Your edits are still in this browser. Copy them, then reload the latest round before continuing." },
  stale_round: { state: "stale-round", title: "Changes not saved — newer feedback exists", message: "Your edits are still in this browser. Copy them, then reload the latest round before continuing." },
  feedback_processed: { state: "processed", title: "Feedback has been processed", message: "`/test uat feedback` processed this canonical feedback artifact. Results are now read-only and Save is unavailable." },
  invalid_feedback_update: { state: "validation", title: "Feedback was not accepted", message: "The service rejected this complete feedback update. Review the highlighted fields; your draft has not been cleared." },
  ambiguous_editable_region: { state: "validation", title: "Feedback cannot be edited safely", message: "The canonical feedback file does not have an unambiguous editable region. No content was changed." },
  feedback_write_failed: { state: "write-error", title: "Some changes aren’t saved", message: "Your work is still here. Retry autosave before leaving, or copy your draft." },
  feedback_commit_uncertain: { state: "commit-uncertain", title: "Save result uncertain", message: "The service may have stored the last snapshot. Keep working here; retry that exact autosave before a newer snapshot is sent." },
  round_not_found: { state: "unavailable", title: "Active UAT round unavailable", message: "This registered round is no longer available. Return to the list or reload registered rounds." },
  source_invalid: { state: "source-error", title: "Canonical source is invalid", message: "The registered artifacts did not pass canonical validation. Feedback editing is unavailable." },
  source_too_large: { state: "source-error", title: "Canonical source is too large", message: "The registered artifact exceeds the service limit. Feedback editing is unavailable." },
  body_too_large: { state: "validation", title: "Feedback update is too large", message: "The complete feedback update exceeds the service limit. Your draft is still present." },
  uat_round_projection_stale: { state: "source-error", title: "Registered round is out of date", message: "The registration no longer matches the canonical artifacts. Feedback editing is unavailable." },
  round_feedback_identity_mismatch: { state: "source-error", title: "Round and feedback do not match", message: "The canonical round and feedback identities differ. Feedback editing is unavailable." },
  offline: { state: "offline", title: "Some changes aren’t saved", message: "Your work is still here. Retry autosave before leaving, or copy your draft." },
};

export function describeFailure(code: string) {
  return failureCopy[code] ?? { state: "source-error" as const, title: "Active UAT request failed", message: "The service returned an unexpected response. No success was recorded." };
}

export function buildRoundStatusCommand(
  status: RoundStatus,
  expectedFeedbackSha256: string,
  expectedCurrentRoundSha256: string,
): RoundStatusUpdateCommand {
  return {
    schema: ROUND_STATUS_UPDATE_SCHEMA,
    schema_version: "1.0",
    expected_feedback_sha256: expectedFeedbackSha256,
    expected_current_round_sha256: expectedCurrentRoundSha256,
    round_status: status,
  };
}

async function requestJson(url: string, init?: RequestInit): Promise<unknown> {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), 30_000);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = object(object(payload, "error response").error ?? {}, "error");
      throw new UatApiError(response.status, typeof error.code === "string" ? error.code : "request_failed", typeof error.message === "string" ? error.message : "Active UAT request failed");
    }
    return payload;
  } catch (error) {
    if (error instanceof UatApiError) throw error;
    throw new UatApiError(0, "offline", "Active UAT service is unavailable");
  } finally {
    globalThis.clearTimeout(timer);
  }
}

export async function fetchRoundList(): Promise<RoundList> {
  return parseRoundList(await requestJson("/api/uat-rounds"));
}

export async function fetchRoundDetail(roundId: string): Promise<ActiveRoundView> {
  return parseActiveRoundView(await requestJson(`/api/uat-rounds/${encodeURIComponent(roundId)}`));
}

export async function updateRoundStatus(roundId: string, command: RoundStatusUpdateCommand): Promise<SaveResponse> {
  const payload = object(await requestJson(`/api/uat-rounds/${encodeURIComponent(roundId)}/status`, { method: "POST", body: JSON.stringify(command) }), "round status response");
  const status = string(payload.status, "round status save status");
  if (status !== "saved" && status !== "idempotent") throw new TypeError("round status save status is unsupported");
  return { status, view: parseActiveRoundView(payload.view) };
}

async function sendFeedback(roundId: string, command: FeedbackUpdateCommand, submit: boolean): Promise<SaveResponse> {
  const suffix = submit ? "/submit" : "";
  const payload = object(await requestJson(`/api/uat-rounds/${encodeURIComponent(roundId)}/feedback${suffix}`, { method: "POST", body: JSON.stringify(command) }), "save response");
  const status = string(payload.status, "save status");
  if (status !== "saved" && status !== "idempotent") throw new TypeError("save status is unsupported");
  const notification = payload.notification === undefined ? undefined : string(payload.notification, "notification");
  if (notification !== undefined && !["submitted", "already_submitted", "pending", "practice_submitted", "practice_already_submitted"].includes(notification)) {
    throw new TypeError("notification state is unsupported");
  }
  return { status, view: parseActiveRoundView(payload.view), notification: notification as SaveResponse["notification"] };
}

export function saveFeedback(roundId: string, command: FeedbackUpdateCommand): Promise<SaveResponse> {
  return sendFeedback(roundId, command, false);
}

export function submitFeedback(roundId: string, command: FeedbackUpdateCommand): Promise<SaveResponse> {
  return sendFeedback(roundId, command, true);
}

export async function resetPracticeRound(roundId: string): Promise<ActiveRoundView> {
  const payload = object(await requestJson(`/api/uat-rounds/${encodeURIComponent(roundId)}/reset`, { method: "POST", body: "{}" }), "practice reset response");
  if (payload.status !== "practice_reset") throw new TypeError("practice reset status is unsupported");
  return parseActiveRoundView(payload.view);
}
