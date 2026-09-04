#!/usr/bin/env bash
# Show what changed upstream (in common) in every file this repo was copied from.
# Usage: scripts/upstream-diff.sh /path/to/common   [--full for the whole diff]
set -euo pipefail
common="${1:?path to the common checkout}"; mode="${2:---stat}"
here="$(cd "$(dirname "$0")/.." && pwd)"
prov="$here/PROVENANCE.yaml"

# JSWARM_UPSTREAM_DIFF_PYTHON overrides the interpreter search entirely, ahead
# of the repo's own venv and the python3 fallback. It exists so this script
# can be tested in isolation (a scratch checkout has no venv of its own) and
# so anyone whose bare python3 lacks PyYAML can point at one that has it,
# without touching their PATH.
if [ -n "${JSWARM_UPSTREAM_DIFF_PYTHON:-}" ]; then
    py="$JSWARM_UPSTREAM_DIFF_PYTHON"
elif [ -x "$here/.venv/bin/python" ]; then
    py="$here/.venv/bin/python"
else
    py="python3"
fi
if ! "$py" -c "import yaml" >/dev/null 2>&1; then
    echo "error: no Python interpreter with PyYAML available (tried \$JSWARM_UPSTREAM_DIFF_PYTHON, $here/.venv/bin/python, and python3)." >&2
    echo "Install it with: $here/.venv/bin/python -m pip install pyyaml (or create the venv from requirements.txt), or set JSWARM_UPSTREAM_DIFF_PYTHON=/path/to/python-with-pyyaml." >&2
    exit 1
fi

"$py" - "$common" "$prov" "$mode" <<'PY'
import re
import subprocess
import sys
import yaml

common, prov, mode = sys.argv[1:4]

try:
    with open(prov) as f:
        data = yaml.safe_load(f)
except FileNotFoundError:
    print(f"error: {prov} not found. Run this from a copied jSwarm checkout that has PROVENANCE.yaml.", file=sys.stderr)
    sys.exit(1)

SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

raw_base = data.get("source_commit")
base = str(raw_base) if raw_base is not None else ""
if not SHA_RE.match(base):
    print(
        f"error: {prov} has an invalid source_commit ({raw_base!r}).\n"
        'Expected a 40-character hex commit sha, e.g. "3c36c4dbe5dc6c4662c1ff96916bba2cd4082d17".\n'
        "If you hand-authored this file, quote the value (source_commit: \"...\") so YAML does not "
        "parse an all-digit sha as a number.",
        file=sys.stderr,
    )
    sys.exit(1)

for e in data["files"]:
    e["source"] = str(e["source"])
    e["target"] = str(e["target"])

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
        target_dir = e["target"].rsplit("/", 1)[0] if "/" in e["target"] else "(repo root)"
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
