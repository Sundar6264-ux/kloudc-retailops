# StoreOps

Store operations for independent grocery and food stores: sales and
inventory from the POS, reorder forecasting, purchase orders and vendor
calendars, invoices, issue tracking, checklists, team tasks and chat, and
insights. A product of KloudC LLC.

This repository holds the **server and the website**. The mobile app is the
separate `storeops-app` repository.

- **One store = one process** (one container): the API, the website and the
  scheduled jobs, with the store's own SQLite database, uploads and settings.
- **POS adapters** (`backend/app/pos/`): Toast today; the store's capabilities
  decide which pages and jobs exist.
- Contracts: `docs/MULTISTORE_M1_CONTRACT.md` (POS adapters, neutral models,
  capabilities) and `docs/M2_CONTRACT.md` (store settings, branding, store
  pack, directory, app tenant config, gating).
- Working notes for developers and AI agents: `.claude/skills/storeops/SKILL.md`.

## Stack

- **Backend**: FastAPI, SQLAlchemy 2 + Alembic, APScheduler (in-process jobs),
  pandas for CSV/XLSX imports, reportlab for PDFs, Tesseract for invoice OCR.
  Runs on Python 3.12 (3.9 still supported and tested).
- **DB**: SQLite in WAL mode (one file per store), Litestream for off-site
  backup to Cloudflare R2.
- **Auth**: server-side sessions (cookie for the website, bearer token for
  the app), bcrypt password hashes.
- **Website**: React + Vite, served by the backend from `frontend/dist`.

## Run a store with Docker (recommended)

    cp .env.example .env          # fill in the store's settings (STORE_CODE first); never commit it
    # Once, before the first start: migrate, load the store pack, stamp the
    # store identity. The server refuses to start without that stamp.
    docker compose run --rm --no-deps store sh -c \
      "alembic upgrade head && python -m app.store init --code <STORE_CODE> --pack packs/generic.json"
    docker compose up -d          # builds the image, migrates the database, starts the store
    curl -s http://127.0.0.1:8000/api/health
    docker compose exec store python scripts/seed_admin.py   # first admin login (asks for a password)

- A store's own pack file (not in the image): add
  `-v "$PWD/<code>.json:/packs/<code>.json:ro"` to the `run` line and pass
  `--pack /packs/<code>.json`.
- The store's data lives in the `store-data` volume (`/data` in the
  container): `storeops.db`, uploads and flyers.
- Off-site backup: `docker compose --profile backup up -d litestream` once
  the R2 values are in `.env` (see `backend/scripts/backup/README.md`).
- Put a reverse proxy (Caddy) or a Cloudflare Tunnel in front, one host name
  per store on the StoreOps domain (`PLATFORM_DOMAIN`).

Running without Docker on a Mac: `backend/scripts/launchd/README.md`.

## Local development

Backend:

    cd backend
    python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
    cp .env.example .env          # blank POS credentials are fine for local work
    .venv/bin/alembic upgrade head
    .venv/bin/python scripts/seed_admin.py
    SCHEDULER_ENABLED=false .venv/bin/uvicorn app.main:app --reload --port 8000

Website (separate dev server with hot reload, talking to the backend over CORS):

    cd frontend
    npm ci
    npm run dev

## Tests

    cd backend && .venv/bin/python -m pytest -q
    cd frontend && node --test $(find src -name '*.test.mjs') && npx vite build

GitHub Actions (`.github/workflows/ci.yml`) runs the backend suite on Python
3.9 and 3.12, the website tests and build, and builds the Docker image and
boots it against an empty database.

## Tools

- `backend/scripts/backup/` - nightly backup, restore drill, Litestream.
- `backend/scripts/parity/` - records a database's answers before and after a
  change and diffs them (the regression gate for changes that must not alter
  a store's numbers).
- `backend/scripts/dump_db_to_csv.sh` - every table to CSV.
- `backend/scripts/branding/make_placeholder_art.py` - regenerates the
  placeholder logo and icons.
