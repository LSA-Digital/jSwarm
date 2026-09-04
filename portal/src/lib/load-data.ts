/**
 * Phase 2 — build-time data loader for the decision-review UI.
 *
 * Astro component frontmatter runs in Node at build time, so we read the
 * canonical review-build object from disk here. The Python wrapper
 * (jswarm/portal/render_ui.py) has ALREADY schema-validated the
 * contracts + manifests through the jsonschema registry and canonicalized the
 * build object before invoking `npm run build`; this loader re-validates the
 * embedded Q&A threads against the fix-decisions qa-thread schema with AJV
 * (same authority, independent runtime) and does a fail-closed shape check.
 *
 * Source of the path (in priority order):
 *   1. process.env.DECISION_REVIEW_DATA_FILE — set by the wrapper.
 *   2. <app>/.tmp-build/review-data.canonical.json — wrapper default output.
 *
 * Fail-closed: a missing/unreadable/malformed file throws at build time.
 */
import fs from "node:fs";
import path from "node:path";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";

const APP_ROOT = process.cwd();
const QA_SCHEMA_PATH = path.resolve(
  APP_ROOT,
  "../schemas/fix-decisions/fix-decisions.qa-thread.schema.json",
);

export interface ReviewBuildData {
  schema: string;
  generated_from: string[];
  qa_threads: QaThread[];
  contracts: ContractReview[];
}

export interface QaThread {
  thread_id: string;
  ticket: string;
  cycle_key: string;
  status: string;
  anchor?: {
    section_id?: string;
    subject?: string;
    contract_slug?: string;
    [k: string]: unknown;
  };
  messages: Array<{
    message_id: string;
    author_role: string;
    created_at_utc: string;
    body: string;
    in_reply_to?: string;
  }>;
  [k: string]: unknown;
}

export interface ContractReview {
  slug: string;
  document_type: string;
  markdown: string;
  view_model: {
    identity: Record<string, string>;
    decision: string;
    summary: string[];
    scope: { in_scope: string[]; out_of_scope: Array<{ item: string; reason: string }> };
    defects: Array<{ key: string; conviction: string; authorization: string; fixed_means: string }>;
    authorized_defects: Array<{
      key: string;
      authorization: string;
      fixed_means: string;
      parent_scope_trace: {
        defect_contract?: string;
        defect_key?: string;
        disposition?: string;
      };
    }>;
    flow: { representation: string; body: string };
    risks: Array<{ priority: string; risk: string; impact: string; guardrail: string }>;
    cost_timeline: string;
    detail_refs: Array<{ ref: string; locator: string }>;
    pm_view: Array<{ anchor: string; quote: string }>;
    pm_anchor_map: Array<{
      technical_sections: string[];
      controlling_sentence: string;
      how_it_serves: string;
    }>;
    terminal_outcome: string;
    sections: SectionNode[];
    section_ids: string[];
    gate: Record<string, unknown>;
  };
  qa_threads: QaThread[];
}

export interface SectionNode {
  section_id: string;
  title: string;
  body: string;
  depth: number;
  subsections?: SectionNode[];
}

export function dataFilePath(): string {
  const fromEnv = process.env.DECISION_REVIEW_DATA_FILE;
  if (fromEnv && fromEnv.trim() !== "") {
    return fromEnv;
  }
  return path.join(process.cwd(), ".tmp-build", "review-data.canonical.json");
}

export function loadReviewData(): ReviewBuildData {
  const file = dataFilePath();
  if (!fs.existsSync(file)) {
    throw new Error(
      `[decision-review-ui] build data object not found at ${file}. The Python ` +
        `wrapper (render_ui.py) must write the canonical review data before ` +
        `'npm run build'. Set DECISION_REVIEW_DATA_FILE or run via the wrapper.`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch (err) {
    throw new Error(`[decision-review-ui] data object at ${file} is not valid JSON: ${String(err)}`);
  }
  assertShape(parsed as ReviewBuildData, file);
  const data = parsed as ReviewBuildData;
  // Independent AJV validation of the embedded Q&A threads (dual authority:
  // Python jsonschema pre-gate + AJV at build).
  validateQaThreads(data.qa_threads, file);
  return data;
}

function assertShape(value: ReviewBuildData, file: string): void {
  if (typeof value !== "object" || value === null) {
    throw new Error(`[decision-review-ui] data object at ${file} must be a JSON object.`);
  }
  if (value.schema !== "jswarm.fix-decisions.review-build/1") {
    throw new Error(
      `[decision-review-ui] unsupported schema ${JSON.stringify(value.schema)} in ${file}; expected "jswarm.fix-decisions.review-build/1".`,
    );
  }
  if (!Array.isArray(value.contracts) || value.contracts.length === 0) {
    throw new Error(`[decision-review-ui] 'contracts' must be a non-empty array in ${file}.`);
  }
  if (!Array.isArray(value.qa_threads)) {
    throw new Error(`[decision-review-ui] 'qa_threads' must be an array in ${file}.`);
  }
}

function validateQaThreads(threads: QaThread[], file: string): void {
  let schema: unknown;
  try {
    schema = JSON.parse(fs.readFileSync(QA_SCHEMA_PATH, "utf-8"));
  } catch (err) {
    throw new Error(
      `[decision-review-ui] cannot read qa-thread schema at ${QA_SCHEMA_PATH}: ${String(err)}`,
    );
  }
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);
  const validate = ajv.compile(schema as object);
  for (const thread of threads) {
    const threadId = thread.thread_id;
    if (!validate(thread)) {
      const detail = (validate.errors ?? [])
        .map((e) => `${e.instancePath || "<root>"}: ${e.message}`)
        .join("; ");
      throw new Error(
        `[decision-review-ui] qa-thread ${threadId ?? "?"} in ${file} failed AJV validation: ${detail}`,
      );
    }
  }
}
