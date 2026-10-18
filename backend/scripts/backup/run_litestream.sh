#!/bin/bash
# Starts Litestream (continuous backup to Cloudflare R2) - only once the
# owner has set it up. Until then it says why and exits 0, so launchd
# (KeepAlive on failure only) does not keep restarting it.
#
#   run_litestream.sh           replicate (what the LaunchDaemon runs)
#   run_litestream.sh --check   validate the setup and list what Litestream sees
#
# Setup: scripts/backup/README.md. Credentials live ONLY in
# ~/.config/storeops/litestream.env (chmod 600), never in git or a plist:
#   LITESTREAM_ACCESS_KEY_ID=...
#   LITESTREAM_SECRET_ACCESS_KEY=...
set -euo pipefail

CONF_DIR="${STOREOPS_LITESTREAM_DIR:-$HOME/.config/storeops}"
ENV_FILE="$CONF_DIR/litestream.env"
CONFIG="$CONF_DIR/litestream.yml"

disabled() {
  echo "$(date '+%Y-%m-%dT%H:%M:%S') Litestream not started: $1 (see scripts/backup/README.md)"
  exit 0
}

[ -f "$CONFIG" ] || disabled "no config at $CONFIG"
[ -f "$ENV_FILE" ] || disabled "no credentials file at $ENV_FILE"
if grep -q "REPLACE_WITH_" "$CONFIG"; then
  disabled "$CONFIG still has REPLACE_WITH_ placeholders"
fi
perms=$(stat -c "%a" "$ENV_FILE" 2>/dev/null || stat -f "%Lp" "$ENV_FILE")  # GNU stat first: on Linux `stat -f` means filesystem status
if [ "$perms" != "600" ] && [ "$perms" != "400" ]; then
  echo "Refusing to start: $ENV_FILE is readable by others (mode $perms). Run: chmod 600 $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
[ -n "${LITESTREAM_ACCESS_KEY_ID:-}" ] || disabled "LITESTREAM_ACCESS_KEY_ID is empty in $ENV_FILE"
[ -n "${LITESTREAM_SECRET_ACCESS_KEY:-}" ] || disabled "LITESTREAM_SECRET_ACCESS_KEY is empty in $ENV_FILE"

LITESTREAM="$(command -v litestream || true)"
for candidate in /opt/homebrew/bin/litestream /usr/local/bin/litestream; do
  [ -n "$LITESTREAM" ] && break
  [ -x "$candidate" ] && LITESTREAM="$candidate"
done
[ -n "$LITESTREAM" ] || { echo "litestream is not installed (brew install litestream)" >&2; exit 1; }

if [ "${1:-}" = "--check" ]; then
  echo "Config: $CONFIG"
  echo "Credentials: $ENV_FILE (mode $perms)"
  "$LITESTREAM" version
  "$LITESTREAM" databases -config "$CONFIG"
  exit 0
fi

exec "$LITESTREAM" replicate -config "$CONFIG"
