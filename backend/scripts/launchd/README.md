# Running a store directly on a Mac (launchd templates)

**Docker is the default way to run a KloudC RetailOps store** (repo root: `Dockerfile`,
`docker-compose.yml`, `.env.example`). These templates are for the rare case
of running one store directly on a Mac without Docker.

| File | What |
|---|---|
| `com.kloudc.retailops.backend.plist` | The API + website + scheduled jobs, one process (`uvicorn app.main:app`), restarted on a crash |
| `com.kloudc.retailops.watchdog.plist` + `watchdog.sh` | Every 5 minutes, re-bootstraps the backend if launchd left it unloaded |
| `../backup/com.kloudc.retailops.backup.plist`, `../backup/com.kloudc.retailops.litestream.plist` | Nightly backup and Litestream (see `../backup/README.md`) |

Placeholders to replace in every template:

| Placeholder | Example |
|---|---|
| `__RETAILOPS_DIR__` | `/opt/retailops` (the checkout; the backend is `__RETAILOPS_DIR__/backend`) |
| `__RUN_AS_USER__` | the macOS user that owns the checkout |

Install (from a real Terminal; needs `sudo` once):

    cd /opt/retailops/backend
    python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
    (cd ../frontend && npm ci && npm run build)          # the backend serves frontend/dist
    cp .env.example .env                                 # fill in the store's settings
    .venv/bin/alembic upgrade head
    mkdir -p logs
    for f in com.kloudc.retailops.backend com.kloudc.retailops.watchdog; do
      sed -e "s#__RETAILOPS_DIR__#/opt/retailops#g" -e "s#__RUN_AS_USER__#$(whoami)#g" \
          scripts/launchd/$f.plist | sudo tee /Library/LaunchDaemons/$f.plist >/dev/null
      sudo launchctl bootstrap system /Library/LaunchDaemons/$f.plist
    done
    curl -s http://127.0.0.1:8000/api/health             # {"status":"ok"}

The backend listens on `127.0.0.1:8000`; put a reverse proxy (Caddy) or a
Cloudflare Tunnel in front of it for the store's host name on the KloudC RetailOps
domain. A Python change needs `sudo launchctl kickstart -k system/com.kloudc.retailops.backend`;
a rebuilt `frontend/dist` is picked up without a restart.
