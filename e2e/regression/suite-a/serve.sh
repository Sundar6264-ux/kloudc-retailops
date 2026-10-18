#!/bin/zsh
# Starts the throwaway regression server (foreground) on REG_PORT against the COPY
# at REG_WORK/copy.db, with every credential blank, the scheduler and all pushes /
# WhatsApp / email off, a fake SMTP and a mock Toast (see serve.py). Run from
# run_suite_a.sh, or by hand after prepare.sh.
HERE=${0:A:h}
source $HERE/env.sh
cd $REG_WORK || exit 1
mkdir -p $REG_WORK/flyers $REG_WORK/uploads/invoices $REG_WORK/uploads/team
exec env -i HOME=$HOME PATH=/usr/bin:/bin \
  REG_WORK=$REG_WORK REG_REPO=$REG_REPO REG_PORT=$REG_PORT REG_MOCK_PORT=$REG_MOCK_PORT \
  DATABASE_URL="sqlite:///$REG_WORK/copy.db" \
  SCHEDULER_ENABLED=false NOTIFICATIONS_ENABLED=false TEAM_JOBS_ENABLED=false \
  TEAM_AUTOTASK_CHECKLIST_ENABLED=false TEAM_AUTOTASK_INVOICE_ENABLED=false TEAM_AUTOTASK_ORDER_ENABLED=false \
  TEAM_WHATSAPP_FALLBACK=false INVENTORY_API_SNAPSHOTS=false SALES_REPULL_ENABLED=false \
  INVENTORY_API_READY_WHATSAPP=false PO_WHATSAPP_ENABLED=false \
  TOAST_CLIENT_ID=regression-mock TOAST_CLIENT_SECRET=regression-mock TOAST_RESTAURANT_GUID=regression-mock \
  TOAST_API_BASE=http://127.0.0.1:$REG_MOCK_PORT \
  TOAST_ANALYTICS_CLIENT_ID= TOAST_ANALYTICS_CLIENT_SECRET= TOAST_ANALYTICS_BASE=http://127.0.0.1:9 \
  WHAPI_API_TOKEN= WHAPI_CHAT_ID= WHAPI_SPICE_TOWN_CHECKLIST_GROUP_ID= WHAPI_STREET_KITCHEN_CHECKLIST_GROUP_ID= \
  WHAPI_BASE_URL=http://127.0.0.1:9 \
  GMAIL_ADDRESS=orders-fake@spicetown.test GMAIL_APP_PASSWORD=fake-not-real TOAST_INVOICE_EMAIL=toast-fake@spicetown.test \
  ANTHROPIC_API_KEY= FIREBASE_CREDENTIALS_FILE= \
  SESSION_COOKIE_SECURE=false SESSION_COOKIE_DOMAIN= \
  DEMO_USERNAMES=app_store_review,appreview \
  FRONTEND_DIST_DIR=$REG_WORK/dist FLYERS_DIR=$REG_WORK/flyers \
  INVOICE_UPLOADS_DIR=$REG_WORK/uploads/invoices TEAM_UPLOADS_DIR=$REG_WORK/uploads/team \
  PUBLIC_BASE_URL=http://127.0.0.1:$REG_PORT CORS_ORIGINS=http://127.0.0.1:$REG_PORT \
  nice -n 15 $REG_PY $HERE/serve.py
