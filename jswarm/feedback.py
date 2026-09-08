"""Local-only feedback drafts. This module has no upload or transcript discovery path."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

from jswarm.compliance.sanitizer import sanitize
from jswarm.platform import current as current_platform

FORM_URL = "https://jarviswarm.com/feedback"
MAX_BYTES = 24_000
FIELDS = ("summary", "expected", "actual", "steps", "command", "excerpt")


def draft(data: object) -> dict:
    if not isinstance(data, dict) or set(data) - set(FIELDS):
        raise ValueError("Use only summary, expected, actual, steps, command, and optional excerpt.")
    if any(not isinstance(value, str) for value in data.values()):
        raise ValueError("Feedback fields must be text.")
    if any(not data.get(key, "").strip() for key in ("summary", "expected", "actual")):
        raise ValueError("Describe the problem, expected result, and actual result.")
    if len(json.dumps(data).encode()) > MAX_BYTES:
        raise ValueError("Keep the report under 24 KB. Include a short excerpt, not a whole transcript.")
    source = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        commit = "unknown"
    import re
    if not re.fullmatch(r"[a-f0-9]{40,64}", commit):
        commit = "unknown"
    return {"schema": "jswarm.feedback.v1", "id": str(uuid.uuid4()),
            "environment": {"commit": commit, "os": current_platform().name,
                            "architecture": platform.machine(), "python": platform.python_version()},
            "report": sanitize({key: data.get(key, "").strip() for key in FIELDS})}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare feedback locally. Nothing is sent to LSA.")
    parser.add_argument("--input", type=Path, help="JSON containing only the text you chose to include")
    parser.add_argument("--output", type=Path, help="New local draft file; existing files are never overwritten")
    args = parser.parse_args(argv)
    if args.input is None:
        parser.print_help()
        print(f"Private review and submission: {FORM_URL}")
        return 0
    try:
        if args.input.stat().st_size > MAX_BYTES:
            raise ValueError("Input exceeds 24 KB. Select a shorter excerpt.")
        value = draft(json.loads(args.input.read_text(encoding="utf-8")))
        rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            # No broad scans, no hidden session capture, no overwrite, owner-only permissions.
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
            print(f"Draft saved: {args.output}. Nothing sent.")
            print(f"Next: /jFeedback can review and send after approval when connected. Browser fallback: {FORM_URL}. Redaction is not a privacy guarantee.")
        else:
            print(rendered, end="")
        return 0
    except (OSError, ValueError) as exc:
        # Do not echo raw user content or parser error excerpts.
        print(f"Could not prepare the draft ({type(exc).__name__}). Check the fields, file size, and output path.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
