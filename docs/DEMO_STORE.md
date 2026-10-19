# The DEMO store (runbook)

Plan section 3.1a, batch MT13. A complete, made-up store with store code
**`DEMO`** for App Review and sales demos. Its data is generated, reset every
night, and never leaves the server.

## What it is

| Part | Where |
|---|---|
| Made-up data generator | `backend/scripts/demo/generate_demo_data.py` (+ `demo_catalog.py`, `fake_pdf.py`) |
| Build / check / swap | `backend/scripts/demo/reset_demo.py`, `backend/scripts/demo/smoke_demo.py` |
| Server + nightly reset in one process | `backend/scripts/demo/run_demo.py` |
| Store pack | `backend/packs/demo.json` (checklists, PO sections, leads, roles) |
| POS connector | `backend/app/pos/adapters/csv_only/` (`POS_PROVIDER=csv`: no POS calls) |
| Mac mini LaunchDaemon template | `deploy/demo/com.kloudc.retailops.demo.plist` |
| Docker service | `deploy/demo/docker-compose.demo.yml` |
| Settings template | `deploy/demo/demo.env.example` |
| Review notes | `docs/APP_REVIEW_NOTES.md` |

The data: ~400 generic products in 9 departments (EAN-13 barcodes in the
20-29 in-store range, never a real product), 8 fictitious suppliers
("Demo Produce Supply" ...), 90 days of sales (~12,000 orders), weekly catalog
CSV uploads loaded through the real upload code, delivery schedules,
purchase log, placed orders, cart lines, ~50 sample invoice PDFs (each says
"SAMPLE - DEMO DATA, NOT A REAL DOCUMENT"), opening/closing checklists with
45 days of submissions, tasks, team chat and announcements. Staff logins are
roles (`store_manager`, `grocery_lead` ...) with random passwords nobody
knows. The review login is **`appreview`**. Same seed + same date = same
data (only the password hash differs).

## Safety rules (built in, tested)

- `run_demo.py` forces, whatever `.env` says: `STORE_CODE=DEMO`,
  `POS_PROVIDER=csv`, `DEMO_MODE=true`, every outside credential blank
  (WhatsApp, e-mail, Anthropic, Toast, Firebase) and every outside base URL
  on `http://127.0.0.1:9`. It refuses to start if the settings name another
  store or turn `DEMO_MODE` off.
- `DEMO_MODE=true`: every login is a demo login (`app/demo.py`), so the
  read-only guard applies to all of them, admins included.
- The data folder must be new/empty or carry `.retailops-demo-data`; a `live/`
  folder is only replaced when it is a generated DEMO data set (marker file
  **and** marker row in its database). A real store's folder is refused
  untouched.
- Each new data set is smoke-checked with the real app (sign-in, every GET
  route without a 5xx, main pages non-empty, a write refused) before it
  replaces the old one. If the nightly build fails, yesterday's data stays.

## Run it on the Mac mini (LaunchDaemon)

The plan (D11) prefers the DEMO store on its own machine or container; on a
shared machine it runs as a Background-priority process on its own port.

1. A separate KloudC RetailOps checkout for the DEMO store, e.g.
   `~/retailops-demo` (`git clone https://github.com/Sundar6264-ux/kloudc-retailops.git retailops-demo`),
   with its own venv: `cd retailops-demo/backend && python3 -m venv .venv &&
   .venv/bin/pip install -r requirements.txt`.
2. `cp ../deploy/demo/demo.env.example .env` and fill `PLATFORM_DOMAIN`,
   `PUBLIC_HOST` (the DEMO host, e.g. `https://demo.<domain>`) and, if you
   want to choose it, `DEMO_REVIEW_PASSWORD` (10+ characters). Leave it blank
   to have one generated into `<data-dir>/review_password` (mode 600).
3. Try it in a terminal first:
   `.venv/bin/python scripts/demo/run_demo.py --data-dir ~/retailops-demo-data --port 8030`
   then `curl -s http://127.0.0.1:8030/api/app/legal` shows `"demo": true`.
   Ctrl-C stops it.
4. Fill the placeholders in `deploy/demo/com.kloudc.retailops.demo.plist`
   and install it (commands in the file). Logs:
   `~/retailops-demo-data/logs/demo.log`.
5. Point the DEMO host (Cloudflare tunnel / proxy) at `127.0.0.1:8030`, and
   add `DEMO` to the store directory (MT10) with that host as `api_base_url`.

## Run it with Docker

`docker compose -f deploy/demo/docker-compose.demo.yml up -d --build`
(after `cp deploy/demo/demo.env.example deploy/demo/demo.env`). Data lives in
the `demo-data` volume; the review password, when not set in `demo.env`, is
in `/data/demo/review_password` inside it
(`docker compose -f deploy/demo/docker-compose.demo.yml exec demo cat /data/demo/review_password`).
The image is the store image from the root `Dockerfile` (its entrypoint runs the
runner as a one-off command instead of the normal migrate + serve);
GitHub Actions builds and tests it - Docker is not installed on the store Mac.

## Everyday operations

- **Reset now** (server keeps running; the swap waits for the next night):
  `python scripts/demo/reset_demo.py --data-dir <dir>`. To swap at once,
  stop the server first, then add `--swap`; or restart the runner with
  `--reset-on-start`.
- **Check a data set**: `DEMO_REVIEW_PASSWORD=... python scripts/demo/smoke_demo.py --data <dir>/live`
  (signs in, so run it on a copy if the server is live).
- **Change the reset time**: `--reset-at HH:MM` (store time; default 03:30).
- **Bigger / smaller data**: `--days`, `--items` (defaults 90 / 400).
- **Upgrading the code**: pull the DEMO checkout and restart the runner with
  `--reset-on-start` (a fresh data set at the new Alembic head).
