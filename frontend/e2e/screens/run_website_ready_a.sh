#!/bin/zsh
# Track A screens (website_ready_a.mjs) against the REAL server in multi mode
# (../platform/real_server.py: throwaway PostgreSQL databases, fakes for every
# outside service).
#   E2E_DB_PREFIX=so_wra_e2e OUT=<dir> frontend/e2e/screens/run_website_ready_a.sh
set -e
setopt no_bg_nice 2>/dev/null || true
HERE=${0:A:h}
REPO=${HERE:h:h:h}
W=${WRA_WORK:-/tmp/website-ready-a-work}
PORT=${PORT:-8881}
OUT=${OUT:-/tmp/website-ready-a}
case "$W" in /|"$HOME"|"$REPO"*) echo "refusing WRA_WORK=$W"; exit 2;; esac
rm -rf "$W" && mkdir -p "$W"
(cd "$REPO/frontend" && npx vite build --outDir "$W/dist" --emptyOutDir >/dev/null)
STORE_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
MASTER_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
E2E_DB_PREFIX=${E2E_DB_PREFIX:-so_wra_e2e} MOCK_DIST="$W/dist" MOCK_PORT=$PORT MOCK_STORE_PASSWORD=$STORE_PW MOCK_MASTER_PASSWORD=$MASTER_PW \
  "$REPO/backend/.venv/bin/python" "$HERE/../platform/real_server.py" > "$W/server.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; wait $pid 2>/dev/null || true' EXIT INT TERM
for i in {1..300}; do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; kill -0 $pid 2>/dev/null || { cat "$W/server.log"; exit 1; }; sleep 0.5; done
BASE="http://127.0.0.1:$PORT" MASTER_PW=$MASTER_PW STORE_PW=$STORE_PW OUT="$OUT" node "$HERE/website_ready_a.mjs"
