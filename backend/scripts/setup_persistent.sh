#!/bin/bash
# One-time (and safe to re-run) setup for a permanently-running Spicetown dashboard:
#   1. Builds the frontend into a static bundle the backend serves directly.
#   2. Installs a launchd DAEMON (system domain, not a per-login-session agent) so
#      the backend runs at boot regardless of GUI login state, and auto-restarts
#      if it ever crashes. Needs sudo since /Library/LaunchDaemons is root-owned.
#   3. Disables system sleep on AC power (display sleep is left alone - the
#      monitor still turns off on its own timer). Without this, macOS's Power Nap
#      puts the whole machine to sleep after ~10 min idle and only briefly
#      DarkWakes it for background maintenance, which is what made this Mac
#      appear to drop on and off the tailnet every few minutes.
#   4. Publishes it privately on your Tailscale network at a stable HTTPS URL.
#
# Run this from a real Terminal on this Mac (not through Claude's sandboxed
# Bash tool) - it needs to talk to the real launchd and the real network stack.
set -euo pipefail

REPO_ROOT="/Users/sundar/spicetown-backend"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
PLIST_SRC="$BACKEND_DIR/scripts/com.spicetown.backend.plist"
PLIST_DEST="/Library/LaunchDaemons/com.spicetown.backend.plist"

echo "== 1/5  Building frontend =="
cd "$FRONTEND_DIR"
npm install
npm run build

echo "== 2/5  Installing launchd service (will prompt for your password - needed for /Library/LaunchDaemons) =="
mkdir -p "$BACKEND_DIR/logs"
sudo cp "$PLIST_SRC" "$PLIST_DEST"
sudo chown root:wheel "$PLIST_DEST"
sudo launchctl bootout system/com.spicetown.backend 2>/dev/null || true
sudo launchctl bootstrap system "$PLIST_DEST"
sudo launchctl enable system/com.spicetown.backend

echo "== 3/5  Disabling system sleep on AC power (display sleep untouched) =="
sudo pmset -c sleep 0 disksleep 0 womp 1

echo "== 4/5  Waiting for backend to come up =="
for i in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null; then
    echo "Backend is up on port 8000."
    break
  fi
  sleep 1
  if [ "$i" -eq 20 ]; then
    echo "Backend didn't come up in time - check $BACKEND_DIR/logs/backend.error.log"
    exit 1
  fi
done

echo "== 5/5  Publishing privately on your tailnet =="
tailscale serve --bg 8000

echo
echo "Done. Status:"
tailscale serve status
DNS_NAME="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
echo
echo "Dashboard: https://$DNS_NAME/"
echo "(Reachable from any device already on your tailnet - this Mac, your iPhone, etc. Not reachable from the open internet.)"
echo
echo "The backend now restarts on its own after crashes or a reboot - you won't need to run uvicorn manually again."
echo "If you change frontend code, re-run this script (or just 'npm run build' in frontend/) to publish the update."
