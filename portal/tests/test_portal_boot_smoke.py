"""Regression test for the portal build-and-boot defect (public-split B2).

Before this fix, two independent bugs left the UAT/FIX portal permanently
unbuildable on a fresh clone:

1. ``jswarm/portal/render_ui.py`` and ``jswarm/uat_prepare_local_process.py``
   still pointed at the pre-split ``decision-review-ui`` directory; the real
   Astro project had been renamed to ``portal`` but those references were
   never updated, so the build looked for a directory that no longer existed.
2. Zero example fix-decision contracts shipped anywhere in the repo, so even
   with the path fixed, the auto-discovery build (``schemas/fix-decisions/
   examples/*.json``) had nothing to render and failed unconditionally.

The practical consequence: ``install.sh portal`` could never produce a
``dist/`` directory, the portal server refused to start (it hard-requires
``dist/index.html``), and ``GET /uat/`` 404'd even after a UAT round had been
correctly issued and confirmed over the API -- a round that existed and was
walkable by machine, but invisible to a human.

This test builds the portal from the shipped repo state, using the exact
auto-discovery path ``install.sh portal`` uses (no build manifest, no test
double), boots a real server against that build, and confirms the UAT route
serves real content over real HTTP -- so a future regression in the path, the
shipped fixtures, or the Jinja2 render template fails CI instead of being
discovered by a human opening a browser.

Slow (a real ``npm ci`` + ``astro build``): marked ``portal_build`` and
excluded from the default ``pytest -q`` run (see ``pytest.ini``). CI runs it
explicitly as its own step, with Node installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

pytestmark = pytest.mark.portal_build

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _node_available() -> bool:
    return shutil.which("node") is not None and shutil.which("npm") is not None


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory):
    """A real portal build from the shipped repo state (no fixtures, no mocking)."""
    if not _node_available():
        pytest.skip("Node/npm not on PATH; the portal build smoke test requires a managed Node runtime")
    out = tmp_path_factory.mktemp("portal-boot-smoke-dist")
    # No --build-manifest: this is the exact auto-discovery path
    # `install.sh portal` / `render_ui.py --build-only` uses, against the
    # examples checked into schemas/fix-decisions/examples/.
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "jswarm.portal.render_ui", "--out", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        "portal build failed from the shipped repo state -- this is exactly the "
        "defect this test guards against (a stale `decision-review-ui` path, "
        "missing schemas/fix-decisions/examples fixtures, or a missing render "
        "template all fail here):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert (out / "index.html").is_file(), "astro build did not produce the front page"
    assert (out / "uat" / "index.html").is_file(), "astro build did not produce the /uat/ route"
    review_pages = list((out / "review").glob("*/index.html"))
    assert review_pages, "astro build did not produce any /review/ pages from the shipped example contracts"
    return out


def test_portal_builds_from_shipped_repo_state(built_dist):
    """The build itself is the primary regression guard -- see the built_dist fixture."""
    assert (built_dist / "uat" / "index.html").is_file()


def test_uat_route_serves_real_content_not_a_404_or_empty_stub(built_dist, tmp_path):
    from jswarm.portal.tests.service_fixtures import LiveServer

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        with urllib.request.urlopen(f"{server.client.base}/uat/", timeout=10) as resp:
            status = resp.status
            html = resp.read().decode("utf-8")
        assert status == 200, "GET /uat/ must not 404 (this is the exact symptom the rehearsal observed)"
        assert "Active UAT rounds" in html, "the UAT shell's real heading is missing -- looks like a stub page"
        assert "data-uat-workbench" in html, "the UAT workbench mount point is missing from the built page"
        assert "not_found" not in html and "no such file" not in html, "response looks like a routed error, not the real page"
    finally:
        server.close()


def _minimal_v1_round_manifest() -> dict:
    """A minimal, self-contained ``uat-canonical-package@1`` manifest.

    Deliberately does not reach for ``service_fixtures.make_com391_v2_manifest``
    -- that helper reads DEMO-391 fixture files under ``.jswarm/plans/DEMO-391/``
    that were never restored after the public split (a separate, pre-existing
    gap from the one this test guards against). Building the manifest inline
    keeps this test independent of that gap while still exercising the real
    ``jswarm.uat_round_materialize`` package builder end to end.
    """
    import hashlib

    from jswarm.uat_round_materialize import RECOVERY_POLICY

    return {
        "schema_version": "uat-canonical-package@1",
        "ticket": "PORTALSMOKE-1",
        "round_id": "round-1",
        "certified_build_hash": hashlib.sha1(b"portal-boot-smoke").hexdigest(),
        "folder_path": "portal-boot-smoke",
        "app_url": "http://127.0.0.1:8766",
        "login": "no login required for this fixture",
        "observer": {"available": False, "capture": "N/A", "fallback": "manual walkthrough"},
        "recovery_policy": RECOVERY_POLICY,
        "known_sources_checked": ["portal-boot-smoke.md#known-sources"],
        "journeys": [
            {
                "journey_id": "session-expiry-walkthrough",
                "source": "portal boot smoke fixture",
                "scenario_id": "UAT-SMOKE-1",
                "gwt_sha256": hashlib.sha256(b"given-when-then-portal-smoke").hexdigest(),
                "uat_test_anchor": "session expiry walkthrough",
                "actions": [
                    "Sign in.",
                    "Wait until the advertised session lifetime is about to elapse.",
                    "Confirm the session is still valid.",
                ],
                "outcomes": [
                    {
                        "atom_id": "ATOM-1",
                        "expected": "The session remains valid for the full advertised lifetime.",
                        "fail_if": ["The session is rejected before the advertised lifetime elapses."],
                    },
                ],
                "atom_ids": ["ATOM-1"],
                "known_items": [],
                "requirement_ref": "DEMO-PORTAL-1 session expiry fix",
            }
        ],
    }


def test_issued_uat_round_content_is_served_over_the_same_route_a_human_opens(built_dist, tmp_path):
    """Issue a real round and confirm its content is reachable at /uat/?round=<id>.

    The static shell served at /uat/ is the same for every round (the round
    id is a client-side query param that the bundled JS uses to call the API
    below) -- so this proves both halves the rehearsal found broken: the page
    itself loads (no 404), and the exact round data the page's own bundle
    fetches for that id is real, correct content, not empty.
    """
    from jswarm.portal.tests.service_fixtures import LiveServer, make_active_uat_round

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        manifest = _minimal_v1_round_manifest()
        fixture = make_active_uat_round(server.approved_root, manifest=manifest)
        round_id = server.register_active_round(fixture)

        with urllib.request.urlopen(f"{server.client.base}/uat/?round={round_id}", timeout=10) as resp:
            assert resp.status == 200, "GET /uat/?round=<id> must not 404 once a round has been issued"

        status, listing = server.client.get("/api/uat-rounds")
        assert status == 200
        assert listing["rounds"][0]["round_review_id"] == round_id

        status, detail = server.client.get(f"/api/uat-rounds/{round_id}")
        assert status == 200
        detail_text = str(detail)
        assert "session-expiry-walkthrough" in detail_text, "the issued round's real journey content is not present in what the page fetches"
        assert "ATOM-1" in detail_text, "the issued round's real outcome content is not present in what the page fetches"
    finally:
        server.close()


def test_issued_uat_round_with_no_application_url_is_still_served_with_real_content(built_dist, tmp_path):
    """Regression test for the public-split B (portal) UAT defect: a round whose
    ``app_url`` is the literal string ``"N/A"`` -- the documented escape hatch
    ``jswarm/uat_round_materialize.py::_validate_app_url`` accepts for a work
    item with no running application (a pure library or CLI-only change) --
    must still be a real, walkable round once issued.

    Before the fix, this exact package materialized and served correctly
    (the backend has always accepted "N/A"), but the portal's own frontend
    parser (``portal/src/lib/uat-rounds.ts::safeAppUrl``) called ``new
    URL("N/A")``, which throws, and the browser showed a generic "Canonical
    source is invalid" banner instead of the round -- a defect only visible
    by actually opening the round in a browser (see
    ``portal/tests/uat-rounds.test.mjs`` for a direct unit test of the fixed
    parser against this exact shape of data, which is what actually guards
    against a regression in the parsing behavior this test's HTTP layer
    cannot exercise on its own). This test guards the server side of the
    same contract: the round must still issue and serve real content, not a
    404 or an empty stub, when app_url is "N/A".
    """
    from jswarm.portal.tests.service_fixtures import LiveServer, make_active_uat_round

    server = LiveServer(tmp_path, dist_dir=built_dist)
    try:
        manifest = _minimal_v1_round_manifest()
        manifest["app_url"] = "N/A"
        fixture = make_active_uat_round(server.approved_root, manifest=manifest)
        round_id = server.register_active_round(fixture)

        with urllib.request.urlopen(f"{server.client.base}/uat/?round={round_id}", timeout=10) as resp:
            assert resp.status == 200, "GET /uat/?round=<id> must not 404 for an app_url: N/A round"

        status, detail = server.client.get(f"/api/uat-rounds/{round_id}")
        assert status == 200, "the app_url: N/A round must still be issued and fetchable, not rejected"
        detail_text = str(detail)
        assert detail["current_round"]["normalized_package"]["app_url"] == "N/A"
        assert "session-expiry-walkthrough" in detail_text, "the issued round's real journey content is not present in what the page fetches"
        assert "ATOM-1" in detail_text, "the issued round's real outcome content is not present in what the page fetches"
    finally:
        server.close()
