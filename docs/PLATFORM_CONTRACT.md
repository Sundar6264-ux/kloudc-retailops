# StoreOps platform contract (shared service, database per store)

Milestone after M2: **one shared StoreOps service**, each store's data in its
**own PostgreSQL database**, a **control plane** run by the master admin, store
sign-up by request + master approval, one login page, store-admin
integrations, and a guarded path to move one store to its own server.

This file is the **shared interface** the four parallel tracks (A-D, section
19) code against. Each track appends an "As built - <part>" section at the
end. A track that must change an interface changes it here, in the same
commit, and says so in its As built section.

Sources: `MULTI_STORE_PLAN.md` 3.2, 6, 8.2, 9.2, 11.6 (M5, MT24-MT32);
`docs/M2_CONTRACT.md` (M2 interfaces, still in force unless replaced here);
`docs/MULTISTORE_M1_CONTRACT.md`; `M2_HANDOFF.md`. Survey of `main` at
`aeffc15` (section 1).

---

## 0. Owner decisions and ground rules

### 0.1 Owner decisions (final)

| # | Decision |
|---|---|
| P1 | **One shared StoreOps service**; each store's data in its **own PostgreSQL database** (database per store, not schema per store, not shared tables). |
| P2 | **PostgreSQL only for production** (owner: "Use postgre sql, not sqllite"). Code stays dialect-neutral where cheap so unit tests can still run on SQLite, but the **real suite and CI run on Postgres 16**. |
| P3 | A **control plane** (platform database) with **master admins** (the owner / KloudC), separate from store users. |
| P4 | New stores **sign up by request**; they stay **pending** until a master **approves**; approval **provisions automatically** (create database, migrate, store-init with a pack, create the owner's admin login). |
| P5 | **One login page** for everyone, website and app: **Store ID + Username + Password**. The app's two-step "store code, then login" becomes one screen. |
| P6 | Integrations are set up by the **store admin** after login (Settings -> Integrations): Toast (with "Test connection"), Clover "Connect" (placeholder until M3), WhatsApp, email (SMTP), AI key. Secrets **encrypted at rest**, never returned in full. |
| P7 | **Dedicated server option**: any one store can later move to its own server, **only with master approval**. Registry fields + routing hooks + a guarded, feature-flagged (off) code path + a runbook. |
| P8 | The **Notifications page is removed from the website** (route redirects to the dashboard; alerts are controlled in the app). The API stays. |
| P9 | Sign-up mode: **request + master approves** (no self-serve activation). |

### 0.2 Ground rules for every track

1. **Python 3.12 only** (Docker target). The M2 "Python 3.9 compatible" rule
   (M2 0.4) is lifted; `X | Y` and `match` are allowed. CI drops 3.9.
2. **Additive API.** No existing route, JSON key or push `data` key is
   renamed or removed (M2 0.1), except the website Notifications **page**
   (P8). New request fields are optional where a single-store (M2) server
   must still accept old clients (`store_code` on login is optional in
   `single` mode, required in `multi` mode).
3. **Two modes, one code base.** `TENANCY_MODE=single` (default; today's M2
   behaviour: one store from `DATABASE_URL` + `STORE_CODE`, no platform
   database) and `TENANCY_MODE=multi` (the shared service). Every change must
   keep `single` working; the existing suite proves it.
4. **Tenant from the credential, never from a header alone** (section 4).
5. **No outside calls in tests** (M2 0.5): fakes for Toast, Clover, Whapi,
   SMTP, FCM, Anthropic. "Test connection" code paths are injected fakes in
   tests.
6. **No secrets in git.** `.env.example` blank. The master key ring comes from
   env (`STOREOPS_SECRET_KEYS`); a dev key ring is generated only when
   `APP_ENV=development` into a gitignored file (`backend/.secrets/`).
7. **Postgres databases in dev/tests**: cluster `127.0.0.1:5433`, user
   `storeops` (trust, local only), binaries `/opt/homebrew/opt/postgresql@16/bin`.
   Every agent creates its own databases with its track prefix (`so_a_*`,
   `so_b_*`, `so_c_*`, `so_d_*`, integration `so_int_*`) and drops them when
   done. Never touch another prefix.
8. **Heavy runs** (full suites, Playwright, flutter full runs, big
   migrations) only via `/Users/sundar/.claude/jobs/e26a1611/tmp/m1heavy.sh <cmd...>`,
   one at a time; `flutter test --concurrency=2`.
9. **Spice Town is off limits** (`/Users/sundar/spicetown-dev/*`,
   `/Users/sundar/spicetown-mobile`, `/Users/sundar/spicetown-backend`).
   Read-only production copies only for a parity check, deleted after.
10. **File ownership** is in section 19. Touching another track's file is
    allowed only for a one-line import/registration/include, noted in As
    built.
11. **Commits**: logical commits, message ends with
    `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

---

## 1. Survey: what blocks Postgres and multi-tenancy (main @ `aeffc15`)

Scope: `backend/app` (192 `.py` files, 60,682 lines), `backend/alembic`
(61 migrations, head `0062`), `backend/tests` (97 `test_*.py`), `backend/scripts`.

### 1.1 SQLite-only SQL in application code

| Item | Sites | Fix (owner A) |
|---|---|---|
| `sqlalchemy.dialects.sqlite.insert` upserts | **4 live**: `pos/sync/catalog.py:282` (`on_conflict_do_nothing`), `pos/sync/sales.py:157`, `services/inventory_parser.py:114`, `services/insights_data.py:307` (`on_conflict_do_update`). **2 dead imports**: `services/inventory_api.py:79`, `services/sales_sync.py:30` (noqa). | `app.dbcompat.dialect_insert(table, bind)` (both dialects support `on_conflict_*` with `index_elements`); delete dead imports. |
| `INSERT OR IGNORE` raw SQL | `pos/neutral_ids.py:104`, `:116` (also migration 0060, irrelevant on PG - 11.1) | `INSERT ... ON CONFLICT DO NOTHING` (valid on SQLite >= 3.24 and PG). |
| `sqlite_sequence` read | `team/changes.py:248` (`current_cursor`) | PG: `SELECT last_value FROM pg_sequences` / `pg_get_serial_sequence`; the bare `except` there also **poisons the PG transaction** (1.4). |
| SQLite functions in raw SQL | `services/report_cache.py:158-165`: `ifnull(...)`, `count(*) \|\| ':'`, `max(finished_at) \|\| ''` (timestamp concatenated with text) | `coalesce`, explicit `CAST(... AS TEXT)`. |
| `func.total` (SQLite-only aggregate) | `services/similar_products.py:893-895` | `func.coalesce(func.sum(x), 0.0)`. |
| Timestamps bound as SQLite text | `pos/neutral_ids.py:84-86` `_db_time()` used at `:100,:127,:151` | bind real aware-UTC `datetime` objects. |
| Dates bound as ISO strings | `pos/neutral_ids.py:163` (`business_date = :d` with `.isoformat()`) | bind `dt.date`. |
| Boolean default `text("0")` | `models.py:113` (`server_default=text("0")` -> `DEFAULT 0` on a boolean, rejected by PG) | `sa.false()`. The other `server_default="0"/"1"` on Booleans (`models.py:267,269,996,1065`, `po_send_models.py:42`, `team/tasks_models.py:76`, `team/chat_models.py:147`) render as quoted literals, which PG accepts; A normalises them to `sa.false()/sa.true()` in the baseline. |
| `PRAGMA` / WAL / `check_same_thread` | `db.py:42-70` (engine setup) | B: dialect branch in the engine factory (5.2). |
| `sqlite_autoincrement=True` | `models.py:255`, `setting_models.py:31` + 9 migration tables | harmless on PG; the baseline uses `IDENTITY` (never reuses ids, same guarantee). |
| Raw `text()` SQL total | **11 sites in 5 files**: `pos/neutral_ids.py` (7), `services/report_cache.py:158`, `team/changes.py:248`, `store/__main__.py:59,68`, `models.py:113` | all reviewed by A; `store/__main__.py` ones are portable. |
| Case-insensitive `LIKE` | `.like()` x6 (`pos/sync/catalog.py:259`, `services/item_vendors.py:201`, `services/inventory_api.py:216,225,814`, `services/inventory_parser.py:79`): all machine-written prefixes (`api:`, `backfill:`), case does not matter. `.ilike()` x3 (`team/chat_service.py:888,896`, `team/tasks_service.py:654`) portable. `func.lower(...).contains` (`services/flyers.py:816`) portable. | no change needed; covered by the C-collation rule (11.3). |
| `GROUP BY` looseness | **29 `group_by(` calls in 17 files** (`pos/sync/accuracy.py`, `team/bootstrap.py`, `team/tasks_service.py`, `services/flyers.py`, `checklist_admin.py`, `forecast.py`, `stock_issues.py`, `reconciliation.py`, `item_vendors.py`, `similar_products.py`, `inventory_api.py`, `purchase_order_log.py`, `fix_issues.py`, `reorder.py`, `notifications.py`, `insights.py`, `delivery_suggestions.py`) | A runs the suite on PG; every "must appear in the GROUP BY clause" is fixed at its site. |
| Other functions used | `func.count/max/min/sum/coalesce/lower/trim/length/distinct` only - all portable. No `strftime`, `julianday`, `date()`, `json_extract`, `group_concat`, `round` in app code. | - |

### 1.2 Migrations

- 61 revisions `0002`-`0062` written for SQLite. **15 files use
  `batch_alter_table`** (0026, 0029, 0031, 0032, 0033, 0036, 0038, 0039, 0040,
  0041, 0042, 0043, 0045, 0046, 0053); `0029` and `0033` use
  `recreate="always"` + `sqlite_autoincrement` and read/write `sqlite_master`
  / `sqlite_sequence` directly (`0029:31,47-51`); `0060:116,131` use
  `INSERT OR IGNORE`. 275 lines across the migrations touch raw SQL,
  `server_default`, or SQLite specifics.
- Several migrations **seed rows** (0026 teams, 0034/0037 category overrides,
  0039 checklist definitions, 0044 WhatsApp groups, 0058 notification
  defaults, 0060 cursors); `store-init` removes some of them (M2 3.5).
- `alembic/env.py` has no `render_as_batch`, reads `get_settings().database_url`
  only (no per-store URL).
- Decision: **a frozen Postgres baseline at `0062`** instead of replaying
  0002-0062 on PG (plan 8.2), section 11.1.

### 1.3 Types

Columns in models: `String` 258, `Integer` 155, `UTCDateTime` 108, `Text` 54,
`Boolean` 32, `Float` 29, `Date` 16; 62 tables. No `Enum`, `JSON`,
`LargeBinary` or length-limited `String(n)`.

`UTCDateTime` (`db.py:11-37`): `impl = DateTime(timezone=True)`, binds **naive
UTC**, re-attaches UTC on read. On PG that maps to `timestamptz` and a naive
bind is interpreted in the **session TimeZone** - wrong unless the session is
UTC. Fix (A): engines connect with `options=-c timezone=UTC` (B, 5.2) **and**
`UTCDateTime` binds aware UTC on PostgreSQL (naive UTC on SQLite as today).

### 1.4 Transaction semantics that differ on PG

- An error inside a PG transaction **aborts the transaction** until rollback.
  SQLite lets the next statement run. Sites that catch a DB error and carry on
  in the same session: **14 `except IntegrityError`** (`routers/vendor_calendar.py:62`,
  `team/chat_service.py:158,171,361,703,1008`, `team/announcements_service.py:187`,
  `team/moderation.py:90`, `team/recurrence.py:443`, `team/rules.py:84`,
  `services/suggestion_dismissals.py:52`, `services/po_send.py:771`,
  `services/notifications.py:381,965`), **3 `begin_nested`** (savepoints;
  portable: `team/recurrence.py:426`, `services/item_vendors.py:328`,
  `services/notifications.py:405`), and the broad `except Exception` around a
  query in `team/changes.py:246-251`. A audits each: wrap the risky statement
  in `begin_nested()` or roll back before continuing.
- NULL ordering: SQLite sorts NULLs **first** in ASC, PG **last**. A checks
  ordered lists in tests; where order is user-visible, add
  `.nulls_first()` to keep today's behaviour.
- Text ordering: SQLite is bytewise. Store databases are created with
  `LC_COLLATE 'C'` / `LC_CTYPE 'C'` (11.3) so ordering, `lower()` and `ILIKE`
  match SQLite exactly (ASCII-only case folding, as today).

### 1.5 Single-store assumptions (multi-tenancy blockers)

| Item | Count / sites | Plan |
|---|---|---|
| `Depends(get_db)` | **305 in 32 files** | unchanged; `get_db()` yields the **current store's** session (5.3). |
| `SessionLocal()` calls | **34 calls in 21 files** (`scheduler.py` x9, `store/__main__.py` x3, `services/invoice_submission.py:396,406`, `flyer_sender.py:142,379`, `checklist_schemas.py:106,263`, `notification_triggers.py:526,681`, `routers/sales.py:153`, `pos/sync/open_orders.py:418`, `team/source_sync.py:62`, `team/router_core.py:102`, `team/router_chat.py:45`, `team/jobs.py:35`, `team/tasks_service.py:753`, `inventory_api_switch.py:79`, `whatsapp_groups.py:105`, `insights_data.py:972`, `checklist_submission.py:499`, `store/pack.py:678`, `store/identity.py:148`) | unchanged; `SessionLocal()` becomes **tenant-aware** (opens the current store's engine; raises `NoStoreContext` in multi mode without a store). |
| `Session(bind=bind)` from `db.get_bind()` | `inventory.py:217,221` -> `stock_issues.py:232`, `inventory_api.py:552`, `fix_issues.py:747` | portable: the bind is the tenant engine. |
| `get_settings()` | **111 calls in 53 files**; module-level copies `db.py:39`, `main.py:90` | unchanged call sites; `get_settings()` returns the **current store's merged Settings** in multi mode (8.1). |
| Module-level locks | **45** (list in 6.4) | per-store `keyed_lock` or kept global (classified). |
| Module-level caches / state | **28** mutable containers + 2 `lru_cache` + 8 `None` placeholders (list in 6.5) | `tenant_cache()` / `tenant_state()` or kept global (classified). |
| Raw threads (lose contextvars) | `routers/sales.py:173`, `pos/sync/open_orders.py:431`, `services/stock_issues.py:200`, `services/insights_data.py:184` (`_spawn`), `services/flyer_sender.py:116` (sender loop) | `spawn_in_store()` (6.6); the flyer sender loops over stores. |
| Schedulers | 3 in-process `BackgroundScheduler`s (`scheduler.py:279,455,524`), **16 `add_job`** + team jobs registered via `team/jobs.py` (`TEAM_JOBS`), timezone = one store's `store_timezone` | one scheduler process, per-store jobs in each store's timezone (6.2). |
| Long-poll signal | `team/changes.py:181-235` (one process-wide version) | version per store (6.7). |
| Uploads | `team/media.py:125` (`team_uploads_dir`), `services/invoice_submission.py:96` (`invoice_uploads_dir`), `services/flyers.py:163` (`flyers_dir`), `store/identity.py:105` (`uploads_root`), `services/insights_data.py:710` (`insights_hmac_key_file`) | per-store roots `<UPLOADS_ROOT>/<CODE>/...` via the settings overlay (7). |
| Auth / sessions | `auth.py:41` opaque token, stored raw as PK in `user_sessions` (`models.py:273-285`); cookie `session_token`; lookup `auth.py:97-114` | store-bound token, stored hashed (4.2). |
| POS connector cache | `pos/registry.py:36-37` (by provider name) | key by (store, provider) (9). |
| Toast token caches | `pos/adapters/toast/client.py:33,56` (keyed by `toast_client_id`), `pos/adapters/toast/analytics.py:88-90` (one global token) | keyed by store (6.5). |
| Direct engine use | `services/report_cache.py:43,131-133` (event listeners on the one engine), `services/fix_issues.py:744` (`from app.db import engine`) | `register_engine_hook()` / `tenant_engine()` (6.5). |
| Push | `services/push.py:29-30` one Firebase app (product-wide: kept), `data.store` from `settings.store_code` | store code from tenant context. |
| Identity stamp | `store/identity.py` checked once in lifespan (`main.py:71`) | per store, on first engine open (5.2). |
| Pack cache | `store/pack.py:664-665` `_state` | per store (`tenant_state`). |
| Public routes without a store | `tests/test_review_route_gates.py:22-50`: login x2, logout, `/api/notifications/unregister`, `/api/app/config`, `/api/app/legal`, `/api/app/branding`, `/api/public/checklists/{token}` x4, `/c`, `/c/{path}`, `/branding/{name}`, `/.well-known/*` x2, `/api/health` | explicit store code (4.4). |
| Test harness | `tests/conftest.py`: SQLite file, `drop_all/create_all` per test, engine imported at module load; 131 `monkeypatch`/assignments on `get_settings()` attributes | A: Postgres harness (18.1). |
| Scripts that are SQLite-only | `scripts/backup/*` (Litestream / `PRAGMA integrity_check`), `scripts/parity/*` (`sqlite_master`, `PRAGMA table_info`), `scripts/category_fix_list.py`, `scripts/demo/generate_demo_data.py:1033` | parity tools stay SQLite (they read SQLite copies); A adds `scripts/pg/` (copy + verify) and a per-store `pg_dump` backup; demo generator gets a dialect branch. |
| Deps | `requirements.txt` has no Postgres driver (venv has `psycopg 3.3.6`); `cryptography 50.0.2` present (via firebase-admin) | A pins `psycopg[binary]==3.3.6`; C pins `cryptography==50.0.2`. |

---

## 2. Architecture

```
            app.<PLATFORM_DOMAIN>  (website + API, one image, TENANCY_MODE=multi)
                     |
        TenantMiddleware: token -> store (or explicit public store code)
                     |
     +---------------+-------------------------+
     |                                         |
 Platform DB (so_platform)          Engine registry (lazy, LRU, bounded pools)
 stores, signup_requests,             |            |             |
 platform_users, store_settings,   so_store_a   so_store_b ...  so_store_n
 store_credentials, audit_log,     (Postgres, one database per store, schema = alembic head)
 dedicated_hosting_requests,
 login_attempts, platform_sessions
                     |
 Scheduler (one leader per deployment, advisory lock) -> per-store jobs in store tz
 Uploads: <UPLOADS_ROOT>/<STORE_CODE>/{team,invoices,flyers,.keys}
```

- The website and the API are served from the **platform origin**
  (`PLATFORM_PUBLIC_URL`, default `https://app.<PLATFORM_DOMAIN>`).
- One process per node runs web + scheduler (uvicorn, 1 worker, threads), as
  today. A second web process may run with `SCHEDULER_ENABLED=false`; the
  scheduler leader is elected by a Postgres advisory lock on the platform DB
  (6.2), so two schedulers never run jobs twice.
- A **dedicated** store runs the same image on its own server and database;
  the shared service answers `store_moved` for it (section 12).

---

## 3. Platform database schema (owner C; Alembic env `alembic_platform`)

Database name `so_platform` (setting `PLATFORM_DATABASE_URL`). All timestamps
`timestamptz` UTC. Text columns `varchar`/`text`. Codes are stored upper case.

### 3.1 `stores`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint identity` PK | |
| `code` | `varchar(24)` UNIQUE NOT NULL | pattern `^[A-Z0-9][A-Z0-9_]{2,23}$`; not in `RESERVED_CODES` (4.1) |
| `name` | `text` NOT NULL | display name (copied to store_settings `store_name`) |
| `status` | `varchar(16)` NOT NULL | `pending` (approved, row created, not provisioned yet) / `provisioning` / `active` / `suspended` / `failed` (provisioning failed; resumable) / `archived` (off-boarded, login refused, DB kept until purge) |
| `status_reason` | `text` NULL | why suspended/failed |
| `hosting` | `varchar(16)` NOT NULL default `shared` | `shared` / `dedicated` (12) |
| `api_base_url` | `text` NULL | NULL = the platform origin; set only for `dedicated` (https, no path, host under `PLATFORM_DOMAIN` or `DIRECTORY_EXTRA_API_HOSTS`; never `spicetown.shop`) |
| `db_name` | `varchar(63)` UNIQUE NOT NULL | `STORE_DB_PREFIX + code.lower()`, e.g. `so_xyz_grocery` |
| `db_host_ref` | `varchar(64)` NULL | NULL = the shared cluster (`STORE_DATABASE_URL_TEMPLATE`); else a key into `STORE_DB_HOSTS` env (dedicated; no credentials in this table) |
| `timezone` | `varchar(64)` NOT NULL | IANA |
| `pos_provider` | `varchar(16)` NOT NULL | `toast` / `clover` / `csv` |
| `pack_id` | `varchar(64)` NOT NULL | pack used at store-init |
| `instance_id` | `varchar(32)` NULL | the `store_identity` stamp written at provisioning (M2 1.5); must match the store DB |
| `schema_revision` | `varchar(32)` NULL | last Alembic revision seen by `migrate-all` |
| `settings_version` | `integer` NOT NULL default 1 | bumped on every store_settings / store_credentials change (cache invalidation, 8.3) |
| `provisioning_step` | `varchar(32)` NULL | last completed step (10) |
| `maintenance_message` | `text` NULL | shown by store lookup while `suspended` |
| `min_build_ios`, `min_build_android` | `integer` NOT NULL default 0 | directory compat |
| `signup_request_id` | `bigint` NULL FK -> `signup_requests.id` | |
| `created_at` | `timestamptz` NOT NULL | |
| `created_by` | `varchar(128)` NOT NULL | `platform:<username>` or `signup:<request id>` |
| `approved_by` | `varchar(128)` NULL | `platform:<username>` |
| `approved_at` | `timestamptz` NULL | |
| `updated_at` | `timestamptz` NOT NULL | |

Index: `(status)`.

### 3.2 `signup_requests`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint identity` PK | |
| `requested_code` | `varchar(24)` NOT NULL | normalised; unique among `status='pending'` (partial unique index) and not taken in `stores` |
| `store_name` | `text` NOT NULL | 2-80 chars |
| `contact_name` | `text` NOT NULL | |
| `contact_email` | `text` NOT NULL | basic email check; lower-cased |
| `contact_phone` | `text` NULL | |
| `address` | `text` NULL | |
| `timezone` | `varchar(64)` NOT NULL | IANA, validated |
| `pos_provider` | `varchar(16)` NOT NULL | `toast` / `clover` / `csv` |
| `owner_username` | `varchar(64)` NOT NULL | becomes the store's first admin |
| `owner_password_hash` | `text` NOT NULL | bcrypt (same as `app.auth.hash_password`); the password itself is never stored or logged |
| `notes` | `text` NULL | free text, max 2000 |
| `status` | `varchar(16)` NOT NULL | `pending` / `approved` / `rejected` / `withdrawn` |
| `status_token_hash` | `varchar(64)` NOT NULL | sha256 of the one-time token returned at sign-up (pending page polling) |
| `reviewed_by` | `varchar(128)` NULL | |
| `reviewed_at` | `timestamptz` NULL | |
| `review_note` | `text` NULL | shown to the requester on reject |
| `store_id` | `bigint` NULL FK -> `stores.id` | set on approve |
| `client_ip` | `varchar(64)` NULL | rate limiting / audit |
| `created_at` | `timestamptz` NOT NULL | |

### 3.3 `platform_users` and `platform_sessions`

`platform_users`: `id` identity PK; `username varchar(64)` UNIQUE (compared
lower-case); `password_hash text`; `display_name text`; `email text NULL`;
`role varchar(16)` (`master`; room for `support` later); `is_active bool`;
`totp_secret_enc text NULL` (encrypted like credentials, 8.4); `created_at`;
`last_login_at NULL`.

`platform_sessions`: `token_hash varchar(64)` PK (sha256 hex of the full
token); `platform_user_id` FK; `created_at`; `expires_at`; `client_ip NULL`.

Master accounts are **never** rows in a store database and store users are
**never** rows here.

### 3.4 `store_settings`

| Column | Type | Notes |
|---|---|---|
| `store_id` | FK -> `stores.id` | PK part |
| `key` | `varchar(64)` | PK part; must be in `STORE_SETTING_KEYS` (8.2) |
| `value` | `text` NOT NULL | JSON-encoded scalar |
| `updated_at` | `timestamptz` | |
| `updated_by` | `varchar(128)` | `platform:<u>` / `store:<CODE>:<username>` / `system` |

No secrets here (secrets live in `store_credentials`). History is in `audit_log`.

### 3.5 `store_credentials`

| Column | Type | Notes |
|---|---|---|
| `id` | identity PK | |
| `store_id` | FK | UNIQUE `(store_id, kind)` |
| `kind` | `varchar(16)` | `toast` / `clover` / `whatsapp` / `smtp` / `ai` (9.2) |
| `public_fields` | `text` (JSON) | non-secret fields (client id, restaurant GUID, SMTP host...) - returned to the store admin |
| `ciphertext` | `text` | base64 of AES-256-GCM ciphertext+tag of the JSON of the **secret** fields |
| `nonce` | `varchar(32)` | base64, 12 bytes, random per write |
| `key_id` | `varchar(32)` | which key in the ring encrypted it |
| `last4` | `varchar(8)` NULL | last 4 chars of the primary secret (display only) |
| `status` | `varchar(16)` | `untested` / `ok` / `failed` |
| `last_tested_at` | `timestamptz` NULL | |
| `last_test_message` | `text` NULL | plain English, never includes secrets |
| `updated_by` | `varchar(128)` | |
| `updated_at`, `created_at` | `timestamptz` | |

### 3.6 `audit_log` (append only)

`id bigint identity` PK; `at timestamptz`; `actor_type` (`platform_user` /
`store_user` / `signup` / `system`); `actor_id varchar(64) NULL`;
`actor_name varchar(128) NULL`; `store_code varchar(24) NULL`;
`action varchar(64)` (dotted names, 3.8); `target varchar(128) NULL`;
`details text` (JSON, **never** secrets or passwords); `client_ip NULL`;
`request_id NULL`. Indexes `(store_code, at)`, `(action, at)`. The migration
adds a trigger that rejects `UPDATE`/`DELETE` on `audit_log` (PG only).

### 3.7 `dedicated_hosting_requests`

`id` identity PK; `store_id` FK; `requested_by varchar(128)`; `reason text`;
`target_api_base_url text NULL`; `target_db_host_ref varchar(64) NULL`;
`status` (`requested` / `approved` / `rejected` / `migrating` / `done` /
`rolled_back` / `cancelled`); `reviewed_by NULL`; `reviewed_at NULL`;
`review_note NULL`; `created_at`; `completed_at NULL`. At most one open
(`requested`/`approved`/`migrating`) request per store (partial unique index).

### 3.8 `login_attempts`

`id` identity PK; `at timestamptz`; `store_code varchar(24)`;
`username_lower varchar(64)`; `client_ip varchar(64)`; `ok bool`. Index
`(store_code, username_lower, at)`, `(client_ip, at)`. Pruned after 7 days by
a platform job. Used for lockout across processes (13.2).

Audit actions (minimum): `login.ok`, `login.failed`, `login.locked`,
`platform_login.ok`, `platform_login.failed`, `signup.created`,
`signup.approved`, `signup.rejected`, `store.provisioning_started`,
`store.provisioned`, `store.provision_failed`, `store.suspended`,
`store.resumed`, `store.archived`, `store.settings_changed`,
`credential.saved`, `credential.deleted`, `credential.tested`,
`credential.decrypt_failed`, `hosting.requested`, `hosting.approved`,
`hosting.rejected`, `hosting.completed`, `hosting.rolled_back`,
`platform_user.created`, `platform_user.password_changed`,
`migrate_all.run`.

---

## 4. Tenant resolution, login and sessions

### 4.1 Store codes

Normalise: trim ASCII whitespace, upper-case, `-` -> `_`; pattern
`^[A-Z0-9][A-Z0-9_]{2,23}$` (M2 4.2). `RESERVED_CODES = {PLATFORM, MASTER,
ADMIN, ROOT, SYSTEM, API, APP, WWW, STATIC, DIRECTORY, SUPPORT, HELP,
STOREOPS, KLOUDC, TEST, NULL, NONE}` plus the value of `PLATFORM_LOGIN_CODE`.
`DEMO` is a real store code (M2 MT13).

### 4.2 Tokens (owner B)

- Store session token: `"<STORE_CODE>.<secret>"`, secret =
  `secrets.token_urlsafe(32)`. Platform session token:
  `"<PLATFORM_LOGIN_CODE>.<secret>"`.
- Stored **hashed**: `user_sessions.token` (store DB, existing column, no
  migration) and `platform_sessions.token_hash` hold `sha256(full token)`
  hex. Lookup hashes the presented token. (Existing raw-token rows simply stop
  matching: one forced re-login after deploy; no store is on the shared
  service yet.)
- Same delivery as today: web = httpOnly cookie `session_token`; app =
  `Authorization: Bearer <token>` from `/api/auth/mobile-login`.
- `single` mode: tokens are still prefixed with `STORE_CODE` (or `_` when
  `STORE_CODE` is blank, dev/tests); a token whose prefix is not this store
  is refused.

### 4.3 Per-request resolution (owner B: `app/tenancy/middleware.py`)

A pure ASGI middleware `TenantMiddleware` runs before routing:

1. Token from cookie `session_token` or `Authorization: Bearer`.
2. If a token is present: prefix = text before the first `.`.
   - prefix == `PLATFORM_LOGIN_CODE` -> **platform scope**
     (`current_scope() == "platform"`); no store context.
   - else look the prefix up in the registry. Unknown -> no store context
     (the route's auth dependency then answers 401). Store `hosting` is
     `dedicated` and this node does not serve it -> **409**
     `{"error":"store_moved","api_base_url":...}` for `/api/*` (12.3).
     Status not `active` -> no store context + `request.state.store_block =
     status` (auth dependency answers 403 `store_suspended` /
     `store_not_ready`).
   - else set the store context for the request.
   - The session row is verified **in that store's database** by
     `get_current_user` (hash lookup). A forged prefix gives a token that does
     not exist in that database -> 401. **The tenant is never taken from a
     client header alone.**
3. No token: if the route is a **public store route** (4.4) the explicit code
   (path parameter or `store` query parameter) sets the context (after
   normalisation + registry check; unknown/inactive -> 404
   `{"error":"unknown_store"}`).
4. `X-Store` header / `?store=` **with** a token: must equal the token's
   store, else **409** `{"error":"store_mismatch"}`. Never used to pick the
   store.
5. `get_db()` with no store context in `multi` mode raises
   `HTTPException(401, "Not logged in")` (keeps today's 401 for logged-out
   calls to store routes).

Context propagation: a `contextvars.ContextVar`. FastAPI/Starlette copy the
context into the threadpool for sync endpoints, sync dependencies and
`BackgroundTasks`; B proves this with tests. Raw threads must use
`spawn_in_store()` (6.6).

### 4.4 Public routes take the store explicitly

| Route | Multi-mode form |
|---|---|
| `POST /api/auth/login`, `/api/auth/mobile-login` | body `store_code` (required in multi) |
| `POST /api/auth/logout` | from the token |
| `POST /api/notifications/unregister` | body adds `store_code` (required in multi; app sends it) |
| `GET /api/app/branding` | session's store, else `?store=CODE`; neither -> product branding (store fields null) |
| `GET /api/app/config` | platform-level (min builds), `?store=CODE` adds the store's directory min build |
| `GET /api/app/legal` | `?store=CODE` optional (store AI notice) else product defaults |
| `/api/public/checklists/{token}...` (4 routes) | `?store=CODE` required in multi; links are `<PLATFORM_PUBLIC_URL>/c/<CODE>/<token>`; `/c/{path}` page accepts `/c/<CODE>/<token>` (and the old `/c/<token>` in single mode) |
| `GET /api/public/stores/{code}` (new, 13.4) | path |
| `POST /api/signup`, `GET /api/signup/*` (new) | no store context |
| `/branding/{name}`, `/.well-known/*`, `/api/health` | platform-level, no store |

`tests/test_review_route_gates.py`'s `PUBLIC` list gains only the new public
routes of 13.3/13.4; anything else stays behind login.

### 4.5 Login (owner C: `app/routers/auth.py`)

`POST /api/auth/login` (web, cookie) and `POST /api/auth/mobile-login` (token
in body), body:

```json
{"store_code": "XYZ_GROCERY", "username": "maria", "password": "..."}
```

Order of checks (same timing path for every failure; bcrypt is always run,
against a dummy hash when the user is unknown):
1. Normalise `store_code`. In `single` mode: blank or equal to `STORE_CODE`,
   else 401.
2. Lockout check (13.2) -> 429 `{"error":"locked","retry_after":N}`.
3. `store_code == PLATFORM_LOGIN_CODE` -> platform login against
   `platform_users` (+ TOTP when enrolled/required) -> platform session; reply
   `{"scope":"platform","user":{...}}`.
4. Registry lookup; unknown store, unknown user or wrong password -> **401
   "Incorrect store ID, username or password"** (one message, no
   enumeration).
5. Credentials good but store `dedicated` and not served here -> 409
   `store_moved` with `api_base_url`; `suspended` -> 403
   `{"error":"store_suspended","message":...}`; `pending`/`provisioning`/
   `failed` -> 403 `store_not_ready`; `archived` -> 401 (as unknown).
6. Session created in the store DB (`create_session` inside
   `tenant_session(code)`); reply = today's `_me_out(user)` plus
   `"scope":"store"`, `"store_code"`, `"api_base_url"` (null = this origin).
7. `login_attempts` row + audit `login.ok` / `login.failed`.

`GET /api/auth/me`: store scope -> today's body + `"scope":"store"`;
platform scope -> `{"scope":"platform","user":{"username","display_name","role"}}`.

---

## 5. Engine registry and `get_db` (owner B)

### 5.1 Store database URL

`STORE_DATABASE_URL_TEMPLATE`, e.g.
`postgresql+psycopg://storeops@127.0.0.1:5433/{db_name}`. A store with
`db_host_ref` uses `STORE_DB_HOSTS[db_host_ref]` (env JSON object
`{"ref": "postgresql+psycopg://user:pass@host:5432/{db_name}"}`) - dedicated
only (12). Passwords live only in env.

### 5.2 Registry behaviour

- One `Engine` per store database, created **lazily** on first use.
- Pool per engine: `pool_size=TENANT_POOL_SIZE` (2),
  `max_overflow=TENANT_MAX_OVERFLOW` (3), `pool_timeout=TENANT_POOL_TIMEOUT`
  (10 s), `pool_pre_ping=True`, `pool_recycle=1800`; connect args
  `options="-c timezone=UTC -c statement_timeout=<STATEMENT_TIMEOUT_MS>"`
  (default 60000; jobs may raise it per session).
- At most `TENANT_MAX_ENGINES` (40) open engines; least recently used engine
  with **no checked-out connections** is disposed when the limit is hit; an
  engine idle for `TENANT_ENGINE_IDLE_SECONDS` (600) is disposed by a
  housekeeping tick. Startup refuses a configuration where
  `TENANT_MAX_ENGINES * (pool_size + max_overflow) + platform pool + 10`
  exceeds the server's `max_connections` (checked once at startup).
- First open of a store engine checks the `store_identity` stamp: code ==
  registry code and `instance_id` == `stores.instance_id` (when set). Mismatch
  -> `StoreIdentityError`, engine not cached, audit
  `store.identity_mismatch`, request 503.
- SQLite engines (single mode, unit tests) keep today's pragmas and
  `check_same_thread=False`.
- `single` mode: exactly one engine from `DATABASE_URL` (today's behaviour);
  `app.db.engine` keeps pointing at it.

### 5.3 `app/db.py` after B

```python
Base                      # unchanged (store models)
UTCDateTime               # owner A (1.3)
engine                    # single mode: the engine; multi mode: None (use tenant_engine())
SessionLocal              # callable: Session for the CURRENT store (tenant-aware); class attr `kw` kept
def get_db() -> Generator[Session, None, None]   # current store's session; 401 without a store (multi)
def get_engine(store_code: Optional[str] = None) -> Engine
```

---

## 6. Jobs, locks, caches, threads (owner B)

### 6.1 Rules

- Every job runs inside `store_context(code)`; every cache entry, lock and
  file path is keyed by the store, unless listed below as **global** with a
  reason.
- `for_each_store()` runs a function once per active store (sequential by
  default), catches and logs per-store errors and never lets one store's
  failure stop the others.

### 6.2 Scheduler

- One scheduler set per deployment, started only by the **leader**: the
  process that holds `pg_try_advisory_lock(<platform DB>, 0x53544F5245)` on a
  dedicated platform connection (single mode: no lock, as today). Losing the
  connection stops the schedulers.
- **Cron jobs in store local time** (sales sync 04:00, insights 04:30,
  morning summary 09:00, closing checklist 21:30...): registered **once per
  store** with job id `"<job>:<CODE>"` and `CronTrigger(timezone=store tz)`,
  through `register_store_job(...)`. A deterministic per-store jitter
  (0-120 s, hash of the code) spreads POS calls.
- **Interval jobs** (every 10/15 min, open orders, invoice_failed,
  cart_removal...) are registered **once** and call `for_each_store(fn)`.
- `reconcile_store_jobs()` adds/removes per-store jobs; runs at start, every
  5 minutes, and when C calls `on_store_changed(code)` (approval, suspend,
  resume, timezone or POS change).
- Capability gating (`drop_unsupported_jobs`, M1 MT6) becomes per store:
  a store whose POS lacks a capability gets no job for it.
- Executor: thread pool `SCHEDULER_MAX_WORKERS` (4); `coalesce=True`,
  `max_instances=1` per job id, generous `misfire_grace_time` (as today per
  job).
- Job bookkeeping (`job_runs`) stays in each store's DB (unchanged).
- Platform jobs (C): prune `login_attempts`, provisioning retries (none
  automatic in v1), `migrate-all` is a command, not a job.

### 6.3 Threads and in-process senders

`flyer_sender` (one global loop thread, `services/flyer_sender.py:116`)
becomes one loop that, per wake-up, iterates stores with queued sends
(`for_each_store`), keeping today's per-store ordering.

### 6.4 The 45 module-level locks

**Global (kept, reason):** `team/media.py:77` and `services/flyers.py:144`
(`_PROCESSING_SLOTS`: CPU/memory budget of this process),
`services/flyers.py:603` (`_pdf_cap_lock`, process resource),
`services/push.py:30` (one Firebase app for the product),
`pos/registry.py:37` (guards the connector map; map is re-keyed, 9),
`team/changes.py:181` (guards the waiter list; versions per store, 6.7),
`services/insights_data.py:906` (`_live_locks_guard`, guards a dict that
becomes per store).

**Per store via `keyed_lock(name)`** (serialise work or writes for one store;
a single scheduler/web process per node keeps these correct):
`routers/orders.py:194`, `pos/sync/catalog.py:659,660`,
`pos/sync/accuracy.py:89`, `pos/sync/open_orders.py:340`,
`team/tasks_service.py:104`, `services/flyers.py:147,148`,
`services/schedule_suggestions.py:672`, `services/inventory_api_switch.py:42`,
`services/critical_dismissals.py:100`, `services/order_actions.py:64`,
`services/suggestion_dismissals.py:21`, `services/issue_leads.py:84,85`,
`services/inventory_api.py:453`, `services/purchase_order_log.py:43`,
`services/flyer_sender.py:76,85`, `services/po_send.py:86`,
`services/notifications.py:439`, `services/insights_data.py:953,1143`,
`services/po_cart.py:37` (RLock: `keyed_lock(name, reentrant=True)`),
`services/items_sold.py:66`, `store/pack.py:664`.
Where a lock protects a DB invariant across processes (`po_cart`,
`order_actions`, `critical_dismissals`, `purchase_order_log`, `po_send`,
flyer `_SEND_LOCK`), B additionally takes `advisory_lock(db, name)` inside the
transaction on PostgreSQL.

**Cache guards (move with their cache to `tenant_cache`, which has its own
per-store lock):** `pos/adapters/toast/analytics.py:84,90`,
`services/schedule_suggestions.py:216`, `services/stock_issues.py:98`,
`services/whatsapp_groups.py:311`, `services/checklist_schemas.py:234`,
`services/similar_products.py:854`, `services/inventory_api.py:862`,
`services/invoice_close_suggestions.py:216`, `services/report_cache.py:73`,
`services/insights_data.py:702`, `services/delivery_suggestions.py:708`.

### 6.5 Module-level caches and state

**Per store (`tenant_cache` / `tenant_state`):** `pos/registry.py:36`
(key `(store, provider)`), `pos/adapters/toast/client.py:33` (key
`(store, client_id)`), `pos/adapters/toast/analytics.py:88,89` (`_blocked_until`,
`_token`), `pos/sync/catalog.py:661`, `services/schedule_suggestions.py:217`,
`services/stock_issues.py:97`, `services/similar_products.py:853`,
`services/inventory_api.py:609,863`, `services/items_sold.py:65,70,71`,
`services/flyer_sender.py:82,87`, `services/invoice_close_suggestions.py:217`,
`services/report_cache.py:74,75`, `services/insights_data.py:703,905,907`,
`services/delivery_suggestions.py:709`, `services/checklist_schemas.py:233`,
`services/whatsapp_groups.py:312`, `store/pack.py:665`,
`team/recurrence.py:262,264,266`, `team/auto_tasks.py:108`.

**Engine-level hooks:** `services/report_cache.py:43,131-133` attaches
`after_cursor_execute`/`commit`/`rollback` listeners to the one `app.db.engine`;
`services/fix_issues.py:744` falls back to `from app.db import engine`. In multi
mode both use B's `register_engine_hook()` / `tenant_engine()` so every store
engine gets the listeners and the warm-up reads the current store.

**Global (kept):** code registries `team/changes.py:49,53`,
`team/media.py:98`, `team/jobs.py:22`; `services/push.py:29` (Firebase app);
`store/pack.py:598` (`generic.json`, product constant);
`similar_products.py:105` and `delivery_suggestions.py:159` `lru_cache`s (pure
functions of their arguments); the `scheduler.py:34-36` handles.

**Cache lint (B, `tests/tenancy/test_cache_lint.py`):** an AST scan of
`backend/app` fails on any new module-level mutable container,
`lru_cache`/`cache`, `threading.Lock/RLock/Semaphore/Event`, or `global`
statement that is not created through `app.tenancy` or listed in
`backend/app/tenancy/global_state_allowlist.txt` (`<path>:<name>  # reason`).

### 6.6 Threads

`spawn_in_store(fn, *args, name=...)` starts a daemon thread that runs `fn`
inside the caller's store context (`contextvars.copy_context().run`). The 5
raw-thread sites (1.5) use it.

### 6.7 Long-poll

`team/changes.py` keeps one waiter list but the version becomes **per store**
(`change_version()` / `notify_changed()` / `wait_for_change()` read the store
from the context; `after_commit` reads it from `session.info["store_code"]`,
which the tenant `SessionLocal` sets). A wake-up for store A never wakes B's
waiters. Cross-process wake-ups (PG `LISTEN/NOTIFY`) are deferred; with one
web process per node nothing changes for clients (they already re-poll every
20 s).

---

## 7. Files (owner B)

- Store files live under `<UPLOADS_ROOT>/<STORE_CODE>/`:
  `team/`, `invoices/`, `flyers/`, `.keys/` (per-store `insights_hmac.key`,
  mode 0600), plus the M2 marker `.store_identity`.
- The settings overlay (8.1) sets `uploads_root`, `team_uploads_dir`,
  `invoice_uploads_dir`, `flyers_dir`, `insights_hmac_key_file` per store, so
  the call sites in 1.5 do not change.
- `store_path(*parts)` resolves under the current store's root and refuses
  any path that escapes it (`..`, absolute parts, symlinks out).
- Object storage (R2, presigned URLs) is deferred (MT29); the `store_path`
  seam is where it plugs in.

---

## 8. Settings and secrets

### 8.1 Precedence (owner B: `app/config.py`, `app/tenancy/settings.py`)

`multi` mode, inside a store context, `get_settings()` returns a per-store
`Settings` object built as:

```
env / .env (process defaults)            lowest
  < platform store_settings (whitelisted keys only, 8.2)
  < store_credentials (decrypted secret + public fields, mapped to Settings fields, 9.2)
  < derived per-store values (store_code, uploads paths, public_host = PLATFORM_PUBLIC_URL)   highest
```

- Platform-only fields (`PLATFORM_ONLY_KEYS`: database and tenancy settings,
  `cors_origins`, `platform_domain`, `product_name`, `company_name`,
  `app_bundle_id`, `apple_team_id`, `android_cert_sha256`,
  `firebase_credentials_file`, `scheduler_enabled`, `notifications_enabled`,
  `team_jobs_enabled`, `session_*`, secret-key settings, `directory_*`,
  `frontend_dist_dir`) can never be overridden per store.
- The object is cached per `(store_code, settings_version)`; the version is
  re-read from `stores` at most every `SETTINGS_REFRESH_SECONDS` (10) per
  store; `invalidate_store_settings(code)` drops it at once (C calls it after
  writes in this process).
- No store context (multi mode) -> the process `Settings` (platform level).
  `single` mode -> today's behaviour exactly.
- Tests keep working: `monkeypatch.setattr(get_settings(), ...)` patches the
  cached object of the test's store.

### 8.2 `STORE_SETTING_KEYS` (whitelist, defined by B in `app/tenancy/settings.py`)

`store_name, store_short_name, store_address, store_phone, store_email,
store_description, logo_url, icon_url, brand_color, brand_secondary_color,
store_timezone, currency, store_locale, demo_mode, business_day_cutoff_hour,
first_sales_date, pos_provider, ap_inbox_email, labels_url, ai_enabled,
ai_consent_notice, email_from_name, po_whatsapp_enabled,
sales_sync_hour, sales_sync_minute, sales_sync_backfill_days,
sales_repull_enabled, sales_repull_days, sales_repull_times,
insights_nightly_enabled, insights_nightly_hour, insights_nightly_minute,
inventory_api_snapshots, inventory_api_snapshot_hour,
inventory_api_snapshot_minute, open_orders_full_scan_hour,
open_orders_full_scan_minute, open_orders_today_refresh_minutes,
projection_method, team_* (feature switches and autotask settings in
config.py), min_supported_build_ios, min_supported_build_android,
loss_watch_*, odd_check_*`. Values are validated with the same pydantic
validators as env values (a bad value is refused at write time, 400).

### 8.3 Credentials -> Settings fields (owner C: `app/platform/credentials.py`)

| Kind | Public fields (returned) | Secret fields (never returned) | Settings fields filled |
|---|---|---|---|
| `toast` | `client_id`, `restaurant_guid`, `api_base` (default `https://ws-api.toasttab.com`), `analytics_client_id` | `client_secret`, `analytics_client_secret` | `toast_client_id`, `toast_restaurant_guid`, `toast_api_base`, `toast_analytics_client_id`, `toast_client_secret`, `toast_analytics_client_secret` |
| `clover` | `merchant_id`, `region` (`us`/`eu`) | `access_token`, `refresh_token` | none until M3 (placeholder; 9.3) |
| `whatsapp` | `base_url` (default `https://gate.whapi.cloud`), `group_chat_ids` (JSON object) | `api_token` | `whapi_base_url`, `whatsapp_group_chat_ids`, `whapi_api_token` |
| `smtp` | `host`, `port`, `ssl`, `username`, `from_name` | `password` | `smtp_host`, `smtp_port`, `smtp_ssl`, `smtp_username`, `email_from_name`, `smtp_password` |
| `ai` | - | `api_key` | `anthropic_api_key` |

`last4` = last 4 characters of `client_secret` / `access_token` /
`api_token` / `password` / `api_key`.

```python
# app/platform/credentials.py (C)
KINDS: Dict[str, CredentialSpec]
def save_credential(code: str, kind: str, public: dict, secrets: dict, *, actor: str,
                    clear: Sequence[str] = ()) -> CredentialView   # blank secret = keep existing
def credential_view(code: str, kind: str) -> CredentialView        # no secrets, has last4/status
def list_credentials(code: str) -> List[CredentialView]
def delete_credential(code: str, kind: str, *, actor: str) -> None
def settings_overrides(code: str) -> Dict[str, Any]                # B calls this; decrypted, Settings field names
def decrypt_secrets(code: str, kind: str) -> Dict[str, str]        # internal; never logged
```

### 8.4 Encryption (owner C: `app/platform/crypto.py`)

- AES-256-GCM (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`),
  12-byte random nonce per write, **associated data**
  `f"storeops:{store_code}:{kind}:v1"` - a row copied to another store does
  not decrypt (decrypt failure -> treated as not configured, audit
  `credential.decrypt_failed`).
- Key ring from env `STOREOPS_SECRET_KEYS="k2:<base64 32 bytes>,k1:<...>"`;
  the first key encrypts, any listed key decrypts (by `key_id`). Legacy single
  key `STOREOPS_SECRET_KEY` = ring `{"k1": ...}`.
- Dev only (`APP_ENV=development`): when no ring is set, generate one into
  `backend/.secrets/storeops_keyring` (mode 0600, gitignored by A's
  `.gitignore` line `backend/.secrets/`). Production with no ring -> startup
  refuses (`multi` mode).
- `python -m app.platform rotate-keys` re-encrypts every row with the active
  key; `python -m app.platform gen-key` prints a new key.
- `totp_secret_enc` on platform users uses the same scheme with AAD
  `storeops:platform:totp:<username>`.

---

## 9. Connectors and integrations

### 9.1 Connector registry (owner B: `app/pos/registry.py`)

`get_connector()` keeps its signature; the provider comes from the current
store's `pos_provider`; the instance cache is keyed `(store_code, provider)`.
Connectors read credentials from `get_settings()` at call time (already true),
so the overlay supplies each store's own credentials.

### 9.2 "Test connection" (owner C: `app/platform/connector_tests.py`)

```python
TestResult = NamedTuple("TestResult", [("ok", bool), ("message", str)])
TESTERS: Dict[str, Callable[[dict, dict], TestResult]]   # kind -> fn(public, secrets)
```

- `toast`: fetch an auth token with the given client id/secret, then one
  read for the restaurant GUID (connector's existing client code).
- `whatsapp`: Whapi `GET /settings` (or the cheapest authenticated read).
- `smtp`: connect + login, no message sent.
- `ai`: one cheapest authenticated metadata call (no generation).
- `clover`: not testable until M3 -> `TestResult(False, "Clover connection arrives with M3")`.
- Network timeouts 10 s; messages are plain English and never echo secrets.
- Tests replace `TESTERS[kind]` with fakes; a test asserts no socket is
  opened (M2 conftest guard stays on).

### 9.3 Clover "Connect"

`POST /api/settings/integrations/clover/connect` -> `501
{"error":"not_available","message":"Clover connection arrives with M3"}`.
M3 replaces it with the OAuth v2 redirect (`authorize_url` in the response) and
`GET /api/settings/integrations/clover/callback`.

---

## 10. Provisioning (owner C: `app/platform/provisioning.py`)

`approve_signup(request_id, *, actor, overrides)` creates the `stores` row
(`pending`) and starts `provision_store(code)` in a background thread
(`spawn`, no store context); the API returns **202**
`{"store_code", "status": "provisioning"}`. Steps, each idempotent, each
recorded in `stores.provisioning_step`; a failure sets `status='failed'`,
`status_reason`, audit `store.provision_failed`; **Retry** resumes at the
failed step:

1. `reserve` - status `provisioning`; `db_name` computed; refuse if the code
   or `db_name` is taken by another store.
2. `create_database` - `CREATE DATABASE "<db_name>" TEMPLATE template0
   ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'` via `PG_ADMIN_URL`
   (autocommit). If it exists: continue only if it is empty or stamped with
   this code (never adopt another store's data).
3. `migrate` - `app.migrate.upgrade_store_db(url)` (A) to head.
4. `store_init` - `app.store.init_store(db, code, pack_path)` (C refactors
   `app/store/__main__.py` init into this function; CLI unchanged) with the
   pack `pack_id` (`backend/packs/<pack_id>.json`, or the private packs dir).
   Stamps identity; `stores.instance_id` saved.
5. `owner_admin` - create the owner user (`is_admin=True`) with the
   bcrypt hash from the sign-up (never a new plaintext).
6. `settings` - initial `store_settings`: `store_name`, `store_timezone`,
   `pos_provider`, `store_address`, `store_phone`, `store_email`.
7. `files` - create `<UPLOADS_ROOT>/<CODE>/` and its marker.
8. `activate` - status `active`, `approved_*` set, `on_store_changed(code)`
   (scheduler), audit `store.provisioned`.

Never automatic: dropping a database. `python -m app.platform purge-store CODE
--confirm CODE` (only `failed` or `archived` stores; master only) drops it.

Commands (`python -m app.platform ...`, C): `create-master --username U`
(password from prompt or `STOREOPS_MASTER_PASSWORD`), `provision --code C
--name N --pack P --tz Z --pos toast --owner-username U` (manual store,
prompts for owner password), `list-stores`, `migrate-all`, `rotate-keys`,
`gen-key`, `purge-store`, `suspend CODE --reason`, `resume CODE`.

---

## 11. Migrations

### 11.1 Store databases (owner A)

- Script location stays `backend/alembic/` (one store schema).
- **PostgreSQL baseline**: a frozen file `backend/alembic/pg_baseline/0062.sql`
  (DDL generated once from the SQLite schema at `0062`, mapped to PG types,
  reviewed, committed) plus `pg_baseline/0062_seed.py` (the rows migrations
  0026-0062 seed that a new store keeps: e.g. the 0026 teams; the ones
  `store-init` deletes are not seeded). `env.py` on PostgreSQL: if the
  database has no `alembic_version` and no tables -> run the baseline + seed,
  `stamp 0062`, then continue to `head`. Running any revision `<= 0062` on PG
  otherwise raises a clear error ("PostgreSQL store databases start from the
  0062 baseline"). Downgrade below `0062` on PG is refused.
- **Every new migration (0063+) must run on both SQLite and PostgreSQL**;
  CI runs the chain on both. `batch_alter_table` is allowed (no-op wrapper on
  PG), `recreate="always"` is not.
- `env.py` takes the URL from `-x db_url=...` or `-x store=CODE` (resolved
  through the registry, multi mode) else `DATABASE_URL` (single mode).
- Python API (A, `app/migrate.py`):

```python
def upgrade_store_db(url: str, revision: str = "head") -> str      # returns the revision now applied
def current_store_revision(url: str) -> Optional[str]
def store_head_revision() -> str
def downgrade_store_db(url: str, revision: str) -> str             # (A, as built) never below 0062 on PG
class BaselineError(RuntimeError)                                  # (A, as built)
```

- Schema parity test (A): SQLite chain at head vs PG baseline+chain at head ->
  same tables, columns, nullability, type family, PK, unique constraints,
  indexes, CHECK constraints, normalised server defaults. **As built: no FK
  constraints on PostgreSQL** (SQLite never enforced them; "As built - A");
  `compare_metadata` against `Base.metadata` on PG finds exactly what it finds
  on the SQLite chain (5 pre-existing index/unique drifts, FKs ignored).

### 11.2 Platform database (owner C)

- Separate env `backend/alembic_platform/` (`env.py`, `versions/p0001_*.py`),
  separate metadata (`app.platform.models.PlatformBase`), its own
  `alembic_version` table in `so_platform`. `alembic.ini` gains a `[platform]`
  section: `alembic -n platform upgrade head`.
- PG only (the platform DB is never SQLite, except a SQLite smoke test of the
  models if cheap).
- API: `app.platform.db.upgrade_platform_db(url=None) -> str`.

### 11.3 `migrate-all` (owner C)

`python -m app.platform migrate-all [--store CODE] [--dry-run] [--keep-going]`:
1. upgrade the platform DB;
2. for each store with status `active`, `suspended`, `provisioning`, `failed`
   on this cluster (`hosting=shared`), `upgrade_store_db`, record
   `stores.schema_revision`; dedicated stores are listed as "migrate on their
   own server";
3. stop at the first failure unless `--keep-going`; exit non-zero on any
   failure; audit `migrate_all.run` with per-store results.
New store databases are always created with `LC_COLLATE 'C'`/`LC_CTYPE 'C'`
(1.4).

### 11.4 Data move (owner A)

`scripts/pg/copy_sqlite_to_pg.py --sqlite PATH --pg-url URL` (target at the
same head; table-by-table copy in FK order; sequences reset to max(id); row
counts and per-table checksums compared; refuses a non-empty target) and
`scripts/pg/verify_copy.py`. Used for moving an M2 single-instance store onto
the platform (plan 8.2 cutover); rehearsed on fake-store data only in this
milestone.

---

## 12. Dedicated hosting (owners C, B)

### 12.1 Rules

1. A store moves to its own server **only** after a master approves a
   `dedicated_hosting_requests` row. Store admins may request; masters may
   request and approve.
2. All of it sits behind `DEDICATED_HOSTING_ENABLED` (default **false**).
   Off: request/approve/complete endpoints answer 409
   `{"error":"dedicated_hosting_disabled"}`; registry rows can never become
   `dedicated`.
3. The dedicated server runs the **same image** with `TENANCY_MODE=multi`,
   `DEDICATED_STORE_CODES=<CODE>`, its own Postgres (`STORE_DB_HOSTS`), read
   access to the platform DB (registry, settings, credentials; a restricted
   role) and the same key ring. It refuses any other store (409
   `store_moved` with the platform URL).
4. The move follows `docs/DEDICATED_HOSTING_RUNBOOK.md` (C): approve ->
   prepare server -> `pg_dump`/`pg_restore` the store DB -> 15-minute
   read-only window at 03:00 store time -> `complete` flips `hosting`,
   `api_base_url`, `db_host_ref` -> verify -> rollback (flip back) within 7
   days if needed.

### 12.2 Code path (B: `app/tenancy/routing.py`; C: API + runbook)

```python
def node_serves(store: StoreRef) -> bool        # shared store on a shared node, or listed in DEDICATED_STORE_CODES
def store_moved_response(store: StoreRef) -> JSONResponse   # 409 store_moved
```

The `complete` step and the routing branch are written, tested with the
flag on in tests, and wrapped in a clearly marked block:

```python
# --- DEDICATED HOSTING (owner approval required; DEDICATED_HOSTING_ENABLED) ---
# See docs/DEDICATED_HOSTING_RUNBOOK.md. Off by default.
```

### 12.3 Clients

- Store lookup (13.4) returns the store's own `api_base_url`.
- Website: a `store_moved` login reply sends the browser to
  `<api_base_url>/#/login?store=<CODE>`.
- App: the lookup already gives the right `api_base_url`; a `store_moved`
  reply refreshes the lookup and retries once.

---

## 13. API

All JSON errors: `{"error": "<code>", "message": "<plain English>"}` for new
routes (FastAPI `detail` stays on old routes).

### 13.1 Platform (master only; platform session; owner C; prefix `/api/platform`)

| Method | Path | Body / result |
|---|---|---|
| GET | `/signups?status=pending` | list |
| GET | `/signups/{id}` | detail (no password hash) |
| POST | `/signups/{id}/approve` | `{"code"?: override, "pack_id"?, "timezone"?, "note"?}` -> 202 `{"store_code","status":"provisioning"}` |
| POST | `/signups/{id}/reject` | `{"note"}` |
| GET | `/stores?status=` | list (code, name, status, hosting, pos, timezone, created/approved, schema_revision) |
| GET | `/stores/{code}` | detail + provisioning step + credential kinds configured (no secrets) |
| POST | `/stores/{code}/retry-provisioning` | 202 |
| POST | `/stores/{code}/suspend` | `{"reason"}` |
| POST | `/stores/{code}/resume` | |
| POST | `/stores/{code}/archive` | `{"confirm": "<CODE>"}` |
| GET / PUT | `/stores/{code}/settings` | whitelisted `store_settings` (8.2) |
| GET | `/hosting-requests?status=` | list |
| POST | `/stores/{code}/hosting-requests` | `{"reason","target_api_base_url"?}` |
| POST | `/hosting-requests/{id}/approve` / `/reject` / `/complete` / `/rollback` | guarded by the flag (12.1) |
| GET | `/audit?store=&action=&before=&limit=` | newest first, max 200 |
| GET | `/packs` | available pack ids |
| GET / POST | `/users` | masters (create: username, password, display name) |
| POST | `/users/me/password` | `{"current","new"}` |

A store session on any `/api/platform/*` route -> 403; no session -> 401.

### 13.2 Login and lockout

Section 4.5. Lockout: 5 failures for `(store_code, username)` in 15 minutes
-> locked 15 minutes; 30 failures per client IP in 15 minutes -> locked 15
minutes (`LOGIN_LOCKOUT_FAILURES`, `LOGIN_LOCKOUT_MINUTES`,
`LOGIN_IP_LOCKOUT_FAILURES`). Platform logins use the same table with
`store_code = PLATFORM_LOGIN_CODE`. Client IP from
`DIRECTORY_CLIENT_IP_HEADER` when set, else the socket peer.

### 13.3 Sign-up (public; owner C)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/signup` | body: `store_name, requested_code, contact_name, contact_email, contact_phone?, address?, timezone, pos_provider, owner_username, owner_password (>= 10 chars), notes?, accept_terms: true, website` (honeypot, must be empty). -> 202 `{"request_id", "status_token", "status":"pending"}`. Rate limit `SIGNUP_RATE_PER_HOUR` (5) per IP and 50/hour overall; `SIGNUP_ENABLED=false` -> 404. |
| GET | `/api/signup/code-available?code=` | `{"code","available":bool,"reason"?}`; same rate limiter as store lookup |
| GET | `/api/signup/status?request_id=&token=` | `{"status","store_code"?,"message"?}`; wrong token -> 404 |

### 13.4 Store lookup (public; owner C)

`GET /api/public/stores/{code}`: the **same JSON and error shapes as the M2
directory** (`docs/M2_CONTRACT.md` 4.2: `schema`, `code`, `name`,
`api_base_url`, `status` `active|maintenance`, `message`, `branding`,
`min_build`, `updated_at`), built from `stores` + `store_settings`.
`api_base_url` = the store's own (dedicated) or `PLATFORM_PUBLIC_URL`.
`suspended` -> `status:"maintenance"` with `maintenance_message`; `pending`,
`provisioning`, `failed`, `archived`, unknown -> 404 `unknown_store`. Same
rate limits and cache headers as the directory.

### 13.5 Integrations (store admin; owner C; prefix `/api/settings/integrations`)

| Method | Path | Notes |
|---|---|---|
| GET | `` | `[{"kind","label","configured","status","last4","public","updated_at","updated_by","last_tested_at","last_test_message"}]` for all 5 kinds |
| PUT | `/{kind}` | `{"public": {...}, "secrets": {...}, "clear": [...]}`; blank/missing secret = keep; returns the view |
| POST | `/{kind}/test` | tests the stored values, or unsaved values in the body (same shape as PUT); records status; 5 per minute per store |
| DELETE | `/{kind}` | |
| POST | `/clover/connect` | 501 until M3 (9.3) |

`require_admin`; the DEMO store and demo users -> 403 (`demo_guard`); `single`
mode -> 409 `{"error":"platform_mode_required"}` (credentials come from
`.env` there). Responses are scanned in tests: no secret value ever appears.

Optional (C "should"): `GET/PATCH /api/settings/store` for the whitelisted
non-secret store settings (name, address, phone, colours, AI switch,
labels URL), and `POST /api/settings/hosting/request` (store admin asks for a
dedicated server).

---

## 14. Website (owner D)

Hash routes (the site uses a hash router):

| Route | Who | What |
|---|---|---|
| `/#/login` | everyone | **Store ID**, Username, Password, "Sign in". Remembers the last Store ID (localStorage `storeops_last_store`, try/catch). Accepts `?store=CODE` prefill. Link "New store? Request access". Store ID `PLATFORM` -> platform login -> console. Error texts map 401/403/409/429 (incorrect, suspended / not ready, moved -> redirect, locked + wait). Product branding before login; optional store logo after a successful `GET /api/public/stores/{code}` on Store ID blur. |
| `/#/signup` | public | the 13.3 form, live code availability, password rules, terms checkbox, hidden honeypot; success -> `/#/signup/pending?id=&t=` |
| `/#/signup/pending` | public | status (polls every 30 s while visible, max 1 hour): pending / approved ("Sign in with Store ID X") / rejected (+ note) |
| `/#/platform` | master | one screen with tabs (tile-like, remembers the last tab): **Requests** (approve with pack/timezone/code override, reject with note), **Stores** (status chips, suspend/resume, retry provisioning, settings, hosting), **Hosting** (requests; disabled state when the flag is off), **Audit** (filter by store/action). Slim frozen header (search + filter + info icon). |
| `/#/settings/integrations` | store admin | cards: Toast, Clover, WhatsApp, Email (SMTP), AI. Status chip, "saved **** 1234", write-only secret inputs, Save, **Test connection**, Remove. Clover card: "Connect" -> "Coming with M3". |
| `/#/notifications` | - | **removed**: `<Navigate to="/" replace />`; sidebar/feature entry (`featureRoutes.js:177`), Help text and `NotificationSettingsPage.*` removed; `components/notifications/*` removed if unused elsewhere. The API stays. |

All existing pages keep working for store sessions. Platform sessions see only
`/#/platform` (anything else redirects there). Phone width 375 and desktop
1280 both pass.

## 15. App (owner D, repo `storeops-app`)

- **One login screen**: Store ID, Username, Password, Sign in (+ biometric
  when enabled for that store; + QR scan icon that fills Store ID; + quick-pick
  chips of known stores). The separate store-code screen is removed.
- On Sign in: normalise code -> resolve (`GET <PLATFORM_API_URL>/api/public/stores/<CODE>`,
  fallback to the M2 directory and static fallback, then the saved record) ->
  `api_base_url` host policy (M2 5.2) -> `POST <api_base_url>/api/auth/mobile-login`
  `{"store_code","username","password"}` -> on 200 activate the tenant
  (branding fetch, theme), save `session_token.<CODE>`. Error mapping:
  401 / 403 `store_suspended` / `store_not_ready` / 409 `store_moved`
  (re-resolve once) / 429.
- New define `PLATFORM_API_URL` (default `https://app.<PLATFORM_DOMAIN>`);
  `DIRECTORY_URL` stays as the fallback for M2 single-instance stores.
- `POST /api/notifications/unregister` sends `store_code`.
- Switch store / sign out -> the same single login screen (Store ID prefilled
  with the last store; clear button).
- Store-scoped storage keys (M2 5.4) unchanged.

---

## 16. Interfaces B exposes (A, C, D code against these before B lands)

B's **first commit** on `platform/b-tenancy` is this package with the exact
signatures and `single`-mode behaviour (no behaviour change), pushed within
the first working block; A and C may cherry-pick it.

```python
# app/tenancy/__init__.py  - re-exports everything below
from app.tenancy.errors import NoStoreContext, UnknownStore, StoreNotActive, StoreMoved

# --- mode -------------------------------------------------------------------
def is_multi_tenant() -> bool                              # TENANCY_MODE == "multi"

# --- registry (app/tenancy/registry.py) --------------------------------------
@dataclass(frozen=True)
class StoreRef:
    code: str                    # upper case
    name: str
    status: str                  # pending|provisioning|active|suspended|failed|archived
    hosting: str                 # shared|dedicated
    api_base_url: Optional[str]
    db_name: str
    db_host_ref: Optional[str]
    timezone: str
    pos_provider: str
    instance_id: Optional[str]
    settings_version: int

def get_store(code: str, *, refresh: bool = False) -> StoreRef      # raises UnknownStore; cached 10 s
def list_stores(statuses: Iterable[str] = ("active",), hosting: Optional[str] = "shared") -> List[StoreRef]
def set_registry_source(source: "RegistrySource") -> None          # tests; C's platform source in multi mode
class RegistrySource(Protocol):
    def load(self, code: str) -> Optional[StoreRef]: ...
    def list(self, statuses: Iterable[str]) -> List[StoreRef]: ...
    def settings_version(self, code: str) -> int: ...
# single mode source: one StoreRef from STORE_CODE / DATABASE_URL / store_timezone / pos_provider.

# --- context (app/tenancy/context.py) ---------------------------------------
def current_store() -> Optional[StoreRef]
def current_store_code() -> Optional[str]
def require_store() -> StoreRef                                     # raises NoStoreContext
def current_scope() -> str                                          # "store" | "platform" | "none"
@contextmanager
def store_context(code: str, *, allow_inactive: bool = False) -> Iterator[StoreRef]
def run_in_store(code: str, fn: Callable[..., T], *args, **kwargs) -> T
def spawn_in_store(fn: Callable[..., Any], *args, name: str = "", **kwargs) -> threading.Thread  # uses the CURRENT context

# --- engines and sessions (app/tenancy/engines.py) ---------------------------
def tenant_engine(code: Optional[str] = None) -> Engine            # None = current store
@contextmanager
def tenant_session(code: Optional[str] = None) -> Iterator[Session]  # sets the context too; closes; no auto-commit
def store_database_url(store: StoreRef) -> str
def dispose_engine(code: str) -> None
def engine_stats() -> Dict[str, dict]                               # code -> {"checked_out","pool_size","last_used"}
def register_engine_hook(fn: Callable[[str, Engine], None]) -> None  # called for every engine created (and the single-mode engine); e.g. report_cache listeners

# --- jobs (app/tenancy/jobs.py) ----------------------------------------------
def for_each_store(fn: Callable[[StoreRef], T], *, statuses=("active",), hosting: Optional[str] = "shared",
                   stop_on_error: bool = False, max_workers: int = 1) -> Dict[str, Union[T, BaseException]]
def register_store_job(job_id: str, func: Callable[[], None], trigger: Callable[[StoreRef], BaseTrigger], *,
                       capability: Optional[str] = None, scheduler: str = "main",
                       misfire_grace_time: Optional[int] = None) -> None   # func runs inside store_context
def reconcile_store_jobs() -> None
def on_store_changed(code: str) -> None                             # C calls after approve/suspend/resume/settings change

# --- locks (app/tenancy/locks.py) --------------------------------------------
def keyed_lock(name: str, *, store: Optional[str] = None, reentrant: bool = False) -> "threading.Lock"
@contextmanager
def advisory_lock(db: Session, name: str) -> Iterator[None]         # PG: pg_advisory_xact_lock; SQLite: keyed_lock

# --- caches (app/tenancy/cache.py) --------------------------------------------
class TenantCache:
    def get(self, key: Hashable, default: Any = None) -> Any
    def set(self, key: Hashable, value: Any) -> None
    def pop(self, key: Hashable, default: Any = None) -> Any
    def get_or_set(self, key: Hashable, factory: Callable[[], Any]) -> Any
    def clear(self) -> None                       # current store only
    def clear_all_stores(self) -> None
    @property
    def lock(self) -> "threading.RLock": ...      # the current store's lock for this cache
def tenant_cache(name: str, *, maxsize: int = 256, ttl: Optional[float] = None) -> TenantCache   # same name -> same object
def tenant_state(name: str, factory: Callable[[], T]) -> T          # per-store mutable object (e.g. the old `_state` dicts)
def clear_tenant_caches(code: Optional[str] = None) -> None         # tests, store suspend

# --- files (app/tenancy/paths.py) --------------------------------------------
def store_root(code: Optional[str] = None) -> pathlib.Path          # <UPLOADS_ROOT>/<CODE> (single mode: UPLOADS_ROOT)
def store_path(*parts: str, code: Optional[str] = None, create: bool = False) -> pathlib.Path  # refuses escapes

# --- settings (app/tenancy/settings.py) ---------------------------------------
STORE_SETTING_KEYS: FrozenSet[str]
PLATFORM_ONLY_KEYS: FrozenSet[str]
def store_settings_for(code: str) -> "Settings"                     # what get_settings() returns inside that store
def invalidate_store_settings(code: Optional[str] = None) -> None
def set_settings_source(source: "SettingsSource") -> None           # C's platform source (store_settings + credentials)
class SettingsSource(Protocol):
    def store_settings(self, code: str) -> Dict[str, Any]: ...
    def credential_overrides(self, code: str) -> Dict[str, Any]: ...

# --- routing (app/tenancy/routing.py) ------------------------------------------
def node_serves(store: StoreRef) -> bool
def store_moved_response(store: StoreRef) -> "JSONResponse"

# --- auth helpers (app/auth.py, B) -----------------------------------------------
def make_token(scope_code: str) -> str                              # "<CODE>.<secret>"
def token_hash(token: str) -> str                                   # sha256 hex
def token_scope_code(token: str) -> Optional[str]                   # prefix or None
def create_session(db: Session, user_id: int) -> Tuple[str, dt.datetime]   # unchanged signature; store-bound token
# get_current_user / require_admin unchanged signatures

# --- testing (app/tenancy/testing.py) -------------------------------------------
def make_test_store(code: str, url: str, **fields) -> StoreRef      # registers in an in-memory source
@contextmanager
def as_store(code: str) -> Iterator[None]                            # test helper = store_context
```

Additions made by B in the interface commit (additive; nothing above changed):

```python
# app/tenancy/errors.py: StoreIdentityError (app.store.identity re-exports it), StoreUnavailable (-> 503)
# registry: normalize_store_code(raw), is_valid_store_code(code), RESERVED_CODES, find_store(code) -> Optional,
#           invalidate_store(code=None), single_store_ref()
# context:  current_store_block(), platform_scope(), no_store(); store_context(..., allow_remote=False)
#           (StoreMoved for a dedicated store this node does not serve)
# engines:  tenant_session(code=None, *, allow_inactive=False, allow_remote=False); sweep_idle_engines();
#           create_store_engine(url, tenant=True); check_connection_budget()
# jobs:     register_store_job(..., coalesce=True, max_instances=1, jitter=True) - trigger(store) may return None;
#           register_store_startup_job(job_id, func, delay_seconds); each_store_job(func) (interval jobs);
#           store_jitter_seconds(code)
# locks:    tenant_lock(name, reentrant=False) -> TenantLock: a MODULE-LEVEL drop-in for threading.Lock()
#           whose real lock is keyed_lock(name) of the store current at acquire time
# cache:    tenant_local(name, factory) -> a module-level proxy (dict/list/set...) whose object is per store
# settings: validate_store_setting(key, value) (C's PUT validation), CREDENTIAL_SETTING_KEYS
# tokens:   app/tenancy/tokens.py holds make_token / token_hash / token_scope_code (app.auth re-exports)
# events:   app/tenancy/events.py add_listener(fn(action, store_code, details)) - C writes audit rows
#           (e.g. "store.identity_mismatch")
# middleware: register_public_store_route(prefix) - logged-out routes that take ?store=CODE
# db:       get_optional_db() - like get_db but yields None (no 401) without a store (app-wide deps)
```

C provides the platform implementations of `RegistrySource` and
`SettingsSource` (`app/platform/sources.py`) and calls
`set_registry_source` / `set_settings_source` in the lifespan when
`TENANCY_MODE=multi`. A provides `app/dbcompat.py`:

```python
def dialect_insert(table, bind_or_session) -> Insert      # sqlite or postgresql insert with on_conflict_* support
def is_postgres(bind_or_session) -> bool
def is_sqlite(bind_or_session) -> bool                    # (A, as built)
def sequence_last_value(db: Session, table: str, column: str = "id") -> Optional[int]
class fsum(FunctionElement)                               # (A, as built) float SUM that adds up like SQLite's
def group_order(*keys) -> list                            # (A, as built) ORDER BY = SQLite's GROUP BY output order
```

---

## 17. New settings fields (owner B adds all of them to `app/config.py` and `.env.example`)

| Field | Env | Default | Used by |
|---|---|---|---|
| `tenancy_mode` | `TENANCY_MODE` | `"single"` | all |
| `app_env` | `APP_ENV` | `"production"` | dev key ring (8.4) |
| `platform_database_url` | `PLATFORM_DATABASE_URL` | `""` (required in multi) | B, C |
| `store_database_url_template` | `STORE_DATABASE_URL_TEMPLATE` | `""` (required in multi; contains `{db_name}`) | B |
| `store_db_hosts` | `STORE_DB_HOSTS` | `""` (JSON object) | B (dedicated) |
| `store_db_prefix` | `STORE_DB_PREFIX` | `"so_"` | C |
| `pg_admin_url` | `PG_ADMIN_URL` | `""` -> template with `db_name=postgres` | C (CREATE DATABASE) |
| `tenant_pool_size` / `tenant_max_overflow` / `tenant_pool_timeout` | `TENANT_POOL_SIZE` / `TENANT_MAX_OVERFLOW` / `TENANT_POOL_TIMEOUT` | `2` / `3` / `10` | B |
| `tenant_max_engines` | `TENANT_MAX_ENGINES` | `40` | B |
| `tenant_engine_idle_seconds` | `TENANT_ENGINE_IDLE_SECONDS` | `600` | B |
| `statement_timeout_ms` | `STATEMENT_TIMEOUT_MS` | `60000` | B |
| `settings_refresh_seconds` | `SETTINGS_REFRESH_SECONDS` | `10` | B |
| `scheduler_max_workers` | `SCHEDULER_MAX_WORKERS` | `4` | B |
| `platform_public_url` | `PLATFORM_PUBLIC_URL` | `""` -> `https://app.<platform_domain>` (refused under `spicetown.shop`) | B, C, D |
| `platform_login_code` | `PLATFORM_LOGIN_CODE` | `"PLATFORM"` | B, C, D |
| `storeops_secret_keys` | `STOREOPS_SECRET_KEYS` | `""` (secret) | C |
| `storeops_secret_key` | `STOREOPS_SECRET_KEY` | `""` (secret; legacy single key) | C |
| `storeops_secret_key_file` | `STOREOPS_SECRET_KEY_FILE` | `".secrets/storeops_keyring"` (dev only) | C |
| `signup_enabled` | `SIGNUP_ENABLED` | `True` | C |
| `signup_rate_per_hour` | `SIGNUP_RATE_PER_HOUR` | `5` | C |
| `login_lockout_failures` / `login_lockout_minutes` / `login_ip_lockout_failures` | ... | `5` / `15` / `30` | C |
| `platform_require_totp` | `PLATFORM_REQUIRE_TOTP` | `False` | C |
| `dedicated_hosting_enabled` | `DEDICATED_HOSTING_ENABLED` | `False` | B, C |
| `dedicated_store_codes` | `DEDICATED_STORE_CODES` | `""` (comma list) | B |
| `private_packs_dir` | `PRIVATE_PACKS_DIR` | `""` | C (pack lookup) |

---

## 18. Test plan

### 18.1 Harness (owner A)

- Default backend run = **PostgreSQL**: `STOREOPS_TEST_PG_URL`
  (`postgresql+psycopg://storeops@127.0.0.1:5433/postgres` locally; CI
  service). conftest creates a per-run database `so_<track>_test_<pid>`
  (prefix from `STOREOPS_TEST_DB_PREFIX`, default `so_a`), creates the schema
  once (baseline path), and between tests `TRUNCATE ... RESTART IDENTITY
  CASCADE` + re-seed (teams), instead of drop/create. Drops the database at
  session end (and refuses to touch a database without the run prefix, like
  today's `_refuse_a_real_database`).
- `STOREOPS_TEST_DB=sqlite` keeps today's SQLite harness for quick unit runs.
- After B merges, conftest switches the default to `TENANCY_MODE=multi` with
  one registered test store (`TEST_STORE`, via `app.tenancy.testing`), so
  every route test goes through the middleware; `client_for` returns
  store-bound tokens. A `single`-mode job in CI keeps M2 behaviour covered
  (SQLite, subset or full).
- CI (`.github/workflows/ci.yml`, A): backend job Python 3.12, `services:
  postgres:16` (trust, user `storeops`), full suite on PG; a second job on
  SQLite single mode; the migration chain test on both.

### 18.2 Track tests (minimum)

- **A**: full existing suite green on PG (2687 today); schema parity
  (11.1); baseline + upgrade + downgrade-to-0062 on PG; chain on SQLite;
  `dialect_insert` upserts on both; `UTCDateTime` round trip on PG with a
  non-UTC server TimeZone; copy tool on a fake-store SQLite DB (row counts,
  checksums).
- **B**: context propagation (sync endpoint, sync dependency, BackgroundTasks,
  `run_in_threadpool`, `spawn_in_store`, scheduler job); engine LRU and idle
  disposal never dispose a busy engine; pool-bleed test (interleaved requests
  for 3 stores in 16 threads, 300 requests, each answer from its own DB);
  identity mismatch refused; `keyed_lock` and `tenant_cache` isolation;
  settings overlay precedence + invalidation + platform-only keys refused;
  `store_path` escapes refused; per-store cron in each store's timezone (DST
  case); leader lock (second scheduler does not start); cache lint.
- **C**: platform migrations up/down; sign-up validation, honeypot, reserved
  and taken codes, rate limits; approve -> provisioning end to end on PG
  (`so_c_*` databases: create, migrate, init with `packs/test_toast.json`,
  owner login works, scheduler jobs appear); failure at each step + retry;
  reject; suspend -> login 403 + jobs gone + lookup "maintenance"; resume;
  archive; credentials: ciphertext has no plaintext, wrong-store AAD fails,
  key rotation, blank secret keeps old, responses never contain a secret
  (scan), test-connection fakes, audit row for every action; lockout; TOTP if
  built; dedicated: flag off -> 409 everywhere; flag on -> request -> approve
  -> complete -> shared node answers `store_moved`, lookup returns the new
  URL; rollback.
- **D**: website `node --test` for new pure modules (login error mapping,
  signup validation, integrations view model); Playwright (via m1heavy) at
  1280 and 375: login with Store ID, wrong password, suspended store, signup
  -> pending, master approve -> pending page shows approved -> store login,
  integrations save/test (fake server), `/#/notifications` redirects, no
  Notifications link anywhere. App: widget tests for the single login screen
  (lookup, errors, store_moved retry, QR fill, known-store chips), switch
  store returns to it, `flutter analyze` clean, full `flutter test
  --concurrency=2`.

### 18.3 Cross-tenant isolation attack suite (owner C, `tests/platform/test_isolation_attacks.py`; B helps)

Two or three provisioned stores on PG with **canary strings** seeded in every
text column store B owns (users, vendors, items, chats, checklists, invoices,
flyers, settings).

1. **Canary crawl**: call every GET route in the app (enumerated from
   `app.routes`, path params filled from store A's own ids and from store B's
   ids) as store A's admin; no response body or header contains a B canary.
2. **Forged prefix**: A's token with B's code as prefix -> 401; B's code
   with a random secret -> 401.
3. **Header/query override**: A's token + `X-Store: B` / `?store=B` -> 409
   `store_mismatch` or A's data; never B's.
4. **IDOR**: B's row ids (users, tasks, invoices, media tokens, checklist link
   tokens, flyer ids) via A's session -> 404/403.
5. **Scope crossing**: platform session on store routes -> 401/403; store
   admin on `/api/platform/*` -> 403; store admin of A on
   `/api/settings/integrations` sees only A's kinds.
6. **Public routes**: B's checklist token with `?store=A` -> 404; A's store
   lookup never reveals B's settings.
7. **Files**: B's media/invoice/flyer paths via A -> 404; `../` and absolute
   paths refused.
8. **Caches**: warm every cached report for A, then request as B -> B's own
   numbers; suspending A clears A's caches only.
9. **Jobs**: a per-store job for A writes only A's DB (row counts of B
   unchanged); an interval job iterating stores keeps contexts separate.
10. **Concurrency**: 300 interleaved requests across 3 stores in 16 threads;
    every response carries its own store marker.
11. **Sessions**: suspended store's token -> 403; session of a deleted user ->
    401; logout in A does not touch B.
12. **Provisioning**: approval cannot reuse an existing store's code or a
    non-empty database of another store; a stamp mismatch blocks the engine.
13. **Credentials**: a ciphertext copied from B's row into A's row does not
    decrypt (AAD) and is reported as not configured.
14. **Push**: a push sent in A's context carries `data.store = A`.

---

## 19. Tracks, file ownership, branches

Branches: server `platform/a-postgres`, `platform/b-tenancy`,
`platform/c-platform`, `platform/d-clients`; app `platform/d-app`. Merge order:
**B's interface commit first** (section 16), then A, B (rest), C, D as each is
green; the later merger resolves conflicts and re-runs the suite. An
integration stage follows (full PG suite in multi mode, isolation suite,
Playwright, app).

### (A) Postgres port of existing code, migrations, test harness

- `backend/requirements.txt`, `backend/requirements-dev.txt`, `Dockerfile`
  (driver only), `.github/workflows/ci.yml` (backend jobs), `.gitignore`
  (`backend/.secrets/`).
- `backend/app/dbcompat.py` (new), `backend/app/migrate.py` (new).
- `backend/app/db.py`: **only** the `UTCDateTime` class.
- `backend/alembic/env.py`, `backend/alembic/pg_baseline/**` (new),
  `backend/alembic/script.py.mako` (dialect note).
- SQL hunks: `pos/sync/catalog.py` (`:43`, `:282`), `pos/sync/sales.py`
  (`:37`, `:157`), `services/inventory_parser.py` (`:15`, `:114`),
  `services/insights_data.py` (`:56`, `:297-310`), `services/inventory_api.py:79`,
  `services/sales_sync.py:30`, `pos/neutral_ids.py` (whole file),
  `services/report_cache.py` (`data_version`, `:150-172`),
  `team/changes.py` (`current_cursor`, `:240-255`),
  `services/similar_products.py` (`:885-900`), `models.py:113` and the other
  Boolean `server_default`s, every `except IntegrityError` site (1.4), every
  `group_by` / ordering fix the PG run demands.
- `backend/tests/conftest.py`, `backend/tests/pg_harness.py` (new),
  `backend/tests/test_pg_*.py` (new), dialect fixes in existing tests.
- `backend/scripts/pg/**` (new), `backend/scripts/demo/generate_demo_data.py`
  (dialect branch), per-store `pg_dump` backup script under
  `backend/scripts/backup/pg_*` (new).

### (B) Tenancy core

- `backend/app/tenancy/**` (new) incl. `global_state_allowlist.txt`.
- `backend/app/db.py` (all but `UTCDateTime`), `backend/app/config.py`,
  `backend/.env.example`, `backend/app/main.py` (middleware, lifespan, leader;
  C/D add include lines only), `backend/app/scheduler.py`,
  `backend/app/team/jobs.py`, `backend/app/auth.py`,
  `backend/app/store/identity.py`, `backend/app/store/settings_view.py`,
  `backend/app/store/pack.py` (state per store),
  `backend/app/pos/registry.py`, `backend/app/pos/adapters/toast/client.py`
  (token cache), `backend/app/pos/adapters/toast/analytics.py` (globals),
  `backend/app/services/push.py` (`store` key).
- Process-global hunks listed in 6.4/6.5/6.6/6.7 in their files (lock/cache/
  thread lines only), `team/changes.py:175-235`, `services/flyer_sender.py`
  (loop), `routers/sales.py:150-175`, `pos/sync/open_orders.py:410-435`,
  `services/stock_issues.py:195-235`, `services/report_cache.py:43,131-133`
  (engine hooks; A owns `data_version`), `services/fix_issues.py:744`.
- `backend/tests/tenancy/**` (new).

### (C) Platform backend

- `backend/app/platform/**` (new: models, db, sources, crypto, credentials,
  connector_tests, provisioning, signup, audit, throttle, totp, routers,
  `__main__` CLI), `backend/alembic_platform/**` (new), `backend/alembic.ini`
  (`[platform]` section).
- `backend/app/routers/auth.py` (store-aware login/logout/me, platform
  branch), `backend/app/store/__main__.py` (`init_store()` refactor),
  `backend/app/demo_guard.py` (new write routes), include lines in
  `app/main.py`, the `PUBLIC` list in `tests/test_review_route_gates.py`.
- `backend/app/routers/checklist_public.py` + `services/checklist_public*`
  link format (`/c/<CODE>/<token>`, `?store=`), `routers/notifications.py`
  unregister `store_code`, `routers/branding.py` / `routers/legal.py` /
  `team/router_core.py` app-config `?store=` handling.
- `docs/DEDICATED_HOSTING_RUNBOOK.md`, `docker-compose.platform.yml`.
- `backend/tests/platform/**` incl. the isolation attack suite (18.3).

### (D) Clients

- Server repo: `frontend/src/**`, `frontend/index.html`, `frontend/e2e/**`,
  `backend/app/glossary.py` (help text).
- App repo: `lib/features/authentication/**`, `lib/core/tenant/**`,
  `lib/core/config/env_config.dart`, `lib/core/api/**` (login call),
  `lib/core/notifications/**` (unregister `store_code`), app tests,
  `docs/OWNER_SETUP.md` (new define).

### Test commands

- Backend (from `/Users/sundar/storeops/storeops/backend`), via m1heavy for
  full runs: `STOREOPS_TEST_PG_URL=postgresql+psycopg://storeops@127.0.0.1:5433/postgres
  STOREOPS_TEST_DB_PREFIX=so_<track> .venv/bin/python -m pytest -q`;
  SQLite: `STOREOPS_TEST_DB=sqlite .venv/bin/python -m pytest -q`.
- Platform migrations: `.venv/bin/alembic -n platform upgrade head`
  (`PLATFORM_DATABASE_URL` set to the track's own database).
- Website: `node --test $(find src -name '*.test.mjs')`, `npx vite build`,
  Playwright via m1heavy.
- App: `flutter analyze`, `flutter test --concurrency=2` via m1heavy.

---

## 20. Deferred (not in this milestone) and owner actions

Deferred: job queue workers (MT28; one leader scheduler instead); object
storage R2 + presigned URLs (MT29); cross-process long-poll via
`LISTEN/NOTIFY`; Stripe/Clover billing and usage metering (MT30); export ZIP
+ off-boarding automation; PgBouncer; per-POS-app global rate budget; external
pen test (MT32); Clover OAuth (M3); moving the existing M2 single-instance
stores (DEMO, fakes) onto the platform (cutover, MT31).

Owner actions (placeholders, never faked):
1. Production Postgres 16 (managed or VM), a `storeops_app` role and a
   `storeops_admin` role with `CREATEDB` (`PG_ADMIN_URL`).
2. Generate the master key ring (`python -m app.platform gen-key`) and keep
   it in the server's secret store (`STOREOPS_SECRET_KEYS`); losing it loses
   every store's saved credentials.
3. Create the first master: `python -m app.platform create-master --username <you>`.
4. DNS: `app.<PLATFORM_DOMAIN>` for the shared service (M2 owner TODO 4).
5. Terms URL for the sign-up form (`TERMS_URL`).
6. Decide when to turn on `DEDICATED_HOSTING_ENABLED` (only when a store
   needs it) and `PLATFORM_REQUIRE_TOTP` (recommended for production).

---

## As built - A (PostgreSQL port)

Branch `plat/postgres-port` (worktree `wt-pg`), on top of `ab5cf34`. Not pushed
(merge order: B's interface commit, then A).

### What changed (app code)

- **`app/dbcompat.py`** (new): `dialect_insert`, `is_postgres`, `is_sqlite`,
  `sequence_last_value` (section 16), plus two helpers the parity run needed:
  `fsum(col)` (float SUM: PostgreSQL sums as numeric and casts back, so
  `2285.87` stays `2285.87` instead of `2285.8700000000013`; SQLite keeps
  `sum()`, which is compensated from 3.43) and `group_order(*keys)` (ORDER BY
  that reproduces SQLite's GROUP BY output order: keys ascending, NULL group
  first).
- **`UTCDateTime`** (`app/db.py`, class only): on PostgreSQL binds aware UTC
  (naive = UTC, as everywhere) and converts any aware value read back to UTC.
  The stored instant never depends on the session TimeZone; B's
  `-c timezone=UTC` connect option is still wanted (raw SQL date functions),
  but nothing in the app needs it today. Tested with Asia/Kolkata and
  America/Los_Angeles sessions; CI runs the suite with `PGTZ=America/New_York`.
- **SQLite-only SQL removed** at every site in 1.1: the 4 upserts + 2 plain
  inserts use `dialect_insert`; `neutral_ids` uses `ON CONFLICT DO NOTHING`
  and binds through `UTCDateTime`/`Date`; `report_cache.data_version` uses
  `coalesce` + text casts; `similar_products` `total()` -> `coalesce(sum)`;
  `team/changes.current_cursor` uses `sequence_last_value` (on PostgreSQL the
  committed `max(seq)` first, the sequence only when every row is pruned - a
  sequence also counts in-flight seqs and a long-poll must not skip one);
  `orders.is_modifier` default `sa.false()`. Dead `sqlite_insert` imports
  removed (the two shim tests no longer expect that name).
- Found by the PostgreSQL suite: `sum()` of booleans (tasks progress,
  invoice-close fingerprint) casts to integer; the three "table missing"
  fallbacks that caught only `OperationalError` also catch `ProgrammingError`
  (PostgreSQL's UndefinedTable) and roll back; the catalog snapshot INSERT
  asks for `preserve_rowcount` (PostgreSQL otherwise reports -1).
- Found by the parity run (section "Parity" below): ORDER BY ties broken by
  `id` in the primary key's direction and `nulls_first()/nulls_last()` where
  the sort column is nullable (20 sites); `group_order` on the 25 GROUP BY
  queries whose rows are iterated in order; `fsum` on the 10 float sums.
- 1.4 audit: all 14 `except IntegrityError` sites roll back (or sit in a
  savepoint) before the next statement; the 3 `begin_nested` sites are fine;
  the broad `except` in `changes.py` is gone. `group_by` looseness: none on
  PostgreSQL (the full suite runs every one).

### Migrations (11.1)

- `backend/alembic/pg_baseline/0062.sql` + `0062_seed.json`, generated from
  the SQLite chain by `scripts/pg/make_baseline.py` (`--check` in CI). 62
  tables with the chain's own column order, NOT NULL, normalised defaults
  (booleans `true/false`, numbers bare), PKs, unique and CHECK constraints
  and indexes (same names). Single-column integer PKs are
  `GENERATED BY DEFAULT AS IDENTITY`; timestamps `timestamptz`; floats
  `double precision`.
- **Interface change: no FOREIGN KEY constraints on PostgreSQL.** SQLite never
  enforced them (no `PRAGMA foreign_keys=ON` anywhere) and the app relies on
  that: deleting a user keeps done tasks / history rows that name them
  (`test_deleting_a_user_cleans_up_what_would_keep_acting_for_them` fails
  with enforced FKs), and production data holds such references. The models
  keep their `ForeignKey()`s (joins, documentation). Enforcing them later
  needs ON DELETE rules per key and a data clean-up - a separate decision.
  New migrations should not add FK constraints (template note).
- Seed: exactly the rows the SQLite chain leaves in a fresh database at 0062
  (4 teams, 19 category overrides, 3 checklist definitions + 4 versions,
  2 WhatsApp groups), the migrations' own "now" filled at baseline time. So a
  fresh PostgreSQL store == a fresh SQLite store, and C's `init_store()`
  removes the legacy rows exactly as on SQLite. (Contract text said "not the
  ones store-init deletes"; seeding all keeps the two paths identical.)
- `alembic/env.py`: URL from `config.attributes["db_url"]`, `-x db_url=`,
  `-x store=CODE` (through `app.tenancy` once B lands; a clear error before),
  else `DATABASE_URL`. PostgreSQL: per-database advisory xact lock, empty
  database -> baseline + seed + stamp 0062 in the same transaction, a
  database with tables but no revision is refused, any step through a
  revision <= 0062 (upgrade or downgrade) raises `BaselineError`. Offline
  `--sql` mode is refused for PostgreSQL. `%` in URLs is escaped for
  configparser.
- `app/migrate.py`: `upgrade_store_db`, `current_store_revision`,
  `store_head_revision`, `downgrade_store_db`, `BaselineError`; runs in
  process without touching logging (C's provisioning and `migrate-all` call
  `upgrade_store_db(url)` on a database created with
  `TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'`).
- `script.py.mako`: the both-dialects rule for 0063+.

### Test harness (18.1)

- Default = PostgreSQL. `STOREOPS_TEST_PG_URL` (default
  `postgresql+psycopg://storeops@127.0.0.1:5433/postgres`),
  `STOREOPS_TEST_DB_PREFIX` (default `so_a`); `STOREOPS_TEST_DB=sqlite` for
  the old per-test SQLite file. No server reachable -> the run stops with a
  hint (it never silently falls back).
- One database per run `<prefix>_test_<pid>` (C collation), schema built by
  `upgrade_store_db` (so every run exercises the baseline), dropped at the
  end; leftovers of killed runs (dead pid, same prefix) are dropped at the
  next start. Between tests `so_test_reset()` truncates only non-empty tables
  and restarts used sequences (~ms); a test that drops/recreates a table
  (5 "older database" tests) changes the schema signature and the next test
  gets the schema rebuilt. The report cache is ended after each reset (on
  SQLite the metadata `after_create` hook did that).
- Numbers (Mac mini, via m1heavy): **PostgreSQL 2702 passed, 21 skipped
  (~3.3 min)**; **SQLite 2693 passed, 30 skipped (~4 min)**. The extra
  PostgreSQL skips on SQLite are the PostgreSQL-only tests in
  `test_pg_port.py`; the 21 are the private-pack / tool-dependent skips that
  existed before.
- For B (multi mode default): the harness only needs `engine` to point at
  the run database; `pg_harness.app_url(name)` gives URLs for extra test
  stores (create with the same `CREATE DATABASE` line, prefix-checked).

### Copy tool, backups, demo (11.4)

- `scripts/pg/copy_sqlite_to_pg.py --sqlite PATH --pg-url URL`: source opened
  read-only in one read transaction, must be at >= 0062; target empty
  (migrated here) or at the same revision holding only the baseline seed
  rows (replaced); COPY in FK order, one transaction; strict conversion
  (`sqlite_pg.py`: a value that does not fit stops the copy naming table,
  column and row); identity sequences set to max(id); row counts + per-column
  checksums compared (ordered by PK, bytewise). `verify_copy.py` alone.
  Demo store (14 days): 6,707 rows, 0 differences, 0.3 s; Spice Town prod
  copy: 0 differences, ~60 s.
- `scripts/backup/pg_backup.py backup|drill` (stdlib only; pg client >= the
  server's major version; passwords via ~/.pgpass/PGPASSWORD, refused in the
  URL). The Docker image has no pg_dump 16 (bookworm ships 15): run backups
  from the host or a `postgres:16` sidecar.
- `scripts/demo/generate_demo_data.py --pg-url URL`: the DEMO store into an
  empty PostgreSQL database (copy tool, 0 differences or the run fails).

### Parity (Spice Town pack, read-only prod copy)

Fresh `.backup` of the production database (read-only handle, 0059), both
recordings from this branch with `--migrate` (to 0062): SQLite
(`parity_record.py`) vs PostgreSQL (`parity_record.py --pg-url`, a
throwaway `so_a_parity` database). 110 endpoints, all 200 on both, no
network attempts, alerts and scheduler jobs identical.

First run: 8,576 differences in 5 endpoints - all row order (ties, GROUP BY
order) and float sums. After the fixes above: **12 differences, each
justified**:

| Where | n | Why |
|---|---|---|
| `ordering.forecast.30` `items[18]` total / avg | 2 | `194.89000000000001` (SQLite: compensated sum of the binary floats) vs `194.89` (PostgreSQL: exact decimal sum). Last-digit noise; every rounded figure the UI shows is equal. |
| `fingerprint.json` item_vendors.created_at/updated_at, pos_item_map.created_at, sync_cursors.updated_at | 4 | Stamped "now" by migrations 0060/0061 during `--migrate`: two SQLite recordings differ in the same 4. |
| `fingerprint_after.json` the same 4 | 4 | Same. |
| `fingerprint_after.json` category_overrides.updated_at, checklist_definitions.updated_at | 2 | SQLite holds second-precision text (`2026-10-01 01:30:37`, written by migration raw SQL); PostgreSQL holds the same instant, which reads back with microseconds. Listed in the recording's `meta.json` (`non_canonical_sqlite_columns`). |

Copies (prod copy, scratch, `so_a_parity`) deleted after the run.

### Rules for new code (all tracks)

ORDER BY must be total (end with the id, in the primary sort's direction)
and say where NULLs go if the column is nullable; GROUP BY rows used in
order get `group_order(...)`; float SUMs use `fsum`; never `sum()` a
boolean; raw SQL binds timestamps/dates through the column types and uses
`true/false`; "table missing" fallbacks catch `(OperationalError,
ProgrammingError)` and roll back; an INSERT whose rowcount matters passes
`preserve_rowcount`; upserts via `dialect_insert`.

### Deferred / known limits

- Queries without any ORDER BY that return lists rely on scan order; on
  PostgreSQL an UPDATE moves a row physically. Not exhaustively audited
  (the parity run and the suite show no case); fix at the site when seen.
- FK enforcement (above). Exact binary float parity (would need summing
  in Python with `math.fsum`; not worth the speed).
- CI's PostgreSQL job and image are untested until the branch is pushed.

### Owner actions

None new: production PostgreSQL 16 (20, item 1). Schedule
`pg_backup.py backup` nightly and `drill` monthly per store on a host with
the PostgreSQL 16 client.

## As built - B (tenancy core)

Branch `plat/tenancy-core` (worktree `wt-tenancy`), on `ab5cf34`. Commits:
`bc3fb00` interface package (section 16, no behaviour change), `017bf41`
wiring (db, auth tokens, middleware, lifespan, scheduler, leader),
`4f5a75f` per-store locks / caches / threads / long-poll / flyer sender +
cache lint, `89e36f9` PostgreSQL tests, the private-keys fix, `d606919`
structured access errors, and this section.

### What exists

- `app/tenancy/` implements every section 16 signature, plus the additive
  extras listed under section 16 ("Additions made by B"). Modules: `errors`,
  `mode`, `registry` (StoreRef, single-mode source, 10 s cache incl. negative
  answers), `context` (ContextVar; single mode always has its one store,
  code `_` when STORE_CODE is blank), `engines` (lazy LRU, idle sweep, busy
  engines never disposed, PG `options=-c timezone=UTC -c
  statement_timeout=...`, identity + uploads-marker check on first open,
  `store.identity_mismatch` event), `jobs`, `locks`, `cache`, `paths`,
  `settings`, `routing` (dedicated-hosting block, clearly marked),
  `middleware`, `tokens`, `leader`, `boot`, `events`, `testing`, and
  `global_state_allowlist.txt`.
- `app/db.py`: `engine` (single mode; `None` in multi), tenant-aware
  `SessionLocal()` (sets `info["store_code"]`; `kw` / `configure` kept),
  `get_db`, `get_optional_db`, `get_engine`. Without a store in multi mode
  `get_db` answers 401 `Not logged in`; a token of a suspended store 403
  `store_suspended`, pending / provisioning / failed 403 `store_not_ready`, a
  master's session on a store route 403 `platform_scope`, an identity or
  connection failure 503 `store_unavailable`. These answers keep FastAPI's
  `detail` (old clients) and add `error` + `message` (rendered by the
  HTTPException handler in `main.py`). An archived store's token answers
  401 like an unknown store.
- `app/auth.py`: tokens `"<CODE>.<secret>"`, stored as sha256 hex in
  `user_sessions.token` (no migration); `lookup_session(db, token)` (prefix
  must be the current store; single mode also accepts the dev placeholder
  `_`), `create_session` / `delete_session` / `get_current_user` unchanged
  signatures. Raw pre-platform tokens stop working: **every user signs in
  once after this deploys** (M2 stores included).
- `TenantMiddleware` sits inside CORS (CORS stays outermost). Public store
  routes taking `?store=`: `/api/public/checklists/`, `/api/app/branding`,
  `/api/app/config`, `/api/app/legal`; C adds more with
  `register_public_store_route(prefix)`. A platform token ignores `?store=`
  (it is a filter on `/api/platform/*`).
- Lifespan: single mode exactly as M2. Multi mode `app/tenancy/boot.py`:
  startup hooks (`add_startup_hook` - C installs its registry and settings
  sources there), `STORE_DATABASE_URL_TEMPLATE` required, connection budget
  check, leader election (`pg_try_advisory_lock(0x53544F5245)` on a
  dedicated AUTOCOMMIT connection to `PLATFORM_DATABASE_URL`, watchdog every
  30 s, a follower keeps trying), flyer sender in every process.
- Scheduler (multi mode, leader only): `start_multi_tenant_schedulers()`.
  Per store (`"<job>:<CODE>"`, store timezone and store settings, 0-120 s
  per-store offset): daily_sales_sync, open_orders_full_scan, sales_repull
  (one OrTrigger per store), insights_nightly, insights_catchup,
  inventory_api_snapshot, notify_closing_checklist, notify_morning_summary,
  notify_open_orders_HHMM, every TEAM_JOBS job (`team_<id>`). Startup
  catch-ups per store when first seen (startup_catchup_sync,
  startup_open_orders_today, startup_fix_issues_warmup,
  startup_open_orders_full_scan). Interval jobs once, each tick over every
  store: open_orders_today_refresh, menu_barcode_recheck,
  notify_invoice_failed, notify_cart_removal_request - their intervals are
  the process values (a per-store `open_orders_today_refresh_minutes` is
  not used in multi mode). A "platform" scheduler reconciles every 5 min
  and sweeps idle engines every minute. Capability gating per store
  (`JOB_CAPABILITIES` by function name). Executors: `SCHEDULER_MAX_WORKERS`
  threads per scheduler.
- Locks: all 31 per-store locks of 6.4 (incl. the 11 cache guards) are
  `tenant_lock(name)`; `po_cart` is reentrant. `advisory_lock(db, name)` is
  added inside the same blocks for po_cart, order_actions (4 sites),
  critical_dismissals (2), purchase_order_log (3), po_send and the flyer
  `_SEND_LOCK` (2). The xact lock ends at the block's commit. Kept global as
  listed in 6.4; `insights_data._live_locks` (map of per-key locks) is per
  store with a global guard.
- Caches / state: every per-store item of 6.5 is `tenant_local(...)` (call
  sites unchanged), plus `open_orders._EMPLOYEE_CACHE` and
  `_live_last_failure` (not in 6.5, per store now). `stock_issues._generation`
  and `toast_analytics._token` keep a module name for old callers; the live
  values are per store. Store pack: pack + source per store; the test
  override (`set_override`) stays process-wide. POS connectors keyed
  `(store, provider)`; `reset_cache(store_code=None)`.
- Threads: `routers/sales.py`, `pos/sync/open_orders.py`,
  `services/stock_issues.py`, `services/insights_data._spawn` use
  `spawn_in_store`; the flyer sender queues `(store, send id)` and sweeps
  per store (`for_each_store`; the first multi-mode sweep runs in the
  background).
- Long-poll: versions per store; `notify_changed(store_code=None)`; a
  commit wakes only its own store's waiters.
- Report cache: listeners on every engine via `register_engine_hook`;
  a commit through ALPHA's engine ends only ALPHA's cache.
- Settings: precedence env < registry row (name, timezone, POS) <
  store_settings < credentials < derived. **Addition:**
  `STORE_PRIVATE_KEYS` (credentials, `gmail_*`, `whapi_chat_id` + legacy
  group ids, `toast_invoice_email` / `ap_inbox_email`, store name / address
  / phone / email / description, logos, `labels_url`, `public_base_url`,
  `email_from_name`, `first_sales_date`, `demo_mode`, `insights_hmac_secret`)
  never come from the platform env in multi mode - they start at the class
  default, so one store's values left in `.env` cannot reach other stores.
  Derived: `store_code`, `uploads_root` / `team_uploads_dir` /
  `invoice_uploads_dir` / `flyers_dir` / `insights_hmac_key_file` under
  `<UPLOADS_ROOT>/<CODE>/`, `public_host` = PLATFORM_PUBLIC_URL,
  `store_pack_file` blank. A bad stored value is dropped (logged), never
  fatal; `validate_store_setting` refuses it at write time.
- Cache lint: `tests/tenancy/test_cache_lint.py` (module-level mutable
  containers, locks, `lru_cache`/`cache`, `global`) against the allowlist
  (171 entries, each with a reason; constant tables included so a new
  mutable one has to be added on purpose; a stale entry fails too).

### Files outside B's list (contract 0.2.10)

- `app/demo_guard.py` (C): `Depends(get_optional_db)` + `db is None` early
  return (it is an app-wide dependency and ran `get_db` on login / health /
  platform routes, which would answer 401 in multi mode), and
  `lookup_session` instead of `db.get(UserSession, token)`.
- Tests changed for the new per-store state / hashed tokens (one line
  each): `test_flyers.py` (pacing), `test_push_devices.py` (token hash),
  `test_team_tasks.py` (`recurrence._run_state`), `test_team_auto_tasks.py`
  and `test_v1_batch_oct14.py` (`auto_tasks._order_scan`).
- `app/services/inventory_api.py` keeps `import threading` (an old name the
  compat test resolves).

### Tests

- Full backend suite (SQLite single mode, existing harness) + tenancy:
  **2744 passed, 20 skipped** (2687 existing + 57 new), via m1heavy.
- `tests/tenancy/` (57): PostgreSQL 16 on the dev cluster, databases
  `so_b_<pid>_<store>` created with C collation and dropped at the end;
  skipped when PG is not reachable. Isolation over HTTP (each token reads
  its store; forged prefix / random secret / unknown store -> 401;
  `X-Store` / `?store=` mismatch -> 409; suspended / not ready / archived;
  dedicated store `store_moved` only with the flag; dedicated node serves
  only its codes; admin user lists per store; 300 interleaved requests in
  16 threads over 3 stores, every answer from its own DB); identity /
  instance-id / uploads-marker mismatch refused (503 + event); sessions
  hashed; logout isolation; context propagation (sync endpoint, sync
  dependency, BackgroundTasks, run_in_threadpool, spawn_in_store, raw
  thread has none, scheduler job runner); per-store locks, reentrant lock,
  PG advisory lock across sessions; tenant_cache / tenant_local isolation,
  LRU, TTL; real caches (report cache incl. the engine hook, POS
  connectors); settings precedence, platform-only keys ignored, private
  keys not leaked from env, invalidation, bad values dropped, registry
  timezone; push `data.store`; `store_path` escapes (`..`, absolute,
  backslash, symlink out); long-poll per store; engine LRU / idle sweep
  never close a busy engine; UTC + statement timeout on PG; SessionLocal
  without a store; connection budget; for_each_store (once per active
  store, writes stay in their DB, errors isolated, suspended / pending /
  dedicated skipped, dedicated node); per-store cron in each timezone across
  the 2026-11-01 US DST change; reconcile on suspend / timezone change;
  startup jobs once; capability gating per store (Clover / CSV stores get
  no POS jobs); per-store settings shape the jobs; full multi-mode
  scheduler start; each_store_job; leader election (one leader; a lost
  connection stops it, the other takes over); single mode unchanged
  (implicit store, tokens, middleware no-op); cache lint.

### Notes for A (PostgreSQL port)

Found while running store code on PG (not fixed here - A's files):
1. `Base.metadata.create_all` fails on PG: Boolean `server_default=text("0")`
   (`models.py:113`, `orders.is_modifier`). The tenancy tests patch Boolean
   0/1 defaults to false/true for their own create_all only.
2. `services/invoice_close_suggestions.py:222` `_fingerprint`: `sum(boolean)`
   does not exist on PG (the close-suggestions call fails, logged).
3. `team/changes.py` `current_cursor`: the `sqlite_sequence` query fails and
   poisons the PG transaction -> `GET /api/team/bootstrap` 500 (known, 1.1).
4. A's conftest switch to multi mode: wrap each test in
   `as_store(TEST_STORE)` (or make the client requests carry store tokens -
   `create_session` inside the store context does that); `app.db.engine` is
   `None` in multi mode, use `tenant_engine(TEST_STORE)` for create_all /
   truncate; fixtures that run outside a store context use the platform
   bucket of tenant caches.

### Notes for C (platform backend)

- Install sources in a startup hook: `app.tenancy.boot.add_startup_hook(fn)`
  calling `set_registry_source` / `set_settings_source`. Store settings
  values are expected decoded (not JSON strings); credentials as Settings
  field names.
- Login: create the session inside `tenant_session(code)` (the token gets
  the code prefix). Logout: `delete_session` inside the store. A platform
  token gets scope `platform` from the middleware; C verifies
  `platform_sessions` itself (B's `token_hash` is the same sha256).
- Provisioning: `tenant_session(code, allow_inactive=True)` / `store_context(
  code, allow_inactive=True)`; an engine of a `provisioning` store opens
  without a stamp; after activation the stamp must match
  `stores.instance_id`. Call `on_store_changed(code)` after approve /
  suspend / resume / archive / settings, timezone or POS changes (clears the
  store's caches and engine when it is no longer active, reschedules jobs).
- Audit: `app.tenancy.events.add_listener(fn(action, store_code, details))`
  receives `store.identity_mismatch`.
- `validate_store_setting(key, value)` for the settings PUT (400 on
  ValueError).

### Owner actions (B)

- Production PostgreSQL `max_connections`: with the defaults
  (TENANT_MAX_ENGINES 40 x (2 + 3) + 10 = 210) the startup check refuses a
  server with the stock 100 / 200; set `max_connections` to 250+ or lower
  `TENANT_MAX_ENGINES`. (The dev cluster has 200: dev multi mode needs
  `TENANT_MAX_ENGINES=38` or less.)
- Everyone signs in once after this ships (tokens are now store-bound and
  stored hashed).

### Deferred (B)

Cross-process long-poll wake-ups (`LISTEN/NOTIFY`); per-store interval
lengths in multi mode; running the flyer sender only on the leader; a
platform-level (not per-store) Toast Analytics budget for stores that share
one Toast client.
