# KloudC RetailOps - pilot and production runbook

How to run KloudC RetailOps for real: first as a **pilot on this Mac**, later on
a **cloud VM**, and how to **onboard the first real store** next to the system
it runs on today without anything being sent twice.

Contents:
1. Pilot on this Mac (one command)
2. Reaching it: Tailscale now, app.kloudc.com later
3. Cloud VM (Docker compose)
4. Onboarding the first real store (pilot mode, import, private pack, parity)
5. Backups, restore drill and restoring a store
6. Start-up checks, health, readiness and logs
7. Updating, restarting, uninstalling

---

## 1. Pilot on this Mac (one command)

From your own account in a real Terminal:

    sudo ~/storeops/storeops/deploy/pilot-mac/install.sh

It asks for your Mac password once (sudo), and once for the password of the
first master account. It takes 5-10 minutes the first time (Python packages
and the website build) and is safe to run again.

What it sets up (everything under `~/kloudc-retailops`, nothing in the dev
checkout):

| What | Where | Notes |
|---|---|---|
| Release checkout | `~/kloudc-retailops/app` | A clone of `main`; `update.sh` moves it to a new release. Its own Python venv and website build. |
| PostgreSQL 16 cluster | `~/kloudc-retailops/pgdata`, port **5434**, socket in `~/kloudc-retailops/run` | Separate from the dev cluster (5433). Only your Mac user can connect, over the socket (peer auth, no passwords). `max_connections = 250`. |
| Server | `127.0.0.1:8900` | API + website + scheduled jobs, one process. Never listens on the network itself. |
| Config | `~/kloudc-retailops/config/pilot.env` | `APP_ENV=production`, multi-store mode, logs, public address. Sign-ups closed during the pilot (masters create stores). |
| Key ring (secret) | `~/kloudc-retailops/secrets/retailops.env` | Encrypts every store's saved passwords and keys. **Copy it somewhere safe and offline now** - without it they can't be read again. |
| Logs | `~/kloudc-retailops/logs` | `error.log` first; `app.log` (JSON lines), `startup.log`, `server.err.log`, `postgres.log`, `backup.log`, `parity-<CODE>.jsonl`. |
| Backups | `~/kloudc-retailops/backups` | Nightly at 03:15, kept 30 days; restore drill on the 1st of each month. |
| Uploads | `~/kloudc-retailops/uploads/<STORE>` | Invoices, team photos, flyers per store. |

launchd jobs (LaunchDaemons: they run at boot with nobody logged in, as your
Mac user, never as root). Only these labels are ever touched:

| Label | Runs |
|---|---|
| `com.kloudc.retailops.pilot.postgres` | the cluster, restarted on a crash |
| `com.kloudc.retailops.pilot.server` | `start-server.sh`: config check, migrations (platform, then every store), then the server; restarted on a crash |
| `com.kloudc.retailops.pilot.backup` | `run-backup.sh backup` at 03:15 |
| `com.kloudc.retailops.pilot.drill` | `run-backup.sh drill` on the 1st at 05:00 |
| `com.kloudc.retailops.pilot.parity` | `run-parity.sh` at 07:30 (does nothing until `config/parity.env` exists) |

First sign-in: open the https address the installer prints (the Tailscale
address; plain http on 127.0.0.1 is only for health checks - the login
cookie is https-only), Store ID `PLATFORM`,
your master username and password. Two-step sign-in is **required in
production**: the console asks you to set it up first (any authenticator app).

Options: `--no-tailscale`, `--skip-web`, `--dry-run DIR` (only renders the
launchd files into DIR), `--uninstall` (stops and removes the jobs; data,
backups and secrets stay).

## 2. Reaching it

### Now: Tailscale (tailnet only)

The installer runs `tailscale serve --bg --https=10003 http://127.0.0.1:8900`:
the pilot is at `https://<this Mac's tailnet name>:10003`, reachable only from
devices signed in to your tailnet (your phone with the Tailscale app). Ports
443, 8443 and 10000-10002 are already used by other services on this Mac, so
the pilot uses 10003 and never changes an existing mapping.

To remove it: `sudo tailscale serve --https=10003 off`.

### Later: app.kloudc.com

`kloudc.com`'s DNS is at Bluehost today. Two good ways to put the service at
`https://app.kloudc.com`:

**A. Cloudflare Tunnel from this Mac (no open ports).** A tunnel's public
name must be in a Cloudflare DNS zone, so kloudc.com's DNS moves to Cloudflare
(free plan) - the website itself stays at Bluehost:
1. Cloudflare dashboard -> Add a site -> `kloudc.com` -> Free. Cloudflare
   copies the existing records; check that the website (A/CNAME for `@` and
   `www`), email (MX, SPF/DKIM TXT) and any verification TXT records are all
   there before going on.
2. Bluehost -> Domains -> kloudc.com -> Name Servers -> Custom: the two
   Cloudflare name servers. Wait until Cloudflare says the site is active
   (minutes to a few hours). Check the website and email still work.
