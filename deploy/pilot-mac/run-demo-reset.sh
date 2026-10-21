#!/bin/bash
# launchd runs this nightly (com.kloudc.retailops.pilot.demo): fresh made-up
# data for the App Review DEMO store (app/ops/demo_store.py). Does nothing
# until the owner has made the DEMO store once:
#   python -m app.platform demo-provision      (docs/GO_LIVE_RUNBOOK.md)
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_env
mkdir -p "$PILOT_ROOT/run/demo-build"
cd "$BACKEND_DIR"
exec env SCHEDULER_ENABLED=false PILOT_ROOT="$PILOT_ROOT" "$VENV_PY" -m app.platform demo-reset --skip-if-missing \
  --workdir "$PILOT_ROOT/run/demo-build" --review-password-file "$PILOT_ROOT/secrets/demo_review_password"
