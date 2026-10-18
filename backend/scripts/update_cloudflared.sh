#!/usr/bin/env bash
# scripts/update_cloudflared.sh — keep cloudflared current via Homebrew.
#
# cloudflared's own built-in auto-updater is deliberately disabled when it's
# installed via a package manager ("cloudflared will not automatically
# update if installed by a package manager" - confirmed in this app's own
# tunnel startup logs) - Homebrew is expected to own updates instead. Without
# something actually running `brew upgrade`, the binary would just sit on
# whatever version was installed at setup time forever. This checks for a
# newer version daily and, if one was installed, restarts the tunnel daemon
# so the new binary actually takes effect (a `brew upgrade` alone doesn't
# restart anything already running).
#
# Runs as root (see com.spicetown.cloudflared.updater.plist - needed for the
# launchctl restart), but Homebrew itself refuses to run as root ("Running
# Homebrew as root is extremely dangerous and no longer supported") - so the
# brew step specifically drops to the owning user via `sudo -u`.
set -uo pipefail

LOG="/Users/sundar/spicetown-backend/backend/logs/cloudflared_update.log"
BREW_USER="sundar"
# A root launchd process's `sudo -u` doesn't inherit sundar's normal shell
# environment - -H sets HOME so Homebrew finds its own config, and PATH is
# passed explicitly so `brew` itself resolves (this Mac's brew lives under
# /opt/homebrew, not on root's default PATH).
BREW="sudo -u $BREW_USER -H env PATH=/opt/homebrew/bin:/usr/bin:/bin brew"

BEFORE="$($BREW list --versions cloudflared 2>/dev/null)"
$BREW upgrade cloudflared >> "$LOG" 2>&1
AFTER="$($BREW list --versions cloudflared 2>/dev/null)"

if [ "$BEFORE" != "$AFTER" ]; then
  echo "$(date -u +%FT%TZ) cloudflared updated: '${BEFORE}' -> '${AFTER}', restarting tunnel" >> "$LOG"
  launchctl bootout system/com.spicetown.cloudflared >> "$LOG" 2>&1 || true
  sleep 2
  launchctl bootstrap system /Library/LaunchDaemons/com.spicetown.cloudflared.plist >> "$LOG" 2>&1
else
  echo "$(date -u +%FT%TZ) cloudflared already current (${AFTER})" >> "$LOG"
fi
