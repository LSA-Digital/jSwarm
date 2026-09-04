"""Build the configured `Tracker` for a repo, defaulting to none.

This is the only place that knows the adapter config shape. Nothing outside
`jswarm.tracker` needs to know Jira exists at all.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from jswarm.tracker.base import Tracker
from jswarm.tracker.jira import JiraTracker
from jswarm.tracker.none import NullTracker

_ADAPTERS = ("jira", "none")


def load(repo: Path) -> Tracker:
    """Read `<repo>/.jswarm/config.yaml` and build its configured tracker.

    A missing config file, a missing `tracker` key, or `adapter: none` are
    all treated identically: no tracker. An unrecognised adapter name raises
    rather than silently falling back, naming the adapters that do exist.
    """
    config_path = Path(repo) / ".jswarm" / "config.yaml"
    if not config_path.is_file():
        return NullTracker()

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    tracker_cfg = data.get("tracker") or {}
    adapter = tracker_cfg.get("adapter", "none")

    if adapter == "none":
        return NullTracker()
    if adapter == "jira":
        return JiraTracker(key_prefix=tracker_cfg.get("key_prefix", ""))

    raise ValueError(
        f"unknown tracker adapter {adapter!r} in {config_path}; "
        f"supported adapters are: {', '.join(_ADAPTERS)}"
    )
