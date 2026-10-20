#!/bin/bash
# launchd runs this (com.kloudc.retailops.pilot.worker, only when installed
# with install.sh --worker): a background job worker for JOB_QUEUE_MODE=queue.
# The server (start-server.sh) migrates the databases; the worker only waits
# for PostgreSQL. PILOT_WORKER_CONCURRENCY (default 2) jobs at a time.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_env
cd "$BACKEND_DIR"
for _ in $(seq 1 60); do pg_ready && break; sleep 1; done
exec "$VENV_PY" -m app.jobqueue worker --concurrency "${PILOT_WORKER_CONCURRENCY:-2}" --grace 25
