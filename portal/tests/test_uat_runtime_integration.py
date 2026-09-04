"""COM-391 Phase 4 — production-build shell and real-service integration proof."""

from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from jswarm.portal.tests.service_fixtures import (
    LiveServer,
    make_active_uat_round,
    make_com391_v2_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv/bin/python"
UI_APP_DIR = REPO_ROOT / "portal"
# A machine-managed Node (fnm or otherwise) resolved from PATH, not a hardcoded
# developer-machine path -- this repo makes no assumption about how Node was
# installed, only that "node" resolves for whoever has the runtime browser
# integration tooling set up.
_node_on_path = shutil.which("node")
NODE_BIN = Path(_node_on_path) if _node_on_path else None
PLAYWRIGHT_DIR = REPO_ROOT / "portal/node_modules/playwright"


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory):
    # This module drives a real browser (Playwright + Node) against a real
    # production build of the portal UI. That toolchain is optional in this
    # repo (npm ci / Playwright install are not part of the Python test
    # bootstrap) -- skip cleanly rather than fail when it is not provisioned,
    # the same way `install.sh portal` treats Node as optional-but-required-
    # for-that-feature.
    if not (UI_APP_DIR / "node_modules/.bin/astro").exists():
        pytest.skip("portal/node_modules not installed (npm ci); skipping runtime browser integration tests")
    if NODE_BIN is None or not NODE_BIN.is_file():
        pytest.skip("no managed Node runtime on PATH; skipping runtime browser integration tests")
    if not PLAYWRIGHT_DIR.exists():
        pytest.skip(f"Playwright not installed under {PLAYWRIGHT_DIR}; skipping runtime browser integration tests")
    out = tmp_path_factory.mktemp("com391-uat-runtime-dist")
    result = subprocess.run(
        [
            str(VENV_PYTHON),
            "-m",
            "jswarm.portal.render_ui",
            "--build-manifest",
            "auto",
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"production UI build failed:\n{result.stdout}\n{result.stderr}"
    return out


def _complete_command(fixture, comment):
    """Full-snapshot v2 step feedback carrying one owner-private comment.

    v1 rounds are deliberately legacy read-only after the nested-v2 cutover, so
    the save contract this test proves only exists on a v2 round.
    """
    return {
        "schema": "jswarm.test-uat.feedback-update/v2",
        "schema_version": "2.0",
        "expected_feedback_sha256": fixture["receipt"]["feedback_sha256"],
        "expected_current_round_sha256": fixture["receipt"]["current_round_sha256"],
        "step_entries": {
            step["step_id"]: {
                "complete": True,
                "assessment": "ALIGNED",
                "finding_severity": "NONE",
                "finding_disposition": "SATISFIED",
                "observed": "Observed the expected result during the runtime integration proof.",
                "comment": comment,
            }
            for journey in make_com391_v2_manifest()["journeys"]
            for step in journey["steps"]
        },
    }


def test_production_uat_shell_matches_real_service_round_and_save_contract(tmp_path, built_dist, caplog):
    html = (built_dist / "uat/index.html").read_text(encoding="utf-8")
    assert "Active UAT rounds" in html
    assert "Read-only canonical current round" in html
    assert "data-uat-feedback-form" in html

    server = LiveServer(tmp_path)
    try:
        fixture = make_active_uat_round(server.approved_root, manifest=make_com391_v2_manifest())
        round_id = server.register_active_round(fixture)
        status, listing = server.client.get("/api/uat-rounds")
        assert status == 200
        assert listing["rounds"][0]["round_review_id"] == round_id

        status, detail = server.client.get(f"/api/uat-rounds/{round_id}")
        assert status == 200
        assert detail["current_round"]["writable"] is False
        assert detail["capabilities"]["feedback_update"] is True

        private_comment = "PRIVATE-COM391-INTEGRATION-COMMENT"
        before_current = fixture["current_round_path"].read_bytes()
        status, saved = server.client.post(
            f"/api/uat-rounds/{round_id}/feedback",
            _complete_command(fixture, private_comment),
        )
        assert (status, saved["status"]) == (200, "saved")
        assert fixture["current_round_path"].read_bytes() == before_current
        assert private_comment not in caplog.text
    finally:
        server.close()


class _ImmutableRegionParser(HTMLParser):
    interactive_tags = {"a", "button", "input", "select", "textarea", "summary", "details"}

    def __init__(self):
        super().__init__()
        self.immutable_depth = 0
        self.interactive_inside = []
        self.source_outside = False
        self.source_trigger_outside = []
        self.source_trigger_inside = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.immutable_depth:
            self.immutable_depth += 1
            if tag in self.interactive_tags or "tabindex" in attributes or attributes.get("contenteditable") is not None:
                self.interactive_inside.append(tag)
            if "data-uat-open-source" in attributes:
                self.source_trigger_inside.append(tag)
        elif "data-uat-current-round" in attributes:
            self.immutable_depth = 1
        if "data-uat-current-round-source" in attributes and not self.immutable_depth:
            self.source_outside = True
        if "data-uat-open-source" in attributes and not self.immutable_depth:
            self.source_trigger_outside.append(tag)

    def handle_endtag(self, _tag):
        if self.immutable_depth:
            self.immutable_depth -= 1


def test_generated_shell_keeps_immutable_region_control_free_and_source_disclosure_outside(built_dist):
    parser = _ImmutableRegionParser()
    parser.feed((built_dist / "uat/index.html").read_text(encoding="utf-8"))
    assert parser.interactive_inside == []
    assert parser.source_outside is True
    # The canonical-source disclosure is no longer a <details>/<summary>: it is a
    # button that opens a <dialog>. The contract the old <summary> assertion
    # carried is unchanged — the control that reveals the exact source must be a
    # real interactive control, and it must sit outside the immutable region so
    # that region stays control-free — so it is asserted against that control.
    assert parser.source_trigger_outside == ["button"]
    assert parser.source_trigger_inside == []


def test_production_build_preserves_com389_routes_and_adds_runtime_route(built_dist):
    assert (built_dist / "index.html").is_file()
    assert (built_dist / "uat/index.html").is_file()
    assert (built_dist / "fix/index.html").is_file()
    assert list((built_dist / "review").glob("*/index.html")), "existing COM-389 review routes disappeared"
    index_html = (built_dist / "index.html").read_text(encoding="utf-8")
    assert 'href="/fix/"' in index_html
    assert 'href="/uat/"' in index_html
    assert "Open active UAT rounds" in index_html


class _PeerAreaContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lifecycle_depth = 0
        self.lifecycle_links = []
        self.lifecycle_attributes = {}
        self.lifecycle_band_paths = []
        self.lifecycle_stages = set()
        self.lifecycle_halves = []
        self.fix_cards = []
        self.uat_cards = []
        self.uat_runtime_hooks = set()
        self.area_labels = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "data-fix-uat-lifecycle" in attributes:
            self.lifecycle_depth = 1
            self.lifecycle_attributes = attributes
        elif self.lifecycle_depth:
            self.lifecycle_depth += 1
            if tag == "a":
                self.lifecycle_links.append(attributes.get("href", ""))
        if self.lifecycle_depth and attributes.get("id") == "lifecycle-band-path":
            self.lifecycle_band_paths.append(attributes)
        if self.lifecycle_depth and "data-lifecycle-stage" in attributes:
            self.lifecycle_stages.add(attributes["data-lifecycle-stage"])
        if "data-lifecycle-half" in attributes:
            self.lifecycle_halves.append(attributes)
        if "data-fix-decision-review-area" in attributes:
            self.area_labels.add("FIX DECISION REVIEW")
        if "data-test-uat-feedback-area" in attributes:
            self.area_labels.add("TEST UAT FEEDBACK")
        if "data-fix-summary-card" in attributes:
            self.fix_cards.append(attributes)
        if "data-uat-summary-card" in attributes:
            self.uat_cards.append(attributes)
        for hook in ("data-uat-summary-status", "data-uat-summary-warnings", "data-uat-summary-list"):
            if hook in attributes:
                self.uat_runtime_hooks.add(hook)

    def handle_endtag(self, _tag):
        if self.lifecycle_depth:
            self.lifecycle_depth -= 1


def test_generated_front_page_has_accessible_navigational_lifecycle_and_peer_cards(built_dist):
    parser = _PeerAreaContractParser()
    parser.feed((built_dist / "index.html").read_text(encoding="utf-8"))

    assert parser.lifecycle_attributes.get("aria-label") == "FIX and UAT TEST lifecycle"
    assert parser.lifecycle_attributes.get("data-order") == "static"
    assert parser.lifecycle_attributes.get("data-lifecycle-loop") == "continuous"
    # One continuous figure-eight. The single <path> of the oval this graphic
    # replaced is gone, so the "one continuous loop, never overlaid or
    # concentric groups" contract is proven against the shape that exists now:
    # exactly one crossing band, exactly one lobe per side (asserted below), and
    # all eight stages labelled inside the graphic.
    assert parser.lifecycle_attributes.get("data-lifecycle-shape") == "infinity"
    assert len(parser.lifecycle_band_paths) == 1, "exactly one crossing band"
    assert parser.lifecycle_stages == {
        "diagnose", "contract", "repair", "prove",
        "prepare", "execute", "observe", "feedback",
    }
    assert {(half.get("data-lifecycle-half"), half.get("data-color")) for half in parser.lifecycle_halves} == {
        ("fix", "blue"),
        ("uat", "green"),
    }
    assert parser.lifecycle_links == ["/fix/", "/uat/", "/fix/", "/uat/"]
    assert parser.area_labels == {"FIX DECISION REVIEW", "TEST UAT FEEDBACK"}
    assert parser.fix_cards
    assert all(card.get("data-order") == "newest-first" for card in parser.fix_cards)
    assert all(card.get("data-detail-href", "").startswith("/review/") for card in parser.fix_cards)
    assert parser.uat_runtime_hooks == {"data-uat-summary-status", "data-uat-summary-warnings", "data-uat-summary-list"}
    assert parser.uat_cards == [], "runtime UAT cards must not be hard-coded into the static build"
    html = (built_dist / "index.html").read_text(encoding="utf-8")
    assert "COM-391/round-newer" not in html and "COM-391/round-older" not in html
    assert "FIX cycle" in html and "UAT TEST cycle" in html
    assert "FIX verification" in html
    # The relabelled graphic spaces the slash: "findings / feedback".
    assert re.search(r"findings\s*/\s*feedback", html)
    assert "prefers-reduced-motion" in html
    assert "320px" in html


def test_lifecycle_links_and_fix_index_are_visibly_operable_in_real_browser(tmp_path, built_dist):
    """Owner-facing links must be visible, underlined, and navigable at both layouts."""
    playwright = PLAYWRIGHT_DIR
    assert playwright.exists(), f"shared dashboard Playwright runtime is missing: {playwright}"
    review_pages = sorted((built_dist / "review").glob("*/index.html"))
    assert review_pages, "the fixture build must include review publications"

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        browser_script = tmp_path / "lifecycle-fix-index-browser.cjs"
        browser_script.write_text(
            r'''
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2]);
const origin = process.argv[3];
const expectedReviewCount = Number(process.argv[4]);

function decorationLine(value) {
  return String(value).split(/\s+/).filter(Boolean);
}

async function visibleCycleLink(page, kind) {
  const links = page.locator(`[data-lifecycle-caption-link="${kind}"]`);
  const count = await links.count();
  for (let index = 0; index < count; index += 1) {
    const link = links.nth(index);
    if (await link.isVisible()) return link;
  }
  throw new Error(`no visible ${kind} lifecycle caption link`);
}

async function proveCaption(page, kind, expectedPath, viewport) {
  await page.goto(`${origin}/`, { waitUntil: "networkidle" });
  const link = await visibleCycleLink(page, kind);
  assert.equal(await link.getAttribute("href"), expectedPath, `${kind} href at ${viewport}`);
  const evidence = await link.evaluate((node) => {
    const text = node.querySelector("text") || node;
    const linkStyle = getComputedStyle(node);
    const textStyle = getComputedStyle(text);
    return {
      linkDecorationLine: linkStyle.textDecorationLine,
      textDecorationLine: textStyle.textDecorationLine,
      textDecoration: textStyle.textDecoration,
      ariaLabel: node.getAttribute("aria-label"),
      tagName: node.tagName,
    };
  });
  assert.ok(
    decorationLine(evidence.textDecorationLine).includes("underline") ||
      decorationLine(evidence.linkDecorationLine).includes("underline") ||
      evidence.textDecoration.includes("underline"),
    `${kind} caption is visibly underlined at ${viewport}: ${JSON.stringify(evidence)}`,
  );
  await link.focus();
  assert.equal(await link.evaluate((node) => document.activeElement === node), true, `${kind} caption accepts keyboard focus at ${viewport}`);
  assert.notEqual(
    await link.evaluate((node) => getComputedStyle(node).outlineStyle),
    "none",
    `${kind} caption has a visible keyboard focus outline at ${viewport}`,
  );
  await link.click();
  await page.waitForURL(`**${expectedPath}`);
  assert.equal(new URL(page.url()).pathname, expectedPath, `${kind} click navigates at ${viewport}`);
  return evidence;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const desktop = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const desktopFix = await proveCaption(desktop, "fix", "/fix/", "1440x900");
    const desktopUat = await proveCaption(desktop, "uat", "/uat/", "1440x900");

    await desktop.goto(`${origin}/fix/`, { waitUntil: "networkidle" });
    assert.match(await desktop.locator("h1").textContent(), /FIX/i, "the /fix index has a FIX heading");
    const cards = desktop.locator("[data-fix-index-card]");
    assert.equal(await cards.count(), expectedReviewCount, "the /fix index lists every built review publication");
    const firstLink = cards.first().locator("a").first();
    const reviewHref = await firstLink.getAttribute("href");
    assert.match(reviewHref, /^\/review\/[^/]+\/$/, "a FIX index entry targets its existing review page");
    await firstLink.click();
    await desktop.waitForURL(`**${reviewHref}`);
    assert.equal(new URL(desktop.url()).pathname, reviewHref, "FIX publication entry navigates to its review page");

    const mobile = await browser.newPage({ viewport: { width: 375, height: 812 } });
    const mobileFix = await proveCaption(mobile, "fix", "/fix/", "375x812");
    const mobileUat = await proveCaption(mobile, "uat", "/uat/", "375x812");
    await mobile.goto(`${origin}/fix/`, { waitUntil: "networkidle" });
    assert.equal(await mobile.locator("[data-fix-index-card]").count(), expectedReviewCount, "the mobile /fix index lists every publication");
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, "the /fix index fits a 375px viewport");
    await mobile.goto(`${origin}/`, { waitUntil: "networkidle" });
    assert.equal(await mobile.locator(".lifecycle-diagram").isVisible(), false, "SVG is hidden below 560px");

    console.log(JSON.stringify({
      desktop: { fix: desktopFix, uat: desktopUat, navigated: ["/fix/", "/uat/"] },
      mobile: { fix: mobileFix, uat: mobileUat, navigated: ["/fix/", "/uat/"] },
      fixIndex: { count: expectedReviewCount, reviewHref },
    }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(NODE_BIN), str(browser_script), str(playwright), f"http://{server.host}:{server.port}", str(len(review_pages))],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"browser lifecycle/index contract failed:\n{result.stdout}\n{result.stderr}"
        print(result.stdout.strip())
    finally:
        server.close()

def test_generated_front_page_defers_two_canonical_uat_cards_to_runtime_api(built_dist):
    html = (built_dist / "index.html").read_text(encoding="utf-8")
    parser = _PeerAreaContractParser()
    parser.feed(html)

    assert parser.uat_runtime_hooks == {"data-uat-summary-status", "data-uat-summary-warnings", "data-uat-summary-list"}
    assert parser.uat_cards == []
    assert "COM-391/round-newer" not in html and "COM-391/round-older" not in html
    assert parser.fix_cards, "FIX cards remain build-time rendered while UAT cards are fetched at runtime"


def test_select_cascade_posts_the_rendered_dependent_value_once(tmp_path, built_dist):
    """A select's input-before-change sequence must produce one truthful snapshot.

    This runs the actual browser bundle against the real fixture service rather
    than asserting component source or simulating the DOM.  It protects the
    event-order regression where select ``input`` cascaded the disposition
    before ``change`` decided which fields belonged in the committed snapshot.
    """
    playwright = PLAYWRIGHT_DIR
    assert playwright.exists(), f"shared dashboard Playwright runtime is missing: {playwright}"

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        fixture = make_active_uat_round(server.approved_root, manifest=make_com391_v2_manifest())
        round_id = server.register_active_round(fixture)
        browser_script = tmp_path / "select-cascade-browser.cjs"
        browser_script.write_text(
            r'''
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2]);
const pageUrl = process.argv[3];

(async () => {
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const posted = [];
page.on("request", (request) => {
  if (request.method() === "POST" && request.url().endsWith("/feedback")) {
    posted.push(JSON.parse(request.postData()));
  }
});

async function change(selector, value, dispatchInput = false) {
  const request = page.waitForRequest((candidate) =>
    candidate.method() === "POST" && candidate.url().endsWith("/feedback"),
  );
  await page.evaluate(({ selector, value, dispatchInput }) => {
    const control = document.querySelector(selector);
    if (!(control instanceof HTMLSelectElement)) throw new Error(`missing select ${selector}`);
    control.value = value;
    if (dispatchInput) control.dispatchEvent(new Event("input", { bubbles: true }));
    control.dispatchEvent(new Event("change", { bubbles: true }));
  }, { selector, value, dispatchInput });
  await request;
  await page.waitForTimeout(75);
}

try {
  await page.goto(pageUrl, { waitUntil: "networkidle" });
  await page.locator("[data-uat-workbench]").waitFor({ state: "visible" });
  const card = page.locator("[data-uat-step-card]").first();
  await card.locator("[data-uat-step-caret]").click();
  const stepId = await card.getAttribute("data-uat-step-card");
  assert.ok(stepId, "first step has a stable ID");

  await change("[data-uat-assessment]", "PASS");
  await change("[data-uat-severity]", "NONE");
  const beforeCascade = posted.length;
  await change("[data-uat-severity]", "MINOR", true);

  assert.equal(posted.length, beforeCascade + 1, "input then change is one committing select gesture");
  const domEntry = await card.evaluate((element) => ({
    complete: element.querySelector("[data-uat-complete]").checked,
    assessment: element.querySelector("[data-uat-assessment]").value,
    finding_severity: element.querySelector("[data-uat-severity]").value,
    finding_disposition: element.querySelector("[data-uat-disposition]").value,
    observed: element.querySelector("[data-uat-observed]").value,
    comment: element.querySelector("[data-uat-comment]").value,
  }));
  assert.deepEqual(
    posted.at(-1).step_entries[stepId],
    domEntry,
    "the complete-snapshot POST exactly matches every rendered field",
  );
  assert.equal(domEntry.finding_disposition, "ACCEPTED-BY-OWNER");

  await change("[data-uat-assessment]", "FAIL");
  await change("[data-uat-severity]", "MAJOR");
  await card.locator("[data-uat-observed]").fill("The critical failure was observed.");
  await card.locator("[data-uat-observed]").blur();
  await page.waitForTimeout(75);
  await card.locator("[data-uat-complete]").check();
  await page.waitForTimeout(75);
  await card.locator("[data-uat-step-caret]").click();
  await card.locator("[data-uat-stop-action]").click();
  await card.locator("[data-uat-stop-note]").fill("Cannot continue after the observed failure.");
  await page.route("**/feedback", (route) => route.fulfill({
    status: 500,
    contentType: "application/json",
    body: JSON.stringify({ error: { code: "write_failed", message: "fixture failure" } }),
  }));
  await card.locator("[data-uat-stop-confirm]").click();
  await page.locator("[data-uat-autosave-alert]").waitFor({ state: "visible" });
  const failedSnapshot = posted.at(-1);
  for (const unreachedId of failedSnapshot.walk_stop.unreached_step_ids) {
    for (const field of ["assessment", "finding_severity", "finding_disposition", "observed"]) {
      const visual = page.locator(`[data-uat-field-save-visual="${unreachedId}:${field}"]`);
      assert.equal(await visual.getAttribute("data-state"), "failed", `${unreachedId}:${field} must retain failed save state`);
      assert.match(await visual.textContent(), /Not saved/, `${unreachedId}:${field} must report the failed save`);
    }
  }
} finally {
  await browser.close();
}
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(NODE_BIN),
                str(browser_script),
                str(playwright),
                f"http://{server.host}:{server.port}/uat/?round={round_id}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"browser regression failed:\n{result.stdout}\n{result.stderr}"
    finally:
        server.close()


def test_beforeunload_only_warns_for_unpersistable_or_inflight_autosave_risk(tmp_path, built_dist):
    """A restorable local draft must not turn every later reload into a warning."""
    playwright = PLAYWRIGHT_DIR
    assert playwright.exists(), f"shared dashboard Playwright runtime is missing: {playwright}"

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        fixture = make_active_uat_round(server.approved_root, manifest=make_com391_v2_manifest())
        round_id = server.register_active_round(fixture)
        browser_script = tmp_path / "beforeunload-local-draft-browser.cjs"
        browser_script.write_text(
            r'''
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2]);
const pageUrl = process.argv[3];

async function unloadPrompted(page) {
  return page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setViewportSize({ width: 320, height: 800 });
    await page.goto(pageUrl, { waitUntil: "networkidle" });
    await page.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, "the workbench fits a 320px viewport");
    assert.equal(await unloadPrompted(page), false, "an untouched page must not prompt");

    const card = page.locator("[data-uat-step-card]").first();
    const stepId = await card.getAttribute("data-uat-step-card");
    assert.ok(stepId, "the restored draft target has a stable step ID");
    await card.locator("[data-uat-step-caret]").click();
    const comment = card.locator("[data-uat-comment]");
    await comment.focus();
    await comment.pressSequentially("restored local draft", { delay: 1 });
    assert.match(await page.evaluate(() => localStorage.key(0) || ""), /^jw\.uat\.draft\.v1:/, "typing persists a local draft before blur");
    await page.reload({ waitUntil: "networkidle" });
    await page.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    assert.equal(await comment.inputValue(), "restored local draft", "the draft restores verbatim after reload");
    assert.equal(await unloadPrompted(page), false, "a wholly restorable draft must not prompt after reload");
    assert.equal(await page.locator("[data-uat-draft-restored]").isVisible(), true, "restoration is explained to the owner");
    await page.waitForFunction(() => localStorage.length === 0);
    assert.equal(await page.evaluate(() => localStorage.length), 0, "confirmed autosave clears the local draft");
    const storedEntry = await page.evaluate(async (restoredStepId) => {
      const response = await fetch(`/api/uat-rounds/${encodeURIComponent(new URL(location.href).searchParams.get("round"))}`);
      const detail = await response.json();
      return detail.feedback.step_entries[restoredStepId].comment;
    }, stepId);
    assert.equal(storedEntry, "restored local draft", "restored input resumes safely to the round API");

    const blockedStoragePage = await browser.newPage();
    await blockedStoragePage.addInitScript(() => {
      Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new DOMException("blocked", "SecurityError"); } });
    });
    await blockedStoragePage.goto(pageUrl, { waitUntil: "networkidle" });
    await blockedStoragePage.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    const blockedCard = blockedStoragePage.locator("[data-uat-step-card]").first();
    await blockedCard.locator("[data-uat-step-caret]").click();
    await blockedCard.locator("[data-uat-comment]").fill("cannot persist this draft");
    assert.equal(await unloadPrompted(blockedStoragePage), true, "a draft that cannot enter local storage must still prompt");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(NODE_BIN), str(browser_script), str(playwright), f"http://{server.host}:{server.port}/uat/?round={round_id}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"browser beforeunload contract failed:\n{result.stdout}\n{result.stderr}"
    finally:
        server.close()


def test_known_items_render_at_journey_scoring_point_without_empty_affordance(tmp_path, built_dist):
    """Step disclosures render only where targeted; empty steps and rounds stay quiet."""
    playwright = PLAYWRIGHT_DIR
    assert playwright.exists(), f"shared dashboard Playwright runtime is missing: {playwright}"

    disclosure_text = (
        "KNOWN LIVE LIMITATION: when the recommendation arrives as plain advice, "
        "the one-click action is not yet shown. Do not score its absence as a defect."
    )
    disclosed_manifest = make_com391_v2_manifest()
    target_step_id = disclosed_manifest["journeys"][0]["steps"][0]["step_id"]
    disclosed_manifest["journeys"][0]["known_items"] = [{
        "text": disclosure_text,
        "source_ref": "COM-391.uat-test.md#known-live-limitation",
        "step_refs": [target_step_id],
    }]
    disclosed_manifest["known_sources_checked"].append("COM-391.uat-test.md#known-live-limitation")

    disclosed_server = LiveServer(tmp_path / "disclosed", dist_dir=built_dist)
    empty_server = LiveServer(tmp_path / "empty", dist_dir=built_dist)
    try:
        disclosed = make_active_uat_round(disclosed_server.approved_root, manifest=disclosed_manifest)
        disclosed_round = disclosed_server.register_active_round(disclosed)
        empty = make_active_uat_round(empty_server.approved_root, manifest=make_com391_v2_manifest())
        empty_round = empty_server.register_active_round(empty)
        browser_script = tmp_path / "known-items-browser.cjs"
        browser_script.write_text(
            r'''
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2]);
const disclosedUrl = process.argv[3];
const emptyUrl = process.argv[4];
const disclosureText = process.argv[5];
const targetStepId = process.argv[6];

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 320, height: 800 } });
    await page.goto(disclosedUrl, { waitUntil: "networkidle" });
    await page.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    const firstJourney = page.locator("[data-uat-journey]").first();
    const targetCard = firstJourney.locator(`[data-uat-step-card="${targetStepId}"]`);
    assert.equal(await targetCard.count(), 1, "the disclosure target is the stable step ID from step_refs");
    const disclosure = targetCard.locator("[data-uat-known-items]");
    await disclosure.waitFor({ state: "visible" });
    assert.equal((await disclosure.textContent()).includes(disclosureText), true, "the targeted limitation is rendered verbatim");
    assert.equal(
      await disclosure.locator("[data-uat-known-item-source]").getAttribute("data-uat-known-item-source"),
      "COM-391.uat-test.md#known-live-limitation",
      "the disclosure keeps its canonical source reference",
    );
    assert.equal(await page.locator("[data-uat-known-items]").count(), 1, "a step-scoped disclosure is not repeated across journeys or steps");

    const stepCards = firstJourney.locator("[data-uat-step-card]");
    for (let index = 0; index < await stepCards.count(); index += 1) {
      const card = stepCards.nth(index);
      if (await card.getAttribute("data-uat-step-card") === targetStepId) continue;
      assert.equal(await card.locator("[data-uat-known-items]").count(), 0, "an untargeted step renders no empty disclosure shell or affordance");
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, "disclosures fit a 320px viewport");

    await page.goto(emptyUrl, { waitUntil: "networkidle" });
    await page.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    assert.equal(await page.locator("[data-uat-known-items]").count(), 0, "an empty round renders no disclosure shell or affordance");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(NODE_BIN), str(browser_script), str(playwright),
                f"http://{disclosed_server.host}:{disclosed_server.port}/uat/?round={disclosed_round}",
                f"http://{empty_server.host}:{empty_server.port}/uat/?round={empty_round}",
                disclosure_text,
                target_step_id,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"browser disclosure contract failed:\n{result.stdout}\n{result.stderr}"
    finally:
        disclosed_server.close()
        empty_server.close()



