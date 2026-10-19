#!/bin/bash
# Deploy a new release to the pilot (as the normal user - no sudo): fetch and
# check out the ref, install requirements, rebuild the website, check the
# config, then restart the server (launchd starts it again at once; the
# start script migrates the databases first) and wait for /api/ready.
#
#   deploy/pilot-mac/update.sh [REF]
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
ref="${1:-$PILOT_REF}"
[ "$(id -u)" != 0 ] || die "run update.sh as the normal user"
say "fetching $ref"
git -C "$APP_DIR" fetch --quiet origin
git -C "$APP_DIR" checkout --quiet "$ref"
git -C "$APP_DIR" merge --quiet --ff-only "origin/$ref" 2>/dev/null || true
say "now at $(git -C "$APP_DIR" rev-parse --short HEAD)"
"$VENV_PY" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt"
(cd "$APP_DIR/frontend" && npm ci --no-audit --no-fund --silent && npx vite build >/dev/null)
load_env
(cd "$BACKEND_DIR" && SCHEDULER_ENABLED=false "$VENV_PY" -m app.ops check-config) || die "config check failed: not restarting"
say "restarting the server"
pkill -TERM -u "$(id -u)" -f "$BACKEND_DIR/.venv/bin/uvicorn app.main:app" || true
for _ in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PILOT_APP_PORT/api/ready" >/dev/null 2>&1; then say "ready"; exit 0; fi
  sleep 2
done
die "not ready after 3 minutes - see $LOG_DIR/startup.log and $LOG_DIR/error.log"
