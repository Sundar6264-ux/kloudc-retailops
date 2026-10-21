#!/bin/bash
# Run one server command with this install's settings loaded, as the normal
# user (no sudo). The go-live runbook's commands use it, e.g.
#   deploy/pilot-mac/retailops.sh ops check-config
#   deploy/pilot-mac/retailops.sh platform pilot-mode STORE_CODE off
#   deploy/pilot-mac/retailops.sh platform demo-provision
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[ "$(id -u)" != 0 ] || die "run retailops.sh as the normal user (no sudo)"
case "${1:-}" in
  platform|ops) ;;
  *) die "usage: retailops.sh platform|ops COMMAND [ARGS...]" ;;
esac
module="$1"; shift
load_env
cd "$BACKEND_DIR"
exec env SCHEDULER_ENABLED=false PILOT_ROOT="$PILOT_ROOT" "$VENV_PY" -m "app.$module" "$@"