def test_queue_cards_and_portal_theme_are_visibly_distinct_in_real_browser(tmp_path, built_dist):
    # Computed pixels, not source tokens, prove queue selection and theming.
    playwright = PLAYWRIGHT_DIR
    assert playwright.exists(), f"shared dashboard Playwright runtime is missing: {playwright}"

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        fixture = make_active_uat_round(server.approved_root, manifest=make_com391_v2_manifest())
        selected_round = server.register_active_round(fixture)
        peer_fixture = fixture.copy()
        peer_fixture["registration"] = fixture["registration"].copy()
        peer_fixture["registration"]["round_review_id"] = "COM-391/round-fixture-002"
        peer_round = server.register_active_round(peer_fixture)

        browser_script = tmp_path / "queue-theme-browser.cjs"
        browser_script.write_text(
            r'''const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2]);
const pageUrl = process.argv[3];
const selectedRound = process.argv[4];
const peerRound = process.argv[5];
const fixUrl = process.argv[6];

function rgbTuple(value) {
  const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  assert.ok(match, `expected an rgb computed color, got ${value}`);
  return match.slice(1, 4).map(Number);
}
function luminance(value) {
  return rgbTuple(value).map((channel) => channel / 255).map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
}
function contrast(a, b) {
  const [lighter, darker] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (lighter + 0.05) / (darker + 0.05);
}
async function fixSnapshot(page, expectedTheme) {
  await page.goto(fixUrl, { waitUntil: "networkidle", timeout: 30000 });
  const card = page.locator("[data-fix-index-card]").first();
  await card.waitFor({ state: "visible", timeout: 30000 });
  const link = card.locator("a").first();
  assert.equal(await link.isVisible(), true, `${expectedTheme}: FIX publication link is visible`);
  const values = await page.evaluate(() => {
    const card = document.querySelector("[data-fix-index-card]");
    const link = card?.querySelector("a");
    if (!(card instanceof HTMLElement) || !(link instanceof HTMLAnchorElement)) {
      throw new Error("required FIX theme probes are missing");
    }
    const bodyStyle = getComputedStyle(document.body);
    const cardStyle = getComputedStyle(card);
    const linkStyle = getComputedStyle(link);
    return {
      theme: document.documentElement.dataset.theme || "system",
      bodyBackground: bodyStyle.backgroundColor,
      bodyText: bodyStyle.color,
      cardBackground: cardStyle.backgroundColor,
      cardText: cardStyle.color,
      linkColor: linkStyle.color,
      linkDecoration: linkStyle.textDecorationLine,
    };
  });
  assert.ok(contrast(values.bodyText, values.bodyBackground) >= 4.5, `${expectedTheme}: FIX page body text meets WCAG AA`);
  assert.ok(contrast(values.cardText, values.cardBackground) >= 4.5, `${expectedTheme}: FIX card text meets WCAG AA`);
  assert.ok(contrast(values.linkColor, values.cardBackground) >= 4.5, `${expectedTheme}: FIX link text meets WCAG AA`);
  assert.match(values.linkDecoration, /underline/, `${expectedTheme}: FIX link remains visibly underlined`);
  return {
    ...values,
    bodyContrast: contrast(values.bodyText, values.bodyBackground),
    cardContrast: contrast(values.cardText, values.cardBackground),
    linkContrast: contrast(values.linkColor, values.cardBackground),
  };
}

async function snapshot(page, expectedTheme) {
  await page.locator(`[data-uat-round-card][data-uat-round-id="${selectedRound}"]`).waitFor({ state: "visible", timeout: 30000 });
  const selected = page.locator(`[data-uat-round-card][data-uat-round-id="${selectedRound}"]`);
  const peer = page.locator(`[data-uat-round-card][data-uat-round-id="${peerRound}"]`);
  const pill = selected.locator("[data-uat-round-card-action]");
  await selected.focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  assert.equal(await selected.evaluate((element) => element === document.activeElement), true, `${expectedTheme}: keyboard returns focus to selected card`);
  assert.notEqual(await selected.getAttribute("aria-current"), null, `${expectedTheme}: selected card carries current state`);
  assert.equal((await pill.textContent()).trim(), "NOW REVIEWING", `${expectedTheme}: selected pill uses owner-approved copy`);
  assert.equal(await pill.isVisible(), true, `${expectedTheme}: selected pill is visibly rendered`);
  assert.equal(await peer.locator("[data-uat-round-card-action]").count(), 0, `${expectedTheme}: unselected card has no viewing pill`);
  const values = await page.evaluate(({ selectedRound, peerRound }) => {
    const selected = document.querySelector(`[data-uat-round-card][data-uat-round-id="${selectedRound}"]`);
    const peer = document.querySelector(`[data-uat-round-card][data-uat-round-id="${peerRound}"]`);
    const pill = selected?.querySelector("[data-uat-round-card-action]");
    const saveVisual = document.querySelector("[data-uat-field-save-visual]");
    if (!(selected instanceof HTMLElement) || !(peer instanceof HTMLElement) || !(pill instanceof HTMLElement) || !(saveVisual instanceof HTMLElement)) {
      throw new Error("required visible queue/theme probes are missing");
    }
    const selectedStyle = getComputedStyle(selected);
    const peerStyle = getComputedStyle(peer);
    const bodyStyle = getComputedStyle(document.body);
    const pillStyle = getComputedStyle(pill);
    const saveStyle = getComputedStyle(saveVisual);
    const focusStyle = getComputedStyle(selected);
    saveVisual.dataset.state = "saved";
    saveVisual.textContent = "✓ Saved";
    saveVisual.classList.add("text-jw-low");
    const savedStyle = getComputedStyle(saveVisual);
    const savedProbe = { visibility: savedStyle.visibility, opacity: savedStyle.opacity, color: savedStyle.color };
    saveVisual.dataset.state = "idle";
    saveVisual.textContent = "";
    saveVisual.classList.remove("text-jw-low");
    return {
      theme: document.documentElement.dataset.theme || "system",
      blueVariable: getComputedStyle(document.documentElement).getPropertyValue("--jw-blue").trim(),
      selectedBackground: selectedStyle.backgroundColor,
      selectedBorder: selectedStyle.borderLeftColor,
      peerBackground: peerStyle.backgroundColor,
      bodyBackground: bodyStyle.backgroundColor,
      bodyText: bodyStyle.color,
      pillBackground: pillStyle.backgroundColor,
      pillText: pillStyle.color,
      focusOutlineColor: focusStyle.outlineColor,
      focusOutlineWidth: focusStyle.outlineWidth,
      saveVisibility: saveStyle.visibility,
      saveOpacity: saveStyle.opacity,
      savedVisibility: savedProbe.visibility,
      savedOpacity: savedProbe.opacity,
      savedColor: savedProbe.color,
    };
  }, { selectedRound, peerRound });
  assert.notEqual(values.selectedBackground, values.peerBackground, `${expectedTheme}: selected and unselected computed backgrounds differ`);
  assert.ok(contrast(values.bodyText, values.bodyBackground) >= 4.5, `${expectedTheme}: body text meets WCAG AA`);
  assert.ok(contrast(values.selectedBorder, values.peerBackground) >= 3, `${expectedTheme}: selected state boundary ${values.selectedBorder} vs ${values.peerBackground} is ${contrast(values.selectedBorder, values.peerBackground)}; --jw-blue=${values.blueVariable}`);
  assert.ok(contrast(values.pillText, values.pillBackground) >= 4.5, `${expectedTheme}: viewing pill text meets WCAG AA`);
  assert.ok(parseFloat(values.focusOutlineWidth) >= 2, `${expectedTheme}: keyboard focus outline remains at least 2px`);
  assert.ok(contrast(values.focusOutlineColor, values.bodyBackground) >= 3, `${expectedTheme}: keyboard focus outline meets non-text AA`);
  assert.equal(values.saveVisibility, "hidden", `${expectedTheme}: idle field save indicator remains visually hidden`);
  assert.equal(values.saveOpacity, "0", `${expectedTheme}: idle field save indicator has zero opacity`);
  assert.equal(values.savedVisibility, "visible", `${expectedTheme}: saved field indicator is visibly rendered`);
  assert.equal(values.savedOpacity, "1", `${expectedTheme}: saved field indicator is fully opaque`);
  assert.ok(contrast(values.savedColor, values.bodyBackground) >= 4.5, `${expectedTheme}: saved field indicator text meets WCAG AA`);
  return { ...values, bodyContrast: contrast(values.bodyText, values.bodyBackground), stateContrast: contrast(values.selectedBorder, values.peerBackground), pillContrast: contrast(values.pillText, values.pillBackground) };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ colorScheme: "light" });
  try {
    await page.goto(pageUrl, { waitUntil: "networkidle", timeout: 30000 });
    const light = await snapshot(page, "light");
    const fixLight = await fixSnapshot(page, "light");
    const toggle = page.locator("[data-theme-toggle]");
    assert.equal(await toggle.isVisible(), true, "portal-wide theme toggle is visible on the FIX index");
    await toggle.click();
    await page.locator('html[data-theme="dark"]').waitFor({ state: "attached", timeout: 30000 });
    const fixDark = await fixSnapshot(page, "dark");
    assert.notEqual(fixLight.bodyBackground, fixDark.bodyBackground, "theme toggle changes the FIX page background");
    assert.notEqual(fixLight.cardBackground, fixDark.cardBackground, "theme toggle changes the FIX card background");
    await page.goto(pageUrl, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForFunction(({ selectedRound }) => {
      const selected = document.querySelector(`[data-uat-round-card][data-uat-round-id="${selectedRound}"]`);
      const pill = selected?.querySelector("[data-uat-round-card-action]");
      return selected instanceof HTMLElement && pill instanceof HTMLElement
        && getComputedStyle(selected).borderLeftColor === getComputedStyle(pill).backgroundColor;
    }, { selectedRound }, { timeout: 30000 });
    const dark = await snapshot(page, "dark");
    assert.notEqual(light.bodyBackground, dark.bodyBackground, "theme toggle changes the computed portal background");
    assert.notEqual(light.peerBackground, dark.peerBackground, "theme toggle changes the computed queue palette");
    const stored = await page.evaluate(() => { try { return localStorage.getItem("decision-review-theme"); } catch { return null; } });
    assert.equal(stored, "dark", "explicit theme choice persists per browser");
    const responsive = {};
    for (const width of [1440, 768, 375]) {
      await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
      responsive[width] = await page.evaluate(() => ({
        fits: document.documentElement.scrollWidth <= window.innerWidth,
        toggleVisible: Boolean(document.querySelector("[data-theme-toggle]")?.getBoundingClientRect().width),
      }));
      assert.equal(responsive[width].fits, true, `${width}px: portal has no horizontal overflow`);
      assert.equal(responsive[width].toggleVisible, true, `${width}px: theme toggle remains visible`);
    }

    const systemDarkContext = await browser.newContext({ colorScheme: "dark" });
    const systemDarkPage = await systemDarkContext.newPage();
    await systemDarkPage.goto(pageUrl, { waitUntil: "networkidle", timeout: 30000 });
    const systemDark = await snapshot(systemDarkPage, "system dark");
    assert.equal(systemDark.theme, "system", "dark preference remains the default without an explicit choice");
    assert.equal(systemDark.bodyBackground, dark.bodyBackground, "prefers-color-scheme dark receives the dark computed palette");
    await systemDarkContext.close();

    const explicitLightContext = await browser.newContext({ colorScheme: "dark" });
    await explicitLightContext.addInitScript(() => localStorage.setItem("decision-review-theme", "light"));
    const explicitLightPage = await explicitLightContext.newPage();
    await explicitLightPage.goto(pageUrl, { waitUntil: "networkidle", timeout: 30000 });
    const explicitLight = await snapshot(explicitLightPage, "explicit light");
    assert.equal(explicitLight.theme, "light", "stored light choice overrides a dark system preference before rendering content");
    assert.equal(explicitLight.bodyBackground, light.bodyBackground, "stored light choice receives the light computed palette");
    await explicitLightContext.close();

    console.log(JSON.stringify({ light, dark, fixLight, fixDark, systemDark, explicitLight, responsive }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(NODE_BIN),
                str(browser_script),
                str(playwright),
                f"http://{server.host}:{server.port}/uat/?round={selected_round}",
                selected_round,
                peer_round,
                f"http://{server.host}:{server.port}/fix/",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"queue/theme browser regression failed:\n{result.stdout}\n{result.stderr}"
        print(result.stdout.strip())
    finally:
        server.close()


def test_step_assessment_status_is_visible_themed_and_persists_canonical_values(tmp_path, built_dist):
    """Assessment drives the visible card status; completion only collapses feedback."""
    playwright = PLAYWRIGHT_DIR
    assert playwright.exists(), f"shared dashboard Playwright runtime is missing: {playwright}"

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        fixture = make_active_uat_round(server.approved_root, manifest=make_com391_v2_manifest())
        round_id = server.register_active_round(fixture)
        browser_script = tmp_path / "step-assessment-status-browser.cjs"
        browser_script.write_text(
            r'''
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2]);
const pageUrl = process.argv[3];

