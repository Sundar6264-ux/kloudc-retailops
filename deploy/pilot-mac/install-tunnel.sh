#!/bin/bash
# KloudC RetailOps - the public address app.kloudc.com through its OWN
# Cloudflare Tunnel 'kloudc-retailops' (docs/GO_LIVE_RUNBOOK.md, step 3).
#
#   1. In the Cloudflare dashboard (Zero Trust > Networks > Tunnels): create
#      tunnel "kloudc-retailops", public hostname app.kloudc.com ->
#      http://127.0.0.1:8900. Copy its token into
#      ~/kloudc-retailops/secrets/tunnel.token (one line; chmod 600).
#   2. sudo ~/kloudc-retailops/app/deploy/pilot-mac/install-tunnel.sh
#
# What it touches: ONLY /Library/LaunchDaemons/com.kloudc.retailops.pilot.tunnel.plist
# (its own job), <install>/config/tunnel.yml (locally managed option only)
# and <install>/run/tunnel-home. It NEVER runs `cloudflared service install`,
# never reads or writes ~/.cloudflared (config.yml, cert.pem) and never stops,
# changes or replaces another tunnel or cloudflared service on this Mac.
#
# Options: --dry-run DIR (render into DIR, no root needed)  --uninstall
# (stop and remove only this tunnel job)  --status
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=""; ACTION=install
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN="$2"; shift ;;
    --uninstall) ACTION=uninstall ;;
    --status) ACTION=status ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -n "$DRY_RUN" ]; then
  RUN_USER="$(id -un)"
else
  [ "$(id -u)" = 0 ] || { echo "Run it with sudo: sudo $0" >&2; exit 1; }
  RUN_USER="${SUDO_USER:-}"
  [ -n "$RUN_USER" ] && [ "$RUN_USER" != root ] || { echo "Run it with sudo from your own account" >&2; exit 1; }
fi
RUN_HOME="$(dscl . -read "/Users/$RUN_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}' || true)"
[ -n "$RUN_HOME" ] || RUN_HOME="$(eval echo "~$RUN_USER")"
export HOME="$RUN_HOME"
# shellcheck source=pilot.conf
. "$HERE/pilot.conf"
: "${TUNNEL_NAME:=kloudc-retailops}"
: "${TUNNEL_HOSTNAME:=app.kloudc.com}"
LABEL="$PILOT_LABEL_PREFIX.tunnel"
DEST="/Library/LaunchDaemons/$LABEL.plist"
TOKEN_FILE="$PILOT_ROOT/secrets/tunnel.token"
CREDS_FILE="$PILOT_ROOT/secrets/tunnel-credentials.json"
CONFIG_OUT="$PILOT_ROOT/config/tunnel.yml"

case "$LABEL" in com.kloudc.retailops.*) ;; *) echo "refusing: unexpected label $LABEL" >&2; exit 1 ;; esac

if [ "$ACTION" = status ]; then
  launchctl print "system/$LABEL" 2>/dev/null | grep -E "state =|pid =" || echo "$LABEL is not running"
  exit 0
fi
if [ "$ACTION" = uninstall ]; then
  [ -n "$DRY_RUN" ] && { echo "would stop and remove $LABEL only"; exit 0; }
  launchctl bootout "system/$LABEL" 2>/dev/null || true
  rm -f "$DEST"
  echo "Removed $LABEL. app.kloudc.com stops answering; the Tailscale address keeps working."
  exit 0
fi

CLOUDFLARED="${CLOUDFLARED:-$(command -v cloudflared || true)}"
[ -n "$CLOUDFLARED" ] || CLOUDFLARED=/opt/homebrew/bin/cloudflared
if [ -z "$DRY_RUN" ]; then
  [ -x "$CLOUDFLARED" ] || { echo "cloudflared not found: brew install cloudflared" >&2; exit 1; }
fi

