#!/usr/bin/env bash
# The clean-Mac reference run (task A7). Executed by a human on a separate,
# genuinely clean macOS user account -- this is the proof that a stranger's
# Mac, not just a developer's, can go from nothing to a working jSwarm.
#
# Before running this, the account must already have installed:
#   - Xcode command line tools    (xcode-select --install)
#   - Homebrew                    (https://brew.sh)
#   - git                         (brew install git)
#   - gh                          (brew install gh)            -- for step 9/10 (opening the merge PR)
#   - Python 3.12                 (brew install python@3.12)   -- `install.sh check` verifies this
#   - Node                        (brew install fnm && fnm install 24 && fnm use 24)
#                                  -- NOT verified by `install.sh check`; only the `portal` step
#                                  needs it (it builds decision-review-ui), and dies there by name
#                                  if missing. Installing it up front avoids a mid-run stop.
#   - the agent host (Claude Code): npm install -g @anthropic-ai/claude-code
#
# This script covers steps 1 to 4, 6, and 8 of the ten-step reference run.
# Steps 5 (restart the agent host), 7 (connect Jira), and 9 (run one real
# work item through the lifecycle) are a human's -- the script pauses for
# each. Step 10 (recording undocumented dependencies, confusing
# instructions, and manual recovery steps) is also the human's, afterward.
#
# Every failing command below is named: `set -euo pipefail` plus an ERR
# trap prints which step was running before the script exits non-zero, so
# a failure here doesn't require re-deriving where it happened.
set -euo pipefail

CURRENT_STEP="startup"
on_error() {
  local exit_code=$?
  echo "FAILED at: ${CURRENT_STEP} (exit ${exit_code})" >&2
  exit "$exit_code"
}
trap on_error ERR

step() {
  CURRENT_STEP="$*"
  echo "== ${CURRENT_STEP}"
}

PROOF_REPO="${PROOF_REPO:?set PROOF_REPO to the proof project clone URL}"
PROOF_KEY="${PROOF_KEY:-}"          # the Jira project key, empty to exercise the tracker-free path
export JSWARM_HOME="${JSWARM_HOME:-$HOME/dev/jswarm}"

step "1. clone jSwarm"
test -d "$JSWARM_HOME" || git clone https://github.com/LSA-Digital/jSwarm.git "$JSWARM_HOME"
cd "$JSWARM_HOME"

step "2. check"
./install.sh check

step "3. install dry run"
./install.sh install --dry-run
step "3b. assert dry run wrote nothing (no install.lock.yaml)"
test ! -f "$HOME/.jswarm/install.lock.yaml"

step "4. install"
./install.sh install
step "4b. assert install wrote the lock file"
test -f "$HOME/.jswarm/install.lock.yaml"
step "4c. assert the lock file records state: complete"
grep -q '^state: complete' "$HOME/.jswarm/install.lock.yaml"

step "5. restart the agent host now, then press return"
read -r _

step "6. verify"
./install.sh verify

step "7. connect Jira now if PROOF_KEY is set, then press return"
read -r _

step "8. adopt"
proof_dir="$HOME/dev/$(basename "$PROOF_REPO" .git)"
test -d "$proof_dir" || git clone "$PROOF_REPO" "$proof_dir"
if [ -n "$PROOF_KEY" ]; then ./install.sh adopt "$proof_dir" --jira-key "$PROOF_KEY"
else                         ./install.sh adopt "$proof_dir"; fi
step "8b. assert adopt wrote the .jswarm/.adopted marker"
test -f "$proof_dir/.jswarm/.adopted"

step "8c. portal up"
./install.sh portal --background

step "8d. probe http://localhost:8766/uat/"
# `portal --background` returns as soon as the server process is forked; the config
# render and UI build already finished synchronously before that point, but binding
# the port can lag by a beat. Retry briefly rather than treat the first miss as fatal
# -- a same-second race here would otherwise waste the whole trip to this machine.
portal_ok=0
for _ in 1 2 3 4 5; do
  if curl -fsS http://localhost:8766/uat/ >/dev/null 2>&1; then portal_ok=1; break; fi
  sleep 1
done
if [ "$portal_ok" -ne 1 ]; then
  ./install.sh portal --stop || true
  echo "FAILED at: ${CURRENT_STEP} -- portal never answered http://localhost:8766/uat/" >&2
  exit 1
fi

step "8e. portal down"
./install.sh portal --stop

step "8f. assert no leaked local paths in what got deployed"
# The needles below are built by concatenation so this script's own source never
# contains them contiguous: this file is itself scanned by the leak gate, and the
# point of this check is to find these strings in a scanned TARGET (the deployed
# skills / the adopted project), never to leak them from the gate's own inputs
# (same convention as tests/test_leakgate.py).
leak_needle_1="dev""/common"
leak_needle_2="/Us""ers/"
if grep -rlE "${leak_needle_1}|${leak_needle_2}" "$HOME/.claude/skills" "$proof_dir/.claude" 2>/dev/null; then
  echo "FAILED at: ${CURRENT_STEP} -- leaked path(s) found in the file(s) listed above" >&2
  exit 1
fi

echo
echo "clean-Mac smoke: OK. Step 9 is yours: open an agent session in $proof_dir and run one"
echo "work item through /jPlan -> /jGo -> /jTest -> /jUAT -> /jFix -> /jClose -> /jMerge."
echo "Step 10: record every undocumented dependency, confusing instruction, and manual recovery step."
