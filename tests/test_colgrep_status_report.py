"""WORK-241 AC-5 RED tests for the ColGREP status-report generator.

These tests freeze the read-only status-first contract from
``.jswarm/plans/WORK-241/WORK-241.specs.status-report.md``. They are expected to
fail in RED while ``jswarm.colgrep_status_report`` contains only importable API
stubs, and turn GREEN only when the next implementation phase builds the real
report logic.
"""

from __future__ import annotations

from typing import Any, Callable

from jswarm.colgrep_status_report import (
    _LONG_RUNNING_LAUNCHD,
    Probes,
    _parse_launchd_loaded,
    _parse_rebuild_target,
    _read_active_ops_jobs,
    _resolve_raw_dir,
    _scan_ps_lines_for_rebuilds,
    build_report,
    render_markdown,
)

FIXED_NOW = 1_783_356_000.0
CODE_BACKEND = "http://localhost:3280"
CONTENT_BACKEND = "http://localhost:3281"
DEFAULT_INDICES = ["common", "common-wt-has-520-overlay", "common-full-g000001-deadbeef"]
DEFAULT_STATS = {
    "common": {"num_documents": 100, "num_embeddings": 1_000, "exists": True},
    "common-wt-has-520-overlay": {"num_documents": 10, "num_embeddings": 100, "exists": True},
    "common-full-g000001-deadbeef": {"num_documents": 3, "num_embeddings": 30, "exists": True},
}
DEFAULT_SIZES = {
    "common": 1_073_741_824,
    "common-wt-has-520-overlay": 134_217_728,
    "common-full-g000001-deadbeef": 67_108_864,
}
DEFAULT_MTIMES = {
    "common": FIXED_NOW - 3_600,
    "common-wt-has-520-overlay": FIXED_NOW - 7_200,
    "common-full-g000001-deadbeef": FIXED_NOW - 86_400,
}


def _fake_probes(**overrides: Callable[..., Any]) -> Probes:
    """Return a fully populated probe seam; individual tests override one field."""

    def backend_up(base_url: str) -> dict[str, Any]:
        if base_url == CODE_BACKEND or base_url.endswith(":3280"):
            return {"up": True, "detail": "3 indices served from :3280"}
        if base_url == CONTENT_BACKEND or base_url.endswith(":3281"):
            return {"up": True, "detail": "2 content indices served from :3281"}
        return {"up": False, "detail": f"unexpected backend {base_url}"}

    def list_indices(_base_url: str) -> list[str]:
        return list(DEFAULT_INDICES)

    def index_stats(index_name: str) -> dict[str, Any]:
        return dict(DEFAULT_STATS[index_name])

    def probe_queryable(index_name: str) -> dict[str, Any]:
        return {"queryability_hit": True, "reason": f"{index_name} query returned a hit"}

    def scan_active_rebuilds() -> list[dict[str, Any]]:
        return []

    def index_dir_size(index_name: str) -> int | None:
        return DEFAULT_SIZES.get(index_name)

    def index_last_updated(index_name: str) -> float | None:
        return DEFAULT_MTIMES.get(index_name)

    def launchd_state() -> dict[str, dict[str, Any]]:
        return {
            "overlay-fleet-supervisor": {"loaded": True, "pid": 4242},
            "watcher": {"loaded": False, "pid": None},
            "health-check": {"loaded": True, "pid": 5252},
        }

    def memguard_available() -> bool:
        return True

    def eta_estimate(index_name: str) -> dict[str, Any] | None:
        if index_name == "common":
            return {"eta": "12m", "est_final_size": "1.5 GiB", "basis": "fake docs-so-far"}
        return None

    defaults: dict[str, Callable[..., Any]] = {
        "backend_up": backend_up,
        "list_indices": list_indices,
        "index_stats": index_stats,
        "probe_queryable": probe_queryable,
        "scan_active_rebuilds": scan_active_rebuilds,
        "index_dir_size": index_dir_size,
        "index_last_updated": index_last_updated,
        "launchd_state": launchd_state,
        "memguard_available": memguard_available,
        "eta_estimate": eta_estimate,
    }
    defaults.update(overrides)
    return Probes(**defaults)


def _row_by_index(report: dict[str, Any], index_name: str) -> dict[str, Any]:
    return {row["index"]: row for row in report["indices"]}[index_name]


def test_report_leads_with_active_rebuild_answer_and_active_jobs_before_recommendations():
    active_job = {
        "pid": 111,
        "repo": "/workspace/common",
        "elapsed_s": 3_720,
        "log": "/workspace/colgrep-idx/log/common-base-rebuild-COM241.log",
    }
    report = build_report(
        _fake_probes(scan_active_rebuilds=lambda: [active_job]),
        now=FIXED_NOW,
    )

    markdown = render_markdown(report)

    lead_pos = markdown.index("Rebuild running: YES")
    jobs_pos = markdown.index("Active jobs")
    advisory_pos = markdown.index("Advisories")
    action_pos = markdown.index("Recommended action sequence")
    assert lead_pos < jobs_pos < advisory_pos < action_pos
    assert "PID 111" in markdown
    assert active_job["log"] in markdown

    no_rebuild_markdown = render_markdown(build_report(_fake_probes(), now=FIXED_NOW))
    assert "Rebuild running: NO" in no_rebuild_markdown


def test_all_indices_table_carries_health_lastupdated_size_eta_estfinal_per_row():
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [
                {
                    "pid": 222,
                    "repo": "/workspace/common",
                    "elapsed_s": 600,
                    "log": "/workspace/colgrep-idx/log/common-base-rebuild-COM241.log",
                }
            ]
        ),
        now=FIXED_NOW,
    )

    assert {row["index"] for row in report["indices"]} == set(DEFAULT_INDICES)
    for row in report["indices"]:
        assert row["health"]
        assert row["last_updated_human"] not in (None, "", "—")
        assert row["size_human"] not in (None, "", "—")

    rebuilding_base = _row_by_index(report, "common")
    assert rebuilding_base["health"] == "⏳ building"
    assert rebuilding_base["rebuild_elapsed"] != "—"
    assert rebuilding_base["eta"] != "—"
    assert rebuilding_base["est_final_size"] != "—"

    markdown = render_markdown(report)
    for expected in ("common", "Last updated", "Est. final size", rebuilding_base["eta"]):
        assert expected in markdown


def test_listed_but_not_queryable_base_renders_warning_not_ok():
    report = build_report(
        _fake_probes(
            probe_queryable=lambda index_name: {
                "queryability_hit": False,
                "reason": "SQLite error: database disk image is malformed",
            }
        ),
        now=FIXED_NOW,
    )

    base = _row_by_index(report, "common")
    assert base["health"] == "⚠️ listed-not-queryable"
    assert "✅" not in base["health"]

    markdown = render_markdown(report)
    assert "⚠️ listed-not-queryable" in markdown
    assert "common | base | ✅" not in markdown


