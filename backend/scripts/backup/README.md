# Backups

Two layers, both off until set up:

| Layer | What | Where | Status |
|---|---|---|---|
| Nightly copy | `backup_db.py`: consistent copy at 03:30, `integrity_check`, gzip, kept 30 days | a folder on the same machine, e.g. `~/storeops-backups/nightly` | Ready - install below |
| Off-site, continuous | Litestream streams every change to Cloudflare R2 (~10 s behind), 30 days of point-in-time history | R2 bucket, one folder per store: `stores/<STORE_CODE>/` | **Waiting for the owner's R2 bucket and keys** |

The nightly copy protects against a bad deploy, a broken migration or a
deleted row ("give me last Tuesday's database"). It lives on the same
machine, so it does not protect against losing the machine - that is what R2
is for.

Plus a **restore drill** (`restore_drill.py`) that proves a backup actually
restores: run it after installing, then once a month.

All three scripts use only the Python standard library.

Paths below use `$STOREOPS_DIR` for the checkout (e.g. `/opt/storeops`) and
`storeops.db` for the store database (`DATABASE_URL`). **In Docker** the
database is `/data/storeops.db` in the store's volume and Litestream runs as
the `litestream` service of `docker-compose.yml` (see "In Docker" below).

## Nightly backup

What one run does (`backup_db.py`, details in its docstring):
1. checks there is twice the database's size free;
2. copies the database with SQLite's online backup API from a **read-only**
   connection (one consistent snapshot; the server keeps writing meanwhile);
3. runs `PRAGMA integrity_check` on the copy - anything but `ok` fails the
   run, keeps the copy as `*.corrupt` and deletes nothing;
4. gzips it (roughly 3.5x smaller) and reads it back;
5. only then deletes backups older than 30 days - never the newest one;
6. appends one line to `backup.log` and rewrites `last_backup.json`.

A database of about 430 MB takes about 15 s.

Run it by hand:

    python3 $STOREOPS_DIR/backend/scripts/backup/backup_db.py \
        --db $STOREOPS_DIR/backend/storeops.db \
        --dest ~/storeops-backups/nightly

Install as a LaunchAgent on a Mac (runs 03:30 daily, before the 04:00 jobs).
Replace `__STOREOPS_DIR__` and `__BACKUP_DIR__` in the template first:

    mkdir -p ~/storeops-backups/nightly
    sed -e "s#__STOREOPS_DIR__#$STOREOPS_DIR#g" -e "s#__BACKUP_DIR__#$HOME/storeops-backups/nightly#g" \
        $STOREOPS_DIR/backend/scripts/backup/com.kloudc.storeops.backup.plist > ~/Library/LaunchAgents/com.kloudc.storeops.backup.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kloudc.storeops.backup.plist
    launchctl kickstart gui/$(id -u)/com.kloudc.storeops.backup     # one run now, to check
    tail -1 ~/storeops-backups/nightly/backup.log                    # expect "... OK storeops-....db.gz ..."

A LaunchAgent only runs while its user is logged in. If the machine runs
with nobody logged in, install the same file as a LaunchDaemon instead: add
`<key>UserName</key><string>THE_USER</string>`, copy it to
`/Library/LaunchDaemons/` and `sudo launchctl bootstrap system ...`. On Linux,
run the same command from cron or a systemd timer.

To stop: `launchctl bootout gui/$(id -u)/com.kloudc.storeops.backup`.

## Restore drill

    python3 $STOREOPS_DIR/backend/scripts/backup/restore_drill.py \
        --backup-dir ~/storeops-backups/nightly \
        --compare-db $STOREOPS_DIR/backend/storeops.db

Takes the newest backup, restores it into a temporary folder, runs
`integrity_check` and `foreign_key_check`, counts every table's rows (next to
the live database's counts, opened read-only), shows the migration head and
the newest sales / upload / job dates, prints a report and deletes the
temporary copy. Exit code 0 = the backup is good. It never writes to the live
database.

**To actually restore** (only with the server stopped; a Mac install from
`scripts/launchd/`):

    # 1. Unpack next to the live file first (takes a few seconds; the server keeps running).
    cd $STOREOPS_DIR/backend
    gunzip -c ~/storeops-backups/nightly/storeops-YYYYMMDD-HHMMSS.db.gz > storeops.db.restoring
    # 2. Stop the watchdog FIRST: every 5 minutes it re-starts a backend it finds
    #    stopped, which would open a half-swapped database. Then stop the server.
    sudo launchctl bootout system/com.kloudc.storeops.watchdog
    sudo launchctl bootout system/com.kloudc.storeops.backend
    # 3. Keep the old database WITH its -wal/-shm (the -wal can hold the newest
    #    writes if the server crashed), then swap the restored file in.
    B=storeops.db.broken-$(date +%Y%m%d-%H%M%S)
    mv storeops.db $B; [ -f storeops.db-wal ] && mv storeops.db-wal $B-wal; [ -f storeops.db-shm ] && mv storeops.db-shm $B-shm
    mv storeops.db.restoring storeops.db
    # 4. Start the server, then the watchdog.
    sudo launchctl bootstrap system /Library/LaunchDaemons/com.kloudc.storeops.backend.plist
    sudo launchctl bootstrap system /Library/LaunchDaemons/com.kloudc.storeops.watchdog.plist

