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

def scan(root: Path, rules_path: Path) -> list[Finding]:
    patterns = _rules(rules_path)
    out: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == rules_path.name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for pat in patterns:
                if pat.search(line):
                    out.append(Finding(str(path.relative_to(root)), n, pat.pattern))
    return out

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jswarm.leakgate")
    ap.add_argument("--root", default=".")
    ap.add_argument("--rules", default="leakgate.yaml")
    ns = ap.parse_args(argv)
    findings = scan(Path(ns.root), Path(ns.root) / ns.rules)
    for f in findings:
        print(f"{f.path}:{f.line}: matches {f.pattern}")
    return 1 if findings else 0

if __name__ == "__main__":
    sys.exit(main())
