---
name: storeops
description: Operating notes for the StoreOps server + website repository (FastAPI + SQLite + React/Vite; one process per store; POS adapters). Load before changing anything under backend/ or frontend/ so known traps are not repeated.
---

# StoreOps - project skill

StoreOps is a multi-store operations product (KloudC LLC): one container per
store runs the API, the website and the scheduled jobs against that store's
own SQLite database and uploads. The POS is behind an adapter
(`backend/app/pos/`); Toast is the first one.

Read first:
- `docs/MULTISTORE_M1_CONTRACT.md` - the M1 interfaces (POS adapters, neutral
  models, capabilities). Still in force.
- `docs/M2_CONTRACT.md` - the M2 interfaces (store settings, branding, store
  pack, directory, app tenant config, gating) and each batch's "As built".

## Ground rules

1. **No store's own data or secrets in git.** No real names, addresses,
   phone numbers, staff, catalogs, prices or credentials. Settings default to
   StoreOps values or blank; a store's values live in its `.env` and its
   store pack, outside git. `.env.example` files hold blank values only.
2. **Never under another product's domain.** Hosts come from settings
   (`PLATFORM_DOMAIN`, `PUBLIC_BASE_URL`, ...). The name-grep test (M2
   contract 9.2, in `backend/tests/`) fails on the legacy origin store's
   names outside its allowlist; shrink the allowlist, never grow it.
3. **No outside calls in tests.** Point outside base URLs at
   `http://127.0.0.1:9` (discard port) so an accidental call fails fast. No
   real Toast / Clover / Whapi / SMTP / FCM / Anthropic calls, ever.
4. **Additive API changes only.** Clients in the field read existing keys;
   add keys and routes, never rename or remove them.

## Architecture in one screen

- **Backend**: FastAPI, SQLAlchemy 2, Alembic, APScheduler (in-process jobs,
  no OS cron). `app/main.py` mounts the built website (`frontend/dist`) at
  `/` when it exists: one process, one port. Health: `GET /api/health`.
- **DB**: SQLite in WAL mode with a 5 s busy timeout (`app/db.py`), so a
  reader (`sqlite3`, Litestream, backups) never blocks the app.
- **Auth**: opaque session tokens in `user_sessions` (not JWT), cookie for
  the website (HttpOnly, Secure, SameSite=Lax), bearer token for the app.
  Every router is created with `dependencies=[Depends(get_current_user)]`.
- **Frontend**: React + Vite + react-router-dom with `HashRouter` (the
  static mount has no SPA catch-all, so `BrowserRouter` deep links would 404).
- **Deploy**: the repo-root `Dockerfile` (multi-stage: website build, then
  Python 3.12-slim with Tesseract) and `docker-compose.yml` (one store plus an
  optional Litestream sidecar). `alembic upgrade head` runs on container start.

## Gotchas (each one shipped as a real bug once)

1. **Python 3.9 compatibility.** CI runs 3.9 and 3.12. Use
   `typing.Optional[X]`, never `X | None`, and no `match`.
2. **SQLite drops timezone info on read.** Every timestamp column uses
   `app/db.py`'s `UTCDateTime()`, never `DateTime(timezone=True)`, or values
   come back naive and browsers show them hours off. Show times in the store
   time zone, never with a bare `.toLocaleString()`.
3. **Toast timestamps** (`2026-08-23T18:28:45.000+0000`, no colon in the
   offset) do not parse with `datetime.fromisoformat` on 3.9; use
   `strptime(..., "%Y-%m-%dT%H:%M:%S.%f%z")`.
4. **Toast selections nest `modifiers`**; they are stored as their own rows
   with `parent_selection_guid`. "Real sold items" queries need
   `parent_selection_guid IS NULL`, or modifiers are double-counted. When a
   shared field's meaning changes, grep every consumer, not just the one you
   are working on.
5. **A Toast order's `businessDate` / `openedDate` can be a scheduled pickup
   time**, not when it was paid; "sold" counts use the payment date.
6. **Multi-value inventory fields** (`supplier`, `barcode`) are
   semicolon-separated; split before validating or filtering.
7. **The inventory parser keeps an allowlisted column subset**, matched by
   lowercased header name. A new column needs `COLUMN_MAP` (and a migration);
   it never backfills old uploads.
8. **Toast API limits**: 429s on bulk backfills (retry failed dates from
   `job_runs`); the Purchasing API is scope-blocked; the Analytics API is a
   separate product with its own credentials and has no cost/margin data.
9. **Testing auth over plain http**: the session cookie is `Secure`, so
   `httpx` will not send it back over http (correct behaviour); use bearer
   tokens or `curl` cookie jars in local checks.
10. **Custom button hover styles need `:not(:disabled)`** - the global
    `button:hover:not(:disabled)` rule has higher specificity than a bare
    `.class:hover`, so the button flashes the accent colour otherwise.
11. **Never use a whole-file write on a large existing source file** to make
    a small change; edit in place. A whole-file overwrite once dropped most
    of `api.js`.
12. **Scheduled jobs run once ~10 s after start-up** (gap-aware sync
    catch-up). `SCHEDULER_ENABLED=false` / `NOTIFICATIONS_ENABLED=false`
    turn the jobs off for local runs and smoke tests.

## Adding a feature (established pattern)

1. Service function in `backend/app/services/<feature>.py` (pure queries,
   no request objects), router in `backend/app/routers/`, registered in
   `app/main.py`.
2. A migration only when a table or column is needed; the next free number,
   recorded in the M2 contract first.
3. Gate the feature: a key in `app/features.py` and, when it needs a POS
   capability or a store feature, the entries in `app/pos/gating.py`.
4. Website: page in `frontend/src/pages/`, API call in `src/api.js`, route
   in `src/featureRoutes.js`; pure logic in a `*Model.js` with a
   `*.test.mjs` next to it (`node --test`).
5. Tests: `backend/tests/test_<feature>.py`; both Python versions.

## Commands

    cd backend && python -m pytest -q             # backend suite
    cd frontend && node --test $(find src -name '*.test.mjs') && npx vite build
    cd backend && alembic upgrade head            # migrate the DATABASE_URL database
    python scripts/parity/parity_record.py ...    # parity harness, see scripts/parity/README.md

The parity harness is the regression gate for any change that must not
alter a store's numbers: record old and new code against the same database
copy and expect `DIFFERENCES: 0` (same Python version on both sides).
