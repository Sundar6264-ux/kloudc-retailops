#!/bin/bash
# launchd runs this daily: the pilot store's key numbers against the system
# it still runs on (read-only on both sides). Configure it in
# $PILOT_ROOT/config/parity.env (outside git):
#   PARITY_STORE=CODE
#   PARITY_REFERENCE=/path/to/the/reference/store.db
#   PARITY_DAYS=14
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_env
if [ ! -f "$PARITY_ENV" ]; then say "parity: $PARITY_ENV not set up - nothing to do"; exit 0; fi
set -a; . "$PARITY_ENV"; set +a
[ -n "${PARITY_STORE:-}" ] && [ -n "${PARITY_REFERENCE:-}" ] || { say "parity: PARITY_STORE / PARITY_REFERENCE blank"; exit 0; }
cd "$BACKEND_DIR"
exec env SCHEDULER_ENABLED=false "$VENV_PY" -m app.platform parity "$PARITY_STORE" --reference "$PARITY_REFERENCE" \
  --days "${PARITY_DAYS:-14}" --log-dir "$LOG_DIR"
