#!/bin/bash
# Onboarding DRY RUN on a development PostgreSQL cluster: everything the
# first real store goes through, timed, then thrown away.
#
#   backend/scripts/onboarding/dry_run.sh --source /path/store.db [--pack /outside/git/store.pack.json]
#       [--pg-url postgresql+psycopg://storeops@127.0.0.1:5433/postgres] [--prefix kfs_dry_]
#       [--work /scratch/folder] [--code PILOT1] [--keep]
#
# 1. a scratch platform database <prefix>platform, migrated;
# 2. store CODE provisioned (generic pack) and put in pilot mode;
# 3. the source is copied READ-ONLY (sqlite3 .backup) into the work folder and
#    imported (import-sqlite: snapshot, upgrade, copy, row counts + checksums);
# 4. the private pack loaded (when --pack is given; it must be outside git);
# 5. parity of the store against the copy (must be clean);
# 6. readiness, then backup_all + restore drill of the scratch databases;
# 7. unless --keep: every <prefix>* database dropped, the work folder deleted
#    (also when a step fails).
# Never sends anything (pilot mode, no credentials) and never writes the source.
set -uo pipefail
BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PG_URL="postgresql+psycopg://storeops@127.0.0.1:5433/postgres"
PREFIX="kfs_dry_"; CODE="PILOT1"; SOURCE=""; PACK=""; WORK=""; KEEP=0
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="$2"; shift ;;
    --pack) PACK="$2"; shift ;;
    --pg-url) PG_URL="$2"; shift ;;
    --prefix) PREFIX="$2"; shift ;;
    --work) WORK="$2"; shift ;;
    --code) CODE="$2"; shift ;;
    --keep) KEEP=1 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[ -f "$SOURCE" ] || { echo "--source must be a SQLite store database" >&2; exit 2; }
[[ "$PREFIX" =~ ^[a-z][a-z0-9_]*_$ ]] || { echo "--prefix: lower case, ending with _" >&2; exit 2; }
WORK="${WORK:-$(mktemp -d -t retailops-dry-run)}"
mkdir -p "$WORK"/{logs,uploads,backups,work}
SERVER="${PG_URL%/*}"
PSQL_URL="${SERVER/postgresql+psycopg/postgresql}/postgres"

cleanup() {
  [ "$KEEP" = 1 ] && { echo "kept: $WORK and the ${PREFIX}* databases"; return; }
  for db in $("$PG_BIN/psql" -X -d "$PSQL_URL" -Atc "SELECT datname FROM pg_database WHERE datname LIKE '${PREFIX//_/\\_}%'"); do
    case "$db" in "$PREFIX"*) "$PG_BIN/psql" -X -d "$PSQL_URL" -qc "DROP DATABASE \"$db\" WITH (FORCE)" && echo "dropped $db" ;; esac
  done
  rm -rf "$WORK"
  echo "work folder deleted"
}
trap cleanup EXIT

step() { echo; echo "== $*"; }
timed() {
  local start end rc
  start=$(python3 -c 'import time; print(time.time())')
  "$@"; rc=$?
  end=$(python3 -c 'import time; print(time.time())')
  printf 'TIMING %-28s %6.1f s  (exit %s)\n' "$TIMED_LABEL" "$(python3 -c "print($end - $start)")" "$rc" >&2
  return $rc
}

export TENANCY_MODE=multi APP_ENV=test SCHEDULER_ENABLED=false NOTIFICATIONS_ENABLED=false TEAM_JOBS_ENABLED=false
export PLATFORM_DATABASE_URL="$SERVER/${PREFIX}platform"
export STORE_DATABASE_URL_TEMPLATE="$SERVER/{db_name}"
export PG_ADMIN_URL="$PG_URL" STORE_DB_PREFIX="$PREFIX" UPLOADS_ROOT="$WORK/uploads" LOG_DIR="$WORK/logs"
export WHAPI_API_TOKEN= GMAIL_APP_PASSWORD= SMTP_PASSWORD= TOAST_CLIENT_ID= TOAST_CLIENT_SECRET= ANTHROPIC_API_KEY= FIREBASE_CREDENTIALS_FILE=
cd "$BACKEND"
export RETAILOPS_SECRET_KEYS="$(.venv/bin/python -m app.platform gen-key 2>/dev/null)"
export RETAILOPS_OWNER_PASSWORD="dry-run-$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
PY=.venv/bin/python

step "1. platform database ${PREFIX}platform"
TIMED_LABEL=createdb timed "$PG_BIN/psql" -X -d "$PSQL_URL" -qc \
  "CREATE DATABASE \"${PREFIX}platform\" TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'" || exit 1
TIMED_LABEL=migrate-platform timed $PY -m app.platform migrate-platform || exit 1
step "2. provision $CODE + pilot mode"
TIMED_LABEL=provision timed $PY -m app.platform provision --code "$CODE" --name "Dry Run Store" --pack generic \
  --tz America/New_York --pos toast --owner-username owner > "$WORK/provision.json" || { cat "$WORK/provision.json"; exit 1; }
TIMED_LABEL=pilot-mode timed $PY -m app.platform pilot-mode "$CODE" on || exit 1
step "3. read-only copy of the source + import"
TIMED_LABEL=source-copy timed $PY -c "import pathlib, sys; from app.ops.store_import import snapshot_sqlite; \
print(snapshot_sqlite(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])))" "$SOURCE" "$WORK/source-copy.db" || exit 1
ls -l "$WORK/source-copy.db" | awk '{print "source copy: " $5 " bytes"}'
TIMED_LABEL=import-sqlite timed $PY -m app.platform import-sqlite "$CODE" --sqlite "$WORK/source-copy.db" --workdir "$WORK/work" \
  > "$WORK/import.json" 2> "$WORK/import.log" || { tail -20 "$WORK/import.log"; exit 1; }
python3 - "$WORK/import.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
print({k: d[k] for k in ("source_revision", "revision", "tables", "rows", "differences", "previous_stamp", "cleared",
                         "owner_added", "snapshot_bytes")})
print("import timings:", d["timings_s"])
PYEOF
if [ -n "$PACK" ]; then
  step "4. private pack"
  TIMED_LABEL=load-pack timed $PY -m app.platform load-pack "$CODE" --file "$PACK" > "$WORK/pack.json" || { cat "$WORK/pack.json"; exit 1; }
  python3 -c "import json,sys; d=json.load(open('$WORK/pack.json')); print({k: d[k] for k in ('pack_id','written','inserted')})"
fi
step "5. parity against the copy"
TIMED_LABEL=parity timed $PY -m app.platform parity "$CODE" --reference "$WORK/source-copy.db" --days 14 --log-dir "$WORK/logs" > "$WORK/parity.json"
python3 -c "import json; d=json.load(open('$WORK/parity.json')); print({k: d[k] for k in ('ok','from','to','days_compared','days_with_differences','totals','seconds')})"
step "6. readiness, backup, restore drill"
TIMED_LABEL=ready timed $PY -m app.ops ready | tr -d '\n' | cut -c1-200; echo
TIMED_LABEL=backup_all timed $PY scripts/backup/backup_all.py backup --platform-url "$PLATFORM_DATABASE_URL" --dest "$WORK/backups" --pg-bin "$PG_BIN" | tail -4
du -sh "$WORK/backups"/*/ 2>/dev/null | sed 's#'"$WORK"'/##'
TIMED_LABEL=drill timed $PY scripts/backup/backup_all.py drill --platform-url "$PLATFORM_DATABASE_URL" --dest "$WORK/backups" --pg-bin "$PG_BIN" | tail -4
echo; echo "DRY RUN OK"
