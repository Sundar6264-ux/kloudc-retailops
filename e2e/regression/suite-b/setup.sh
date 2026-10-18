#!/bin/zsh
# Suite B setup: fresh READ-ONLY copy of the prod DB -> migrate to head -> temp users
# -> website built from git HEAD (plus any working-tree files named in SUITE_B_OVERLAY).
# Never writes the prod DB: sqlite3 opens it with mode=ro and only runs .backup.
set -e
HERE=${0:A:h}
W=${SUITE_B_WORK:-/Users/sundar/.claude/jobs/e26a1611/tmp/suiteB}
REPO=${SUITE_B_REPO:-/Users/sundar/spicetown-dev/website}
PROD=/Users/sundar/spicetown-backend/backend/spicetown.db
mkdir -p $W && cd $W
if [[ "$1" != "--keep-db" ]]; then
  rm -f prodcopy.db prodcopy.db-wal prodcopy.db-shm
  nice -n 15 sqlite3 "file:$PROD?mode=ro" ".backup $W/prodcopy.db"
  (cd $REPO/backend && env -i HOME=$HOME PATH=/usr/bin:/bin DATABASE_URL="sqlite:///$W/prodcopy.db" \
     TOAST_CLIENT_ID= WHAPI_API_TOKEN= nice -n 15 .venv/bin/alembic upgrade head)
fi
# The copy's existing flyers need their files, or the library's thumbnails 404 and the sweep
# fails ("no failed API calls"). A plain copy of the real folder (only ever read).
FLYERS_SRC="$HOME/Spice Town Flyers"
if [[ "$1" != "--keep-db" && -d "$FLYERS_SRC" ]]; then
  rm -rf $W/flyers && mkdir -p $W/flyers && rsync -a "$FLYERS_SRC"/ $W/flyers/
fi
(cd $REPO/backend && env -i HOME=$HOME PATH=/usr/bin:/bin SUITE_B_WORK=$W SUITE_B_REPO=$REPO DATABASE_URL="sqlite:///$W/prodcopy.db" \
   TOAST_CLIENT_ID= WHAPI_API_TOKEN= nice -n 15 .venv/bin/python $HERE/mkusers.py)
if [[ "$1" != "--no-build" && "$2" != "--no-build" ]]; then
  rm -rf $W/src && mkdir -p $W/src
  (cd $REPO && git archive HEAD frontend | tar -x -C $W/src)
  for f in ${=SUITE_B_OVERLAY:-}; do cp $REPO/frontend/src/$f $W/src/frontend/src/$f; done
  ln -s $REPO/frontend/node_modules $W/src/frontend/node_modules
  (cd $W/src/frontend && nice -n 15 npm run build -- --outDir $W/dist --emptyOutDir 2>&1 | tail -2)
fi
