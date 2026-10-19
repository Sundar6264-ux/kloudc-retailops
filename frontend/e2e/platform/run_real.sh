#!/bin/zsh
# Website platform checks (platform.mjs) against the REAL server in multi mode
# (real_server.py: throwaway PostgreSQL databases, fakes for every outside
# service), at 1280 and 375 pixels. The mock run is run.sh.
#
#   E2E_DB_PREFIX=so_<you> frontend/e2e/platform/run_real.sh
#
# E2E_PG_URL (default the local dev cluster) must allow CREATE DATABASE;
# every database starts with E2E_DB_PREFIX and is dropped at the end.
set -e
setopt no_bg_nice 2>/dev/null || true
HERE=${0:A:h}
REPO=${HERE:h:h:h}
W=${PLATFORM_E2E_WORK:-/tmp/storeops-platform-e2e-real}
PORT=${MOCK_PORT:-8872}
case "$W" in /|"$HOME"|"$REPO"*) echo "refusing PLATFORM_E2E_WORK=$W"; exit 2;; esac
rm -rf "$W" && mkdir -p "$W"
(cd "$REPO/frontend" && npx vite build --outDir "$W/dist" --emptyOutDir >/dev/null)
STORE_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
MASTER_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
MOCK_DIST="$W/dist" MOCK_PORT=$PORT MOCK_STORE_PASSWORD=$STORE_PW MOCK_MASTER_PASSWORD=$MASTER_PW \
  "$REPO/backend/.venv/bin/python" "$HERE/real_server.py" > "$W/server.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; wait $pid 2>/dev/null || true' EXIT INT TERM
for i in {1..300}; do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; kill -0 $pid 2>/dev/null || { cat "$W/server.log"; exit 1; }; sleep 0.5; done
PLATFORM_E2E_WORK="$W" MOCK_PORT=$PORT MOCK_STORE_PASSWORD=$STORE_PW MOCK_MASTER_PASSWORD=$MASTER_PW \
  E2E_REAL=1 E2E_APPROVE_PACK=generic E2E_HOSTING_ENABLED=1 node "$HERE/platform.mjs"
