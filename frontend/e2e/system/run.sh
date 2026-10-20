#!/bin/zsh
# System panel screens (track C) against the REAL server in multi mode with
# the job queue and live updates on (system_server.py; a throwaway platform
# database, nothing outside is called), at 1440 and 375 px, light and dark.
#
#   E2E_PLAYWRIGHT=/path/to/playwright/index.mjs frontend/e2e/system/run.sh
#
# The panel sits in the console's Platform settings tab (track A).
set -e
setopt no_bg_nice 2>/dev/null || true
HERE=${0:A:h}
REPO=${HERE:h:h:h}
W=${SYSTEM_E2E_WORK:-/tmp/retailops-system-e2e}
PORT=${MOCK_PORT:-8883}
OUT=${SYSTEM_SHOTS:-$W/shots}
case "$W" in /|"$HOME"|"$REPO"*) echo "refusing SYSTEM_E2E_WORK=$W"; exit 2;; esac
rm -rf "$W" && mkdir -p "$W"
(cd "$REPO/frontend" && npx vite build --outDir "$W/dist" --emptyOutDir >/dev/null)
MASTER_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
MOCK_DIST="$W/dist" MOCK_PORT=$PORT MOCK_MASTER_PASSWORD=$MASTER_PW E2E_DB_PREFIX=${E2E_DB_PREFIX:-so_wrc_e2e} \
  "$REPO/backend/.venv/bin/python" "$HERE/system_server.py" > "$W/server.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; wait $pid 2>/dev/null || true' EXIT INT TERM
for i in {1..300}; do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; kill -0 $pid 2>/dev/null || { cat "$W/server.log"; exit 1; }; sleep 0.5; done
SYSTEM_SHOTS="$OUT" MOCK_PORT=$PORT MOCK_MASTER_PASSWORD=$MASTER_PW node "$HERE/system.mjs"
