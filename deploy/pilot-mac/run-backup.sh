#!/bin/bash
# launchd runs this nightly (backup) and monthly (drill):
#   run-backup.sh backup | drill
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_env
what="${1:-backup}"
args=(--platform-url "$PLATFORM_DATABASE_URL" --dest "$PILOT_ROOT/backups" --pg-bin "$PILOT_PG_BIN")
[ "$what" = backup ] && args+=(--keep-days "$PILOT_KEEP_DAYS")
exec "$VENV_PY" "$BACKEND_DIR/scripts/backup/backup_all.py" "$what" "${args[@]}"