def test_all_infra_components_reported_and_ordered_action_sequence_reaches_goal():
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            probe_queryable=lambda index_name: {
                "queryability_hit": index_name != "common",
                "reason": "SQLite error: database disk image is malformed" if index_name == "common" else "hit",
            },
            launchd_state=lambda: {
                "overlay-fleet-supervisor": {"loaded": False, "pid": None},
                "watcher": {"loaded": True, "pid": 3131},
                "health-check": {"loaded": True, "pid": 4141},
            },
        ),
        now=FIXED_NOW,
    )

    components = [row["component"].lower() for row in report["infrastructure"]]
    for required in (
        ":3280",
        ":3281",
        "container",
        "memory-guard",
        "fleet-supervisor",
        "watcher",
        "health-check",
        "active-rebuild",
    ):
        assert any(required in component for component in components), required

    assert [step["action"] for step in report["action_sequence"]] == [
        "quiesced-reload",
        "verify-queryable",
        "restart-fleet-supervisor",
        "verify-no-reflap",
    ]
    for step in report["action_sequence"]:
        assert step["step"] >= 1
        assert step["command"]
        assert step["verify"]
        assert isinstance(step["operator_gate"], bool)


def test_infrastructure_table_rows_present():
    report = build_report(
        _fake_probes(
            launchd_state=lambda: {
                "overlay-fleet-supervisor": {"loaded": True, "pid": 4242},
                "watcher": {"loaded": False, "pid": None},
                "health-check": {"loaded": True, "pid": 5252},
            }
        ),
        now=FIXED_NOW,
    )

    markdown = render_markdown(report)

    for expected in (
        ":3280",
        ":3281",
        "memory-guard",
        "overlay-fleet-supervisor",
        "watcher",
        "health-check",
        "4242",
        "not loaded",
        "5252",
    ):
        assert expected in markdown


def test_probe_failure_is_fail_open_not_crash():
    def raising_size(_index_name: str) -> int:
        raise RuntimeError("du unavailable")

    report = build_report(_fake_probes(index_dir_size=raising_size), now=FIXED_NOW)

    assert report["schema_version"] == "colgrep.status-report.v1"
    assert any("n/a" in row["size_human"].lower() for row in report["indices"])

    markdown = render_markdown(report)
    assert "ColGREP" in markdown
    assert "n/a" in markdown.lower()


def test_json_and_md_consistency():
    report = build_report(_fake_probes(), now=FIXED_NOW)
    markdown = render_markdown(report)

    assert report["generated_at"] in markdown
    assert report["summary"]["total_size_human"] in markdown
    for advisory in report["summary"]["advisories"]:
        assert advisory in markdown
    for row in report["infrastructure"]:
        assert row["component"] in markdown
        assert row["state"] in markdown
    for row in report["indices"]:
        assert row["index"] in markdown
        assert row["type"] in markdown
        assert row["health"] in markdown
        assert str(row["docs"]) in markdown
        assert row["size_human"] in markdown
        assert row["last_updated_human"] in markdown


def _launchd_state(*, supervisor: bool = True, watcher: bool = True, health_check: bool = True) -> dict[str, dict[str, Any]]:
    return {
        "overlay-fleet-supervisor": {"loaded": supervisor, "pid": 4242 if supervisor else None},
        "watcher": {"loaded": watcher, "pid": 3131 if watcher else None},
        "health-check": {"loaded": health_check, "pid": 5252 if health_check else None},
    }


def _actions(report: dict[str, Any]) -> list[str]:
    return [step["action"] for step in report["action_sequence"]]


def _action_text(step: dict[str, Any]) -> str:
    return " ".join(str(value) for value in step.values())


def test_supervisor_down_with_healthy_bases_recommends_restart_not_steady_state():
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=False, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )

    actions = _actions(report)
    assert "restart-fleet-supervisor" in actions
    assert actions[-1] == "verify-no-reflap"
    assert "steady state (healthy + served + supervised)" not in render_markdown(report)


def test_watcher_down_recommends_restart():
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=False, health_check=True),
        ),
        now=FIXED_NOW,
    )

    actions = _actions(report)
    assert "restart-watcher" in actions
    assert "steady state (healthy + served + supervised)" not in render_markdown(report)


def test_health_check_down_recommends_restart():
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=False),
        ),
        now=FIXED_NOW,
    )

    actions = _actions(report)
    assert "restart-health-check" in actions
    assert "steady state (healthy + served + supervised)" not in render_markdown(report)


def test_queryability_probe_exception_renders_unknown_not_healthy_and_blocks_steady():
    def raising_probe(_index_name: str) -> dict[str, Any]:
        raise RuntimeError("queryability probe timed out")

    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            probe_queryable=raising_probe,
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )

    base_row = _row_by_index(report, "common")
    assert base_row["health"] == "⚠️ queryability-unknown"
    assert "✅" not in base_row["health"]
    assert "steady state (healthy + served + supervised)" not in render_markdown(report)
    assert "quiesced-reload" not in _actions(report)
    assert any(
        not step.get("operator_gate") and ("queryability" in _action_text(step) or "diagnose" in _action_text(step))
        for step in report["action_sequence"]
    )


