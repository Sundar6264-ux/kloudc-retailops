#!/bin/bash
# Copy the help content (content/help) into the app as its bundled copy
# (docs/DESIGN_SYSTEM.md 5.2) and regenerate the app's bundled glossary.
#
#   scripts/help/sync_help_to_app.sh [path/to/storeops-app]
#
# Default app path: the folder next to this repository (../storeops-app).
# Writes <app>/assets/help/ (every JSON file and articles/, plus
# manifest.json with "hash" = sha256 of the content) and
# <app>/lib/core/glossary/glossary_data.dart. The app lists assets/help/ and
# assets/help/articles/ under flutter: assets:. Run it whenever content/help
# or backend/app/glossary.py changes; scripts/help/check_help_sync.sh tells
# you when the copy is stale.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${1:-$REPO/../storeops-app}"
APP="$(cd "$APP" && pwd)"
SRC="$REPO/content/help"
DEST="$APP/assets/help"
[ -f "$APP/pubspec.yaml" ] || { echo "not a Flutter app: $APP" >&2; exit 2; }

HASH="$("$REPO/scripts/help/help_hash.sh")"
rm -rf "$DEST"
mkdir -p "$DEST/articles"
for f in "$SRC"/*.json; do
  [ "$(basename "$f")" = "manifest.json" ] || cp "$f" "$DEST/"
done
cp "$SRC"/articles/*.json "$DEST/articles/"
# The manifest with the content hash, so the app (and the check script) can
# tell a stale copy from a current one.
python3 - "$SRC/manifest.json" "$DEST/manifest.json" "$HASH" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
m["hash"] = sys.argv[3]
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(m, indent=1, ensure_ascii=False) + "\n")
PY
# An index of the article files (Flutter can't list an asset folder).
(cd "$DEST/articles" && ls *.json | python3 -c 'import json,sys; print(json.dumps(sorted(l.strip() for l in sys.stdin if l.strip()), indent=1))') > "$DEST/articles/index.json"

# The bundled glossary (status + concept terms), same JSON as /api/glossary.
(cd "$REPO/backend" && .venv/bin/python scripts/export_glossary.py --app-repo "$APP")
echo "help content $HASH -> $DEST"
