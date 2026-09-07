#!/usr/bin/env bash
#
# install.sh: the jSwarm installer.
#
#   check                                         prerequisites, read-only, safe anywhere
#   install [--with-colgrep] [--dry-run]
#   verify
#   adopt <repo-path> [--jira-key KEY] [--no-hooks] [--dry-run]
#   unadopt <repo-path> [--dry-run]
#   upgrade [--dry-run]
#   uninstall [--keep-backups] [--dry-run]
#   portal [--background] | portal --stop
#
# Every subcommand that writes anything takes --dry-run, and --dry-run
# writes nothing at all: it is decided once, in jswarm.installer.fsops, and
# every write anywhere downstream (lock file, backups, skills, project
# adoption) goes through that one decision.
#
# Portable: resolves its own location as JSWARM_HOME (unless the caller has
# already set JSWARM_HOME, in which case that wins). Fail-closed: set -euo
# pipefail. Global writes go to $HOME only through jswarm.installer, which
# backs up first.
set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SCRIPT_SOURCE" ]; do
  d="$(cd -P "$(dirname "$SCRIPT_SOURCE")" && pwd)"
  SCRIPT_SOURCE="$(readlink "$SCRIPT_SOURCE")"; [[ "$SCRIPT_SOURCE" != /* ]] && SCRIPT_SOURCE="$d/$SCRIPT_SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_SOURCE")" && pwd)"
export JSWARM_HOME="${JSWARM_HOME:-$SCRIPT_DIR}"

VENV_PY="$JSWARM_HOME/.venv/bin/python"

step() { printf '\n\033[1;36m> %s\033[0m\n' "$*"; }
ok()   { printf '  \033[0;32mok\033[0m %s\n' "$*"; }
warn() { printf '  \033[0;33m!!\033[0m %s\n' "$*"; }
err()  { printf '  \033[0;31mERR\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
  cat <<EOF
install.sh: the jSwarm installer

Usage:
  $(basename "$0") check
  $(basename "$0") install [--with-colgrep] [--dry-run]
  $(basename "$0") verify
  $(basename "$0") adopt <repo-path> [--jira-key KEY] [--no-hooks] [--dry-run]
  $(basename "$0") unadopt <repo-path> [--dry-run]
  $(basename "$0") upgrade [--dry-run]
  $(basename "$0") uninstall [--keep-backups] [--dry-run]
  $(basename "$0") portal [--background]
  $(basename "$0") portal --stop

Resolved jSwarm clone: $JSWARM_HOME
EOF
}

# Resolve a python usable for read-only, dependency-free calls (check).
# Prefers the clone's own venv; falls back to any python3 on PATH. Never
# fatal on its own -- callers that need real work (a yaml-backed subcommand)
# check the result themselves.
resolve_py_readonly() {
  { [[ -x "$VENV_PY" ]] && echo "$VENV_PY"; } || command -v python3.12 || command -v python3 || true
}

# Resolve a python with this project's dependencies (PyYAML in particular)
# importable -- i.e. one that came from a completed `install` venv step.
# Empty output means "no working install"; every subcommand that needs this
# treats that identically to a missing lock file.
resolve_py_or_empty() {
  local py; py="$(resolve_py_readonly)"
  [[ -n "$py" ]] || { echo ""; return; }
  "$py" -c "import yaml" >/dev/null 2>&1 && echo "$py" || echo ""
}

# Not exit-on-failure itself: a function invoked inside a command
# substitution runs in a subshell, where `exit` would only end that
# subshell and silently leave the caller with an empty $py under `set -e`
# quirks that are easy to get wrong. Each call site checks the result and
# dies itself instead.
die_if_not_installed() {
  local who="$1" py="$2"
  if [[ -z "$py" ]]; then
    err "$who: no working install found."
    echo "Next: $(basename "$0") install   (here, in the jSwarm clone)"
    exit 1
  fi
}

# ---------------------------------------------------------------- check
cmd_check() {
  [[ $# -eq 0 ]] || die "check: unknown option: $1"
  local py; py="$(resolve_py_readonly)"
  [[ -n "$py" ]] || die "check: no Python 3 found on PATH. Install Python 3.12+ with venv support (see docs/platforms.md)"
  ( cd "$JSWARM_HOME" && "$py" -m jswarm.installer.cli check )
}

# ---------------------------------------------------------------- install
cmd_install() {
  local dry=0 colgrep=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) dry=1 ;;
      --with-colgrep) colgrep=1 ;;
      *) die "install: unknown option: $1" ;;
    esac
    shift
  done

  local dryflag=""; [[ "$dry" -eq 1 ]] && dryflag="--dry-run"
  local colgrepflag=""; [[ "$colgrep" -eq 1 ]] && colgrepflag="--with-colgrep"

  local venv_ready=0
  if [[ -x "$VENV_PY" ]] && "$VENV_PY" -c "import yaml, psutil" >/dev/null 2>&1; then
    venv_ready=1
  fi

  if [[ "$venv_ready" -eq 0 ]]; then
    if [[ "$dry" -eq 1 ]]; then
      step "install: preview (no venv yet)"
      warn "DRY-RUN: nothing under \$HOME will be written."
      echo "  would write: ${HOME:-\$HOME}/.jswarm/install.lock.yaml"
      echo "  would create: .venv using Python 3.12+ and install requirements.txt into it"
      echo "  would run  : skills -> ~/.claude/skills"
      echo "  would run  : portal_config -> ~/.jswarm/decision-review/config.json"
      if [[ "$colgrep" -eq 1 ]]; then
        echo "  would run  : cargo install colgrep (skipped if already on PATH)"
        echo "  would run  : register the colgrep MCP server (colgrep_search, colgrep_list_dev_indices) with the agent host"
        echo "  would run  : colgrep-search, code-overview -> ~/.claude/skills"
        echo "  colgrep    : requires a Rust toolchain (cargo); check reports it, run: $(basename "$0") check"
      fi
      ok "DRY-RUN complete. Re-run without --dry-run to apply."
      echo "Next: $(basename "$0") install   (here, in the jSwarm clone) to apply it."
      return 0
    fi
    step "install: python venv ($JSWARM_HOME/.venv)"
    local bootstrap_py=""
    for candidate in python3.12 python3; do
      if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
        bootstrap_py="$candidate"; break
      fi
    done
    [[ -n "$bootstrap_py" ]] || die "install: Python 3.12+ with venv support is required. See docs/platforms.md, then re-run: $(basename "$0") install"
    ( cd "$JSWARM_HOME" && "$bootstrap_py" -m venv .venv && .venv/bin/python -m pip install -q --upgrade pip && .venv/bin/python -m pip install -q -r requirements.txt )
    ok "venv created"
  fi

  step "install: skills, portal_config${colgrep:+, colgrep}"
  ( cd "$JSWARM_HOME" && "$VENV_PY" -m jswarm.installer.cli install $dryflag $colgrepflag )
}

# ---------------------------------------------------------------- verify
cmd_verify() {
  [[ $# -eq 0 ]] || die "verify: unknown option: $1"
  local py; py="$(resolve_py_or_empty)"
  if [[ -z "$py" ]]; then
    err "verify: no install found."
    echo "Next: $(basename "$0") install   (here, in the jSwarm clone)"
    return 1
  fi
  ( cd "$JSWARM_HOME" && "$py" -m jswarm.installer.cli verify )
}

# ---------------------------------------------------------------- adopt
cmd_adopt() {
  [[ $# -ge 1 ]] || die "usage: adopt <repo-path> [--jira-key KEY] [--no-hooks] [--dry-run]"
  local repo="$1"; shift
  local jira_key="" no_hooks=0 dry=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --jira-key) [[ $# -ge 2 ]] || die "adopt: --jira-key needs a value"; jira_key="$2"; shift ;;
      --no-hooks) no_hooks=1 ;;
      --dry-run) dry=1 ;;
      *) die "adopt: unknown option: $1" ;;
    esac
    shift
  done
  local py; py="$(resolve_py_or_empty)"; die_if_not_installed adopt "$py"
  local args=(adopt "$repo")
  [[ -n "$jira_key" ]] && args+=(--jira-key "$jira_key")
  [[ "$no_hooks" -eq 1 ]] && args+=(--no-hooks)
  [[ "$dry" -eq 1 ]] && args+=(--dry-run)
  step "adopt: $repo"
  ( cd "$JSWARM_HOME" && "$py" -m jswarm.installer.cli "${args[@]}" )
}

# ---------------------------------------------------------------- unadopt
cmd_unadopt() {
  [[ $# -ge 1 ]] || die "usage: unadopt <repo-path> [--dry-run]"
  local repo="$1"; shift
  local dry=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) dry=1 ;;
      *) die "unadopt: unknown option: $1" ;;
    esac
    shift
  done
  local py; py="$(resolve_py_or_empty)"; die_if_not_installed unadopt "$py"
  local args=(unadopt "$repo")
  [[ "$dry" -eq 1 ]] && args+=(--dry-run)
  step "unadopt: $repo"
  ( cd "$JSWARM_HOME" && "$py" -m jswarm.installer.cli "${args[@]}" )
}

# ---------------------------------------------------------------- upgrade
cmd_upgrade() {
  local dry=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) dry=1 ;;
      *) die "upgrade: unknown option: $1" ;;
    esac
    shift
  done
  local py; py="$(resolve_py_or_empty)"; die_if_not_installed upgrade "$py"
  local args=(upgrade)
  [[ "$dry" -eq 1 ]] && args+=(--dry-run)
  step "upgrade"
  ( cd "$JSWARM_HOME" && "$py" -m jswarm.installer.cli "${args[@]}" )
}

# ---------------------------------------------------------------- uninstall
cmd_uninstall() {
  local keep=0 dry=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --keep-backups) keep=1 ;;
      --dry-run) dry=1 ;;
      *) die "uninstall: unknown option: $1" ;;
    esac
    shift
  done
  local py; py="$(resolve_py_or_empty)"
  if [[ -z "$py" ]]; then
    ok "uninstall: nothing found to remove (no working install)."
    echo "Next: nothing further required -- there is nothing installed."
    return 0
  fi
  local args=(uninstall)
  [[ "$keep" -eq 1 ]] && args+=(--keep-backups)
  [[ "$dry" -eq 1 ]] && args+=(--dry-run)
  step "uninstall"
  ( cd "$JSWARM_HOME" && "$py" -m jswarm.installer.cli "${args[@]}" )
}

# ---------------------------------------------------------------- portal
cmd_portal_stop() {
  local py; py="$(resolve_py_or_empty)"; die_if_not_installed portal "$py"
  ( cd "$JSWARM_HOME" && "$py" -m jswarm.portal.process --stop "$@" )
}

cmd_portal() {
  local dry=0 bg=0 stop=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) dry=1 ;;
      --background) bg=1 ;;
      --stop) stop=1 ;;
      *) die "portal: unknown option: $1" ;;
    esac
    shift
  done
  if [[ "$stop" -eq 1 ]]; then
    step "portal: stop"
    if [[ "$dry" -eq 1 ]]; then cmd_portal_stop --dry-run; else cmd_portal_stop; fi
    return 0
  fi
  local py; py="$(resolve_py_or_empty)"; die_if_not_installed portal "$py"
  local cfg="$HOME/.jswarm/decision-review/config.json"

  step "portal 1/3: config -> $cfg"
  if [[ -f "$cfg" ]]; then
    ok "using existing config $cfg"
  elif [[ "$dry" -eq 1 ]]; then
    warn "would render config for the portal server on 8766"
  else
    ( cd "$JSWARM_HOME" && "$py" -m jswarm.portal.render_portal_config --out "$cfg" )
  fi

  step "portal 2/3: build portal UI (Node)"
  if [[ "$dry" -eq 1 ]]; then
    warn "would run: npm ci --ignore-scripts && npm run build (jswarm.portal.render_ui --build-only)"
  else
    command -v node >/dev/null 2>&1 || die "portal: Node is required to build the UI. Install Node 24 with npm (any installation method; see docs/platforms.md)"
    ( cd "$JSWARM_HOME" && "$py" -m jswarm.portal.render_ui --build-only )
  fi

  step "portal 3/3: start server"
  local args=()
  [[ "$bg" -eq 1 ]] && args+=(--background)
  [[ "$dry" -eq 1 ]] && args+=(--dry-run)
  ( cd "$JSWARM_HOME" && "$py" -m jswarm.portal.process ${args[@]+"${args[@]}"} )
}

# ---------------------------------------------------------------- dispatch
main() {
  [[ $# -ge 1 ]] || { usage; exit 2; }
  case "$1" in
    check) shift; cmd_check "$@" ;;
    install) shift; cmd_install "$@" ;;
    verify) shift; cmd_verify "$@" ;;
    adopt) shift; cmd_adopt "$@" ;;
    unadopt) shift; cmd_unadopt "$@" ;;
    upgrade) shift; cmd_upgrade "$@" ;;
    uninstall) shift; cmd_uninstall "$@" ;;
    portal) shift; cmd_portal "$@" ;;
    -h|--help|help) usage ;;
    *) err "unknown command: $1"; usage; exit 2 ;;
  esac
}
main "$@"