def test_list_indices_failure_renders_na_and_blocks_steady():
    def raising_list(_base_url: str) -> list[str]:
        raise RuntimeError("index listing failed: backend timeout")

    report = build_report(
        _fake_probes(
            list_indices=raising_list,
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )
    markdown = render_markdown(report)

    assert "steady state (healthy + served + supervised)" not in markdown
    assert "index listing failed" in markdown.lower()
    assert "n/a" in markdown.lower()
    actions = _actions(report)
    assert "quiesced-reload" not in actions
    assert "memory-guarded-rebuild" not in actions
    assert any(
        not step.get("operator_gate") and ("re-probe" in _action_text(step) or "listing" in _action_text(step))
        for step in report["action_sequence"]
    )


def test_active_rebuild_scan_exception_is_unknown_and_fails_closed_no_mutation():
    def raising_scan() -> list[dict[str, Any]]:
        raise RuntimeError("ps scan unavailable")

    report = build_report(
        _fake_probes(
            scan_active_rebuilds=raising_scan,
            probe_queryable=lambda index_name: {"queryability_hit": index_name != "common", "reason": "malformed base"},
            launchd_state=lambda: _launchd_state(supervisor=False, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )
    markdown = render_markdown(report)

    assert "Rebuild running: UNKNOWN" in markdown
    actions = _actions(report)
    assert "quiesced-reload" not in actions
    assert "restart-fleet-supervisor" not in actions
    assert "memory-guarded-rebuild" not in actions
    assert any("resolve rebuild-scan uncertainty" in _action_text(step) for step in report["action_sequence"])


def test_resolve_raw_dir_excludes_sibling_families():
    resolved = _resolve_raw_dir(
        "common",
        [
            "common-25f88f7a",
            "common-b2a9026b",
            "common-wt-has-520-abc-overlay",
            "common-full-g000001-deadbeef",
            "common-wt-has-497-4c1636ec",
        ],
    )

    assert resolved == ["common-25f88f7a", "common-b2a9026b"]


def test_parse_rebuild_target_handles_memguard_wrapper():
    assert (
        _parse_rebuild_target(
            "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python /x/colgrep_mem_guard.py --cap-gb 32 -- "
            "colgrep init -y --force-cpu /workspace/hai-sim-engine"
        )["repo"]
        == "/workspace/hai-sim-engine"
    )
    assert (
        _parse_rebuild_target("colgrep init -y --force-cpu /workspace/foo")["repo"]
        == "/workspace/foo"
    )


def test_parse_launchd_loaded_requires_exact_label():
    assert _parse_launchd_loaded("266\t0\tcom.colgrep.health-check-disabled", "health-check")["loaded"] is False
    assert _parse_launchd_loaded("267\t0\tcom.colgrep.watcher", "watcher") == {"loaded": True, "pid": 267}


def test_parse_rebuild_target_ignores_flags_after_repo():
    """R1: a flag AFTER the repo (with its own value) must not steal the repo slot."""
    assert (
        _parse_rebuild_target(
            "colgrep init -y --force-cpu /workspace/foo --index common"
        )["repo"]
        == "/workspace/foo"
    )
    assert (
        _parse_rebuild_target(
            "/x/colgrep_mem_guard.sh --cap-gb 32 -- colgrep init /workspace/foo --flag value"
        )["repo"]
        == "/workspace/foo"
    )
    # Existing passing shapes must remain green: bare and mem-guard-wrapped
    # `--force-cpu <repo>` still resolve to the repo, not the interpreter.
    assert (
        _parse_rebuild_target("colgrep init -y --force-cpu /workspace/foo")["repo"]
        == "/workspace/foo"
    )
    assert (
        _parse_rebuild_target(
            "${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python /x/colgrep_mem_guard.py --cap-gb 32 -- "
            "colgrep init -y --force-cpu /workspace/hai-sim-engine"
        )["repo"]
        == "/workspace/hai-sim-engine"
    )


def test_parse_launchd_loaded_malformed_output_is_unknown_not_false():
    """R2: malformed/header-only launchctl output is parser uncertainty (None), not a
    false-negative "not loaded" default — an unknown must never block a mutating
    recommendation with a fabricated healthy/absent read."""
    result = _parse_launchd_loaded("PID\tStatus\tLabel", "watcher")
    assert result["loaded"] is None
    assert result["pid"] is None

    # Still-working cases: a valid row with the exact label present -> True + pid.
    assert _parse_launchd_loaded("267\t0\tcom.colgrep.watcher", "watcher") == {"loaded": True, "pid": 267}
    # A valid row whose exact label is absent -> False (proven absent, not unknown).
    assert (
        _parse_launchd_loaded("266\t0\tcom.colgrep.health-check-disabled", "health-check")["loaded"]
        is False
    )


def test_unprobed_generation_not_labeled_queryable():
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )

    generation = _row_by_index(report, "common-full-g000001-deadbeef")
    assert "listed-unprobed" in generation["health"]
    assert "✅ queryable" not in generation["health"]


def test_zero_doc_generation_is_listed_not_missing():
    """R3: a listed generation/orphan-generation row with docs<=0 (or unknown/absent
    stats) must be classified by TYPE first -> listed-unprobed / orphan-listed, never
    the base-style "missing" label that implies an actionable missing base."""

    def zero_doc_stats(index_name: str) -> dict[str, Any]:
        if index_name == "common-full-g000001-deadbeef":
            return {"num_documents": 0, "exists": False}
        return dict(DEFAULT_STATS[index_name])

    report = build_report(
        _fake_probes(
            index_stats=zero_doc_stats,
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )

    generation = _row_by_index(report, "common-full-g000001-deadbeef")
    assert generation["type"] in ("generation", "orphan-generation")
    assert "listed" in generation["health"]
    assert "❌ missing" not in generation["health"]


def test_missing_base_conditional_restart_and_final_verify():
    def missing_common_stats(index_name: str) -> dict[str, Any]:
        if index_name == "common":
            return {"exists": False, "num_documents": 0}
        return dict(DEFAULT_STATS[index_name])

    loaded_report = build_report(
        _fake_probes(
            index_stats=missing_common_stats,
            probe_queryable=lambda index_name: {"queryability_hit": index_name != "common", "reason": "missing base"},
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )
    loaded_actions = _actions(loaded_report)
    assert loaded_actions[-1] == "verify-no-reflap"
    assert "restart-fleet-supervisor" not in loaded_actions

    unloaded_report = build_report(
        _fake_probes(
            index_stats=missing_common_stats,
            probe_queryable=lambda index_name: {"queryability_hit": index_name != "common", "reason": "missing base"},
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=False, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )
    unloaded_actions = _actions(unloaded_report)
    assert "restart-fleet-supervisor" in unloaded_actions
    assert unloaded_actions[-1] == "verify-no-reflap"


# -----------------------------------------------------------------------------
# WORK-241 AC-6 remediation: Bug 2 (rebuild-scan blind to overlay build-overlay
# encodes) + Fix 3 (action_sequence must emit REAL runnable commands).
# -----------------------------------------------------------------------------


def test_scan_ps_lines_for_rebuilds_matches_overlay_build_and_refresh_if_stale():
    """TASK-520 gap: a live `colgrep_worktree.py build-overlay` (or
    `refresh-if-stale`) encode must be visible to the rebuild scan — previously the
    regex only matched native `colgrep init` / mem-guard processes and the status
    report falsely reported "Rebuild running: NO" during a live overlay build."""
    ps_stdout = (
        "  PID ELAPSED COMMAND\n"
        "  4242    05:10 ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python "
        "${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/colgrep-worktree build-overlay WORK-241 "
        "--worktree /workspace/hai-sim-engine\n"
        "  4343    02:00 ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python "
        "${JSWARM_HOME:-$HOME/dev/jswarm}/scripts/colgrep_worktree.py refresh-if-stale "
        "/workspace/hai-sim-engine\n"
        "  9999    00:05 -bash\n"
    )

    jobs = _scan_ps_lines_for_rebuilds(ps_stdout)

    assert len(jobs) == 2
    repos = {job["repo"] for job in jobs}
    assert "/workspace/hai-sim-engine" in repos
    pids = {job["pid"] for job in jobs}
    assert pids == {4242, 4343}

    report = build_report(_fake_probes(scan_active_rebuilds=lambda: jobs), now=FIXED_NOW)
    assert report["active_rebuild"]["running"] is True
    assert any(job["repo"] == "/workspace/hai-sim-engine" for job in report["active_rebuild"]["jobs"])
    markdown = render_markdown(report)
    assert "Rebuild running: YES" in markdown


def test_scan_ps_lines_for_rebuilds_ignores_unrelated_processes():
    ps_stdout = "  PID ELAPSED COMMAND\n  1000    00:01 -bash\n  1001    00:02 vim foo.py\n"
    assert _scan_ps_lines_for_rebuilds(ps_stdout) == []


def test_scan_ps_lines_for_rebuilds_matches_dotted_module_invocation():
    """Fix 4/5 (WORK-241 AC-6 jCritic pass): a live overlay-build/refresh-if-stale
    encode launched via `python -m jswarm.colgrep_worktree ... build-overlay`
    (the dotted-module invocation shape, distinct from the direct-script or shim
    forms already covered) must still be visible to the rebuild scan — a live
    encode must never be read as "not running" just because of how it was
    launched."""
    ps_stdout = (
        "  PID ELAPSED COMMAND\n"
        "  5151    03:00 ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python -m jswarm.colgrep_worktree "
        "build-overlay WORK-241 --worktree /workspace/hai-sim-engine\n"
        "  9999    00:05 -bash\n"
    )

    jobs = _scan_ps_lines_for_rebuilds(ps_stdout)

    assert len(jobs) == 1
    assert jobs[0]["pid"] == 5151
    assert jobs[0]["repo"] == "/workspace/hai-sim-engine"

    report = build_report(_fake_probes(scan_active_rebuilds=lambda: jobs), now=FIXED_NOW)
    assert report["active_rebuild"]["running"] is True
    markdown = render_markdown(report)
    assert "Rebuild running: YES" in markdown


def test_stale_active_ops_tokens_are_non_gating_evidence(tmp_path):
    active_ops = tmp_path / "active-ops"
    active_ops.mkdir()
    token = active_ops / "op-84905-1783561720-9e787990.json"
    token.write_text(
        '{"kind":"overlay-build","pid":84905,"started_at":"2026-07-08T12:00:00Z",'
        '"details":{"worktree":"/workspace/hai-sim-engine/.claude/worktrees/hai-sim-engine-wt-has-520"}}\n',
        encoding="utf-8",
    )

    active_ops_scan = _read_active_ops_jobs(active_ops, now=1_783_612_800.0, pid_live_fn=lambda _pid: False)

    assert active_ops_scan["active"] == []
    assert len(active_ops_scan["stale"]) == 1
    assert active_ops_scan["stale"][0]["pid"] == 84905

    report = build_report(
        _fake_probes(scan_active_rebuilds=lambda: {"jobs": [], "stale_active_ops": active_ops_scan["stale"]}),
        now=FIXED_NOW,
    )
    assert report["active_rebuild"]["running"] is False
    assert report["active_rebuild"]["status"] == "none"
    assert report["active_rebuild"]["jobs"] == []
    assert report["active_rebuild"]["stale_active_ops"][0]["pid"] == 84905
    markdown = render_markdown(report)
    assert "Rebuild running: NO" in markdown
    assert "Rebuild running: YES" not in markdown
    assert "Stale active-op tokens (non-gating)" in markdown


def test_live_active_ops_tokens_still_supplement_active_jobs(tmp_path):
    active_ops = tmp_path / "active-ops"
    active_ops.mkdir()
    (active_ops / "op-4242-1783561720-live.json").write_text(
        '{"kind":"overlay-build","pid":4242,"started_at":"2026-07-08T12:00:00Z",'
        '"details":{"worktree":"/tmp/live-worktree"}}\n',
        encoding="utf-8",
    )

    active_ops_scan = _read_active_ops_jobs(active_ops, now=1_783_612_800.0, pid_live_fn=lambda _pid: True)

    assert len(active_ops_scan["active"]) == 1
    assert active_ops_scan["stale"] == []
    report = build_report(
        _fake_probes(scan_active_rebuilds=lambda: {"jobs": active_ops_scan["active"], "stale_active_ops": []}),
        now=FIXED_NOW,
    )
    assert report["active_rebuild"]["running"] is True
    assert "Rebuild running: YES" in render_markdown(report)


def test_quiesced_reload_uses_real_wrapper_not_noop_lib():
    """WORK-241 AC-6 Bug 1 fix, surfaced through the action_sequence: the quiesced-
    reload command must reference the real executable wrapper
    (next-plaid/bin/nextplaid-quiesce-restart), never the raw library file
    (next-plaid/lib/nextplaid-quiesce.sh), which is a no-op when run directly."""

    def missing_common_stats(index_name: str) -> dict[str, Any]:
        if index_name == "common":
            return {"exists": False, "num_documents": 0}
        return dict(DEFAULT_STATS[index_name])

    report = build_report(
        _fake_probes(
            index_stats=missing_common_stats,
            probe_queryable=lambda index_name: {"queryability_hit": index_name != "common", "reason": "missing base"},
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )

    reload_steps = [step for step in report["action_sequence"] if step["action"] == "quiesced-reload"]
    assert reload_steps, "expected a quiesced-reload step for a missing base"
    for step in reload_steps:
        assert "nextplaid-quiesce-restart" in step["command"]
        assert "nextplaid-quiesce.sh" not in step["command"]


def test_memory_guarded_rebuild_step_flags_missing_parameters():
    """Fix 3: a command that still needs an operator-supplied value (<cap>, <repo>)
    keeps the placeholder but is explicitly flagged, so an agent/operator resolves
    the parameter rather than treating the placeholder text as runnable."""

    def missing_common_stats(index_name: str) -> dict[str, Any]:
        if index_name == "common":
            return {"exists": False, "num_documents": 0}
        return dict(DEFAULT_STATS[index_name])

    report = build_report(
        _fake_probes(
            index_stats=missing_common_stats,
            probe_queryable=lambda index_name: {"queryability_hit": index_name != "common", "reason": "missing base"},
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )

    rebuild_step = next(step for step in report["action_sequence"] if step["action"] == "memory-guarded-rebuild")
    assert rebuild_step.get("blocked_by_missing_parameter") == ["cap", "repo"]


def test_no_action_step_recommends_generation_reaper_for_orphan_cleanup():
    """Fix 3: `generation_reaper reap` is the WRONG tool for a RAM/listed orphan
    pile (it refuses without a worktree-generation manifest and preserves served
    names) — no action step may ever point at it for orphan cleanup."""
    report = build_report(_fake_probes(scan_active_rebuilds=lambda: []), now=FIXED_NOW)
    assert not any("generation_reaper" in step["command"] for step in report["action_sequence"])


def test_orphan_generations_get_guarded_lifecycle_cleanup_action_not_generation_reaper():
    """Fix 3 (WORK-241 AC-6 jCritic finding #3): the orphan action must be a REAL,
    runnable, guarded command — `colgrep_orphan_cleanup.py plan`/`apply --family
    <family>`, never `generation_reaper reap` (wrong tool) and never the old
    `colgrep_index_lifecycle.py cleanup` pairing (a bare unload with no paired
    restart, and not scoped to the observed family). A single dominant family in
    Table B lets `--family` be derived concretely (no placeholder, not blocked)."""
    orphan_indices = ["common", "common-full-g000001-aaaaaaaa", "common-full-g000002-bbbbbbbb"]

    def orphan_stats(index_name: str) -> dict[str, Any]:
        if index_name == "common":
            return {"num_documents": 100, "exists": True}
        return {"num_documents": 3, "exists": True}

    report = build_report(
        _fake_probes(
            list_indices=lambda _base_url: list(orphan_indices),
            index_stats=orphan_stats,
            index_dir_size=lambda _name: 1024,
            index_last_updated=lambda _name: FIXED_NOW - 3600,
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )

    assert report["summary"]["orphans"] == 2
    cleanup_steps = [step for step in report["action_sequence"] if "orphan" in step["action"]]
    assert cleanup_steps, "expected an orphan-cleanup action step when orphan generations are present"
    joined_commands = " ".join(step["command"] for step in cleanup_steps)
    assert "colgrep_orphan_cleanup.py" in joined_commands
    assert "generation_reaper" not in joined_commands
    assert "colgrep_index_lifecycle.py" not in joined_commands
    assert "launchctl unload" not in joined_commands
    assert any("plan" in step["command"] for step in cleanup_steps)
    assert any("apply" in step["command"] for step in cleanup_steps)
    # A single dominant family ("common") is derivable -> concrete, unblocked commands.
    assert "--family common" in joined_commands
    assert not any(step.get("blocked_by_missing_parameter") for step in cleanup_steps)
    apply_step = next(step for step in cleanup_steps if step["action"] == "orphan-cleanup-apply")
    assert "--stop-driver" in apply_step["command"]
    assert "--restart-driver" in apply_step["command"]


def test_orphan_cleanup_multiple_families_is_blocked_not_guessed():
    """Fix 3: when the observed orphan generations span MORE THAN ONE family
    prefix, `--family` cannot be uniquely derived from Table B — the step must be
    flagged `blocked_by_missing_parameter: ["family"]` rather than presenting an
    un-runnable (or worse, silently wrong) guessed family as ready-to-run."""
    orphan_indices = [
        "common",
        "common-full-g000001-aaaaaaaa",
        "common-full-g000002-bbbbbbbb",
        "other-repo",
        "other-repo-full-g000001-cccccccc",
        "other-repo-full-g000002-dddddddd",
    ]

    def orphan_stats(index_name: str) -> dict[str, Any]:
        if index_name in ("common", "other-repo"):
            return {"num_documents": 100, "exists": True}
        return {"num_documents": 3, "exists": True}

    report = build_report(
        _fake_probes(
            list_indices=lambda _base_url: list(orphan_indices),
            index_stats=orphan_stats,
            index_dir_size=lambda _name: 1024,
            index_last_updated=lambda _name: FIXED_NOW - 3600,
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
        ),
        now=FIXED_NOW,
    )

    cleanup_steps = [step for step in report["action_sequence"] if "orphan" in step["action"]]
    assert cleanup_steps
    for step in cleanup_steps:
        assert step.get("blocked_by_missing_parameter") == ["family"]
    joined_commands = " ".join(step["command"] for step in cleanup_steps)
    assert "colgrep_orphan_cleanup.py" in joined_commands


def test_no_action_step_ever_emits_raw_launchctl_or_noop_quiesce_lib():
    """Fix 2/6 (WORK-241 AC-6 jCritic pass): EVERY mutating action_sequence step
    must route through a guarded, agent-runnable wrapper — never raw `launchctl
    load`/`launchctl unload` prose (no paired stop-without-restart either) and
    never the raw no-op `next-plaid/lib/nextplaid-quiesce.sh` library file.
    Exercises every branch that emits a mutating step: fleet-supervisor/watcher/
    health-check down, a missing base (quiesced-reload + conditional restart),
    and an orphan pile."""

    def missing_common_stats(index_name: str) -> dict[str, Any]:
        if index_name == "common":
            return {"exists": False, "num_documents": 0}
        return dict(DEFAULT_STATS[index_name])

    def orphan_stats(index_name: str) -> dict[str, Any]:
        if index_name == "common":
            return {"num_documents": 100, "exists": True}
        return {"num_documents": 3, "exists": True}

    scenarios = [
        _fake_probes(scan_active_rebuilds=lambda: [], launchd_state=lambda: _launchd_state(supervisor=False)),
        _fake_probes(scan_active_rebuilds=lambda: [], launchd_state=lambda: _launchd_state(watcher=False)),
        _fake_probes(scan_active_rebuilds=lambda: [], launchd_state=lambda: _launchd_state(health_check=False)),
        _fake_probes(
            index_stats=missing_common_stats,
            probe_queryable=lambda index_name: {"queryability_hit": index_name != "common", "reason": "missing base"},
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: _launchd_state(supervisor=False),
        ),
        _fake_probes(
            list_indices=lambda _base_url: ["common", "common-full-g000001-aaaaaaaa", "common-full-g000002-bbbbbbbb"],
            index_stats=orphan_stats,
            index_dir_size=lambda _name: 1024,
            index_last_updated=lambda _name: FIXED_NOW - 3600,
            scan_active_rebuilds=lambda: [],
        ),
    ]

    for probes in scenarios:
        report = build_report(probes, now=FIXED_NOW)
        for step in report["action_sequence"]:
            command = step["command"]
            assert "launchctl load" not in command, step
            assert "launchctl unload" not in command, step
            assert "nextplaid-quiesce.sh" not in command, step


def _infra_row(report: dict[str, Any], needle: str) -> dict[str, Any]:
    return next(row for row in report["infrastructure"] if needle in row["component"].lower())


def test_supervisor_loaded_no_live_pid_recommends_restart_not_steady_state():
    """WORK-241 AC-6 jCritic delta finding (residual of finding #6, reopened at
    the report layer): overlay-fleet-supervisor is LONG-RUNNING — `loaded`
    alone is not "up" for it. A `{"loaded": True, "pid": None}` supervisor is
    crash-looping and must recommend a restart, never render as a healthy
    🟢 loaded row, and never claim steady state."""
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: {
                "overlay-fleet-supervisor": {"loaded": True, "pid": None},
                "watcher": {"loaded": True, "pid": 3131},
                "health-check": {"loaded": True, "pid": 5252},
            },
        ),
        now=FIXED_NOW,
    )

    actions = _actions(report)
    assert "restart-fleet-supervisor" in actions
    markdown = render_markdown(report)
    assert "steady state (healthy + served + supervised)" not in markdown

    supervisor_row = _infra_row(report, "fleet-supervisor")
    assert "🟢 loaded" != supervisor_row["state"]
    assert "crash-loop" in supervisor_row["state"].lower()


def test_watcher_loaded_no_live_pid_recommends_restart():
    """Same crash-loop residual as the supervisor case, but for `watcher` —
    the other LONG-RUNNING component."""
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: {
                "overlay-fleet-supervisor": {"loaded": True, "pid": 4242},
                "watcher": {"loaded": True, "pid": None},
                "health-check": {"loaded": True, "pid": 5252},
            },
        ),
        now=FIXED_NOW,
    )

    actions = _actions(report)
    assert "restart-watcher" in actions
    markdown = render_markdown(report)
    assert "steady state (healthy + served + supervised)" not in markdown

    watcher_row = _infra_row(report, "watcher")
    assert "🟢 loaded" != watcher_row["state"]
    assert "crash-loop" in watcher_row["state"].lower()


def test_health_check_loaded_no_live_pid_is_healthy_periodic_steady_state():
    """health-check is PERIODIC, not LONG-RUNNING — `loaded` without a live
    pid between runs is expected and must NOT be treated as a crash-loop or
    block steady state, unlike overlay-fleet-supervisor/watcher above."""
    report = build_report(
        _fake_probes(
            scan_active_rebuilds=lambda: [],
            launchd_state=lambda: {
                "overlay-fleet-supervisor": {"loaded": True, "pid": 4242},
                "watcher": {"loaded": True, "pid": 3131},
                "health-check": {"loaded": True, "pid": None},
            },
        ),
        now=FIXED_NOW,
    )

    actions = _actions(report)
    assert "restart-health-check" not in actions
    assert "restart-fleet-supervisor" not in actions
    assert "restart-watcher" not in actions
    markdown = render_markdown(report)
    assert "steady state (healthy + served + supervised)" in markdown

    health_check_row = _infra_row(report, "health-check")
    assert "crash-loop" not in health_check_row["state"].lower()


def test_long_running_launchd_set_matches_launchd_control_module():
    """Drift guard: this module's LONG-RUNNING set must stay identical to
    `colgrep_launchd_control.LONG_RUNNING_COMPONENTS`, which already treats
    overlay-fleet-supervisor/watcher as requiring a live pid to be "up". This
    import is TEST-ONLY (colgrep_launchd_control imports
    `_parse_launchd_loaded` from colgrep_status_report, so a module-level
    import back here would be circular)."""
    from jswarm.platform import colgrep_launchd_control

    assert _LONG_RUNNING_LAUNCHD == colgrep_launchd_control.LONG_RUNNING_COMPONENTS


# -----------------------------------------------------------------------------
# WORK-241 Phase B RED: status must converge with fleet-plan worktree blockers.
# -----------------------------------------------------------------------------


def _with_fleet_plan(probes: Probes, snapshot: dict[str, Any] | Callable[[], dict[str, Any]]) -> Probes:
    """Attach the Phase-B optional probe dynamically so behavior tests fail on
    missing consumption (not on construction) until Probes grows the real field."""
    fleet_plan_fn = snapshot if callable(snapshot) else (lambda: snapshot)
    setattr(probes, "fleet_plan", fleet_plan_fn)
    return probes


def _fleet_plan_snapshot(*, path: str, action: str, next_action: str, actual_blocker: str) -> dict[str, Any]:
    """Match the REAL `colgrep_overlay_fleet_supervisor.py plan` worktree-record
    shape (verified live): keys are exactly `action, build_observation,
    builder_live, dependency_assessment, eta_status, lifecycle_state,
    manifest_state, next_action, path, queue, reason, reason_code, spawned` —
    there is NO `worktree_key` field; records key on `path`, and the consumer
    derives the component key as that path's basename."""
    return {
        "schema_version": "colgrep.fleet-plan.v1",
        "worktrees": [
            {
                "path": path,
                "action": action,
                "next_action": next_action,
                "dependency_assessment": {
                    "dependency": "overlay-fleet-supervisor",
                    "required_state": "running",
                    "observed_state": "not-running",
                    "observed_source": "injected-test-snapshot",
                    "shell_state": "disabled",
                    "daemon_up": False,
                    "actual_blocker": actual_blocker,
                },
            }
        ],
    }


def _find_step(report: dict[str, Any], *, component: str) -> dict[str, Any]:
    return next(step for step in report["action_sequence"] if step.get("component") == component)


def test_probes_fleet_plan_is_optional_default_none_and_absent_callers_emit_no_worktree_actions():
    from dataclasses import fields

    field_defaults = {field.name: field.default for field in fields(Probes)}

    assert "fleet_plan" in field_defaults
    assert field_defaults["fleet_plan"] is None

    report = build_report(_fake_probes(), now=FIXED_NOW)
    assert not any(str(step.get("component", "")).startswith("worktree:") for step in report["action_sequence"])


def test_fleet_plan_supervisor_blocked_worktree_appends_restart_after_infra_before_terminal_verify():
    report = build_report(
        _with_fleet_plan(
            _fake_probes(
                probe_queryable=lambda index_name: {
                    "queryability_hit": index_name != "common",
                    "reason": "SQLite error: database disk image is malformed" if index_name == "common" else "hit",
                },
                scan_active_rebuilds=lambda: [],
                launchd_state=lambda: _launchd_state(supervisor=True, watcher=False, health_check=True),
            ),
            _fleet_plan_snapshot(
                path="/workspace/hai-sim-engine-wt-has-520",
                action="fleet-supervisor-not-running",
                next_action="start-fleet-supervisor",
                actual_blocker="overlay-fleet-supervisor-not-running",
            ),
        ),
        now=FIXED_NOW,
    )

    steps = report["action_sequence"]
    worktree_step = _find_step(report, component="worktree:hai-sim-engine-wt-has-520")
    worktree_idx = steps.index(worktree_step)
    action_positions = {step["action"]: index for index, step in enumerate(steps)}

    assert worktree_step["action"] == "restart-fleet-supervisor"
    assert worktree_step["command"] == (
        ".venv/bin/python jswarm/platform/colgrep_launchd_control.py restart "
        "--component overlay-fleet-supervisor --json"
    )
    assert worktree_step["operator_gate"] is True
    assert worktree_step["verify"]
    assert "daemon_up" in worktree_step["verify"]
    assert "action" in worktree_step["verify"]

    assert action_positions["quiesced-reload"] < worktree_idx
    assert action_positions["verify-queryable"] < worktree_idx
    assert action_positions["restart-watcher"] < worktree_idx
    assert worktree_idx < action_positions["verify-no-reflap"]
    assert action_positions["verify-no-reflap"] == len(steps) - 1


def test_fleet_plan_operator_gated_blockers_emit_advisory_not_supervisor_restart():
    for actual_blocker, next_action in (
        ("lane-disabled", "enable-overlay-lane"),
        ("seed-capability-unavailable", "install-seed-capability"),
    ):
        report = build_report(
            _with_fleet_plan(
                _fake_probes(
                    scan_active_rebuilds=lambda: [],
                    launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
                ),
                _fleet_plan_snapshot(
                    path=f"/workspace/hai-sim-engine-wt-{actual_blocker}",
                    action="blocked",
                    next_action=next_action,
                    actual_blocker=actual_blocker,
                ),
            ),
            now=FIXED_NOW,
        )

        step = _find_step(report, component=f"worktree:hai-sim-engine-wt-{actual_blocker}")
        step_text = _action_text(step)

        assert step["operator_gate"] is True
        assert step["action"] != "restart-fleet-supervisor"
        assert next_action in step_text
        assert actual_blocker in step_text
        assert "colgrep_launchd_control.py restart --component overlay-fleet-supervisor" not in step_text


def test_fleet_plan_config_unresolved_blocker_emits_advisory_not_supervisor_restart():
    report = build_report(
        _with_fleet_plan(
            _fake_probes(
                scan_active_rebuilds=lambda: [],
                launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
            ),
            _fleet_plan_snapshot(
                path="/workspace/hai-sim-engine-wt-lane-config-unresolved",
                action="full-index-config-unresolved",
                next_action="resolve-daemon-config",
                actual_blocker="lane-config-unresolved",
            ),
        ),
        now=FIXED_NOW,
    )

    step = _find_step(report, component="worktree:hai-sim-engine-wt-lane-config-unresolved")
    step_text = _action_text(step)

    assert step["operator_gate"] is True
    assert step["action"] == "operator-review-required"
    assert "resolve-daemon-config" in step_text
    assert "lane-config-unresolved" in step_text
    assert "colgrep_launchd_control.py restart --component overlay-fleet-supervisor" not in step_text


def test_fleet_plan_probe_raises_fail_open_and_omits_worktree_actions():
    def raising_fleet_plan() -> dict[str, Any]:
        raise RuntimeError("fleet-plan unavailable")

    report = build_report(
        _with_fleet_plan(
            _fake_probes(
                scan_active_rebuilds=lambda: [],
                launchd_state=lambda: _launchd_state(supervisor=True, watcher=True, health_check=True),
            ),
            raising_fleet_plan,
        ),
        now=FIXED_NOW,
    )

    assert report["schema_version"] == "colgrep.status-report.v1"
    assert not any(str(step.get("component", "")).startswith("worktree:") for step in report["action_sequence"])


# -----------------------------------------------------------------------------
# WORK-289 BR-15 — registry-integrity linter parity between `health` and `report`.
#
# BR-05 shipped `_registry_integrity_findings()` as an unconditional `health` row
# (colgrep_worktree.py `dependency_health`). The `report` surface (this module)
# never got it, so `report` and `health` could silently disagree about registry
# corruption. These tests wire the SAME findings (via an injected probe — the
# detection function itself lives in colgrep_worktree.py and must be reused, not
# reimplemented here) into `report`'s infrastructure table.
# -----------------------------------------------------------------------------
def _with_registry_integrity_findings(probes: Probes, findings: list[dict[str, Any]] | Callable[[], list[dict[str, Any]]]) -> Probes:
    """Attach the optional probe dynamically so behavior tests fail on missing
    consumption (not on construction) until Probes grows the real field."""
    findings_fn = findings if callable(findings) else (lambda: findings)
    setattr(probes, "registry_integrity_findings", findings_fn)
    return probes


def test_probes_registry_integrity_findings_is_optional_default_none():
    from dataclasses import fields

    field_defaults = {field.name: field.default for field in fields(Probes)}
    assert "registry_integrity_findings" in field_defaults
    assert field_defaults["registry_integrity_findings"] is None


def test_report_surfaces_registry_integrity_findings_row_when_violations_exist():
    finding = {
        "class": "api-index-equals-base",
        "ticket": "TASK-583",
        "project": "hai-sim-engine",
        "worktree_path": "/workspace/hai-sim-engine-wt-has-583",
        "api_index_name": "hai-sim-engine",
        "base_index": "hai-sim-engine",
        "detail": "registry entry for ticket 'TASK-583' has api_index_name == base_index",
    }
    report = build_report(
        _with_registry_integrity_findings(_fake_probes(), [finding]),
        now=FIXED_NOW,
    )

    row = next(r for r in report["infrastructure"] if r.get("component") == "registry integrity lint")
    assert "1 finding" in row["state"]
    assert "api-index-equals-base" in row["detail"]
    assert "TASK-583" in row["detail"]


def test_report_registry_integrity_row_is_clean_when_no_violations():
    report = build_report(
        _with_registry_integrity_findings(_fake_probes(), []),
        now=FIXED_NOW,
    )

    row = next(r for r in report["infrastructure"] if r.get("component") == "registry integrity lint")
    assert "🟢" in row["state"]


def test_report_registry_integrity_row_absent_probe_is_honest_no_probe():
    # Absent Probes field entirely (back-compat callers that never set it) must not
    # fabricate a "clean" row — the whole reason BR-15 exists is that silence must
    # never be mistaken for "checked and agreed".
    report = build_report(_fake_probes(), now=FIXED_NOW)

    row = next(r for r in report["infrastructure"] if r.get("component") == "registry integrity lint")
    assert "n/a" in row["state"].lower() or "no probe" in row["detail"].lower()


def test_report_registry_integrity_probe_raises_fail_open_not_silently_clean():
    def raising_findings() -> list[dict[str, Any]]:
        raise RuntimeError("registry unreadable")

    report = build_report(
        _with_registry_integrity_findings(_fake_probes(), raising_findings),
        now=FIXED_NOW,
    )

    row = next(r for r in report["infrastructure"] if r.get("component") == "registry integrity lint")
    assert "🟢" not in row["state"], row
    assert "n/a" in row["state"].lower() or "unknown" in row["state"].lower()


# -----------------------------------------------------------------------------
# WORK-289 BR-17 — api-index-equals-base precision, report-surface parity with
# health. `_registry_integrity_findings()` (colgrep_worktree.py) now enriches the
# api-index-equals-base shape with whether a dedicated overlay index demonstrably
# exists (source-confirmed: reconcile_registry_manifest_at_fleet_tick,
# colgrep_overlay_fleet_supervisor.py, WORK-244 P0-9, always pairs
# api_index_name=base_index with physical_colgrep_dir="" for a genuinely
# base-authoritative row). When no dedicated index exists the finding's `class`
# becomes "api-index-equals-base-informational" -- `report` must not render that
# as a ⚠️ WARN row (informational at most), and must never silently drop it either.
# -----------------------------------------------------------------------------
def test_report_registry_integrity_row_is_informational_not_warn_when_no_dedicated_index_exists():
    finding = {
        "class": "api-index-equals-base-informational",
        "ticket": "TASK-583",
        "project": "hai-sim-engine",
        "worktree_path": "/workspace/hai-sim-engine/.claude/worktrees/hai-sim-engine-wt-has-583",
        "api_index_name": "hai-sim-engine",
        "base_index": "hai-sim-engine",
        "dedicated_index_exists": False,
        "detail": "registry entry for ticket 'TASK-583' has api_index_name == base_index but no dedicated index exists (likely honest base-authoritative encoding)",
    }
    report = build_report(
        _with_registry_integrity_findings(_fake_probes(), [finding]),
        now=FIXED_NOW,
    )

    row = next(r for r in report["infrastructure"] if r.get("component") == "registry integrity lint")
    assert "⚠️" not in row["state"], (
        f"an informational-only (no dedicated index) finding must not WARN: {row}"
    )
    assert "TASK-583" in row["detail"], (
        f"the informational finding must still be named, never silently dropped: {row}"
    )


def test_report_registry_integrity_row_still_warns_when_hard_and_informational_findings_coexist():
    hard_finding = {
        "class": "api-index-equals-base",
        "ticket": "TASK-582",
        "project": "hai-sim-engine",
        "worktree_path": "/workspace/hai-sim-engine/.claude/worktrees/hai-sim-engine-wt-has-582",
        "api_index_name": "hai-sim-engine",
        "base_index": "hai-sim-engine",
        "dedicated_index_exists": True,
        "detail": "registry entry for ticket 'TASK-582' has api_index_name == base_index",
    }
    informational_finding = {
        "class": "api-index-equals-base-informational",
        "ticket": "TASK-583",
        "project": "hai-sim-engine",
        "worktree_path": "/workspace/hai-sim-engine/.claude/worktrees/hai-sim-engine-wt-has-583",
        "api_index_name": "hai-sim-engine",
        "base_index": "hai-sim-engine",
        "dedicated_index_exists": False,
        "detail": "registry entry for ticket 'TASK-583' has api_index_name == base_index but no dedicated index exists",
    }
    report = build_report(
        _with_registry_integrity_findings(_fake_probes(), [hard_finding, informational_finding]),
        now=FIXED_NOW,
    )

    row = next(r for r in report["infrastructure"] if r.get("component") == "registry integrity lint")
    assert "⚠️" in row["state"], f"the coexisting hard finding must still WARN: {row}"
    assert "TASK-582" in row["detail"], "the hard finding must still be named"
    assert "TASK-583" in row["detail"], (
        "the coexisting informational finding must still be surfaced, not dropped"
    )


# -----------------------------------------------------------------------------
# WORK-289 BR-08 — status-plane disagreement parity between `health` and `report`.
#
# Field failure (2026Jul23 retro §WRONG-8): `report` said `orphans:0`/"no active
# rebuild" while `resolve` showed "overlay-rebuilding" and the pid file pointed at
# a dead worker — the queue plane and the watcher-liveness plane were never
# cross-checked on the `report` surface. These tests wire the SAME findings (via
# an injected probe — the detection function lives in colgrep_worktree.py's
# `_status_plane_disagreements()` and must be reused, not reimplemented here)
# into `report`'s infrastructure table as a loud DISAGREEMENT line.
# -----------------------------------------------------------------------------
def _with_status_plane_disagreements(probes: Probes, findings: list[dict[str, Any]] | Callable[[], list[dict[str, Any]]]) -> Probes:
    """Attach the optional probe dynamically so behavior tests fail on missing
    consumption (not on construction) until Probes grows the real field."""
    findings_fn = findings if callable(findings) else (lambda: findings)
    setattr(probes, "status_plane_disagreements", findings_fn)
    return probes


def test_probes_status_plane_disagreements_is_optional_default_none():
    from dataclasses import fields

    field_defaults = {field.name: field.default for field in fields(Probes)}
    assert "status_plane_disagreements" in field_defaults
    assert field_defaults["status_plane_disagreements"] is None


def test_report_surfaces_status_plane_disagreement_row_when_planes_disagree():
    finding = {
        "class": "queue-active-watcher-dead",
        "ticket": "TASK-999",
        "project": "hai-sim-engine",
        "worktree_path": "/workspace/hai-sim-engine-wt-has-999",
        "queue_state": "running",
        "watcher_state": "watcher-dead",
        "worker_pid": 999999999,
        "detail": "refresh-queue for ticket 'TASK-999' claims an active rebuild (state='running') but watcher-liveness says 'watcher-dead'",
    }
    report = build_report(
        _with_status_plane_disagreements(_fake_probes(), [finding]),
        now=FIXED_NOW,
    )

    row = next(r for r in report["infrastructure"] if r.get("component") == "status-plane disagreement")
    assert "DISAGREEMENT" in row["state"]
    assert "TASK-999" in row["state"]
    assert "running" in row["detail"]
    assert "watcher-dead" in row["detail"]


def test_report_status_plane_disagreement_row_is_clean_when_planes_agree():
    report = build_report(
        _with_status_plane_disagreements(_fake_probes(), []),
        now=FIXED_NOW,
    )

    row = next(r for r in report["infrastructure"] if r.get("component") == "status-plane disagreement")
    assert "🟢" in row["state"]


def test_report_status_plane_disagreement_row_absent_probe_is_honest_no_probe():
    # Absent Probes field entirely (back-compat callers that never set it) must not
    # fabricate a "clean" row — silence must never be mistaken for "checked and
    # agreed" (retro §WRONG-8, same shape as BR-15).
    report = build_report(_fake_probes(), now=FIXED_NOW)

    row = next(r for r in report["infrastructure"] if r.get("component") == "status-plane disagreement")
    assert "n/a" in row["state"].lower() or "no probe" in row["detail"].lower()


def test_report_status_plane_disagreement_probe_raises_fail_open_not_silently_clean():
    def raising_findings() -> list[dict[str, Any]]:
        raise RuntimeError("registry unreadable")

    report = build_report(
        _with_status_plane_disagreements(_fake_probes(), raising_findings),
        now=FIXED_NOW,
    )

    row = next(r for r in report["infrastructure"] if r.get("component") == "status-plane disagreement")
    assert "🟢" not in row["state"], row
    assert "n/a" in row["state"].lower() or "unknown" in row["state"].lower()
