#!/usr/bin/env bash
# Show what changed upstream (in common) in every file this repo was copied from.
# Usage: scripts/upstream-diff.sh /path/to/common   [--full for the whole diff]
set -euo pipefail
common="${1:?path to the common checkout}"; mode="${2:---stat}"
here="$(cd "$(dirname "$0")/.." && pwd)"
prov="$here/PROVENANCE.yaml"

py="$here/.venv/bin/python"
if [ ! -x "$py" ]; then
    py="python3"
fi
if ! "$py" -c "import yaml" >/dev/null 2>&1; then
    echo "error: no Python interpreter with PyYAML available (tried $here/.venv/bin/python and python3)." >&2
    echo "Install it with: $here/.venv/bin/python -m pip install pyyaml (or create the venv from requirements.txt)." >&2
    exit 1
fi

"$py" - "$common" "$prov" "$mode" <<'PY'
import subprocess, sys
import yaml

common, prov, mode = sys.argv[1:4]

try:
    with open(prov) as f:
        data = yaml.safe_load(f)
except FileNotFoundError:
    print(f"error: {prov} not found. Run this from a copied jSwarm checkout that has PROVENANCE.yaml.", file=sys.stderr)
    sys.exit(1)

base = data["source_commit"]

is_repo = subprocess.run(
    ["git", "-C", common, "rev-parse", "--is-inside-work-tree"],
    capture_output=True, text=True,
)
if is_repo.returncode != 0:
    print(f"error: {common} is not a git repository (or does not exist).", file=sys.stderr)
    sys.exit(1)

has_commit = subprocess.run(
    ["git", "-C", common, "cat-file", "-e", base + "^{commit}"],
    capture_output=True, text=True,
)
if has_commit.returncode != 0:
    print(
        f"error: commit {base} (recorded in PROVENANCE.yaml as source_commit) was not found in {common}.\n"
        "This usually means the path points at the wrong repository, or the checkout is shallow.",
        file=sys.stderr,
    )
    sys.exit(1)

groups = {}
missing = []
for e in data["files"]:
    exists = subprocess.run(
        ["git", "-C", common, "cat-file", "-e", f"HEAD:{e['source']}"],
        capture_output=True, text=True,
    )
    if exists.returncode != 0:
        missing.append(e)
        continue

    args = ["git", "-C", common, "diff"] + (["--stat"] if mode == "--stat" else []) + [f"{base}..HEAD", "--", e["source"]]
    out = subprocess.run(args, capture_output=True, text=True).stdout.strip()
    if out:
        target_dir = e["target"].rsplit("/", 1)[0] if "/" in e["target"] else "."
        groups.setdefault(target_dir, []).append((e, out))

for target_dir in sorted(groups):
    print(f"# {target_dir}")
    for e, out in groups[target_dir]:
        print(f"== {e['target']}   (from {e['source']})\n{out}\n")

if missing:
    print("# Deleted or renamed upstream since the copy")
    for e in missing:
        print(f"== {e['target']}   (from {e['source']})\nsource no longer exists at HEAD in {common}\n")
PY
