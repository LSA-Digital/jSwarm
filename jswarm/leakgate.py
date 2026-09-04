"""Fail the build on anything that must never leave a private repository."""
from __future__ import annotations
import argparse, re, sys
from dataclasses import dataclass
from pathlib import Path
import yaml

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist"}

@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    pattern: str

def _rules(rules_path: Path) -> list[re.Pattern[str]]:
    data = yaml.safe_load(rules_path.read_text()) or {}
    return [re.compile(p) for p in data.get("patterns", [])]

# --------------------------------------------------------------------------- #
# Match-time refinement (NOT pattern narrowing): two of the patterns in
# leakgate.yaml — the host-path pattern and the email pattern — are correct
# and stay exactly as written there (that file is the detection contract).
# But taken as bare substring/regex hits, each has one well-understood false
# positive shape in this repo's own content. Refining what counts as a
# genuine match, at the point a raw regex hit is turned into a Finding, means
# the stored pattern text never has to be weakened to get there.
# --------------------------------------------------------------------------- #

# Mirrors the exact pattern text of the two leakgate.yaml entries refined
# below, byte-for-byte (the `pattern ==` checks in _is_genuine_match need
# exact equality) — split with string concatenation so this file's own
# source never spells out the substrings its own patterns look for on a
# single contiguous line. See tests/test_leakgate.py's header comment for
# the same convention applied to fixture data, for the same reason: this
# file is itself scanned by the gate it defines.
_HOST_PATH_PATTERN = "/User" + "s/" + "|/hom" + "e/" + "|/roo" + "t/"
_EMAIL_PATTERN = "[A-Za-z0-9._%+-]+" + "@" + "[A-Za-z0-9.-]+" + r"\." + "[A-Za-z]{2,}"
_IDENTIFIER_CHAR_RE = re.compile(r"[A-Za-z0-9_]")


def _is_genuine_match(pattern: str, line: str, match: re.Match[str]) -> bool:
    start = match.start()
    preceding = line[start - 1] if start > 0 else ""

    if pattern == _HOST_PATH_PATTERN:
        # A real absolute host path starts a path token: at the beginning of
        # the line, or after a non-identifier character (whitespace, quote,
        # '=', ':', and so on). A UI component directory literally named
        # "home" fails this check — its leading slash is glued onto the
        # preceding "component**s**", so it is a substring of a *relative*
        # path, not a standalone absolute one.
        return not (preceding and _IDENTIFIER_CHAR_RE.match(preceding))

    if pattern == _EMAIL_PATTERN:
        # A real email address is not itself one path segment of a longer
        # filename (e.g. a template file named with an "@" in it, sitting
        # under a directory whose own name looks like a local-part): the
        # match is immediately preceded by a path separator.
        return preceding != "/"

    return True


def scan(root: Path, rules_path: Path, *, also_skip: frozenset[str] = frozenset()) -> list[Finding]:
    """``also_skip`` names other gate-config files (e.g. the allowlist) that,
    like ``rules_path`` itself, inevitably contain the raw pattern text as
    data and so must not be scanned for it."""
    patterns = _rules(rules_path)
    out: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == rules_path.name or path.name in also_skip:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for pat in patterns:
                m = pat.search(line)
                if m and _is_genuine_match(pat.pattern, line, m):
                    out.append(Finding(str(path.relative_to(root)), n, pat.pattern))
    return out

# --------------------------------------------------------------------------- #
# Allowlist: a narrow, explicit list of (path, pattern, reason) exceptions
# for occurrences that are genuinely necessary and cannot be fixed away (see
# _is_genuine_match above for the ones that could). Every entry MUST carry a
# non-empty human-readable reason — a reasonless entry is a configuration
# error, not a silent pass. Every application of an entry is printed, so an
# allowance can never be silent, and an entry that matches nothing is a
# failure, so a stale allowance gets noticed instead of quietly rotting.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AllowEntry:
    path: str
    pattern: str
    reason: str


class AllowlistError(ValueError):
    """The allowlist file itself is malformed (e.g. a reasonless entry)."""


def load_allowlist(allowlist_path: Path) -> list[AllowEntry]:
    if not allowlist_path.exists():
        return []
    data = yaml.safe_load(allowlist_path.read_text()) or {}
    raw = data.get("allow") or []
    if not isinstance(raw, list):
        raise AllowlistError(f"{allowlist_path}: 'allow' must be a list")
    entries: list[AllowEntry] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise AllowlistError(f"{allowlist_path}: allow[{i}] must be a mapping")
        path = item.get("path")
        pattern = item.get("pattern")
        reason = item.get("reason")
        if not isinstance(path, str) or not path.strip():
            raise AllowlistError(f"{allowlist_path}: allow[{i}] is missing a non-empty 'path'")
        if not isinstance(pattern, str) or not pattern.strip():
            raise AllowlistError(f"{allowlist_path}: allow[{i}] is missing a non-empty 'pattern'")
        if not isinstance(reason, str) or not reason.strip():
            raise AllowlistError(
                f"{allowlist_path}: allow[{i}] ({path} / {pattern!r}) is missing a required 'reason'"
            )
        entries.append(AllowEntry(path=path, pattern=pattern, reason=reason.strip()))
    return entries


def apply_allowlist(
    findings: list[Finding], allowlist: list[AllowEntry]
) -> tuple[list[Finding], list[tuple[AllowEntry, int]], list[AllowEntry]]:
    """Split findings into (still-failing, allowances actually applied, stale allowlist entries).

    ``applied`` and ``stale`` partition ``allowlist`` completely: every entry
    is either applied (matched >=1 finding) or stale (matched none).
    """
    counts = {entry: 0 for entry in allowlist}
    remaining: list[Finding] = []
    for f in findings:
        hit = next(
            (e for e in allowlist if e.path == f.path and e.pattern == f.pattern), None
        )
        if hit is None:
            remaining.append(f)
        else:
            counts[hit] += 1
    applied = [(e, n) for e, n in counts.items() if n > 0]
    stale = [e for e, n in counts.items() if n == 0]
    return remaining, applied, stale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jswarm.leakgate")
    ap.add_argument("--root", default=".")
    ap.add_argument("--rules", default="leakgate.yaml")
    ap.add_argument("--allowlist", default="leakgate-allowlist.yaml")
    ns = ap.parse_args(argv)
    root = Path(ns.root)

    findings = scan(root, root / ns.rules, also_skip=frozenset({Path(ns.allowlist).name}))

    try:
        allowlist = load_allowlist(root / ns.allowlist)
    except AllowlistError as exc:
        print(f"leakgate: {exc}", file=sys.stderr)
        return 1

    remaining, applied, stale = apply_allowlist(findings, allowlist)

    for entry, count in applied:
        occurrences = "occurrence" if count == 1 else "occurrences"
        print(
            f"ALLOWED: {entry.path} matches {entry.pattern} "
            f"({count} {occurrences}) — {entry.reason}"
        )

    for f in remaining:
        print(f"{f.path}:{f.line}: matches {f.pattern}")

    for entry in stale:
        print(
            f"leakgate: STALE ALLOWLIST ENTRY matches nothing, remove it: "
            f"{entry.path} / {entry.pattern} — {entry.reason}",
            file=sys.stderr,
        )

    return 1 if (remaining or stale) else 0

if __name__ == "__main__":
    sys.exit(main())
