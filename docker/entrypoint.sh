#!/bin/sh
# Container start: make the volume's folders, check the configuration,
# migrate the database(s) to the newest schema, then run the server (API +
# website + scheduled jobs).
#
# Exactly one uvicorn worker: the scheduled jobs run inside the process, so
# a second worker would run every job twice.
set -eu

cd /app/backend
mkdir -p "$INVOICE_UPLOADS_DIR" "$TEAM_UPLOADS_DIR" "$FLYERS_DIR"

if [ "${1:-}" = "" ] || [ "${1:-}" = "serve" ]; then
  # Production refuses an unsafe configuration (app/ops/prod_checks.py);
  # the same check runs again inside the server.
  SCHEDULER_ENABLED=false python -m app.ops check-config
  if [ "${TENANCY_MODE:-single}" = "multi" ]; then
    # The shared service: the platform database, then every store database
    # on the shared cluster (one store failing does not keep the rest down;
    # /api/ready reports it).
    echo "retailops: migrate-platform + migrate-all"
    SCHEDULER_ENABLED=false python -m app.platform migrate-platform
    SCHEDULER_ENABLED=false python -m app.platform migrate-all --keep-going || echo "retailops: migrate-all reported a problem"
  else
    echo "retailops: alembic upgrade head"
    alembic upgrade head
  fi
  echo "retailops: starting on port ${PORT:-8000}"
  # Behind a reverse proxy or tunnel, set FORWARDED_ALLOW_IPS (uvicorn reads
  # it) to the proxy's address so client IPs and https are seen correctly.
  # SIGTERM (docker stop): open requests get 25 s, then the jobs stop.
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --proxy-headers \
    --timeout-graceful-shutdown 25 --no-access-log
fi

# Anything else runs as a one-off command in the same environment, e.g.
#   docker compose run --rm store python scripts/seed_admin.py
exec "$@"
