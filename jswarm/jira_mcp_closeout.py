#!/usr/bin/env python3
"""Retry-safe Jira helper for the local Atlassian MCP HTTP server.

This intentionally covers the Jira operations that have been failing through
agent MCP clients: closeout comments/transitions plus queued ticket filing. It
retries transient MCP/client failures and writes an exact manual-action artifact
when Jira remains unavailable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.client import HTTPMessage
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeAlias, cast

DEFAULT_MCP_URL = "http://localhost:28080/mcp"
DEFAULT_TIMEOUT_SECONDS = 45.0
LOCALHOST_NAMES = {"127.0.0.1", "localhost", "::1"}
Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
JsonDict: TypeAlias = dict[str, Json]


class JiraMcpError(RuntimeError):
    """Raised when the Jira MCP server returns an error response."""


@dataclass(frozen=True)
class RetryResult:
    value: object
    attempts: int


class JiraMcpClient:
    def __init__(self, url: str = DEFAULT_MCP_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.url: str = url
        self.timeout_seconds: float = timeout_seconds
        self.session_id: str | None = None
        validate_localhost_url(url)

    def initialize(self) -> None:
        response, headers = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "jira-mcp-closeout", "version": "1.0"},
                },
            },
        )
        if response.get("error"):
            raise JiraMcpError(format_jsonrpc_error(response["error"]))
        self.session_id = headers.get("mcp-session-id")
        if not self.session_id:
            raise JiraMcpError("MCP initialize did not return mcp-session-id")

    def call_tool(self, name: str, arguments: JsonDict) -> JsonDict:
        if self.session_id is None:
            self.initialize()
        response, _ = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            session_id=self.session_id,
        )
        if response.get("error"):
            raise JiraMcpError(format_jsonrpc_error(response["error"]))
        result = response.get("result")
        if not isinstance(result, dict):
            raise JiraMcpError(f"Unexpected MCP tool result: {response!r}")
        result_dict = cast(JsonDict, result)
        if result_dict.get("isError"):
            raise JiraMcpError(extract_text(result_dict) or f"Tool {name} returned isError")
        return result_dict

    def _post(self, payload: JsonDict, *, session_id: str | None = None) -> tuple[JsonDict, dict[str, str]]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if session_id:
            headers["mcp-session-id"] = session_id
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - localhost-only MCP helper.
                raw = cast(bytes, response.read()).decode("utf-8")
                header_map = cast(HTTPMessage, response.headers)
                response_headers = {str(key).lower(): str(header_map[key]) for key in header_map.keys()}
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace") if error.fp else ""
            raise JiraMcpError(f"HTTP {error.code} from Atlassian MCP: {details[:500]}") from error
        except urllib.error.URLError as error:
            raise JiraMcpError(f"Unable to reach Atlassian MCP at {self.url}: {error.reason}") from error
        return parse_mcp_response(raw), response_headers


def validate_localhost_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in LOCALHOST_NAMES:
        raise ValueError(f"Refusing non-local Atlassian MCP URL: {url}")


def parse_mcp_response(raw: str) -> JsonDict:
    raw = raw.strip()
    if not raw:
        raise JiraMcpError("Empty MCP response")
    if raw.startswith("{"):
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise JiraMcpError(f"Unexpected JSON MCP response: {raw[:200]}")
        return cast(JsonDict, decoded)
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        decoded = json.loads(line[6:])
        if isinstance(decoded, dict):
            return cast(JsonDict, decoded)
    raise JiraMcpError(f"No JSON-RPC data event found in MCP response: {raw[:500]}")


def format_jsonrpc_error(error: object) -> str:
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        data = error.get("data")
        suffix = f" data={data!r}" if data else ""
        return f"JSON-RPC error {code}: {message}{suffix}"
    return f"JSON-RPC error: {error!r}"


def extract_text(result: JsonDict) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text_value = item.get("text")
            if isinstance(text_value, str):
                chunks.append(text_value)
    return "\n".join(chunks).strip()


def extract_json_text(result: JsonDict) -> Json:
    text = extract_text(result)
    if not text:
        raise JiraMcpError("MCP tool result did not include text content")
    return cast(Json, json.loads(text))


def retry(action: Callable[[], object], *, attempts: int, backoff_seconds: float) -> RetryResult:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return RetryResult(action(), attempt)
        except Exception as error:  # noqa: BLE001 - preserve and report exact external failure.
            last_error = error
            if attempt < attempts and backoff_seconds > 0:
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def get_issue(client: JiraMcpClient, issue_key: str) -> JsonDict:
    return client.call_tool(
        "jira_get_issue",
        {
            "issue_key": issue_key,
            # `parent`/`issuetype` are requested so the post-create read-back-verify
            # (verify_parent_link) has the fields it asserts on (COM-203 AC-1).
            "fields": "summary,status,updated,parent,issuetype",
            "comment_limit": 0,
            "update_history": False,
        },
    )


def create_issue(client: JiraMcpClient, project_key: str, summary: str, issue_type: str, description: str, parent: str | None) -> JsonDict:
    args: JsonDict = {
        "project_key": project_key,
        "summary": summary,
        "issue_type": issue_type,
        "description": description,
    }
    if parent:
        # Atlassian MCP `additional_fields` expects a plain parent KEY string, not the
        # legacy REST object shape. Official schema (lazy-mcp/hierarchy/atlassian/
        # jira_create_issue.json): {"parent": "PROJ-123"}. The nested form
        # {"parent": {"key": "…"}} makes the MCP reject create with
        # "expected 'key' property to be a string" (COM-143, HAS-238, Jun-2025 tool
        # failures). Read-back verify after create catches any silent link miss
        # (COM-203 AC-1).
        args["additional_fields"] = json.dumps({"parent": parent})
    return client.call_tool("jira_create_issue", args)


def extract_issue_key(result: JsonDict) -> str | None:
    """Best-effort extraction of the new issue key from a create-issue result.

    Prefers a structured `{"key": "<KEY>"}` (or nested `issue.key`) JSON body; falls
    back to scanning the plain text for a Jira key pattern.
    """
    try:
        data = extract_json_text(result)
    except (JiraMcpError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        key = data.get("key")
        if isinstance(key, str) and key:
            return key
        issue = data.get("issue")
        if isinstance(issue, dict) and isinstance(issue.get("key"), str) and issue["key"]:
            return cast(str, issue["key"])
    match = re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", extract_text(result))
    return match.group(0) if match else None


def parent_key_of(issue: Json) -> str | None:
    """Extract `parent.key` from a get-issue result, tolerant of both shapes.

    The Atlassian MCP `jira_get_issue` tool returns a FLATTENED projection with
    `parent` at the top level; raw Jira REST nests it under `fields.parent`. Accept
    either so the read-back-verify works against the real tool and raw payloads alike.
    """
    if not isinstance(issue, dict):
        return None
    parent = issue.get("parent")
    if not isinstance(parent, dict):
        fields = issue.get("fields")
        parent = fields.get("parent") if isinstance(fields, dict) else None
    key = parent.get("key") if isinstance(parent, dict) else None
    return key if isinstance(key, str) else None


def verify_parent_link(client: JiraMcpClient, issue_key: str, expected_parent: str) -> bool:
    """Read the issue back and confirm its parent key == expected_parent."""
    try:
        data = extract_json_text(get_issue(client, issue_key))
    except (JiraMcpError, json.JSONDecodeError):
        return False
    return parent_key_of(data) == expected_parent


def verify_and_report_parent(
    client: JiraMcpClient,
    *,
    new_issue_key: str,
    expected_parent: str,
    artifact_dir: Path,
    ticket_context: str,
) -> bool:
    """Verify the parent link after create; on failure warn LOUDLY + write a manual-action artifact.

    Returns True iff the read-back confirmed the parent link. A False return is never
    silent — it emits a stderr WARN and a durable manual-action artifact (COM-203 AC-1 /
    NFR-014-NO-FALSE-GREEN-UNDERREPORT: the helper must never report clean success while
    the parent silently failed to link).
    """
    if verify_parent_link(client, new_issue_key, expected_parent):
        return True
    manual_action = (
        f"Set the parent of `{new_issue_key}` to `{expected_parent}` in Jira. "
        f"The create call did not link the parent — a read-back of `{new_issue_key}` "
        f"showed no/incorrect `fields.parent.key`."
    )
    report = write_failure_report(
        artifact_dir=artifact_dir,
        ticket_context=ticket_context,
        issue_key=new_issue_key,
        action="atlassian_jira_create_issue_parent_link",
        manual_action=manual_action,
        error=RuntimeError(f"parent {expected_parent} not linked on create of {new_issue_key}"),
    )
    print(
        f"WARN parent link unverified for {new_issue_key} (expected parent {expected_parent}); "
        f"manual report: {report}",
        file=sys.stderr,
    )
    return False


def add_comment(client: JiraMcpClient, issue_key: str, body: str) -> JsonDict:
    return client.call_tool("jira_add_comment", {"issue_key": issue_key, "body": body})


def transition_done(client: JiraMcpClient, issue_key: str, comment: str | None = None) -> JsonDict:
    transitions_result = client.call_tool("jira_get_transitions", {"issue_key": issue_key})
    transitions = extract_json_text(transitions_result)
    if not isinstance(transitions, list):
        raise JiraMcpError("jira_get_transitions did not return a list")
    done_transition = next(
        (
            transition
            for transition in transitions
            if isinstance(transition, dict) and "done" in str(transition.get("name", "")).lower()
        ),
        None,
    )
    if not done_transition or not done_transition.get("id"):
        names = [str(item.get("name")) for item in transitions if isinstance(item, dict)]
        raise JiraMcpError(f"No Done transition available for {issue_key}; available transitions: {names}")
    args: JsonDict = {"issue_key": issue_key, "transition_id": str(done_transition["id"])}
    if comment:
        args["comment"] = comment
    return client.call_tool("jira_transition_issue", args)


def read_body(args: argparse.Namespace) -> str:
    if getattr(args, "body", None) and getattr(args, "body_file", None):
        raise SystemExit("Use either --body or --body-file, not both")
    if getattr(args, "comment", None) and getattr(args, "comment_file", None):
        raise SystemExit("Use either --comment or --comment-file, not both")
    body_file = getattr(args, "body_file", None) or getattr(args, "comment_file", None)
    inline = getattr(args, "body", None) or getattr(args, "comment", None)
    if body_file:
        return Path(body_file).read_text(encoding="utf-8")
    return inline or ""


def write_failure_report(
    *,
    artifact_dir: Path,
    ticket_context: str,
    issue_key: str,
    action: str,
    manual_action: str,
    error: Exception,
) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    safe_ticket = re.sub(r"[^A-Za-z0-9_-]+", "-", ticket_context or issue_key or "SESSION")
    path = artifact_dir / f"{safe_ticket}.toolfail.atlassianjira.{now.strftime('%Y%m%dT%H%M%SZ')}.md"
    content = f"""+++\ndate = \"{now.strftime('%Y-%m-%d')}\"\nreporter = \"jira_mcp_closeout.py\"\nticket_context = \"{ticket_context}\"\nproject = \"common\"\nseverity = \"degraded\"\ntool_name = \"{action}\"\ntool_category = \"atlassian\"\nerror_message = {json.dumps(str(error))}\nerror_type = \"timeout_or_mcp_error\"\nstatus = \"open\"\njira_ticket = \"{issue_key}\"\nresolved_at = \"\"\nresolution = \"\"\n+++\n\n# Tool Failure Report: Atlassian Jira Closeout\n\n## Failure\n\nAction `{action}` for `{issue_key}` failed after retry attempts.\n\n```\n{str(error)}\n```\n\n## Manual Action Required\n\n{manual_action}\n\n## Notes\n\nThe code/merge state may already be final-green. Do not mark closeout complete until the manual Jira action above is applied or this report is resolved.\n"""
    path.write_text(content, encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Retry-safe Jira closeout via local Atlassian MCP")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--backoff-seconds", type=float, default=2.0)
    parser.add_argument("--artifact-dir", default="docs/tool-failure-reports")
    parser.add_argument("--ticket-context", default="")
    subparsers = parser.add_subparsers(dest="command", required=True)

    get = subparsers.add_parser("get-issue", help="Fetch a Jira issue by key")
    get.add_argument("issue_key")

    create = subparsers.add_parser("create-issue", help="Create a Jira issue")
    create.add_argument("--project-key", required=True)
    create.add_argument("--summary", required=True)
    create.add_argument("--issue-type", required=True)
    create.add_argument("--description")
    create.add_argument("--description-file")
    create.add_argument("--parent")

    comment = subparsers.add_parser("comment", help="Add a Jira comment")
    comment.add_argument("issue_key")
    comment.add_argument("--body")
    comment.add_argument("--body-file")

    done = subparsers.add_parser("transition-done", help="Transition a Jira issue to Done")
    done.add_argument("issue_key")
    done.add_argument("--comment")
    done.add_argument("--comment-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = JiraMcpClient(args.mcp_url, args.timeout_seconds)
    ticket_context = args.ticket_context or getattr(args, "issue_key", "SESSION")
    artifact_dir = Path(args.artifact_dir)

    try:
        if args.command == "get-issue":
            result = retry(
                lambda: get_issue(client, args.issue_key),
                attempts=args.attempts,
                backoff_seconds=args.backoff_seconds,
            )
            print(extract_text(cast(JsonDict, result.value)) or f"OK get-issue {args.issue_key} attempts={result.attempts}")
            return 0
        if args.command == "create-issue":
            if args.description and args.description_file:
                raise SystemExit("Use either --description or --description-file, not both")
            description = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else (args.description or "")
            result = retry(
                lambda: create_issue(client, args.project_key, args.summary, args.issue_type, description, args.parent),
                attempts=args.attempts,
                backoff_seconds=args.backoff_seconds,
            )
            print(extract_text(cast(JsonDict, result.value)) or f"OK create-issue attempts={result.attempts}")
            if args.parent:
                # A verification error must NEVER discard a successful create: if it
                # propagated to the outer `except` the issue would be re-reported as a
                # create failure (and, before the fix below, re-created). Keep the failure
                # loud + artifacted, but return 0 — the issue WAS created (COM-203 finding 2).
                new_key: str | None = None
                try:
                    new_key = extract_issue_key(cast(JsonDict, result.value))
                    if new_key:
                        verify_and_report_parent(
                            client,
                            new_issue_key=new_key,
                            expected_parent=args.parent,
                            artifact_dir=artifact_dir,
                            ticket_context=ticket_context,
                        )
                    else:
                        report = write_failure_report(
                            artifact_dir=artifact_dir,
                            ticket_context=ticket_context,
                            issue_key=args.parent,
                            action="atlassian_jira_create_issue_parent_link",
                            manual_action=(
                                f"An issue was created but its key could not be parsed from the create "
                                f"response, so the parent link to `{args.parent}` could not be verified. "
                                f"Find the new issue and confirm/set its parent to `{args.parent}` manually."
                            ),
                            error=RuntimeError("created issue key not parseable from create response"),
                        )
                        print(
                            f"WARN could not determine the created issue key to verify parent {args.parent}; "
                            f"manual report: {report}",
                            file=sys.stderr,
                        )
                except Exception as verify_error:  # noqa: BLE001 - never let verification void a real create.
                    report = write_failure_report(
                        artifact_dir=artifact_dir,
                        ticket_context=ticket_context,
                        issue_key=new_key or args.parent,
                        action="atlassian_jira_create_issue_parent_link",
                        manual_action=(
                            f"The issue was created but its parent link to `{args.parent}` could not be "
                            f"verified (read-back error: {verify_error}). Confirm or set the parent manually."
                        ),
                        error=verify_error,
                    )
                    print(
                        f"WARN parent-link verification failed after a successful create "
                        f"(parent {args.parent}); manual report: {report}",
                        file=sys.stderr,
                    )
            return 0
        if args.command == "comment":
            body = read_body(args)
            if not body.strip():
                raise SystemExit("comment requires --body or --body-file")
            result = retry(
                lambda: add_comment(client, args.issue_key, body),
                attempts=args.attempts,
                backoff_seconds=args.backoff_seconds,
            )
            print(f"OK comment {args.issue_key} attempts={result.attempts}")
            return 0
        if args.command == "transition-done":
            comment = read_body(args)
            result = retry(
                lambda: transition_done(client, args.issue_key, comment or None),
                attempts=args.attempts,
                backoff_seconds=args.backoff_seconds,
            )
            print(f"OK transition-done {args.issue_key} attempts={result.attempts}")
            return 0
        raise SystemExit(f"Unsupported command: {args.command}")
    except Exception as error:  # noqa: BLE001 - failure report must capture any closeout blocker.
        # Do NOT re-run the operation here. retry() above already exhausted transient
        # retries; re-executing create-issue in the error path risks a double-create that
        # is then reported as success WITHOUT verification (COM-203 critic finding 2). The
        # error path's sole job is to record an exact manual-action artifact.
        if args.command == "get-issue":
            manual_action = f"Fetch `{args.issue_key}` in the Jira UI and compare summary/status with local plan metadata."
            action = "atlassian_jira_get_issue"
        elif args.command == "create-issue":
            description = Path(args.description_file).read_text(encoding="utf-8") if args.description_file else (args.description or "")
            parent_line = f" under parent `{args.parent}`" if args.parent else ""
            manual_action = f"Create a Jira `{args.issue_type}` in project `{args.project_key}`{parent_line}.\n\nSummary:\n```\n{args.summary}\n```\n\nDescription:\n```markdown\n{description}\n```"
            action = "atlassian_jira_create_issue"
        elif args.command == "comment":
            body = read_body(args)
            manual_action = f"Add this Jira comment to `{args.issue_key}`:\n\n```markdown\n{body}\n```"
            action = "atlassian_jira_add_comment"
        elif args.command == "transition-done":
            comment = read_body(args)
            comment_suffix = f" with this transition comment:\n\n```markdown\n{comment}\n```" if comment else ""
            manual_action = f"Transition `{args.issue_key}` to Done{comment_suffix}."
            action = "atlassian_jira_transition_issue"
        else:
            manual_action = f"Complete Jira action `{args.command}` manually for `{getattr(args, 'issue_key', ticket_context)}`."
            action = f"atlassian_jira_{args.command}"
        report = write_failure_report(
            artifact_dir=artifact_dir,
            ticket_context=ticket_context,
            issue_key=getattr(args, "issue_key", ticket_context),
            action=action,
            manual_action=manual_action,
            error=error,
        )
        print(f"FAIL {action} {getattr(args, 'issue_key', ticket_context)}; manual report: {report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
