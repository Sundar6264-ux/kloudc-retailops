#!/usr/bin/env bash
# Periodic check for a bare-metal Mac install (com.kloudc.storeops.watchdog):
# re-bootstraps any listed LaunchDaemon that is not loaded at all.
#
# RunAtLoad + KeepAlive already cover "start at boot" and "restart on crash",
# but launchd can leave a service unloaded after repeated rapid crashes, or
# after a manual `bootout` that was never followed by `bootstrap`. This is the
# safety net for those cases.
#
# Services: STOREOPS_WATCH_SERVICES, space-separated "label:plist" pairs.
# Default: the backend only. Log: STOREOPS_WATCHDOG_LOG (default: logs/ next
# to this repo's backend).
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="${STOREOPS_WATCHDOG_LOG:-$HERE/../../logs/watchdog.log}"
SERVICES=(${STOREOPS_WATCH_SERVICES:-"com.kloudc.storeops.backend:/Library/LaunchDaemons/com.kloudc.storeops.backend.plist"})

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