const expected = {
  light: {
    PASS: { pill: "PASS", ground: "rgb(227, 243, 232)", border: "rgb(15, 107, 58)" },
    FAIL: { pill: "FAIL", ground: "rgb(253, 231, 231)", border: "rgb(179, 38, 30)" },
    BLOCKED: { pill: "BLOCKED", ground: "rgb(253, 236, 200)", border: "rgb(155, 86, 0)" },
    NOT_OBSERVED: { pill: "NOT OBSERVED", ground: "rgb(253, 236, 200)", border: "rgb(155, 86, 0)" },
    NOT_APPLICABLE: { pill: "N/A", ground: "rgb(227, 235, 247)", border: "rgb(58, 90, 138)" },
  },
  dark: {
    PASS: { pill: "PASS", ground: "rgb(24, 60, 40)", border: "rgb(116, 214, 154)" },
    FAIL: { pill: "FAIL", ground: "rgb(75, 31, 31)", border: "rgb(255, 180, 171)" },
    BLOCKED: { pill: "BLOCKED", ground: "rgb(70, 53, 26)", border: "rgb(244, 192, 106)" },
    NOT_OBSERVED: { pill: "NOT OBSERVED", ground: "rgb(70, 53, 26)", border: "rgb(244, 192, 106)" },
    NOT_APPLICABLE: { pill: "N/A", ground: "rgb(28, 53, 87)", border: "rgb(170, 199, 255)" },
  },
};