if [ -f "$TOKEN_FILE" ]; then
  MODE=token
  if [ -z "$DRY_RUN" ]; then
    "$CLOUDFLARED" tunnel run --help 2>/dev/null | grep -q -- "--token-file" \
      || { echo "This cloudflared is too old for --token-file: brew upgrade cloudflared" >&2; exit 1; }
    chown "$RUN_USER" "$TOKEN_FILE"; chmod 600 "$TOKEN_FILE"
  fi
  # Its own (nearly empty) config file, so cloudflared never falls back to a
  # default config another tunnel on this Mac uses (/etc/cloudflared,
  # ~/.cloudflared ...). The routes live in the Cloudflare dashboard.
  out="$PILOT_ROOT/config/tunnel-token.yml"; [ -n "$DRY_RUN" ] && out="$DRY_RUN/tunnel-token.yml"
  mkdir -p "$(dirname "$out")"
  printf '%s\n' "# KloudC RetailOps tunnel (token mode): routes are set in the Cloudflare dashboard." \
    "no-autoupdate: true" > "$out"
  ARGS="        <string>tunnel</string>
        <string>--no-autoupdate</string>
        <string>--config</string>
        <string>$PILOT_ROOT/config/tunnel-token.yml</string>
        <string>run</string>
        <string>--token-file</string>
        <string>$TOKEN_FILE</string>"
elif [ -f "$CREDS_FILE" ]; then
  MODE=config
  out="$CONFIG_OUT"; [ -n "$DRY_RUN" ] && out="$DRY_RUN/tunnel.yml"
  mkdir -p "$(dirname "$out")"
  sed -e "s#__ROOT__#$PILOT_ROOT#g" -e "s#__APP_PORT__#$PILOT_APP_PORT#g" \
      -e "s#app.kloudc.com#$TUNNEL_HOSTNAME#g" -e "s#^tunnel: .*#tunnel: $TUNNEL_NAME#" \
      "$HERE/tunnel/config.yml.template" > "$out"
  ARGS="        <string>tunnel</string>
        <string>--no-autoupdate</string>
        <string>--config</string>
        <string>$CONFIG_OUT</string>
        <string>run</string>
        <string>$TUNNEL_NAME</string>"
else
  echo "No tunnel secret yet. Save the tunnel token from the Cloudflare dashboard into" >&2
  echo "  $TOKEN_FILE" >&2
  echo "(or, for a locally managed tunnel, its credentials JSON into $CREDS_FILE), then run this again." >&2
  exit 1
fi

render() {  # render <out file>
  local tmp; tmp="$(mktemp)"
  printf '%s\n' "$ARGS" > "$tmp"
  sed -e "s#__LABEL_PREFIX__#$PILOT_LABEL_PREFIX#g" -e "s#__ROOT__#$PILOT_ROOT#g" -e "s#__USER__#$RUN_USER#g" \
      -e "s#__APP_PORT__#$PILOT_APP_PORT#g" -e "s#__CLOUDFLARED__#$CLOUDFLARED#g" \
      -e "/__TUNNEL_ARGS__/{
r $tmp
d
}" "$HERE/launchd/tunnel.plist" > "$1"
  rm -f "$tmp"
  plutil -lint -s "$1"
}

if [ -n "$DRY_RUN" ]; then
  mkdir -p "$DRY_RUN"
  render "$DRY_RUN/$LABEL.plist"
  echo "rendered $LABEL ($MODE) into $DRY_RUN (nothing installed)"
  exit 0
fi

mkdir -p "$PILOT_ROOT/run/tunnel-home" "$PILOT_ROOT/logs"
chown "$RUN_USER" "$PILOT_ROOT/run/tunnel-home"
render "$DEST.new"
chown root:wheel "$DEST.new"; chmod 644 "$DEST.new"; mv "$DEST.new" "$DEST"
launchctl bootout "system/$LABEL" 2>/dev/null || true
launchctl bootstrap system "$DEST"
echo "Started $LABEL ($MODE). In a minute, open https://$TUNNEL_HOSTNAME/api/health"
echo "Logs: $PILOT_ROOT/logs/tunnel.err.log"
