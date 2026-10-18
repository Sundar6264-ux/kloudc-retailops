# Multi-store M1 contract: connector layer with Toast parity

M1 of the multi-store plan (`MULTI_STORE_PLAN.md`, section 11.2) puts Toast
behind a POS connector so a second store on another POS can run the same
code. Its golden rule: **zero behaviour change for Spice Town**. Every API
answer, page, report number, scheduled job, push and WhatsApp/email stays the
same; old module names keep working; API changes are additive only (app
builds 5-9 keep working); migrations are additive, numbered from 0060.

Each batch (MT1-MT6) appends an "As built" section below: what changed, new
modules / tables / endpoints / settings, how parity was checked (numbers),
and what was deferred. The parity harness from MT1 must report **0
differences** on the same production copy after every batch.

---

## As built - MT1 (safety net + parity harness)

Branch `multistore-m1`. No app code changed: MT1 adds scripts, templates and
tests only. Nothing was installed, deployed or restarted.

### 1. Parity harness - `backend/scripts/parity/`

| File | Role |
|---|---|
| `parity_record.py` | Records a database copy's answers into a folder |
| `parity_diff.py` | Compares two recordings; exit 0 = no differences, 1 = differences |
| `parity_endpoints.py` | The 110 recorded endpoint calls (edit here to add one) |
| `parity_sandbox.py` | Harness environment, network guard, background-thread guard |
| `parity_clock.py` | Frozen "now" (no extra dependency) |
| `parity_fingerprint.py` | Row counts + per-column checksums of every table |
| `README.md` | How to use, check a batch, re-record the baseline |

**How a recording is made.** The database is copied with SQLite's backup API
from a read-only handle into a scratch folder (the source is never written;
a test checks its bytes are unchanged). The copy is fingerprinted, then the
app boots in a child process against it: schedulers off, every outside
secret blank (Toast, Toast Analytics, Whapi, Gmail, Anthropic, Firebase), the
scratch folder as working directory (no `.env` read), `PYTHONHASHSEED=0`,
`TZ=America/New_York`. Outbound sockets and DNS raise (attempts are listed;
there were none). Background threads started by app code (Open Checks
refresh, Insights fill, Fix Issues forecast refresh) are recorded but not
run. "Now" is frozen at 12:00 store time on the day after the newest synced
business date (2026-10-15 12:00 EDT for the Oct 15 copy), `--now` to
override. It calls as the lowest-id non-demo admin (`Spicetown_admin`).

