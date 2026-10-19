#!/bin/bash
# Is the app's bundled help (assets/help) the same as content/help?
#   scripts/help/check_help_sync.sh [path/to/storeops-app]
# Exit 0 = in sync, 1 = stale (run sync_help_to_app.sh), 2 = no app copy.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${1:-$REPO/../storeops-app}"
MAN="$APP/assets/help/manifest.json"
[ -f "$MAN" ] || { echo "no bundled help at $APP/assets/help" >&2; exit 2; }
WANT="$("$REPO/scripts/help/help_hash.sh")"
HAVE="$("$REPO/scripts/help/help_hash.sh" "$APP/assets/help")"
RECORDED="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("hash",""))' "$MAN")"
if [ "$WANT" = "$HAVE" ] && [ "$HAVE" = "$RECORDED" ]; then echo "app help is current ($WANT)"; exit 0; fi
echo "app help is stale: content $WANT, app files $HAVE, app manifest $RECORDED - run scripts/help/sync_help_to_app.sh" >&2
exit 1