function rgbTuple(value) {
  const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  assert.ok(match, `expected an rgb computed color, got ${value}`);
  return match.slice(1, 4).map(Number);
}
function luminance(value) {
  return rgbTuple(value).map((channel) => channel / 255).map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
}
function contrast(a, b) {
  const [lighter, darker] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (lighter + 0.05) / (darker + 0.05);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const posts = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().endsWith("/feedback")) posts.push(JSON.parse(request.postData()));
    });
    await page.goto(pageUrl, { waitUntil: "networkidle" });
    await page.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    const cards = page.locator("[data-uat-step-card]");
    assert.ok(await cards.count() >= 5, "fixture exposes one card per assessment status");
    assert.equal((await cards.first().locator("[data-uat-step-state]").textContent()).trim(), "NOT ASSESSED");

    const assessments = ["PASS", "FAIL", "BLOCKED", "NOT_OBSERVED", "NOT_APPLICABLE"];
    for (let index = 0; index < assessments.length; index += 1) {
      const assessment = assessments[index];
      const card = cards.nth(index);
      const stepId = await card.getAttribute("data-uat-step-card");
      await card.locator("[data-uat-step-caret]").click();
      const request = page.waitForRequest((candidate) => candidate.method() === "POST" && candidate.url().endsWith("/feedback"));
      await card.locator("[data-uat-assessment]").selectOption(assessment);
      await request;
      assert.equal(posts.at(-1).step_entries[stepId].assessment, assessment, `${assessment} is POSTed unchanged`);
    }

    const evidence = {};
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => { document.documentElement.dataset.theme = value; }, theme);
      evidence[theme] = {};
      for (let index = 0; index < assessments.length; index += 1) {
        const assessment = assessments[index];
        const card = cards.nth(index);
        const values = await card.evaluate((element) => {
          const badge = element.querySelector("[data-uat-step-state]");
          if (!(badge instanceof HTMLElement)) throw new Error("status pill is missing");
          const cardStyle = getComputedStyle(element);
          const badgeStyle = getComputedStyle(badge);
          return {
            pill: badge.textContent.trim(),
            ground: cardStyle.backgroundColor,
            border: cardStyle.borderTopColor,
            text: cardStyle.color,
            pillText: badgeStyle.color,
            pillGround: badgeStyle.backgroundColor,
          };
        });
        assert.deepEqual(
          { pill: values.pill, ground: values.ground, border: values.border },
          expected[theme][assessment],
          `${theme} ${assessment} has the required visible status treatment`,
        );
        const textRatio = contrast(values.text, values.ground);
        const borderRatio = contrast(values.border, values.ground);
        const pillRatio = contrast(values.pillText, values.pillGround);
        assert.ok(textRatio >= 4.5, `${theme} ${assessment} card text contrast is ${textRatio}`);
        assert.ok(pillRatio >= 4.5, `${theme} ${assessment} pill text contrast is ${pillRatio}`);
        assert.ok(borderRatio >= 3, `${theme} ${assessment} border contrast is ${borderRatio}`);
        evidence[theme][assessment] = { ...values, textRatio, pillRatio, borderRatio };
      }
    }

    const passCard = cards.first();
    const severityRequest = page.waitForRequest((candidate) => candidate.method() === "POST" && candidate.url().endsWith("/feedback"));
    await passCard.locator("[data-uat-severity]").selectOption("NONE");
    await severityRequest;
    const details = passCard.locator("[data-uat-step-caret]");
    assert.equal(await details.getAttribute("open"), "", "feedback form is open before completion");
    const completeRequest = page.waitForRequest((candidate) => candidate.method() === "POST" && candidate.url().endsWith("/feedback"));
    await passCard.locator("[data-uat-complete]").check();
    await completeRequest;
    assert.equal(await details.getAttribute("open"), null, "checking complete auto-collapses the feedback form");
    assert.equal((await passCard.locator("[data-uat-step-state]").textContent()).trim(), "PASS", "completion does not replace an existing assessment status");
    assert.equal(posts.at(-1).step_entries[await passCard.getAttribute("data-uat-step-card")].assessment, "PASS", "completion POST preserves canonical PASS");
    assert.equal(posts.at(-1).step_entries[await passCard.getAttribute("data-uat-step-card")].observed, "", "completion does not fabricate an owner observation");

    for (const index of [5, 6]) {
      const shortcutCard = cards.nth(index);
      const shortcutId = await shortcutCard.getAttribute("data-uat-step-card");
      await shortcutCard.locator("[data-uat-step-caret]").click();
      if (index === 6) {
        const comment = shortcutCard.locator("[data-uat-comment]");
        await comment.fill("temporary note");
        await comment.fill("");
        await comment.blur();
      }
      const shortcutDetails = shortcutCard.locator("[data-uat-step-caret]");
      const shortcutRequest = page.waitForRequest((candidate) => candidate.method() === "POST" && candidate.url().endsWith("/feedback"));
      await shortcutCard.locator("[data-uat-complete]").check();
      await shortcutRequest;
      const shortcutEntry = posts.at(-1).step_entries[shortcutId];
      assert.deepEqual(
        {
          complete: shortcutEntry.complete,
          assessment: shortcutEntry.assessment,
          finding_severity: shortcutEntry.finding_severity,
          finding_disposition: shortcutEntry.finding_disposition,
          observed: shortcutEntry.observed,
          comment: shortcutEntry.comment,
        },
        {
          complete: true,
          assessment: "",
          finding_severity: "",
          finding_disposition: "",
          observed: "",
          comment: "",
        },
        index === 5 ? "empty completion preserves genuinely optional fields" : "typed-then-cleared completion stays genuinely empty",
      );
      assert.equal(await shortcutDetails.getAttribute("open"), null, "optional completion collapses feedback");
      assert.equal((await shortcutCard.locator("[data-uat-step-state]").textContent()).trim(), "NOT ASSESSED", "completion does not fabricate a status");
    }

    console.log(JSON.stringify(evidence));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(NODE_BIN), str(browser_script), str(playwright), f"http://{server.host}:{server.port}/uat/?round={round_id}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, f"browser step status contract failed:\n{result.stdout}\n{result.stderr}"
        print(result.stdout.strip())
    finally:
        server.close()


