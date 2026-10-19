#!/bin/zsh
# Website platform checks (docs/PLATFORM_CONTRACT.md 14, 18.2 D): build the
# website, start the in-memory platform mock (mock_server.mjs) with random
# passwords, run platform.mjs at 1280 and 375 pixels.
#
#   frontend/e2e/platform/run.sh
#
# PLATFORM_E2E_WORK (default below) holds the build, screenshots and
# result.json - delete it when done. Playwright comes from
# frontend/e2e/node_modules (as for e2e/store/run.sh).
set -e
setopt no_bg_nice 2>/dev/null || true
HERE=${0:A:h}
REPO=${HERE:h:h:h}
W=${PLATFORM_E2E_WORK:-/tmp/retailops-platform-e2e}
PORT=${MOCK_PORT:-8871}
case "$W" in /|"$HOME"|"$REPO"*) echo "refusing PLATFORM_E2E_WORK=$W"; exit 2;; esac
rm -rf "$W" && mkdir -p "$W"
(cd "$REPO/frontend" && npx vite build --outDir "$W/dist" --emptyOutDir >/dev/null)
STORE_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
MASTER_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
MOCK_DIST="$W/dist" MOCK_PORT=$PORT MOCK_STORE_PASSWORD=$STORE_PW MOCK_MASTER_PASSWORD=$MASTER_PW \
  node "$HERE/mock_server.mjs" > "$W/mock.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null || true' EXIT INT TERM
for i in {1..50}; do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 0.2; done
PLATFORM_E2E_WORK="$W" MOCK_PORT=$PORT MOCK_STORE_PASSWORD=$STORE_PW MOCK_MASTER_PASSWORD=$MASTER_PW \
  node "$HERE/platform.mjs"
