#!/bin/bash
# Pilot on this Mac, step 1 (as the normal user, never root): folders, the
# release checkout, its Python venv and website build, the PostgreSQL
# cluster, the config and the key ring. Safe to run again: every step skips
# what is already there. install.sh runs it for you.
#
#   deploy/pilot-mac/prepare.sh [--skip-web] [--skip-venv] [--public-url https://...]
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

SKIP_WEB=0; SKIP_VENV=0; PUBLIC_URL="${PILOT_PUBLIC_URL:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-web) SKIP_WEB=1 ;;
    --skip-venv) SKIP_VENV=1 ;;
    --public-url) PUBLIC_URL="$2"; shift ;;
    *) die "unknown option $1" ;;
  esac
  shift
done
[ "$(id -u)" != 0 ] || die "run prepare.sh as the normal user, not root"
umask 077

say "1/7 folders under $PILOT_ROOT"
mkdir -p "$PILOT_ROOT"/{run,logs,backups,uploads,config,secrets}
chmod 700 "$PILOT_ROOT" "$PILOT_ROOT/secrets" "$PILOT_ROOT/config"

say "2/7 release checkout ($PILOT_SOURCE_REPO @ $PILOT_REF)"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --quiet --branch "$PILOT_REF" "$PILOT_SOURCE_REPO" "$APP_DIR"
else
  say "    already there ($(git -C "$APP_DIR" rev-parse --short HEAD)); update.sh moves it to a new release"
fi

[ "$SKIP_VENV" = 0 ] || [ -x "$VENV_PY" ] || die "--skip-venv but there is no venv at $BACKEND_DIR/.venv yet"
if [ "$SKIP_VENV" = 0 ] && [ ! -x "$VENV_PY" ]; then
  say "3/7 Python venv + requirements (a few minutes the first time)"
  "$PILOT_PYTHON" -m venv "$BACKEND_DIR/.venv"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r "$BACKEND_DIR/requirements.txt"
else
  say "3/7 Python venv: skipped (present or --skip-venv)"
fi

if [ "$SKIP_WEB" = 0 ] && [ ! -f "$APP_DIR/frontend/dist/index.html" ]; then
  say "4/7 website build (npm ci + vite build)"
  (cd "$APP_DIR/frontend" && npm ci --no-audit --no-fund --silent && npx vite build >/dev/null)
else
  say "4/7 website build: skipped (present or --skip-web)"
fi

say "5/7 PostgreSQL cluster ($PG_DATA, port $PILOT_PG_PORT, socket $PG_RUN)"
if [ ! -f "$PG_DATA/PG_VERSION" ]; then
  "$PILOT_PG_BIN/initdb" -D "$PG_DATA" -U "$PILOT_PG_ROLE" -E UTF8 --locale=C \
      --auth-local=peer --auth-host=scram-sha-256 >/dev/null
  # Only this Mac user may connect, over the socket, as the pilot role (no
  # passwords exist; TCP on 127.0.0.1 is open for nobody).
  printf 'kloudc  %s  %s\n' "$(id -un)" "$PILOT_PG_ROLE" >> "$PG_DATA/pg_ident.conf"
  { printf 'local   all   %s   peer map=kloudc\n' "$PILOT_PG_ROLE"; cat "$PG_DATA/pg_hba.conf"; } > "$PG_DATA/pg_hba.conf.new"
  mv "$PG_DATA/pg_hba.conf.new" "$PG_DATA/pg_hba.conf"
  cat >> "$PG_DATA/postgresql.conf" <<CONF

# --- KloudC RetailOps pilot (deploy/pilot-mac/prepare.sh) ---
port = $PILOT_PG_PORT
listen_addresses = '127.0.0.1'
unix_socket_directories = '$PG_RUN'
unix_socket_permissions = 0700
max_connections = 250
shared_buffers = 256MB
work_mem = 8MB
maintenance_work_mem = 128MB
timezone = 'UTC'
log_destination = 'stderr'
log_min_duration_statement = 2000
log_line_prefix = '%m [%p] %d '
CONF
else
  say "    cluster already there"
fi

say "6/7 config ($CONFIG_ENV) and key ring ($SECRETS_ENV)"
if [ -z "$PUBLIC_URL" ] && command -v tailscale >/dev/null 2>&1; then
  host="$(tailscale status --self --json 2>/dev/null | "$PILOT_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
  [ -n "$host" ] && PUBLIC_URL="https://$host:$PILOT_TS_HTTPS_PORT"
fi
[ -n "$PUBLIC_URL" ] || PUBLIC_URL="http://127.0.0.1:$PILOT_APP_PORT"
if [ ! -f "$CONFIG_ENV" ]; then
  cat > "$CONFIG_ENV" <<CONF
# KloudC RetailOps pilot settings (written by prepare.sh; edit freely).
# Secrets are in $SECRETS_ENV, never here.
APP_ENV='production'
TENANCY_MODE='multi'
PLATFORM_DATABASE_URL='$(pg_url so_platform)'
STORE_DATABASE_URL_TEMPLATE='$(pg_url '{db_name}')'
PG_ADMIN_URL='$(pg_url postgres)'
STORE_DB_PREFIX='so_'
UPLOADS_ROOT='$PILOT_ROOT/uploads'
FRONTEND_DIST_DIR='$APP_DIR/frontend/dist'
LOG_DIR='$LOG_DIR'
LOG_FORMAT='json'
SESSION_COOKIE_SECURE='true'
PLATFORM_PUBLIC_URL='$PUBLIC_URL'
CORS_ORIGINS='$PUBLIC_URL'
# Sign-ups stay closed during the pilot: masters create stores
# (python -m app.platform provision). Open them later with true.
SIGNUP_ENABLED='false'
# Blank = on in production: every master sets up two-step sign-in first.
PLATFORM_REQUIRE_TOTP=''
# Push notifications: the Firebase service-account JSON (owner action).
FIREBASE_CREDENTIALS_FILE=''
CONF
  chmod 600 "$CONFIG_ENV"
else
  say "    config already there (kept)"
fi
if [ ! -f "$SECRETS_ENV" ]; then
  [ -x "$VENV_PY" ] || die "the venv is needed to generate the key ring"
  ring="$(cd "$BACKEND_DIR" && "$VENV_PY" -m app.platform gen-key 2>/dev/null)"
  printf '# KloudC RetailOps pilot secrets. KEEP AN OFFLINE COPY: losing the key ring\n# loses every saved store password and key.\nRETAILOPS_SECRET_KEYS='%s'\n' "$ring" > "$SECRETS_ENV"
  chmod 600 "$SECRETS_ENV"
  say "    new key ring written - copy $SECRETS_ENV somewhere safe and offline"
else
  say "    key ring already there (kept)"
fi

say "7/7 done. Next (install.sh does these): start the cluster, setup-db.sh, start the server."
