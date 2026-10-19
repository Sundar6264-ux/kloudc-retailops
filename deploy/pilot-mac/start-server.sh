#!/bin/bash
# launchd runs this (com.kloudc.retailops.pilot.server): config check,
# migrations (platform, then every store), then the server - one uvicorn
# worker on 127.0.0.1 (the schedulers run inside it; leader election keeps
# a second process from running a job twice anyway).
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_env
cd "$BACKEND_DIR"
export FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
for _ in $(seq 1 60); do pg_ready && break; sleep 1; done
SCHEDULER_ENABLED=false "$VENV_PY" -m app.ops check-config >>"$LOG_DIR/startup.log" 2>&1 \
  || { say "config check failed - see $LOG_DIR/startup.log" >&2; exit 1; }
SCHEDULER_ENABLED=false "$VENV_PY" -m app.platform migrate-platform >>"$LOG_DIR/startup.log" 2>&1 \
  || { say "platform migration failed - see $LOG_DIR/startup.log" >&2; exit 1; }
# One store failing to migrate does not keep the others down: /api/ready
# reports it and the master console shows the store's revision.
SCHEDULER_ENABLED=false "$VENV_PY" -m app.platform migrate-all --keep-going >>"$LOG_DIR/startup.log" 2>&1 \
  || say "migrate-all reported a problem - see $LOG_DIR/startup.log" >&2
exec "$BACKEND_DIR/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PILOT_APP_PORT" --workers 1 \
  --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS" --timeout-graceful-shutdown 25 --no-access-log
