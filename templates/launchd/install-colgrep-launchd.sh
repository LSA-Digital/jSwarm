#!/bin/bash
# install-colgrep-launchd.sh — install/refresh the ColGREP launchd agents from the
# version-controlled source of truth in this directory into ~/Library/LaunchAgents.
#
# These agents were previously ONLY in ~/Library/LaunchAgents (untracked) — if lost,
# the self-healing watchdog silently disappeared. This makes them reproducible.
#
#   com.colgrep.health-check              — self-healing next-plaid-api watchdog (StartInterval 60s)
#   com.colgrep.watcher                   — overlay-freshness fswatch watcher (KeepAlive)
#   com.colgrep.init-aris                 — ARIS content index init at load
#   com.colgrep.overlay-fleet-supervisor  — per-worktree overlay supervisor fleet parent (KeepAlive)
#   com.colgrep.freshness-probe           — COM-349 dual-primary standing freshness probe (StartInterval)
#
# Usage: bash deploy/launchd/install-colgrep-launchd.sh [--dry-run]
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HOME/Library/LaunchAgents"
AGENTS=(com.colgrep.health-check com.colgrep.watcher com.colgrep.init-aris com.colgrep.overlay-fleet-supervisor com.colgrep.freshness-probe)
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
UID_NUM="$(id -u)"

mkdir -p "$DEST_DIR"
for a in "${AGENTS[@]}"; do
    src="$SRC_DIR/$a.plist"; dest="$DEST_DIR/$a.plist"
    [ -f "$src" ] || { echo "MISSING SOURCE: $src" >&2; exit 1; }
    if [ "$DRY" = "1" ]; then echo "[dry-run] would install $a → $dest"; continue; fi
    cp -f "$src" "$dest"
    # reload: bootout (ignore if not loaded) then bootstrap; fall back to legacy load.
    launchctl bootout "gui/$UID_NUM/$a" 2>/dev/null || true
    if launchctl bootstrap "gui/$UID_NUM" "$dest" 2>/dev/null; then
        echo "installed + bootstrapped: $a"
    else
        launchctl unload "$dest" 2>/dev/null || true
        launchctl load "$dest" 2>/dev/null && echo "installed + loaded (legacy): $a" || echo "WARN: load failed for $a" >&2
    fi
done

echo "--- launchctl status ---"
launchctl list 2>/dev/null | grep -E 'com\.colgrep\.' || echo "(none loaded)"