In Docker the same steps are: unpack into the volume as `storeops.db.restoring`,
`docker compose stop litestream store`, move the old files aside and the
restored file in (keep the `-wal`), `docker compose up -d`.

(If Litestream is running, stop it before step 2 too - see below.)

## Off-site: Litestream to Cloudflare R2 (owner steps)

Nothing here runs until these steps are done. No key is in git or in any
plist; never paste keys into a chat or a ticket.

1. **Create the bucket.** Cloudflare dashboard -> R2 Object Storage -> Create
   bucket. Name e.g. `kloudc-store-backups`, location Automatic, default
   (private) access. One bucket serves every store: each store gets its own
   folder (`stores/<STORE_CODE>/`).
2. **Create a key.** R2 -> Manage R2 API Tokens -> Create API token:
   permission **Object Read & Write**, "Apply to specific buckets only" = the
   bucket above, no expiry. Copy the **Access Key ID** and the **Secret
   Access Key** (shown once), and note the **Account ID** (R2 overview page).

### In Docker (the default)

3. In the store's `.env` (next to `docker-compose.yml`, never in git) fill in
   `LITESTREAM_R2_ACCOUNT_ID`, `LITESTREAM_R2_BUCKET`, `STORE_CODE`,
   `LITESTREAM_ACCESS_KEY_ID` and `LITESTREAM_SECRET_ACCESS_KEY`
   (`chmod 600 .env`).
4. Start it: `docker compose --profile backup up -d litestream`, then
   `docker compose logs litestream`. After a minute the bucket has
   `stores/<STORE_CODE>/db/generations/...`.
5. Prove a restore from R2 into a scratch folder (never over the live file):

       docker compose run --rm --no-deps litestream restore -config /etc/litestream.yml -o /data/r2-restore.db /data/storeops.db
       python3 $STOREOPS_DIR/backend/scripts/backup/restore_drill.py --backup <volume path>/r2-restore.db
       rm <volume path>/r2-restore.db

### On a Mac without Docker

3. **Install Litestream**: `brew install litestream`, then `litestream version`.
4. **Put the config and keys in place** (as the user that runs the store):

       mkdir -p ~/.config/storeops && chmod 700 ~/.config/storeops
       cp $STOREOPS_DIR/backend/scripts/backup/litestream.yml.template ~/.config/storeops/litestream.yml
       # edit it: replace REPLACE_WITH_ACCOUNT_ID, REPLACE_WITH_BUCKET_NAME,
       # REPLACE_WITH_STORE_CODE and REPLACE_WITH_STOREOPS_DIR
       printf 'LITESTREAM_ACCESS_KEY_ID=%s\nLITESTREAM_SECRET_ACCESS_KEY=%s\n' 'PASTE_ID' 'PASTE_SECRET' > ~/.config/storeops/litestream.env
       chmod 600 ~/.config/storeops/litestream.env

5. **Check it:** `$STOREOPS_DIR/backend/scripts/backup/run_litestream.sh --check`
   should list the database.
6. **Install the LaunchDaemon** (replace `__STOREOPS_DIR__`, `__RUN_AS_USER__`
   and `__RUN_AS_HOME__` in the template, as for the backup plist), then:

       sudo launchctl bootstrap system /Library/LaunchDaemons/com.kloudc.storeops.litestream.plist
       tail -f $STOREOPS_DIR/backend/logs/litestream.log

7. **Prove a restore from R2** (into /tmp, never over the live file):

       litestream restore -config ~/.config/storeops/litestream.yml -o /tmp/r2-restore.db $STOREOPS_DIR/backend/storeops.db
       python3 $STOREOPS_DIR/backend/scripts/backup/restore_drill.py --backup /tmp/r2-restore.db \
           --compare-db $STOREOPS_DIR/backend/storeops.db
       rm /tmp/r2-restore.db

Things to know about Litestream:
- It needs the database in WAL mode (the app sets it) and takes over WAL
  checkpointing. The app never forces a checkpoint or VACUUM, so they don't
  fight.
- Never run two Litestream processes against the same database or the same
  bucket folder.
- Stop it (`sudo launchctl bootout system/com.kloudc.storeops.litestream`, or
  `docker compose stop litestream`) before restoring a database file over the
  live one, and start it again after: it then starts a fresh generation.
- Cost: storage of ~120 MB of snapshots + WAL per store per month is well
  inside R2's free tier (10 GB); R2 has no egress fees.
