# Parity harness (multi-store plan, batch MT1)

M1's golden rule is **zero behaviour change for the pilot store**. This harness
is how every M1 batch proves it: record what the app answers on a copy of
the production database before the change, record again after, diff. The
batch may ship only with **0 differences**.

    parity_record.py    boot the app on a copy, record the answers
    parity_diff.py      compare two recordings
    parity_endpoints.py the list of endpoints recorded (edit here to add one)
    parity_sandbox.py   environment, network guard, background-thread guard
    parity_clock.py     the frozen "now"
    parity_fingerprint.py  row counts + per-column checksums of every table

## What a recording holds

- `endpoints/*.json` - 110 read endpoints called as an admin, each with its
  status code and full answer: Items Sold for each of the last 14 days plus
  21/30/60/90 days ago and 7/30-day ranges (= sales by day and the Overview
  sales tile), the CSV exports, Reorder Candidates 30/7 and 90/14 (all and
  critical only), Forecast, Supplier Projection for the top 10 vendors (by
  items in the newest upload), Vendor Calendar 2 and 20 days, Fix Issues
  summary + all 10 lists + closed report, Open Checks (cached all-time list),
  the inventory reports (price changes, store vs container, barcodes, dead
  stock, negative, duplicates, vendor prices), placed orders / cart / PO
  sends, every Insights page (cache only), PO sections, Inventory-from-Toast
  status pages, team task summary/stats/list, invoices, notifications,
  checklists overview, Fix Issues leads, and `/api/auth/me`, `/api/app/config`.
- `alerts.json` - what each push trigger (critical vendors, order due, daily
  sales, open checks, closing checklist, ...) would send at 09:00 and at
  21:30 on the frozen day, and each person's Morning summary lines.
  Computed only; nothing is sent.
- `scheduler_jobs.json` - every job the three schedulers would register in
  production (id, function name, trigger, args), without starting them.
- `fingerprint.json` - every table's row count and a checksum per column.
- `fingerprint_after.json` - the same, taken after all the calls: read
  endpoints must not write. On the Oct 15 copy the only change is the
  harness's own login row in `user_sessions` (its random `token` column is
  left out).
- `meta.json` - the frozen now, user, top vendors, Python version, timings,
  git commit, blocked network attempts, background threads not run.

Endpoint answers are written with the server's JSON key order (format 2):
the website lists some objects in key order (Fix Issues `by_reason` /
`by_type`, checklist schemas), so the diff reports a **key order change**
between two format 2 recordings. It also reports a JSON **type change**,
including `3` -> `3.0` (the app's Dart models cast `json['id'] as int`,
which throws on a double), and a changed content type or CSV download name.

## How it stays honest and deterministic

- It works on a **copy** (SQLite backup API from a read-only handle) in a
  scratch folder, deleted afterwards. The file you pass is never written.
- The app runs in a child process with schedulers off, every outside-service
  secret blank, the scratch folder as working directory (so no `.env` is
  read), `PYTHONHASHSEED=0`, `TZ=America/New_York`.
- **Network is blocked** at the socket level (Toast, Whapi, SMTP, Firebase,
  Anthropic, DNS); attempts are listed in `meta.json`. On the Oct 15 copy
  there were none.
- **Background threads started by app code are not run** (Open Checks
  refresh, Insights fill, Fix Issues forecast refresh): they'd call Toast and
  write at a time decided by thread scheduling. Listed in `meta.json`.
- **"Now" is frozen** (default: 12:00 store time on the day after the newest
  synced business date - for the Oct 15 copy, 2026-10-15 12:00 EDT). Override
  with `--now 2026-10-15T12:00:00-04:00`.

Proof, Oct 15 production copy (430 MB, migration 0059): recorded twice with
Python 3.12 -> **0 differences**; twice with Python 3.9 -> **0 differences**.
Changing one sold quantity on one order line -> 19 differences in 8 files
(Items Sold day and ranges, CSV exports, Forecast, the orders checksum).

**Use one Python for both sides.** 3.12's `sum()` adds floats with
compensated summation, so 3.9 and 3.12 round a few forecast numbers a cent
apart (23 values on the Oct 15 copy). The diff tool refuses to compare
recordings from different Python versions. Production runs 3.12; the
baseline is a 3.12 recording.

## Baseline

The baseline for the Oct 15 production copy is outside git (it holds
business data): `$M/baseline/`, where `$M` is a private scratch folder outside git
(Python 3.12), plus `baseline-py39/` for quick checks with the dev venv.

## Checking a batch

