#!/bin/bash
# Pilot on this Mac, step 2 (as the normal user, with the cluster running):
# migrate the platform database and create the first master if there is
# none (asks for a password; RETAILOPS_MASTER_PASSWORD works too).
#
#   deploy/pilot-mac/setup-db.sh [--master USERNAME]
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
MASTER="${PILOT_MASTER:-master}"
while [ $# -gt 0 ]; do
  case "$1" in
    --master) MASTER="$2"; shift ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ "$(id -u)" != 0 ] || die "run setup-db.sh as the normal user, not root"
for _ in $(seq 1 30); do pg_ready && break; sleep 1; done
pg_ready || die "the pilot PostgreSQL is not answering on $PG_RUN:$PILOT_PG_PORT"
load_env
cd "$BACKEND_DIR"
if ! "$PILOT_PG_BIN/psql" -h "$PG_RUN" -p "$PILOT_PG_PORT" -U "$PILOT_PG_ROLE" -d postgres -Atc \
    "SELECT 1 FROM pg_database WHERE datname = 'so_platform'" | grep -q 1; then
  say "creating the platform database so_platform"
  "$PILOT_PG_BIN/createdb" -h "$PG_RUN" -p "$PILOT_PG_PORT" -U "$PILOT_PG_ROLE" -T template0 -E UTF8 \
      --lc-collate=C --lc-ctype=C so_platform
fi
say "migrating the platform database"
SCHEDULER_ENABLED=false "$VENV_PY" -m app.platform migrate-platform
masters="$("$PILOT_PG_BIN/psql" -h "$PG_RUN" -p "$PILOT_PG_PORT" -U "$PILOT_PG_ROLE" -d so_platform -Atc \
    "SELECT count(*) FROM platform_users WHERE is_active")"
if [ "${masters:-0}" = 0 ]; then
  if [ -t 0 ] || [ -n "${RETAILOPS_MASTER_PASSWORD:-}" ]; then
    say "creating the first master '$MASTER' (choose a strong password)"
    SCHEDULER_ENABLED=false "$VENV_PY" -m app.platform create-master --username "$MASTER"
  else
    say "no master yet and no terminal to ask for a password: run later"
    say "  $PILOT_DIR/setup-db.sh --master $MASTER"
  fi
else
  say "masters: $masters (kept)"
fi
SCHEDULER_ENABLED=false "$VENV_PY" -m app.ops check-config || true
