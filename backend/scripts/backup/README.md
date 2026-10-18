# Backups (multi-store plan, batch MT1)

Two layers, both off by default until installed:

| Layer | What | Where | Status |
|---|---|---|---|
| Nightly copy | `backup_db.py`: consistent copy at 03:30, `integrity_check`, gzip, kept 30 days | `~/spicetown-backups/nightly` on the Mac mini | Ready - install the LaunchAgent below |
| Off-site, continuous | Litestream streams every change to Cloudflare R2 (~10 s behind), 30 days of point-in-time history | R2 bucket, folder `stores/spicetown/` | **Waiting for the owner's R2 keys** |

The nightly copy protects against a bad deploy, a broken migration or a
deleted row ("give me last Tuesday's database"). It lives on the same Mac,
so it does not protect against losing the Mac - that is what R2 is for.

Plus a **restore drill** (`restore_drill.py`) that proves a backup actually
restores: run it after installing, then once a month.

All three scripts use only the Python standard library.

## Nightly backup

What one run does (`backup_db.py`, details in its docstring):
1. checks there is twice the database's size free;
2. copies the database with SQLite's online backup API from a **read-only**
   connection (one consistent snapshot; the server keeps writing meanwhile);
3. runs `PRAGMA integrity_check` on the copy - anything but `ok` fails the
   run, keeps the copy as `*.corrupt` and deletes nothing;
4. gzips it (430 MB -> 120 MB on the Oct 15 database) and reads it back;
5. only then deletes backups older than 30 days - never the newest one;
6. appends one line to `backup.log` and rewrites `last_backup.json`.

On the Oct 15 production database (read-only): 14.5 s in all, 119,786,771 bytes.

Run it by hand:

    python3 /Users/sundar/spicetown-backend/backend/scripts/backup/backup_db.py \
        --db /Users/sundar/spicetown-backend/backend/spicetown.db \
        --dest ~/spicetown-backups/nightly

Install as a LaunchAgent (runs 03:30 daily, before the 04:00 jobs):

    mkdir -p ~/spicetown-backups/nightly
    cp /Users/sundar/spicetown-backend/backend/scripts/backup/com.spicetown.backup.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.spicetown.backup.plist
    launchctl kickstart gui/$(id -u)/com.spicetown.backup     # one run now, to check
    tail -1 ~/spicetown-backups/nightly/backup.log            # expect "... OK spicetown-....db.gz ..."

A LaunchAgent only runs while `sundar` is logged in to the Mac (it is, on
the Mac mini today). If the Mac is ever run with nobody logged in, install
the same file as a LaunchDaemon instead: add `<key>UserName</key><string>sundar</string>`,
copy it to `/Library/LaunchDaemons/` and `sudo launchctl bootstrap system ...`.

To stop: `launchctl bootout gui/$(id -u)/com.spicetown.backup`.

## Restore drill

    python3 /Users/sundar/spicetown-backend/backend/scripts/backup/restore_drill.py \
        --backup-dir ~/spicetown-backups/nightly \
        --compare-db /Users/sundar/spicetown-backend/backend/spicetown.db

Takes the newest backup, restores it into a temporary folder, runs
`integrity_check` and `foreign_key_check`, counts every table's rows (next to
the live database's counts, opened read-only), shows the migration head and
the newest sales / upload / job dates, prints a report and deletes the
temporary copy. Exit code 0 = the backup is good. It never writes to the live
database.