**What is recorded.**
- 110 read endpoints with status and full answer: Items Sold for each of
  the last 14 days and 21/30/60/90 days ago, 7- and 30-day ranges (= sales by
  day and the Overview sales tiles), Items Sold CSV, sales CSV export, sales
  accuracy; Reorder Candidates 30/7 and 90/14, each all and critical-only;
  Forecast; suppliers; critical dismissals; Supplier Projection for the top
  10 vendors (most items in the newest upload, chosen by raw SQL so later
  vendor refactors can't change the choice); cart preview; Vendor Calendar 2
  and 20 days (+ with ordered), schedules, schedule suggestions; invoice
  vendors today; Fix Issues reasons, summary, all 10 lists, closed report;
  Open Checks (cached all-time list only); price changes, store vs
  container, missing/invalid barcodes, dead stock, negative inventory,
  duplicates, vendor price comparison; placed orders (all, placed, recent),
  cart, PO sends, vendor contacts; all 10 Insights pages (cache only) plus
  compare for yesterday and menu performance for the kitchen; PO sections
  (sections, options, vendor rules, renames, items, history, changes);
  Inventory-from-Toast status, nightly compare, switch; team task summary,
  stats, list; invoices list and close suggestions; notification defaults;
  checklists overview; Fix Issues leads; `/api/auth/me`, `/api/auth/features`,
  `/api/app/config`, `/api/glossary`, `/api/jobs/status`.
- `alerts.json`: what every push trigger (11 of them) would send at 09:00
  and 21:30 on the frozen day, plus each Morning summary recipient's lines.
  Computed, never dispatched.
- `scheduler_jobs.json`: the 26 jobs the three schedulers register in
  production (15 main, 6 notifications, 5 team): id, function name,
  trigger, args, misfire/coalesce/max-instances. Not started.
- `fingerprint.json`: 58 tables (all but `alembic_version` and `sqlite_*`,
  whose changes are expected/bookkeeping), row count + one checksum per column.

**Diff rules.** A changed value, removed key, list length change, status
change, missing endpoint/table/column, changed row count or column checksum
is a DIFFERENCE. A new JSON key, table or column is an ADDITION (allowed in
M1, listed; `--strict` counts it). A new or changed push alert or scheduled
job is always a difference. Recordings with a different now, user, top-vendor
list or Python minor version are refused as not comparable. The ignore list
is empty: nothing needed ignoring once now, hash seed and threads were fixed.

**Parity proof (Oct 15 production copy, 434 MB, migration 0059, 125,330
order rows):**
- Python 3.12 (prod runtime), recorded twice: **0 differences** (113 files compared).
- Python 3.9, recorded twice: **0 differences**.
- Sensitivity: the same copy with one order line's quantity +1 gives 19
  differences in 8 files (Items Sold d-1 / 7-day / 30-day, both CSVs,
  Forecast, the `orders.quantity` checksum).
- A recording takes ~45-52 s (fingerprint ~9-11 s), all 110 endpoints 200.

**Finding: 3.9 and 3.12 round differently.** Python 3.12's `sum()` uses
compensated float summation, so 23 forecast values (Reorder 30/7 and 90/14,
Supplier Projection top03/top06: `units_sold`, `recent_7/14_day_sales`)
differ by 0.01 between a 3.9 and a 3.12 recording of the same copy. This is
pre-existing and not an M1 change (prod has run 3.12 since Sep 27). The diff
tool therefore refuses to compare recordings from different Python minors.

**Baseline** (not in git - business data):
`/Users/sundar/.claude/jobs/e26a1611/tmp/multitenant/m1/baseline/` (3.12)
and `baseline-py39/` (3.9), recorded from commit 08b43c0 code (MT1 adds no
app code) on `prodcopy.db` in the same folder (the read-only copy made Oct
15 13:48). Every later M1 batch checks against these:

    nice -n 15 .venv312/bin/python scripts/parity/parity_record.py --db $M/prodcopy.db --out $M/after-MTn --force [--migrate]
    .venv312/bin/python scripts/parity/parity_diff.py $M/baseline $M/after-MTn

`--migrate` runs this checkout's `alembic upgrade head` on the scratch copy
first (for MT4's migrations). Re-recording steps are in the README.

### 2. Backups - `backend/scripts/backup/`

| File | Role |
|---|---|
| `backup_db.py` | Nightly backup: read-only online backup, `integrity_check`, gzip + read-back, 30-day retention (never the newest), log line + `last_backup.json` |
| `restore_drill.py` | Restores the newest backup (or any `.db`/`.db.gz`, e.g. a Litestream restore) into a temp folder: `integrity_check`, `foreign_key_check`, row counts (next to the live DB's, read-only), migration head, newest dates; prints a report |
| `com.spicetown.backup.plist` | LaunchAgent template, 03:30 daily, Nice 15, low-priority IO. **Not installed** |
| `litestream.yml.template` | Litestream -> Cloudflare R2 config, folder `stores/spicetown/db`, 30-day retention, placeholders only - no credentials |
| `run_litestream.sh` | Wrapper: stays off (exit 0, says why) until config + `~/.config/spicetown/litestream.env` (chmod 600) exist with real values; refuses a world-readable key file; `--check` mode |
| `com.spicetown.litestream.plist` | LaunchDaemon template (UserName sundar, KeepAlive on failure only). **Not installed** |
| `README.md` | Install steps for the nightly backup, restore steps, and the owner's R2 steps (bucket, token, where the keys go, test restore) |

Safety rules built in: a failed or corrupt backup deletes nothing; the
corrupt copy is kept as `*.corrupt`; partial files are cleaned; a backup
needs twice the database size free; files of another store's prefix are
never touched by retention.

**Run against production (read-only), Oct 15 14:06:**
`2026-10-15T14:06:58 OK spicetown-20261015-140658.db.gz 119786771 bytes integrity=ok tables=59 pruned=0 14.5s`

**Restore drill on that backup:**

    RESTORE DRILL PASSED
      backup:        .../backup-drill/nightly/spicetown-20261015-140658.db.gz (119,786,771 bytes)
      restored:      434,335,744 bytes in 0.7 s (temp copy deleted)
      integrity:     ok (4.5 s)
      tables:        59
      migration:     0059
      newest sales:  2026-10-14
      newest upload: 2026-10-15
      users:         10
      FK problems:   0
      row counts (backup / live now): every table equal, e.g.
        orders 125,330 / 125,330; inventory_snapshots 654,916 / 654,916; job_runs 6,105 / 6,105
      took 6.0 s

(Full output: `/Users/sundar/.claude/jobs/e26a1611/tmp/multitenant/m1/backup-drill/restore_drill_output.txt`.)

### 3. Tests

- `tests/test_parity_harness.py` (16): diff tool (identical -> 0; changed
  number with its path; removed key, list length, status; bool vs number;
  additions allowed / strict; missing vs new endpoint; CSV line diffs;
  fingerprint additions vs changes; a new job or alert is never allowed;
  not-comparable now/Python; CLI exit codes), frozen clock, per-column
  fingerprint, and an end-to-end test that records a seeded database twice
  in subprocesses and gets 0 differences, no network attempts, the source
  file byte-identical and the scratch copies removed.
- `tests/test_backup_scripts.py` (13): checked gzip + log + source never
  written; rows still in the WAL are included; retention (only > 30 days,
  never the newest, never another store's files); failure and corrupt paths
  delete nothing; CLI exit codes; restore drill (newest picked, live
  comparison, damaged and truncated backups fail, plain `.db` accepted);
  plists valid with no credentials; Litestream wrapper stays off until set
  up and refuses a readable key file.

**Full backend suites** (worktree, `-p no:cacheprovider`): Python 3.9 -
2217 passed, 12 skipped, 1 failed; Python 3.12 - 2217 passed, 12 skipped, 1
failed. The one failure, `test_public_checklist_link.py::test_page_paths_trailing_slash_and_nested`,
needs a built `frontend/dist` (the static mount is decided at import); the
fresh worktree has none. With a stub `frontend/dist/index.html` the file
passes (34 passed). Not an MT1 change.

`test_insights_backfill.py::test_real_client_raises_unprocessable_after_new_requests`
also failed at first in the worktree: it relied on a `.env` setting
`TOAST_RESTAURANT_GUID`. Fixed in the test (sets the GUID itself); no app code.

### Deferred / for the owner

- **R2 keys**: Litestream is written and tested up to the point of needing
  the owner's Cloudflare account (README "Off-site" steps 1-7). Not enabled.
- **Installing** the nightly-backup LaunchAgent and the Litestream daemon is
  the owner's call (commands in the README); nothing was installed.
- The harness records as an admin only; a staff-user pass (feature gating,
  "Hide sales numbers") and write paths (WhatsApp/email/PO send payloads)
  are covered by the existing unit tests, not by the harness. WhatsApp
  checklist / PO messages are produced on writes, so they are not in the
  recording.
- The "one week live with 0 differences" exit criterion needs the harness to
  be re-run against fresh production copies after M1 deploys.

### Verification of MT1 (adversarial pass, Oct 15)

MT1 changes no app code (`git diff 08b43c0 -- backend/app backend/alembic frontend` is empty), so
nothing a user sees can have changed. The check was therefore on the safety net itself: what it
would *miss* in later batches.

**Gaps found in the harness and fixed (recording format 2):**

| Gap | Why it matters | Fix |
|---|---|---|
| `3` -> `3.0` (int vs float) was not a difference | The app's Dart models cast `json['id'] as int` (47 strict `as int` / `as double` casts in `mobile/lib`); a float there throws in builds 5-9 | Any JSON type change is a difference |
| Endpoint answers were written with sorted keys, so a key reorder was invisible | The website lists some objects in key order (Fix Issues `by_reason` / `by_type`, checklist `schemas`; the app's `open_by_lead`) | Answers keep the server's key order; the diff reports "key order changed" for the keys both sides have (a new key in the middle is still an allowed addition). Checked only between two format 2 recordings; a format 1 baseline gets a note |
| Content type and CSV download name were recorded/never compared | A changed export file name or content type is visible | `content_type` and `content_disposition` compared |
| The database was fingerprinted only before the calls | A read endpoint that starts writing (a cache row, a lazy back-fill) would not show | `fingerprint_after.json` after all calls; on the Oct 15 copy the only change is the harness's own login row (`user_sessions`, random `token` column left out) |

**Backup README fix:** the manual restore stopped the backend while the production watchdog
(`com.spicetown.watchdog`, every 5 minutes) re-bootstraps a stopped backend - it could start the
server on a half-written database. The steps now unpack to `spicetown.db.restoring` first, stop the
watchdog before the backend, keep the old file's `-wal`/`-shm` (they can hold the newest writes
after a crash) instead of deleting them, swap with one `mv`, and start the watchdog again last.
Test: `test_restore_steps_stop_the_watchdog_and_keep_the_old_wal`.

**Baselines re-recorded in format 2** (app code from `git archive 08b43c0`, harness from this
branch) on the same `prodcopy.db`: `m1/baseline/` (3.12.14) and `m1/baseline-py39/` (3.9.6), each
with a `PROVENANCE.txt`. The builder's format 1 recordings are kept as `baseline-v1-format1/` and
`baseline-py39-v1-format1/`. Recording twice with the same Python: 0 differences (114 files);
multistore-m1 HEAD vs the new baselines: 0 differences on 3.12 and on 3.9. On a second, fresh
read-only production copy (14:29), 08b43c0 vs HEAD: 0 differences; 110/110 endpoints 200, no
network attempts, no background threads. 3.9 vs 3.12 on the same copy: 24 differences in 5 files
(the known float-summation cents), refused by the diff as not comparable.

---

## As built - MT2 (Toast adapter, pure move)

Branch `multistore-m1`. The Toast code moved into `backend/app/pos/` without
changing what it does. Nothing in the app calls Toast differently; the old
module names keep working. No migration, no endpoint, no JSON key, no
scheduled job changed. Nothing was installed, deployed or restarted.

### 1. New package `backend/app/pos/`

| File | What it holds |
|---|---|
| `base.py` | `PosConnector` (Protocol, plan 4.3), `BaseConnector` (capability checks; every method the POS lacks raises `NotSupported`), errors `PosError`, `NotSupported` (-> HTTP 409 later), `PosUnavailable` (-> HTTP 503 later). Re-exports `Capability`. |
| `capabilities.py` | From the prep step, unchanged: 17 flags, `TOAST_CAPABILITIES` (13). |
| `http.py` | `retry_wait_seconds()`: the "how long after a 429" rule (Retry-After, else a reset-time header, else 2/4/6 s, capped), shared so the next POS doesn't copy it. Toast's `_wait_seconds` now calls it with `x-toast-ratelimit-reset`; same numbers. |
| `registry.py` | `get_connector()` reads the new `POS_PROVIDER` setting (default `toast`, blank = toast), one cached connector per provider; an unknown name raises `UnknownPosProvider`. |
| `compat.py` | `install_forwarding()`: makes an old module a view of the moved names (see 3). |
| `adapters/toast/client.py` | Login, **the one token cache** `_TOKEN_CACHE`, `ToastAPIError`, `ToastFetchError`, `toast_get` (429 backoff, re-login once on 401). From `toast_client.py` + `inventory_api.py`. |
| `adapters/toast/orders.py` | `fetch_orders_for_business_date` (ordersBulk). From `toast_client.py`. |
| `adapters/toast/labor.py` | `fetch_employees`. From `toast_client.py`. |
| `adapters/toast/stock.py` | `fetch_stock`, `fetch_stock_live`, `STOCK_PATH`. From `inventory_api.py`. |
| `adapters/toast/catalog.py` | `fetch_menus`, `fetch_menu_last_updated`, `parse_menus`, `MenuItemInfo`, `menu_price`, `merged_barcode`, Each/Container pairing, skipped menus. From `inventory_api.py`. |
| `adapters/toast/timeparse.py` | `parse_toast_time`. From `inventory_api.py`. |
| `adapters/toast/analytics.py` | The whole Analytics (ERA) client. From `services/toast_analytics.py`. |
| `adapters/toast/csv_import.py` | Toast Retail export: `COLUMN_MAP`, required/numeric columns, `InventoryParseError`, file reading. From `inventory_parser.py`. |
| `adapters/toast/connector.py` | `ToastConnector` (source `toast`, name `Toast`, `TOAST_CAPABILITIES`). `catalog_changed_at()` works; the methods that return normalized models raise `NotImplementedError` until MT3. Nothing in the app calls it yet. |

New setting: `pos_provider` (`POS_PROVIDER`), default `"toast"`, in
`config.py`. Production's `.env` doesn't need it.

### 2. What moved, line for line

Every moved function, class and constant was compared with its old source
(AST, after removing the new module prefixes): identical, except three
intended changes:

- `toast_get` calls `get_access_token` / `_drop_token()` in its own module
  instead of `toast_client.get_access_token` / `toast_client._TOKEN_CACHE.pop(...)`.
  `_drop_token()` pops the same key from the same dict, so behaviour is
  identical. This is the plan's "one token cache": the cache had one dict
  already; the direct reach into another module's private dict is gone.
- `_wait_seconds` delegates to `pos/http.py` (same three rules, same cap).
- The two loggers keep their old names (`app.services.inventory_api` for the
  429 warning, `app.services.toast_analytics`), so log lines are unchanged.

The Analytics client keeps its own token: it has its own credentials
(`TOAST_ANALYTICS_CLIENT_ID`), so sharing a cache with the Standard API would
be a behaviour change.

Left where they are on purpose (shared files, small hunks only while MT4-MT6
run in parallel): `inventory_parser.parse_and_store` (the DB write; MT5 fills
`item_vendors` there), and everything POS-neutral in `inventory_api.py`
(`build_api_rows`, `compare_rows`, the live-stock overlay), which MT3 moves to
`pos/sync/`. `sales_sync` / `open_orders` order mapping is MT3's too.

### 3. Old module names

`app/toast_client.py` and `app/services/toast_analytics.py` are now short
shims; `app/services/inventory_api.py` and `app/services/inventory_parser.py`
keep their remaining code with the moved block replaced by imports. All four
use `app.pos.compat.install_forwarding`:

- every name each module had before the move still resolves, to the same
  object (`toast_client` 11 names, `toast_analytics` 48, `inventory_api` 135,
  `inventory_parser` 15 - checked by a test against the list taken at b9501ad);
- **patching the old path patches the adapter**: `monkeypatch.setattr(toast_client,
  "get_access_token", ...)` or `monkeypatch.setattr(inventory_api, "toast_get", ...)`
  reaches the moved code exactly as before, `mock.patch` too, and undo
  restores both. This matters for tests written on the parallel MT4-MT6
  branches against the old paths;
- patching the new path is seen through the old path;
- the one gap: code still in `inventory_api` / `inventory_parser` calls a few
  moved names by bare name (`fetch_stock_live`, `parse_menus`, ...). An
  old-path patch updates that copy too; a new-path patch of one of those
  names does not reach it. MT3 removes that code from `inventory_api`.

Call sites in the rest of the app are unchanged (they import the old names).
New code should import from `app.pos.adapters.toast.*` (or, from MT3, go
through `get_connector()`).

### 4. Tests

- New `tests/test_pos_toast_adapter.py` (40 tests): old names per module,
  identity with the adapter, old-path and new-path patching, `mock.patch`,
  undo, the bare-name copies, one token cache and a 401 dropping the shared
  token, logger names, the 429 rule, registry / `POS_PROVIDER`,
  `NotSupported` for a missing capability, and each moved module importing
  first in a fresh interpreter (no import cycle).
- Re-pointed to the adapter (patches and imports now name
  `app.pos.adapters.toast.*`): `test_inventory_api.py`,
  `test_inventory_api_late_csv.py`, `test_inventory_api_readiness.py`,
  `test_verify2_failure_modes.py`, `test_sales_accuracy_job.py`,
  `test_insights.py`, `test_insights_backfill.py`, `test_insights_verify.py`.
  The other Toast-payload files (`test_open_checks_tab_names.py`,
  `test_sales_items_sold_accuracy.py`, ...) patch `sales_sync` /
  `open_orders`, which didn't move, and stay as they are until MT3. Before
  re-pointing, all 15 Toast-related files passed unchanged on the old paths
  (275 tests), which is the shim check on real tests.
- Full suite, Python 3.9: 2264 passed, 12 skipped, 1 failed. Python 3.12:
  2264 passed, 12 skipped, 1 failed. The failure on both is the known
  `test_public_checklist_link.py::test_page_paths_trailing_slash_and_nested`,
  which needs a built `frontend/dist` that the worktree doesn't have; with a
  stub `frontend/dist/index.html` that file passes (34 passed, 1 skipped;
  the stub was removed afterwards).

### 5. Parity (same `m1/prodcopy.db` as MT1, migration 0059, no `--migrate`: MT2 has no migration)

- Python 3.12 recording vs `m1/baseline/`: **0 differences** across 114
  files (also 0 with `--strict`, so not even an added key, table or
  column); 110 of 110 endpoints answered 200; 0 network attempts; 0
  background threads.
- Python 3.9 recording vs `m1/baseline-py39/`: **0 differences** across 114
  files.
- Scheduled jobs (`scheduler_jobs.json`) and push alerts (`alerts.json`) are
  part of those 114 files: identical.

### 6. Deferred

- To MT3: normalized models; `ToastConnector.fetch_orders` / `fetch_open_orders`
  / `fetch_catalog` / `fetch_stock` / `fetch_employees` / `get_day_totals` /
  `run_report` / `parse_catalog_export` / `health` (now `NotImplementedError`);
  moving the order mapping (`sales_sync._flatten_selection`,
  `_parse_toast_timestamp`, `open_orders` normalizers) and the POS-neutral
  `inventory_api` code into `pos/sync/`; then switching callers to
  `get_connector()`.
- `inventory_parser.parse_and_store` keeps the row loop (header matching,
  "$"/","/"%" cleaning); MT3's `parse_catalog_export` should take the parsing
  half without touching MT5's `item_vendors` write.
- `toast_email.py` / `toast_pdf.py` -> `pos/ap_inbox/` is MT6's (AP inbox rename).
- `insights_data.REPORTS` (the ERA report specs) stays in `insights_data`
  until `run_report()` is built.

### Verification of MT2 (adversarial pass, Oct 15)

No problem was confirmed, so no code changed in this pass. What was checked:

- **Moved code against the old code.** Every name each old module had at b9501ad
  (11 in `toast_client`, 135 in `inventory_api`, 15 in `inventory_parser`, 48 in
  `toast_analytics`) still resolves on HEAD, with the same default arguments. Only the
  bodies that now call the adapter modules (`fetch_*`, `toast_get`, `_wait_seconds`) differ
  in source. `analytics.py` and the menu parsing are byte-for-byte the same apart from the
  docstring and the logger name.
- **The b9501ad Toast tests, unchanged, run against HEAD's app code.** That is 40 files
  touching `toast_client`, `inventory_api`, `toast_analytics`, `inventory_parser`,
  `sales_sync` and `open_orders`, with the old-path patches. Result: 978 passed and 3
  skipped on 3.12 and on 3.9. The run left out one test, which needs `frontend/`, and that
  isn't in the export.
- **Patching edge cases.** `mock.patch.object(..., autospec=True)` on an old path;
  `mock.patch` of a name the old module doesn't hold (delattr then re-set on exit); a name
  the old module does hold (both copies restored); an old-path patch nested inside a
  new-path patch. Every case restored the adapter and the old module to the same objects.
- **Imports.** Each of the 21 old and new modules imports first in a fresh interpreter on 3.9
  and on 3.12 (no cycles). No 3.10+ syntax in `app/pos`.
- **Unchanged.** MT2 touches no migration, model, router, scheduler or `main.py` (`alembic
  heads` = 0061, the prep placeholders), so it adds no route and no demo-guard surface.
  `pos_provider` is the only new setting; nothing lists settings in an API answer.
- **Parallel branches.** A `git merge-tree` of HEAD with `m1-mt4`, `m1-mt5` and `m1-mt6`
  merges every code file cleanly. Only this contract and the skill file conflict, because all
  four branches append to them. MT6's `pos/gating.py` reads `settings.pos_provider` with the
  same normalising (`strip().lower()`, blank = toast).
- **Parity on a fresh read-only production copy** (15:22, migration 0059, 125,330 order rows).
  b9501ad code (`git archive`) and HEAD were each recorded on the same copy:
  - 3.12: **0 differences** in 114 files, also 0 with `--strict`.
  - 3.9: **0 differences** in 114 files, also 0 with `--strict`.
  - Both versions: 110/110 endpoints answered 200, with 0 network attempts and 0 background
    threads.
- **Full suite.** 2264 passed and 12 skipped on 3.9 and on 3.12. One test was deselected:
  `test_public_checklist_link.py::test_page_paths_trailing_slash_and_nested`, which needs a
  built `frontend/dist`.

**Dismissed / known, not fixed (no caller depends on them):**

- Patching a name on the new adapter path does not reach code left in `inventory_api` /
  `inventory_parser` that calls its own imported copy. The builder documented this, and it
  ends when MT3 moves that code.
- The non-forwarded globals of the old modules (`toast_client.get_settings` / `httpx` /
  `time` / `dt`, and the same in `inventory_api`) are no longer seen by the moved code if
  replaced wholesale on the old module. No test on any of the 5 branches does this; tests
  patch attributes on `get_settings()` or on the shared `time` / `httpx` modules, and those
  still reach the moved code.
- `from app.services.toast_analytics import *` and `vars(old_module)` would not list the
  forwarded names. Nothing uses either (`dir()`, `hasattr` and `getattr` do list them).

## As built - MT3 (normalized models + POS-neutral sync)

Branch `multistore-m1`, commits `741a052`, `8a7bd9b`, `c23159e`, `b634550`. The sync
jobs now read POS-neutral models from the store's connector and write our tables; the
Toast JSON stops at `app/pos/adapters/toast/`. Spice Town's rows, job_runs rows, answers,
pushes and scheduled jobs are unchanged (proved below). No migration, no route, no JSON
key, no setting, no scheduled job changed. Nothing was installed, deployed or restarted.

### 1. Normalized models - `backend/app/pos/models.py`

Frozen dataclasses (Python 3.9 typing): `Item`, `Category`, `Vendor`, `CatalogSnapshot`,
`StockLevel` (+ `StockStatus` COUNT / OUT / UNTRACKED), `CatalogRow`, `Employee`, `Check`,
`OrderLine` (`is_modifier`, `parent_external_id`), `Order`, `SalesDay`, `ReceivingRecord`,
`AnalyticsReport`, `ConnectorHealth`.

- Money and quantities are `Decimal`, built by `to_decimal(float(x))` from the POS's JSON
  number: `float(model value)` is exactly the float the code always used, so the rows the
  sync writes are bit-identical.
- Additions to plan 4.4, each needed to keep a Toast rule exact:
  - `Item.locations` (every menu / group / subgroup an item is listed under) and
    `Item.other_names` (the menu name with and without " - Each"), for "keep the CSV's
    placement / spelling while the POS still agrees";
  - `Item.price_rule` (Toast `pricingStrategy`, only stored in the menu cache);
  - `Order.created_at` / `paid_at` / `closed`, `Check.paid_at` / `deleted`;
  - `OrderLine.check_external_id` / `check_index`;
  - `SalesDay.has_item_totals` / `has_day_metrics` ("Toast answered this report for the
    date at all" changes the verdict).

### 2. Toast adapter returns the models

| Toast adapter | New in MT3 | Was |
|---|---|---|
| `orders.py` | `map_order(raw, business_date)` -> `Order`, `map_check`, `map_selection` (modifiers nested at any depth -> lines, depth first), `is_open` (no closedDate, not voided/deleted), `_parse_toast_timestamp`; `fetch_orders_with_retry` and `_fetch_with_retry` moved unchanged (two 429 variants, kept separate on purpose) | `sales_sync._extract_line_items` / `_flatten_selection`, `open_orders` field reads |
| `labor.py` | `map_employee`, `_employee_name` | `open_orders._employee_name` |
| `stock.py` | `stock_status`, `stock_level`, `stock_levels(rows, lenient)` (lenient = the live path, which always skipped non-objects) | `inventory_api` reading `status` / `quantity` |
| `catalog.py` | `item_from_menu_info`, `catalog_snapshot(menus)` | `build_api_rows` reading `MenuItemInfo` |
| `day_totals.py` (new) | `day_totals(start, end)` -> `{date: SalesDay}`: the same two Analytics calls in the same order; an Analytics error comes back as `DayTotalsUnavailable("Toast Analytics", err)`, whose text is the old "Toast Analytics: ..." | `sales_accuracy` calling `menu_day_totals` / `day_metrics` |
| `connector.py` | `fetch_orders(d, *, max_retries=4, sleep=None)`, `fetch_open_orders(dates, *, max_retries=None, pause_seconds=0)`, `fetch_employees`, `has_day_totals`, `get_day_totals`, `fetch_catalog_snapshot` / `fetch_catalog`, `fetch_stock(live=)`, `health` | MT2: only `catalog_changed_at` |

`app/pos/base.py` gained the matching interface methods (keyword-only extras, so the plan
4.3 plain calls still work) and `DayTotalsUnavailable`. `merged_barcode` / `_codes` moved
to the new POS-neutral `app/pos/barcodes.py` (the Toast catalog module and
`inventory_api` still expose both). The connector looks every Toast function up through
its module at call time, so a test patch on the old or the new path is seen.

Toast errors pass through unchanged (httpx errors, `ToastFetchError`): job_runs details
and pages show their text today. Mapping them to `PosUnavailable` (503) waits for the
router work.

### 3. POS-neutral sync - `backend/app/pos/sync/`

| Module | From | Writes / answers |
|---|---|---|
| `sales.py` | `services/sales_sync.py` | `orders`: `line_rows(order)` (one row per line, the same keys as always), `line_paid_at`, `_upsert_line_items` and `_prune_missing` (verbatim), `sync_sales_for_date`, `latest_job_run`, `fetch_orders` |
| `open_orders.py` | `services/open_orders.py` | `open_order_cache` and the open checks answers: `_normalize_open_order(Order, ...)`, `tab_names_of`, scans, cache, freshness (all of the old module) |
| `accuracy.py` | `services/sales_accuracy.py` | `job_runs` sales_repull / sales_accuracy: `_analytics_view(SalesDay)` gives the same dict, so stored fingerprints still match |
| `catalog.py` | `services/inventory_api.py` | `build_inventory_rows(db, items, stock, changed_at, ...)`, `compare_rows`, `store_api_snapshot`, `implausible_build`, `run_snapshot`, `refresh_catalog_cache`, `save_catalog_cache` (`toast_menu_items`), `effective_quantity`, the live overlay (`get_live_stock`, `overlay_live_stock`, `LiveStockSnapshot`, `live_stock_meta` ...) |

Kept exactly:
- **job_runs names**: `toast_sales_sync`, `open_orders_scan`, `sales_repull`,
  `sales_accuracy`, `inventory_api_snapshot` and `menu_barcode_recheck`, with the same
  detail text, so the status widget, the accuracy job and the catch-up scan read them as
  before.
- **Toast price semantics**: a top-level line's net already holds its modifiers' prices;
  modifier lines are their own rows with a parent id. Reports keep counting top-level
  rows only.
- **Pruning** happens only when the POS answered with orders, and nothing is pruned on an
  empty answer.
- **CSV wins over the API set** for the same date (`store_api_snapshot` checks twice).
- **Logger names** are the old ones (`app.services.sales_sync`, `...open_orders`,
  `...sales_accuracy`, `...inventory_api`).

The `inventory_snapshots` inputs from our own DB (latest CSV rows, `sales_by_item`,
`has_csv_for`, `_after_new_snapshot` ...) stay in `services/inventory_api.py`.
`pos/sync/catalog` looks them up there at call time, so existing patches still reach it
and MT4's one-line hunk in `sales_by_item` merges cleanly.

### 4. Old module names (thin shims)

- **`services/sales_sync.py`** forwards to `pos/sync/sales` and the Toast `orders` module.
  It keeps `_extract_line_items(raw)` / `_flatten_selection(...)` (Toast JSON in, the same
  row dicts out).
- **`services/open_orders.py`** forwards to `pos/sync/open_orders` and the Toast
  `orders` / `labor` modules. It keeps `_normalize_open_order`, `_tab_names`,
  `_order_total`, `_line_items`, `_paid_at`, `_promised_at` and `_placed_at` with their
  old Toast-JSON signatures.
- **`services/sales_accuracy.py`** forwards everything to `pos/sync/accuracy`.
  `sales_accuracy.toast_analytics` is still the Analytics client, so
  `monkeypatch.setattr(sales_accuracy.toast_analytics, "menu_day_totals", ...)` still
  feeds the job.
- **`services/inventory_api.py`** forwards the moved names to `pos/sync/catalog`. It
  keeps these functions with their Toast-JSON signatures, each mapping through the
  adapter:
  - `build_api_rows(db, stock_json, menus_json, ...)`;
  - `run_api_snapshot(fetch_stock_fn, fetch_menus_fn)`;
  - `refresh_menu_cache`;
  - `compare_now`;
  - `get_live_stock(fetch_fn)` and `overlay_live_stock(latest, fetch_fn)`;
  - `live_quantity(snap, "QUANTITY", qty)` and `format_status`;
  - `_placement` and `_save_menu_cache`.
- **`services/items_sold.py`**: the live "today" pull now goes `pos.sync.sales.fetch_orders`
  -> connector -> `line_rows`. Its old names `fetch_orders_with_retry` /
  `_extract_line_items` still resolve, but its live pull no longer calls them. Patch
  `sales_sync.fetch_orders_for_business_date` or the connector instead; no test patched
  the old ones.

Every name each of these modules had at aa7688e still resolves:

| Module | Names checked |
|---|---|
| `sales_sync` | 23 |
| `open_orders` | 50 |
| `sales_accuracy` | 49 |
| `items_sold` | 33 |
| `inventory_api` | 135 (the MT2 list) |

Old-path patches reach the moved code and undo restores it. The remaining "new-path patch
doesn't reach a bare-name copy" gap is down to three names in `inventory_api`:
`merged_barcode` in `barcode_overlay`, `live_stock_meta` in `status`, and
`ToastFetchError` in `compare_now`.

### 5. Tests

New tests (70; all pass on 3.9 and 3.12):
- `tests/test_pos_sync_neutral.py` (19): a fake POS registered in `registry.PROVIDERS`
  feeds synthetic `Order` / `Item` / `StockLevel` / `SalesDay` models (no Toast JSON)
  through every job. It covers:
  - rows, voids at every level, paid-time fallbacks, unit price at quantity 0;
  - idempotent re-run, pruning, no prune on an empty answer, a failure row;
  - the open checks live view, cache, scan retries and pauses, employees;
  - an accuracy re-sync and the report label;
  - the catalog build (CSV placement and spelling kept, out-of-stock keeps the CSV
    negative count, stats) and the job writing (and not when a CSV exists or the stock
    list is empty);
  - the hourly re-check pulling only on change, the live overlay, and Items Sold today.
- `tests/test_pos_toast_mapping.py` (20): the same Toast payloads go through the pre-MT3
  functions (`tests/pre_mt3_reference.py`, verbatim copies from aa7688e) and through the
  new adapter + neutral path. They must be identical by `repr`, so 3 vs 3.0, True vs 1 and
  naive vs aware all count as different. Covered:
  - 3,000 random Toast orders (modifiers to depth 3; null, missing and odd fields; bad
    timestamps), 1,600 open-check candidates, and tab names (odd payloads too);
  - employees, stock statuses, `format_status` and `live_quantity` on a grid;
  - 203 inventory builds (fixture catalog plus 200 random stock lists).

  A mutation check (paid-time rule, tab-name de-duplication, days-on-hand rounding) made
  them fail as they should.
- `tests/test_pos_sync_shims.py` (31): the old-name lists above; moved names are the new
  objects; old-path patches feed the jobs (`sales_sync.fetch_orders_for_business_date`,
  `open_orders._fetch_with_retry` / `fetch_employees`, the Analytics client,
  `inventory_api.has_csv_for` ...). Also: unchanged Analytics error text, `mock.patch`
  undo, the job_runs names, and no import cycle (14 modules each imported first in a
  fresh interpreter).
- Changed: `tests/test_pos_toast_adapter.py` (two MT2 tests that pinned "MT3 not built
  yet"); `tests/conftest.py` now refuses every outbound connection and DNS lookup for the
  whole run (loopback and Unix sockets allowed).

Full suite (one test deselected, as before: `test_public_checklist_link.py::
test_page_paths_trailing_slash_and_nested` needs a built `frontend/dist`):

| Python | Passed | Skipped | Network attempts |
|---|---|---|---|
| 3.9 | 2334 | 12 | 0 |
| 3.12 | 2334 | 12 | 0 |

**The unchanged pre-MT3 test suite (aa7688e's `tests/`) against the MT3 code (3.9):**
- 2259 passed, 15 skipped.
- 2 failed: exactly the two MT2 tests updated above.
- One of them now tries Toast unpatched; the guard blocked it.

### 6. Proof on a production copy

The copy is the same `m1/prodcopy.db` as MT1/MT2: migration 0059, 125,330 order rows. MT3
has no migration.

**Parity harness** (`parity_record.py`) against the MT1 baselines:

| Python | Differences (114 files) | With `--strict` | Endpoints | Network attempts | Threads |
|---|---|---|---|---|---|
| 3.12 | 0 | 0 | 110/110 answered 200 | 0 | 0 |
| 3.9 | 0 | 0 | 110/110 answered 200 | 0 | 0 |

**Old sync vs new sync, row by row** (new `scripts/parity/sync_compare.py`; see the
parity README). The old code is aa7688e's `backend/app` (`git archive`); the new code is
this branch. Each runs on its own scratch copy of the prod copy, with the network blocked,
the clock frozen (2026-10-15 12:00 EDT, epoch time too) and the same recorded Toast
answers (fixtures sha256 `5698c22f073151f5…`).

The app stores no raw order JSON, so the ordersBulk answers are rebuilt from the copy's
own rows:
- 142 business dates and 24,561 orders, with checks, selections and modifiers nested by
  parent id;
- varied on purpose: voids on the order, check or line; deleted orders and checks;
  payment time on the check or only on the order; `+00:00` and local-offset timestamps;
  "bad-time" values; int and float numbers;
- 83 open orders, with tab names, promised times and servers.

The menus and stock answers are the real Toast recordings from Oct 11
(`b2inv/live_menus.json`, `live_stock.json`). The scenario:
1. sync all 142 dates;
2. two dates answered only after 429s;
3. a second pass over 40 dates with Toast-side edits: lines removed, orders voided,
   checks deleted, a price changed, an order moved away, 2 empty answers (no prune) and
   one failure;
4. the open checks full scan, today refresh, cache and live view;
5. Items Sold with today live;
6. the accuracy re-pull twice, and its report;
7. the inventory API snapshot, written for a date without a CSV, compared for the CSV
   date, and refused on an empty stock list;
8. the menu re-check, unchanged and then forced; Compare now; the live overlay over
   5,221 items; the admin status.

| Table | Rows compared, every column | Differing |
|---|---|---|
| orders | 125,311 | 0 |
| open_order_cache | 79 | 0 |
| job_runs | 6,337 | 0 |
| toast_menu_items | 5,222 | 0 |
| inventory_snapshots (touched dates) | 16,005 | 0 |

Both runs gave the same 33 function answers and made the same 371 Toast calls, with no
network attempts and no background threads. **DIFFERENCES: 0 on 3.12 and 0 on 3.9.**

Sensitivity check: the new code with two one-line changes (the voided-check paid-time
fallback; days on hand rounded to 2 places) gave 15 differences (217 order rows and 1,015
snapshot rows).

### 7. Behaviour that can differ, only on payloads Toast does not send

Each of these needs malformed Toast JSON. Toast's schema doesn't allow it, and the
comparisons above never hit it.
- **Items Sold "today", order a mapping can't read** (e.g. a non-numeric price string):
  the mapping now runs inside the live pull. The pull counts as failed: 503 and 15 s
  fast-fail, instead of a 500 on every request.
- **Live stock, non-numeric `QUANTITY`**: the live fetch counts as failed and the
  snapshot counts are used. Before, building the page raised.
- **Nightly snapshot, non-object stock entry**: the same error text on the job row, but
  the menus GET is no longer made first.
- **Accuracy, non-numeric value in the Analytics day totals** (the client always gives
  floats): the run fails before any date is recorded. Before, it failed after the dates
  ahead of it.
- **Open order JSON with no `guid` key**: now "" instead of None. Before, the NOT NULL
  column refused the row.
- **Logs: a malformed `promisedDate`** is now also logged by the nightly sync, which maps
  every order. A bad `closedDate` is parsed silently.

### 8. Safety note

While developing, one MT2 test that expected `fetch_orders` to be missing ran unpatched
once. It sent a single POST to Toast's login (`ws-api.toasttab.com/authentication/...`)
with the test run's empty client id and secret, and Toast answered 401. No credentials
were sent and nothing was read. From then on every test run used a network guard, and
`tests/conftest.py` now blocks outbound connections for every run.

### 9. Integration notes (merging `m1-mt4`, `m1-mt5`, `m1-mt6`)

A trial `git merge-tree` of this HEAD:
- **`m1-mt5` and `m1-mt6`**: every code file merges; only this contract and `SKILL.md`
  conflict (append-only).
- **`m1-mt4`** conflicts in `services/sales_sync.py` and `services/sales_accuracy.py`.
  MT4 edited the old bodies, which now live in `pos/sync/`. The recipe, checked on a
  trial merged tree where the full suite passed (2345, 3.9, 0 network attempts):
  1. Take MT3's versions of both shims (`git checkout --ours`).
  2. In `app/pos/sync/sales.py`:
     - add `from app.pos.neutral_ids import after_sales_sync, neutral_line_values`;
     - in `_upsert_line_items`, apply MT4's hunk verbatim:
       `values = {**item, **neutral_line_values(item)}`, then use `values` in the insert
       and in `update_cols`. The row dicts have the same keys as before, so it fits.
     - after the `finally` in `sync_sales_for_date`, add
       `if job.status == "success": after_sales_sync(db, business_date); db.refresh(job)`.
  3. In `app/pos/sync/accuracy.py` `stored_day_totals`, replace
     `Order.parent_selection_guid.is_(None)` with `Order.top_level()`.
  4. In MT4's `tests/test_pos_neutral_ids.py::test_the_thirteen_report_queries_use_the_helper`,
     count `app/pos/sync/*.py` as well as `app/services/*.py`: the accuracy query moved.
     There are still 13 uses.
  - `items_sold.py` and `inventory_api.py` merge cleanly; MT4's hunks are in untouched
    lines.
  - The exact hunks for steps 2-3 are saved as
    `~/.claude/jobs/e26a1611/tmp/multitenant/m1/mt3/mt4_into_pos_sync.patch` (outside
    git).
  - After merging, re-run `sync_compare.py`, old = aa7688e and new = merged. It needs the
    copy migrated to 0060 first: run `alembic upgrade head` on the scratch copy, as
    parity `--migrate` does.
- MT4 asked MT3 to use its `sync_cursors` helpers. `after_sales_sync` moves the cursor,
  so step 2 covers it.

### 10. Deferred

- **Not built:**
  - `run_report` (Insights still calls the Analytics client directly; `ANALYTICS_REPORTS`
    gating is MT6's);
  - `fetch_orders_modified` (`ORDERS_INCREMENTAL` is not a Toast capability yet);
  - `parse_catalog_export` (the CSV upload still goes through `inventory_parser`, which
    MT5 changed).
- **Toast wording kept in shown text, so nothing changes:**
  - job_runs detail "… no longer in Toast removed";
  - "Toast's stock list came back empty";
  - the log line "Toast Analytics is not configured".

  M2 makes UI text name the store's POS.
- **Still Toast-only in the neutral code:** `open_orders._today_is_stale` still checks
  `settings.toast_client_id` (exact old rule); M2 can ask the connector instead.
- **Table and column names:** `toast_menu_items` and the `toast_*` columns keep their
  names (MT4 added the neutral columns next to them).
- **Errors:** POS errors are not mapped to `PosUnavailable` / `NotSupported` (503/409)
  yet, because their text is shown today.

### Verification of MT3 (adversarial pass, Oct 15)

**Confirmed and fixed (1), plus a test-guard gap:**

- **The nightly sales sync and Items Sold "today" could fail on order fields they never
  read before.** Since MT3 both map the whole Toast order (`map_order`), including
  `promisedDate` / `estimatedFulfillmentDate` and `server`. Before MT3, only Open Checks read
  those. A value of the wrong type in one of them, such as a number or object for
  `promisedDate`, or a string or list for `server`, raised `TypeError` or `AttributeError`.
  That failed the whole business date (job_runs "failed", no rows written), and Items Sold
  "today" answered 503. The pre-MT3 sync wrote that date's rows as usual. Toast's schema
  doesn't allow these values, and the production logs have no parse warning, so it was never
  seen. The fix in `app/pos/adapters/toast/orders.py::map_order`: such a value becomes None,
  and a promise that isn't a string is logged like any bad timestamp. Well-formed values are
  unchanged. For Open Checks this one odd case now shows the order without a promise or
  server, where before it failed the scan. New test file `tests/test_pos_toast_mapping_tolerance.py`
  (8 tests):
  - 6 odd payloads give exactly the pre-MT3 rows (`repr`-equal to `pre_mt3_reference`);
  - well-formed values are unchanged;
  - the nightly job writes the date despite the odd fields.
  Without the fix, 7 of the 8 fail.
- **The test network guard started only when the first test ran.** `tests/conftest.py` now
  also installs it when the file is imported, so module-level code is covered while tests are
  collected (checked with a probe module). The builder's "0 network attempts" count came
  from a plugin that sits behind the conftest guard, so it could only ever read 0. This pass
  counted attempts in front of the guard instead (a counter checked to catch a deliberate DNS
  lookup): **0 real attempts** in both full runs below.

**Checked, nothing found:**
- **Every name, old against new.** All names in `sales_sync`, `open_orders`,
  `sales_accuracy`, `inventory_api`, `items_sold`, `toast_client` and `toast_analytics` at
  aa7688e still resolve on HEAD, with the same kind and signature. The one exception is
  the private `sales_accuracy._analytics_view`, which now takes a `SalesDay`; only the
  accuracy job calls it.
- **Moved bodies.** These were diffed against aa7688e and are verbatim apart from wording:
  - `compare_rows`, `store_api_snapshot` and `effective_quantity`;
  - `run_snapshot` and `refresh_catalog_cache`, which make the same calls in the same order;
  - the live overlay, open checks and accuracy code.

  Reviewed by hand:
  - `float(to_decimal(x)) == float(x)`, including `-0.0`, large values and `bool`;
  - `sku` "" -> None as before;
  - tab names, totals and employees.
- **Behaviour.** The scheduler, routers, migrations and `main.py` are untouched:
  - no new route, so there's no demo-guard surface;
  - the scheduler uses a memory jobstore, so no stored function references.
- **Python 3.9.** No 3.10+ syntax in the changed files.
- **Migrations on a fresh read-only prod copy (17:02).** `alembic upgrade head` (0061),
  `downgrade 0059`, `upgrade head`:
  - `integrity_check` ok and `foreign_key_check` empty;
  - row counts of every table identical before, after the downgrade and after the
    re-upgrade.

**Numbers (fresh read-only prod copy, 17:02, migration 0059, 125,330 order rows):**

| Check | Result |
|---|---|
| Parity: aa7688e vs MT3 as built, 3.12 | 0 differences / 114 files (also `--strict`), 110/110 answered 200, 0 network, 0 threads |
| Parity: aa7688e vs MT3 + this fix, 3.12 | 0 differences / 114 files with `--strict` |
| `sync_compare.py`: aa7688e vs MT3 as built, and vs MT3 + fix | 0 differences both times. orders 125,311, open_order_cache 79, job_runs 6,363, toast_menu_items 5,222, inventory_snapshots 16,005 rows identical; 33 answers, 371 Toast calls each |
| Full suite before the fix | 3.9: 2334 passed, 12 skipped; 3.12: 2334 passed, 12 skipped |
| Full suite after the fix | 3.9: 2342 passed, 12 skipped; 3.12: 2342 passed, 12 skipped; 0 network attempts |

The same frontend test was deselected in both runs, as before.

**Dismissed:** `items_sold` still holds its old `fetch_orders_with_retry` /
`_extract_line_items` names, but its live pull no longer calls them. Patching them there has
no effect; nothing does that (documented above). The other §7 payload-only differences stand
as the builder listed them.
## As built - MT4 (POS-neutral ids, additive migration 0060)

Branch `m1-mt4` (parallel track, off `multistore-m1` at the prep commit).
Plan sections 4.3 (item ids), 4.4, 4.8 (cursors) and 8.1 ("expand, backfill,
dual-write"). Nothing installed, deployed or restarted.

### 1. Migration `0060_pos_neutral_ids` (additive only)

| Where | Added | Filled with |
|---|---|---|
| `orders` | `source` VARCHAR NOT NULL DEFAULT 'toast' | 'toast' |
| | `external_order_id`, `external_check_id`, `external_line_id` | `toast_order_guid`, `toast_check_guid`, `toast_selection_guid` |
| | `parent_external_line_id` | `parent_selection_guid` |
| | `is_modifier` BOOLEAN NOT NULL DEFAULT 0 | `parent_selection_guid IS NOT NULL` |
| | `channel` | NULL (Toast only sends a dining-option GUID; see deferred) |
| | `dining_option_id` | `dining_option` (the GUID) |
| | unique index `uq_orders_source_external_line_id (source, external_line_id)` | the future upsert key (plan 4.8); unique because the selection GUID already is |
| `open_order_cache` | `external_order_id` | `toast_order_guid` |
| new `sync_cursors` | PK (`source`, `stream`), `cursor`, `updated_at` | `('toast', 'orders_business_date', <newest business date with a successful toast_sales_sync>)` |
| new `pos_item_map` | PK (`source`, `external_id`), `item_id` (indexed), `created_at` | every item id we hold - sold items, every inventory snapshot item, the Toast menu cache - mapped to itself |

- Old columns, indexes and the `uq_orders_selection_guid` upsert key are
  untouched. The plan's "old columns as SQLAlchemy synonyms" became real,
  dual-written columns instead: a synonym cannot be read by raw SQL or the
  `sqlite3` tool, and it cannot carry the new unique index.
- Backfill: plain SQL (an ORM update would stamp `orders.updated_at`), in id
  batches of 20,000, only rows whose `external_line_id` is still NULL - so it
  is idempotent and a re-run of a half-done upgrade is safe (columns and
  tables are added only if missing).
- Downgrade drops only what 0060 added, with SQLite's native `DROP COLUMN`
  (3.35+; prod runtime has 3.53.4, the 3.9 venv 3.51.0), so `orders` is not
  rebuilt and the original `CREATE TABLE` text comes back byte-identical.
- 0061 (MT5's placeholder) is untouched; the chain stays 0059 -> 0060 -> 0061.

### 2. Code

- `Order.top_level()` (`app/models.py`): returns exactly
  `orders.parent_selection_guid IS NULL` - same SQL, same index. It replaces
  the **13** SQL filters written by hand (`demand_forecast.py` x2,
  `inventory_api.py`, `forecast.py`, `weekly_sales.py`, `similar_products.py`,
  `fix_issues.py`, `items_sold.py`, `export.py`, `sales_accuracy.py`,
  `insights.py`, `weekly_digest.py`, `delivery_suggestions.py`). A test fails
  if anyone spells the old filter by hand again.
- The other 2 of the plan's "15" are not SQL: `items_sold.py:164`
  (`li.get("parent_selection_guid")`, live Toast pull) and `open_orders.py:94`
  (`row["parent_selection_guid"] is not None`). They test raw Toast line
  dicts, and their truthiness differs slightly ("" vs None), so they were
  left as they are; MT3's normalized `OrderLine.is_modifier` replaces them.
  Reading `is_modifier` instead of `parent_selection_guid` in `top_level()`
  is a one-line switch for later (on the prod copy both give 115,999
  top-level rows), deliberately not made in M1.
- New `app/pos/neutral_ids.py`: `neutral_line_values(line)`,
  `heal_orders`, `record_items`, `record_catalog_items`,
  `get_cursor` / `set_cursor` / `advance_sales_cursor`, `after_sales_sync`.
  MT3's `pos/sync/sales` should use these cursor helpers.
- Writers, going forward:
  - `sales_sync._upsert_line_items` writes the neutral columns in the same
    upsert (also on conflict, so a line Toast moved to another check gets
    its new `external_check_id`). Line dicts, job names and job details are
    unchanged.
  - `sync_sales_for_date`, after a **successful** run only, calls
    `after_sales_sync`: fills neutral ids on any row still missing them (rows
    the old process writes between `alembic upgrade` and the restart, and the
    same for `open_order_cache`), adds the day's sold item ids plus the
    newest snapshot's and the menu cache's ids to `pos_item_map`, and moves
    the sales cursor forward (never back on a backfill). It never raises; a
    failure is logged and the sync's result is unchanged (tested).
  - Model column defaults copy the Toast columns on any ORM/Core insert that
    does not set them (`OpenOrderCache` rows from the open-checks scan, test
    fixtures), so `open_orders.py` needed no change.
- Shared files touched (small hunks): `models.py` (Order columns +
  `top_level()`, two new classes placed right after `Order`,
  `OpenOrderCache.external_order_id`), `sales_sync.py` (+8 lines), the 13
  one-line filter swaps. No route, JSON key, setting, scheduled job or
  frontend/app change.

### 3. Prod-copy checks (fresh read-only copy of production, Oct 15 15:00, 434 MB, 0059)

`m1/mt4/updownup.py`, upgrade head -> downgrade 0059 -> upgrade head:

| | Python 3.12.14 / SQLite 3.53.4 | Python 3.9.6 / SQLite 3.51.0 |
|---|---|---|
| upgrade (whole alembic run incl. backfill of 125,330 rows) | 1.10 s | 1.10 s |
| downgrade | 2.17 s | 2.06 s |
| upgrade again | 0.92 s | 1.10 s |

- `PRAGMA integrity_check` ok and `foreign_key_check` empty after every step;
  row counts of all 59 existing tables unchanged after every step; a SHA-256
  over every old `orders` column (all rows) and over `open_order_cache`
  identical at every step; schema after the downgrade identical to the
  original; schema and backfill identical after both upgrades.
- Backfill: 0 mismatches between each neutral column and its Toast column,
  `is_modifier` = 9,331 rows (= rows with a parent), 115,999 top-level rows
  either way, `pos_item_map` 6,560 ids (0 sold or inventory items unmapped),
  cursor `2026-10-14`.
- The file grew 434 -> 466 MB (+32 MB: four more GUID copies per order line
  and the new unique index).
- Dual-write replay: all 1,001 lines of business date 2026-10-14 had their
  neutral ids blanked and were re-upserted through the new
  `_upsert_line_items` + `after_sales_sync` (0.72 s): the whole `orders` table
  came out byte-identical to the backfilled one, 0 mismatches.

### 4. Parity

On `m1/prodcopy.db` (the MT1 baseline copy) with `--migrate` (scratch copy
upgraded to head = 0061):

- Python 3.12 vs `m1/baseline`: **0 differences** across 114 files, 110/110
  endpoints 200, 0 network attempts, 0 background threads. 22 allowed
  additions, all in `fingerprint*.json` (the new columns and the two new
  tables); no JSON key, alert or scheduled job added.
- Python 3.9 vs `m1/baseline-py39`: **0 differences**, the same 22 additions.

### 5. Tests

- New `tests/test_pos_neutral_ids.py` (13, pass on 3.9 and 3.12): `top_level()` SQL, no hand-written
  filter left, 13 helper uses; ORM inserts copy ids (several rows in one
  flush); sales sync dual-write incl. a moved line, cursor only forward,
  nothing recorded on a failed sync; healing rows written by old code
  without touching `updated_at`; catalog ids mapped; bookkeeping failure
  leaves the sync's status/detail unchanged; migration backfill on an
  existing 0059 database (old columns unchanged, index used by the heal
  query, migrated columns = model columns, up/down/up with identical schema)
  and a re-run on a half-done database.
- Full suite: Python 3.9 **2237 passed, 12 skipped, 1 failed**; Python 3.12
  **2237 passed, 12 skipped, 1 failed**. The one failure on both is the known
  `test_public_checklist_link.py::test_page_paths_trailing_slash_and_nested`
  (needs a built `frontend/dist`, which the worktree does not have).

### Deferred

- `orders.channel` stays NULL for Toast: turning dining-option GUIDs into a
  normalized channel needs the Toast config API (`/config/v2/diningOptions`)
  plus a name -> channel map (today's `insights.channel_of` names). MT3/M2.
- Reads still use the Toast columns; the upsert conflict key is still
  `toast_selection_guid`. Switching reads/keys to the neutral columns, and
  dropping the old ones, is the "contract" step after the minimum app build
  (plan 8.1).
- `pos_item_map` catalog ids are swept after each successful sales sync, not
  at inventory-upload time (to keep MT2/MT5's inventory files untouched); an
  upload's new items are mapped within a day. Nothing reads the map in M1.

### MT4 verification (adversarial pass)

- **Fixed:** the new copy-the-Toast-id column defaults broke a multi-row
  `insert(Order).values([...])` / `sqlite_insert(Order).values([...])` (and
  the same on `open_order_cache`) with `KeyError: 'orders.toast_order_guid_m0'`.
  SQLAlchemy 2.0 cannot isolate one row of such a statement for a
  context-aware default. No current code path inserts that way, but it worked
  before 0060 and a moved sales sync might. The defaults now read the row's
  own value; `test_multi_row_inserts_still_work_and_copy_per_row` covers
  Core, ORM-bulk, ORM-flush and multi-values inserts (it fails without the fix).
- **Fresh production copy** (read-only backup, Oct 15 15:24, 434 MB, 0059,
  125,330 order lines), base code (`b9501ad`) and MT4 code each recorded on
  it with `--migrate`:
  - Python 3.12: **0 differences** across 114 files. 22 additions, all of them
    the new columns and tables in the fingerprints.
  - Python 3.9: **0 differences**, with the same 22 additions.
- **Migration up/down/up on that copy** (separate script):
  - **Integrity:** `integrity_check` ok and `foreign_key_check` empty at all 4 steps.
  - **Old data:** the row counts of every old table, the old `orders`
    columns and `open_order_cache` hashed identical at every step. The
    schema after the downgrade equals the original.
  - **Backfill:** 0 mismatches, 9,331 modifiers, 115,999 top-level rows
    either way, 6,560 ids mapped with 0 unmapped, and the cursor is 2026-10-14.
  - **Query plan:** the top-level query still uses `ix_orders_parent_selection_guid`.
  - **Timings:** 3.12 took 1.28 / 2.61 / 1.62 s and 3.9 took 1.14 / 2.15 / 1.06 s.
- **Full sales sync replay** (not just the upsert): business dates
  2026-09-01, 2026-10-13 and 2026-10-14 ran through `sync_sales_for_date`.
  Toast's answer was rebuilt from the stored rows, with the network blocked.
  Base and MT4 copies were compared:
  - **Job results:** the same statuses and details (715/626/1,001 lines).
  - **Old columns:** the hash over every old `orders` column is identical
    and no `updated_at` was touched.
  - **New columns:** 0 neutral-id mismatches.
  - **Cursor:** stays at 2026-10-14 (the older day did not move it back).
- **Checked and not a problem:**
  - **Drift:** Alembic autogenerate shows the same drift set as base (MT4 adds none).
  - **Old filter:** no other hand-written filter is left (raw SQL included).
  - **Serialized shape:** no endpoint serializes `Order` / `OpenOrderCache`
    whole, so no JSON shape changed. The CSV export uses a fixed column list.
  - **Scope:** no new route, so the demo guard is unaffected. No scheduler change.
  - **Cache:** the extra `UPDATE orders` after a sync only clears the report
    cache a second time, which changes no output.
- **Note for MT3:** `sync_cursors` holds the newest business date synced
  successfully *at least once*. A manual sync of today (`/api/jobs` with
  `business_date`) moves it to today even though the day isn't finished, so
  don't treat it as "next date to fetch" without re-syncing the cursor day.
- **Suites after the fix:**
  - **Python 3.9:** 2238 passed, 12 skipped, 1 failed.
  - **Python 3.12:** 2238 passed, 12 skipped, 1 failed.
  - **The failure:** on both it's `test_public_checklist_link.py::test_page_paths_trailing_slash_and_nested`,
    which also fails on base code here. It passes once `frontend/dist`
    exists, so the worktree simply has no build.
## As built - MT5 (vendors become app data)

Branch `m1-mt5` (from `multistore-m1` at the prep commit `b9501ad`). Plan
section 4.9, row MT5. Nothing was pushed, deployed, installed or restarted.

**In one line:** the vendor list of every item now also lives in a new
`item_vendors` table, filled from the Toast CSV, and the 8 hand-written ";"
parsers now go through one helper. Every Spice Town page still reads the
item's own CSV field, so nothing a user sees changed (parity: 0 differences).

### 1. New table `item_vendors` (migration `0061_item_vendors.py`)

| Column | Meaning |
|---|---|
| `id` | primary key |
| `item_id` | the item (Toast GUID for Spice Town = `inventory_snapshots.item_id`), indexed |
| `vendor_name` | as the source spells it (trimmed), indexed |
| `vendor_item_code` | the vendor's own code for the item (the CSV's `supplier item id` slot for that vendor), NULL when unknown |
| `rank` | 1 = listed first; follows the order the source lists the vendors |
| `source` | `toast_csv` (from the CSV upload) or `manual` (typed in the app; no editor in M1) |
| `source_date` | the CSV snapshot date the row came from (NULL for manual) |
| `created_at`, `updated_at` | `UTCDateTime` |

Unique `(item_id, vendor_name)` (`uq_item_vendors_item_vendor`). Model:
`app/item_vendor_models.py` (kept out of `models.py`, like
`setting_models.py`). The empty `vendors_reference` scaffold (Toast
Purchasing, still blocked) was left alone rather than altered: additive only.

The migration creates the table and backfills it from each item's **newest
CSV (non-API) snapshot row** - the same row the Toast API snapshot carries
`supplier` / `supplier_item_id` forward from (`inventory_api.latest_csv_row_per_item`).
For the Oct 15 copy that equals the latest snapshot for all 5,562 current items
(checked: 0 items differ). The migration holds its own frozen copy of the
ranking rule; a test fuzzes it against the app's. Downgrade drops the table only.

### 2. One vendor rule - `app/services/item_vendors.py`

- `split_field(raw, keep_empty=False)`: any ";" Toast field. Parts trimmed;
  empty parts dropped (or kept as slots with `keep_empty`); **order, case,
  repeats and junk values kept exactly** - this is what all the old parsers did.
- `vendors_of(item, keep_empty=False)`: an item's vendors in listed order.
  `item` = a snapshot row, a dict with `"supplier"`, or the raw string. For
  Toast it reads the item's own CSV field (**the CSV wins**).
- `ranked_vendors(supplier, supplier_item_id)`: the table's rows for one CSV
  row - one per vendor, rank 1..n in listed order; empty slots skipped
  without using a rank; a vendor listed again (`"Chetak;Chetak"`, `"Real
  Foods;Radhey Foods;Real Foods"`, matched ignoring case) keeps its first
  position and first spelling and takes the first code found across its
  slots; codes follow the slots (`";2524;856977005124"`), and when the two
  fields don't line up a single code is kept only for a single vendor (the
  existing `similar_products.vendor_refs` rule, which the PO pages show).
  Junk values (`ACT_SUP_UPDT`, `2234`) are **kept** - as the CSV has them;
  they are filtered where shown (`is_junk_vendor`), as before.
- `refresh_from_csv(db, item_ids=None)`: makes the table match each item's
  newest CSV row; rewrites only items whose list changed, otherwise only
  moves `source_date`. A non-empty CSV list replaces the item's whole list
  (manual rows too - the CSV wins); an empty CSV list removes only the
  CSV's own rows (manual ones stay); items with no CSV row are left alone;
  an older day's CSV uploaded late changes nothing (the newest row wins).
- `refresh_after_csv_upload(db, item_ids)`: the upload hook - in a
  savepoint, never raises (logs "item_vendors: refresh after CSV upload failed").
- `render_vendor_fields(vendors)`: the other direction (table -> the
  `supplier` / `supplier_item_id` pair) for M3 (see Deferred).
- `load_vendor_table`, `table_vendors_of`, `latest_csv_rows`,
  `compare_with_snapshots` (mismatch counts).

### 3. The 8 parsers, re-pointed (old names kept)

| Old parser | Now |
|---|---|
| `reorder._split_suppliers` | `vendors_of(raw)` |
| `supplier_projection._split_suppliers` (also imported by inventory router, delivery review/suggestions, PO cart, PO sections, store vs container) | `vendors_of(raw)` |
| `po_section_rules._split` | `vendors_of(raw)` |
| `issue_leads._split` | `vendors_of(raw)` |
| `purchase_order_log._resolve_item_id` inline split | `vendors_of(snap, keep_empty=True) or [""]` (keeps the old `(None or "").split(";") == [""]` quirk) |
| `similar_products.split_multi` / `split_aligned` / `aligned_codes` | `split_field` / `split_field(keep_empty=True)` / `aligned_item_codes` |
| `category_overrides.Rules.rule_for` inline split | `vendors_of(supplier)` (empty parts never matched a rule anyway) |
| `product_match.split_codes` (vendor item codes and barcodes, not names) | `split_field(value)` |

Barcode-only splitters (`inventory_api`, `barcode_report`, inventory router)
were not touched: not vendors, and `inventory_api.py` is a shared file.

### 4. CSV import fills the table

`inventory_parser.parse_and_store` (7 added lines) collects the item ids it
loaded and calls `refresh_after_csv_upload` before its commit, so the
snapshot and the table are written together. The upload's answer, job
detail, the API-snapshot replacement ("CSV wins over an API snapshot of the
same date") and everything else are unchanged. The Toast API snapshot path
was not changed: it has no vendor data and keeps carrying `supplier` from the
newest CSV row, which is exactly what the table holds.

### 5. What did not change

No endpoint, JSON key, page, app screen, scheduled job, push, WhatsApp or
email changed. Nothing reads `item_vendors` for a Spice Town page in M1. No
new setting; the capability list was not touched (`ITEM_VENDORS` stays off
for Toast - Toast's API has no vendor field). No editor UI (per the plan,
that is for the Clover store).

### 6. Parity and proof (Oct 15 production copy `m1/prodcopy.db`, 434 MB, migration 0059 -> 0061)

- **Parity harness, `--migrate`:** Python 3.12 vs `m1/baseline/`:
  **DIFFERENCES: 0** over 114 files (110/110 endpoints 200, no network
  attempts, no background threads); additions: `tables.item_vendors` in
  `fingerprint.json` / `fingerprint_after.json` only. Python 3.9 vs
  `m1/baseline-py39/`: **DIFFERENCES: 0**, same two additions. The recording
  covers Reorder (30/7, 90/14, all and critical), Supplier Projection for the
  top 10 vendors, PO sections (cart preview), Vendor Calendar 2 and 20 days,
  schedule suggestions, Fix Issues lists and leads, and the critical-vendor /
  order-due pushes at 09:00 and 21:30.
- **Parsers, old vs new on every value in the copy** (frozen copies of the
  08b43c0 code in `tests/test_item_vendors.py`): 520 distinct `supplier`
  strings through all 8 parsers, 3,379 distinct (`supplier`,
  `supplier_item_id`) pairs through the code alignment, 6,918 barcode and
  2,869 code strings through the code splitters: **0 differences**.
- **Table after the migration:** 5,541 rows for 5,145 items (of 6,177 items
  with a CSV row; the rest have no vendor); 236 items with 2-8 vendors
  (rank 2: 236, 3: 90, 4: 43, 5: 14, 6: 8, 7: 4, 8: 1); 189 items list a
  vendor more than once in Toast (collapsed to one row each); 2,884 rows
  have a vendor item code; 0 junk rows on this copy. `compare_with_snapshots`:
  **0 mismatched items**. A full `refresh_from_csv` right after the migration
  changes nothing (0 items) - the migration and the app write the same rows.
  Every item's (vendor, code) list equals `vendor_refs` (what PO pages show)
  apart from junk: 0 differences.
- **Migration on the copy:** up 0059 -> 0061, then down to 0060 and up
  again: 2.7 s, identical rows (checksum), `integrity_check` ok,
  inventory_snapshots 654,916 and orders 125,330 unchanged.
- **Upload hook cost:** a next-day CSV of 5,562 items (one reordered, one
  vendor dropped): ~1-2 s extra inside the upload (0.8 s of it is the
  newest-CSV-row query), 1 item rewritten, 5,017 rows re-dated, 0 mismatches after.

### 7. Tests

`tests/test_item_vendors.py` (38 tests): the 8 parsers vs frozen copies of
the old code on 3,000 fuzzed strings (repeats, case, junk, tabs, non-breaking
space, empty slots); code alignment; the empty-supplier preference in placed
orders; category supplier rules; `vendors_of` edge cases (None, "", spaces,
";;", repeats, case, junk, non-string); ranking (empty, order, duplicates by
case, whitespace, junk kept, codes by slot / trailing slot / misaligned /
duplicate takes a later code); agreement with `vendor_refs`; render round
trip; refresh (newest CSV row, API rows ignored, idempotent, re-dating keeps
ids, CSV wins over manual, empty CSV keeps manual, dropped vendor, items
without CSV rows, > 500 items); CSV upload fills and updates the table and a
late older file changes nothing; the upload endpoint's answer keys are
unchanged; a failing refresh never fails the upload and rolls back only its
own writes; migration 0061 backfill = app rule, columns = model, down/up;
the migration's frozen rule = app rule (1,500 fuzzed cases); the check script.

**Full backend suites** (`-p no:cacheprovider`): Python 3.9 - 2262 passed,
12 skipped, 1 failed; Python 3.12 - 2262 passed, 12 skipped, 1 failed. The one failure is
the known `test_public_checklist_link.py::test_page_paths_trailing_slash_and_nested`
(needs a built `frontend/dist`; not an MT5 change).

### 8. New script

`backend/scripts/item_vendors_check.py --db PATH [--refresh]`: read-only
check of the table against the snapshots (exit 1 on mismatches); `--refresh`
re-syncs the table only. The path is required (never taken from `.env`).

### 9. Deferred

- **Editor and import** (website, app, spreadsheet) for stores whose POS has
  no vendor field - plan 4.9 step 4, M2/M3. `source='manual'` is ready for it.
- **Reading the table for pages:** M1 pages read the item's own field. For
  a Clover store (M3) the catalog sync should fill each snapshot row's
  `supplier` / `supplier_item_id` from the table with `render_vendor_fields`
  (or `vendors_of` should take the table when the store's vendor source is
  the app), so all the existing pages work unchanged there.
- `vendor_item_code` is not yet used by PO PDFs / Send PO (they keep
  `_supplier_item_id_for` on the CSV field).
- The table is per `item_id`; MT4's `pos_item_map` maps ids to themselves for
  Spice Town, so no change is needed when MT4 merges.
- Integration: MT5 filled `0061` only; `0060` stays MT4's.

### 10. MT5 verification (adversarial check, Oct 15)

No behaviour change found; nothing needed fixing.

- **Parity on a fresh read-only production copy** (made 15:31, migration 0059,
  654,916 snapshot rows): app code at `b9501ad` (before MT5) vs `m1-mt5` HEAD
  with `--migrate`. Python 3.12: **DIFFERENCES: 0** over 114 files. Python 3.9:
  **DIFFERENCES: 0**. 110/110 endpoints 200, scheduled jobs and push alerts
  unchanged, no network attempts. The only additions are `tables.item_vendors`
  in the two fingerprints.
- **Migration 0061 on a scratch copy of it:** up, down to 0059, up again.
  `integrity_check` ok and 0 `foreign_key_check` rows at every step; every
  other table's row count unchanged; after down the table and its indexes are
  gone; the rows after the second up are identical (checksum). 5,541 rows for
  5,145 items; the check script finds 0 mismatches, and a full refresh right
  after changes 0 items.
- **Upload savepoint on a real file database** (the app's own engine, WAL),
  Python 3.9 and 3.12: a refresh that writes and then fails leaves the upload
  committed and the table empty; a working refresh commits with the upload; an
  upload with no loadable rows is fine. The report cache is still cleared by
  the upload (a savepoint rollback does not reset its "dirty" flag).
- **Every changed parser:** the same answers as before for any text or empty
  value (the only difference is for non-text values, which crashed before).
  No import cycle (every changed module imports on its own under 3.9). No new
  route, so no demo-guard change.
- **Upload time** on the fresh copy (5,562-item next-day CSV, measured 3 times
  each way at low priority): 4.8-5.7 s without the table refresh, 5.5-8.4 s
  with it. Most of the extra time (about 1.9 s of 2.1 s when measured alone)
  is the "newest CSV row per item" query, which reads every snapshot row of
  each uploaded item to skip API rows. The upload endpoint runs on the
  server's event loop, so other requests wait for those extra seconds once
  per upload. Acceptable for M1; a quicker query (newest row per item via the
  `(item_id, snapshot_date)` index) is a possible later improvement.
- Full suites: Python 3.9 2262 passed, 12 skipped, 1 failed; Python 3.12 2262
  passed, 12 skipped, 1 failed. The failure on both is the known
  `test_page_paths_trailing_slash_and_nested`, which needs a built
  `frontend/dist` that this worktree doesn't have.
## As built - MT6 (neutral contracts + capabilities)

Branch `m1-mt6` (from `multistore-m1` at `b9501ad`, built in parallel with MT2-MT5). No
migration, no new table. Nothing pushed, deployed or restarted. For Spice Town every gate passes
and every old name keeps working: the only visible change is new JSON keys.

### 1. New module - `backend/app/pos/gating.py`

| Piece | What it does |
|---|---|
| `PROVIDERS` | `"toast" -> ("Toast", TOAST_CAPABILITIES)`. The provider is `settings.pos_provider` when that setting exists (MT2 adds it), else `"toast"`. An unknown provider has **no** capabilities (every POS feature off). |
| `pos_name()`, `store_capabilities()`, `has_capability()`, `capability_values()` | The store's POS and its capability set; `capability_values()` lists the string values in the enum's own order. |
| `require_capability(*caps)` | FastAPI dependency: HTTP **409** `"Not available for this store: its point of sale (<pos>) doesn't provide <what>."` unless the POS has every capability. Runs after the login / feature checks on each route. |
| `FEATURE_CAPABILITIES` + `features_for_store()` | `open_orders` needs `OPEN_ORDERS`, `sales_insights` needs `ANALYTICS_REPORTS`; `/me` drops a feature the POS can't support. |
| `JOB_CAPABILITIES` + `drop_unsupported_jobs()` | Scheduled job function -> capabilities; jobs the POS can't serve are removed before the scheduler starts. `POS_NEUTRAL_JOB_FUNCS` lists the rest; a test fails if a new job is in neither list. |

When MT2's `app.pos.registry` lands, `store_capabilities()` / `pos_name()` can read the
connector's own `capabilities` / `display_name` (one-line change at integration); the table stays
the fallback.

### 2. Settings - neutral names, old names still read (`backend/app/config.py`)

| New | Old (still read) | Rule |
|---|---|---|
| `STORE_TIMEZONE` (default blank) | `TOAST_TIMEZONE` | Blank = use the old one. Set = it wins. After loading, `settings.store_timezone == settings.toast_timezone` always. |
| `AP_INBOX_EMAIL` (default unset) | `TOAST_INVOICE_EMAIL` | Unset = use the old one. Set = it wins. **Set but blank = the invoice-inbox email is off** (`EmailNotConfigured`, recorded in the submission's email error like missing Gmail settings). Both attributes hold the same value. |

Readers switched to the neutral names: `app/timeutil.py` (`restaurant_timezone`), the three
schedulers' zone in `app/scheduler.py`, and `services/toast_email.py` (`ap_inbox_address()`, which
also works with a settings stand-in carrying only the old name). Production's `.env` sets
`TOAST_TIMEZONE` and `TOAST_INVOICE_EMAIL` only, so it resolves exactly as before. `.env.example`
documents both new names (commented out).

### 3. API - additive only

| Endpoint | Added |
|---|---|
| `GET /api/app/config` (no login) | `pos_name` ("Toast"), `capabilities` (13 strings) after the old keys |
| `GET /api/auth/me`, `POST /api/auth/login`, `POST /api/auth/mobile-login` | `pos_name`, `capabilities` at the end; `allowed_features` is also intersected with the store's capabilities (identical for Spice Town). `/api/auth/users` unchanged. |
| Every invoice answer (`submission_out`: list, get, submit, resend, override, mark/unmark, delete flow, trash, duplicate check) | `ap_applicable`, `ap_email_sent`, `ap_email_error`, `ap_email_status_overridden_by`, `ap_closed_at`, `ap_closed_by_name`, `can_mark_ap_closed` - each equal (value and JSON type) to its `toast_*` twin, appended after the old `submission_out` keys (in `GET /api/invoices` items the list's own `close_suggestion` key still comes last, after them; the old keys keep their relative order) |
| `POST /api/invoices/{id}/mark-ap-closed`, `POST /api/invoices/{id}/unmark-ap-closed` | Aliases on the **same handler** as `mark-toast-closed` / `unmark-toast-closed` (same feature gate, same errors, same Team task close). Demo guard: both in `ALLOWLIST` with the same `own_invoice` check as the old routes. |
| `POST /api/invoices/{id}/resend` `channels`, `POST .../override-status` `channel` | `"ap_email"` accepted as the neutral name of `"toast_email"`, mapped before validation (so a 422 for anything else reads exactly as before). |

Not renamed: every `toast_*` key, the `toast_email` channel, `/mark-toast-closed`,
`/unmark-toast-closed`.

### 4. Routes gated by capability (409 when missing; all on for Spice Town)

| Route(s) | Capability |
|---|---|
| `/api/insights/*` (router) | `ANALYTICS_REPORTS` |
| `/api/insights/returning-customers` | + `CARD_FINGERPRINTS` |
| `/api/orders/*` (Open Checks router) | `OPEN_ORDERS` |
| `/api/orders/employees` | + `EMPLOYEES` |
| `/api/inventory-api/*` (Inventory from Toast) | `CATALOG` + `STOCK_LEVELS` |
| `POST /api/inventory-api/menu-recheck` | + `CATALOG_CHANGE_STAMP` |
| `GET /api/sales/accuracy`, `POST /api/sales/accuracy/run` | `DAY_TOTALS` |
| `POST /api/inventory/upload` | `CSV_CATALOG_IMPORT` |

### 5. Scheduled jobs registered by capability

| Job function (ids) | Needs |
|---|---|
| `run_daily_sales_sync` (`daily_sales_sync`, `startup_catchup_sync`) | `ORDERS_BY_BUSINESS_DATE` |
| `run_open_orders_full_scan`, `run_open_orders_today_refresh` (+ startup runs), `run_open_orders_summary` (`notify_open_orders_1600/2130`) | `OPEN_ORDERS` |
| `run_sales_repull_job` (`sales_repull_*`) | `DAY_TOTALS` |
| `run_insights_nightly`, `run_insights_catchup` | `ANALYTICS_REPORTS` |
| `run_inventory_api_snapshot` | `CATALOG` + `STOCK_LEVELS` |
| `run_menu_barcode_recheck` | `CATALOG_CHANGE_STAMP` |
| `warm_fix_issues_cache`, `run_alert_job`, `run_morning_summary`, team jobs | none (POS-neutral) |

`run_morning_summary` refreshes open checks first only when the store has `OPEN_ORDERS` (always,
for Toast). The existing settings switches (`SALES_REPULL_ENABLED`, `INSIGHTS_NIGHTLY_ENABLED`,
`MENU_BARCODE_RECHECK_MINUTES`, ...) still apply on top.

### 6. How parity was checked (production copy `m1/prodcopy.db`, Oct 15, migration 0059)

- **Python 3.12** (`m1/mt6/after-MT6-py312` vs `m1/baseline`): **DIFFERENCES: 0** across 114
  files; 110/110 endpoints 200; no network attempts; no background threads. 606 allowed additions,
  all expected: `pos_name` + `capabilities` in `app.config` and `auth.me` (4) and the 7 `ap_*`
  keys on each of the 86 invoices (602).
- **Python 3.9** (`m1/mt6/after-MT6-py39` vs `m1/baseline-py39`): **DIFFERENCES: 0**, the same
  606 additions.
- **Scheduled jobs:** `scheduler_jobs.json` is byte-identical to the baseline on both Pythons
  (main 15, notifications 6, team 5 jobs). `alerts.json` (push alerts at 09:00 / 21:30 and the
  morning summary lines) byte-identical too.
- **App builds 5-9:** a throwaway checker (`m1/mt6/dart_contract_check.py`, outside git) reads the
  `json['key'] as Type` casts of `CurrentUser`, `AppConfig` and `InvoiceSubmissionModel` from the
  mobile repo at builds 5, 6, 7, 8, 8 (Oct 14), 9 and HEAD (`git show`, read-only) and checks them
  against this branch's real answers (`/me`, `/login`, `/mobile-login`, `/app/config`, invoice list
  / get / mark-ap-closed / unmark / override with `ap_email`): **1,844 reads, 0 problems** on 3.9
  and 3.12. Sensitivity: turning one `id` into a float, dropping `file_url` and nulling
  `allowed_features` gives 133 problems. None of the models iterates over keys, and neither the
  website nor the app already uses a `pos_name`, `capabilities` or `ap_*` name.

### 7. Tests

`backend/tests/test_pos_gating.py`, 48 tests: Spice Town's capability set and labels; unknown
provider; `/app/config` and `/me` additions and key order; `/me` dropping features without the
capability; each gated route answers 409 with the message without its capability and is never
gated for Spice Town; the gate runs after 401/403; both settings fallbacks, precedence and "blank =
off" (also through `toast_email.send_toast_invoice_email` with a fake SMTP); invoice answers keep
all 28 old keys in order with each `ap_*` key equal in value and type; `/mark-ap-closed` and
`/unmark-ap-closed` behave exactly like the old routes (success, 400, 409, 404, 403 without the
feature); the demo guard refuses the aliases on a real invoice and allows them on its own;
`ap_email` channel alias; the Spice Town job list equals the baseline list; every job is
classified; each capability removes exactly its jobs; the morning summary skips its refresh
without `OPEN_ORDERS`; the schedulers use `store_timezone`.

Full suite: Python 3.9 **2272 passed, 12 skipped, 1 failed**; Python 3.12 **2272 passed, 12
skipped, 1 failed**. The one failure on both is the known
`test_public_checklist_link.py::test_page_paths_trailing_slash_and_nested` (needs a built
`frontend/dist`, which the worktree doesn't have - same as MT1).

### 8. Deferred

- **Website and app gating** on `capabilities` / `pos_name` (hide Insights, Open Checks,
  "Inventory from Toast", `{pos}` text) - the server side is ready; the clients are M2. Admins'
  `allowed_features` is now intersected too, but the app treats admins as "all features" itself.
- **Items Sold "today" live pull** (`LIVE_TODAY_SALES`) is not gated: it's inside the shared Items
  Sold route; a non-Toast store needs the MT3 connector path, not a 409.
- **Moving `toast_email.py` / `toast_pdf.py` to `app/pos/ap_inbox/`**: left in place to avoid
  clashing with MT2's moves (tests monkeypatch module globals there); only the setting was renamed.
- **Push alerts that read Toast-only caches** (loss watch, inventory-API ready, open checks in the
  morning summary) are not gated per capability - they read our own tables and send nothing when
  those are empty.
- **`to_toast` on `/api/po-sections` reset** and the other Toast-named keys outside invoices
  (`toast_category*`, `toast_received`, ...) keep their names; neutral aliases when a client needs
  them.
- **`capabilities` is per POS only**: the store-level switches (AP inbox, labels, WhatsApp, AI)
  are M2 store settings.
- Integration: point `gating.store_capabilities()` / `pos_name()` at MT2's registry, and keep
  `JOB_CAPABILITIES` keyed by function name if MT3 renames or moves job functions (the
  "every job is classified" test will flag it).

### 9. Verification - MT6 (adversarial pass, Oct 15)

- **Parity on a fresh production copy** (taken read-only at 15:18, migration 0059, 125,330
  order rows): the base commit `b9501ad` and this branch were each recorded on the same copy.
  Python 3.12: **0 differences**, 114 files, 606 additions (the 4 `/me` + config keys and 7
  `ap_*` keys on each of 86 invoices); Python 3.9: **0 differences**, same 606 additions. All 110
  endpoints 200 on both, no network attempts, no suppressed threads; `scheduler_jobs.json` and
  `alerts.json` byte-identical to the base recording on both Pythons. Independently checked:
  every `ap_*` value and JSON type equals its `toast_*` twin on all 86 invoices, and the old keys
  keep their relative order.
- **Routes**: the only route change in the whole app is the two new POST aliases
  (`/mark-ap-closed`, `/unmark-ap-closed`); 325 -> 327 routes.
- **Request validation**: resend / override-status 422 errors and the two request JSON schemas are
  identical to the base commit for every non-alias input tried (bad value, wrong type, null,
  missing); `["ap_email", "toast_email"]` sends one email (the send loop checks membership).
- **Settings**: old names only (production's `.env`; the launchd job sets only `PATH`) resolve
  exactly as before; blank or whitespace `STORE_TIMEZONE` falls back; `AP_INBOX_EMAIL=""` turns
  the email off; no code copies `Settings` around the validator (`model_copy`/`model_construct`).
  Production and dev venvs have the same pydantic / pydantic-settings / FastAPI / APScheduler.
- **Migrations** (fresh copy): `upgrade head` (0061) -> `downgrade 0059` -> `upgrade head`:
  integrity ok, 0 foreign-key problems, all 59 tables' row counts and the schema unchanged
  (0060/0061 are still the prep step's no-op placeholders; MT6 adds none).
- **Full suite**: Python 3.9 2272 passed, 12 skipped, 1 failed; Python 3.12 2272 passed, 12
  skipped, 1 failed - the known `frontend/dist` test only.
- No code problems found; only the key-order sentence above was corrected.

---

## As built - M1 integration (MT2-MT6 merged, Oct 15)

Branch `multistore-m1`. The three parallel tracks were merged in order with `--no-ff`, then
the pieces built apart were wired together in separate commits. Nothing was pushed,
deployed, installed or restarted; no real Toast / Whapi / SMTP / FCM / Anthropic call.

### 1. Merges and conflicts

| Commit | Merge | Conflicts and resolution |
|---|---|---|
| `98f76b9` | `m1-mt4` | `services/sales_sync.py`, `services/sales_accuracy.py`: MT4 edited bodies MT3 had moved to `app/pos/sync/`. Kept MT3's shims and applied MT4's hunks where the code now lives (MT3 section 9 recipe): the upsert writes `neutral_line_values`, `after_sales_sync` runs after a successful sync, `stored_day_totals` uses `Order.top_level()`. The shim also forwards `after_sales_sync` / `neutral_line_values` (names MT4 had put on `sales_sync`). MT4's "13 uses of the helper" test now counts `app/pos/sync/` too (still 13). Contract doc: both sides kept. |
| `f0e7258` | `m1-mt5` | Code clean. Contract doc and `SKILL.md`: append-only, both sides kept. |
| `d4f33c2` | `m1-mt6` | Code clean. Contract doc and `SKILL.md`: append-only, both sides kept. |

`alembic heads`: single head **0061** (0059 -> 0060 MT4 -> 0061 MT5).

### 2. Glue (separate commits)

- **`88cbcf7` MT3 sync -> MT4 columns.** `pos/sync/sales.neutral_line_rows(order, source)` builds
  the neutral `orders` columns (source, external order / check / line ids, parent line id,
  `is_modifier` from the model, `dining_option_id`, `channel` only when the POS maps one) from
  the connector's models and its `source`; the upsert writes them with the Toast columns. For
  Toast they equal MT4's `neutral_line_values(row)` (checked on 400 random orders, and row by
  row on the prod copy below). `_upsert_line_items(db, date, items)` still works without the
  neutral list. `after_sales_sync` / `record_catalog_items` take the source, so `pos_item_map`
  and `sync_cursors` are written under the store's POS ("toast" here). The open-checks cache
  writes `external_order_id` explicitly (same value the model default copied).
- **`1ea4be8` MT6 gating -> MT2 registry.** `gating.pos_provider()` normalises through
  `registry.provider_name`; `pos_name()` / `store_capabilities()` read the registered
  connector's `display_name` / `capabilities` (`app/pos/capabilities.py`); MT6's `PROVIDERS`
  table is only the fallback for a provider with no adapter. `registry.provider_name` now treats
  a whitespace-only `POS_PROVIDER` as toast (MT6's rule; before, `get_connector` raised).
  MT6's `store_without` test fixture trims the Toast connector's set instead of the table.
- **MT5 item_vendors <- CSV import:** no code change needed. The CSV reader moved to
  `pos/adapters/toast/csv_import.py` in MT2, but `parse_and_store` (which MT5 hooks) stayed in
  `services/inventory_parser.py` and reads through the adapter; covered by a new test.
- **MT4 top-level helper in MT3's moved code:** the one SQL filter MT3 moved
  (`pos/sync/accuracy.stored_day_totals`) uses `Order.top_level()`. The two Python checks on
  line dicts (`items_sold.py`, `pos/sync/open_orders.py`) stay as MT4 documented.
- **`cd51f1b`** `tests/test_m1_integration.py` (11 tests): Toast neutral rows equal MT4's
  rule; another POS writes under its own source / cursor / item map; a re-sync keeps a stored
  channel; open-checks cache id; gating from the registry (Toast, a registered connector, blank
  / padded provider); CSV upload fills `item_vendors`.
- **`940706c`** `scripts/parity/sync_compare.py` also dumps `sync_cursors`, `pos_item_map` and
  `item_vendors` when the copy has them.

### 3. Proof

**Full suite** (one test deselected as in every batch: `test_public_checklist_link.py::
test_page_paths_trailing_slash_and_nested` needs a built `frontend/dist`):

| Python | Passed | Skipped | Failed |
|---|---|---|---|
| 3.9.6 | 2453 | 12 | 0 |
| 3.12.14 | 2453 | 12 | 0 |

**Parity harness, fresh read-only production copy** (taken 17:33, migration 0059, 125,324
order rows): `08b43c0` (ops-fixes, pre-M1) recorded as is, merged HEAD recorded with
`--migrate` (0061), same copy.

| Python | Files | Differences | Additions |
|---|---|---|---|
| 3.12 | 114 | **0** | 630 |
| 3.9 | 114 | **0** | 630 |

Additions = MT6's 606 JSON keys (`pos_name` + `capabilities` in `/api/app/config` and
`/api/auth/me`, 7 `ap_*` keys on each of 86 invoices) + MT4's 22 and MT5's 2 new
columns / tables in the database fingerprints. 110/110 endpoints, 0 network attempts, 0
suppressed threads; `scheduler_jobs.json` and `alerts.json` byte-identical on both Pythons.
On the MT1 copy (`m1/prodcopy.db`) merged HEAD vs the MT1 baselines: **0 differences** on 3.12
and on 3.9 (same 630 additions).

**Migrations on the fresh copy** (HEAD code, both Pythons): upgrade 0059 -> 0061 (4.9-6.6 s),
downgrade to 0059 (2.1-2.2 s), upgrade again (2.0-3.1 s). `integrity_check` ok and
`foreign_key_check` empty at each step; row counts of every pre-M1 table and a checksum of the
old `orders` / `open_order_cache` columns unchanged at every step; schema after the downgrade
identical to the original. After upgrade: 125,324 orders rows all `source='toast'` with
`external_line_id = toast_selection_guid`, 9,327 modifiers, 6,559 ids in `pos_item_map`, cursor
2026-10-14, `item_vendors` 5,541 rows for 5,145 items.

**Row-by-row sync comparison** (`sync_compare.py`, copy migrated to 0061): old = MT2 + MT4
before MT3 (a `git merge-tree` of `aa7688e` and `m1-mt4`, backend merged cleanly), new =
merged HEAD. 142 business dates, 24,560 orders, 371 Toast calls each side.

| Table (every column) | Rows | Differing |
|---|---|---|
| orders (incl. MT4 columns) | 125,305 | 0 |
| open_order_cache | 78 | 0 |
| job_runs | 6,388 | 0 |
| toast_menu_items | 5,222 | 0 |
| inventory_snapshots | 16,005 | 0 |
| sync_cursors | 1 | 0 |
| pos_item_map | 6,559 | 0 |
| item_vendors | 5,541 | 0 |

33 function answers equal; **DIFFERENCES: 0** on 3.12 and on 3.9. With MT3's own proof
(`aa7688e` vs MT3, 0) and MT4's replay (base vs MT4 on the old columns, 0), the merged sync
writes what 08b43c0 wrote, plus the neutral columns.

### 4. Deferred (unchanged from the batches)

- Website / app hiding pages from `capabilities` (M2); Items Sold "today" and Toast-only
  push alerts not gated; `toast_email.py` / `toast_pdf.py` not moved to `pos/ap_inbox/`.
- `orders.channel` stays empty for Toast; reports and the upsert key still use the Toast
  columns (contract step, plan 8.1).
- Connector `run_report`, `fetch_orders_modified`, `parse_catalog_export`; POS errors not yet
  mapped to 409 / 503.
- MT5's faster newest-CSV-row query; vendor editor; Clover pages reading `item_vendors`.
- Owner decisions from MT1: Litestream keys, installing the nightly backup.

### 5. Verification - M1 integration (adversarial pass, Oct 15)

An independent check of the merged branch (HEAD `0ccaef4`). **No problems were confirmed, so no code changed.**

**Fresh production copy** (taken 17:55 read-only, migration 0059, 125,324 order rows). The old side was the pre-M1 app (`08b43c0`, recorded with the MT1 harness from `d2b73a3`). The new side was the merged code with `--migrate` (0059 -> 0061).

| Python | Differences | Allowed additions | Endpoints 200 | Network attempts | Threads not run |
|---|---|---|---|---|---|
| 3.12 | 0 | 630 | 110 / 110 | 0 | 0 |
| 3.9 | 0 | 630 | 110 / 110 | 0 | 0 |

- **What the 630 additions are:** `pos_name` and `capabilities` on `/api/app/config` and `/api/auth/me`; 7 `ap_*` keys on each of the 86 invoices; and in both fingerprints, the 3 new tables plus the 9 new columns.
- **The `ap_*` keys:** all 602 of them equal their `toast_*` twins.
- **Scheduled jobs and push alerts:** byte-identical.

**Routes:** the full method + path + status + response-model table of `app.main` was compared, old against new. The only changes are 2 added routes: `POST /api/invoices/{id}/mark-ap-closed` and `/unmark-ap-closed`. Both are in the demo-guard allowlist with the `own_invoice` check. Nothing was removed or renamed. OpenAPI builds with 266 paths and no duplicate-operation warnings.

**Migration up, down, up on that copy** (both Pythons):
- `integrity_check` was ok and `foreign_key_check` was empty at every step.
- Every pre-M1 table kept its row count at every step, and the old `orders` column aggregates were unchanged.
- After the downgrade, the schema is identical to the original. Upgrading again gives the same schema and counts as the first upgrade.
- Backfill: all 125,324 rows have `external_line_id`, `external_order_id` and `dining_option_id` equal to their Toast twins, and `is_modifier` matches the parent rule (9,327 modifiers). `channel` is NULL everywhere, `source` is `toast` everywhere, and there are 6,559 `pos_item_map` rows, 5,541 `item_vendors` rows and 1 cursor (2026-10-14). All 24 `open_order_cache` rows have `external_order_id`.

**Row-by-row sync comparison**, rebuilt independently: the old side came from a new `git merge-tree --write-tree aa7688e m1-mt4` (MT2 + MT4 before MT3), compared against the merged HEAD on the migrated fresh copy (3.12).
- Every row matched in all 8 tables:
  - orders: 125,305
  - open_order_cache: 78
  - job_runs: 6,390
  - toast_menu_items: 5,222
  - inventory_snapshots: 16,005
  - sync_cursors: 1
  - pos_item_map: 6,559
  - item_vendors: 5,541
- The 33 function answers and the 371 Toast calls matched too. **DIFFERENCES: 0.**

**Tests:** see the table below. In both runs the one failing or deselected test is `test_public_checklist_link.py::test_page_paths_trailing_slash_and_nested`. It needs a built `frontend/dist` and fails the same way on the pre-M1 code, so it is not an M1 problem.

| Python | Passed | Skipped | Failed |
|---|---|---|---|
| 3.12 | 2453 | 12 | 1 (the `frontend/dist` test) |
| 3.9 | 2453 | 12 | 0 (`frontend/dist` test deselected) |

**Other checks that found nothing:**
- All 171 `app` modules import on their own in a fresh 3.9 interpreter, so there are no import cycles.
- No 3.10+ syntax in the M1 diff.
- `job_runs` names are unchanged; the only new name in the grep is a constraint name.
- No other writers of `orders` or `open_order_cache` bypass the neutral columns.
- No `parent_selection_guid IS NULL` filter is left outside `Order.top_level()`.
- `line_rows` and `neutral_line_rows` stay index-aligned: both iterate `order.lines`.
- MT5's upload hook runs in a savepoint, so a failure cannot roll back the snapshot rows.
- `POS_PROVIDER`, `STORE_TIMEZONE` and `AP_INBOX_EMAIL` are not set in the production `.env` or the backend LaunchDaemon, so the neutral settings fall back to the Toast values.
- No frontend or requirements changes.

---

## Final check - M1 (Oct 15, evening) and the separate-product decision

**Owner decision made while M1 ran:** the multi-store product is a **separate product**, not
merged into Spice Town for now. `multistore-m1` is therefore **not merged** into `ops-fixes` /
`main`, **not pushed** and **not deployed**; it becomes the start of a new repository (name and
GitHub repo pending). Hosting: Docker first, no Kubernetes until about 20-50 stores. The owner
handoff (repo split steps, de-branding list with file:line, backups, parity how-to, hosting) is
`docs/MULTISTORE_M1_HANDOFF.md` (canonical copy `/Users/sundar/spicetown-dev/MULTISTORE_M1_HANDOFF.md`).
No code changed in this step.

**Fresh read-only production copy** taken 18:09 (434 MB, migration 0059, 125,324 order lines);
deleted afterwards. Old side: `08b43c0` (= `ops-fixes` = `main`, `git archive` plus the current
harness scripts). New side: `multistore-m1` at `d4d86ad` with `--migrate`.

| Check | Python 3.9.6 | Python 3.12.14 |
|---|---|---|
| Full backend suite (website built first, so nothing deselected) | 2454 passed, 12 skipped, 0 failed | 2454 passed, 12 skipped, 0 failed |
| `alembic heads` | `0061 (head)` only | `0061 (head)` only |
| Upgrade 0059 -> 0061 / downgrade -> 0059 / upgrade again | 4.25 / 2.05 / 1.87 s | 4.14 / 2.03 / 1.75 s |
| Parity, 114 files | **0 differences**, 630 additions | **0 differences**, 630 additions |

- Migrations: `integrity_check` ok and `foreign_key_check` empty at every step; every pre-M1
  table's row count and the old `orders` / `open_order_cache` column checksums unchanged at
  every step; schema after the downgrade identical to the original; second upgrade's schema
  identical to the first. After upgrade: 125,324 rows with `external_line_id =
  toast_selection_guid`, 9,327 modifiers, all `source='toast'`; 6,559 `pos_item_map`; cursor
  2026-10-14; `item_vendors` 5,541 rows / 5,145 items; 25 of 25 `open_order_cache` rows with
  `external_order_id`.
- Parity: 110/110 endpoints 200 on all four recordings, 0 network attempts, 0 background
  threads not run; `scheduler_jobs.json` and `alerts.json` byte-identical on both Pythons.
  `--strict` lists exactly the 630 additions (in 5 files: `app.config`, `auth.me`,
  `invoices.list` and the two fingerprints) and nothing else.
- Website: `node --test` on all 21 `*.test.mjs` files: 208 passed, 0 failed. `vite build`
  succeeded (`frontend/node_modules` symlinked to the `website` checkout's; `dist/` is
  git-ignored).
- Logs: `/Users/sundar/.claude/jobs/e26a1611/tmp/multitenant/m1/final/`.

**Deferred** (unchanged): see "As built - M1 integration", section 4, and the handoff's
section 7.
