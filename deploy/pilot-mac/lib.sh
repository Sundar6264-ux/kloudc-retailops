# Shared helpers for the pilot scripts (sourced, never run).
set -euo pipefail
PILOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=pilot.conf
. "$PILOT_DIR/pilot.conf"
PG_DATA="$PILOT_ROOT/pgdata"
PG_RUN="$PILOT_ROOT/run"
APP_DIR="$PILOT_ROOT/app"
BACKEND_DIR="$APP_DIR/backend"
VENV_PY="$BACKEND_DIR/.venv/bin/python"
CONFIG_ENV="$PILOT_ROOT/config/pilot.env"
SECRETS_ENV="$PILOT_ROOT/secrets/retailops.env"
PARITY_ENV="$PILOT_ROOT/config/parity.env"
LOG_DIR="$PILOT_ROOT/logs"

say() { printf '%s %s\n' "[$(date '+%H:%M:%S')]" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# The SQLAlchemy URL of database $1 on the pilot cluster (unix socket, peer auth).
pg_url() { printf 'postgresql+psycopg://%s@/%s?host=%s&port=%s' "$PILOT_PG_ROLE" "$1" "$PG_RUN" "$PILOT_PG_PORT"; }

load_env() {
  [ -f "$CONFIG_ENV" ] || die "$CONFIG_ENV is missing: run prepare.sh first"
  [ -f "$SECRETS_ENV" ] || die "$SECRETS_ENV is missing: run prepare.sh first"
  set -a
  # shellcheck disable=SC1090
  . "$CONFIG_ENV"
  # shellcheck disable=SC1090
  . "$SECRETS_ENV"
  set +a
}

pg_ready() { "$PILOT_PG_BIN/pg_isready" -q -h "$PG_RUN" -p "$PILOT_PG_PORT"; }
