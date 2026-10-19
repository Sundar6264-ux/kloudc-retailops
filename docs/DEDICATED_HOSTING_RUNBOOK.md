# Dedicated hosting runbook: move one store to its own server

Platform contract section 12. A store normally lives on the shared KloudC RetailOps
service (its own PostgreSQL database on the shared cluster). With **master
approval**, one store can move to its own server. Nothing here happens
automatically, and all of it is switched off until the owner turns it on.

## 0. The guard rails

| Guard | Where |
|---|---|
| `DEDICATED_HOSTING_ENABLED=false` (default) | every hosting API answers 409 `dedicated_hosting_disabled`; a registry row can never become `dedicated`; the shared service keeps serving a row marked `dedicated` (B logs a warning) |
| Master approval | only a master can `approve`, `start`, `complete`, `rollback` (`/api/platform/hosting-requests/{id}/...`); a store admin can only *request* (`POST /api/settings/hosting/request`) |
| One open request per store | partial unique index on `dedicated_hosting_requests` |
| `complete` needs an approved request | `app/platform/hosting.py` (the block marked `DEDICATED HOSTING`) |
| Routing | `app/tenancy/routing.py::node_serves` (track B): the shared node answers 409 `store_moved` for a dedicated store; the dedicated node (`DEDICATED_STORE_CODES=<CODE>`) refuses every other store |
| Allowed addresses | `api_base_url` must be `https://<host>` under `PLATFORM_DOMAIN` or in `DIRECTORY_EXTRA_API_HOSTS`, never the legacy domain |
| Credentials | DB passwords only in env (`STORE_DB_HOSTS`), never in the platform database |

Everything is audited (`hosting.requested`, `hosting.approved`,
`hosting.migrating`, `hosting.completed`, `hosting.rolled_back`,
`hosting.rejected`, `hosting.cancelled`).

## 1. Owner decision

1. Read the request in the console (**Platform -> Hosting**) or
   `GET /api/platform/hosting-requests?status=requested`.
2. Decide the server size, the region and the new address, e.g.
   `https://xyz-grocery.<PLATFORM_DOMAIN>`.
3. Turn the feature on for the shared service: `DEDICATED_HOSTING_ENABLED=true`
   in the shared `.env`, restart. (It stays on while any store is dedicated;
   with it off, a dedicated row is served from the shared service again.)
4. Approve: `POST /api/platform/hosting-requests/{id}/approve`
   `{"target_api_base_url": "https://xyz-grocery.<domain>", "target_db_host_ref": "xyz", "note": "..."}`.

## 2. Prepare the dedicated server (no traffic yet)

1. A VM with Docker and PostgreSQL 16 (or a managed Postgres).
2. Same image as the shared service (same git tag).
3. `.env` of the dedicated server:

   ```
   TENANCY_MODE=multi
   DEDICATED_STORE_CODES=XYZ_GROCERY
   DEDICATED_HOSTING_ENABLED=true
   PLATFORM_DATABASE_URL=postgresql+psycopg://retailops_ro@<platform-host>/so_platform   # read access (restricted role)
   STORE_DB_HOSTS={"xyz": "postgresql+psycopg://retailops_app:<pw>@127.0.0.1:5432/{db_name}"}
   RETAILOPS_SECRET_KEYS=<the SAME key ring as the shared service>
   PLATFORM_PUBLIC_URL=https://app.<domain>
   UPLOADS_ROOT=/data/uploads
   SCHEDULER_ENABLED=true
   ```

   Add the same `STORE_DB_HOSTS` entry to the shared service's `.env` (the
   shared side never connects to it after the move, but `migrate-all` lists it).
4. The platform-database role for the dedicated node: `SELECT` on
   `stores`, `store_settings`, `store_credentials`; `INSERT` on `audit_log`,
   `login_attempts`. No other rights.
5. DNS: `xyz-grocery.<domain>` -> the new server (Cloudflare Tunnel or A
   record). TLS terminates in front, as on the shared service.
6. `POST /api/platform/hosting-requests/{id}/start` (status `migrating`).

## 3. Copy the data (15-minute read-only window, 03:00 store time)

```bash
# On the shared cluster (read-only window: suspend the store first)
python -m app.platform suspend XYZ_GROCERY --reason "moving to its own server"
pg_dump -Fc -d so_xyz_grocery -f /tmp/so_xyz_grocery.dump
rsync -a /srv/retailops/uploads/XYZ_GROCERY/ newhost:/data/uploads/XYZ_GROCERY/

# On the dedicated server
createdb -T template0 -E UTF8 --lc-collate=C --lc-ctype=C so_xyz_grocery
pg_restore -d so_xyz_grocery --no-owner /tmp/so_xyz_grocery.dump
```

Check: row counts of the big tables (`orders`, `sales_*`,
`invoice_submissions`, `users`) match; `SELECT * FROM store_identity` shows
the same code and instance id as `stores.instance_id`.

## 4. Flip (the only step that changes routing)

```bash
curl -X POST https://app.<domain>/api/platform/hosting-requests/<id>/complete \
  -H "Authorization: Bearer <master token>" -H "Content-Type: application/json" \
  -d '{"api_base_url": "https://xyz-grocery.<domain>", "db_host_ref": "xyz"}'
python -m app.platform resume XYZ_GROCERY
```

`complete` sets `stores.hosting='dedicated'`, `api_base_url`, `db_host_ref`
(and remembers the previous values for rollback). From then on:

- the shared service answers 409 `store_moved` (with the new `api_base_url`)
  to that store's requests and logins, and closes its engine and caches;
- `GET /api/public/stores/XYZ_GROCERY` returns the new `api_base_url`; the
  app re-resolves and retries once; the website redirects to
  `<api_base_url>/#/login?store=XYZ_GROCERY`;
- the dedicated server serves only XYZ_GROCERY.

## 5. Verify (same day)

- Owner signs in on the website and the app (Store ID unchanged).
- Jobs ran on the new server (`job_runs` there), not on the shared one.
- Pushes arrive; WhatsApp/email sends work (credentials come from the
  platform database through the same key ring).
- `migrate-all` on the shared service lists the store under `dedicated`
  ("migrate on its own server"); run `alembic upgrade head` there on each
  release.

## 6. Rollback (within 7 days)

```bash
python -m app.platform suspend XYZ_GROCERY --reason "rollback"
pg_dump -Fc -d so_xyz_grocery -f /tmp/back.dump          # on the dedicated server
pg_restore --clean -d so_xyz_grocery /tmp/back.dump       # on the shared cluster
rsync -a newhost:/data/uploads/XYZ_GROCERY/ /srv/retailops/uploads/XYZ_GROCERY/
curl -X POST .../api/platform/hosting-requests/<id>/rollback -d '{"note": "why"}'
python -m app.platform resume XYZ_GROCERY
```

`rollback` restores `hosting='shared'` and the previous `api_base_url` /
`db_host_ref`. After 7 days a move back is a new request.

## 7. Turning it off again

Only when no store is `dedicated`: set `DEDICATED_HOSTING_ENABLED=false`
and restart. (If a row is still `dedicated` with the flag off, the shared
service serves it from the shared cluster again and logs a warning - which
is wrong if its data now lives elsewhere, so roll back first.)
