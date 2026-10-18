#!/usr/bin/env bash
# scripts/watchdog.sh — periodic health check + auto-recovery for every core
# LaunchDaemon on this Mac (spicetown-backend, spicetown-labels, and the
# shared Cloudflare Tunnel).
#
# Why this exists: RunAtLoad + KeepAlive in each service's own plist already
# cover "start at boot" and "restart on crash" - but RunAtLoad's real boot-
# time behavior on this exact setup has never actually been exercised (this
# machine hasn't rebooted since these were installed), and KeepAlive can
# still leave a service unloaded if launchd throttles it into its "penalty
# box" after repeated rapid crashes, or if a service gets manually unloaded
# (e.g. a `bootout` during a restart that never got followed by `bootstrap`)
# and nothing else notices. This is the belt-and-suspenders net: every
# `com.spicetown.watchdog.plist`-scheduled run (see StartInterval there),
# check whether each service is actually loaded at all, and re-bootstrap it
# if not. Runs as root (see that plist - no UserName means root), since
# only root can bootstrap/bootout into the system domain regardless of which
# user the target daemon itself runs as.
set -uo pipefail

LOG="/Users/sundar/spicetown-backend/backend/logs/watchdog.log"

SERVICES=(
  "com.spicetown.backend:/Library/LaunchDaemons/com.spicetown.backend.plist"
  "com.spicetown.cloudflared:/Library/LaunchDaemons/com.spicetown.cloudflared.plist"
  "com.spicetown.labels:/Library/LaunchDaemons/com.spicetown.labels.plist"
)

for entry in "${SERVICES[@]}"; do
  label="${entry%%:*}"
  plist="${entry#*:}"

  if ! launchctl print "system/${label}" >/dev/null 2>&1; then
    echo "$(date -u +%FT%TZ) ${label} not loaded - bootstrapping" >> "$LOG"
    if [ ! -f "$plist" ]; then
      echo "$(date -u +%FT%TZ) ${label}: plist missing at ${plist} - cannot recover" >> "$LOG"
      continue
    fi
    launchctl bootstrap system "$plist" >> "$LOG" 2>&1
    if launchctl print "system/${label}" >/dev/null 2>&1; then
      echo "$(date -u +%FT%TZ) ${label} recovered" >> "$LOG"
    else
      echo "$(date -u +%FT%TZ) ${label} bootstrap FAILED - still not loaded" >> "$LOG"
    fi
  fi
done
