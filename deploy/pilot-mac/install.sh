#!/bin/bash
# KloudC RetailOps pilot on this Mac - the ONE command the owner runs:
#
#   sudo ~/storeops/storeops/deploy/pilot-mac/install.sh
#
# What it does (docs/PILOT_RUNBOOK.md has the long version):
#  1. as you (not root): prepare.sh - folders under ~/kloudc-retailops, the
#     release checkout, its venv and website build, the PostgreSQL cluster
#     (port 5434, its own data folder - not the dev cluster), config + key ring;
#  2. installs and starts the database LaunchDaemon, then as you: setup-db.sh
#     (platform database, migrations, the first master - asks for a password);
#  3. installs and starts the server (127.0.0.1:8900), the nightly backup and
#     DEMO refresh (a no-op until the DEMO store exists),
#     the monthly restore drill and the daily parity check;
#  4. waits for /api/ready, then `tailscale serve` on the tailnet only
#     (https://<this Mac>.ts.net:10003).
# Only com.kloudc.retailops.pilot.* launchd jobs are ever touched. Safe to run
# again. Options: --no-tailscale  --skip-web  --dry-run (render the plists to
# a folder and stop; no root needed)  --uninstall (stop and remove the jobs;
# data, backups and secrets stay).
#
# OPTIONAL, never part of the default path (docs/DEPLOY_MULTI_PROCESS.md):
#   --worker         install + start only the background job worker
#                    (needs JOB_QUEUE_MODE=queue in config/pilot.env);
#                    with --dry-run, also render its plist
#   --remove-worker  stop and remove only the worker
#   --demo-job       install + start only the nightly DEMO refresh job (an
#                    install made before the DEMO store existed); with
#                    --dry-run, render only its plist
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TAILSCALE=1; DRY_RUN=""; UNINSTALL=0; PREP_ARGS=(); WORKER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-tailscale) TAILSCALE=0 ;;
    --skip-web) PREP_ARGS+=(--skip-web) ;;
    --dry-run) DRY_RUN="$2"; shift ;;
    --uninstall) UNINSTALL=1 ;;
    --worker) WORKER=install ;;
    --remove-worker) WORKER=remove ;;
    --demo-job) WORKER=demo ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -n "$DRY_RUN" ]; then
  RUN_USER="$(id -un)"
else
  [ "$(id -u)" = 0 ] || { echo "Run it with sudo: sudo $0" >&2; exit 1; }
  RUN_USER="${SUDO_USER:-}"
  [ -n "$RUN_USER" ] && [ "$RUN_USER" != root ] || { echo "Run it with sudo from your own account (SUDO_USER is empty)" >&2; exit 1; }
fi
RUN_HOME="$(dscl . -read "/Users/$RUN_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
[ -n "$RUN_HOME" ] || RUN_HOME="$(eval echo "~$RUN_USER")"
export HOME="$RUN_HOME"
# shellcheck source=pilot.conf
. "$HERE/pilot.conf"
export PILOT_ROOT
JOBS=(postgres server backup drill parity demo)

render() {  # render <job> <out file>
  sed -e "s#__LABEL_PREFIX__#$PILOT_LABEL_PREFIX#g" -e "s#__ROOT__#$PILOT_ROOT#g" -e "s#__USER__#$RUN_USER#g" \
      -e "s#__HOME__#$RUN_HOME#g" -e "s#__PG_BIN__#$PILOT_PG_BIN#g" -e "s#__APP_PORT__#$PILOT_APP_PORT#g" \
      -e "s#__BACKUP_HOUR__#$PILOT_BACKUP_HOUR#g" -e "s#__BACKUP_MINUTE__#$PILOT_BACKUP_MINUTE#g" \
      -e "s#__PARITY_HOUR__#$PILOT_PARITY_HOUR#g" -e "s#__PARITY_MINUTE__#$PILOT_PARITY_MINUTE#g" \
      -e "s#__DEMO_HOUR__#$PILOT_DEMO_HOUR#g" -e "s#__DEMO_MINUTE__#$PILOT_DEMO_MINUTE#g" \
      -e "s#__KEEP_DAYS__#$PILOT_KEEP_DAYS#g" "$HERE/launchd/$1.plist" > "$2"
  plutil -lint -s "$2"
}

if [ -n "$DRY_RUN" ]; then
  mkdir -p "$DRY_RUN"
  if [ "$WORKER" = install ]; then
    render worker "$DRY_RUN/$PILOT_LABEL_PREFIX.worker.plist"
    echo "rendered the worker plist into $DRY_RUN (nothing installed)"
    exit 0
  fi
  if [ "$WORKER" = demo ]; then
    render demo "$DRY_RUN/$PILOT_LABEL_PREFIX.demo.plist"
    echo "rendered the DEMO refresh plist into $DRY_RUN (nothing installed)"
    exit 0
  fi
  for j in "${JOBS[@]}"; do render "$j" "$DRY_RUN/$PILOT_LABEL_PREFIX.$j.plist"; done
  echo "rendered ${#JOBS[@]} plists into $DRY_RUN (nothing installed)"
  exit 0
