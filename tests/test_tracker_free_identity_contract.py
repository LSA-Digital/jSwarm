"""A slug work item must survive the whole `/jPlan` -> UAT-round-accepted path.

Section 4 of docs/superpowers/specs/2026-09-03-jswarm-public-repo-split-design.md
says a work item is EITHER a tracker key (``PS-14``) OR a plain slug
(``add-csv-export``), local state is identical either way, and nothing
downstream of ``/jPlan`` knows or cares which form was used.

A clean-Mac rehearsal (2026-09-04) found three places where the code broke
that contract for the slug/tracker-free path:

1. ``jswarm/uat_round_materialize.py`` validated ticket ids against a
   tracker-key-ONLY regex, so a slug ``/jPlan`` produces was rejected outright
   (``INVALID: invalid ticket id: 'add-power-fn'``) -- and ``/jPlan`` itself
   refuses a tracker-key-shaped id when no tracker is configured, so on the
   tracker-free path no id existed that both ends would accept.
2. ``jswarm/uat_practical_cutover.py``'s cutover step required a
   ``active_round_sources`` config key that nothing in the documented flow
   ever creates -- fatal on a project's very first round, tracked or not.
3. The ticket lint gate (``jswarm/precompact_reconcile/matrices.py``'s
   ``_TICKET_RE``, used by ``jswarm/precompact_reconcile/lifecycle_audit.py``)
   used the same tracker-key-only regex, so a slug ticket's plan was never
   even located -- the lint silently reported a false-positive "OK" without
   inspecting anything. Once (1) is fixed and the lint gate genuinely
   resolves a slug ticket's plan, a second bug surfaces: ``lifecycle_audit``'s
   ``lint_checkin_evaluation_rows`` unconditionally imports the enterprise-only
   ``joptimize`` package (not shipped in this public core) before checking
   whether there is anything to lint at all, so every ticket whose plan
   resolves -- slug or tracker-key -- would hard-crash the lint gate.

These tests exercise a slug ticket through each of the three fixes so the
tracker-free path is guarded by execution, not by prose.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

sys.path.insert(0, str(REPO_ROOT))

from jswarm.precompact_reconcile.lifecycle_audit import (  # noqa: E402
    _resolve_paths,
    lint_checkin_evaluation_rows,
    lint_slices,
)
from jswarm.uat_practical_cutover import practical_cutover  # noqa: E402
from jswarm.uat_round_materialize import _is_valid_ticket_id  # noqa: E402

SLUG_TICKET = "add-power-fn"
TRACKER_TICKET = "PS-14"


def _build_round_fixture(area: Path, ticket: str) -> tuple[Path, Path]:
    """Write a minimal, schema-valid v2 round request + matching acceptance
    evidence for `ticket` under `area`, mirroring jUAT/SKILL.md's own worked
    example (with real integer step counts, not the doc's placeholder
    strings). Returns (request_path, evidence_path)."""
    given, when, then = ["clause"], ["clause"], ["clause"]
    gwt_payload = json.dumps(
        {"given": given, "when": when, "then": then},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    gwt_sha = hashlib.sha256(gwt_payload).hexdigest()

    request = {
        "schema": "jswarm.test-uat.practical-cutover-request/v1",
        "schema_version": "1.0",
        "ticket": ticket,
        "canonical_manifest": {
            "schema_version": "uat-canonical-package@2",
            "ticket": ticket,
            "round_id": "round-1",
            "certified_build_hash": "a" * 40,
            "folder_path": "N/A",
            "app_url": "http://localhost:8766",
            "login": "no login required",
            "observer": {"available": False, "capture": "N/A", "fallback": "narrator"},
            "recovery_policy": (
                "SETUP_REFRESH_ONLY; POST_ACTION_REFRESH_RELOAD_RETRY_CANNOT_PASS_ORIGINAL_ATOM"
            ),
            "known_sources_checked": ["nothing prior"],
            "journeys": [{
                "journey_id": "journey-1", "title": "Journey 1: Power fn", "source": "manual",
                "uat_test_anchor": "N/A",
                "app_link": {"href": "/", "label": "App"}, "requirement_ref": "N/A",
                "known_items": [], "atom_ids": ["atom-1"],
                "outcomes": [{"atom_id": "atom-1", "expected": "it works", "fail_if": ["it does not"]}],
                "scenarios": [{
                    "scenario_id": "scenario-1", "title": "Power fn works",
                    "gwt": [{"gwt_ref": gwt_sha, "sha256": gwt_sha, "given": given, "when": when, "then": then}],
                }],
                "steps": [{
                    "step_id": "step-1", "ordinal": 1, "name": "Try it",
                    "instruction": "do the thing",
                    "expected_outcome": "it works",
                    "scenario_links": [{"scenario_id": "scenario-1", "gwt_refs": [gwt_sha]}],
                    "assessment_options": ["PASS", "FAIL", "BLOCKED", "NOT_OBSERVED"],
                    "app_link": {"href": "/", "label": "App"},
                }],
            }],
        },
    }
    request_path = area / f"{ticket}.uat-round-request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    request_sha = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    evidence = {
        "schema": "jswarm.test-uat.practical-acceptance-evidence/v1",
        "schema_version": "1.0",
        "round_review_id": f"{ticket}/round-1",
        "verdict": "ACCEPTED",
        "accepted_by": "ticket-boss",
        "accepted_at": "2026-09-04T00:00:00.000000Z",
        "journey_step_counts": {"journey-1": 1},
        "step_counts": {"total": 1},
        "request_sha256": request_sha,
    }
    evidence_path = area / f"{ticket}.uat-acceptance-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    return request_path, evidence_path


# ── 1. jswarm/uat_round_materialize.py: identity contract ────────────────────

@pytest.mark.parametrize("ticket", [SLUG_TICKET, TRACKER_TICKET])
def test_is_valid_ticket_id_accepts_both_forms(ticket):
    assert _is_valid_ticket_id(ticket)


@pytest.mark.parametrize("ticket", ["Add Power Fn", "PS_14", "-leading", ""])
def test_is_valid_ticket_id_still_rejects_what_is_neither(ticket):
    assert not _is_valid_ticket_id(ticket)


def test_uat_round_materialize_create_accepts_a_slug_ticket_from_the_cli(tmp_path):
    """The exact command `/jPlan` step-5-assemble-plan.md documents, run from
    an adopted-project stand-in, with the slug id jPlan itself would produce
    on the tracker-free path."""
    plans_root = tmp_path / ".jswarm" / "plans"
    plans_root.mkdir(parents=True)
    result = subprocess.run(
        [
            str(VENV_PYTHON), str(REPO_ROOT / "jswarm" / "uat_round_materialize.py"), "create",
            "--ticket", SLUG_TICKET,
            "--plans-root", str(plans_root),
            "--rules", str(REPO_ROOT / "skills" / "jTest" / "UAT_RULES.json"),
            "--patterns", str(REPO_ROOT / "skills" / "jTest" / "UAT_ROUND_PATTERNS.json"),
            "--pattern", "default",
        ],
        cwd=str(tmp_path),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ticket"] == SLUG_TICKET


# ── 2. jswarm/uat_practical_cutover.py: active_round_sources defaults ────────

def test_practical_cutover_defaults_missing_active_round_sources(tmp_path):
    """A config.json with no `active_round_sources` key at all -- exactly what
    the rendered decision-review config template produces, since nothing in
    the documented /jUAT flow ever adds the key -- must not be treated as a
    fatal setup error on a project's first-ever round."""
    request_path, evidence_path = _build_round_fixture(tmp_path, SLUG_TICKET)
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    result = practical_cutover({
        "authorized_by": "ticket-boss", "note": "test cutover",
        "request_path": request_path, "acceptance_evidence_path": evidence_path,
        "config_path": config_path, "archive_dir": tmp_path / "history" / "20260904T000000Z",
        "current_round_path": tmp_path / "current-round.md",
        "feedback_path": tmp_path / "feedback.md",
        "handoff_path": tmp_path / "handoff.json",
        "receipt_path": tmp_path / "receipt.json",
        "ledger_path": tmp_path / "ledger.jsonl",
        "service_pid_path": tmp_path / "service.pid",
        "round_review_id": f"{SLUG_TICKET}/round-1",
    })
    assert result["status"] == "cutover-complete"

    new_config = json.loads(config_path.read_text(encoding="utf-8"))
    sources = new_config["active_round_sources"]
    assert len(sources) == 1
    assert sources[0]["round_review_id"] == f"{SLUG_TICKET}/round-1"
    assert sources[0]["ticket"] == SLUG_TICKET


