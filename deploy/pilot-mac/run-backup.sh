#!/bin/bash
# launchd runs this nightly (backup) and monthly (drill):
#   run-backup.sh backup | drill
# After a good nightly backup, with BACKUP_OFFSITE_DIR set in config/pilot.env
# (e.g. a folder in iCloud Drive), an encrypted copy of the newest dumps and
# the store files goes there (scripts/backup/offsite_copy.py; the key is
# secrets/offsite-backup.key - keep a copy of it in a password manager).
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_env
what="${1:-backup}"
args=(--platform-url "$PLATFORM_DATABASE_URL" --dest "$PILOT_ROOT/backups" --pg-bin "$PILOT_PG_BIN")
if [ "$what" != backup ]; then
  exec "$VENV_PY" "$BACKEND_DIR/scripts/backup/backup_all.py" "$what" "${args[@]}"
fi
args+=(--keep-days "$PILOT_KEEP_DAYS")
rc=0
"$VENV_PY" "$BACKEND_DIR/scripts/backup/backup_all.py" backup "${args[@]}" || rc=$?
if [ -n "${BACKUP_OFFSITE_DIR:-}" ]; then
  "$VENV_PY" "$BACKEND_DIR/scripts/backup/offsite_copy.py" copy --dest "$PILOT_ROOT/backups" \
    --to "$BACKUP_OFFSITE_DIR" --key-file "$PILOT_ROOT/secrets/offsite-backup.key" \
    --uploads "$PILOT_ROOT/uploads" --keep "${BACKUP_OFFSITE_KEEP:-14}" || rc=$?
fi
exit "$rc"
