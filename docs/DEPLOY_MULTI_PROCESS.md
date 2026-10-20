# Running more than one server process

KloudC RetailOps runs as **one server process** by default: it serves the
website and API, runs the scheduled jobs and keeps store files on its own
disk. That is right for the pilot and for a small shared service. This guide
is for when one process is no longer enough. Every switch below is **off by
default**; nothing changes until you set it and restart.

Website-ready track C, `docs/WEBSITE_READY_CONTRACT.md` C1-C6.

## 1. The pieces

| Process | How many | Runs | Started by |
|---|---|---|---|
| **Web** | 1 or more | uvicorn: website + API | `start-server.sh` (pilot), `app` service (Docker) |
| **Leader** | exactly 1 (elected) | the schedulers: per-store jobs, daily jobs | any web process that wins the PostgreSQL advisory lock; the others keep trying every 30 s |
| **Worker** | 0 or more | background jobs from the queue | `python -m app.jobqueue worker` (`start-worker.sh`, `worker` service) |

Shared state lives in PostgreSQL (the platform database) and, once moved, in
object storage. Processes tell each other about changes with PostgreSQL
`LISTEN/NOTIFY`.

## 2. The switches

| Setting | Default | What it does |
|---|---|---|
| `JOB_QUEUE_MODE` | `inline` | `queue`: jobs go to the `jobs` table and workers run them. Multi mode with a PostgreSQL platform database only; single-store mode is always inline. |
| `LIVE_UPDATES_MODE` | `local` | `postgres`: Team updates and store changes (settings, POS keys) reach every process. Needed as soon as there are two web processes. |
| `STORAGE_BACKEND` | `local` | `s3`: store files in an S3-compatible bucket (Cloudflare R2). Needs the `STORAGE_S3_*` settings; the server refuses to start without them. |
| `STORAGE_S3_ENDPOINT`, `STORAGE_S3_BUCKET`, `STORAGE_S3_ACCESS_KEY_ID`, `STORAGE_S3_SECRET_ACCESS_KEY` | empty | The bucket. R2 endpoint: `https://<account id>.r2.cloudflarestorage.com`. Keys go in the secrets file, never in git. |
| `STORAGE_S3_REGION` | `auto` | R2 uses `auto`. |
| `STORAGE_S3_PREFIX` | `stores` | Objects are `<prefix>/<STORE_ID>/<key>`. |
| `STORAGE_PRESIGN` | `true` | Downloads use short-lived direct links; `false` makes the server stream files. |
| `DB_POOL_MODE` | `direct` | `pgbouncer`: the database URLs point at PgBouncer in transaction mode. |
| `PLATFORM_DIRECT_URL` | empty | A direct (not PgBouncer) URL of the platform database for the leader lock and LISTEN. Required with `pgbouncer`. |
| `PILOT_WORKER_CONCURRENCY` / `WORKER_CONCURRENCY` | 2 | Jobs one worker runs at a time. |

The master console shows all of this in **Platform settings > System**, and
`/api/ready` adds `job_queue`, `jobs_due`, `jobs_dead`, `job_workers` and
`live_updates` (information only: a web process stays ready without workers).

## 3. Turning it on

Order matters: live updates first, then the queue and workers, then more web
processes. Each step can be undone on its own (section 6).

### 3.1 Pilot Mac

1. Back up first: `deploy/pilot-mac/run-backup.sh`.
2. Add to `~/kloudc-retailops/config/pilot.env`:
   ```
   LIVE_UPDATES_MODE=postgres
   JOB_QUEUE_MODE=queue
   ```
3. Restart the server: `sudo launchctl kickstart -k system/com.kloudc.retailops.pilot.server`.
   The platform migration `p0004` (job tables) runs at start.
4. Install the worker (refuses unless step 2 is done):
   `sudo deploy/pilot-mac/install.sh --worker`. Only the worker is installed;
   `deploy/pilot-mac/install.sh --dry-run /tmp/worker --worker` renders its
   plist without installing. Plain `install.sh` never installs it.
5. Open the master console, **Platform settings > System**: Workers shows
   one **Running** worker; Background jobs shows **Queue**.

