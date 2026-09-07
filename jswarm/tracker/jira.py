"""Jira operations through the active host's authenticated Atlassian MCP.

Claude Code owns OAuth. A shell process cannot inherit its authenticated MCP
session. Emit an explicit host request instead of silently targeting a local
proxy. The lifecycle skill runs the tools and supplies the observed evidence
to complete_request; emitting a request is never reported as success.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from jswarm.tracker.base import HostRequest, Result, WorkItem
from jswarm.workitem.identity import parse


def _request(action: str, work_id: str, *, text: str | None = None,
             target_state: str | None = None) -> HostRequest:
    if parse(work_id).kind != "tracker-key":
        raise ValueError("Jira requires an issue key such as PS-14; use a tracker-free project for local slugs")
    payload = json.dumps([action, work_id, text, target_state], ensure_ascii=False)
    request_id = hashlib.sha256(payload.encode()).hexdigest()
    return HostRequest(action, work_id, request_id, text, target_state)


@dataclass(frozen=True)
class JiraTracker:
    key_prefix: str

    def is_configured(self) -> bool:
        # Configuration does not imply authenticated, reachable, or synchronized.
        return True

    def describe(self) -> str:
        return f"Jira ({self.key_prefix}), via the host's authenticated Atlassian MCP"

    def resolve(self, work_id: str) -> HostRequest | None:
        if parse(work_id).kind == "slug":
            return None
        return _request("resolve", work_id)

    def comment(self, work_id: str, text: str) -> HostRequest | Result:
        if parse(work_id).kind == "slug":
            return Result(True, True, "Local work item; no Jira comment needed.")
        return _request("comment", work_id, text=text)

    def transition(self, work_id: str, target_state: str) -> HostRequest | Result:
        if parse(work_id).kind == "slug":
            return Result(True, True, "Local work item; no Jira transition needed.")
        return _request("transition", work_id, target_state=target_state)


def complete_request(request: HostRequest, evidence: object) -> WorkItem | Result:
    """Validate host-observed results bound to this exact operation.

    This validates a receipt; it does not independently authenticate the remote
    service. The host must populate it from real tool responses, not estimates.
    """
    if not isinstance(evidence, dict):
        raise ValueError("host result must be a JSON object")
    for field in ("request_id", "work_id", "action", "server"):
        if evidence.get(field) != getattr(request, field):
            raise ValueError(f"host result {field} does not match this request")
    if evidence.get("ok") is False:
        message = evidence.get("error")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("failed host result requires the observed error")
        return Result(False, False, f"Jira {request.action} failed: {message}. Local evidence is preserved.")
    if evidence.get("ok") is not True:
        raise ValueError("host result requires a boolean ok")
    if not isinstance(evidence.get("tool"), str) or not evidence["tool"].strip():
        raise ValueError("host result requires the actual Atlassian tool name")
    observed = evidence.get("observed")
    if not isinstance(observed, dict) or observed.get("key") != request.work_id:
        raise ValueError("observed Jira issue key does not match this request")
    if request.action == "resolve":
        if not all(isinstance(observed.get(f), str) for f in ("title", "description", "status")):
            raise ValueError("resolved issue requires title, description, and status strings")
        if not observed["title"].strip() or not observed["status"].strip():
            raise ValueError("resolved issue requires a non-empty title and status")
        return WorkItem(request.work_id, observed["title"], observed["description"], observed["status"])
    if request.action == "comment":
        if not str(observed.get("comment_id") or "").strip() or observed.get("text") != request.text:
            raise ValueError("comment receipt requires its Jira id and exact posted text")
        return Result(True, False, f"commented on {request.work_id}; Jira comment {observed['comment_id']}")
    if request.action == "transition":
        status = observed.get("status")
        if not isinstance(status, str) or status.casefold() != (request.target_state or "").casefold():
            raise ValueError("read-back Jira status does not match the requested target state")
        return Result(True, False, f"transitioned {request.work_id} to {status}, verified by reading the issue")
    raise ValueError("unsupported host request action")
