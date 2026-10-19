#!/bin/sh
# Container start: make the volume's folders, migrate the database to the
# newest schema, then run the store (API + website + scheduled jobs).
#
# Exactly one uvicorn worker: the scheduled jobs run inside the process, so
# a second worker would run every job twice.
set -eu

cd /app/backend
mkdir -p "$INVOICE_UPLOADS_DIR" "$TEAM_UPLOADS_DIR" "$FLYERS_DIR"

if [ "${1:-}" = "" ] || [ "${1:-}" = "serve" ]; then
  echo "retailops: alembic upgrade head"
  alembic upgrade head
  echo "retailops: starting on port ${PORT:-8000}"
  # Behind a reverse proxy or tunnel, set FORWARDED_ALLOW_IPS (uvicorn reads
  # it) to the proxy's address so client IPs and https are seen correctly.
  exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --proxy-headers
fi

# Anything else runs as a one-off command in the same environment, e.g.
#   docker compose run --rm store python scripts/seed_admin.py
exec "$@"
