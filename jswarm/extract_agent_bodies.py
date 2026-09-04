"""Extract agent prompt bodies for upgrade survival (Phase 13).

OMC and OMO upgrades have historically wiped customized agent prompts in
this host's agents directory (``*.md`` files under
`jswarm.host.claude_code.ClaudeCodeHost.agents_dir`) and
``~/.config/opencode/oh-my-openagent.json``.
This script extracts the prompt body (everything after the YAML frontmatter
and any optional ``<ROUTE:...>`` tag) from each live agent file into a
version-controlled body file at
``jswarm/config/agent-bodies/<slug>.body.md`` (absent by default — an
adopter populates it locally; see the module docstring on
``jswarm.regenerate_claude_agents`` for the fail-open contract when it's
missing).

After Phase 4 regeneration lands, an OMC wipeout becomes recoverable by:
  1. running the equivalent post-upgrade restore tooling, which reads YAML
     frontmatter from ``subagent-context-profiles.yaml``, emits an optional
     ROUTE tag, and appends the committed body file.
  2. confirming via the upgrade-survival smoke that all agents dispatch and
     bodies match committed sources byte-for-byte.

This script is idempotent: re-running it with the same live state produces
zero diff. Re-run it after any intentional prompt edits land in the live
agent files so the canonical bodies stay in sync.

Round-trip contract: for every agent ``<slug>``,
  ``(frontmatter + leading-blanks + ROUTE-tag + post-ROUTE-blanks) + body``
must equal the original ``<slug>.md`` file in this host's agents directory
(`jswarm.host.claude_code.ClaudeCodeHost.agents_dir`) byte-for-byte.

Usage:
  ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python jswarm/extract_agent_bodies.py
  ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python jswarm/extract_agent_bodies.py --check
  ${JSWARM_HOME:-$HOME/dev/jswarm}/.venv/bin/python jswarm/extract_agent_bodies.py --src /path/to/agents --dst /path/to/bodies

``--check`` performs the round-trip integrity check without writing any
files and exits non-zero on mismatch.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from jswarm.host import current as _current_host

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = _current_host().agents_dir()
DEFAULT_DST = _REPO_ROOT / "jswarm" / "config" / "agent-bodies"

FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
ROUTE_RE = re.compile(r"^\s*<ROUTE:[^>]+>\s*\n", re.DOTALL)


@dataclass(frozen=True)
class _Split:
    """Result of splitting one agent .md into prefix + body."""

    slug: str
    prefix: str  # frontmatter (+ blanks + optional ROUTE tag + blanks)
    body: str


def _split_agent_file(path: Path) -> _Split:
    """Split ``path`` into (prefix, body). Raises ValueError if no frontmatter."""
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path.name}: no frontmatter")
    after_fm = text[m.end():]
    after_blanks = after_fm.lstrip("\n")
    leading_blanks = len(after_fm) - len(after_blanks)
    rm = ROUTE_RE.match(after_blanks)
    if rm:
        after_route = after_blanks[rm.end():]
        post_route_blanks = len(after_route) - len(after_route.lstrip("\n"))
        prefix_end = m.end() + leading_blanks + rm.end() + post_route_blanks
    else:
        prefix_end = m.end() + leading_blanks
    return _Split(slug=path.stem, prefix=text[:prefix_end], body=text[prefix_end:])


def extract(src: Path, dst: Path, *, write: bool = True) -> list[tuple[str, int, bool]]:
    """Extract bodies. Returns list of (slug, body_byte_size, round_trip_ok)."""
    dst.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, int, bool]] = []
    for path in sorted(src.glob("*.md")):
        try:
            split = _split_agent_file(path)
        except ValueError as exc:
            results.append((path.stem, 0, False))
            print(f"  WARN {path.stem}: {exc}", file=sys.stderr)
            continue
        if not split.body.strip():
            results.append((split.slug, 0, False))
            print(f"  WARN {split.slug}: empty body", file=sys.stderr)
            continue
        body = split.body if split.body.endswith("\n") else split.body + "\n"
        # Round-trip check
        original = path.read_text(encoding="utf-8")
        reconstructed = split.prefix + split.body
        round_trip_ok = reconstructed == original
        if write and round_trip_ok:
            (dst / f"{split.slug}.body.md").write_text(body, encoding="utf-8")
        results.append((split.slug, len(body.encode("utf-8")), round_trip_ok))
    return results


def check_round_trip(src: Path, dst: Path) -> list[tuple[str, str]]:
    """Verify committed body files reconstruct each live agent .md exactly.

    Returns list of (slug, mismatch_reason). Empty list = all clean.
    """
    mismatches: list[tuple[str, str]] = []
    for path in sorted(src.glob("*.md")):
        body_path = dst / f"{path.stem}.body.md"
        if not body_path.exists():
            mismatches.append((path.stem, "body file missing in committed location"))
            continue
        try:
            split = _split_agent_file(path)
        except ValueError as exc:
            mismatches.append((path.stem, str(exc)))
            continue
        committed_body = body_path.read_text(encoding="utf-8")
        reconstructed = split.prefix + committed_body
        # Allow the committed file to have a trailing newline that the source
        # might not — treat trailing-newline-only diffs as clean.
        original = path.read_text(encoding="utf-8")
        if reconstructed == original:
            continue
        if reconstructed.rstrip("\n") == original.rstrip("\n"):
            continue
        for i, (a, b) in enumerate(zip(reconstructed, original)):
            if a != b:
                mismatches.append((path.stem, f"byte {i}: committed has {a!r}, live has {b!r}"))
                break
        else:
            mismatches.append((
                path.stem,
                f"length diff: reconstructed={len(reconstructed)} live={len(original)}",
            ))
    return mismatches


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--dst", type=Path, default=DEFAULT_DST)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Round-trip check only; do not write files. Exit 1 on mismatch.",
    )
    args = parser.parse_args(argv)

    if args.check:
        mismatches = check_round_trip(args.src, args.dst)
        if mismatches:
            print(f"Round-trip check FAILED: {len(mismatches)} mismatches", file=sys.stderr)
            for slug, reason in mismatches:
                print(f"  {slug}: {reason}", file=sys.stderr)
            return 1
        print(f"Round-trip check OK: all {sum(1 for _ in args.src.glob('*.md'))} agents match")
        return 0

    results = extract(args.src, args.dst, write=True)
    written = sum(1 for _, _, ok in results if ok)
    skipped = [slug for slug, _, ok in results if not ok]
    print(f"Extracted {written} agent bodies → {args.dst}")
    if skipped:
        print(f"Skipped: {len(skipped)} ({', '.join(skipped)})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
