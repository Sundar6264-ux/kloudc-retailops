#!/bin/bash
# Dumps every table in the Spicetown SQLite database to its own CSV file.
#
# Usage:
#   ./dump_db_to_csv.sh [db_path] [output_dir]
#
# Defaults to the real database and a timestamped folder under your home
# directory, so repeated runs never overwrite a previous dump.
set -euo pipefail

DB_PATH="${1:-/Users/sundar/spicetown-backend/backend/spicetown.db}"
OUT_DIR="${2:-$HOME/spicetown-db-dumps/$(date +%Y-%m-%d_%H%M%S)}"

if [ ! -f "$DB_PATH" ]; then
  echo "Database not found: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

tables=$(sqlite3 "$DB_PATH" "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")

echo "Dumping tables from $DB_PATH"
echo "  -> $OUT_DIR"
echo

for table in $tables; do
  row_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM \"$table\";")

  # Plain `SELECT *` has no guaranteed order - it comes back in whatever order
  # rows were physically inserted, which is NOT the same as date order once a
  # table's been backfilled or re-synced out of sequence (job_runs especially -
  # retries and full re-backfills insert rows for old dates long after newer
  # ones already exist). Order each table by something a human actually wants
  # to read it in.
  case "$table" in
    orders) order_by="business_date, opened_at" ;;
    inventory_snapshots) order_by="snapshot_date, item_id" ;;
    job_runs) order_by="business_date, started_at" ;;
    user_sessions) order_by="created_at" ;;
    users) order_by="id" ;;
    *) order_by="" ;;
  esac

  if [ -n "$order_by" ]; then
    query="SELECT * FROM \"$table\" ORDER BY $order_by;"
  else
    query="SELECT * FROM \"$table\";"
  fi

  # sqlite3's .mode csv writes RFC4180 CRLF line endings by default, which
  # show up as a literal ^M per row in most Unix editors/terminals - strip
  # the \r so the file has plain \n line endings instead.
  sqlite3 "$DB_PATH" <<EOF | tr -d '\r' > "$OUT_DIR/$table.csv"
.headers on
.mode csv
$query
EOF
  echo "  $table.csv  ($row_count rows)"
done

echo
echo "Done: $(ls "$OUT_DIR" | wc -l | tr -d ' ') file(s) in $OUT_DIR"
