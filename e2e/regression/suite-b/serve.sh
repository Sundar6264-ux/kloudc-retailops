#!/bin/zsh
# Throwaway Suite B server on a PROD COPY (never the real DB): port 8831, every
# external service blank or pointed at local mocks (Whapi -> mock_whapi.py on
# 8838, SMTP -> fake outbox, Toast / Analytics / Anthropic / Firebase blank).
# Usage: serve.sh            (WORK defaults to the job tmp dir)
set -e
HERE=${0:A:h}
W=${SUITE_B_WORK:-/tmp/retailops-suiteB}
PORT=${SUITE_B_PORT:-8831}
WHAPI_PORT=${SUITE_B_WHAPI_PORT:-8838}
REPO=${SUITE_B_REPO:-${HERE:h:h:h}}   # default: this repository
mkdir -p $W/outbox $W/flyers $W/uploads/invoices $W/uploads/team $W/logs
cd $W
exec env -i HOME=$HOME PATH=/usr/bin:/bin \
  SUITE_B_WORK=$W SUITE_B_PORT=$PORT SUITE_B_REPO=$REPO \
  DATABASE_URL="sqlite:///$W/prodcopy.db" \
  SCHEDULER_ENABLED=false NOTIFICATIONS_ENABLED=false TEAM_JOBS_ENABLED=false \
  TEAM_AUTOTASK_CHECKLIST_ENABLED=false TEAM_AUTOTASK_INVOICE_ENABLED=false TEAM_AUTOTASK_ORDER_ENABLED=false \
  TEAM_WHATSAPP_FALLBACK=false INVENTORY_API_SNAPSHOTS=false SALES_REPULL_ENABLED=false INSIGHTS_NIGHTLY_ENABLED=false \
  TOAST_CLIENT_ID= TOAST_CLIENT_SECRET= TOAST_RESTAURANT_GUID= TOAST_API_BASE=http://127.0.0.1:9 \
  TOAST_ANALYTICS_CLIENT_ID= TOAST_ANALYTICS_CLIENT_SECRET= TOAST_ANALYTICS_BASE=http://127.0.0.1:9 \
  WHAPI_API_TOKEN=mock-token-not-real WHAPI_BASE_URL=http://127.0.0.1:$WHAPI_PORT \
  WHAPI_CHAT_ID= WHAPI_STORE_CHECKLIST_GROUP_ID= WHAPI_KITCHEN_CHECKLIST_GROUP_ID= \
  GMAIL_ADDRESS=orders-fake@retailops.test GMAIL_APP_PASSWORD=fake-not-real ANTHROPIC_API_KEY= FIREBASE_CREDENTIALS_FILE= \
  SESSION_COOKIE_SECURE=false SESSION_COOKIE_DOMAIN= APP_ENV=test \
  INSIGHTS_HMAC_KEY_FILE=$W/insights_hmac.key \
  FRONTEND_DIST_DIR=$W/dist FLYERS_DIR=$W/flyers INVOICE_UPLOADS_DIR=$W/uploads/invoices TEAM_UPLOADS_DIR=$W/uploads/team \
  PUBLIC_BASE_URL=http://127.0.0.1:$PORT CORS_ORIGINS=http://127.0.0.1:$PORT \
  nice -n 15 $REPO/backend/.venv/bin/python $HERE/serve.py