def test_optional_completion_and_contract_cascades_are_truthful_in_real_browser(tmp_path, built_dist):
    """No completion field is required and every offered cascade has a legal leaf."""
    playwright = PLAYWRIGHT_DIR
    assert playwright.exists(), f"shared dashboard Playwright runtime is missing: {playwright}"

    text_server = LiveServer(tmp_path / "text-only", dist_dir=built_dist)
    empty_server = LiveServer(tmp_path / "empty", dist_dir=built_dist)
    cascade_server = LiveServer(tmp_path / "cascade", dist_dir=built_dist)
    try:
        text_fixture = make_active_uat_round(text_server.approved_root, manifest=make_com391_v2_manifest())
        empty_fixture = make_active_uat_round(empty_server.approved_root, manifest=make_com391_v2_manifest())
        cascade_fixture = make_active_uat_round(cascade_server.approved_root, manifest=make_com391_v2_manifest())
        text_round = text_server.register_active_round(text_fixture)
        empty_round = empty_server.register_active_round(empty_fixture)
        cascade_round = cascade_server.register_active_round(cascade_fixture)
        browser_script = tmp_path / "optional-completion-contract-cascade-browser.cjs"
        browser_script.write_text(
            r'''
const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2]);
const textUrl = process.argv[3];
const emptyUrl = process.argv[4];
const cascadeUrl = process.argv[5];

async function waitForFeedbackPost(page, action) {
  const responsePromise = page.waitForResponse((candidate) =>
    candidate.request().method() === "POST" && candidate.url().endsWith("/feedback"),
  );
  await action();
  const response = await responsePromise;
  assert.equal(response.status(), 200, `autosave failed: ${await response.text()}`);
  return JSON.parse(response.request().postData());
}

async function submitAndCapture(page) {
  const responsePromise = page.waitForResponse((candidate) =>
    candidate.request().method() === "POST" && candidate.url().endsWith("/feedback/submit"),
  );
  await page.locator("[data-uat-save]").click();
  const response = await responsePromise;
  assert.equal(response.status(), 200, `submit failed: ${await response.text()}`);
  const posted = JSON.parse(response.request().postData());
  await page.locator("[data-uat-save-state]").waitFor({ state: "visible" });
  assert.match(await page.locator("[data-uat-save-state]").textContent(), /submitted to the ticket agent/);
  return posted;
}

async function assertErrorsActuallyHidden(card, label) {
  const evidence = await card.evaluate((element) => ({
    fieldErrors: [...element.querySelectorAll("[data-uat-field-error]")].map((node) => ({
      text: node.textContent,
      display: getComputedStyle(node).display,
      hiddenClass: node.classList.contains("hidden"),
    })),
    invalidControls: element.querySelectorAll('[aria-invalid="true"]').length,
  }));
  assert.equal(evidence.invalidControls, 0, `${label}: no control is marked invalid`);
  assert.ok(evidence.fieldErrors.every((error) => error.display === "none"), `${label}: every field error is computed hidden`);
  return evidence;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const textPage = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await textPage.goto(textUrl, { waitUntil: "networkidle" });
    await textPage.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    const textCard = textPage.locator("[data-uat-step-card]").first();
    await textCard.locator("[data-uat-step-caret]").click();
    const textStep = await textCard.getAttribute("data-uat-step-card");

    const initialCascade = await textCard.evaluate((element) => {
      const severity = element.querySelector("[data-uat-severity]");
      const disposition = element.querySelector("[data-uat-disposition]");
      return {
        severityDisabled: severity.disabled,
        severityText: severity.options[0].textContent,
        dispositionDisabled: disposition.disabled,
        dispositionText: disposition.options[0].textContent,
      };
    });
    assert.deepEqual(initialCascade, {
      severityDisabled: true,
      severityText: "Choose assessment first",
      dispositionDisabled: true,
      dispositionText: "Choose assessment and severity first",
    }, "unavailable dependent controls state their prerequisite instead of asking an empty question");

    const observation = "Only the owner observation was entered.";
    await waitForFeedbackPost(textPage, async () => {
      await textCard.locator("[data-uat-observed]").fill(observation);
      await textCard.locator("[data-uat-observed]").blur();
    });
    const textCompletion = await waitForFeedbackPost(textPage, () => textCard.locator("[data-uat-complete]").check());
    assert.deepEqual(textCompletion.step_entries[textStep], {
      complete: true,
      assessment: "",
      finding_severity: "",
      finding_disposition: "",
      observed: observation,
      comment: "",
    }, "text-only completion POST retains blank optional classifications");
    const textErrors = await assertErrorsActuallyHidden(textCard, "text-only completion");

    const oldDeadPairs = [
      ["PASS", "MAJOR"], ["FAIL", "NONE"], ["BLOCKED", "MINOR"], ["BLOCKED", "MAJOR"],
      ["NOT_OBSERVED", "MINOR"], ["NOT_OBSERVED", "MAJOR"],
      ["NOT_APPLICABLE", "MINOR"], ["NOT_APPLICABLE", "MAJOR"],
    ];
    const cascadePage = await browser.newPage({ viewport: { width: 768, height: 900 } });
    await cascadePage.goto(cascadeUrl, { waitUntil: "networkidle" });
    await cascadePage.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    const cascadeCard = cascadePage.locator("[data-uat-step-card]").first();
    await cascadeCard.locator("[data-uat-step-caret]").click();
    const pairEvidence = [];
    for (const [assessment, severity] of oldDeadPairs) {
      if (await cascadeCard.locator("[data-uat-assessment]").inputValue() !== assessment) {
        await waitForFeedbackPost(cascadePage, () => cascadeCard.locator("[data-uat-assessment]").selectOption(assessment));
      }
      const severities = await cascadeCard.locator("[data-uat-severity] option").evaluateAll((options) => options.map((option) => option.value).filter(Boolean));
      if (!severities.includes(severity)) {
        pairEvidence.push({ assessment, severity, state: "unreachable", offeredSeverities: severities });
        continue;
      }
      await waitForFeedbackPost(cascadePage, () => cascadeCard.locator("[data-uat-severity]").selectOption(severity));
      const disposition = await cascadeCard.locator("[data-uat-disposition]").evaluate((control) => ({
        disabled: control.disabled,
        values: [...control.options].map((option) => option.value).filter(Boolean),
        text: [...control.options].map((option) => option.textContent),
      }));
      assert.equal(disposition.disabled, false, `${assessment}/${severity}: disposition is operable`);
      assert.ok(disposition.values.length > 0, `${assessment}/${severity}: disposition has a legal contract option`);
      pairEvidence.push({ assessment, severity, state: "available", dispositions: disposition.values });
    }
    assert.deepEqual(
      pairEvidence.map(({ assessment, severity, state }) => [assessment, severity, state]),
      [
        ["PASS", "MAJOR", "unreachable"], ["FAIL", "NONE", "available"],
        ["BLOCKED", "MINOR", "unreachable"], ["BLOCKED", "MAJOR", "unreachable"],
        ["NOT_OBSERVED", "MINOR", "unreachable"], ["NOT_OBSERVED", "MAJOR", "unreachable"],
        ["NOT_APPLICABLE", "MINOR", "unreachable"], ["NOT_APPLICABLE", "MAJOR", "unreachable"],
      ],
      "the seven still-illegal pairs are unreachable; newly legal FAIL/NONE exposes OPEN",
    );
    assert.deepEqual(pairEvidence.find((item) => item.assessment === "FAIL" && item.severity === "NONE").dispositions, ["OPEN"]);
    console.log(JSON.stringify({ stage: "pre-submit", textCompletion: textCompletion.step_entries[textStep], pairEvidence, textErrors }));

    const textSubmit = await submitAndCapture(textPage);
    assert.deepEqual(textSubmit.step_entries[textStep], textCompletion.step_entries[textStep], "text-only card submits exactly as completed");

    const emptyPage = await browser.newPage({ viewport: { width: 375, height: 812 } });
    await emptyPage.goto(emptyUrl, { waitUntil: "networkidle" });
    await emptyPage.locator("[data-uat-workbench]").waitFor({ state: "visible" });
    const emptyCard = emptyPage.locator("[data-uat-step-card]").first();
    await emptyCard.locator("[data-uat-step-caret]").click();
    const emptyStep = await emptyCard.getAttribute("data-uat-step-card");
    const emptyCompletion = await waitForFeedbackPost(emptyPage, () => emptyCard.locator("[data-uat-complete]").check());
    assert.deepEqual(emptyCompletion.step_entries[emptyStep], {
      complete: true,
      assessment: "",
      finding_severity: "",
      finding_disposition: "",
      observed: "",
      comment: "",
    }, "empty completion POST does not fabricate owner input");
    const emptyErrors = await assertErrorsActuallyHidden(emptyCard, "empty completion");
    const emptySubmit = await submitAndCapture(emptyPage);
    assert.deepEqual(emptySubmit.step_entries[emptyStep], emptyCompletion.step_entries[emptyStep], "empty card submits exactly as completed");

    console.log(JSON.stringify({ textCompletion: textSubmit.step_entries[textStep], emptyCompletion: emptySubmit.step_entries[emptyStep], pairEvidence, textErrors, emptyErrors }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(NODE_BIN), str(browser_script), str(playwright),
                f"http://{text_server.host}:{text_server.port}/uat/?round={text_round}",
                f"http://{empty_server.host}:{empty_server.port}/uat/?round={empty_round}",
                f"http://{cascade_server.host}:{cascade_server.port}/uat/?round={cascade_round}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"optional completion browser regression failed:\n{result.stdout}\n{result.stderr}"
        print(result.stdout.strip())
    finally:
        text_server.close()
        empty_server.close()
        cascade_server.close()


def test_queue_round_status_is_shared_first_change_only_and_visibly_safe(tmp_path, built_dist):
    """The queue status is a real shared control, not a browser-local decoration."""
    playwright = PLAYWRIGHT_DIR
    assert playwright.exists(), f"shared dashboard Playwright runtime is missing: {playwright}"

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        fixture = make_active_uat_round(server.approved_root, manifest=make_com391_v2_manifest())
        round_id = server.register_active_round(fixture)
        browser_script = tmp_path / "queue-round-status-browser.cjs"
        browser_script.write_text(
            r'''const assert = require("node:assert/strict");
const { chromium } = require(process.argv[2]);
const pageUrl = process.argv[3];
const roundId = process.argv[4];

function rgbTuple(value) {
  const match = String(value).match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  assert.ok(match, `expected rgb color, got ${value}`);
  return match.slice(1, 4).map(Number);
}
function luminance(value) {
  return rgbTuple(value).map((channel) => channel / 255).map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
}
function contrast(a, b) {
  const [lighter, darker] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (lighter + 0.05) / (darker + 0.05);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const statusPosts = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/status") && request.headers()["x-test-external"] !== "1") {
      statusPosts.push(JSON.parse(request.postData()));
    }
  });
  const card = () => page.locator(`[data-uat-round-card][data-uat-round-id="${roundId}"]`);
  const status = () => card().locator("[data-uat-round-status]");

  async function selectStatus(value, expectedStatus = 200) {
    const responsePromise = page.waitForResponse((response) =>
      response.request().method() === "POST" && response.url().endsWith("/status")
    );
    await status().selectOption(value);
    const response = await responsePromise;
    assert.equal(response.status(), expectedStatus, `${value} status POST`);
    return response;
  }
  async function changeFirstComment(value) {
    const step = page.locator("[data-uat-step-card]").first();
    const caret = step.locator("[data-uat-step-caret]");
    if ((await caret.getAttribute("aria-expanded")) !== "true") await caret.click();
    const responsePromise = page.waitForResponse((response) =>
      response.request().method() === "POST" && response.url().endsWith("/feedback")
    );
    const comment = step.locator("[data-uat-comment]");
    await comment.fill(value);
    await comment.blur();
    const response = await responsePromise;
    assert.equal(response.status(), 200, "step edit persisted");
    return response;
  }

  try {
    await page.goto(pageUrl, { waitUntil: "networkidle", timeout: 30000 });
    await card().waitFor({ state: "visible", timeout: 30000 });
    await status().waitFor({ state: "visible", timeout: 30000 });

    const optionEvidence = await status().evaluate((select) => Array.from(select.options).map((option) => ({ value: option.value, label: option.textContent.trim() })));
    assert.deepEqual(optionEvidence, [
      { value: "NOT_STARTED", label: "NOT STARTED" },
      { value: "IN_PROGRESS", label: "IN PROGRESS" },
      { value: "COMPLETE", label: "COMPLETE" },
      { value: "FAILED", label: "FAILED" },
    ], "dropdown offers exactly the owner-approved statuses");
    assert.equal(await status().inputValue(), "NOT_STARTED");
    assert.equal(await page.locator('[data-uat-round-card]').evaluateAll((cards) => cards.some((card) => /\bISSUED\b/.test(card.textContent))), false, "ISSUED renders on no queue card");

    const computed = await status().evaluate((select) => {
      const style = getComputedStyle(select);
      const rect = select.getBoundingClientRect();
      return {
        display: style.display,
        visibility: style.visibility,
        opacity: style.opacity,
        color: style.color,
        backgroundColor: style.backgroundColor,
        width: rect.width,
        height: rect.height,
      };
    });
    assert.notEqual(computed.display, "none");
    assert.equal(computed.visibility, "visible");
    assert.equal(computed.opacity, "1");
    assert.ok(computed.width > 0 && computed.height >= 44, `dropdown has an operable computed box: ${JSON.stringify(computed)}`);
    assert.ok(contrast(computed.color, computed.backgroundColor) >= 4.5, "dropdown computed text contrast meets AA");

    await selectStatus("COMPLETE");
    await page.waitForFunction(({ roundId }) => document.querySelector(`[data-uat-round-card][data-uat-round-id="${roundId}"] [data-uat-round-status]`)?.value === "COMPLETE", { roundId });
    await page.reload({ waitUntil: "networkidle" });
    await status().waitFor({ state: "visible" });
    assert.equal(await status().inputValue(), "COMPLETE", "manual selection survives reload from shared service state");

    await selectStatus("NOT_STARTED");
    await changeFirstComment("First persisted owner change promotes the new round.");
    await page.waitForFunction(({ roundId }) => document.querySelector(`[data-uat-round-card][data-uat-round-id="${roundId}"] [data-uat-round-status]`)?.value === "IN_PROGRESS", { roundId });
    assert.equal(await status().inputValue(), "IN_PROGRESS", "first persisted content change promotes NOT STARTED");

    await selectStatus("FAILED");
    await changeFirstComment("A later typo correction must not override the owner's terminal status.");
    assert.equal(await status().inputValue(), "FAILED", "later content edits do not override manual FAILED");
    await page.reload({ waitUntil: "networkidle" });
    await status().waitFor({ state: "visible" });
    assert.equal(await status().inputValue(), "FAILED", "manual terminal status remains canonical after later edits and reload");

    const current = await page.evaluate(async (roundId) => (await fetch(`/api/uat-rounds/${encodeURIComponent(roundId)}`)).json(), roundId);
    const external = await page.evaluate(async ({ roundId, current }) => {
      const response = await fetch(`/api/uat-rounds/${encodeURIComponent(roundId)}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Test-External": "1" },
        body: JSON.stringify({
          schema: "jswarm.test-uat.round-status-update/v1",
          schema_version: "1.0",
          expected_feedback_sha256: current.feedback.sha256,
          expected_current_round_sha256: current.current_round.sha256,
          round_status: "COMPLETE",
        }),
      });
      return { status: response.status, body: await response.json() };
    }, { roundId, current });
    assert.equal(external.status, 200, "external owner changes canonical status");
    const staleResponse = await selectStatus("IN_PROGRESS", 409);
    const staleBody = await staleResponse.json();
    assert.equal(staleBody.error.code, "stale_feedback");
    assert.equal(await status().inputValue(), "FAILED", "stale choice reverts instead of masquerading as saved");
    const staleMessage = card().locator("[data-uat-round-status-message]");
    await staleMessage.waitFor({ state: "visible" });
    const staleEvidence = await staleMessage.evaluate((node) => ({
      text: node.textContent.trim(),
      display: getComputedStyle(node).display,
      visibility: getComputedStyle(node).visibility,
      opacity: getComputedStyle(node).opacity,
    }));
    assert.match(staleEvidence.text, /not saved|changed/i);
    assert.notEqual(staleEvidence.display, "none");
    assert.equal(staleEvidence.visibility, "visible");
    assert.equal(staleEvidence.opacity, "1");
    await page.reload({ waitUntil: "networkidle" });
    await status().waitFor({ state: "visible" });
    assert.equal(await status().inputValue(), "COMPLETE", "reload reveals the newer canonical status after a stale rejection");

    assert.deepEqual(statusPosts.map((body) => body.round_status), ["COMPLETE", "NOT_STARTED", "FAILED", "IN_PROGRESS"]);
    assert.ok(statusPosts.every((body) => Object.keys(body).sort().join(",") === "expected_current_round_sha256,expected_feedback_sha256,round_status,schema,schema_version"), "every status POST uses only the closed command fields");
    console.log(JSON.stringify({ optionEvidence, computed, computedContrast: contrast(computed.color, computed.backgroundColor), persisted: "COMPLETE", firstChange: "IN_PROGRESS", terminalAfterLaterEdit: "FAILED", staleEvidence, issuedCards: 0 }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                str(NODE_BIN), str(browser_script), str(playwright),
                f"http://{server.host}:{server.port}/uat/?round={round_id}", round_id,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"round status browser regression failed:\n{result.stdout}\n{result.stderr}"
        print(result.stdout.strip())
    finally:
        server.close()