### 3.2 Cloud VM (Docker)

1. In `.env.platform`: `LIVE_UPDATES_MODE=postgres`, `JOB_QUEUE_MODE=queue`.
2. `docker compose -f docker-compose.platform.yml up -d app` (migrates).
3. `docker compose -f docker-compose.platform.yml --profile queue up -d --scale worker=2`.
4. More web processes: run more `app` containers behind the proxy, or
   `--workers N` in the entrypoint. Leader election keeps the schedulers on
   one of them.

### 3.3 Object storage (Cloudflare R2)

1. Create the bucket and an API token with read/write on it only.
2. Put the `STORAGE_S3_*` values in the secrets file. Leave
   `STORAGE_BACKEND=local` for now.
3. `python -m app.storage migrate --to s3 --dry-run` (what would be copied).
4. `python -m app.storage migrate --to s3 --verify` (copy and check size +
   SHA-256 of every file). Safe to stop and run again: copied files are
   skipped. Hidden files such as `.keys/` never leave the server.
5. Set `STORAGE_BACKEND=s3`, restart, then run step 4 again to catch files
   written between the copy and the switch.
6. Keep the local folders as a backup until you are happy; the tool never
   deletes them.

### 3.4 PgBouncer

Use it when many processes or stores would open more connections than
PostgreSQL allows.

```
[databases]
* = host=127.0.0.1 port=5432
[pgbouncer]
pool_mode = transaction
ignore_startup_parameters = extra_float_digits,options
max_client_conn = 1000
default_pool_size = 20
```

Then set the time zone and statement timeout on the server side (PgBouncer
drops startup options):

```
ALTER ROLE retailops_admin SET timezone = 'UTC';
ALTER ROLE retailops_admin SET statement_timeout = '30s';
```

and in the environment: `DB_POOL_MODE=pgbouncer`, `PLATFORM_DATABASE_URL` and
`STORE_DATABASE_URL_TEMPLATE` pointing at PgBouncer, and `PLATFORM_DIRECT_URL`
pointing at PostgreSQL itself. The app then sends no prepared statements and
keeps no idle per-store pool. Migrations (`migrate-platform`, `migrate-all`)
should use direct URLs too.

## 4. What runs where in queue mode

- The leader's scheduler does not run per-store jobs itself: at each fire time
  it adds a `store_job` row (one waiting copy per job and store at most), and
  a worker runs the same function in that store.
- Daily jobs registered with `app.jobqueue.register_periodic` (usage
  measures, purges, the queue's own clean-up at 04:10 UTC) are added the same
  way.
- Interval jobs (open orders refresh, alerts) stay on the leader.
- A failed job is retried after 30 s, 1 min, 2 min ... (at most 1 h) until
  its `max_attempts`, then it is **Failed** and waits for a master's
  **Retry** (audited).
- A worker that stops checking in for 2 minutes, or a job past its time
  limit, is taken back and retried elsewhere.

## 5. Watching it

- Master console, **Platform settings > System**: tiles for Storage,
  Background jobs, Workers and Live updates; the jobs that need attention,
  with Retry; each worker and when it last checked in.
- `python -m app.jobqueue status` prints the same as JSON.
- Logs: `worker.out.log` / `worker.err.log` (pilot), `docker compose logs worker`.

## 6. Rollback

Each switch is independent; undo in reverse order.

- **Workers**: `sudo deploy/pilot-mac/install.sh --remove-worker` (or stop
  the `worker` service). Set `JOB_QUEUE_MODE=inline` and restart the server:
  jobs run inside it again. Jobs still in the queue stay there (visible in
  System); retry or ignore them.
- **Live updates**: `LIVE_UPDATES_MODE=local` and go back to one web process.
- **Storage**: set `STORAGE_BACKEND=local` and restart. Files written to the
  bucket after the switch are not on the local disk: copy them back first
  (any S3 tool, `stores/<STORE_ID>/...` to `<UPLOADS_ROOT>/<STORE_ID>/...`).
- **PgBouncer**: `DB_POOL_MODE=direct` with direct URLs.
- The `p0004` tables can stay; they are unused in inline mode.
