#!/bin/sh
# Runs backup_all.py once a day at BACKUP_AT (HH:MM, the container's UTC
# clock) and the restore drill on day 1 of each month, forever. Dumps go to
# /backups (a volume - copy it off the machine, e.g. rclone to R2).
set -eu
AT="${BACKUP_AT:-07:15}"
KEEP="${BACKUP_KEEP_DAYS:-30}"
URL="${BACKUP_PLATFORM_URL:?set BACKUP_PLATFORM_URL (no password in it; PGPASSWORD is used)}"
run() {
  python3 /opt/retailops/backup/backup_all.py "$1" --platform-url "$URL" --dest /backups ${2:-} || true
}
if [ "${1:-}" = "now" ]; then run backup "--keep-days $KEEP"; exit 0; fi
while true; do
  now=$(date -u +%s)
  next=$(date -u -d "today $AT" +%s)
  [ "$next" -le "$now" ] && next=$(date -u -d "tomorrow $AT" +%s)
  sleep $((next - now))
  run backup "--keep-days $KEEP"
  [ "$(date -u +%d)" = "01" ] && run drill
done
