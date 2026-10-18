#!/bin/zsh
# One-time setup for a regression run (re-run it to start from a fresh prod copy):
#  1. a read-only .backup of the live DB into REG_WORK (the live file is opened mode=ro),
#  2. Alembic upgrade head on the COPY,
#  3. temp logins in the copy (mkusers.py),
#  4. REG_WORK/pristine.db = that state (ctl.sh reset restores it between specs),
#  5. the website built from git HEAD (REG_BUILD=worktree builds the working tree instead)
#     into REG_WORK/dist.
# Delete REG_WORK afterwards (it holds a copy of real store data): ctl.sh clean.
set -eu
HERE=${0:A:h}
source $HERE/env.sh
mkdir -p $REG_WORK
cd $REG_WORK
rm -f copy.db copy.db-wal copy.db-shm pristine.db pristine.db-wal pristine.db-shm
nice -n 15 sqlite3 "file:$REG_PROD_DB?mode=ro" ".backup $REG_WORK/copy.db"
sqlite3 copy.db "PRAGMA integrity_check" | grep -qx ok
CLEAN_ENV=(env -i HOME=$HOME PATH=/usr/bin:/bin REG_WORK=$REG_WORK DATABASE_URL="sqlite:///$REG_WORK/copy.db"
  SCHEDULER_ENABLED=false NOTIFICATIONS_ENABLED=false TOAST_CLIENT_ID= TOAST_CLIENT_SECRET= WHAPI_API_TOKEN=
  GMAIL_ADDRESS= GMAIL_APP_PASSWORD= ANTHROPIC_API_KEY= FIREBASE_CREDENTIALS_FILE= FLYERS_DIR=$REG_WORK/flyers)
(cd $REG_REPO/backend && $CLEAN_ENV nice -n 15 $REG_REPO/backend/.venv/bin/alembic \
   upgrade head)
echo "copy at: $(sqlite3 copy.db 'select version_num from alembic_version')"
(cd $REG_WORK && $CLEAN_ENV PYTHONPATH=$REG_REPO/backend nice -n 15 $REG_PY $HERE/mkusers.py)
sqlite3 copy.db "PRAGMA wal_checkpoint(TRUNCATE)" >/dev/null
cp copy.db pristine.db

$HERE/build.sh
