#!/bin/zsh
# Per-store website checks (MT9): build the website, make two throwaway fake
# stores (fresh databases, generated logins), serve them, run stores.mjs.
#
#   frontend/e2e/store/run.sh            # everything; servers are stopped at the end
#
# STORE_E2E_WORK (default below) holds the build, the two databases, users,
# screenshots and result.json - delete it when done. STORE_E2E_PY is a Python
# with the backend's requirements. Playwright comes from frontend/e2e/node_modules
# (npm install playwright there, or a symlink to an existing install).
set -e
HERE=${0:A:h}
REPO=${HERE:h:h:h}
W=${STORE_E2E_WORK:-/tmp/storeops-store-e2e}
PY=${STORE_E2E_PY:-python3}
TOAST_PORT=${TOAST_PORT:-8861}
CLOVER_PORT=${CLOVER_PORT:-8862}
case "$W" in /|"$HOME"|"$REPO"*) echo "refusing STORE_E2E_WORK=$W"; exit 2;; esac
rm -rf "$W" && mkdir -p "$W"
(cd "$REPO/frontend" && npx vite build --outDir "$W/dist" --emptyOutDir >/dev/null)
for code in STOREOPS_TEST_TOAST STOREOPS_TEST_CLOVER; do
  mkdir -p "$W/$code"
  (cd "$REPO/backend" && env -i HOME="$HOME" PATH=/usr/bin:/bin DATABASE_URL="sqlite:///$W/$code/store.db" \
     "$PY" -m alembic upgrade head >/dev/null 2>&1)
  # A new store's one-time setup (M2 contract 3.5): its own pack, the old
  # migrations' origin-store rows removed, the identity stamp.
  pack=${${code#STOREOPS_}:l}.json
  (cd "$REPO/backend" && env -i HOME="$HOME" PATH=/usr/bin:/bin DATABASE_URL="sqlite:///$W/$code/store.db" \
     UPLOADS_ROOT="$W/$code/uploads" "$PY" -m app.store init --code $code --pack "packs/$pack" > "$W/$code/store-init.json")
  env -i HOME="$HOME" PATH=/usr/bin:/bin STORE_E2E_WORK="$W" STORE_E2E_CODE=$code "$PY" -I "$HERE/seed.py"
done
pids=()
cleanup() { for p in $pids; do kill $p 2>/dev/null || true; done }
trap cleanup EXIT INT TERM
for pair in STOREOPS_TEST_TOAST:$TOAST_PORT STOREOPS_TEST_CLOVER:$CLOVER_PORT; do
  code=${pair%%:*}; port=${pair##*:}
  env -i HOME="$HOME" PATH=/usr/bin:/bin STORE_E2E_WORK="$W" STORE_E2E_CODE=$code STORE_E2E_PORT=$port \
    "$PY" -I "$HERE/serve.py" > "$W/$code/server.log" 2>&1 &
  pids+=($!)
done
for port in $TOAST_PORT $CLOVER_PORT; do
  for i in {1..60}; do curl -sf "http://127.0.0.1:$port/api/app/branding" >/dev/null && break; sleep 1; done
done
head -1 "$W"/*/server.log
STORE_E2E_WORK="$W" TOAST_PORT=$TOAST_PORT CLOVER_PORT=$CLOVER_PORT node "$HERE/stores.mjs"
