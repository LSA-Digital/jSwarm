"""The Jira adapter: wraps the existing local Atlassian MCP client
(`jswarm.jira_mcp_closeout`) behind the `Tracker` interface.

`jira_mcp_closeout.py` was built for one workflow, closeout, and its shape
does not fit `Tracker` cleanly in two ways that matter here:

- `transition_done()` only ever searches for a transition whose name
  contains "done"; `Tracker.transition()` must reach any `target_state`.
- `get_issue()` requests `summary,status,updated,parent,issuetype` and never
  `description`; `Tracker.resolve()` needs a description.

Rather than change that module, which is out of scope for this task, this
adapter drives its `JiraMcpClient` directly for get-issue and transition, and
reuses `add_comment()` unchanged since it already fits `comment()` exactly.
"""
from __future__ import annotations

from dataclasses import dataclass

from jswarm.jira_mcp_closeout import (
    DEFAULT_MCP_URL,
    JiraMcpClient,
    JiraMcpError,
    add_comment,
    extract_json_text,
    retry,
)
from jswarm.tracker.base import Result, WorkItem


@dataclass(frozen=True)
class JiraTracker:
    key_prefix: str
    mcp_url: str = DEFAULT_MCP_URL
    attempts: int = 3
    backoff_seconds: float = 2.0

    def is_configured(self) -> bool:
        return True

    def describe(self) -> str:
        return f"Jira ({self.key_prefix})"

    def _client(self) -> JiraMcpClient:
        return JiraMcpClient(self.mcp_url)

    def resolve(self, work_id: str) -> WorkItem | None:
        client = self._client()
        result = client.call_tool(
            "jira_get_issue",
            {
                "issue_key": work_id,
                "fields": "summary,description,status",
                "comment_limit": 0,
                "update_history": False,
            },
        )
        data = extract_json_text(result)
        if not isinstance(data, dict):
            return None
        status = data.get("status")
        status_name = status.get("name") if isinstance(status, dict) else str(status or "")
        return WorkItem(
            key=work_id,
            title=str(data.get("summary") or ""),
            description=str(data.get("description") or ""),
            status=status_name,
        )

    def comment(self, work_id: str, text: str) -> Result:
        try:
            retry(
                lambda: add_comment(self._client(), work_id, text),
                attempts=self.attempts,
                backoff_seconds=self.backoff_seconds,
            )
        except Exception as error:  # noqa: BLE001 - a tracker outage must never take the lifecycle down
            return Result(
                ok=False,
                skipped=False,
                message=(
                    f"comment on {work_id} failed: {error}. Local state was already "
                    f"written; add this comment in Jira by hand, or re-run once Jira "
                    f"is reachable."
                ),
            )
        return Result(ok=True, skipped=False, message=f"commented on {work_id}")

    def transition(self, work_id: str, target_state: str) -> Result:
        try:
            retry(
                lambda: self._transition_to(work_id, target_state),
                attempts=self.attempts,
                backoff_seconds=self.backoff_seconds,
            )
        except Exception as error:  # noqa: BLE001 - a tracker outage must never take the lifecycle down
            return Result(
                ok=False,
                skipped=False,
                message=(
                    f"transition of {work_id} to {target_state!r} failed: {error}. "
                    f"Local state was already written; make the transition in Jira "
                    f"by hand, or re-run once Jira is reachable."
                ),
            )
        return Result(ok=True, skipped=False, message=f"transitioned {work_id} to {target_state}")

    def _transition_to(self, work_id: str, target_state: str) -> None:
        client = self._client()
        transitions_result = client.call_tool("jira_get_transitions", {"issue_key": work_id})
        transitions = extract_json_text(transitions_result)
        if not isinstance(transitions, list):
            raise JiraMcpError("jira_get_transitions did not return a list")
        match = next(
            (
                item
                for item in transitions
                if isinstance(item, dict) and str(item.get("name", "")).lower() == target_state.lower()
            ),
            None,
        )
        if not match or not match.get("id"):
            names = [str(item.get("name")) for item in transitions if isinstance(item, dict)]
            raise JiraMcpError(
                f"no {target_state!r} transition available for {work_id}; available: {names}"
            )
        client.call_tool(
            "jira_transition_issue",
            {"issue_key": work_id, "transition_id": str(match["id"])},
        )