**To actually restore** (only with the server stopped):

    # 1. Unpack next to the live file first (takes a few seconds; the server keeps running).
    cd /Users/sundar/spicetown-backend/backend
    gunzip -c ~/spicetown-backups/nightly/spicetown-YYYYMMDD-HHMMSS.db.gz > spicetown.db.restoring
    # 2. Stop the watchdog FIRST: every 5 minutes it re-starts a backend it finds
    #    stopped, which would open a half-swapped database. Then stop the server.
    sudo launchctl bootout system/com.spicetown.watchdog
    sudo launchctl bootout system/com.spicetown.backend
    # 3. Keep the old database WITH its -wal/-shm (the -wal can hold the newest
    #    writes if the server crashed), then swap the restored file in.
    B=spicetown.db.broken-$(date +%Y%m%d-%H%M%S)
    mv spicetown.db $B; [ -f spicetown.db-wal ] && mv spicetown.db-wal $B-wal; [ -f spicetown.db-shm ] && mv spicetown.db-shm $B-shm
    mv spicetown.db.restoring spicetown.db
    # 4. Start the server, then the watchdog.
    sudo launchctl bootstrap system /Library/LaunchDaemons/com.spicetown.backend.plist
    sudo launchctl bootstrap system /Library/LaunchDaemons/com.spicetown.watchdog.plist

(If Litestream is running, stop it before step 2 too - see below.)

## Off-site: Litestream to Cloudflare R2 (owner steps)

Nothing here runs until these steps are done. No key is in git or in any
plist; never paste keys into a chat or a ticket.

1. **Create the bucket.** Cloudflare dashboard -> R2 Object Storage -> Create
   bucket. Name e.g. `kloudc-store-backups`, location Automatic, default
   (private) access. One bucket serves every store: each store gets its own
   folder (`stores/<store>/`).
2. **Create a key for this Mac.** R2 -> Manage R2 API Tokens -> Create API
   token: permission **Object Read & Write**, "Apply to specific buckets
   only" = the bucket above, no expiry. Copy the **Access Key ID** and the
   **Secret Access Key** (shown once), and note the **Account ID** (R2
   overview page).
3. **Install Litestream** on the Mac mini: `brew install litestream`, then
   `litestream version`.
4. **Put the config and keys in place** (as `sundar`):

       mkdir -p ~/.config/spicetown && chmod 700 ~/.config/spicetown
       cp /Users/sundar/spicetown-backend/backend/scripts/backup/litestream.yml.template ~/.config/spicetown/litestream.yml
       # edit it: replace REPLACE_WITH_ACCOUNT_ID and REPLACE_WITH_BUCKET_NAME
       printf 'LITESTREAM_ACCESS_KEY_ID=%s\nLITESTREAM_SECRET_ACCESS_KEY=%s\n' 'PASTE_ID' 'PASTE_SECRET' > ~/.config/spicetown/litestream.env
       chmod 600 ~/.config/spicetown/litestream.env

5. **Check it:** `/Users/sundar/spicetown-backend/backend/scripts/backup/run_litestream.sh --check`
   should list the database.
6. **Install the LaunchDaemon:**

       sudo cp /Users/sundar/spicetown-backend/backend/scripts/backup/com.spicetown.litestream.plist /Library/LaunchDaemons/
       sudo launchctl bootstrap system /Library/LaunchDaemons/com.spicetown.litestream.plist
       tail -f /Users/sundar/spicetown-backend/backend/logs/litestream.log

   After a minute the bucket has `stores/spicetown/db/generations/...`.
7. **Prove a restore from R2** (into /tmp, never over the live file):

       litestream restore -config ~/.config/spicetown/litestream.yml -o /tmp/r2-restore.db /Users/sundar/spicetown-backend/backend/spicetown.db
       python3 /Users/sundar/spicetown-backend/backend/scripts/backup/restore_drill.py --backup /tmp/r2-restore.db \
           --compare-db /Users/sundar/spicetown-backend/backend/spicetown.db
       rm /tmp/r2-restore.db

Things to know about Litestream:
- It needs the database in WAL mode (the app sets it) and takes over WAL
  checkpointing. The app never forces a checkpoint or VACUUM (checked Oct 15),
  so they don't fight.
- Never run two Litestream processes against the same database or the same
  bucket folder.
- Stop it (`sudo launchctl bootout system/com.spicetown.litestream`) before
  restoring a database file over the live one, and start it again after:
  it then starts a fresh generation.
- Cost: storage of ~120 MB of snapshots + WAL per store per month is well
  inside R2's free tier (10 GB); R2 has no egress fees.