3. On this Mac (as your user): `cloudflared tunnel login`, then
   `cloudflared tunnel create kloudc-retailops` and
   `cloudflared tunnel route dns kloudc-retailops app.kloudc.com`.
4. A config file `~/kloudc-retailops/config/tunnel.yml` with
   `tunnel: <id>`, `credentials-file: ~/.cloudflared/<id>.json` and
   `ingress: [{hostname: app.kloudc.com, service: http://127.0.0.1:8900}, {service: http_status:404}]`.
   Run it as its own LaunchDaemon (label `com.kloudc.retailops.pilot.tunnel`,
   program `cloudflared tunnel --config <that file> run`). Do **not** use
   `cloudflared service install`: this Mac already runs another tunnel as a
   service and that command would replace it.
5. In `config/pilot.env`: `PLATFORM_PUBLIC_URL='https://app.kloudc.com'`,
   `CORS_ORIGINS='https://app.kloudc.com'`, `PLATFORM_DOMAIN='kloudc.com'`;
   restart the server (section 7). The app build uses
   `PLATFORM_API_URL=https://app.kloudc.com`.

**B. A cloud VM (section 3).** Bluehost DNS keeps kloudc.com; add one record:
`A  app  -> <the VM's public IP>`. Caddy on the VM gets the certificate by
itself.

Not recommended: Tailscale Funnel only serves `*.ts.net` names (no custom
domain), and its three public ports are already in use on this Mac.

## 3. Cloud VM (Docker compose)

A 4-8 GB VM (Hetzner, DigitalOcean...) with Docker:

    git clone <the repo> retailops && cd retailops
    cp deploy/cloud-vm/env.platform.example .env.platform && chmod 600 .env.platform
    # fill POSTGRES_PASSWORD, RETAILOPS_SECRET_KEYS (python -m app.platform gen-key), PLATFORM_HOST...
    docker compose -f docker-compose.platform.yml up -d db
    docker compose -f docker-compose.platform.yml run --rm app python -m app.platform migrate-platform
    docker compose -f docker-compose.platform.yml run --rm app python -m app.platform create-master --username <you>
    docker compose -f docker-compose.platform.yml --profile https --profile backup up -d

- `db`: PostgreSQL 16 with `max_connections=250`.
- `app`: on every start runs the config check, `migrate-platform` and
  `migrate-all` (every store), then one uvicorn worker; `docker stop` gives
  open requests 25 s.
- `caddy` (profile `https`): HTTPS for `PLATFORM_HOST` (DNS A record first).
- `backup` (profile `backup`): `backup_all.py` daily at `BACKUP_AT` (UTC) into
  the `backups` volume, drill on the 1st. Copy that volume off the machine
  (for example `rclone` to Cloudflare R2). One run now:
  `docker compose -f docker-compose.platform.yml run --rm backup now`.

## 4. Onboarding the first real store

The store keeps running on its current system until the switch-over; the
new service runs next to it in **pilot mode**: it syncs from the same POS,
every page works, and **nothing is sent** from it - no WhatsApp, no email, no
push (the transports themselves refuse, so a page, a scheduled job and a
retry are all covered; each refusal is logged and the record says why).

All commands run in the release checkout's backend with the pilot's settings
loaded; on this Mac:

    cd ~/kloudc-retailops/app/backend
    set -a; . ../../config/pilot.env; . ../../secrets/retailops.env; set +a
    PY=.venv/bin/python

1. **Create the store** (sign-ups are closed during the pilot):
   `$PY -m app.platform provision --code STORE_CODE --name "Store name" --tz America/New_York --pos toast --owner-username owner`
2. **Pilot mode on** before anything else:
   `$PY -m app.platform pilot-mode STORE_CODE on` (also a store setting in the
   master console: `pilot_mode`).
3. **Suspend the store** in the master console (Stores -> Suspend; this closes
   the server's connections to its database), then **import** its current
   database. The source is only ever read:

       $PY -m app.platform import-sqlite STORE_CODE --sqlite /path/to/current/store.db \
           [--uploads /path/to/current/uploads] [--workdir /roomy/scratch/folder]

   It snapshots the source (read-only), upgrades the snapshot to this
   release's schema, recreates the store's database, copies every table and
   compares every table's row count and a checksum of every column (any
   difference fails the import), re-stamps the store identity, removes the
   old system's sign-in sessions and phone tokens (people sign in again on
   the new app), adds the provisioned owner login back, and records
   `store.imported` in the audit log. A store that already has data needs
   `--replace --confirm STORE_CODE`.
4. **Load the store's private pack** (its own rules - kept outside git):
   `$PY -m app.platform load-pack STORE_CODE --file ~/somewhere-private/store.pack.json`
   (a file inside a git checkout is refused).
