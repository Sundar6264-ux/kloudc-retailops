#!/bin/bash
# Rebuild the architecture document (HTML + PDF). Read-only against the code; never starts the
# app. Usage:
#   bash docs/architecture/build/build.sh <scratch dir> [<out.pdf>]
# Needs: backend/.venv (imports the app to list routes / tables), node + website/e2e/node_modules
# (Playwright Chromium), a pinned mermaid in <scratch>/npm (npm i mermaid@10.9.1), and python with
# pypdf for the page numbers (<scratch>/venv). Row counts: set PROD_DB to read them from a
# database read-only (sqlite3 "file:...?mode=ro"); otherwise data/rowcounts.txt is reused.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WEB="$(cd "$HERE/../../.." && pwd)"
SCRATCH="${1:?scratch dir}"
OUT_PDF="${2:-$SCRATCH/SPICETOWN_ARCHITECTURE.pdf}"
DATA="$HERE/data"
mkdir -p "$SCRATCH" "$DATA"

if [ ! -f "$SCRATCH/npm/node_modules/mermaid/dist/mermaid.min.js" ]; then
  mkdir -p "$SCRATCH/npm" && (cd "$SCRATCH/npm" && npm init -y >/dev/null && npm install --no-audit --no-fund mermaid@10.9.1 >/dev/null)
fi
if [ ! -x "$SCRATCH/venv/bin/python" ]; then
  python3 -m venv "$SCRATCH/venv" && "$SCRATCH/venv/bin/pip" install -q pypdf
fi

# 1. Facts from the code
(cd "$WEB/backend" && SCHEDULER_ENABLED=false NOTIFICATIONS_ENABLED=false TEAM_JOBS_ENABLED=false PYTHONPATH=. \
  nice -n 15 .venv/bin/python "$HERE/extract_facts.py" "$DATA/facts.json")
python3 -I "$HERE/client_map.py" "$DATA/clientmap.json"
if [ -n "${PROD_DB:-}" ]; then
  sqlite3 "file:$PROD_DB?mode=ro" "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name" |
    while read -r t; do echo "$t|$(sqlite3 "file:$PROD_DB?mode=ro" "select count(*) from \"$t\"")"; done > "$DATA/rowcounts.txt"
fi

# 2. Source HTML, render, page numbers, render again
python3 -I "$HERE/build_doc.py" "$DATA" "$SCRATCH/doc.src.html"
MJS="$SCRATCH/npm/node_modules/mermaid/dist/mermaid.min.js"
nice -n 15 node "$HERE/render.mjs" "$SCRATCH/doc.src.html" "$MJS" "$SCRATCH/pass1.html" "$SCRATCH/pass1.pdf" - "$SCRATCH/report.json"
"$SCRATCH/venv/bin/python" "$HERE/find_pages.py" "$SCRATCH/pass1.pdf" "$SCRATCH/report.json" "$SCRATCH/pagemap.json"
nice -n 15 node "$HERE/render.mjs" "$SCRATCH/doc.src.html" "$MJS" "$WEB/docs/architecture/SPICETOWN_ARCHITECTURE.html" "$OUT_PDF" "$SCRATCH/pagemap.json" "$SCRATCH/report2.json"
echo "HTML: $WEB/docs/architecture/SPICETOWN_ARCHITECTURE.html"
echo "PDF:  $OUT_PDF"