From `website/backend` (or the worktree's `backend`):

    PY=.venv312/bin/python          # the prod runtime; the baseline is 3.12
    M=${PARITY_WORK:?a private scratch folder outside git}
    nice -n 15 $PY scripts/parity/parity_record.py --db $M/prodcopy.db --out $M/after-MTn --force
    $PY scripts/parity/parity_diff.py $M/baseline $M/after-MTn

For a batch that adds a migration, add `--migrate`: the scratch copy is
upgraded with this checkout's `alembic upgrade head` before it is
fingerprinted and recorded. New tables / columns / JSON keys are listed as
**additions** (allowed in M1); `--strict` counts them as differences. A new
or changed push alert or scheduled job is always a difference.

## On PostgreSQL (platform track A)

`--pg-url` records the same copy on PostgreSQL: the scratch copy (after
`--migrate`) is copied into an EMPTY database on this machine with
`scripts/pg/copy_sqlite_to_pg.py` (checksums must match: 0 differences),
and the app boots against PostgreSQL. "Now", user and vendors are still
picked from the SQLite copy, so a SQLite and a PostgreSQL recording of the
same file compare one to one. `fingerprint_after.json` is then the
PostgreSQL database's fingerprint in SQLite's terms
(`parity_fingerprint.pg_fingerprint`); `meta.json` lists the columns whose
SQLite values are stored in an older text form (same instant, e.g. a
timestamp without microseconds) under `postgres_copy`.

    PG=/opt/homebrew/opt/postgresql@16/bin
    $PG/createdb -h 127.0.0.1 -p 5433 -U retailops -T template0 -l C -E UTF8 so_a_parity
    nice -n 15 $PY scripts/parity/parity_record.py --db $M/prodcopy.db --migrate --out $M/rec-sqlite --force
    nice -n 15 $PY scripts/parity/parity_record.py --db $M/prodcopy.db --migrate --out $M/rec-pg --force \
        --pg-url postgresql+psycopg://storeops@127.0.0.1:5433/so_a_parity
    $PY scripts/parity/parity_diff.py $M/rec-sqlite $M/rec-pg
    $PG/dropdb -h 127.0.0.1 -p 5433 -U retailops so_a_parity

Result on the Oct 18 production copy (0059, migrated to 0062): 110 endpoints,
all 200, **12 differences, all explained** (docs/PLATFORM_CONTRACT.md,
"As built - A"): 2 forecast floats in the last digit (PostgreSQL's exact
decimal sum vs SQLite's binary one), 4+4 fingerprint columns stamped by
`--migrate` itself (two SQLite recordings differ in the same 8), 2
fingerprint_after columns holding second-precision timestamp text in SQLite.

## Re-recording the baseline

Only on a fresh production copy, and only from code that is known-good
(the deployed commit), never from a batch under test:

    M=${PARITY_WORK:?a private scratch folder outside git}
    sqlite3 "file:$ORIGIN_DB?mode=ro" ".backup $M/prodcopy.db"   # ORIGIN_DB: the live SQLite file (read-only)
    git checkout <deployed commit>
    nice -n 15 .venv312/bin/python scripts/parity/parity_record.py --db $M/prodcopy.db --out $M/baseline --force
    nice -n 15 .venv312/bin/python scripts/parity/parity_record.py --db $M/prodcopy.db --out $M/baseline-check --force
    .venv312/bin/python scripts/parity/parity_diff.py $M/baseline $M/baseline-check   # must be 0

A recording takes ~50 s on the Mac mini (fingerprint ~10 s).

## Sync code: old vs new, row by row (`sync_compare.py`, batch MT3)

The endpoint recordings above check what the app *reads*. A batch that
changes how the POS sync *writes* (MT3 moved it to `app/pos/sync/`) is also
checked by running the old and the new sync code over the same recorded
Toast answers on the same prod copy and comparing every row they write:

    M=${PARITY_WORK:?a private scratch folder outside git}
    git archive <old commit> backend/app | tar -x -C $M/old_tree
    .venv312/bin/python scripts/parity/sync_compare.py run --db $M/prodcopy.db --code $M/old_tree/backend --out $M/cmp/old
    .venv312/bin/python scripts/parity/sync_compare.py run --db $M/prodcopy.db --code . --out $M/cmp/new
    .venv312/bin/python scripts/parity/sync_compare.py diff $M/cmp/old $M/cmp/new    # DIFFERENCES: 0

The Toast order answers are rebuilt from the copy's own `orders` rows (the
app keeps no raw order JSON), varied on purpose (voids at every level, bad
timestamps, open orders, edits after the first sync); the menus and stock
answers are the real Oct 11 recordings (`--menus` / `--stock`). The scenario
and the dump are described at the top of the script. One run takes ~2 min
(3.12) and needs a scratch copy of the database (~450 MB, deleted after).
