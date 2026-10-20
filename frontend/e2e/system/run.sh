#!/bin/zsh
# System panel screens (track C) against the REAL server in multi mode with
# the job queue and live updates on (system_server.py; a throwaway platform
# database, nothing outside is called), at 1440 and 375 px, light and dark.
#
#   E2E_PLAYWRIGHT=/path/to/playwright/index.mjs frontend/e2e/system/run.sh
#
# Track A mounts <SystemStatusPanel /> in the console's Platform settings;
# until that merge this script builds a copy of the website with a "System"
# tab added (the copy only, never the tree) so the panel is seen in place.
set -e
setopt no_bg_nice 2>/dev/null || true
HERE=${0:A:h}
REPO=${HERE:h:h:h}
W=${SYSTEM_E2E_WORK:-/tmp/retailops-system-e2e}
PORT=${MOCK_PORT:-8883}
OUT=${SYSTEM_SHOTS:-$W/shots}
case "$W" in /|"$HOME"|"$REPO"*) echo "refusing SYSTEM_E2E_WORK=$W"; exit 2;; esac
rm -rf "$W" && mkdir -p "$W/site"
rsync -a --exclude node_modules --exclude dist "$REPO/frontend/" "$W/site/frontend/"
rsync -a "$REPO/content/" "$W/site/content/"
ln -s "$REPO/frontend/node_modules" "$W/site/frontend/node_modules"
if ! grep -q 'SystemStatusPanel' "$W/site/frontend/src/pages/PlatformConsolePage.jsx"; then
  python3 - "$W/site/frontend/src" <<'PY'
import pathlib, sys
src = pathlib.Path(sys.argv[1])
m = src / "platform/platformModel.js"
s = m.read_text()
s = s.replace('  { key: "help", label: "Help"', '  { key: "system", label: "System", icon: "server", pageId: "platform_system", sub: "Storage, jobs and workers" },\n  { key: "help", label: "Help"', 1)
m.write_text(s)
p = src / "pages/PlatformConsolePage.jsx"
s = p.read_text()
s = s.replace('import { HelpCenter, InfoButton } from "../ui/helpBridge.jsx";', 'import { HelpCenter, InfoButton } from "../ui/helpBridge.jsx";\nimport SystemStatusPanel from "../platform/panels/SystemStatusPanel.jsx";', 1)
s = s.replace('      {tab === "help" && <MasterHelp />}', '      {tab === "system" && <SystemStatusPanel />}\n      {tab === "help" && <MasterHelp />}', 1)
p.write_text(s)
PY
fi
(cd "$W/site/frontend" && npx vite build --outDir "$W/dist" --emptyOutDir >/dev/null)
MASTER_PW=$(node -e 'console.log(require("crypto").randomBytes(15).toString("base64url"))')
MOCK_DIST="$W/dist" MOCK_PORT=$PORT MOCK_MASTER_PASSWORD=$MASTER_PW E2E_DB_PREFIX=${E2E_DB_PREFIX:-so_wrc_e2e} \
  "$REPO/backend/.venv/bin/python" "$HERE/system_server.py" > "$W/server.log" 2>&1 &
pid=$!
trap 'kill $pid 2>/dev/null; wait $pid 2>/dev/null || true' EXIT INT TERM
for i in {1..300}; do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; kill -0 $pid 2>/dev/null || { cat "$W/server.log"; exit 1; }; sleep 0.5; done
SYSTEM_SHOTS="$OUT" MOCK_PORT=$PORT MOCK_MASTER_PASSWORD=$MASTER_PW node "$HERE/system.mjs"
