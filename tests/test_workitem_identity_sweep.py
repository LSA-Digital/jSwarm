"""Regression coverage for the remaining tracker-key-only-regex instances found
by the same sweep that produced tests/test_workitem_identity_boundary.py's
class guard: jswarm/update_ticket/promotions.py, jswarm/update_plan/cli.py,
jswarm/precompact_reconcile/matrices.py's `_ticket_from_name`, and
jswarm/uat-scenarios/scaffold_uat_tests.py. Each used to resolve/validate a
work item id with a private ``[A-Z][A-Z0-9_]+-\\d+``-shaped regex instead of
delegating to ``jswarm.workitem.identity``, silently refusing a slug work
item wherever it was reached. See docs/superpowers/specs/2026-09-03-jswarm-
public-repo-split-design.md section 4.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SLUG_TICKET = "add-multiply"
TRACKER_TICKET = "PS-14"


def _write_plan(plans_dir: Path, ticket: str) -> Path:
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / f"{ticket}.plan.description.md"
    plan_path.write_text("# plan\n", encoding="utf-8")
    return plan_path


# --- jswarm/update_ticket/promotions.py::_resolve_plan ----------------------

@pytest.mark.parametrize("ticket", [SLUG_TICKET, TRACKER_TICKET])
def test_promotions_resolve_plan_accepts_both_identity_forms(tmp_path, ticket):
    from jswarm.update_ticket.promotions import _resolve_plan

    plan_path = _write_plan(tmp_path / ".jswarm" / "plans", ticket)
    resolved, reason = _resolve_plan(tmp_path, ticket)
    assert reason is None, reason
    assert resolved == plan_path


def test_promotions_resolve_plan_still_rejects_garbage():
    from jswarm.update_ticket.promotions import _resolve_plan

    resolved, reason = _resolve_plan(Path("/nonexistent"), "Not A Valid Id!!")
    assert resolved is None
    assert reason == "invalid-ticket-key"


# --- jswarm/update_plan/cli.py::_resolve_plan --------------------------------

@pytest.mark.parametrize("ticket", [SLUG_TICKET, TRACKER_TICKET])
def test_update_plan_cli_resolve_plan_accepts_both_identity_forms(tmp_path, ticket):
    from jswarm.update_plan.cli import _resolve_plan

    plan_path = _write_plan(tmp_path / ".jswarm" / "plans", ticket)
    args = argparse.Namespace(plan=None, ticket=ticket, repo_root=str(tmp_path))
    resolved, display_key, reason = _resolve_plan(args)
    assert reason is None, reason
    assert resolved == plan_path
    assert display_key == ticket


# --- jswarm/precompact_reconcile/matrices.py::_ticket_from_name -------------

@pytest.mark.parametrize(
    "filename,expected",
    [
        (f"{SLUG_TICKET}.plan.multiply-function.md", SLUG_TICKET),
        (f"{TRACKER_TICKET}.plan.multiply-function.md", TRACKER_TICKET),
        ("not-a-plan-file.md", None),
    ],
)
def test_ticket_from_name_accepts_both_identity_forms(filename, expected):
    from jswarm.precompact_reconcile.matrices import _ticket_from_name

    assert _ticket_from_name(filename) == expected


# --- jswarm/uat-scenarios/scaffold_uat_tests.py::TICKET_KEY_RE --------------
# jswarm/uat-scenarios/ has a hyphen and is not an importable package; load the
# module by file path, exactly as its own docstring says test callers do.

def _load_scaffold_module():
    script = REPO_ROOT / "jswarm" / "uat-scenarios" / "scaffold_uat_tests.py"
    scenarios_dir = str(script.parent)
    if scenarios_dir not in sys.path:
        sys.path.insert(0, scenarios_dir)
    spec = importlib.util.spec_from_file_location("scaffold_uat_tests", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("ticket", [SLUG_TICKET, TRACKER_TICKET])
def test_scaffold_ticket_key_re_accepts_both_identity_forms(ticket):
    module = _load_scaffold_module()
    assert module.TICKET_KEY_RE.match(ticket)


def test_scaffold_ticket_key_re_still_rejects_an_unsafe_value():
    module = _load_scaffold_module()
    assert not module.TICKET_KEY_RE.match("../../etc/passwd")
