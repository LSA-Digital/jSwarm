#!/usr/bin/env bash
#
# install-jarviswarm.sh: JarviSWARM day-0 installer (COM-398).
#
#   check                      preflight only, prints fix commands, writes nothing
#   install [--dry-run] [--with-hud] [--provider yaml|claude-only] [--jira|--no-jira]
#   adopt <repo> [--jira-key KEY] [--no-hooks] [--dry-run]
#   portal [--dry-run] [--background] [--force-render] | portal --stop
#   verify
#
# Portable: COMMON_DIR resolves from this script's own location. Idempotent.
# Fail-closed: set -euo pipefail. Everything writes to $HOME only via the
# controlled-config engine or jswarm/installer/day0.py, both of which back up.
set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SCRIPT_SOURCE" ]; do
  d="$(cd -P "$(dirname "$SCRIPT_SOURCE")" && pwd)"
  SCRIPT_SOURCE="$(readlink "$SCRIPT_SOURCE")"; [[ "$SCRIPT_SOURCE" != /* ]] && SCRIPT_SOURCE="$d/$SCRIPT_SOURCE"
done
COMMON_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")/.." && pwd)"
export JSWARM_COMMON="${JSWARM_COMMON:-$COMMON_DIR}"

VENV_PY="$COMMON_DIR/.venv/bin/python"
DEPLOY_PY="$COMMON_DIR/jswarm/deploy/deploy.py"
MASTER_ROOT="$COMMON_DIR/docs/_CONTROLLED_CONFIG/dotclaude"
ATLASSIAN_MCP_URL="https://mcp.atlassian.com/v2/mcp"
NONINTERACTIVE="${JSWARM_NONINTERACTIVE:-0}"

step() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[0;32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[0;33m⚠\033[0m %s\n' "$*"; }
err()  { printf '  \033[0;31m✗\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }
fix()  { printf '      fix: %s\n' "$*"; }
# item_err: per-prerequisite failure in `check`'s report. Unlike err() (reserved
# for fatal/CLI-level failures on stderr: require_macos, die, unknown command),
# this is one row of an informational report and belongs on stdout alongside
# ok()/warn() so the whole "check" output reads as a single report.
item_err() { printf '  \033[0;31m✗\033[0m %s\n' "$*"; }

usage() {
  cat <<EOF
install-jarviswarm.sh: JarviSWARM day-0 installer

Usage:
  $(basename "$0") check
  $(basename "$0") install [--dry-run] [--with-hud] [--provider yaml|claude-only] [--jira|--no-jira]
  $(basename "$0") adopt <repo> [--jira-key KEY] [--no-hooks] [--dry-run]
  $(basename "$0") portal [--dry-run] [--background] [--force-render]
  $(basename "$0") portal --stop
  $(basename "$0") verify

Notes:
  --provider defaults to yaml, which keeps whatever agent routing this machine
  already has. On a fresh Claude-only machine pass --provider claude-only.
  portal --stop kills the background portal and removes its pid files.

Resolved common checkout: $COMMON_DIR
Docs: https://jarviswarm.com/docs/getting-started
EOF
}

require_macos() {
  local u; u="${JSWARM_FAKE_UNAME:-$(uname -s)}"
  if [[ "$u" != "Darwin" ]]; then
    err "This installer supports macOS only (detected: $u). Linux and Windows are not on the day-0 path yet."
    exit 2
  fi
}

# ---------------------------------------------------------------- check
cmd_check() {
  require_macos
  local missing=0
  step "check: prerequisites"
  ok "macOS ($(sw_vers -productVersion 2>/dev/null || echo unknown), $(uname -m))"

  if xcode-select -p >/dev/null 2>&1; then ok "Xcode Command Line Tools"; else item_err "Xcode Command Line Tools missing"; fix "xcode-select --install"; missing=1; fi
  if command -v brew >/dev/null 2>&1; then ok "Homebrew ($(command -v brew))"; else item_err "Homebrew missing"; fix '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'; missing=1; fi
  if command -v python3.12 >/dev/null 2>&1; then ok "Python 3.12 ($(python3.12 --version 2>&1))"; else item_err "Python 3.12 missing"; fix "brew install python@3.12"; missing=1; fi
  if [[ -d "$JSWARM_COMMON/.git" ]]; then ok "common checkout ($JSWARM_COMMON)"; else item_err "common checkout missing at $JSWARM_COMMON"; fix "gh auth login && git clone https://github.com/LSA-Digital/common ${JSWARM_HOME:-$HOME/dev/jswarm}"; missing=1; fi
  if command -v claude >/dev/null 2>&1; then ok "Claude Code ($(claude --version 2>/dev/null | head -1))"; else item_err "Claude Code CLI missing"; fix "see https://code.claude.com/docs/en/setup"; missing=1; fi
  if [[ -n "$(git config --global user.name 2>/dev/null)" && -n "$(git config --global user.email 2>/dev/null)" ]]; then ok "git identity ($(git config --global user.name))"; else item_err "git identity missing"; fix 'git config --global user.name "Your Name" && git config --global user.email you@example.com'; missing=1; fi
  if command -v fnm >/dev/null 2>&1 && command -v node >/dev/null 2>&1 && [[ "$(node --version)" == v24* ]]; then ok "fnm + Node 24 ($(node --version))"; else warn "fnm + Node 24 not found (needed for 'portal' and --with-hud, optional otherwise)"; fix 'brew install fnm && fnm install 24 && fnm default 24   # then add: eval "$(fnm env --use-on-cd)" to ~/.zshrc'; fi

  echo
  if [[ "$missing" -eq 1 ]]; then err "check: required items missing (see fix lines above)"; return 1; fi
  ok "check: all required prerequisites present"
}

# ---------------------------------------------------------------- install
cmd_install() {
  require_macos
  # provider defaults to yaml: `claude-only` rewrites and prunes ~/.claude/agents,
  # which silently drops an existing machine's GPT/GLM routing. Day-0 newcomers
  # are told to pass --provider claude-only explicitly (README, Getting started).
  local dry=0 hud=0 provider="yaml" jira="ask"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) dry=1 ;; --with-hud) hud=1 ;;
      --provider)
        [[ $# -ge 2 ]] || die "--provider needs a value: yaml or claude-only"
        provider="$2"
        case "$provider" in
          claude-only|yaml) ;;
          *) die "--provider must be yaml or claude-only (got: $provider)" ;;
        esac
        shift
        ;;
      --jira) jira="yes" ;; --no-jira) jira="no" ;;
      *) die "unknown flag for install: $1" ;;
    esac; shift
  done
  [[ "$dry" -eq 1 ]] && warn "DRY-RUN: nothing under \$HOME will be written."
  cmd_check || die "install: fix the prerequisites first"

  step "install 1/6: python venv ($COMMON_DIR/.venv)"
  # venv_ready tracks whether $VENV_PY can import the locked dependencies
  # (PyYAML in particular). Steps 4 and 5 below shell out to deploy.py and
  # regenerate_claude_agents.py, both of which import yaml at module load;
  # on a fresh clone with no venv, --dry-run must not fall back to a bare
  # system python3.12 for those steps (it lacks PyYAML and crashes).
  #
  # Install from requirements.lock (not requirements.txt): jsetup bootstrap's
  # own check verifies installed versions against the exact pins in
  # requirements.lock (jswarm/installer/jsetup/bootstrap.py's
  # lock_source_consistency/_verify_with_steps), and repair_bootstrap installs
  # from the same lock file internally. Installing from requirements.txt's
  # loose `~=` specifiers can resolve newer releases than the lock pins and
  # make bootstrap's own "deps" check fail, so match its own convention here.
  local venv_ready=0
  if [[ -x "$VENV_PY" ]] && "$VENV_PY" -c "import yaml" >/dev/null 2>&1; then
    ok "venv present"
    venv_ready=1
  elif [[ "$dry" -eq 1 ]]; then
    warn "no venv yet - would run: python3.12 -m venv .venv && .venv/bin/python -m pip install -q -r requirements.lock && .venv/bin/python -m jswarm.installer.jsetup bootstrap"
  else
    ( cd "$COMMON_DIR" && python3.12 -m venv .venv && .venv/bin/python -m pip install -q --upgrade pip && .venv/bin/python -m pip install -q -r requirements.lock && .venv/bin/python -m jswarm.installer.jsetup bootstrap )
    ok "venv bootstrapped"
    venv_ready=1
  fi
  local PY; PY="$( [[ -x "$VENV_PY" ]] && echo "$VENV_PY" || command -v python3.12 )"

  step "install 2/6: ~/.claude/settings.json (env.JSWARM_COMMON)"
  # dryflag is a plain string (not an array): bash 3.2 (macOS default) treats a
  # *declared-but-empty* indexed array as unset under `set -u`, so
  # "${dryflag[@]}" would abort with "unbound variable" when --dry-run is not
  # passed. It is always zero-or-one bare words, so unquoted expansion below is
  # intentional and safe.
  local dryflag=""; [[ "$dry" -eq 1 ]] && dryflag="--dry-run"
  ( cd "$COMMON_DIR" && "$PY" -m jswarm.installer.day0 settings-merge --common "$COMMON_DIR" $dryflag )

  step "install 3/6: ~/.claude/CLAUDE.md JSWARM block"
  ( cd "$COMMON_DIR" && "$PY" -m jswarm.installer.day0 claude-md-sync --common "$COMMON_DIR" $dryflag )

  step "install 4/6: skills, hooks, rules → ~/.claude (controlled-config engine, user context)"
  local ROOTS=(--master-root "$MASTER_ROOT" --common-root "$COMMON_DIR/.claude" --home-root "$HOME/.claude" --catalog-root "$COMMON_DIR")
  local inspect_cmd="$PY $DEPLOY_PY dry-run ${ROOTS[*]} --only-context user --live-home --format text"

  if [[ "$dry" -eq 1 && "$venv_ready" -eq 0 ]]; then
    warn "skipped: needs the venv (deploy.py imports PyYAML). Run install to create the venv first, then re-run --dry-run to preview this step."
  else
  # deploy.py dry-run exits 1 when the plan has blocking entries (deploy.py:2727).
  # That is informational on its own, but it also means apply would be refused,
  # so capture the exit code by hand (set -e would otherwise kill the whole
  # install on a nonzero code from inside a pipeline) rather than let it pass
  # silently or abort with no message.
  local dr_out; dr_out="$(mktemp)"
  local dr_rc=0
  ( cd "$COMMON_DIR" && "$PY" "$DEPLOY_PY" dry-run "${ROOTS[@]}" --only-context user --live-home --format text ) >"$dr_out" 2>&1 || dr_rc=$?
  tail -3 "$dr_out"
  if [[ "$dr_rc" -ne 0 ]]; then
    rm -f "$dr_out"
    die "install 4/6: deploy plan has blocking entries (dry-run exit $dr_rc); apply would be refused. Inspect with: $inspect_cmd"
  fi
  rm -f "$dr_out"

  if [[ "$dry" -eq 0 ]]; then
    local previews="$HOME/.jswarm/backups/controlled-config/dotclaude/previews"
    local wp_out; wp_out="$(mktemp)"
    local wp_rc=0
    ( cd "$COMMON_DIR" && "$PY" "$DEPLOY_PY" dry-run "${ROOTS[@]}" --only-context user --live-home --write-preview ) >"$wp_out" 2>&1 || wp_rc=$?
    if [[ "$wp_rc" -ne 0 ]]; then
      tail -3 "$wp_out"
      rm -f "$wp_out"
      die "install 4/6: deploy plan has blocking entries (dry-run --write-preview exit $wp_rc); apply would be refused. Inspect with: $inspect_cmd"
    fi
    rm -f "$wp_out"
    # `|| true` on the assignment: under `set -e` with pipefail an empty preview
    # directory makes the pipeline exit nonzero and kills the script with no
    # message, so the explicit die below would never be reached.
    local pid; pid="$(ls -t "$previews"/run-*.preview.json 2>/dev/null | head -1 | xargs -I{} basename {} .preview.json)" || true
    [[ -n "$pid" ]] || die "no preview minted under $previews"
    ( cd "$COMMON_DIR" && "$PY" "$DEPLOY_PY" apply "${ROOTS[@]}" --only-context user --live-home --from-preview "$pid" >/dev/null )
    ( cd "$COMMON_DIR" && "$PY" "$DEPLOY_PY" verify "${ROOTS[@]}" --only-context user --live-home >/dev/null )
    ok "deployed and verified (preview $pid)"
  fi
  fi

  step "install 5/6: ~/.claude/agents (provider: $provider)"
  if [[ "$dry" -eq 1 && "$venv_ready" -eq 0 ]]; then
    warn "skipped: needs the venv (regenerate_claude_agents.py imports PyYAML). Run install to create the venv first, then re-run --dry-run to preview this step."
  elif [[ "$dry" -eq 1 ]]; then
    ( cd "$COMMON_DIR" && "$PY" jswarm/regenerate_claude_agents.py --provider "$provider" | tail -3 )
  else
    # The render rewrites and prunes ~/.claude/agents. Copy the directory aside
    # first so a machine that had its own agent definitions can get them back.
    if [[ -d "$HOME/.claude/agents" ]]; then
      local agents_bak="$HOME/.claude/agents.bak-$(date +%Y%m%d-%H%M%S)"
      cp -R "$HOME/.claude/agents" "$agents_bak" || die "install 5/6: could not back up ~/.claude/agents to $agents_bak"
      ok "backed up ~/.claude/agents -> $(basename "$agents_bak")"
    fi
    ( cd "$COMMON_DIR" && "$PY" jswarm/regenerate_claude_agents.py --provider "$provider" --apply )
  fi

  step "install 6/6: Jira (Atlassian hosted MCP)"
  if [[ "$jira" == "ask" && "$NONINTERACTIVE" == "0" && "$dry" -eq 0 ]]; then
    read -r -p "  Register the Atlassian hosted MCP for Jira now? [y/N] " a; [[ "$a" =~ ^[Yy] ]] && jira="yes" || jira="no"
  fi
  if [[ "$jira" == "yes" && "$dry" -eq 0 ]]; then
    if claude mcp list 2>/dev/null | grep -q '^atlassian'; then ok "atlassian MCP already registered"; else
      claude mcp add --scope user --transport http atlassian "$ATLASSIAN_MCP_URL" && ok "registered atlassian MCP (user scope)"; fi
    warn "Finish sign-in: start 'claude' in any folder and run /mcp, pick atlassian, complete the browser login."
  else
    warn "Jira skipped. Later: claude mcp add --scope user --transport http atlassian $ATLASSIAN_MCP_URL"
  fi

  if [[ "$hud" -eq 1 && "$dry" -eq 0 ]]; then
    step "HUD (ccstatusline)"
    command -v node >/dev/null 2>&1 || die "HUD needs fnm Node 24 (see check)"
    npm install -g ccstatusline@2.2.19 >/dev/null
    local nb; nb="$(dirname "$(readlink -f "$(command -v node)")")"
    ( cd "$COMMON_DIR" && "$PY" -m jswarm.installer.day0 settings-merge --common "$COMMON_DIR" \
        --status-line-json "{\"type\":\"command\",\"command\":\"PATH=\\\"$nb:\$PATH\\\" CCSTATUSLINE_WIDTH=\\\"\${COLUMNS:-200}\\\" ccstatusline\",\"refreshInterval\":10,\"padding\":0}" )
    ok "statusLine configured"
  fi

  echo
  if [[ "$dry" -eq 1 ]]; then ok "DRY-RUN complete. Re-run without --dry-run to apply."; else
    ok "JarviSWARM installed. RESTART Claude Code sessions now, then run: $(basename "$0") verify"; fi
}

# ---------------------------------------------------------------- verify
cmd_verify() {
  step "verify"
  # `|| true` on the assignment: with no venv and no system python3.12/python3 the
  # command substitution exits nonzero and `set -e` would kill verify with no message.
  local PY; PY="$( [[ -x "$VENV_PY" ]] && echo "$VENV_PY" || command -v python3.12 || command -v python3 )" || true
  [[ -n "$PY" ]] || die "verify: no Python found (expected $VENV_PY, python3.12, or python3). Run 'install' first."
  local rc=0
  ( cd "$COMMON_DIR" && "$PY" -m jswarm.installer.day0 verify --common "$COMMON_DIR" ) || rc=1
  if command -v claude >/dev/null 2>&1 && claude mcp list 2>/dev/null | grep -q '^atlassian'; then
    printf '%-28s  ON   %s\n' "Jira (atlassian MCP)" "registered; run /mcp in a session to confirm sign-in"
  else
    printf '%-28s  OFF  %s\n' "Jira (atlassian MCP)" "not registered (install --jira, or Integrations page)"
  fi
  return $rc
}

# ---------------------------------------------------------------- adopt
cmd_adopt() {
  require_macos
  [[ $# -ge 1 ]] || die "usage: adopt <repo> [--jira-key KEY] [--no-hooks] [--dry-run]"
  local repo="$1"; shift
  local PY; PY="$( [[ -x "$VENV_PY" ]] && echo "$VENV_PY" || die "run 'install' first (no venv)" )"
  step "adopt: $repo"
  ( cd "$COMMON_DIR" && "$PY" -m jswarm.installer.day0 adopt "$repo" --common "$COMMON_DIR" "$@" )
  if [[ " $* " != *" --dry-run "* ]]; then
    ok "adopted. In that repo, start Claude Code and run /jPlan to open your first ticket."
  fi
}
# portal --stop: the background portal writes server.pid, and the launchd-style
# service path writes service.pid. Stop both and remove the files, so the pid
# the start-up line printed is actually actionable.
cmd_portal_stop() {
  local dir="$HOME/.jswarm/decision-review"
  local found=0
  local f p pid
  for f in server.pid service.pid; do
    p="$dir/$f"
    [[ -f "$p" ]] || continue
    found=1
    pid="$(tr -d '[:space:]' <"$p" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      if kill "$pid" 2>/dev/null; then ok "stopped portal pid $pid ($f)"; else warn "could not stop pid $pid ($f); kill it by hand"; fi
    else
      warn "$f held pid '${pid:-<empty>}', which is not running; removing the stale file"
    fi
    rm -f "$p"
  done
  [[ "$found" -eq 1 ]] || ok "no portal pid file under $dir; nothing to stop"
}

cmd_portal() {
  require_macos
  local dry=0 bg=0 force_render=0 stop=0
  while [[ $# -gt 0 ]]; do case "$1" in
    --dry-run) dry=1 ;; --background) bg=1 ;; --force-render) force_render=1 ;; --stop) stop=1 ;;
    *) die "unknown flag for portal: $1" ;;
  esac; shift; done
  if [[ "$stop" -eq 1 ]]; then
    step "portal: stop"
    cmd_portal_stop
    return 0
  fi
  local PY; PY="$( [[ -x "$VENV_PY" ]] && echo "$VENV_PY" || die "run 'install' first (no venv)" )"
  local cfg="$HOME/.jswarm/decision-review/config.json"

  step "portal 1/3: render config -> $cfg (jswarm.portal.render_portal_config)"
  # Re-rendering resets active_round_sources, so an existing config is left
  # alone unless the caller asks for a fresh render.
  if [[ -f "$cfg" && "$force_render" -eq 0 ]]; then
    ok "using existing config $cfg (--force-render re-renders it; that resets active_round_sources)"
  elif [[ "$dry" -eq 1 ]]; then
    warn "would render config for the single portal server on 8766"
  else
    ( cd "$COMMON_DIR" && "$PY" -m jswarm.portal.render_portal_config --out "$cfg" )
  fi

  step "portal 2/3: build decision-review-ui (fnm Node 24)"
  if [[ "$dry" -eq 1 ]]; then
    warn "would run: npm ci --ignore-scripts && npm run build in decision-review-ui via jswarm.portal.render_ui --build-only"
  else
    command -v node >/dev/null 2>&1 || die "Node 24 via fnm is required: brew install fnm && fnm install 24"
    local build_out; build_out="$(mktemp)"
    local build_rc=0
    ( cd "$COMMON_DIR" && "$PY" -m jswarm.portal.render_ui --build-only ) >"$build_out" 2>&1 || build_rc=$?
    tail -3 "$build_out"
    if [[ "$build_rc" -ne 0 ]]; then
      rm -f "$build_out"
      die "portal 2/3: decision-review-ui build failed (exit $build_rc)"
    fi
    rm -f "$build_out"
  fi

  # jswarm.portal.server runs ONE listener on the config's "port"
  # field (default 8766), serving the built UI and the API together; the
  # config's 8765 CORS origin entry is unused by this single-process server.
  step "portal 3/3: serve http://localhost:8766/uat/"
  local port_pid; port_pid="$(lsof -ti:8766 2>/dev/null | head -1 || true)"
  if [[ -n "$port_pid" && "$dry" -eq 0 ]]; then
    die "port 8766 is in use by pid $port_pid; stop it or pass a different port in $cfg"
  fi
  if [[ "$dry" -eq 1 ]]; then
    local mode="foreground"; [[ "$bg" -eq 1 ]] && mode="background"
    warn "would run in the $mode: $PY -m jswarm.portal.server --config $cfg  (single server, UI+API on 8766)"
    return 0
  fi
  if [[ "$bg" -eq 1 ]]; then
    mkdir -p "$HOME/.jswarm/decision-review"
    ( cd "$COMMON_DIR" && nohup "$PY" -m jswarm.portal.server --config "$cfg" >"$HOME/.jswarm/decision-review/server.log" 2>&1 & echo $! >"$HOME/.jswarm/decision-review/server.pid" )
    ok "started in background (pid $(cat "$HOME/.jswarm/decision-review/server.pid")); log: ~/.jswarm/decision-review/server.log"
    ok "stop it with: $(basename "$0") portal --stop"
  else
    ok "starting in the foreground (Ctrl-C to stop)"
    ( cd "$COMMON_DIR" && exec "$PY" -m jswarm.portal.server --config "$cfg" )
  fi
}

# ---------------------------------------------------------------- dispatch
main() {
  [[ $# -ge 1 ]] || { usage; exit 2; }
  case "$1" in
    check) shift; cmd_check "$@" ;;
    install) shift; cmd_install "$@" ;;
    adopt) shift; cmd_adopt "$@" ;;      # Task 9
    portal) shift; cmd_portal "$@" ;;    # Task 10
    verify) shift; cmd_verify "$@" ;;
    -h|--help|help) usage ;;
    *) err "unknown command: $1"; usage; exit 2 ;;
  esac
}
main "$@"