5. **Resume** the store in the console and restart the server (section 7).
6. **Daily parity.** Create `~/kloudc-retailops/config/parity.env`:

       PARITY_STORE=STORE_CODE
       PARITY_REFERENCE=/path/to/current/store.db
       PARITY_DAYS=14

   Every morning at 07:30 the pilot store's numbers (per closed business day:
   sales lines, orders, quantity, net sales, tax, voided lines) are compared
   with the current system's database, read-only on both sides. Results:
   `logs/parity-STORE_CODE.jsonl` (one line per run) and `parity.run` in the
   audit log. Run it by hand any time:
   `$PY -m app.platform parity STORE_CODE --reference /path/to/current/store.db`.
   Inventory uploads, invoices, checklists and placed orders are shown side by
   side but not counted as differences (people still use the current system).
7. **Switch over** when parity has been clean for a week: turn the old
   system's sends off, then `pilot-mode STORE_CODE off`, and point the
   store's phones at the new app.

Rehearse all of it first with the dry-run script (scratch databases on the
dev cluster, everything dropped afterwards; the source is only read):

    backend/scripts/onboarding/dry_run.sh --source /path/to/store.db [--pack /outside/git/store.pack.json]

## 5. Backups, restore drill and restoring a store

- **Nightly** (`backup_all.py backup`): `pg_dump` of the platform database and
  every store database on the cluster, each checked with `pg_restore --list`,
  kept 30 days (never the newest). One store failing never stops the others;
  `backups/last_backup_all.json` says what happened.
- **Monthly drill** (`backup_all.py drill`): the newest dump of each database is
  restored into a scratch database, its row counts compared with the live
  one, and the scratch database dropped. `backups/last_drill.json`.
- **Not in the dumps**: the key ring (`secrets/retailops.env`) - keep it offline.

**Restore one store** (on this Mac, from `~/kloudc-retailops/app/backend` with
the settings loaded as in section 4):
1. Master console -> Stores -> **Suspend** the store.
2. `S=~/kloudc-retailops/run; P=5434; DB=so_<store code in lower case>`
3. Keep the broken copy: `psql -h $S -p $P -U retailops -d postgres -c "ALTER DATABASE $DB RENAME TO ${DB}_broken"`
4. `createdb -h $S -p $P -U retailops -T template0 -E UTF8 --lc-collate=C --lc-ctype=C $DB`
5. `pg_restore -h $S -p $P -U retailops --no-owner --exit-on-error -d $DB ~/kloudc-retailops/backups/<CODE>/<CODE>-<stamp>.dump`
6. **Resume** the store, restart the server, check the store's pages; drop
   `${DB}_broken` a week later.

The platform database (`so_platform`) restores the same way with the server
stopped (`sudo launchctl bootout system/com.kloudc.retailops.pilot.server`,
then `sudo launchctl bootstrap system /Library/LaunchDaemons/com.kloudc.retailops.pilot.server.plist`).

## 6. Start-up checks, health, readiness and logs

With `APP_ENV=production` the server **refuses to start** when:
- `SESSION_COOKIE_SECURE=false`, or `CORS_ORIGINS` contains `*`;
- multi-store mode and the platform database is not PostgreSQL;
- no key ring, or a development key ring (an id starting `dev`, or a key from
  the local dev ring file);
- `PLATFORM_REQUIRE_TOTP=false` (blank means **on** in production);
- `SIGNUP_ENABLED=true` with no master who could approve a sign-up (sign-ups
  always wait for a master; there is no auto-approve).

Warnings (logged): placeholder domain, no Firebase file (push is a dry run),
no client-IP header behind a proxy, no website build, no `LOG_DIR`.
`python -m app.ops check-config` prints them without starting anything.

- `GET /api/health` - the process answers (liveness).
- `GET /api/ready` - 200 when the databases answer and every migration is
  current (platform database; no active store behind), else 503 naming the
  failing check. `python -m app.ops ready` does the same from a shell.
- Logs carry a **request id** (also returned as `X-Request-ID`; send your own
  to trace a request) and the store code. `LOG_FORMAT=json` writes one JSON
  object per line; `LOG_DIR` adds `app.log` and `error.log` (errors with
  tracebacks), rotated by size. An unexpected error answers
  `{"error": "server_error", "request_id": ...}` - quote the id when asking
  for help.
- Security headers on every answer: `Content-Security-Policy` (only this
  site; inline scripts of the built page allowed by hash; no outside fonts or
  scripts), `Strict-Transport-Security` (production), `X-Frame-Options:
  SAMEORIGIN` / `frame-ancestors 'self'`, `Referrer-Policy`,
  `X-Content-Type-Options`, `Permissions-Policy`. If a page breaks because of
  the CSP, set `SECURITY_HEADERS=report-only` and restart while it is fixed.

## 7. Updating, restarting, uninstalling

- **Update to a new release** (no sudo): `~/kloudc-retailops/app/deploy/pilot-mac/update.sh [REF]`
  - fetches, installs requirements, rebuilds the website, checks the config,
  restarts the server (launchd starts it again at once; the start script
  migrates the databases) and waits for `/api/ready`.
- **Restart** (no sudo): `pkill -TERM -u $(id -u) -f "kloudc-retailops/app/backend/.venv/bin/uvicorn"`.
- **Stop everything**: `sudo ~/storeops/storeops/deploy/pilot-mac/install.sh --uninstall`
  (data, backups and secrets stay in `~/kloudc-retailops`).