fi

as_user() { sudo -u "$RUN_USER" -H env PILOT_ROOT="$PILOT_ROOT" "$@"; }
daemon_path() { echo "/Library/LaunchDaemons/$PILOT_LABEL_PREFIX.$1.plist"; }
stop_job() { launchctl bootout "system/$PILOT_LABEL_PREFIX.$1" 2>/dev/null || true; }
start_job() {
  local dest; dest="$(daemon_path "$1")"
  render "$1" "$dest.new"
  chown root:wheel "$dest.new"; chmod 644 "$dest.new"; mv "$dest.new" "$dest"
  stop_job "$1"
  launchctl bootstrap system "$dest"
  echo "  started $PILOT_LABEL_PREFIX.$1"
}

if [ "$WORKER" = remove ]; then
  stop_job worker; rm -f "$(daemon_path worker)"
  echo "Removed the worker. Queued jobs wait until a worker runs again (or set JOB_QUEUE_MODE=inline and restart the server)."
  exit 0
fi
if [ "$WORKER" = install ]; then
  grep -Eq "^JOB_QUEUE_MODE=(\"|')?queue(\"|')?\$" "$PILOT_ROOT/config/pilot.env" 2>/dev/null \
    || { echo "JOB_QUEUE_MODE=queue is not set in $PILOT_ROOT/config/pilot.env: no worker needed (jobs run in the server)." >&2; exit 1; }
  start_job worker
  echo "Worker installed. Check it in the master console: Platform settings > System > Workers."
  exit 0
fi

if [ "$WORKER" = demo ]; then
  start_job demo
  echo "Nightly DEMO refresh installed (runs at $PILOT_DEMO_HOUR:$PILOT_DEMO_MINUTE; does nothing until the DEMO store exists)."
  exit 0
fi

if [ "$UNINSTALL" = 1 ]; then
  for j in worker demo parity drill backup server postgres; do stop_job "$j"; rm -f "$(daemon_path "$j")"; done
  echo "Removed the pilot's launchd jobs. Data, backups and secrets are still in $PILOT_ROOT."
  exit 0
fi

echo "== 1. prepare (as $RUN_USER)"
as_user /bin/bash "$HERE/prepare.sh" "${PREP_ARGS[@]+"${PREP_ARGS[@]}"}"
mkdir -p "$PILOT_ROOT/logs"; chown "$RUN_USER" "$PILOT_ROOT/logs"

echo "== 2. database"
start_job postgres
as_user /bin/bash "$HERE/setup-db.sh"

echo "== 3. server and scheduled jobs"
for j in server backup drill parity demo; do start_job "$j"; done

echo "== 4. waiting for http://127.0.0.1:$PILOT_APP_PORT/api/ready"
ready=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PILOT_APP_PORT/api/ready" >/dev/null 2>&1; then ready=1; break; fi
  sleep 3
done
if [ "$ready" = 1 ]; then echo "  ready"; else echo "  NOT ready after 3 minutes: see $PILOT_ROOT/logs/startup.log and server.err.log"; fi

if [ "$TAILSCALE" = 1 ] && command -v tailscale >/dev/null 2>&1; then
  echo "== 5. tailscale serve (tailnet only) on https port $PILOT_TS_HTTPS_PORT"
  current="$(tailscale serve status 2>/dev/null || true)"
  port_re="^https://[^ ]+:$PILOT_TS_HTTPS_PORT( |\$)"
  if printf '%s\n' "$current" | grep -qE "$port_re" \
     && ! printf '%s\n' "$current" | grep -A1 -E "$port_re" | grep -q "127.0.0.1:$PILOT_APP_PORT"; then
    echo "  port $PILOT_TS_HTTPS_PORT is already used by another service - skipped (set PILOT_TS_HTTPS_PORT)"
  else
    tailscale serve --bg --https="$PILOT_TS_HTTPS_PORT" "http://127.0.0.1:$PILOT_APP_PORT" >/dev/null
    echo "  $(grep '^PLATFORM_PUBLIC_URL=' "$PILOT_ROOT/config/pilot.env" | cut -d= -f2- | tr -d "'")"
  fi
fi

cat <<DONE

KloudC RetailOps pilot is installed.
  Sign in at:          $(grep '^PLATFORM_PUBLIC_URL=' "$PILOT_ROOT/config/pilot.env" | cut -d= -f2- | tr -d "'")  (Store ID PLATFORM; https only)
  Health (this Mac):   http://127.0.0.1:$PILOT_APP_PORT/api/ready
  Logs:                $PILOT_ROOT/logs  (error.log first)
  Backups:             $PILOT_ROOT/backups  (nightly $PILOT_BACKUP_HOUR:$(printf '%02d' "$PILOT_BACKUP_MINUTE"), drill on the 1st)
  Secrets:             $PILOT_ROOT/secrets/retailops.env  - keep an OFFLINE copy
  Update later:        $HERE/update.sh   (no sudo)
DONE
