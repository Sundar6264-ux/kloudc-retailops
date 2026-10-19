#!/bin/zsh
# End-to-end platform flows (flows.mjs) against the REAL server in multi mode
# (flows_server.py: bootstrapped with the CLI, schedulers on, throwaway
# PostgreSQL databases, every outside service faked), at 1280 and 375 pixels.
#
#   E2E_DB_PREFIX=so_<you> frontend/e2e/platform/run_flows.sh
#
# E2E_PG_URL (default the local dev cluster) must allow CREATE DATABASE;
# every database starts with E2E_DB_PREFIX and is dropped at the end.
set -e
setopt no_bg_nice 2>/dev/null || true
HERE=${0:A:h}
REPO=${HERE:h:h:h}
W=${PLATFORM_E2E_WORK:-/tmp/retailops-platform-e2e-flows}
PORT=${MOCK_PORT:-8873}
case "$W" in /|"$HOME"|"$REPO"*) echo "refusing PLATFORM_E2E_WORK=$W"; exit 2;; esac
rm -rf "$W" && mkdir -p "$W"
# E2E_FRONTEND_DIR / E2E_PYTHON: build / run from another checkout (a worktree
# without node_modules or .venv); defaults are this repo's.
(cd "${E2E_FRONTEND_DIR:-$REPO/frontend}" && npx vite build --outDir "$W/dist" --emptyOutDir >/dev/null)
MASTER_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
MOCK_DIST="$W/dist" MOCK_PORT=$PORT MOCK_MASTER_PASSWORD=$MASTER_PW FLOWS_WORK="$W" \
  "${E2E_PYTHON:-$REPO/backend/.venv/bin/python}" "$HERE/flows_server.py" > "$W/server.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; wait $pid 2>/dev/null || true' EXIT INT TERM
for i in {1..300}; do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; kill -0 $pid 2>/dev/null || { cat "$W/server.log"; exit 1; }; sleep 0.5; done
PLATFORM_E2E_WORK="$W" MOCK_PORT=$PORT MOCK_MASTER_PASSWORD=$MASTER_PW node "$HERE/flows.mjs"