def test_practical_cutover_still_rejects_a_corrupt_non_list_registration(tmp_path):
    request_path, evidence_path = _build_round_fixture(tmp_path, SLUG_TICKET)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"active_round_sources": "not-a-list"}), encoding="utf-8")

    with pytest.raises(ValueError, match="active_round_sources"):
        practical_cutover({
            "authorized_by": "ticket-boss", "note": "test cutover",
            "request_path": request_path, "acceptance_evidence_path": evidence_path,
            "config_path": config_path, "archive_dir": tmp_path / "history" / "20260904T000000Z",
            "current_round_path": tmp_path / "current-round.md",
            "feedback_path": tmp_path / "feedback.md",
            "handoff_path": tmp_path / "handoff.json",
            "receipt_path": tmp_path / "receipt.json",
            "ledger_path": tmp_path / "ledger.jsonl",
            "service_pid_path": tmp_path / "service.pid",
            "round_review_id": f"{SLUG_TICKET}/round-1",
        })


# ── 3. jswarm/precompact_reconcile/lifecycle_audit.py: the ticket lint gate ──

def test_lint_gate_resolves_a_slug_tickets_real_plan(tmp_path):
    """Before the fix, `_resolve_paths` rejected any non-tracker-key id
    outright, so a slug ticket's lint always false-passed without ever
    looking at the plan. It must now genuinely find the plan on disk."""
    plans_dir = tmp_path / ".jswarm" / "plans"
    plans_dir.mkdir(parents=True)
    plan_path = plans_dir / f"{SLUG_TICKET}.plan.power-fn.md"
    plan_path.write_text("# Ticket: add-power-fn\n\nSome plan content.\n", encoding="utf-8")

    resolved_plan_path, ticket_dir, reason = _resolve_paths(SLUG_TICKET, tmp_path)
    assert resolved_plan_path == plan_path
    assert ticket_dir == plans_dir / SLUG_TICKET
    assert reason is None


@pytest.mark.parametrize("ticket", [SLUG_TICKET, TRACKER_TICKET])
def test_lint_gate_does_not_crash_on_a_ticket_with_a_resolved_plan(tmp_path, ticket):
    """Once a slug ticket's plan genuinely resolves, `lint_slices` reaches
    `lint_checkin_evaluation_rows`, which used to import the enterprise-only
    `joptimize` package unconditionally -- crashing this public-core repo's
    lint gate for every ticket with a plan, slug or tracker-key alike."""
    plans_dir = tmp_path / ".jswarm" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / f"{ticket}.plan.power-fn.md").write_text(
        "# Ticket\n\nSome plan content.\n", encoding="utf-8"
    )

    result = lint_slices(ticket, repo_root=tmp_path)
    assert result.ok, result.problems


def test_lint_checkin_evaluation_rows_is_fail_open_without_joptimize(tmp_path):
    # This public core never ships `joptimize`; the function must treat that
    # exactly like ledger absence (both free), not crash.
    assert lint_checkin_evaluation_rows(SLUG_TICKET, project_root=tmp_path) == []
