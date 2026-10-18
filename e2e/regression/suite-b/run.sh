#!/bin/zsh
# Run all of Suite B against the throwaway server (start it first: setup.sh, mock_whapi.py, serve.sh - see README.md).
# Usage: run.sh [script ...]   (default: every flow at 1280 1024 375, then the sweep)
HERE=${0:A:h}
W=${SUITE_B_WORK:-/Users/sundar/.claude/jobs/e26a1611/tmp/suiteB}
mkdir -p $W/logs
[[ -f $W/fixtures/voice.m4a ]] || $HERE/fixtures.sh
# (zsh does not split a ${@:-...} default into words - it ran one script named "team chat ... sweep")
if (( $# )); then scripts=($@); else scripts=(team chat flyers insights checklists admin sweep); fi
rc=0
for s in $scripts; do
  if [[ $s == sweep ]]; then args=(); else args=(1280 1024 375); fi
  print -n "$s ... "
  if nice -n 15 node $HERE/$s.mjs $args > $W/logs/$s.log 2>&1; then print "ok"; else print "FAILED (see $W/logs/$s.log)"; rc=1; fi
  grep -E "^  FAIL" $W/logs/$s.log | head -20
done
exit $rc
